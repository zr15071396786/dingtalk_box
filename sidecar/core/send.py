"""send.py — 发送文件/图片到钉钉

对应 api-protocol.md §3.6
约束：
- file_path 必须在 output_dir() 沙箱内
- 文件必须存在
- 通过 dws drive 上传 → 拿 dentry_id + space_id → dws chat message send
"""
from __future__ import annotations

import os
import re
import sys
import time
import json
import functools
from pathlib import Path
from typing import Any

from . import dws_runner, logging_setup, paths

# v0.3.14：版本号从 version.__version__ 单一真相源读取，不再硬编码 "0.1.0"
from version import __version__ as _VERSION  # noqa: E402

LOG = logging_setup.setup("send")


# v0.3.11 修复：之前用 singleChat=true 的会话当 --group 发，会发到「我与某人/机器人」的单聊
# （dws 的 singleChat=true 仅表示 1v1 单聊，不是「自己跟自己」）。
# 正确路径：contact user search --keyword <self_name> 拿自己的 openDingTalkId → --open-dingtalk-id 发。
#
# 缓存策略：传入 userId+name → 同进程只查一次 dws。userId 必须匹配以避开通讯录重名。
@functools.lru_cache(maxsize=1)
def _get_self_open_dingtalk_id(user_id: str, name: str) -> str:
    """查 dws 拿到「当前登录用户自己」的 openDingTalkId（可作为 --open-dingtalk-id 发单聊）

    dws 1.0.34+ 的 `contact user me` **不返回** openDingTalkId；`contact user get-self`
    同样不返。唯一能拿 openDingTalkId 的官方路径是 `contact user search --keyword <name>`。
    search 是通讯录全公司搜，可能重名（如「张瑞」「张瑞霞」），所以**必须**用 userId 匹配
    找出真正属于自己的那条记录。

    失败原因：
      1. search 没结果 → 名字拼错 / 没登录
      2. search 有结果但 userId 都不匹配 → 同名太多，无法自动判定，必须报错让用户手动指定
    """
    if not user_id or not name:
        raise RuntimeError("user_id 和 name 都不能为空（无法定位自己的 openDingTalkId）")
    res = dws_runner.run(
        ["contact", "user", "search", "--keyword", name],
        timeout=30,
    )
    items = (res.get("result") or [])
    # 按 userId 精确匹配（字符串相等）找出自己那条
    for it in items:
        if str(it.get("userId") or "") == str(user_id):
            oid = it.get("openDingTalkId") or ""
            if oid:
                return oid
    # v0.3.11 修复：必须严格按 userId 匹配，不允许 fallback 到"第一条"
    # （之前 fallback 会拿到重名同事的 openDingTalkId，把消息发给别人——
    #  这就是"发给列表置顶的这个人"的根因之一）
    raise RuntimeError(
        f"无法定位自己的 openDingTalkId：search '{name}' 的结果中没有 userId={user_id} 的记录"
        f"（可能重名太多或通讯录权限不足）。请重新登录钉钉后重试，或联系工具负责人。"
    )


# ── 异常 ─────────────────────────────────────────────────────────────
class FileNotFound(Exception):
    code = -32005
    def __init__(self, path: str):
        super().__init__(f"文件不存在：{path}")
        self.data = {"path": path}


class PathTraversal(Exception):
    code = -32006
    def __init__(self, path: str, root: str):
        super().__init__(f"路径越界：{path}（必须在 {root} 内）")
        self.data = {"path": path, "root": root}


# ── 工具 ─────────────────────────────────────────────────────────────
def _resolve_safe(file_path: str) -> Path:
    """沙箱校验并 resolve"""
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = (paths.output_dir() / p).resolve()
    if not p.exists():
        raise FileNotFound(str(p))
    if not paths.is_under_output(p):
        raise PathTraversal(str(p), str(paths.output_dir()))
    return p


def _parse_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if not s:
        return None
    return int(s)


