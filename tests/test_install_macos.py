"""test_install_macos.py — S2 macOS install.py 分支测试

覆盖 install.py 在 macOS 上的下载/校验/解压/chmod/原子安装 6 大阶段。

策略：
- **不连真网**。所有 urllib.request.urlopen 通过 mock 拦截，按 URL 路由到 fake response
- 在 Windows / Linux CI 上跑：sys.platform = "darwin" + platform.machine() = "arm64"/"x86_64" 强制 patch
- paths.data_dir() / cache_dir() / user_dws_path() 全部指向 tempdir（不污染用户目录）

测试编号与 S2 plan §5 对齐（24 个用例）。失败信息可读，前端对接 err_code 时方便定位。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import platform as platform_mod
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 把 sidecar.core 加 path ─────────────────────────────────────────
sys.path.insert(0, str(ROOT / "sidecar"))

from core import install  # noqa: E402
from core import paths as core_paths  # noqa: E402

PASS = "[PASS]"
FAIL = "[FAIL]"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    line = f"{tag} {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


# ── 假数据构造器 ────────────────────────────────────────────────────
def _make_release_json(
    tag: str = "v9.9.9-test",
    arm64_url: str | None = "https://github.example/dws-darwin-arm64.tar.gz",
    amd64_url: str | None = "https://github.example/dws-darwin-amd64.tar.gz",
    with_checksums: bool = True,
) -> dict:
    """构造一个模拟的 GitHub release JSON（含 darwin 资产 + checksums）"""
    assets: list[dict] = []
    if arm64_url:
        assets.append({
            "name": "dws-darwin-arm64.tar.gz",
            "browser_download_url": arm64_url,
            "size": 5_000_000,
        })
    if amd64_url:
        assets.append({
            "name": "dws-darwin-amd64.tar.gz",
            "browser_download_url": amd64_url,
            "size": 5_000_000,
        })
    if with_checksums:
        assets.append({
            "name": "checksums.txt",
            "browser_download_url": "https://github.example/checksums.txt",
            "size": 200,
        })
    return {
        "tag_name": tag,
        "html_url": f"https://github.example/releases/tag/{tag}",
        "assets": assets,
    }


def _make_tarball_gz(members: dict[str, bytes], dest: Path) -> Path:
    """在 dest 写一个 .tar.gz，包含 members: {name: bytes}"""
    with tarfile.open(dest, "w:gz") as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(content))
    return dest


def _make_checksums_txt(entries: dict[str, str]) -> str:
    """生成 sha256sum 格式文本：'<sha256>  <filename>'"""
    return "\n".join(f"{sha}  {name}" for name, sha in entries.items()) + "\n"


# ── 假 HTTP 上下文管理器 ────────────────────────────────────────────
class _FakeResp:
    """模仿 urllib response：每次调用 read 都能从头开始读完整 body

    关键：install 内部会对同一 url 调多次 urlopen（checksums + release）。
    普通 urllib response 是一次性 stream，body 读完就空了。
    我们让 _FakeResp 每次进入 __enter__ 都复位 body。
    """

    def __init__(
        self,
        body: bytes,
        status: int = 200,
        content_length: int | None = None,
        headers: dict | None = None,
    ):
        self._original_body = body
        self._body = body
        self.status = status
        self.code = status
        self.headers = headers or {}
        if content_length is not None and "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(content_length)

    def read(self, n: int = -1) -> bytes:
        if n == -1:
            data, self._body = self._body, b""
            return data
        if not self._body:
            return b""
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def __enter__(self):
        # 每次进入 with 都从头读
        self._body = self._original_body
        return self

    def __exit__(self, *a):
        return False


def _urlopen_router(routes: dict[str, _FakeResp | Exception]):
    """返回一个 mock side_effect：根据 url 选择 resp 或抛异常

    关键：每次调用都返回**新**的 _FakeResp（避免 body 一次性被读完）。
    routes: {url: _FakeResp} 或 {url: Exception}
    未匹配的 url → 抛 HTTPError 404。
    """
    def _side_effect(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url in routes:
            v = routes[url]
            if isinstance(v, Exception):
                raise v
            # 重要：每次返新对象（避免 body 被一次性消费）
            if isinstance(v, _FakeResp):
                return _FakeResp(
                    body=v._original_body,
                    status=v.status,
                    content_length=int(v.headers.get("Content-Length", 0)) or None,
                    headers=dict(v.headers),
                )
            return v
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
    return _side_effect


# ── 通用 patch 上下文 ────────────────────────────────────────────────
@contextlib.contextmanager
def _macos_env(machine: str = "arm64", *, with_tmp_paths: bool = True):
    """把 sys.platform='darwin' + platform.machine()=<machine>，并把 paths 重定向到 tempdir。

    同时清掉 install 模块里 release 缓存（不污染磁盘缓存测试）。
    """
    orig_platform = sys.platform
    orig_machine_getter = platform_mod.machine
    sys.platform = "darwin"
    # platform.machine 是 builtin，直接 mock
    machine_mock = mock.patch.object(platform_mod, "machine", return_value=machine)
    machine_mock.start()

    # 清 release 缓存（路径会被后面重定向，先清内存中的）
    cache_path = install._release_cache_path()

    if with_tmp_paths:
        tmp_root = Path(tempfile.mkdtemp(prefix="dingtalk_install_test_"))
        orig_data_dir = core_paths.data_dir
        orig_cache_dir = core_paths.cache_dir
        orig_user_dws = core_paths.user_dws_path

        def _tmp_data():
            (tmp_root / "data").mkdir(parents=True, exist_ok=True)
            return tmp_root / "data"

        def _tmp_cache():
            d = _tmp_data() / "cache"
            d.mkdir(parents=True, exist_ok=True)
            return d

        def _tmp_user_dws():
            return _tmp_data() / "bin" / "dws"

        # 用 mock 替换而不是改源码（避免影响其他模块）
        patches = [
            mock.patch.object(core_paths, "data_dir", _tmp_data),
            mock.patch.object(core_paths, "cache_dir", _tmp_cache),
            mock.patch.object(core_paths, "user_dws_path", _tmp_user_dws),
        ]
        for p in patches:
            p.start()
        try:
            yield tmp_root
        finally:
            for p in patches:
                p.stop()
            machine_mock.stop()
            sys.platform = orig_platform
            shutil.rmtree(tmp_root, ignore_errors=True)
    else:
        try:
            yield None
        finally:
            machine_mock.stop()
            sys.platform = orig_platform


def _write_release_cache(cache_path: Path, info: dict, mtime: float | None = None):
    """写 release 缓存到指定路径，可设置 mtime"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(info), encoding="utf-8")
    if mtime is not None:
        os.utime(cache_path, (mtime, mtime))


