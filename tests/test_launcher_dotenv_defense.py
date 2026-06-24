"""test_launcher_dotenv_defense.py — v0.3.13 launcher._parse_dotenv 防御性解析验证

v0.3.9-v0.3.12 bug：deliver/.env 文件里 `# 注释` 和 `KEY=VALUE` 粘在同一行（缺 LF），
_parse_dotenv 把整行当注释跳过 → env var 没注入 → 同事报"未配置模型"，
排查 30 分钟才定位到 .env 文件本身。

v0.3.13 fix：_parse_dotenv 检测注释行里是否含 KEY=VALUE 模式，如果有 →
打印警告 + 提取 KEY=VALUE 继续解析（不让用户因为 .env 格式小错就崩）。

这个 test 验证：
  1. 正常 .env（注释和 key 分行）→ 正常解析
  2. 注释行套 KEY=VALUE（同 .env bug）→ 警告 + 仍能提取 key 注入 env
  3. 纯注释行（无 KEY=VALUE）→ 跳过
  4. 空行 / 缺 = 号 → 跳过
  5. 已有 os.environ 的 key 不覆盖
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 把项目根加到 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "[OK]"
BAD = "[BAD]"
WARN = "[WARN]"


def _make_env(content: str) -> Path:
    """写临时 .env 文件返回路径"""
    tmpdir = Path(tempfile.mkdtemp(prefix="dingtalk_box_env_test_"))
    p = tmpdir / ".env"
    p.write_text(content, encoding="utf-8")
    return p


def _cleanup(p: Path):
    if p.exists():
        shutil.rmtree(p.parent, ignore_errors=True)


def _fresh_environ():
    """备份 os.environ，测试后恢复"""
    saved = dict(os.environ)
    return saved


def _restore_environ(saved):
    """恢复 os.environ（删除测试期间新增的）"""
    for k in list(os.environ.keys()):
        if k not in saved:
            del os.environ[k]
    for k, v in saved.items():
        os.environ[k] = v


def test_normal_env_parses():
    """正常 .env（注释和 key 分行）→ 正常解析，env 注入"""
    saved = _fresh_environ()
    os.environ.pop("TEST_NORMAL_KEY", None)
    try:
        p = _make_env(
            "# 注释\n"
            "TEST_NORMAL_KEY=plain-value\n"
        )
        # 必须 stub out decrypt builtin key 否则 _decrypt_builtin_key 可能是 None
        import launcher
        original = launcher._decrypt_builtin_key
        try:
            launcher._parse_dotenv(p)
            assert os.environ.get("TEST_NORMAL_KEY") == "plain-value", (
                f"normal .env should inject TEST_NORMAL_KEY=plain-value, "
                f"got {os.environ.get('TEST_NORMAL_KEY')!r}"
            )
            print(f"  {OK} normal .env parses correctly")
        finally:
            launcher._decrypt_builtin_key = original
            _cleanup(p)
    finally:
        _restore_environ(saved)


def test_kv_in_comment_line_extracted():
    """v0.3.12 bug repro：# 注释和 KEY=VALUE 粘同一行 → 警告 + 仍能提取"""
    saved = _fresh_environ()
    os.environ.pop("TEST_BUG_KEY", None)
    try:
        # 这就是 deliver/.env 的实际 bug 格式
        p = _make_env(
            "# 钉钉AI助手 - 公用模型本地配置TEST_BUG_KEY=plain-value\n"
        )
        import launcher
        original = launcher._decrypt_builtin_key
        try:
            launcher._parse_dotenv(p)
            assert os.environ.get("TEST_BUG_KEY") == "plain-value", (
                f"defense should extract TEST_BUG_KEY from comment line, "
                f"got {os.environ.get('TEST_BUG_KEY')!r}"
            )
            print(f"  {OK} defense: extracted KEY=VALUE from comment line")
        finally:
            launcher._decrypt_builtin_key = original
            _cleanup(p)
    finally:
        _restore_environ(saved)


def test_pure_comment_line_skipped():
    """纯注释行（无 KEY=VALUE）→ 跳过，不警告"""
    saved = _fresh_environ()
    os.environ.pop("TEST_PURE_COMMENT", None)
    try:
        p = _make_env(
            "# 只是一个注释，没有 KEY=VALUE\n"
            "TEST_OTHER_KEY=foo\n"
        )
        import launcher
        original = launcher._decrypt_builtin_key
        try:
            launcher._parse_dotenv(p)
            assert "TEST_PURE_COMMENT" not in os.environ, (
                "pure comment line should NOT inject any env var"
            )
            assert os.environ.get("TEST_OTHER_KEY") == "foo"
            print(f"  {OK} pure comment line skipped, no spurious env var")
        finally:
            launcher._decrypt_builtin_key = original
            _cleanup(p)
    finally:
        _restore_environ(saved)


