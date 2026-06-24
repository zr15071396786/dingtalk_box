"""install.py — dws 一键安装（Windows 内嵌 copy / macOS GitHub release 下载）

场景：把工具分发给同 corp 同事，他们的机器上没有 dws。
本模块负责：
- check_install()：报告 dws 当前状态（装了 / 没装但内嵌可装 / 都没有）
- install_dws()：把 dws 装到 data_dir()/bin/dws[.exe]
  - **Windows**：从 frozen _MEIPASS/bin/dws.exe（或开发模式 bin/dws.exe）copy
  - **macOS**：从 GitHub release 下载 darwin tarball → 校验 sha256 → 解压 → chmod +x

为什么必须 copy 到 data_dir()/bin/（不是直接用 _MEIPASS 里的）：
1. frozen PyInstaller 的 _MEIPASS 在打包时已固化到 exe 资源里，运行时只读
2. 用户想升级 dws 时只换 data_dir()/bin/ 下那个文件
3. 工具重装/升级不影响已装 dws

macOS 端口（S2）：
- 不内嵌 dws（PyInstaller macOS 打包会把 5MB dws 烤进 .app，体积翻倍且 Gatekeeper 麻烦）
- 走下载：GitHub API → latest release → dws-darwin-{arm64,amd64}.tar.gz → checksums.txt → 校验
- 解压用 stdlib `tarfile`（无新依赖）
- 所有网络 IO 用 stdlib `urllib.request`（无新依赖）
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import logging_setup, paths

LOG = logging_setup.setup("install")


# ── 模块常量（macOS 下载源）────────────────────────────────────────────
_GITHUB_API_URL = (
    "https://api.github.com/repos/DingTalk-Real-AI/dingtalk-workspace-cli/releases/latest"
)
_GITHUB_DOWNLOAD_BASE = (
    "https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/download"
)
_RELEASE_CACHE_FILENAME = "dws_release.json"
_RELEASE_CACHE_TTL_SECONDS = 3600           # 1 小时
_RELEASE_CACHE_FALLBACK_SECONDS = 86400     # 24 小时（离线兜底）
_API_TIMEOUT_SECONDS = 60
_DOWNLOAD_TIMEOUT_SECONDS = 120
_DOWNLOAD_CHUNK_SIZE = 65536


# ── dws 解析辅助（Windows 共用）───────────────────────────────────────
def _bundled_dws_path() -> Path | None:
    """frozen _MEIPASS/bin/dws.exe 或 开发模式 项目根/bin/dws.exe / bin/dws

    macOS 端口（S2）：永远返回 None（不内嵌 dws）。开发者若在 bin/ 下手动
    放了 dws（罕见的 dev 模式调试），会被识别为 bundled——保留这个分支不影响。
    """
    if sys.platform.startswith("win"):
        if getattr(sys, "frozen", False):
            meipass = Path(getattr(sys, "_MEIPASS", ""))
            p = meipass / "bin" / "dws.exe"
            if p.is_file():
                return p
            return None
        root = Path(__file__).resolve().parent.parent.parent
        p = root / "bin" / "dws.exe"
        return p if p.is_file() else None
    # POSIX (macOS / Linux)：dev 模式项目根/bin/dws
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        p = meipass / "bin" / "dws"
        if p.is_file():
            return p
        return None
    root = Path(__file__).resolve().parent.parent.parent
    p = root / "bin" / "dws"
    return p if p.is_file() else None


def _probe_version(dws: str | Path) -> str | None:
    """调 `dws --version` 拿版本号"""
    try:
        proc = subprocess.run(
            [str(dws), "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        line = out.splitlines()[0] if out else None
        return line
    except Exception:
        return None


# ── 1. check_install（Windows 共用 + macOS 扩展字段）───────────────────
def check_install(_params: dict) -> dict:
    """报告 dws 当前状态（跨平台）"""
    resolved = paths.dws_exe_path()
    bundled = _bundled_dws_path()

    installed = False
    path: str | None = None
    version: str | None = None
    try:
        probe = shutil.which("dws") or (
            resolved if Path(resolved).exists() else None
        )
        if probe and Path(probe).exists():
            path = probe
            version = _probe_version(probe)
            installed = version is not None
    except Exception as e:  # noqa: BLE001
        LOG.info("dws probe failed", extra={"err": str(e)})

    installable = bundled is not None and bundled.is_file()
    bundled_size = bundled.stat().st_size if installable else 0

    target = paths.user_dws_path()  # 推荐安装位置

    result: dict[str, Any] = {
        "installed": installed,
        "path": path,
        "version": version,
        "installable": installable,
        "bundled_path": str(bundled) if bundled else None,
        "bundled_size": bundled_size,
        "target_path": str(target),
    }

    # macOS 端口（S2）：追加下载源信息
    if sys.platform == "darwin":
        try:
            arch = _detect_darwin_arch()
            release = _get_latest_release_info()
            result["darwin_arch"] = arch
            result["release_tag"] = release.get("tag") if release else None
            asset_key = f"darwin_{arch}"
            asset_url = release.get(f"{asset_key}_url") if release else None
            result["download_url"] = asset_url
            # Mac 上"是否可安装"=能否从 GitHub 拉到对应资产
            result["installable"] = asset_url is not None
        except Exception as e:  # noqa: BLE001
            LOG.info("macOS check_install extra fields failed", extra={"err": str(e)})
            result["darwin_arch"] = None
            result["release_tag"] = None
            result["download_url"] = None
            # 网络失败时仍允许点击（前端会显示"网络不可用，请稍后重试"）
            result["installable"] = False

    return result


# ── 2. install_dws（dispatcher）───────────────────────────────────────
def install_dws(_params: dict) -> dict:
    """把 dws 装到 data_dir()/bin/dws[.exe]（跨平台）"""
    if sys.platform.startswith("win"):
        return _install_dws_windows(_params)
    if sys.platform == "darwin":
        return _install_dws_macos(_params)
    return {
        "ok": False,
        "err_code": "UNSUPPORTED_PLATFORM",
        "err_msg": f"不支持的平台：{sys.platform}",
    }


# ── 2a. Windows 分支（保留 v0.3.15 全部行为，bit-for-bit）─────────────
def _install_dws_windows(_params: dict) -> dict:
    """把内嵌 dws.exe 装到 %APPDATA%/DingTalkBox/bin/dws.exe"""
    bundled = _bundled_dws_path()
    if not bundled or not bundled.is_file():
        return {
            "ok": False,
            "err_code": "NOT_BUNDLED",
            "err_msg": f"工具未携带 dws：{bundled}",
        }

    target = paths.user_dws_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    # 防御：target 已被占用（其他进程锁住）→ 报具体错
    if target.exists():
        try:
            with open(target, "ab") as f:
                f.seek(0, os.SEEK_END)
        except PermissionError as e:
            return {
                "ok": False,
                "err_code": "FILE_LOCKED",
                "err_msg": f"dws.exe 已被占用（请关闭钉钉相关进程后重试）：{e}",
            }

    # copy：先 copy 到 .new 再 rename，避免半途出错留半截文件
    tmp = target.with_suffix(target.suffix + ".new")
    try:
        shutil.copy2(bundled, tmp)
    except Exception as e:  # noqa: BLE001
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {
            "ok": False,
            "err_code": "COPY_FAILED",
            "err_msg": f"copy 失败 {bundled} → {tmp}: {e}",
        }

    try:
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {
            "ok": False,
            "err_code": "REPLACE_FAILED",
            "err_msg": f"rename {tmp} → {target} 失败: {e}",
        }

    version = _probe_version(target)
    if not version:
        return {
            "ok": False,
            "err_code": "POSTCHECK_FAILED",
            "err_msg": f"已 copy 到 {target}，但 --version 失败，请手动验证",
            "path": str(target),
        }

    LOG.info("dws installed", extra={"path": str(target), "version": version})
    return {
        "ok": True,
        "path": str(target),
        "version": version,
        "size": target.stat().st_size,
    }


# ══════════════════════════════════════════════════════════════════════
# 2b. macOS 分支（S2 新增）：从 GitHub release 下载安装
# ══════════════════════════════════════════════════════════════════════

# ── 架构检测 ────────────────────────────────────────────────────────
def _detect_darwin_arch() -> str:
    """返回 'arm64' (Apple Silicon) 或 'amd64' (Intel/Rosetta)

    映射规则：
      arm64  -> arm64   (M1/M2/M3/M4)
      x86_64 -> amd64   (Intel Mac 或 Rosetta 翻译下的 arm64)
    其他架构（i386 / arm64e）抛 RuntimeError → caller 报 UNSUPPORTED_ARCH。
    """
    m = platform.machine().lower()
    if m == "arm64":
        return "arm64"
    if m == "x86_64":
        return "amd64"
    raise RuntimeError(f"UNSUPPORTED_ARCH: {m}")


# ── GitHub release 信息（带磁盘缓存）─────────────────────────────────
def _release_cache_path() -> Path:
    """缓存文件路径：paths.cache_dir()/dws_release.json"""
    return paths.cache_dir() / _RELEASE_CACHE_FILENAME


def _get_latest_release_info(*, force_refresh: bool = False) -> dict | None:
    """拿 latest release 的精简 dict：tag + 4 个 darwin 资产 + sha256

    返回 None 表示网络/API 失败（caller 自行决定回退到缓存或报错）。

    缓存策略：
      - 缓存 mtime < 1h → 直接返回缓存
      - 缓存过期 → 拉 GitHub API；失败且缓存 mtime < 24h → 返回旧缓存
      - 缓存过期 + 网络失败 + 无可用缓存 → 返回 None
    """
    cache_p = _release_cache_path()

    # 1) 命中新鲜缓存
    if not force_refresh and cache_p.is_file():
        try:
            mtime = cache_p.stat().st_mtime
            if time.time() - mtime < _RELEASE_CACHE_TTL_SECONDS:
                with cache_p.open("r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("tag"):
                    return cached
        except (OSError, json.JSONDecodeError):
            pass  # 缓存损坏 → 走网络

    # 2) 拉 GitHub API
    release_json = _fetch_github_release_json()
    if release_json is None:
        # 3) 网络失败 → 回退到 24h 旧缓存
        if cache_p.is_file():
            try:
                mtime = cache_p.stat().st_mtime
                if time.time() - mtime < _RELEASE_CACHE_FALLBACK_SECONDS:
                    with cache_p.open("r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if cached.get("tag"):
                        LOG.info("using stale release cache (offline)", extra={"path": str(cache_p)})
                        return cached
            except (OSError, json.JSONDecodeError):
                pass
        return None

    # 4) 解析 + 写缓存
    try:
        info = _project_release_info(release_json)
        info["cached_at"] = int(time.time())
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_p.with_suffix(cache_p.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        os.replace(tmp, cache_p)
        return info
    except Exception as e:  # noqa: BLE001
        LOG.warning("release cache write failed", extra={"err": str(e)})
        # 写缓存失败但 info 已有 → 返回内存版本（不阻塞当前调用）
        return info


def _fetch_github_release_json() -> dict | None:
    """GET GitHub API，返回 JSON dict；任何失败 → None。

    urllib.request 必须带 User-Agent，否则 GitHub API 拒服务（400）。
    """
    req = urllib.request.Request(
        _GITHUB_API_URL,
        headers={"User-Agent": "DingTalkBox-Installer/1.0", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            if "rate limit" in body.lower():
                LOG.warning("GitHub API rate limited")
            else:
                LOG.warning("GitHub API 403", extra={"err": str(e)})
        else:
            LOG.warning("GitHub API HTTP error", extra={"code": e.code, "err": str(e)})
        return None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        LOG.warning("GitHub API fetch failed", extra={"err": str(e)})
        return None


def _project_release_info(release_json: dict) -> dict:
    """从 GitHub release JSON 提取 darwin 资产 URL + name。

    sha256 不在此函数获取——install 时单独拉 checksums.txt（永远拉新的，避免
    缓存和真实 release 不一致）。
    """
    tag = release_json.get("tag_name", "")
    assets = release_json.get("assets", [])
    info: dict[str, Any] = {"tag": tag}
    for asset in assets:
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if name == "dws-darwin-arm64.tar.gz":
            info["darwin_arm64_url"] = url
            info["darwin_arm64_name"] = name
        elif name == "dws-darwin-amd64.tar.gz":
            info["darwin_amd64_url"] = url
            info["darwin_amd64_name"] = name
    return info


def _fetch_checksums_for_release(release_json: dict) -> dict[str, str]:
    """下载 checksums.txt，解析为 {filename: sha256_hex} dict。

    标准 sha256sum 格式："<hash>  <filename>"（双空格分隔）。
    """
    checksums_url = None
    for asset in release_json.get("assets", []):
        if asset.get("name") == "checksums.txt":
            checksums_url = asset.get("browser_download_url")
            break
    if not checksums_url:
        raise RuntimeError("CHECKSUMS_NOT_AVAILABLE")

    req = urllib.request.Request(
        checksums_url,
        headers={"User-Agent": "DingTalkBox-Installer/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"NETWORK_FAILED: {e}") from e

    out: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 兼容单/双空格分隔
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            out[parts[1].strip()] = parts[0].lower()
    if not out:
        raise RuntimeError("CHECKSUMS_EMPTY")
    return out


# ── HTTP 下载 / SHA256 / tar 解压 ────────────────────────────────────
def _download_to_temp(url: str, *, suffix: str = "") -> Path:
    """流式下载 url 到 NamedTemporaryFile，返回路径。

    4KB chunk size 流式写入，避免 5MB tarball 全进内存。
    Content-Length 已知时校验接收字节数（防 truncated）。
    """
    fd, raw_path = tempfile.mkstemp(prefix="dws_dl_", suffix=suffix or "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DingTalkBox-Installer/1.0"})
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
            # Content-Length 可能为 None（chunked transfer）
            content_length = resp.headers.get("Content-Length")
            expected_size = int(content_length) if content_length else None
            received = 0
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
            if expected_size is not None and received != expected_size:
                raise RuntimeError(f"DOWNLOAD_TRUNCATED: expected {expected_size}, got {received}")
        return Path(raw_path)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 清理半截文件
        try:
            Path(raw_path).unlink()
        except OSError:
            pass
        raise RuntimeError(f"NETWORK_FAILED: {e}") from e
    except RuntimeError:
        # DOWNLOAD_TRUNCATED 等明确错误
        try:
            Path(raw_path).unlink()
        except OSError:
            pass
        raise
    except Exception:
        try:
            Path(raw_path).unlink()
        except OSError:
            pass
        raise


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA256，返回 64 字符 hex"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _extract_darwin_tarball(tarball: Path, dest_dir: Path) -> Path:
    """解压 tar.gz 到 dest_dir，返回其中 `dws` 二进制路径。

    安全：
      - **第一遍全扫所有 member**：拒绝任何 '..' / 绝对路径 / 符号链接（tar slip 防御）
      - **第二遍只 extract `dws` 单文件**：拒绝目录、特殊文件
    """
    dest_resolved = dest_dir.resolve()
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            # 第一遍：全扫，所有 member 必须路径安全
            for member in tf.getmembers():
                member_path = (dest_dir / member.name).resolve()
                try:
                    member_path.relative_to(dest_resolved)
                except ValueError:
                    raise RuntimeError(f"TAR_SLIP_DETECTED: {member.name}")
                if member.issym() or member.islnk():
                    raise RuntimeError(f"TAR_SLIP_DETECTED: symlink {member.name}")

            # 第二遍：只 extract 名为 dws 的普通文件
            for member in tf.getmembers():
                if member.name != "dws":
                    continue
                if not member.isfile():
                    raise RuntimeError(f"BINARY_NOT_FOUND_IN_TARBALL: 'dws' is not a regular file")
                tf.extract(member, dest_dir, set_attrs=False)
                return dest_dir / "dws"
            # 遍历完没找到 dws
            raise RuntimeError("BINARY_NOT_FOUND_IN_TARBALL")
    except tarfile.ReadError as e:
        raise RuntimeError(f"TAR_EXTRACT_FAILED: {e}") from e
    except tarfile.CompressionError as e:
        raise RuntimeError(f"TAR_EXTRACT_FAILED: {e}") from e
    except tarfile.TarError as e:
        raise RuntimeError(f"TAR_EXTRACT_FAILED: {e}") from e


# ── macOS install 主流程 ────────────────────────────────────────────
def _install_dws_macos(_params: dict) -> dict:
    """从 GitHub release 下载并安装 dws 到 ~/Library/Application Support/DingTalkBox/bin/dws"""
    # 1) 架构
    try:
        arch = _detect_darwin_arch()
    except RuntimeError as e:
        return {
            "ok": False,
            "err_code": "UNSUPPORTED_ARCH",
            "err_msg": str(e),
        }

    # 2) 拿 release 信息（带缓存）
    release = _get_latest_release_info()
    if not release:
        return {
            "ok": False,
            "err_code": "NETWORK_FAILED",
            "err_msg": "无法连接 GitHub release API（请检查网络）",
        }

    asset_key = f"darwin_{arch}"
    asset_url = release.get(f"{asset_key}_url")
    if not asset_url:
        return {
            "ok": False,
            "err_code": "ASSET_NOT_FOUND",
            "err_msg": f"release {release.get('tag')} 没有 {arch} 资产",
        }

    # 3) 拉 checksums.txt（不缓存，每次 install 都重新拿）
    #    _fetch_checksums_for_release 需要原始 release_json，但缓存里没存——重拉一次
    raw_release = _fetch_github_release_json()
    if not raw_release:
        return {
            "ok": False,
            "err_code": "NETWORK_FAILED",
            "err_msg": "无法获取 checksums.txt（请检查网络）",
        }
    try:
        checksums = _fetch_checksums_for_release(raw_release)
    except RuntimeError as e:
        msg = str(e)
        if msg.startswith("NETWORK_FAILED"):
            return {"ok": False, "err_code": "NETWORK_FAILED", "err_msg": msg}
        if msg.startswith("CHECKSUMS_NOT_AVAILABLE"):
            return {
                "ok": False,
                "err_code": "CHECKSUMS_NOT_AVAILABLE",
                "err_msg": "release 没有 checksums.txt，无法校验",
            }
        return {"ok": False, "err_code": "CHECKSUMS_EMPTY", "err_msg": msg}

    asset_name = release.get(f"{asset_key}_name", f"dws-darwin-{arch}.tar.gz")
    expected_sha256 = checksums.get(asset_name)
    if not expected_sha256:
        return {
            "ok": False,
            "err_code": "ASSET_NOT_FOUND",
            "err_msg": f"checksums.txt 里没有 {asset_name}",
        }

    # 4) 下载 tarball
    try:
        tarball = _download_to_temp(asset_url, suffix=".tar.gz")
    except RuntimeError as e:
        msg = str(e)
        if msg.startswith("HTTP_") or "HTTP " in msg:
            # urllib.error.HTTPError 的 str 形如 "HTTP Error 404: Not Found"
            if "404" in msg:
                return {"ok": False, "err_code": "HTTP_404", "err_msg": msg}
            return {"ok": False, "err_code": "DOWNLOAD_FAILED", "err_msg": msg}
        if msg.startswith("DOWNLOAD_TRUNCATED"):
            return {"ok": False, "err_code": "DOWNLOAD_TRUNCATED", "err_msg": msg}
        return {"ok": False, "err_code": "NETWORK_FAILED", "err_msg": msg}
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "err_code": f"HTTP_{e.code}",
            "err_msg": f"下载失败 HTTP {e.code}: {e.reason}",
        }

    try:
        # 5) SHA256 校验
        actual_sha256 = _sha256_file(tarball)
        if actual_sha256.lower() != expected_sha256.lower():
            return {
                "ok": False,
                "err_code": "CHECKSUM_MISMATCH",
                "err_msg": (
                    f"SHA256 不匹配：期望 {expected_sha256[:16]}…，"
                    f"实际 {actual_sha256[:16]}…（下载可能被劫持，请重试）"
                ),
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
            }

        # 6) 解压到临时目录
        with tempfile.TemporaryDirectory(prefix="dws_extract_") as extract_dir_str:
            extract_dir = Path(extract_dir_str)
            try:
                dws_in_temp = _extract_darwin_tarball(tarball, extract_dir)
            except RuntimeError as e:
                msg = str(e)
                if msg.startswith("TAR_SLIP_DETECTED"):
                    return {"ok": False, "err_code": "TAR_SLIP_DETECTED", "err_msg": msg}
                if msg.startswith("BINARY_NOT_FOUND_IN_TARBALL"):
                    return {"ok": False, "err_code": "BINARY_NOT_FOUND_IN_TARBALL", "err_msg": msg}
                return {"ok": False, "err_code": "TAR_EXTRACT_FAILED", "err_msg": msg}

            # 7) chmod +x
            try:
                os.chmod(dws_in_temp, 0o755)
            except OSError as e:
                return {
                    "ok": False,
                    "err_code": "CHMOD_FAILED",
                    "err_msg": f"chmod +x 失败：{e}",
                }

            # 8) 原子移动到目标（shutil.copy2 会保留源 mode，所以先 chmod 再 copy，
            #    保证目标文件最终是 0o755——copy 后再 chmod 也行，但顺序更直观）
            target = paths.user_dws_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".new")
            try:
                shutil.copy2(dws_in_temp, tmp)
                # 显式设目标 mode（copy2 保留源 mode，但保险起见强制设一次）
                os.chmod(tmp, 0o755)
            except OSError as e:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
                return {
                    "ok": False,
                    "err_code": "REPLACE_FAILED",
                    "err_msg": f"copy 到 {tmp} 失败：{e}",
                }
            try:
                os.replace(tmp, target)
            except OSError as e:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
                return {
                    "ok": False,
                    "err_code": "REPLACE_FAILED",
                    "err_msg": f"rename {tmp} → {target} 失败：{e}",
                }

        # 9) Postcheck
        version = _probe_version(target)
        if not version:
            return {
                "ok": False,
                "err_code": "POSTCHECK_FAILED",
                "err_msg": f"已装到 {target}，但 --version 失败，请手动验证",
                "path": str(target),
            }

        LOG.info(
            "dws installed (macOS)",
            extra={"path": str(target), "version": version, "tag": release.get("tag")},
        )
        return {
            "ok": True,
            "path": str(target),
            "version": version,
            "size": target.stat().st_size,
            "release_tag": release.get("tag"),
            "download_url": asset_url,
        }
    finally:
        # 不论成败，tarball 都要清
        try:
            tarball.unlink()
        except OSError:
            pass