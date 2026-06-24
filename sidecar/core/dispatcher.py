"""dispatcher.py — JSON-RPC method router

对应 api-protocol.md §3
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from . import auth, daily_report, install, llm_config, logging_setup, paths, send

# v0.3.14：版本号从 version.__version__ 单一真相源读取，不再硬编码 "0.1.0"
from version import __version__ as _VERSION  # noqa: E402

LOG = logging_setup.setup("dispatcher")


def _read_text_file(params: dict) -> dict:
    """安全读取 output/ 下的文本文件（前端无法直接 fetch file://）

    .json 后缀：解析为 dict（成功/失败都明确报错，不再静默 fallback 到文本）
    其它后缀：原样返回文本
    """
    p_str = params.get("path", "")
    if not p_str:
        raise ValueError("path 必填")
    p = Path(p_str)
    if not paths.is_under_output(p):
        from . import send as _send
        raise _send.PathTraversal(str(p), str(paths.output_dir()))
    if not p.is_file():
        raise FileNotFoundError(str(p))
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        # 显式解析 — 失败时 raise（前端能立刻看到，而不是拿到字符串再 stringify）
        return {"content": json.loads(text), "path": str(p)}
    return {"content": text, "path": str(p)}


_MIME_BY_EXT = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".bmp":  "image/bmp",
}


def _read_file_data_url(params: dict) -> dict:
    """把 output/ 下的二进制文件（PNG/JPG 等）读成 data: URL，前端 <img> 直接渲染。
    避免依赖系统默认图片查看器，规避 file:// 在 webview 里被拦截的问题。
    """
    import base64
    p_str = params.get("path", "")
    if not p_str:
        raise ValueError("path 必填")
    p = Path(p_str)
    if not paths.is_under_output(p):
        from . import send as _send
        raise _send.PathTraversal(str(p), str(paths.output_dir()))
    if not p.is_file():
        raise FileNotFoundError(str(p))
    mime = _MIME_BY_EXT.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"不支持的文件类型：{p.suffix}（仅支持图片）")
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return {"data_url": f"data:{mime};base64,{b64}", "path": str(p), "size": len(raw)}


def _open_with_system(params: dict) -> dict:
    """调系统默认应用打开 output/ 下的文件（.md / .png 等）
    走 os.startfile（Windows）/ xdg-open（Linux）/ open（macOS）。
    失败时返回明确错误（用户能看到"为什么没反应"），不再静默。
    """
    import os
    import subprocess
    import sys
    p_str = params.get("path", "")
    if not p_str:
        raise ValueError("path 必填")
    p = Path(p_str)
    if not paths.is_under_output(p):
        from . import send as _send
        raise _send.PathTraversal(str(p), str(paths.output_dir()))
    if not p.is_file():
        raise FileNotFoundError(str(p))

    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except FileNotFoundError as e:
        # Windows: 系统没装默认 .md 编辑器时会抛 FileNotFoundError
        # 提示用户去装或关联应用，并降级到内嵌预览
        return {
            "ok": False,
            "err_code": "NO_DEFAULT_APP",
            "err_msg": f"系统没有为 {p.suffix} 关联默认应用。请安装 Typora / VSCode / Notepad++ 等编辑器。",
            "path": str(p),
        }
    except OSError as e:
        return {
            "ok": False,
            "err_code": "OPEN_FAILED",
            "err_msg": f"打开失败：{e.strerror or e}",
            "path": str(p),
        }
    return {"ok": True, "path": str(p)}


# ── LLM 配置 / 调用的 4 个 method ─────────────────────────────────
def _get_llm_config(_params: dict) -> dict:
    return llm_config.get_config()


def _get_daily_config(_params: dict) -> dict:
    """前端用：读 daily_report 相关配置
    主要给「生成后自动发送」用 — 前端要知道 default_send_target.type
    """
    return {
        "default_send_target": config.get("daily_report.default_send_target", {"type": "self"}),
        "include_sections": config.get("daily_report.include_sections", []),
        "exclude_chats": config.get("daily_report.exclude_chats", []),
    }


def _set_llm_config(params: dict) -> dict:
    if params.get("clear"):
        llm_config.clear_config()
        return {"ok": True, "configured": False}
    provider = params.get("provider")
    model = params.get("model")
    api_key = params.get("api_key")
    skip_key = bool(params.get("skip_key", False))
    if not all([provider, model]):
        raise ValueError("provider / model 都必填")
    if not skip_key and not api_key:
        raise ValueError("provider / model / api_key 都必填（skip_key=True 时 api_key 可省略）")
    llm_config.set_config(provider, model, api_key or "", skip_key=skip_key)
    return {"ok": True, "configured": True, "provider": provider, "model": model, "key_updated": not skip_key}


def _test_llm_connection(params: dict) -> dict:
    """测试 LLM 连接（不落盘）。
    前端只传 provider / model / api_key（base_url 不传）；
    base_url 从 preset 取。

    走 ai_bridge_runner.run_ai_bridge 子进程化，保留原始 err_code / err_msg，
    前端能看到「HTTP_401 / HTTP_429 / HTTP_5XX / TIMEOUT」具体原因。
    """
    from ai_bridge import providers as _providers
    from .ai_bridge_runner import run_ai_bridge

    api_key = params.get("api_key")
    model = params.get("model")
    provider_id = params.get("provider")

    if not api_key:
        cfg = llm_config.get_config()
        if not cfg["configured"]:
            raise ValueError("需要先填 api_key 或先配置 LLM")
        # resolve_api_key：用户保存的自定义 key 优先，否则用内置默认（env var）
        api_key = llm_config.resolve_api_key()
        if not model:
            model = cfg["model"]
        if not provider_id:
            provider_id = cfg["provider"]

    if not provider_id or not model:
        raise ValueError("provider 和 model 必填")

    # 从 preset 取 base_url（前端不再传）
    preset = _providers.get_by_id(provider_id)
    base_url = preset["base_url"].rstrip("/")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        bundle = Path(td) / "bundle.json"
        bundle.write_text("{}", encoding="utf-8")
        prompt = Path(td) / "prompt.md"
        prompt.write_text("# stub", encoding="utf-8")
        out_dir = Path(td) / "out"
        out_dir.mkdir()
        start = time.time()
        # retries=1：测试连接要立即给用户真实错误，不重试
        rc, payload = run_ai_bridge(
            bundle_path=str(bundle),
            prompt_path=str(prompt),
            output_dir=str(out_dir),
            provider_id=provider_id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=30,
            retries=1,
        )
        duration_ms = int((time.time() - start) * 1000)
        if rc == 0 and payload.get("ok"):
            return {"ok": True, "latency_ms": duration_ms, "provider": provider_id, "model": model}
        # 把 err_code / err_msg 透传给前端；前端的 testLlmConnection 用 r.err_msg 显示
        return {
            "ok": False,
            "latency_ms": duration_ms,
            "err_code": payload.get("err_code", "CRASH"),
            "err_msg": payload.get("err_msg", "AI 子进程返回失败"),
        }


def _ai_analyze(params: dict) -> dict:
    """调试用：手工触发一次 AI 分析"""
    bundle_path = params.get("bundle_path")
    prompt_path = params.get("prompt_path")
    output_dir = params.get("output_dir")
    if not all([bundle_path, prompt_path, output_dir]):
        raise ValueError("bundle_path / prompt_path / output_dir 必填")
    from ai_bridge import main as bridge_main
    start = time.time()
    rc = bridge_main.run([
        "--bundle-path", bundle_path,
        "--prompt-path", prompt_path,
        "--output-dir", output_dir,
        "--provider-id", params.get("provider_id", "openai"),
    ])
    duration_ms = int((time.time() - start) * 1000)
    out_json = Path(output_dir) / "report.json"
    return {
        "ok": (rc == 0),
        "duration_ms": duration_ms,
        "report_json_path": str(out_json) if out_json.is_file() else None,
    }


METHODS = {
    "ping": lambda p: {"ok": True, "version": _VERSION},
    "get_login_status": auth.get_login_status,
    "trigger_login": auth.trigger_login,
    "trigger_logout": auth.trigger_logout,
    "cancel_login": auth.cancel_login,
    "validate_corp": auth.validate_corp,
    "validate_corp_with_status": auth._validate_corp_with_status,
    "generate_daily": daily_report.generate,
    "send_to_dingtalk": send.send_file,
    "list_history": daily_report.list_history,
    "open_output": daily_report.open_output_dir,
    "diagnose": send.collect_diagnostics,
    "read_text_file": _read_text_file,
    "read_file_data_url": _read_file_data_url,
    "open_with_system": _open_with_system,
    # ↓ dws 安装 / 检测 ↓
    "check_dws_install": install.check_install,
    "install_dws": install.install_dws,
    # ↓ LLM ↓
    "get_llm_config": _get_llm_config,
    "get_daily_config": _get_daily_config,
    "set_llm_config": _set_llm_config,
    "test_llm_connection": _test_llm_connection,
    "ai_analyze": _ai_analyze,
    "list_providers": lambda p: {"providers": llm_config.list_providers()},
}


def dispatch(method: str, params: dict) -> Any:
    handler = METHODS.get(method)
    if not handler:
        raise ValueError(f"Unknown method: {method}")
    LOG.info("dispatch", extra={"method": method})
    return handler(params or {})