def test_empty_and_malformed_skipped():
    """空行 / 缺 = 号 / 注释无 KEY=VALUE → 跳过"""
    saved = _fresh_environ()
    try:
        p = _make_env(
            "\n"                                          # 空行
            "   \n"                                       # 空白行
            "# this is a comment\n"                       # 注释
            "NOEQUALS\n"                                  # 缺 =
            "VALID_KEY=valid\n"
        )
        import launcher
        original = launcher._decrypt_builtin_key
        try:
            launcher._parse_dotenv(p)
            assert os.environ.get("VALID_KEY") == "valid"
            assert "NOEQUALS" not in os.environ
            print(f"  {OK} empty/blank/comment/no-= lines handled correctly")
        finally:
            launcher._decrypt_builtin_key = original
            _cleanup(p)
    finally:
        _restore_environ(saved)


def test_existing_env_not_overwritten():
    """已有的 os.environ key 不被 .env 覆盖"""
    saved = _fresh_environ()
    os.environ["TEST_EXISTING"] = "from-caller"
    try:
        p = _make_env("TEST_EXISTING=from-env-file\n")
        import launcher
        original = launcher._decrypt_builtin_key
        try:
            launcher._parse_dotenv(p)
            assert os.environ["TEST_EXISTING"] == "from-caller", (
                f"existing env should NOT be overwritten, "
                f"got {os.environ['TEST_EXISTING']!r}"
            )
            print(f"  {OK} existing os.environ key preserved")
        finally:
            launcher._decrypt_builtin_key = original
            _cleanup(p)
    finally:
        _restore_environ(saved)


def test_real_buggy_env_decrypts():
    """集成测试：deliver/.env 实际 bug 格式 + 真实加密 key"""
    saved = _fresh_environ()
    # 清掉可能污染的 key
    os.environ.pop("DINGTALK_BOX_DEFAULT_QWEN_KEY", None)
    try:
        # 模拟 v0.3.12 bug 的 deliver/.env：注释 + 密文 key 粘一行
        p = _make_env(
            "# 钉钉AI助手 - 公用模型本地配置"
            "DINGTALK_BOX_DEFAULT_QWEN_KEY=enc:v1:ZHhiX3YwMzlfYnVpbHRpbp_8mQ66RvP34htkIi8DmPFtA0AQFuWeBObevn2gvx-slsDh9U2UD1bHtnwQxnSWLgRm8CxvjtAYIfSui8ijQg==\n"
        )
        import launcher
        original = launcher._decrypt_builtin_key
        try:
            launcher._parse_dotenv(p)
            # 真实 key_crypto 解密成功 → env 注入明文
            injected = os.environ.get("DINGTALK_BOX_DEFAULT_QWEN_KEY", "")
            assert injected, "should inject decrypted plaintext key"
            assert not injected.startswith("enc:v1:"), (
                f"env var should be plaintext after decrypt, got encrypted: {injected[:20]}..."
            )
            print(f"  {OK} real buggy .env decrypted + injected: {injected[:8]}...{injected[-4:]}")
        finally:
            launcher._decrypt_builtin_key = original
            _cleanup(p)
    finally:
        _restore_environ(saved)


if __name__ == "__main__":
    print("=" * 60)
    print("v0.3.13 launcher._parse_dotenv defense verification")
    print("=" * 60)
    print()
    print("[1/6] normal .env (comment + key on separate lines)")
    test_normal_env_parses()
    print()
    print("[2/6] KEY=VALUE inside comment line (v0.3.12 bug repro)")
    test_kv_in_comment_line_extracted()
    print()
    print("[3/6] pure comment line (no KEY=VALUE) skipped")
    test_pure_comment_line_skipped()
    print()
    print("[4/6] empty / malformed lines skipped")
    test_empty_and_malformed_skipped()
    print()
    print("[5/6] existing os.environ not overwritten")
    test_existing_env_not_overwritten()
    print()
    print("[6/6] real buggy deliver/.env + real encrypted key")
    test_real_buggy_env_decrypts()
    print()
    print("=" * 60)
    print("ALL CHECKS PASSED -- v0.3.13 .env defense verified")
    print("=" * 60)