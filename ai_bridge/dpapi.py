"""dpapi.py — 平台无关的密钥保护 facade（S1 macOS 端口重构）

设计动机（参考 docs/superpowers/specs/2026-06-22-macos-port-design.md §2.1）：
- 旧版：模块级函数直接调 ctypes.windll.crypt32（Windows only）
- 新版：保持原 public API（`protect` / `unprotect` / `protect_to_file` / `unprotect_from_file`），
  内部根据 sys.platform 选择后端：
    - Windows → dpapi_windows.WindowsDpapiBackend（行为 byte-for-byte 等价 v0.3.15）
    - macOS  → dpapi_macos.MacosKeychainBackend（Keychain + AES fallback）

**Windows 行为零变更（v0.3.15 → S1 硬约束）**：
- 旧 llm_secret.bin 仍是 Windows DPAPI 密文
- Windows 用户的 key 不丢、不需要重新填
- sidecar/core/llm_config.py 调用 `from ai_bridge import dpapi; dpapi.protect_to_file(...)`
  与 v0.3.15 完全一致
"""
from __future__ import annotations

import sys
from typing import Optional

from ai_bridge.dpapi_common import InMemoryBackend, SecretBackend

_CACHED_BACKEND: Optional[SecretBackend] = None


def get_backend() -> SecretBackend:
    """根据当前平台返回对应后端（进程内单例缓存）

    Windows + Mac 之外的平台：抛 RuntimeError（不支持）
    """
    global _CACHED_BACKEND
    if _CACHED_BACKEND is not None:
        return _CACHED_BACKEND

    if sys.platform.startswith("win"):
        from ai_bridge.dpapi_windows import WindowsDpapiBackend
        _CACHED_BACKEND = WindowsDpapiBackend()
    elif sys.platform == "darwin":
        from ai_bridge.dpapi_macos import MacosKeychainBackend
        _CACHED_BACKEND = MacosKeychainBackend()
    else:
        raise RuntimeError(
            f"dpapi: 不支持的平台 {sys.platform}（仅 Windows + macOS）"
        )
    return _CACHED_BACKEND


# ── Public API（与 v0.3.15 完全兼容） ──────────────────────────────

def protect(plaintext: bytes) -> bytes:
    """加密 bytes → bytes（仅 Windows 后端支持 in-memory 加密）

    raise NotImplementedError：Mac 后端（Keychain 模型无 in-memory 等价物）
    raise RuntimeError：DPAPI 调用失败
    """
    backend = get_backend()
    if not isinstance(backend, InMemoryBackend):
        raise NotImplementedError(
            f"{type(backend).__name__} 不支持 in-memory protect()，"
            "请改用 protect_to_file() / unprotect_from_file()"
        )
    return backend.protect(plaintext)


def unprotect(ciphertext: bytes) -> bytes:
    """解密 bytes → bytes（仅 Windows 后端支持 in-memory 解密）"""
    backend = get_backend()
    if not isinstance(backend, InMemoryBackend):
        raise NotImplementedError(
            f"{type(backend).__name__} 不支持 in-memory unprotect()，"
            "请改用 protect_to_file() / unprotect_from_file()"
        )
    return backend.unprotect(ciphertext)


def protect_to_file(plaintext: str, path: str) -> None:
    """明文 API key → 加密文件（所有平台支持）

    Windows：原子写 DPAPI 密文
    Mac：写 "keychain:<uuid>"（Keychain 项）或 "aes:<base64>"（fallback）
    """
    get_backend().protect_to_file(plaintext, path)


def unprotect_from_file(path: str) -> str:
    """读加密文件并解出明文 API key（所有平台支持）

    raise FileNotFoundError：path 不存在
    raise RuntimeError：密文损坏 / Keychain 条目被删 / AES 派生失败等
    """
    return get_backend().unprotect_from_file(path)
