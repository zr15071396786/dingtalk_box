"""auth.py — 登录态 / corp 校验

对应 api-protocol.md §3.2-3.4
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from . import config, dws_runner, logging_setup, paths


# 头像下载缓存：避免每次 get_status 都重新下载 + 重新 base64 编码（80~200KB JSON）
_AVATAR_CACHE: dict[str, str | None] = {}
# 本地缓存兜底：dws 1.0.34 contact user me 不返 avatar 字段时，扫描钉钉 PC 客户端的
# %APPDATA%/DingTalk/<uid_dir>/Avators/login_avatar_*.jpg —— 这是钉钉自己登录后缓存的当前用户
# 头像，WebP / JPG 都有，浏览器渲染 OK。这里缓存「解析后的 mtime」避免每次重启都重读。
_LOCAL_AVATAR_CACHE: dict[str, str | None] = {}


def _fetch_avatar_as_data_url(url: str | None) -> str | None:
    """下载头像 URL → base64 data URL。

    钉钉 CDN 头像在 pywebview WebView2 嵌入式浏览器里常因 CORS / cookie / referrer
    校验问题加载失败。改在 sidecar 这边用 urllib 下载（带 user-agent + referrer 模拟），
    转 base64 后返回 data: URL，前端 <img> 直接渲染（无网络请求，零失败率）。

    失败（超时 / 404 / 非图片）→ 返回 None，前端 fallback 到 emoji。

    缓存：相同 URL 复用上次结果，进程内不重复下载。
    """
    if not url or not url.startswith(("http://", "https://")):
        # url 为空 → 走本地钉钉客户端缓存兜底（dws 1.0.34 contact user me 不返 avatar 字段）
        return _fetch_local_avatar_as_data_url()
    if url in _AVATAR_CACHE:
        return _AVATAR_CACHE[url]
    result: str | None = None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://im.dingtalk.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        if 0 < len(data) <= 2 * 1024 * 1024:  # 0~2MB
            ctype = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
            if ctype.startswith("image/"):
                b64 = base64.b64encode(data).decode("ascii")
                result = f"data:{ctype};base64,{b64}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        logger = logging_setup.setup("auth")
        logger.warning("avatar download failed", extra={"url": url, "err": str(e)[:100]})
    _AVATAR_CACHE[url] = result
    return result


def _fetch_local_avatar_as_data_url() -> str | None:
    """从钉钉 PC 客户端本地缓存读当前登录用户的头像。

    路径模式：%APPDATA%/DingTalk/<uid_dir>/Avators/login_avatar_<hash>.{jpg,webp,png}
    （dws / 钉钉自己登录后下载当前用户头像缓存到这里；hash 是钉钉内部算法，
     我们的 user_id 跟它没有直接关系 —— 一个 DingTalk 安装一般只有一个登录用户，
     所以 glob 出第一个文件就是当前用户的。）

    失败（路径不存在 / 文件 0 字节 / 不是图片）→ 返回 None，前端 fallback 到 SVG 首字。

    缓存：mtime 相同的文件不重读（钉钉在用户更新头像时会更新文件 → mtime 变化 → 自动失效）。
    """
    cache_key = "local"
    if not sys.platform.startswith("win"):
        return None
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    dingtalk_root = Path(appdata) / "DingTalk"
    if not dingtalk_root.is_dir():
        return None
    # 找一个登录用户：<user_dir>/Avators/login_avatar_*
    candidate: Path | None = None
    try:
        for user_dir in dingtalk_root.iterdir():
            if not user_dir.is_dir():
                continue
            av = user_dir / "Avators"
            if not av.is_dir():
                continue
            for f in av.glob("login_avatar_*"):
                if f.is_file() and f.stat().st_size > 0:
                    candidate = f
                    break
            if candidate:
                break
    except OSError:
        return None
    if not candidate:
        return None
    try:
        mtime = candidate.stat().st_mtime
        if cache_key in _LOCAL_AVATAR_CACHE:
            cached = _LOCAL_AVATAR_CACHE[cache_key]
            # 缓存条目是 "<mtime>|<data-url>"，mtime 变了就重读
            if cached and cached.split("|", 1)[0] == str(mtime):
                return cached.split("|", 1)[1]
        data = candidate.read_bytes()
        if not data or len(data) > 2 * 1024 * 1024:
            return None
        # 按扩展名猜 mime（钉钉这里常见 .jpg 但实际可能是 webp）
        ext = candidate.suffix.lower().lstrip(".")
        if ext == "jpg" or ext == "jpeg":
            mime = "image/jpeg"
        elif ext == "webp":
            mime = "image/webp"
        elif ext == "png":
            mime = "image/png"
        else:
            mime = "image/jpeg"
        # 嗅探实际内容，避免扩展名错误（钉钉 .jpg 实际是 webp 的情况验证过）
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        elif data[:2] == b"\xff\xd8":
            mime = "image/jpeg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        else:
            # 不认识的格式，跳过让前端 fallback
            return None
        b64 = base64.b64encode(data).decode("ascii")
        result = f"data:{mime};base64,{b64}"
        _LOCAL_AVATAR_CACHE[cache_key] = f"{mtime}|{result}"
        return result
    except OSError as e:
        logger = logging_setup.setup("auth")
        logger.warning("local avatar read failed", extra={"err": str(e)[:100]})
        return None


def _creationflags() -> int:
    if sys.platform.startswith("win"):
        return subprocess.CREATE_NO_WINDOW
    return 0


# 跟踪所有由 sidecar 拉起的 dws 子进程（典型：`dws auth login`），
# sidecar 退出时统一 terminate，避免僵尸进程。
_BACKGROUND_PROCS: list[subprocess.Popen] = []


def track_background_proc(p: subprocess.Popen) -> None:
    _BACKGROUND_PROCS.append(p)


def shutdown_background_procs() -> None:
    for p in _BACKGROUND_PROCS:
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    _BACKGROUND_PROCS.clear()
    # Bug 8 收尾：清理 active login proc 引用
    global _ACTIVE_LOGIN_PROC
    _ACTIVE_LOGIN_PROC = None


# trigger_login 防重复点击：dws auth login 是阻塞进程，
# 用户连点「启动登录」会 spawn 多个 dws 在后台互相打架。
# 用 module-level 变量记录「当前激活的登录 proc」，
# 第二次进来如果旧 proc 还活着就复用，否则先 kill 旧的再 spawn 新的。
_ACTIVE_LOGIN_PROC: subprocess.Popen | None = None

# v0.3.9：缓存上一次抓到的 dws login user_code — dedup 路径返回它，让前端 modal 二次弹起时
# 还能显示同一个授权码（用户第一次关 modal 时 dws 后台进程没死，user_code 仍然有效）。
_LAST_LOGIN_URL: str | None = None


# ── 3.2 get_login_status ─────────────────────────────────────────────
def get_login_status(_params: dict) -> dict:
    """返回 {logged_in, user, corp, dws_version, token_expires_at}"""
    logger = logging_setup.setup("auth")

    # 1. dws auth status（结构化）
    try:
        status = dws_runner.run(["auth", "status"], timeout=30)
    except dws_runner.DwsError as e:
        # dws auth status 失败 → 当作未登录
        logger.info("auth status failed", extra={"err": str(e)})
        return {
            "logged_in": False,
            "reason": "dws auth status failed: " + str(e),
        }

    # dws 1.0.34 status 结构：{"success": true, "authenticated": true, "refreshed": true,
    #                         "token_valid": true, "expires_at": "...", "corp_id": "..."}
    success = status.get("success", True)
    authenticated = status.get("authenticated", False)
    token_valid = status.get("token_valid", False)
    logged_in = bool(success and authenticated and token_valid)

    result: dict[str, Any] = {
        "logged_in": logged_in,
        "dws_version": _probe_dws_version(),
        "token_expires_at": status.get("expires_at"),
        "refresh_expires_at": status.get("refresh_expires_at"),
    }
    if not logged_in:
        result["reason"] = "token invalid or refresh needed"
        return result

    # 2. 取当前用户
    try:
        me = dws_runner.run(["contact", "user", "me"], timeout=30)
        items = me.get("result") or []
        if items:
            item = items[0]
            oem = item.get("orgEmployeeModel", {}) or {}
            corp = oem.get("corpId") or item.get("corpId")
            corp_name = oem.get("orgName") or ""
            depts = oem.get("depts", []) or []
            dept_name = depts[0].get("deptName") if depts else ""
            # v0.2 修复：dws 1.0.34 contact user me **不返回** openDingTalkId 字段，
            # 实际只有 orgEmployeeModel.userId。但 dws chat message send 接受 --user <userId>
            # 作为 open-dingtalk-id 的替代（"适用于无法获取 userId 的场景"反过来也成立）。
            # 两个字段都填好（userId 一定有，openDingTalkId 可能空），前端优先用 userId。
            user_id = str(oem.get("userId") or item.get("userId") or "")
            # v0.3 修复：钉钉 CDN 头像 URL 在 pywebview WebView2 里常因 CORS / cookie / referrer
            # 加载失败。改在 sidecar 这边下载 + base64，返回 data: URL，浏览器直接渲染（无网络）
            avatar_url = item.get("avatar")
            avatar_data_url = _fetch_avatar_as_data_url(avatar_url)
            result["user"] = {
                "user_id": user_id,
                "open_dingtalk_id": item.get("openDingTalkId") or "",
                "name": oem.get("orgUserName") or item.get("name") or "",
                "avatar": avatar_data_url,  # 可能是 None（下载失败时前端 fallback 到 emoji）
                "avatar_source_url": avatar_url,  # 调试用：原始 URL
                "email": oem.get("orgAuthEmail") or "",
                "mobile": oem.get("orgUserMobile") or "",
                "dept": dept_name,
            }
            result["corp"] = {"corp_id": corp, "corp_name": corp_name}
    except dws_runner.DwsError as e:
        logger.warning("contact user me failed", extra={"err": str(e)})

    return result


# ── 3.3 trigger_login ────────────────────────────────────────────────
def trigger_login(_params: dict) -> dict:
    """调 dws auth login，捕获其输出中的登录 URL

    dws auth login 在需要时会打印登录 URL 并阻塞等待扫码。
    我们后台启动 dws auth login，捕获其 stdout 抓 URL 后保留 dws 进程
    在后台继续等扫码（**不要 kill**，否则用户失去扫码机会）。

    返回 {"ok", "qr_url", "hint", "_dedup"}
    - qr_url：用户扫码的 URL，前端渲染成 QR 码显示在 modal 内
    - _dedup：True 表示复用后台已有 proc（用户重复点击「启动登录」）
    """
    global _ACTIVE_LOGIN_PROC, _LAST_LOGIN_URL
    logger = logging_setup.setup("auth")
    dws = paths.dws_exe_path()

    # 防重复点击：旧 proc 还活着就复用（不 spawn 新的），
    # 旧 proc 已死就 spawn 新的覆盖。
    if _ACTIVE_LOGIN_PROC is not None:
        if _ACTIVE_LOGIN_PROC.poll() is None:
            # 还活着 → 不 spawn 新的
            return {
                "ok": True,
                "hint": "已在系统浏览器打开授权页，请在浏览器中完成扫码登录。",
                "_dedup": True,
            }
        # 已死 → 清理引用，下面 spawn 新的；缓存的 URL 也清掉
        _ACTIVE_LOGIN_PROC = None
        _LAST_LOGIN_URL = None

    proc = subprocess.Popen(
        [dws, "auth", "login"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
    )
    _ACTIVE_LOGIN_PROC = proc
    # 追踪，sidecar 退出时统一 terminate（避免僵尸 dws 进程）
    track_background_proc(proc)
    # 最多等 8s 抓 URL
    url = None
    deadline = time.time() + 8
    captured_lines: list[str] = []
    try:
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            captured_lines.append(line.rstrip())
            if "http" in line and ("login" in line.lower() or "dingtalk" in line.lower()):
                # 提取 URL
                for token in line.split():
                    if token.startswith("http"):
                        url = token.strip("`'\",;")
                        break
                if url:
                    break
    except Exception:
        logger.exception("trigger_login: url capture failed")

    # v0.3.9 修复 ERR_CONNECTION_REFUSED：抓完 URL 后立即关掉 stdout pipe，
    # 否则 dws 的子进程（OAuth callback HTTP server）会继承 stdout pipe fd，
    # pipe 写满后子进程 block，导致 callback 端口监听失败。
    if proc.stdout:
        try:
            proc.stdout.close()
        except Exception:
            pass

    if not url:
        # 没抓到 URL → dws 已在后台跑，靠轮询检测登录完成
        return {
            "ok": True,
            "hint": "已在系统浏览器打开授权页，请在浏览器中完成扫码登录。",
            "captured": "\n".join(captured_lines[-10:]),
        }

    # v0.3.10：抓到的 URL 只用作侧车内部缓存（debug / 排查用），
    # 不再传给前端 —— 改走浏览器授权流程
    _LAST_LOGIN_URL = url
    return {
        "ok": True,
        "hint": "已在系统浏览器打开授权页，请在浏览器中完成扫码登录。",
    }


def cancel_login(_params: dict) -> dict:
    """kill 当前活跃的 dws auth login 子进程 + 清缓存（修复 v0.3.10 dedup bug）

    v0.3.10 之前的 cancelLogin 只停前端轮询，侧车那边的 _ACTIVE_LOGIN_PROC
    还指着活的 dws → 用户再次点登录时 trigger_login 走 dedup 分支不 spawn 新进程 → 浏览器不开。

    现在 cancelLogin 主动调这个 RPC，kill dws + 清 _ACTIVE_LOGIN_PROC +
    _LAST_LOGIN_URL，下次点登录能正常 spawn 新 dws 开浏览器。
    """
    global _ACTIVE_LOGIN_PROC, _LAST_LOGIN_URL
    proc = _ACTIVE_LOGIN_PROC
    if proc is None:
        return {"ok": True, "killed": False, "hint": "无活跃登录进程"}
    # 先 poll 再决定要不要 terminate（已死就不浪费系统调用）
    try:
        alive = proc.poll() is None
    except Exception:
        alive = True
    if alive:
        try:
            proc.terminate()
            # 给 2s 优雅退出；不响应就强杀（Windows 下 terminate 通常够）
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass
    _ACTIVE_LOGIN_PROC = None
    _LAST_LOGIN_URL = None
    return {"ok": True, "killed": bool(alive), "hint": "已取消登录"}


# ── 3.3.5 trigger_logout ────────────────────────────────────────────
def trigger_logout(_params: dict) -> dict:
    """调 dws auth logout 登出（清空本地 token 缓存）

    用途：用户主动登出（切账号 / 退出登录）
    返回：{"ok": bool, "err_code": str?, "err_msg": str?, "raw": dict}
    """
    logger = logging_setup.setup("auth")
    try:
        # lenient_output=True：dws auth logout 实际可能输出普通文本（"Logged out"），
        # 而非 JSON —— 只要 returncode=0 就当成功
        result = dws_runner.run(["auth", "logout"], timeout=30, lenient_output=True)
    except dws_runner.DwsError as e:
        logger.warning("dws auth logout failed", extra={"err": str(e), "returncode": e.returncode})
        return {
            "ok": False,
            "err_code": "DWS_ERROR",
            "err_msg": str(e),
            "returncode": e.returncode,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("trigger_logout crashed")
        return {
            "ok": False,
            "err_code": "CRASH",
            "err_msg": f"登出失败: {e}",
        }
    logger.info("dws logged out", extra={"raw": result})
    return {"ok": True, "raw": result}


# ── 3.4 validate_corp ────────────────────────────────────────────────
def validate_corp(params: dict, *, status: dict | None = None) -> dict:
    """启动时校验 corp 是否匹配（corpId 优先，corp_name 兜底）

    params: {"expected_corp_id": "ding..."} 可选，默认从 config.corp.expected_corp_id 读
    status: 复用 caller 已拿到的 get_login_status 结果（Opt 8 优化），
            避免 boot() 和 validate_corp 各调一次 dws

    返回字段（永远存在，即便失败）：
      ok: 是否通过
      code: MISMATCH / NOT_LOGGED_IN / NO_EXPECTED / STRICT_DISABLED / NO_CORP_INFO / CONTACT_FAILED
      expected_corp_id, expected_corp_name
      current_corp_id, current_corp_name

    规则：
      1. config.corp.strict=false → 直接放行（标 STRICT_DISABLED）
      2. corpId 精确匹配 → OK
      3. corp_name 完全相同 → OK（兜底，dws corpId 字段偶尔缺失时用）
      4. dws 完全拿不到 corp 信息 → 警告但放行（不阻塞 dws 升级期用户）
      5. 否则 → MISMATCH，current 字段全填好让 modal 能展示
    """
    logger = logging_setup.setup("auth")
    expected = params.get("expected_corp_id") or config.get("corp.expected_corp_id", "")
    expected_name = config.get("corp.expected_corp_name", "") or expected
    strict = config.get("corp.strict", True)

    if not expected:
        # v0.2 修复：expected_corp_id 是 admin 在 config.yaml 配的，不是 end user 填的。
        # 之前 ok=False 会弹「corp 不匹配」modal，end user 完全没招（corp 是写死的）。
        # 改成放行 + code=NO_EXPECTED_CONFIGURED，前端走 warning toast
        # （admin 在 sidecar 日志里能看到这条 warning，知道要去配 config）。
        logger.warning(
            "validate_corp: corp.expected_corp_id 未配置，放行登录用户；"
            "请 admin 在 %APPDATA%/DingTalkBox/config.yaml 设置 corp.expected_corp_id",
            extra={"strict": strict},
        )
        return {
            "ok": True, "code": "NO_EXPECTED_CONFIGURED",
            "message": "工具未配置 corp 限制（admin 需在 config.yaml 设置 corp.expected_corp_id）；当前放行。",
            "expected_corp_id": "", "expected_corp_name": expected_name,
            "current_corp_id": "", "current_corp_name": "",
        }

    # strict=false：用户主动选择不强制 corp 校验（公司没统一 corp 时用）
    if not strict:
        return {
            "ok": True, "code": "STRICT_DISABLED",
            "message": "corp 校验已在 config 关闭（strict=false）",
            "expected_corp_id": expected, "expected_corp_name": expected_name,
            "current_corp_id": "", "current_corp_name": "",
        }

    # Opt 8 修复：复用 caller 已拿到的 status，节省一次 dws auth status 调用
    if status is None:
        status = get_login_status({})
    if not status.get("logged_in"):
        return {
            "ok": False, "code": "NOT_LOGGED_IN", "message": "dws 未登录",
            "expected_corp_id": expected, "expected_corp_name": expected_name,
            "current_corp_id": "", "current_corp_name": "",
        }

    # 先用 status 里的 corp（get_login_status 已拿过，结构稳定且免费）
    current_corp = status.get("corp") or {}
    current_corp_id = current_corp.get("corp_id", "") or ""
    current_corp_name = current_corp.get("corp_name", "") or ""

    # status 拿不到 corp 信息时（id 和 name 都空）再调 contact user me 兜底
    # 如果 status 已返回 corp_name（即便 corpId 空），就不再调 dws
    if not current_corp_id and not current_corp_name:
        try:
            me = dws_runner.run(["contact", "user", "me"], timeout=30)
            items = me.get("result") or []
            if items:
                oem = items[0].get("orgEmployeeModel", {}) or {}
                current_corp_id = oem.get("corpId") or items[0].get("corpId", "") or ""
                current_corp_name = oem.get("orgName", "") or current_corp_name
        except dws_runner.DwsError as e:
            logger.warning("validate_corp: contact user me failed", extra={"err": str(e)})
            # status 没 corp + dws 也拿不到 → 放行（不阻塞）
            return {
                "ok": True, "code": "NO_CORP_INFO",
                "message": f"dws 取 corp 失败：{e}。临时放行，请检查 dws 状态。",
                "expected_corp_id": expected, "expected_corp_name": expected_name,
                "current_corp_id": current_corp_id, "current_corp_name": current_corp_name,
            }

    # 三种匹配路径
    id_match = bool(current_corp_id) and current_corp_id == expected
    name_match = bool(current_corp_name) and current_corp_name == expected_name

    # 拿不到任何 corp 信息 → 警告但放行（dws 升级 / 字段变化时不阻塞所有用户）
    if not current_corp_id and not current_corp_name:
        logger.warning("validate_corp: no corp info, allowing through", extra={
            "expected_id": expected, "expected_name": expected_name,
        })
        return {
            "ok": True, "code": "NO_CORP_INFO",
            "message": "dws 未返回 corp 信息，临时放行（建议检查 dws 是否需要升级）",
            "expected_corp_id": expected, "expected_corp_name": expected_name,
            "current_corp_id": "", "current_corp_name": "",
        }

    ok = id_match or name_match
    logger.info("validate_corp", extra={
        "expected_id": expected, "actual_id": current_corp_id,
        "id_match": id_match, "name_match": name_match, "ok": ok,
    })
    return {
        "ok": ok,
        "code": "" if ok else "MISMATCH",
        "expected_corp_id": expected, "expected_corp_name": expected_name,
        "current_corp_id": current_corp_id, "current_corp_name": current_corp_name,
    }


# Opt 8 配套：dispatcher 用的「带 status」入口（仅内部走，给 boot 优化用）
def _validate_corp_with_status(params: dict) -> dict:
    """dispatcher 路径：接受 params.status 复用 caller 的 status"""
    status = params.pop("status", None) if isinstance(params, dict) else None
    return validate_corp(params, status=status)


# ── 工具 ─────────────────────────────────────────────────────────────
def _probe_dws_version() -> str | None:
    try:
        proc = subprocess.run(
            [paths.dws_exe_path(), "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_creationflags(),
            timeout=5,
        )
        line = (proc.stdout or proc.stderr or "").strip().splitlines()
        return line[0] if line else None
    except Exception:
        return None