# ── 核心：上传 + 拿 dentry_id ───────────────────────────────────────
def _upload_and_get_dentry(local: Path) -> dict:
    """1. 上传本地文件到 dws drive
    2. 列目录拿 dentry_id + space_id

    返回 {"dentry_id": int, "space_id": int, "name": str, "size": int}
    """
    name = local.name
    size = local.stat().st_size

    # 1. 上传（不指定 space-id → 我的文件）
    LOG.info("uploading to drive", extra={"path": str(local), "size": size})
    up = dws_runner.run(
        ["drive", "upload", "--file", str(local), "--file-name", name],
        timeout=180,
    )
    # dws drive upload 实际返回：{"result": {"fileId": "...", "spaceId": "...", "fileSize": ...}, "success": true}
    up_result = up.get("result") or {}
    file_id = up_result.get("fileId") or up_result.get("fileKey") or up_result.get("dentryUuid")
    space_id = _parse_int(up_result.get("spaceId"))
    if not file_id:
        raise RuntimeError(f"dws drive upload 未返回 fileId：{up}")
    if space_id is None:
        raise RuntimeError(f"dws drive upload 未返回 spaceId：{up}")

    # 2. 列目录，按 name 找 dentry_id（dws send 需要 int）
    LOG.info("listing drive to resolve dentryId", extra={"file_id": file_id, "file_name": name})
    listed = dws_runner.run(
        ["drive", "list", "--limit", "30", "--space-id", str(space_id), "--order-by", "createTime", "--order", "desc"],
        timeout=30,
    )
    listed_items = (listed.get("result") or {}).get("items") or []
    dentry_id: int | None = None
    for it in listed_items:
        if (it.get("fileId") == file_id) or (it.get("name") == name and it.get("fileSize") == size):
            dentry_id = _parse_int(it.get("dentryId"))
            if dentry_id is not None:
                break

    if dentry_id is None:
        raise RuntimeError(f"未在 drive list 中找到 {name}（fileId={file_id}）的 dentryId")

    return {
        "dentry_id": int(dentry_id),
        "space_id": int(space_id),
        "name": name,
        "size": size,
        "file_id": file_id,
    }


# ── 核心：发钉盘文件 ───────────────────────────────────────────────
def _send_drive_file(
    *,
    dentry_id: int,
    space_id: int,
    file_name: str,
    file_path: str,
    file_type: str,
    file_size: int,
    open_dingtalk_id: str,
    user_id: str,
    title: str,
) -> dict:
    """用 dws chat message send --msg-type file 发钉盘文件

    接收人标识（v0.3.11 修复 --user 不支持富媒体 + singleChat fallback 误发他人）：
      - open_dingtalk_id 优先（dws 0.27- 等老版本 contact user me 必返回；富媒体可用）
      - 缺失时由 send_file 顶层调 _resolve_self_open_dingtalk_id 解析
        （dws 1.0.34+ contact user me / get-self 都不返 openDingTalkId，
         只有 contact user search --keyword <name> 会返）
      - 都没有 → ValueError

    安全：只允许发给当前登录人自己（self-send only）
    """
    if not open_dingtalk_id and not user_id:
        raise ValueError("必须提供 open_dingtalk_id 或 user_id（只允许发给自己）")

    args = [
        "chat", "message", "send",
        "--msg-type", "file",
        "--dentry-id", str(dentry_id),
        "--space-id", str(space_id),
        "--file-name", file_name,
        "--file-path", file_path,
        "--file-type", file_type,
        "--file-size", str(file_size),
        "--title", title,
    ]
    if open_dingtalk_id:
        # 1. 优先 --open-dingtalk-id（dws 0.27- 老版本；富媒体 OK）
        args.extend(["--open-dingtalk-id", open_dingtalk_id])
        recipient_tag = f"openDingTalkId={open_dingtalk_id[:8]}…"
    else:
        # 2. dws 1.0.34+ 缺 openDingTalkId + --user 不支持富媒体：
        #    v0.3.11 修复：用 contact user search 拿自己的 openDingTalkId → --open-dingtalk-id 发
        #    之前用 --group <singleChat=true 的 openConversationId> 会发到「我与某人」的单聊
        if not user_id:
            raise ValueError("open_dingtalk_id 或 user_id 必填（只允许发给自己）")
        # caller（send_file）已经确保 open_dingtalk_id 被解析到位；这里只是兜底
        raise RuntimeError(
            "_send_drive_file 收到空 open_dingtalk_id 但 caller 未解析。"
            "这是 send_file 的 bug，不是前端问题。请联系工具负责人。"
        )

    res = dws_runner.run(args, timeout=180)
    LOG.info("file sent", extra={
        "recipient": recipient_tag,
        "title": title,
    })

    # 提取 openTaskId
    open_task_id = (res.get("result") or {}).get("openTaskId") or res.get("openTaskId")
    return {
        "ok": True,
        "open_task_id": open_task_id,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "raw": res,
    }


