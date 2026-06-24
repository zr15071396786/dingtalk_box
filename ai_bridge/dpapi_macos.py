"""dpapi_macos.py — macOS Keychain 后端 + AES 文件加密 fallback

设计参考 docs/superpowers/specs/2026-06-22-macos-port-design.md §2.1。

核心思想：
- Keychain 项 = "service + username → password" 映射（macOS Security framework）
- 一份业务文件（%APPDATA%/DingTalkBox/llm_secret.bin）存 Keychain 引用：
    "keychain:<uuid>"  →  Keychain 里用 uuid 作 username 查 password（明文 API key）
    "aes:<base64>"     →  文件里直接存 AES-256-GCM 密文（Keychain 不可用 fallback）
- 跨重启可恢复：文件 + Keychain 配对；或文件自包含（AES 模式）

为什么不直接 `keyring.set_password` 然后 ID 写哪？
  答：Keychain 不存 UUID，存 "service + username → password" 映射。
  我们的业务文件是文件系统，UUID 是文件 → Keychain 项的关联键。
  一份文件 + 一份 Keychain 项 = 跨重启可恢复。
"""
from __future__ import annotations

import base64
import getpass
import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Optional

# keyring 在 Mac 上是必装依赖；其他平台导入失败也没关系（dispatcher 只在 darwin 用）
try:
    import keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 派生密钥用的固定 app salt（不变更，变更会导致旧 AES 文件无法解密）
_APP_SALT = b"DingTalkBox/macOS-AES-fallback-v1"

# Keychain service 标识
_KEYRING_SERVICE = "DingTalkBox"

# 文件格式前缀
_PREFIX_KEYCHAIN = "keychain:"
_PREFIX_AES = "aes:"


def _derive_aes_key() -> bytes:
    """从机器可读信息派生 32 字节 AES 主密钥（Keychain fallback 用）

    用 scrypt 强 KDF（n=2^15 抗暴力），输入：
    - hostname（socket.gethostname）
    - 当前用户名（getpass.getuser）
    - 固定 _APP_SALT

    安全性低于 Keychain（密钥来自机器可读取的硬件信息），
    但仍比明文好 —— 攻击者要解密还需要拿到本机 hostname + user。

    Mac 上若 Keychain 因 sandbox / SSH-only 不可用，会走这里。
    """
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    password = f"{socket.gethostname()}:{getpass.getuser()}".encode("utf-8")
    kdf = Scrypt(salt=_APP_SALT, length=32, n=2 ** 15, r=8, p=1)
    return kdf.derive(password)


def _aes_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-256-GCM 加密，nonce 随机 12 bytes 前缀"""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ct  # 12 bytes nonce + ciphertext+tag


def _aes_decrypt(blob: bytes, key: bytes) -> bytes:
    """AES-256-GCM 解密（前 12 bytes 是 nonce）"""
    if len(blob) < 12 + 16:  # nonce + 最小 tag
        raise RuntimeError("AES 密文过短（损坏？）")
    nonce, ct = blob[:12], blob[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None)


def _keyring_set(service: str, username: str, password: str) -> bool:
    """写 Keychain；返回 True=成功，False=失败（fallback 走 AES）"""
    if not _KEYRING_AVAILABLE:
        return False
    try:
        keyring.set_password(service, username, password)
        return True
    except Exception:
        return False


def _keyring_get(service: str, username: str) -> Optional[str]:
    """读 Keychain；返回 None=条目不存在 / Keychain 不可用"""
    if not _KEYRING_AVAILABLE:
        return None
    try:
        return keyring.get_password(service, username)
    except Exception:
        return None


class MacosKeychainBackend:
    """macOS Keychain 后端（实现 SecretBackend）

    文件格式（%APPDATA%/DingTalkBox/llm_secret.bin 内容）：
      "keychain:<uuid>"  — Keychain 项 = (service=DingTalkBox, username=<uuid>, password=明文 API key)
      "aes:<base64>"     — AES-256-GCM 密文（Keychain 不可用时的 fallback）
    """

    def protect_to_file(self, plaintext: str, path: str) -> None:
        """明文 API key → 加密文件"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        key_id = str(uuid.uuid4())

        # 1. 优先尝试写 Keychain
        if _keyring_set(_KEYRING_SERVICE, key_id, plaintext):
            content = f"{_PREFIX_KEYCHAIN}{key_id}\n"
        else:
            # 2. Keychain 不可用（sandbox / SSH-only / keyring 库缺失）→ AES fallback
            key = _derive_aes_key()
            ct = _aes_encrypt(plaintext.encode("utf-8"), key)
            content = f"{_PREFIX_AES}{base64.b64encode(ct).decode('ascii')}\n"

        # 原子写：先写 .tmp 再 os.replace（防止半截）
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, p)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def unprotect_from_file(self, path: str) -> str:
        """读加密文件并解密回明文 API key

        raise FileNotFoundError：path 不存在
        raise RuntimeError：密文损坏 / Keychain 条目被删 / 格式未知 / AES 解密失败
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Mac secret file not found: {path}")
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            raise RuntimeError(f"Mac secret file is empty: {path}")

        if raw.startswith(_PREFIX_KEYCHAIN):
            key_id = raw[len(_PREFIX_KEYCHAIN):]
            v = _keyring_get(_KEYRING_SERVICE, key_id)
            if v is None:
                raise RuntimeError(
                    f"Keychain 条目不存在或被删除（service={_KEYRING_SERVICE} "
                    f"username={key_id}）。请重新配置 LLM API key。"
                )
            return v

        if raw.startswith(_PREFIX_AES):
            b64 = raw[len(_PREFIX_AES):]
            try:
                blob = base64.b64decode(b64)
            except Exception as e:
                raise RuntimeError(f"AES 密文 base64 解码失败：{e}") from e
            key = _derive_aes_key()
            try:
                pt = _aes_decrypt(blob, key)
            except Exception as e:
                raise RuntimeError(f"AES 解密失败（hostname/user 变了？）：{e}") from e
            return pt.decode("utf-8")

        raise RuntimeError(f"未知的 secret 文件格式（前缀不是 keychain: / aes:）：{path}")
