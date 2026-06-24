"""daily_report.py — 日报 bundle 导出 / 渲染 / 历史

流程（api-protocol.md §3.5）：
  阶段 1: fetching_chats  → 调 dws chat message list-all 全量拉取
  阶段 2: ai_summarize    → 已配置 LLM 时自动调 ai_bridge；未配置时返回 awaiting_ai
  阶段 3: render          → PIL 渲染 PNG + 写 MD

进度事件通过 sidecar.events.emit() 主动推送，前端监听。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import llm_config, logging_setup, paths
from .ai_bridge_runner import run_ai_bridge

# 复用现有 dingtalk_daily 模块
def _load_dds():
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "")) / "external"
    else:
        base = Path(__file__).resolve().parent.parent.parent / "external"
    p = base / "dingtalk_daily_summary.py"
    if not p.is_file():
        raise FileNotFoundError(f"dingtalk_daily_summary.py not found at {p}")
    spec = importlib.util.spec_from_file_location("dingtalk_daily_summary", p)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

dds = _load_dds()
LOG = logging_setup.setup("daily_report")


# ── 进度回调注入点 ────────────────────────────────────────────────────
_progress_cb: Callable[[str, int, str | None], None] | None = None


def set_progress_callback(cb: Callable[[str, int, str | None], None] | None) -> None:
    global _progress_cb
    _progress_cb = cb


def _emit(stage: str, percent: int, detail: str | None = None) -> None:
    if _progress_cb is None:
        line = f"[{stage}] {percent}%"
        if detail:
            line += f" {detail}"
        print(line, file=sys.stderr, flush=True)
        return
    try:
        _progress_cb(stage, percent, detail)
    except Exception as e:  # noqa: BLE001
        LOG.warning("progress callback failed", extra={"err": str(e)})


# ── 调 ai_bridge 子进程（封装在 ai_bridge_runner）────────────────────
# 旧 _run_ai_bridge 已迁移到 core/ai_bridge_runner.py；
# 这里直接 import run_ai_bridge。


# ── 3.5 generate_daily（核心） ─────────────────────────────────────
def generate(params: dict) -> dict:
    """生成日报

    params:
      date: "YYYY-MM-DD"（默认今天）
      report_json: dict（如果已由前端 AI 分析完，可直接传入；否则走两步流程）

    进度阶段：
      fetching_chats (0-40)
      ai_summarize    (40-65)
      render_png      (65-90)
      render_md       (90-100)
    """
    date = params.get("date") or datetime.now().strftime("%Y-%m-%d")
    # v0.3.9：拒绝未来日期（前端日历已禁用，后端兜底防绕过）
    try:
        target = datetime.strptime(date, "%Y-%m-%d").date()
        today_ = datetime.now().date()
        if target > today_:
            raise ValueError(
                f"不能生成未来日期的日报：{date}（今天 {today_}）"
            )
    except ValueError as e:
        # 区分「日期格式错」vs「未来日期」，都抛给前端
        if "future" in str(e).lower() or "未来" in str(e):
            raise
        # strptime 失败的格式错 — 抛给上层统一报
        raise ValueError(f"日期格式错误：{date}（需 YYYY-MM-DD）") from e
    report = params.get("report_json")

    start = time.time()
    out_dir = paths.output_for_date(date)
    _emit("fetching_chats", 0, f"开始拉取 {date} 的会话")

    # 阶段 1: 导出 bundle（Opt 2 修复：复用已有 bundle 避免重复 export）
    existing_bundle = params.get("bundle_path")
    if existing_bundle and Path(existing_bundle).is_file():
        # 复用路径：跳过 dws 全量拉取（AI 未配时第二次调 generate_daily 不用再 export）
        bundle_result = {
            "bundle_path": str(existing_bundle),
            # chat_count / message_count 无法从现有 bundle 拿，
            # 但 awaiting_ai 第二步不需要这俩字段（render 不依赖）
            "chat_count": 0,
            "message_count": 0,
        }
        chat_count = 0
        msg_count = 0
        _emit("fetching_chats", 40, f"复用已有 bundle: {Path(existing_bundle).name}")
    else:
        bundle_result = dds.export_daily_bundle(
            date_str=date,
            output_dir=str(out_dir),
            message_limit_per_chat=0,
        )
        chat_count = bundle_result["chat_count"]
        msg_count = bundle_result["message_count"]
        _emit("fetching_chats", 40, f"已拉取 {chat_count} 个会话 / {msg_count} 条消息")

    if report is None:
        # 阶段 2: 看是否配了 LLM
        llm_cfg = llm_config.get_config()
        if not llm_cfg["configured"]:
            _emit("ai_summarize", 40, "等待 AI 分析（未配置 LLM）")
            return {
                "stage": "awaiting_ai",
                "date": date,
                "bundle_path": bundle_result["bundle_path"],
                "prompt_path": bundle_result["prompt_path"],
                "report_template_path": bundle_result["report_template_path"],
                "chat_count": chat_count,
                "message_count": msg_count,
            }

        # 已配置 → 调 ai_bridge
        _emit("ai_summarize", 45, f"调用 {llm_cfg['provider']}/{llm_cfg.get('model')} 分析...")
        # resolve_api_key：用户保存的自定义 key 优先，否则用内置默认（env var）
        # 必须显式传 api_key 给 run_ai_bridge（走 DINGTALK_API_KEY env var），
        # 否则 ai_bridge 子进程会自己读 llm_secret.bin，对 builtin 场景会 DPAPI 失败
        api_key = llm_config.resolve_api_key()
        rc, payload = run_ai_bridge(
            bundle_path=bundle_result["bundle_path"],
            prompt_path=bundle_result["prompt_path"],
            output_dir=str(out_dir),
            provider_id=llm_cfg["provider"],
            model=llm_cfg.get("model"),
            api_key=api_key,
            retries=3,  # 429 自动重试 2 次（间隔 2s/4s）
        )
        if rc != 0 or not payload.get("ok"):
            err_code = payload.get("err_code", "CRASH")
            err_msg = payload.get("err_msg", "AI 子进程异常")
            from .errors import AiBridgeError
            raise AiBridgeError(err_code, err_msg, data={"raw": payload})

        _emit("ai_summarize", 65, "AI 分析完成")
        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        report_path = out_dir / "report.json"
    else:
        # 阶段 2b: 用户已经传 report_json（兼容老 awaiting_ai 流程）
        _emit("ai_summarize", 65, "AI 分析完成")
        report_path = out_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 阶段 3: 渲染
    _emit("render_png", 70, "渲染长图")
    render_result = dds.render_report(
        report,
        output_dir=str(out_dir),
        base_name=None,
    )
    _emit("render_md", 95, "生成 Markdown")

    duration_ms = int((time.time() - start) * 1000)
    png_path = Path(render_result["image_path"])
    md_path = Path(render_result["markdown_path"])
    _emit("done", 100, "完成")

    return {
        "date": date,
        "stage": "done",
        "stats": {
            "chat_count": chat_count,
            "message_count": msg_count,
            "duration_ms": duration_ms,
        },
        "output": {
            "png_path": str(png_path),
            "md_path": str(md_path),
            "json_path": str(report_path),
            "bundle_path": bundle_result["bundle_path"],
        },
        "png_size_bytes": png_path.stat().st_size if png_path.exists() else 0,
        "md_size_bytes": md_path.stat().st_size if md_path.exists() else 0,
    }


# ── 3.5b render_only（拿到 report.json 后单独渲染） ─────────────────
def render_only(params: dict) -> dict:
    """从已存在的 report.json 渲染

    params: {"report_path": "..."}
    """
    report_path = Path(params.get("report_path", ""))
    if not report_path.is_file():
        raise FileNotFoundError(f"report.json 不存在：{report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    date = report.get("date") or datetime.now().strftime("%Y-%m-%d")
    out_dir = paths.output_for_date(date)
    _emit("render_png", 70, "渲染长图")
    res = dds.render_report(report, output_dir=str(out_dir), base_name=None)
    _emit("render_md", 95, "生成 Markdown")
    _emit("done", 100, "完成")
    return {
        "date": date,
        "output": {
            "png_path": res["image_path"],
            "md_path": res["markdown_path"],
        },
    }


# ── 3.7 list_history ───────────────────────────────────────────────
def list_history(_params: dict) -> dict:
    """列出所有历史日报（按日期倒序）"""
    import re
    items: list[dict] = []
    root = paths.output_dir()
    # 显式按名字排序（FS 顺序不可靠：FAT32/NTFS/ext4 各异）
    for d in sorted(root.iterdir(), key=lambda x: x.name, reverse=True):
        if not d.is_dir():
            continue
        if not re.match(r"\d{4}-\d{2}-\d{2}$", d.name):
            continue
        png = next(d.glob("钉钉工作纪要日报-*.png"), None) or next(d.glob("*.png"), None)
        md = next(d.glob("钉钉工作纪要日报-*.md"), None) or next(d.glob("*.md"), None)
        report = d / "report.json"
        bundle = d / "dingtalk_bundle.json"
        items.append({
            "date": d.name,
            "png_path": str(png) if png else None,
            "md_path": str(md) if md else None,
            "json_path": str(report) if report.exists() else None,
            "bundle_path": str(bundle) if bundle.exists() else None,
            "size_bytes": png.stat().st_size if png and png.exists() else 0,
            "generated_at": datetime.fromtimestamp(png.stat().st_mtime).isoformat() if png and png.exists() else None,
        })
    return {"items": items, "total": len(items)}


# ── 3.8 open_output ─────────────────────────────────────────────────
def open_output_dir(params: dict) -> dict:
    """返回某日期报告目录路径（前端用 file:// 打开）

    params: {"date": "YYYY-MM-DD"}（默认最近一次）
    """
    date = params.get("date")
    if date:
        target = paths.output_for_date(date)
    else:
        # 最新一份
        items = list_history({})["items"]
        if not items:
            target = paths.output_dir()
        else:
            target = Path(items[0]["png_path"]).parent if items[0].get("png_path") else paths.output_dir()
    return {"ok": True, "path": str(target)}
