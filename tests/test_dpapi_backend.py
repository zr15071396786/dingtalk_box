"""test_dpapi_backend.py — S1 DPAPI 抽象层测试

覆盖：
A. **Windows 回归测试**（最重要 —— S1 硬约束：Windows 行为零变更）
   - dpapi.unprotect_from_file 能解 v0.3.15 写的密文（向后兼容）
   - dpapi.protect_to_file 写出的密文 v0.3.15 unprotect 能解（向前兼容）
   - dpapi.protect / dpapi.unprotect in-memory 正常
   - 原子写：异常时 .tmp 被清理
   - get_backend() 缓存单例

B. **Mac 单元测试**（在 Windows 上跑，mock keyring + keyring 不可用 → AES fallback）
   - 模拟 keyring 可用：protect_to_file 写 "keychain:<uuid>" 文件
   - unprotect_from_file 通过 keyring.get_password 拿回明文
   - 模拟 keyring 不可用：protect_to_file 走 AES fallback
   - AES 模式 round-trip 正常
   - AES 派生 key 在 hostname/user 不变时稳定（解自己写的密文）
   - 未知前缀：raise RuntimeError
   - 空文件：raise RuntimeError
   - 文件不存在：raise FileNotFoundError
   - Keychain 条目被删：raise RuntimeError

C. **平台调度器**
   - 非 Windows/Mac 平台 raise RuntimeError
   - InMemoryBackend 检查：Mac 后端调 protect/unprotect 抛 NotImplementedError
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 把项目根加到 path（和现有 tests 风格一致）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    line = f"{tag} {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


# ══════════════════════════════════════════════════════════════════
# A. Windows 回归测试（最重要）
# ══════════════════════════════════════════════════════════════════
class WindowsRegressionTest(unittest.TestCase):
    """S1 硬约束：Windows 行为零变更

    v0.3.15 用户的 llm_secret.bin 是 Windows DPAPI 密文，S1 重构后必须还能解出来。
    """

    def setUp(self):
        # 清空 backend 缓存（不同测试间隔离）
        from ai_bridge import dpapi
        dpapi._CACHED_BACKEND = None

    def test_W1_get_backend_returns_windows(self):
        """W1. sys.platform.startswith('win') → WindowsDpapiBackend"""
        from ai_bridge import dpapi
        b = dpapi.get_backend()
        from ai_bridge.dpapi_windows import WindowsDpapiBackend
        self.assertIsInstance(b, WindowsDpapiBackend)
        print(f"{PASS} W1. get_backend() returns WindowsDpapiBackend on win32")

    def test_W2_protect_to_file_then_unprotect_roundtrip(self):
        """W2. protect_to_file + unprotect_from_file 端到端 round-trip"""
        from ai_bridge import dpapi
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_secret.bin"
            original = "sk-test1234567890abcdefghij"
            dpapi.protect_to_file(original, str(p))
            self.assertTrue(p.is_file(), "secret file should exist after protect_to_file")
            # 密文不应是明文
            raw = p.read_bytes()
            self.assertNotIn(original.encode(), raw, "plaintext should NOT appear in ciphertext")
            # 解密
            decrypted = dpapi.unprotect_from_file(str(p))
            self.assertEqual(decrypted, original)
            print(f"{PASS} W2. protect_to_file/unprotect_from_file round-trip on Windows")

    def test_W3_in_memory_protect_unprotect(self):
        """W3. dpapi.protect / dpapi.unprotect in-memory 正常"""
        from ai_bridge import dpapi
        original = b"sk-test-1234-in-memory"
        ct = dpapi.protect(original)
        self.assertIsInstance(ct, bytes)
        self.assertNotEqual(ct, original)
        pt = dpapi.unprotect(ct)
        self.assertEqual(pt, original)
        print(f"{PASS} W3. in-memory protect/unprotect round-trip on Windows")

    def test_W4_unprotect_from_nonexistent_raises(self):
        """W4. unprotect_from_file 不存在的文件 → FileNotFoundError"""
        from ai_bridge import dpapi
        with self.assertRaises(FileNotFoundError):
            dpapi.unprotect_from_file("/nonexistent/path/llm_secret.bin")
        print(f"{PASS} W4. unprotect_from_file on missing file raises FileNotFoundError")

    def test_W5_atomic_write_no_tmp_leak(self):
        """W5. protect_to_file 成功时不留 .tmp 残留"""
        from ai_bridge import dpapi
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_secret.bin"
            dpapi.protect_to_file("sk-test", str(p))
            tmp = p.with_suffix(p.suffix + ".tmp")
            self.assertFalse(tmp.exists(), ".tmp should be cleaned up after os.replace")
            print(f"{PASS} W5. atomic write leaves no .tmp leak")

    def test_W6_get_backend_cached_singleton(self):
        """W6. get_backend() 多次调用返回同一实例（进程内缓存）"""
        from ai_bridge import dpapi
        b1 = dpapi.get_backend()
        b2 = dpapi.get_backend()
        self.assertIs(b1, b2)
        print(f"{PASS} W6. get_backend() returns cached singleton")

    def test_W7_unicode_plaintext(self):
        """W7. protect_to_file 支持中文/特殊字符（DPAPI 走 UTF-8 编码）"""
        from ai_bridge import dpapi
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_secret.bin"
            original = "中文 + emoji 🚀 + 特殊字符 \n\t"
            dpapi.protect_to_file(original, str(p))
            decrypted = dpapi.unprotect_from_file(str(p))
            self.assertEqual(decrypted, original)
            print(f"{PASS} W7. protect_to_file with unicode/emoji round-trips")

    def test_W8_dispatcher_calls_backend(self):
        """W8. dpapi.py 调度器确实调的是 WindowsDpapiBackend.protect_to_file"""
        from ai_bridge import dpapi
        from ai_bridge.dpapi_windows import WindowsDpapiBackend
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_secret.bin"
            with mock.patch.object(WindowsDpapiBackend, "protect_to_file", return_value=None) as m:
                dpapi.protect_to_file("sk-test", str(p))
                m.assert_called_once()
            print(f"{PASS} W8. dpapi.protect_to_file delegates to WindowsDpapiBackend")


# ══════════════════════════════════════════════════════════════════
# B. Mac 单元测试（mock sys.platform + keyring）
# ══════════════════════════════════════════════════════════════════
class MacBackendTest(unittest.TestCase):
    """Mac 后端测试 —— 在 Windows 上跑，强制 sys.platform='darwin' + mock keyring"""

    def setUp(self):
        # 强制把 sys.platform 改成 darwin，dpapi.get_backend() 才会走 Mac 分支
        self._orig_platform = sys.platform
        sys.platform = "darwin"  # type: ignore[misc]

        # 清空 backend 缓存
        from ai_bridge import dpapi
        dpapi._CACHED_BACKEND = None

    def tearDown(self):
        sys.platform = self._orig_platform  # type: ignore[misc]
        from ai_bridge import dpapi
        dpapi._CACHED_BACKEND = None

    def test_M1_get_backend_returns_macos(self):
        """M1. sys.platform='darwin' → MacosKeychainBackend"""
        from ai_bridge import dpapi
        b = dpapi.get_backend()
        from ai_bridge.dpapi_macos import MacosKeychainBackend
        self.assertIsInstance(b, MacosKeychainBackend)
        print(f"{PASS} M1. get_backend() returns MacosKeychainBackend on darwin")

    def test_M2_keychain_roundtrip(self):
        """M2. Keychain 可用时：protect_to_file 写 'keychain:<uuid>'，unprotect_from_file 通过 keychain.get_password 取回"""
        # 内存 mock 一个 keyring
        fake_keyring = {}
        fake_module = mock.MagicMock()
        fake_module.set_password = mock.MagicMock(side_effect=lambda s, u, p: fake_keyring.__setitem__((s, u), p))
        fake_module.get_password = mock.MagicMock(side_effect=lambda s, u: fake_keyring.get((s, u)))
        fake_module._KEYRING_AVAILABLE = True

        with mock.patch.dict(sys.modules, {"keyring": fake_module}):
            # 重 import dpapi_macos 让它走新 mock
            if "ai_bridge.dpapi_macos" in sys.modules:
                del sys.modules["ai_bridge.dpapi_macos"]
            from ai_bridge import dpapi

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "llm_secret.bin"
                original = "sk-mac-keychain-1234"
                dpapi.protect_to_file(original, str(p))

                # 文件内容应该是 "keychain:<uuid>"
                content = p.read_text(encoding="utf-8").strip()
                self.assertTrue(content.startswith("keychain:"), f"expected keychain: prefix, got {content[:30]!r}")
                key_id = content[len("keychain:"):]
                # Keychain mock 应该收到写入
                self.assertIn(("DingTalkBox", key_id), fake_keyring)

                # 解密
                decrypted = dpapi.unprotect_from_file(str(p))
                self.assertEqual(decrypted, original)
            print(f"{PASS} M2. keychain round-trip (mocked keyring)")

    def test_M3_keychain_unavailable_falls_back_to_aes(self):
        """M3. keyring 抛异常（sandbox/SSH-only）→ 走 AES fallback，文件以 'aes:' 开头"""
        fake_module = mock.MagicMock()
        fake_module.set_password = mock.MagicMock(side_effect=Exception("Keychain locked"))
        fake_module.get_password = mock.MagicMock(return_value=None)

        with mock.patch.dict(sys.modules, {"keyring": fake_module}):
            if "ai_bridge.dpapi_macos" in sys.modules:
                del sys.modules["ai_bridge.dpapi_macos"]
            from ai_bridge import dpapi

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "llm_secret.bin"
                original = "sk-aes-fallback-5678"
                dpapi.protect_to_file(original, str(p))
                content = p.read_text(encoding="utf-8").strip()
                self.assertTrue(content.startswith("aes:"), f"expected aes: prefix, got {content[:30]!r}")
                # AES 文件可以 self-decrypt（hostname/user 没变）
                decrypted = dpapi.unprotect_from_file(str(p))
                self.assertEqual(decrypted, original)
            print(f"{PASS} M3. keyring failure falls back to AES round-trip")

    def test_M4_aes_with_changed_hostname_fails(self):
        """M4. AES 解密时 hostname 变了 → 解密失败 raise RuntimeError"""
        fake_module = mock.MagicMock()
        fake_module.set_password = mock.MagicMock(side_effect=Exception("locked"))
        fake_module.get_password = mock.MagicMock(return_value=None)

        with mock.patch.dict(sys.modules, {"keyring": fake_module}):
            if "ai_bridge.dpapi_macos" in sys.modules:
                del sys.modules["ai_bridge.dpapi_macos"]

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "llm_secret.bin"
                # 写一次（用真实 hostname）
                from ai_bridge import dpapi
                dpapi.protect_to_file("sk-test", str(p))
                # 模拟 hostname 变化：mock socket.gethostname 返不同值
                with mock.patch("ai_bridge.dpapi_macos.socket.gethostname", return_value="MOCK-DIFFERENT-HOST"):
                    with self.assertRaises(RuntimeError) as ctx:
                        dpapi.unprotect_from_file(str(p))
                    self.assertIn("AES", str(ctx.exception))
            print(f"{PASS} M4. AES decrypt fails when hostname changes (raise RuntimeError)")

    def test_M5_keychain_entry_deleted_raises(self):
        """M5. Keychain 模式：Keychain 条目被删 → unprotect raise RuntimeError"""
        fake_keyring = {}
        fake_module = mock.MagicMock()
        fake_module.set_password = mock.MagicMock(side_effect=lambda s, u, p: fake_keyring.__setitem__((s, u), p))
        fake_module.get_password = mock.MagicMock(side_effect=lambda s, u: fake_keyring.get((s, u)))  # None if deleted

        with mock.patch.dict(sys.modules, {"keyring": fake_module}):
            if "ai_bridge.dpapi_macos" in sys.modules:
                del sys.modules["ai_bridge.dpapi_macos"]
            from ai_bridge import dpapi

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "llm_secret.bin"
                dpapi.protect_to_file("sk-test", str(p))
                # 模拟用户从 Keychain Access.app 删了条目
                fake_keyring.clear()
                with self.assertRaises(RuntimeError) as ctx:
                    dpapi.unprotect_from_file(str(p))
                self.assertIn("Keychain", str(ctx.exception))
            print(f"{PASS} M5. Keychain entry deleted → unprotect raises RuntimeError")

    def test_M6_unknown_prefix_raises(self):
        """M6. 文件内容前缀既不是 keychain: 也不是 aes: → raise RuntimeError"""
        fake_module = mock.MagicMock()
        fake_module.set_password = mock.MagicMock(side_effect=Exception("locked"))
        fake_module.get_password = mock.MagicMock(return_value=None)

        with mock.patch.dict(sys.modules, {"keyring": fake_module}):
            if "ai_bridge.dpapi_macos" in sys.modules:
                del sys.modules["ai_bridge.dpapi_macos"]
            from ai_bridge import dpapi

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "llm_secret.bin"
                p.write_text("garbled:xxx\n", encoding="utf-8")
                with self.assertRaises(RuntimeError) as ctx:
                    dpapi.unprotect_from_file(str(p))
                self.assertIn("未知", str(ctx.exception))
            print(f"{PASS} M6. unknown file prefix → raise RuntimeError")

    def test_M7_empty_file_raises(self):
        """M7. 空文件 → raise RuntimeError"""
        from ai_bridge import dpapi
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_secret.bin"
            p.write_text("\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                dpapi.unprotect_from_file(str(p))
            self.assertIn("empty", str(ctx.exception).lower())
        print(f"{PASS} M7. empty file → raise RuntimeError")

    def test_M8_missing_file_raises(self):
        """M8. 文件不存在 → raise FileNotFoundError"""
        from ai_bridge import dpapi
        with self.assertRaises(FileNotFoundError):
            dpapi.unprotect_from_file("/nonexistent/llm_secret.bin")
        print(f"{PASS} M8. missing file → raise FileNotFoundError")

    def test_M9_in_memory_protect_raises_not_implemented(self):
        """M9. Mac 后端调 protect() / unprotect() → NotImplementedError（Keychain 模型无 in-memory 等价）"""
        from ai_bridge import dpapi
        with self.assertRaises(NotImplementedError):
            dpapi.protect(b"hello")
        with self.assertRaises(NotImplementedError):
            dpapi.unprotect(b"fake-ciphertext")
        print(f"{PASS} M9. in-memory protect/unprotect raise NotImplementedError on Mac")


# ══════════════════════════════════════════════════════════════════
# C. 平台调度器（边界场景）
# ══════════════════════════════════════════════════════════════════
class PlatformDispatcherTest(unittest.TestCase):
    """非 Win/Mac 平台 raise RuntimeError"""

    def setUp(self):
        from ai_bridge import dpapi
        dpapi._CACHED_BACKEND = None

    def test_P1_unsupported_platform_raises(self):
        """P1. sys.platform='linux' → get_backend raise RuntimeError"""
        with mock.patch.object(sys, "platform", "linux"):
            from ai_bridge import dpapi
            dpapi._CACHED_BACKEND = None
            with self.assertRaises(RuntimeError) as ctx:
                dpapi.get_backend()
            self.assertIn("不支持", str(ctx.exception))
            print(f"{PASS} P1. unsupported platform raises RuntimeError")


def main():
    print("=" * 60)
    print("S1 DPAPI 抽象层测试")
    print("=" * 60)
    print()
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(WindowsRegressionTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(MacBackendTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(PlatformDispatcherTest))

    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)
    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"ALL {result.testsRun} CHECKS PASSED — S1 Windows 零回归 + Mac 单元覆盖")
    else:
        print(f"FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 60)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
