"""dpapi_windows.py — Windows DPAPI 后端（S1 macOS 端口重构）

来自旧 `ai_bridge/dpapi.py` 的逻辑，**完整迁移、零行为变更**：
- protect / unprotect：ctypes 调 crypt32.dll 的 CryptProtectData / CryptUnprotectData
- protect_to_file / unprotect_from_file：原子写（先写 .tmp 再 os.replace）

代码 byte-for-byte 等价于 v0.3.15 的 `dpapi.py` 主体，
只是把 module-level 函数搬进 `WindowsDpapiBackend` 类（实现 SecretBackend Protocol）。

为什么 windows 端代码不能改？
  答：v0.3.15 的 `llm_secret.bin` 是 Windows DPAPI 密文（绑 Windows user），
  重构后 unprotect_from_file 必须还能解出原文，否则所有 Windows 用户升级后
  都会"密钥已备份为 .bak"重新填。S1 的硬约束：**零 Windows 行为变更**。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


# ── ctypes 类型定义 ──────────────────────────────────────────────────
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_CryptProtectData = ctypes.windll.crypt32.CryptProtectData
_CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB),
    wintypes.LPCWSTR,
    ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DATA_BLOB),
]
_CryptProtectData.restype = wintypes.BOOL

_CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData
_CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p,
    ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DATA_BLOB),
]
_CryptUnprotectData.restype = wintypes.BOOL

_LocalFree = ctypes.windll.kernel32.LocalFree
_LocalFree.argtypes = [ctypes.c_void_p]
_LocalFree.restype = ctypes.c_void_p


def _bytes_to_blob(data: bytes) -> _DATA_BLOB:
    blob = _DATA_BLOB()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(
        ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)
    )
    return blob


def _blob_to_bytes(blob: _DATA_BLOB) -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    buf = ctypes.string_at(blob.pbData, blob.cbData)
    return bytes(buf)


class WindowsDpapiBackend:
    """Windows DPAPI 后端（实现 SecretBackend + InMemoryBackend）"""

    # ── SecretBackend (必需) ──────────────────────────────────────
    def protect_to_file(self, plaintext: str, path: str) -> None:
        """加密并写入文件（原子写：先写 .tmp 再 os.replace）"""
        ciphertext = self.protect(plaintext.encode("utf-8"))
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.write_bytes(ciphertext)
            os.replace(tmp, p)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def unprotect_from_file(self, path: str) -> str:
        """读文件并解密；文件不存在 raise FileNotFoundError"""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"DPAPI secret file not found: {path}")
        ciphertext = p.read_bytes()
        return self.unprotect(ciphertext).decode("utf-8")

    # ── InMemoryBackend (Windows 特有) ────────────────────────────
    def protect(self, plaintext: bytes) -> bytes:
        """加密 bytes → bytes（DPAPI CryptProtectData）"""
        in_blob = _bytes_to_blob(plaintext)
        out_blob = _DATA_BLOB()
        ok = _CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok:
            raise RuntimeError("DPAPI CryptProtectData 失败")
        try:
            return _blob_to_bytes(out_blob)
        finally:
            if out_blob.pbData:
                _LocalFree(out_blob.pbData)

    def unprotect(self, ciphertext: bytes) -> bytes:
        """解密 bytes → bytes（DPAPI CryptUnprotectData）"""
        in_blob = _bytes_to_blob(ciphertext)
        out_blob = _DATA_BLOB()
        ok = _CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok:
            raise RuntimeError("DPAPI CryptUnprotectData 失败")
        try:
            return _blob_to_bytes(out_blob)
        finally:
            if out_blob.pbData:
                _LocalFree(out_blob.pbData)
