"""dpapi_common.py — 平台无关的密钥保护后端接口（S1 macOS 端口）

设计动机（参考 docs/superpowers/specs/2026-06-22-macos-port-design.md §2.1）：
- 旧 `ai_bridge/dpapi.py` 是 Windows DPAPI 的硬实现（ctypes.windll.crypt32），
  sidecar/core/llm_config.py 通过它保护用户的 LLM API key
- Mac 端没有 DPAPI，要换成 Keychain Services（用 `keyring` 库）
- 把"后端实现"和"平台选择"拆开：这里只定义接口，平台后端各自实现

接口分两层：
- **FileBackend（必需）**：所有平台都实现。`protect_to_file` / `unprotect_from_file`。
  sidecar/core/llm_config.py 实际只用了这 2 个方法（grep 验证）。
- **InMemoryBackend（仅 Windows 支持）**：可选，in-memory 加解密（bytes ↔ bytes）。
  Windows DPAPI 天然支持；Mac Keychain 是"UUID + 文件"模式，没有 in-memory 等价物。

为什么不强制要求 InMemoryBackend？
  答：Mac Keychain 没法在内存里"加密一组 bytes"（Keychain 存的是"service + username
  → password"字符串映射，不是任意 bytes）。如果强制要求 Mac 后端也实现
  protect/unprotect，只能用 AES-with-static-key 糊一个，安全性下降且无意义。
  让调用方根据 backend 类型走不同路径更诚实。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretBackend(Protocol):
    """所有平台后端必须实现的最小接口（file-based）"""

    def protect_to_file(self, plaintext: str, path: str) -> None:
        """把 str 明文加密后写到 path。

        Windows：bytes (DPAPI 密文) → 写文件
        Mac：UUID + Keychain 引用，文件里存 "keychain:<uuid>" 或 "aes:<uuid>"
        """
        ...

    def unprotect_from_file(self, path: str) -> str:
        """从 path 读加密数据并解密回 str 明文。

        raise FileNotFoundError：path 不存在
        raise RuntimeError：密文损坏 / Keychain 条目被删 / 密钥派生失败等
        """
        ...


@runtime_checkable
class InMemoryBackend(Protocol):
    """可选接口：in-memory bytes ↔ bytes 加解密（仅 Windows DPAPI 支持）

    Mac 端不需要实现 —— Keychain 的存储模型与 DPAPI 不同。
    调用方应优先用 SecretBackend 的 file-based 方法，避开 in-memory。
    """

    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...
