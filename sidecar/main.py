"""sidecar/main.py — stdio JSON-RPC 主循环

协议（api-protocol.md §1）：
  stdin  ：每行一帧 JSON 请求
  stdout ：每行一帧 JSON 响应 / 事件
  帧格式：{"id": uuid, "method": "...", "params": {...}} （请求）
         {"id": uuid, "result": {...}}                       （成功响应）
         {"id": uuid, "error": {"code": int, "message": "...", "data": {...}}} （失败）
         {"event": "progress", "data": {...}}               （事件，无需 id）

启动方式：
  python -m sidecar [可选额外 init 参数]
  或：python sidecar/main.py

调试：DEBUG=1 sidecar 输出更详细日志到 stderr（事件也写到 stderr）
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

# 把 sidecar/ 加入 sys.path（让 core/ 包可导入）
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
# v0.3.14：把项目根也加入 sys.path，让 `from version import __version__` 在
# dev 模式（直接 `python sidecar/main.py` 或 launcher 子进程）下都能找到 version.py。
# frozen 模式下 PyInstaller 的 pathex=[ROOT] + hiddenimports 已处理。
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core import dispatcher, daily_report, errors as core_errors, logging_setup  # noqa: E402
from core.paths import augment_path_with_dws  # noqa: E402
from version import __version__ as _SIDECAR_VERSION  # noqa: E402

LOG = logging_setup.setup("sidecar")


# ── 帧编解码 ───────────────────────────────────────────────────────
def _write_frame(obj: dict) -> None:
    """写一帧到 stdout，立即 flush"""
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _emit_event(event: str, data: dict) -> None:
    """主动事件（无 id）"""
    _write_frame({"event": event, "data": data})


# ── 错误码 ─────────────────────────────────────────────────────────
CODE_PARSE_ERROR = -32700
CODE_INVALID_REQUEST = -32600
CODE_METHOD_NOT_FOUND = -32601
CODE_INVALID_PARAMS = -32602
CODE_INTERNAL_ERROR = -32000
CODE_NOT_LOGGED_IN = -32001
CODE_CORP_MISMATCH = -32007
CODE_DWS_TIMEOUT = -32003
CODE_DWS_ERROR = -32004
CODE_FILE_NOT_FOUND = -32005
CODE_PATH_TRAVERSAL = -32006
CODE_RENDER_FAILED = -32008

_EXCEPTION_TO_CODE = {
    FileNotFoundError: CODE_FILE_NOT_FOUND,
    PermissionError: CODE_PATH_TRAVERSAL,
}


def _code_for(exc: BaseException) -> int:
    for cls, code in _EXCEPTION_TO_CODE.items():
        if isinstance(exc, cls):
            return code
    # 自定义异常的 code 属性
    return getattr(exc, "code", CODE_INTERNAL_ERROR)


# ── 请求处理 ───────────────────────────────────────────────────────
def _handle(request: dict) -> dict:
    req_id = request.get("id") or str(uuid.uuid4())
    if not isinstance(request, dict):
        return {"id": req_id, "error": {"code": CODE_INVALID_REQUEST, "message": "request must be object"}}
    method = request.get("method")
    if not method or not isinstance(method, str):
        return {"id": req_id, "error": {"code": CODE_INVALID_REQUEST, "message": "missing method"}}
    params = request.get("params", {}) or {}
    if not isinstance(params, dict):
        return {"id": req_id, "error": {"code": CODE_INVALID_PARAMS, "message": "params must be object"}}

    try:
        result = dispatcher.dispatch(method, params)
        return {"id": req_id, "result": result}
    except core_errors.AiBridgeError as e:
        code = core_errors.AI_ERR_TO_CODE.get(e.err_code, CODE_INTERNAL_ERROR)
        # 把 raw 拼到 message 里（截断 200 字符）— 用户看错误能直接看到 LLM 返了啥，
        # 不必再点开 详情；data 仍保留完整 payload 给「诊断」按钮
        msg = e.err_msg[:500]
        raw = (e.data or {}).get("raw")
        if raw and len(str(raw)) > 0:
            raw_short = str(raw)[:200].replace("\n", " ")
            msg = f"{msg} | raw: {raw_short}"
        # 把 LLM 完整 dump 文件路径塞进 message（让用户能直接看完整响应，
        # 不受前端 raw 截断限制）
        dump_path = (e.data or {}).get("dump_path")
        if dump_path and "完整响应已写入" not in msg:
            msg = f"{msg} | dump: {dump_path}"
        return {
            "id": req_id,
            "error": {
                "code": code,
                "message": msg[:700],  # 总长度上限
                "data": {"err_code": e.err_code, **e.data},
            },
        }
    except (ValueError, KeyError, TypeError) as e:
        return {
            "id": req_id,
            "error": {
                "code": CODE_INVALID_PARAMS,
                "message": str(e)[:500],
                "data": {"trace": traceback.format_exc()[:1500]},
            },
        }
    except Exception as e:  # noqa: BLE001
        code = _code_for(e)
        data = getattr(e, "data", None) or {}
        # 永远把 traceback 放进去（前端会显示），方便排错
        data = dict(data) if data else {}
        data["trace"] = traceback.format_exc()[:1500]
        LOG.exception("dispatch error", extra={"method": method, "code": code})
        return {"id": req_id, "error": {"code": code, "message": str(e)[:500], "data": data}}


# ── 进度回调 ───────────────────────────────────────────────────────
def _progress_to_event(stage: str, percent: int, detail: str | None) -> None:
    payload = {"stage": stage, "percent": int(percent)}
    if detail:
        payload["detail"] = str(detail)
    _emit_event("progress", payload)


# ── 主循环 ─────────────────────────────────────────────────────────
def main() -> int:
    # 强制 stdin/stdout/stderr 用 UTF-8（Windows 默认 GBK 会导致中文乱码 / 错位成 surrogate）
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    # v0.3.3 修复：把 dws 所在目录 append 到 PATH。
    # 原因：dds（external/dingtalk_daily_summary.py）用裸 "dws" spawn 子进程，
    # 依赖系统 PATH；sidecar 自己的 dws_runner 走 dws_exe_path() 没问题，
    # 但 dds 绕过了 dws_runner，不在 PATH 上的 dws 就会 WinError 2。
    # 必须在 set_progress_callback 之前调（dds 一旦 import 就随时可能用）。
    augment_path_with_dws()

    # 注入进度回调
    daily_report.set_progress_callback(_progress_to_event)

    LOG.info("sidecar started", extra={"pid": os.getpid()})

    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            # DEBUG: 记录收到的 JSON-RPC 请求到 stderr（用于排查 GUI 真实参数）
            try:
                _req_dbg = json.loads(line)
                _method_dbg = _req_dbg.get("method")
                _params_dbg = _req_dbg.get("params", {})
                # 把 report_json 的结构（如果存在）打印出来
                if "report_json" in _params_dbg:
                    rj = _params_dbg["report_json"]
                    rj_summary = {
                        "keys": list(rj.keys()) if isinstance(rj, dict) else f"NOT DICT: {type(rj).__name__}",
                        "tomorrow": rj.get("tomorrow") if isinstance(rj, dict) else None,
                        "context_gaps": rj.get("context_gaps") if isinstance(rj, dict) else None,
                    }
                    sys.stderr.write(f"[sidecar] >>> method={_method_dbg} report_json_summary={json.dumps(rj_summary, ensure_ascii=False)}\n")
                    sys.stderr.flush()
                else:
                    sys.stderr.write(f"[sidecar] >>> method={_method_dbg} params_keys={list(_params_dbg.keys()) if isinstance(_params_dbg, dict) else type(_params_dbg).__name__}\n")
                    sys.stderr.flush()
            except Exception:
                pass
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                _write_frame({
                    "id": None,
                    "error": {"code": CODE_PARSE_ERROR, "message": f"Invalid JSON: {e}"},
                })
                continue
            try:
                response = _handle(request)
            except Exception as e:  # noqa: BLE001  极端兜底
                LOG.exception("unhandled error in handler")
                response = {
                    "id": request.get("id") if isinstance(request, dict) else None,
                    "error": {
                        "code": CODE_INTERNAL_ERROR,
                        "message": f"Internal error: {e}",
                        "data": {"trace": traceback.format_exc()[:1000]},
                    },
                }
            _write_frame(response)
    except KeyboardInterrupt:
        LOG.info("sidecar interrupted")
    finally:
        # 关闭 auth 拉起的 background 子进程（dws auth login 等）
        try:
            from .core.auth import shutdown_background_procs
            shutdown_background_procs()
        except Exception as e:  # noqa: BLE001
            LOG.warning("shutdown background procs failed", extra={"err": str(e)})
        LOG.info("sidecar exited")
    return 0


if __name__ == "__main__":
    sys.exit(main())