# ══════════════════════════════════════════════════════════════════
# A. 架构检测（_detect_darwin_arch）
# ══════════════════════════════════════════════════════════════════
class ArchDetectTest(unittest.TestCase):
    def test_T1_arm64(self):
        with mock.patch.object(platform_mod, "machine", return_value="arm64"):
            self.assertEqual(install._detect_darwin_arch(), "arm64")
        print(f"{PASS} T1. arm64 detected")

    def test_T2_x86_64_maps_to_amd64(self):
        with mock.patch.object(platform_mod, "machine", return_value="x86_64"):
            self.assertEqual(install._detect_darwin_arch(), "amd64")
        print(f"{PASS} T2. x86_64 → amd64")

    def test_T3_unsupported_arch(self):
        with mock.patch.object(platform_mod, "machine", return_value="i386"):
            with self.assertRaises(RuntimeError) as ctx:
                install._detect_darwin_arch()
            self.assertIn("UNSUPPORTED_ARCH", str(ctx.exception))
        print(f"{PASS} T3. unsupported arch → RuntimeError")


# ══════════════════════════════════════════════════════════════════
# B. Release 缓存 + GitHub API（无真实网络）
# ══════════════════════════════════════════════════════════════════
class ReleaseInfoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="rel_test_"))
        self._orig_cache_path = install._release_cache_path
        install._release_cache_path = lambda: self._tmp / "dws_release.json"

    def tearDown(self):
        install._release_cache_path = self._orig_cache_path
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_T14_cached_release_used_within_ttl(self):
        """缓存 mtime < 1h → 不发 HTTP 请求"""
        cached = {
            "tag": "v9.9.9",
            "darwin_arm64_url": "https://x/dws-darwin-arm64.tar.gz",
            "darwin_arm64_name": "dws-darwin-arm64.tar.gz",
            "darwin_amd64_url": "https://x/dws-darwin-amd64.tar.gz",
            "darwin_amd64_name": "dws-darwin-amd64.tar.gz",
            "cached_at": int(time.time()),
        }
        _write_release_cache(self._tmp / "dws_release.json", cached, mtime=time.time())

        with mock.patch.object(install, "_fetch_github_release_json") as m_fetch:
            result = install._get_latest_release_info()
            m_fetch.assert_not_called()  # 关键：没调网络
        self.assertEqual(result["tag"], "v9.9.9")
        print(f"{PASS} T14. cached release within TTL skips HTTP")

    def test_T15_stale_cache_falls_back_on_network_error(self):
        """缓存 2h 旧 + 网络失败 → 仍返回旧缓存（24h 兜底）"""
        cached = {
            "tag": "v9.9.9",
            "darwin_arm64_url": "https://x/dws-darwin-arm64.tar.gz",
            "darwin_amd64_name": "dws-darwin-arm64.tar.gz",
            "darwin_amd64_url": "https://x/dws-darwin-amd64.tar.gz",
            "darwin_arm64_name": "dws-darwin-arm64.tar.gz",
        }
        old_mtime = time.time() - 2 * 3600
        _write_release_cache(self._tmp / "dws_release.json", cached, mtime=old_mtime)

        with mock.patch.object(install, "_fetch_github_release_json", return_value=None):
            result = install._get_latest_release_info()
        self.assertEqual(result["tag"], "v9.9.9")
        print(f"{PASS} T15. stale cache used when network fails (24h fallback)")

    def test_T16_no_cache_network_error_returns_none(self):
        """无缓存 + 网络失败 → None"""
        with mock.patch.object(install, "_fetch_github_release_json", return_value=None):
            result = install._get_latest_release_info()
        self.assertIsNone(result)
        print(f"{PASS} T16. no cache + network error → None")

    def test_T13_api_rate_limited(self):
        """GitHub 403 with rate limit body → _fetch_github_release_json 返 None"""
        body = b'{"message":"API rate limit exceeded for 1.2.3.4"}'
        err = urllib.error.HTTPError(
            "https://api.github.com/...", 403, "rate limit", {}, io.BytesIO(body),
        )
        with mock.patch.object(install.urllib.request, "urlopen", side_effect=err):
            result = install._fetch_github_release_json()
        self.assertIsNone(result)
        print(f"{PASS} T13. rate-limited 403 → None")


