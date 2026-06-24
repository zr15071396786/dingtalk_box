"""scripts/encrypt_builtin_key.py — 开发者机器把明文 Qwen key 加密为 .env 值

用法：
    python scripts/encrypt_builtin_key.py sk-87b8f835b6be47d1b46f3fd7e661133d

输出（一行直接粘到 .env）：
    DINGTALK_BOX_DEFAULT_QWEN_KEY=enc:v1:...

也可不传参，交互式输入（注意：PowerShell 历史可能留痕 → 推荐用传参方式）。
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

# 脚本独立可跑：把项目根加到 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from key_crypto import encrypt_builtin_key  # noqa: E402


def main() -> int:
    if len(sys.argv) >= 2:
        raw = sys.argv[1].strip()
    else:
        print("=== 加密内置 Qwen key ===")
        print("输入将回显（如需隐藏请直接传参：python encrypt_builtin_key.py sk-xxx）")
        raw = input("明文 Qwen API key: ").strip()
    if not raw:
        print("ERROR: 明文 key 不能为空", file=sys.stderr)
        return 1
    if raw.startswith("enc:v1:"):
        print("已经是密文格式，无需重复加密", file=sys.stderr)
        print(raw)
        return 0
    encrypted = encrypt_builtin_key(raw)
    print()
    print("─" * 60)
    print("粘到 .env 的那一行（替换原 DINGTALK_BOX_DEFAULT_QWEN_KEY=...）：")
    print()
    print(f"DINGTALK_BOX_DEFAULT_QWEN_KEY={encrypted}")
    print()
    print("─" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