# ── 3.6 send_to_dingtalk ───────────────────────────────────────────
def send_file(params: dict) -> dict:
    """发送文件到钉钉

    params:
      file_path: 本地绝对路径（必填）
      open_dingtalk_id: 收件人 openDingTalkId（与 user_id 二选一；老版本 dws 必填）
      user_id: 收件人 userId（dws 1.0.34+ 必填；contact user me 不再返回 openDingTalkId）
      title: 消息标题（可选）

    v0.3.11 修复：缺 open_dingtalk_id 时（dws 1.0.34+），会自动通过 contact user me +
    contact user search 拿自己的 openDingTalkId。之前用 singleChat=true 的会话 ID 当
    --group 发会发到「我与某人」的单聊（dws 的 singleChat=true 是 1v1 单聊，不一定是
    自己跟自己）。如果出现这种情况，用户能看到「发给置顶的这个人」。
    """
    file_path = params.get("file_path", "")
    if not file_path:
        raise ValueError("file_path 必填")
    open_dingtalk_id = params.get("open_dingtalk_id", "") or ""
    user_id = params.get("user_id", "") or ""
    if not open_dingtalk_id and not user_id:
        raise ValueError("open_dingtalk_id 或 user_id 必填（只允许发给自己）")

    # v0.3.11：缺 open_dingtalk_id 时自动解析（dws contact user me 不返 openDingTalkId）
    if not open_dingtalk_id and user_id:
        open_dingtalk_id = _resolve_self_open_dingtalk_id(user_id)

    p = _resolve_safe(file_path)
    file_name = p.name
    file_type = p.suffix.lstrip(".").lower() or "png"
    file_size = p.stat().st_size

    title = params.get("title") or f"工作纪要日报 {p.parent.name}"

    info = _upload_and_get_dentry(p)

    return _send_drive_file(
        dentry_id=info["dentry_id"],
        space_id=info["space_id"],
        file_name=file_name,
        file_path=f"/{file_name}",  # 钉钉 API 期望 / 开头的展示路径
        file_type=file_type,
        file_size=file_size,
        open_dingtalk_id=open_dingtalk_id,
        user_id=user_id,
        title=title,
    )


def _resolve_self_open_dingtalk_id(user_id: str) -> str:
    """v0.3.11：解析「当前登录用户自己」的 openDingTalkId

    dws 1.0.34+ 的 contact user me / contact user get-self **都不返回** openDingTalkId；
    只有 contact user search --keyword <name> 会返。所以流程是：
      1. contact user me 拿自己的 userId + name（已有 userId，做一次 me 主要是拿 name）
      2. contact user search --keyword <name> 拿 openDingTalkId（按 userId 精确匹配）

    userId 校验是必要的：search 是全公司通讯录，可能重名（实测搜「张瑞」会同时命中
    张瑞本人和张瑞霞），必须按 userId 匹配找出真正属于当前登录用户的那条。

    缓存：lru_cache(maxsize=1)，同一 userId 同进程内只查一次 dws。
    """
    return _get_self_open_dingtalk_id(user_id, _get_self_name_from_me(user_id))


@functools.lru_cache(maxsize=4)
def _get_self_name_from_me(user_id: str) -> str:
    """从 dws contact user me 拿自己的 name（用于 search keyword）

    userId 进缓存 key：理论上 userId 不会变，但加进来防御未来多账号场景
    """
    try:
        me = dws_runner.run(["contact", "user", "me"], timeout=30)
        items = me.get("result") or []
        if items:
            oem = items[0].get("orgEmployeeModel", {}) or {}
            name = oem.get("orgUserName") or items[0].get("name") or ""
            if name:
                return str(name)
    except Exception as e:
        LOG.warning("contact user me failed in _get_self_name_from_me", extra={"err": str(e)[:100]})
    raise RuntimeError(
        f"无法获取当前登录用户姓名（userId={user_id}），无法解析自己的 openDingTalkId。"
        "请重新登录钉钉后重试。"
    )


# ── 3.9 diagnose ────────────────────────────────────────────────────
def collect_diagnostics(_params: dict) -> dict:
    """收集诊断信息"""
    import platform
    import getpass
    info: dict[str, Any] = {
        "system": {
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "hostname": platform.node(),
            "user": getpass.getuser(),
            "python": sys.version.split()[0],
        },
        "app": {
            "version": _VERSION,
            "data_path": str(paths.data_dir()),
            "dws_path": paths.dws_exe_path(),
        },
    }
    # dws 登录态
    try:
        info["dws"] = _diagnose_dws()
    except Exception as e:  # noqa: BLE001
        info["dws"] = {"error": str(e)}

    # 日志尾部
    try:
        log_files = sorted(paths.logs_dir().glob("sidecar-*.log"), reverse=True)
        if log_files:
            tail = log_files[0].read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            info["log_tail"] = tail
    except Exception as e:  # noqa: BLE001
        info["log_tail_error"] = str(e)

    # config 摘要
    from . import config
    try:
        info["config_snapshot"] = {
            "corp_expected_corp_id": config.get("corp.expected_corp_id"),
            "corp_strict": config.get("corp.strict"),
            "log_level": config.get("logging.level"),
        }
    except Exception as e:  # noqa: BLE001
        info["config_error"] = str(e)

    return info


def _diagnose_dws() -> dict:
    import subprocess
    dws = paths.dws_exe_path()
    proc = subprocess.run(
        [dws, "auth", "status"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
    )
    out = proc.stdout.strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"raw_stdout": out, "raw_stderr": proc.stderr, "returncode": proc.returncode}
    return {
        "logged_in": bool(data.get("authenticated") and data.get("token_valid")),
        "corp_id": data.get("corp_id"),
        "expires_at": data.get("expires_at"),
        "returncode": proc.returncode,
    }