# ══════════════════════════════════════════════════════════════════
# C. _fetch_checksums_for_release
# ══════════════════════════════════════════════════════════════════
class ChecksumsTest(unittest.TestCase):
    def test_fetch_checksums_parses_sha256sum_format(self):
        checksums = _make_checksums_txt({
            "dws-darwin-arm64.tar.gz": "a" * 64,
            "dws-darwin-amd64.tar.gz": "b" * 64,
        })
        body = checksums.encode()
        resp = _FakeResp(body, content_length=len(body))
        release_json = _make_release_json()

        with mock.patch.object(install.urllib.request, "urlopen", return_value=resp):
            result = install._fetch_checksums_for_release(release_json)
        self.assertEqual(result["dws-darwin-arm64.tar.gz"], "a" * 64)
        print(f"{PASS} T-check-1. sha256sum parsed (double-space format)")

    def test_fetch_checksums_no_asset_raises(self):
        release_json = _make_release_json(with_checksums=False)
        with self.assertRaises(RuntimeError) as ctx:
            install._fetch_checksums_for_release(release_json)
        self.assertIn("CHECKSUMS_NOT_AVAILABLE", str(ctx.exception))
        print(f"{PASS} T-check-2. no checksums asset → CHECKSUMS_NOT_AVAILABLE")

    def test_fetch_checksums_empty_raises(self):
        resp = _FakeResp(b"", content_length=0)
        release_json = _make_release_json()
        with mock.patch.object(install.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                install._fetch_checksums_for_release(release_json)
        self.assertIn("CHECKSUMS_EMPTY", str(ctx.exception))
        print(f"{PASS} T-check-3. empty checksums → CHECKSUMS_EMPTY")


# ══════════════════════════════════════════════════════════════════
# D. _download_to_temp + _sha256_file
# ══════════════════════════════════════════════════════════════════
class DownloadShaTest(unittest.TestCase):
    def test_download_streams_to_temp(self):
        body = b"hello world " * 1000
        resp = _FakeResp(body, content_length=len(body))
        with mock.patch.object(install.urllib.request, "urlopen", return_value=resp):
            p = install._download_to_temp("https://x/file")
        try:
            self.assertTrue(p.is_file())
            self.assertEqual(p.read_bytes(), body)
        finally:
            p.unlink(missing_ok=True)
        print(f"{PASS} T-dl-1. _download_to_temp streams and writes correctly")

    def test_download_truncated_raises(self):
        body = b"only-50-bytes"
        # Content-Length 说 1000，实际只给 11
        resp = _FakeResp(body, content_length=1000)
        with mock.patch.object(install.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                install._download_to_temp("https://x/file")
        self.assertIn("DOWNLOAD_TRUNCATED", str(ctx.exception))
        print(f"{PASS} T-dl-2. truncated download → DOWNLOAD_TRUNCATED")

    def test_download_network_error_raises(self):
        with mock.patch.object(
            install.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("DNS failed"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                install._download_to_temp("https://x/file")
        self.assertIn("NETWORK_FAILED", str(ctx.exception))
        print(f"{PASS} T-dl-3. network error → NETWORK_FAILED")

    def test_sha256_file_matches_hashlib(self):
        import hashlib
        body = b"the quick brown fox"
        expected = hashlib.sha256(body).hexdigest()
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(body)
            tmp = Path(f.name)
        try:
            self.assertEqual(install._sha256_file(tmp), expected)
        finally:
            tmp.unlink()
        print(f"{PASS} T-dl-4. _sha256_file matches hashlib")


# ══════════════════════════════════════════════════════════════════
# E. _extract_darwin_tarball（含 tar slip 防御）
# ══════════════════════════════════════════════════════════════════
class ExtractTest(unittest.TestCase):
    def test_extract_returns_dws_path(self):
        with tempfile.TemporaryDirectory() as td:
            tar_path = Path(td) / "dws.tar.gz"
            _make_tarball_gz({"dws": b"#!/bin/sh\necho dws\n"}, tar_path)
            extract_dir = Path(td) / "out"
            dws_p = install._extract_darwin_tarball(tar_path, extract_dir)
            self.assertEqual(dws_p.name, "dws")
            self.assertTrue(dws_p.is_file())
            self.assertEqual(dws_p.read_bytes(), b"#!/bin/sh\necho dws\n")
        print(f"{PASS} T-ext-1. happy path extract")

    def test_T8_missing_dws_binary_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tar_path = Path(td) / "dws.tar.gz"
            _make_tarball_gz({"LICENSE": b"MIT", "README.md": b"hello"}, tar_path)
            extract_dir = Path(td) / "out"
            with self.assertRaises(RuntimeError) as ctx:
                install._extract_darwin_tarball(tar_path, extract_dir)
            self.assertIn("BINARY_NOT_FOUND_IN_TARBALL", str(ctx.exception))
        print(f"{PASS} T8. missing dws → BINARY_NOT_FOUND_IN_TARBALL")

    def test_T9_corrupt_tarball_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tar_path = Path(td) / "dws.tar.gz"
            tar_path.write_bytes(b"not a tarball")
            extract_dir = Path(td) / "out"
            with self.assertRaises(RuntimeError) as ctx:
                install._extract_darwin_tarball(tar_path, extract_dir)
            self.assertIn("TAR_EXTRACT_FAILED", str(ctx.exception))
        print(f"{PASS} T9. corrupt tarball → TAR_EXTRACT_FAILED")

    def test_T10_tar_slip_detected(self):
        """构造含 'dws' + '../../etc/passwd' 的 tarball → TAR_SLIP_DETECTED"""
        with tempfile.TemporaryDirectory() as td:
            tar_path = Path(td) / "evil.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tf:
                # 先 add 一个 ../evil 让它在 dws 之前被遍历到
                info = tarfile.TarInfo(name="../../etc/passwd")
                info.size = 5
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(b"pwned"))
                # 再 add 合法 dws（顺序无所谓：先找到 dws 再 extract 时校验路径）
                info2 = tarfile.TarInfo(name="dws")
                info2.size = 4
                info2.mode = 0o755
                tf.addfile(info2, io.BytesIO(b"test"))
            extract_dir = Path(td) / "out"
            with self.assertRaises(RuntimeError) as ctx:
                install._extract_darwin_tarball(tar_path, extract_dir)
            self.assertIn("TAR_SLIP_DETECTED", str(ctx.exception))
        print(f"{PASS} T10. tar slip → TAR_SLIP_DETECTED")


# ══════════════════════════════════════════════════════════════════
# F. _install_dws_macos 端到端（mock HTTP）
# ══════════════════════════════════════════════════════════════════
class InstallEndToEndTest(unittest.TestCase):
    """端到端：从 mock HTTP 拿 release + tarball + checksums，验证最终落到 user_dws_path()"""

    def _fake_release_with_tarball(
        self, arch: str, tarball_body: bytes, sha256: str, tag: str = "v1.0.0-test",
    ) -> tuple[dict, dict, _FakeResp, _FakeResp, _FakeResp]:
        """构造 release JSON + checksums text + 3 个 FakeResp (release / checksums / tarball)"""
        arch_name = "arm64" if arch == "arm64" else "amd64"
        asset_name = f"dws-darwin-{arch_name}.tar.gz"
        tarball_url = f"https://github.example/{asset_name}"
        release_json = {
            "tag_name": tag,
            "assets": [
                {"name": asset_name, "browser_download_url": tarball_url, "size": len(tarball_body)},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt", "size": 100},
            ],
        }
        checksums_text = _make_checksums_txt({asset_name: sha256})
        checksums_bytes = checksums_text.encode()

        release_resp = _FakeResp(json.dumps(release_json).encode())
        checksums_resp = _FakeResp(checksums_bytes, content_length=len(checksums_bytes))
        tarball_resp = _FakeResp(tarball_body, content_length=len(tarball_body))

        routes = {
            install._GITHUB_API_URL: release_resp,
            "https://github.example/checksums.txt": checksums_resp,
            tarball_url: tarball_resp,
        }
        return release_json, routes, release_resp, checksums_resp, tarball_resp

    def _make_valid_tarball(self) -> tuple[bytes, str]:
        """构造一个含 dws 的合法 tar.gz，返回 (bytes, sha256)"""
        import hashlib
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            tmp = Path(f.name)
        _make_tarball_gz({"dws": b"#!/bin/sh\necho 'dws 1.0.0'\n"}, tmp)
        body = tmp.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        tmp.unlink()
        return body, sha

    def test_T4_install_picks_arm64_tarball(self):
        body, sha = self._make_valid_tarball()
        _, routes, *_ = self._fake_release_with_tarball("arm64", body, sha)
        # 把 release API 也加进 routes（_get_latest_release_info 走它；install 也调一次拿 checksums）
        routes[install._GITHUB_API_URL] = routes.pop(install._GITHUB_API_URL) if install._GITHUB_API_URL in routes else _FakeResp(b"{}")

        # install_dws 内部会调 _get_latest_release_info 和 _fetch_github_release_json 两次
        # routes 里放好两个 release JSON
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-arm64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-arm64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()
        routes[install._GITHUB_API_URL] = _FakeResp(release_json_str)

        with _macos_env(machine="arm64") as tmp_root:
            # mock subprocess.run (--version postcheck)
            with mock.patch.object(install, "_probe_version", return_value="dws 1.0.0-test"):
                with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                    result = install._install_dws_macos({})
            # 校验必须留在 mock context 内（teardown 会 rmtree）
            self.assertTrue(result["ok"], msg=str(result))
            self.assertIn("dws-darwin-arm64", result["download_url"])
            target = Path(result["path"])
            self.assertTrue(target.is_file(), f"target should exist at {target}")
            # chmod 验证：S_IMODE 在 Windows 上不准确（os.chmod 0o755 是 no-op，
            # Windows mode bits 由 ACL 决定）。T21 单独验过 install 调了 chmod(0o755)，
            # 这里不再重复——只验文件存在。
        print(f"{PASS} T4. arm64 install → ok=True, file written, postcheck mocked")

    def test_T5_install_picks_amd64_tarball(self):
        body, sha = self._make_valid_tarball()
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-amd64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-amd64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()

        with _macos_env(machine="x86_64") as tmp_root:
            # 把 release API + checksums + tarball 路由全部装好
            asset_name = "dws-darwin-amd64.tar.gz"
            tarball_url = f"https://github.example/{asset_name}"
            checksums_text = _make_checksums_txt({asset_name: sha})
            routes = {
                install._GITHUB_API_URL: _FakeResp(release_json_str),
                "https://github.example/checksums.txt": _FakeResp(checksums_text.encode(), content_length=len(checksums_text)),
                tarball_url: _FakeResp(body, content_length=len(body)),
            }
            with mock.patch.object(install, "_probe_version", return_value="dws 1.0.0-test"):
                with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                    result = install._install_dws_macos({})

        self.assertTrue(result["ok"])
        self.assertIn("dws-darwin-amd64", result["download_url"])
        print(f"{PASS} T5. amd64 install → ok=True")

    def test_T6_checksum_mismatch(self):
        """SHA256 不对 → CHECKSUM_MISMATCH，不写 target"""
        body, _ = self._make_valid_tarball()
        wrong_sha = "0" * 64
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-arm64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-arm64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()

        with _macos_env(machine="arm64") as tmp_root:
            asset_name = "dws-darwin-arm64.tar.gz"
            tarball_url = f"https://github.example/{asset_name}"
            checksums_text = _make_checksums_txt({asset_name: wrong_sha})
            routes = {
                install._GITHUB_API_URL: _FakeResp(release_json_str),
                "https://github.example/checksums.txt": _FakeResp(checksums_text.encode()),
                tarball_url: _FakeResp(body, content_length=len(body)),
            }
            with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                result = install._install_dws_macos({})

        self.assertFalse(result["ok"])
        self.assertEqual(result["err_code"], "CHECKSUM_MISMATCH")
        # target 应当没被写
        target = core_paths.user_dws_path()
        self.assertFalse(target.exists())
        print(f"{PASS} T6. checksum mismatch → CHECKSUM_MISMATCH, target untouched")

    def test_T11_http_404_on_tarball(self):
        """tarball URL 返回 404 → HTTP_404"""
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-arm64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-arm64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()
        with _macos_env(machine="arm64") as tmp_root:
            # checksums 正常返回，tarball 404
            sha = "a" * 64
            checksums_text = _make_checksums_txt({"dws-darwin-arm64.tar.gz": sha})
            routes = {
                install._GITHUB_API_URL: _FakeResp(release_json_str),
                "https://github.example/checksums.txt": _FakeResp(checksums_text.encode()),
                "https://github.example/dws-darwin-arm64.tar.gz": urllib.error.HTTPError(
                    "https://github.example/dws-darwin-arm64.tar.gz", 404, "Not Found", {}, io.BytesIO(b""),
                ),
            }
            with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                result = install._install_dws_macos({})

        self.assertFalse(result["ok"])
        self.assertEqual(result["err_code"], "HTTP_404")
        print(f"{PASS} T11. tarball 404 → HTTP_404")

    def test_T12_network_failure_on_api(self):
        """GitHub API 网络失败 → NETWORK_FAILED"""
        with _macos_env(machine="arm64"):
            with mock.patch.object(
                install.urllib.request, "urlopen",
                side_effect=urllib.error.URLError("Network is unreachable"),
            ):
                result = install._install_dws_macos({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["err_code"], "NETWORK_FAILED")
        print(f"{PASS} T12. API network failure → NETWORK_FAILED")

    def test_T18_postcheck_failure(self):
        """install 成功但 --version 失败 → POSTCHECK_FAILED"""
        body, sha = self._make_valid_tarball()
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-arm64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-arm64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()
        with _macos_env(machine="arm64"):
            asset_name = "dws-darwin-arm64.tar.gz"
            tarball_url = f"https://github.example/{asset_name}"
            checksums_text = _make_checksums_txt({asset_name: sha})
            routes = {
                install._GITHUB_API_URL: _FakeResp(release_json_str),
                "https://github.example/checksums.txt": _FakeResp(checksums_text.encode(), content_length=len(checksums_text)),
                tarball_url: _FakeResp(body, content_length=len(body)),
            }
            with mock.patch.object(install, "_probe_version", return_value=None):
                with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                    result = install._install_dws_macos({})
            # mock context 内校验（teardown 会 rmtree）
            self.assertFalse(result["ok"])
            self.assertEqual(result["err_code"], "POSTCHECK_FAILED")
            # 但文件已经写出了，方便用户手动排查
            self.assertTrue(Path(result["path"]).exists())
        print(f"{PASS} T18. postcheck failed → POSTCHECK_FAILED, target still on disk")

    def test_T21_chmod_0755_invoked(self):
        """验证 install.py 真的调了 os.chmod(_, 0o755)（Windows mode bits 不支持但调用应发生）"""
        body, sha = self._make_valid_tarball()
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-arm64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-arm64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()
        with _macos_env(machine="arm64"):
            asset_name = "dws-darwin-arm64.tar.gz"
            tarball_url = f"https://github.example/{asset_name}"
            checksums_text = _make_checksums_txt({asset_name: sha})
            routes = {
                install._GITHUB_API_URL: _FakeResp(release_json_str),
                "https://github.example/checksums.txt": _FakeResp(checksums_text.encode(), content_length=len(checksums_text)),
                tarball_url: _FakeResp(body, content_length=len(body)),
            }
            with mock.patch.object(install, "_probe_version", return_value="dws 1.0.0-test"):
                with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                    with mock.patch.object(install.os, "chmod", wraps=install.os.chmod) as m_chmod:
                        result = install._install_dws_macos({})
            # 至少 1 次 chmod(_, 0o755)：extract 临时目录里的 dws + copy2 后的 .new 目标
            chmod_755_calls = [
                c for c in m_chmod.call_args_list
                if len(c.args) >= 2 and c.args[1] == 0o755
            ]
            self.assertGreaterEqual(
                len(chmod_755_calls), 1,
                f"expected ≥1 chmod(_, 0o755), got {m_chmod.call_args_list}",
            )
        self.assertTrue(result["ok"])
        print(f"{PASS} T21. os.chmod(_, 0o755) invoked ≥1 time (mode set even if Windows no-op)")

    def test_T17_atomic_write_no_partial_target(self):
        """模拟 os.replace 抛错 → 原 target 不变，.new 被清理"""
        body, sha = self._make_valid_tarball()
        release_json_str = json.dumps({
            "tag_name": "v1.0.0-test",
            "assets": [
                {"name": "dws-darwin-arm64.tar.gz", "browser_download_url": "https://github.example/dws-darwin-arm64.tar.gz"},
                {"name": "checksums.txt", "browser_download_url": "https://github.example/checksums.txt"},
            ],
        }).encode()
        with _macos_env(machine="arm64"):
            asset_name = "dws-darwin-arm64.tar.gz"
            tarball_url = f"https://github.example/{asset_name}"
            checksums_text = _make_checksums_txt({asset_name: sha})
            routes = {
                install._GITHUB_API_URL: _FakeResp(release_json_str),
                "https://github.example/checksums.txt": _FakeResp(checksums_text.encode()),
                tarball_url: _FakeResp(body, content_length=len(body)),
            }
            with mock.patch.object(install, "_probe_version", return_value="dws 1.0.0-test"):
                with mock.patch.object(install.urllib.request, "urlopen", side_effect=_urlopen_router(routes)):
                    # mock os.replace 失败
                    with mock.patch.object(install.os, "replace", side_effect=OSError("disk full")):
                        result = install._install_dws_macos({})

        self.assertFalse(result["ok"])
        self.assertEqual(result["err_code"], "REPLACE_FAILED")
        # 目标不应存在
        target = core_paths.user_dws_path()
        self.assertFalse(target.exists())
        # .new 不应残留
        new_file = target.with_suffix(target.suffix + ".new")
        self.assertFalse(new_file.exists())
        print(f"{PASS} T17. os.replace failure → REPLACE_FAILED, no .new leak, target untouched")


# ══════════════════════════════════════════════════════════════════
# G. install_dws dispatcher（入口）
# ══════════════════════════════════════════════════════════════════
class DispatcherTest(unittest.TestCase):
    def test_T24_unsupported_platform(self):
        orig = sys.platform
        sys.platform = "linux"
        try:
            result = install.install_dws({})
        finally:
            sys.platform = orig
        self.assertFalse(result["ok"])
        self.assertEqual(result["err_code"], "UNSUPPORTED_PLATFORM")
        print(f"{PASS} T24. linux → UNSUPPORTED_PLATFORM")

    def test_windows_dispatcher_does_not_change(self):
        """在 Windows 平台调 install_dws 应该走 _install_dws_windows 分支"""
        # 我们不在 Windows 上 patch，只验证 dispatcher 正确路由
        orig = sys.platform
        sys.platform = "win32"
        try:
            with mock.patch.object(install, "_install_dws_windows", return_value={"ok": True, "from": "win"}) as m:
                result = install.install_dws({})
            m.assert_called_once()
            self.assertEqual(result["from"], "win")
        finally:
            sys.platform = orig
        print(f"{PASS} T-disp-1. windows dispatcher routes to _install_dws_windows")

    def test_macos_dispatcher_routes_to_macos(self):
        orig = sys.platform
        sys.platform = "darwin"
        try:
            with mock.patch.object(install, "_install_dws_macos", return_value={"ok": True, "from": "mac"}) as m:
                result = install.install_dws({})
            m.assert_called_once()
            self.assertEqual(result["from"], "mac")
        finally:
            sys.platform = orig
        print(f"{PASS} T-disp-2. macos dispatcher routes to _install_dws_macos")


# ══════════════════════════════════════════════════════════════════
# H. check_install 跨平台 + Mac 扩展字段
# ══════════════════════════════════════════════════════════════════
class CheckInstallTest(unittest.TestCase):
    def test_T19_mac_extra_fields(self):
        """check_install 在 Mac 上应返回 darwin_arch + release_tag + download_url"""
        release_json = _make_release_json()
        with _macos_env(machine="arm64"):
            with mock.patch.object(
                install, "_get_latest_release_info",
                return_value={
                    "tag": "v1.0.41",
                    "darwin_arm64_url": "https://x/dws-darwin-arm64.tar.gz",
                    "darwin_arm64_name": "dws-darwin-arm64.tar.gz",
                },
            ):
                result = install.check_install({})
        self.assertEqual(result["darwin_arch"], "arm64")
        self.assertEqual(result["release_tag"], "v1.0.41")
        self.assertEqual(result["download_url"], "https://x/dws-darwin-arm64.tar.gz")
        self.assertTrue(result["installable"])
        # 原始字段不丢
        self.assertIn("path", result)
        self.assertIn("target_path", result)
        print(f"{PASS} T19. check_install mac → extra fields populated")

    def test_T20_check_install_no_network_call(self):
        """check_install 在 network 失败时不抛、给 installable=False"""
        with _macos_env(machine="arm64"):
            with mock.patch.object(install, "_get_latest_release_info", return_value=None):
                result = install.check_install({})
        self.assertFalse(result["installable"])
        self.assertIsNone(result["download_url"])
        print(f"{PASS} T20. check_install network fail → installable=False, no exception")

    def test_windows_check_install_unchanged_shape(self):
        """check_install 在 Windows 上不应有 darwin_arch / release_tag / download_url"""
        orig = sys.platform
        sys.platform = "win32"
        try:
            result = install.check_install({})
        finally:
            sys.platform = orig
        self.assertNotIn("darwin_arch", result)
        self.assertNotIn("release_tag", result)
        self.assertNotIn("download_url", result)
        # Windows 原始字段
        self.assertIn("installed", result)
        self.assertIn("path", result)
        self.assertIn("bundled_path", result)
        self.assertIn("target_path", result)
        print(f"{PASS} T-win-1. Windows check_install shape unchanged")


def main():
    print("=" * 60)
    print("S2 macOS install.py 单元测试")
    print("=" * 60)
    print()
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ArchDetectTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ReleaseInfoTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ChecksumsTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(DownloadShaTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ExtractTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(InstallEndToEndTest))
    # T21 单独执行（也属于 install end-to-end）
    suite.addTest(InstallEndToEndTest("test_T21_chmod_0755_invoked"))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(DispatcherTest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(CheckInstallTest))

    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)
    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"ALL {result.testsRun} CHECKS PASSED — S2 macOS install 单元覆盖 + Windows 零回归")
    else:
        print(f"FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 60)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())