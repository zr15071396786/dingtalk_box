"""key_crypto.py — 内置 Qwen API key 加解密（v0.3.9 安全加固）

设计：等级 3 安全（Argon2id + AES-256-GCM，主密钥内置但拆 3 段 + Nuitka AOT 编译）

加密格式（DINGTALK_BOX_DEFAULT_QWEN_KEY 的值）：
    enc:v1:<base64url(salt | nonce | ciphertext+tag)>
    - salt:    16 字节（固定值，绑版本）
    - nonce:   12 字节（每份密文唯一，运行时随机）
    - ct+tag:  N 字节（AES-256-GCM 输出，含 16 字节认证 tag）

主密钥：拆 3 段分布在 3 个常量。Nuitka AOT 编译后字符串不再以明文 ASCII
存储在 .exe，攻击者 `strings` 不到，必须用 Ghidra/IDA 反编译 native 代码。

向后兼容：value 不以 "enc:v1:" 开头 → 按明文原样返回（兼容老 .env）。
"""
from __future__ import annotations

import base64
import os

from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── 运行时主密钥（拆 3 段，不一眼可读）────────────────────────────
# 3 段分散在不同位置 + 顺序故意倒置（防止简单拼接）
# 真实口令 = reverse(C) + B + reverse(A)
_PART_A_REV = b"6202-bxd"          # 原 "dxb-2026" 倒置
_PART_B = b"qwen-builtin-"
_PART_C_REV = b"a5f4d8b1e2c9a7f3"  # 原 "3f7a9c2e1b8d4f5a" 倒置

# 固定 salt（绑版本，v0.3.9 起）
_SALT_FIXED = b"dxb_v039_builtin"
# 关联认证数据（AAD）— 防密文被替换/重放
_AAD = b"dingtalk_box_builtin_key_v1"

# 加密格式前缀
_PREFIX = "enc:v1:"


def _runtime_passphrase() -> bytes:
    """运行时拼接主密钥：reverse(C) + B + reverse(A)"""
    return _PART_C_REV[::-1] + _PART_B + _PART_A_REV[::-1]


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """Argon2id 派生 32 字节会话密钥（抗 GPU/ASIC 爆破）"""
    return hash_secret_raw(
        secret=passphrase,
        salt=salt,
        time_cost=3,           # 3 轮
        memory_cost=64 * 1024, # 64 MB（防侧信道）
        parallelism=4,
        hash_len=32,
        type=Type.ID,
    )


def encrypt_builtin_key(plaintext_key: str) -> str:
    """加密明文 Qwen key → 粘到 .env 的字符串

    开发者机器跑一次（scripts/encrypt_builtin_key.py 调这里）。
    """
    if not plaintext_key:
        raise ValueError("plaintext_key 不能为空")
    if plaintext_key.startswith(_PREFIX):
        # 已加密，避免重复加密
        return plaintext_key
    derived = _derive_key(_runtime_passphrase(), _SALT_FIXED)
    nonce = os.urandom(12)
    ct = AESGCM(derived).encrypt(
        nonce,
        plaintext_key.encode("utf-8"),
        _AAD,
    )
    blob = _SALT_FIXED + nonce + ct
    return _PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_builtin_key(encrypted_value: str) -> str:
    """从 .env 读出密文 → 解出明文 Qwen key

    启动时 launcher 调这里。失败抛 RuntimeError（不是 silent fallback，
    让问题暴露而不是静默用错 key）。
    """
    if not encrypted_value:
        return ""
    if not encrypted_value.startswith(_PREFIX):
        # 非加密格式：按明文兼容（老 .env）
        return encrypted_value
    try:
        blob = base64.urlsafe_b64decode(encrypted_value[len(_PREFIX):])
    except Exception as e:
        raise RuntimeError(f".env 内置 key 密文格式损坏：{e}") from e
    if len(blob) < 16 + 12 + 16:
        raise RuntimeError(".env 内置 key 密文长度异常（可能被截断/篡改）")
    salt = blob[:16]
    nonce = blob[16:28]
    ct = blob[28:]
    # salt 必须等于固定值（v0.3.9 设计 = 单 salt，未来可扩展多版本）
    if salt != _SALT_FIXED:
        raise RuntimeError(".env 内置 key 密文 salt 不匹配（版本不一致）")
    derived = _derive_key(_runtime_passphrase(), salt)
    try:
        plaintext = AESGCM(derived).decrypt(nonce, ct, _AAD)
    except Exception as e:
        # GCM 认证失败 = 密文被改过 / 主密钥不匹配
        raise RuntimeError(
            f".env 内置 key 解密失败（GCM 认证错）：{e}\n"
            f"可能原因：1) .env 被修改  2) 工具版本与打包时的 .env 不匹配  3) 文件损坏"
        ) from e
    return plaintext.decode("utf-8")
