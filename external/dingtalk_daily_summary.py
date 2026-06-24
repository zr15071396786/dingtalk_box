"""
钉钉工作纪要日报 — 核心库
对应 wechat-cli/reporting/daily_summary.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# ── 渲染常量───────────────────────────────────────
FULL_WIDTH = 1568
BG = "#ffffff"
FG = "#222222"
MUTED = "#b8b8b8"
LINE = "#3a3a3a"

# ── 消息类型映射──────────────────────────────────────────────────────────────
def _guess_msg_type(content: str) -> str:
    if not content:
        return "文本"
    if content.startswith("[图片消息]") or content.startswith("[图片]"):
        return "图片"
    if content.startswith("[文件]"):
        return "文件"
    if content.startswith("[链接]") or "[dingtalk://" in content:
        return "链接"
    if content.startswith("[赞]"):
        return "表情"
    if content.startswith("[小程序]"):
        return "小程序"
    if content.startswith("[语音]") or content.startswith("[通话]"):
        return "语音"
    if content.startswith("[视频]"):
        return "视频"
    if content.startswith("[表情]"):
        return "表情"
    return "文本"


def _clean_text(content: str) -> str:
    """去掉钉钉特有的 mediaId/dingtalk:// 等噪音标记"""
    if not content:
        return ""
    text = re.sub(r'\[图片消息\]\(mediaId=[^)]+\)', "[图片]", content)
    text = re.sub(r'\[图片消息\]', "[图片]", text)
    text = re.sub(r'注意：如需下载使用dws chat message download-media命令下载，请使用@开头的mediaId', "", text)
    text = re.sub(r'dingtalk://[^\s\)]+', "", text)
    text = re.sub(r'\[dingtalk://[^\]]+\]', "[链接]", text)
    text = re.sub(r'\(mediaId=[^)]+\)', "", text)
    text = re.sub(r'fileId: [^\s]+', "", text)
    text = re.sub(r'!\[image\]\([^)]+\)', "[图片]", text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── 会话分类──────────────────────────────────────────────────────────────────
#   singleChat=false（群聊）→ work
#   singleChat=true（私聊） → personal

def _guess_bucket(title: str, single_chat: bool) -> str:
    return "personal" if single_chat else "work"


# ── 噪声 chat 黑名单（不进入 report，节省 token）────────────────────────────────
NOISE_CHAT_TITLES = {
    "钉钉客服",       # 系统客服
    "钉钉小秘书",     # 系统推送
    "AI助理",         # 系统机器人
    "钉钉团队",       # 官方运营
    "通知中心",       # 系统通知
    "工作通知",       # 公司广播
    "OA审批助手",       # 系统通知
    "邮箱助手",       # 系统通知
    "公告",           # 公告群
    "考勤打卡",       # 打卡机器人
}

def _is_noise_chat(title: str) -> bool:
    return title in NOISE_CHAT_TITLES


# ── DWS list-all 调用─────────────────────────────────────────────────────────
def _call_dws_list_all(start_time: str, end_time: str, cursor: str = "0", limit: int = 50) -> dict:
    result = subprocess.run(
        [
            "dws", "chat", "message", "list-all",
            "--start", start_time,
            "--end", end_time,
            "--cursor", cursor,
            "--limit", str(limit),
            "--format", "json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return {"hasMore": False, "conversationMessagesList": []}
    try:
        data = json.loads(result.stdout)
        result_obj = data.get("result", {}) or {}
        return {
            "hasMore": bool(result_obj.get("hasMore")),
            "nextCursor": str(result_obj.get("nextCursor") or ""),
            "conversationMessagesList": result_obj.get("conversationMessagesList") or [],
        }
    except json.JSONDecodeError:
        return {"hasMore": False, "conversationMessagesList": []}


def _parse_messages(messages_raw: list[dict], date_str: str) -> list[dict[str, Any]]:
    """将原始消息列表转换为标准格式"""
    messages: list[dict[str, Any]] = []
    for m in messages_raw:
        raw_content = m.get("content", "")
        text = _clean_text(raw_content)
        msg_type = _guess_msg_type(raw_content)
        create_time = m.get("createTime", "")
        try:
            ts = int(datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").timestamp())
        except Exception:
            ts = 0
        sender = m.get("sender", "")
        sender_open_id = m.get("senderOpenDingTalkId", "")
        messages.append({
            "timestamp": ts,
            "time": create_time,
            "sender": sender,
            "senderOpenDingTalkId": sender_open_id,
            "text": text,
            "msg_type": msg_type,
            "local_type": 1,
            "local_id": m.get("openMessageId", ""),
        })
    return messages


# ── Bundle 导出───────────────────────────────────────────────────────────────
def export_daily_bundle(date_str: str, output_dir: str, message_limit_per_chat: int = 0) -> dict[str, Any]:
    start_time = f"{date_str} 00:00:00"
    end_time = f"{date_str} 23:59:59"

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    chat_dir = root / "chat_exports"
    chat_dir.mkdir(exist_ok=True)
    # 清理上次 export 留下的旧 md 文件，避免与新数据混合导致同名重复
    for old_file in chat_dir.glob("*.md"):
        old_file.unlink()

    all_conversations: list[dict] = []
    cursor = "0"
    page = 0

    print(f"[dingtalk-daily] 开始拉取 {date_str} 的消息...", flush=True)

    while True:
        data = _call_dws_list_all(start_time, end_time, cursor=cursor, limit=50)
        convos = data.get("conversationMessagesList") or []
        if not convos:
            break
        all_conversations.extend(convos)
        page += 1
        print(f"  第 {page} 页: +{len(convos)} 条会话", flush=True)
        if not data.get("hasMore"):
            break
        cursor = str(data.get("nextCursor", ""))
        if cursor == "0" or not cursor:
            break

    print(f"[dingtalk-daily] 共拉取 {len(all_conversations)} 个会话", flush=True)

    # === 阶段 1：按 openConversationId 聚合原始消息（处理 DWS list-all 翻页重复）===
    # DWS list-all API 翻页时按时间窗口分页，同一会话可能被分到多个 conversationMessagesList 对象里
    # （同一 convId 出现多次，每次只含该时间窗内的消息）。这里先按 convId 聚合，
    # 阶段 2 再统一生成 chat_payload 和 markdown 导出，避免 chat_exports 出现"分页切片"文件。
    conv_buckets: dict[str, dict[str, Any]] = {}  # convId -> {title, single_chat, messages_by_id, first_seen}
    fallback_idx = 0

    for conv in all_conversations:
        cid = conv.get("openConversationId", "")
        if not cid:
            # 没有 convId 的会话用序号占位，保证不丢数据
            fallback_idx += 1
            cid = f"__no_conv_id_{fallback_idx}"

        if cid not in conv_buckets:
            conv_buckets[cid] = {
                "title": conv.get("title", "未知会话"),
                "single_chat": conv.get("singleChat", False),
                "messages_by_id": {},
                "first_seen": len(conv_buckets),
            }

        bucket = conv_buckets[cid]
        messages_raw = conv.get("messages") or []
        if message_limit_per_chat > 0:
            messages_raw = messages_raw[:message_limit_per_chat]

        for raw in messages_raw:
            raw_content = raw.get("content", "")
            text = _clean_text(raw_content)
            msg_type = _guess_msg_type(raw_content)
            create_time = raw.get("createTime", "")
            try:
                ts = int(datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").timestamp())
            except Exception:
                ts = 0
            local_id = raw.get("openMessageId", "")
            # 同一 local_id 只保留首次出现的（防御性去重）
            if local_id and local_id in bucket["messages_by_id"]:
                continue
            bucket["messages_by_id"][local_id] = {
                "timestamp": ts,
                "time": create_time,
                "sender": raw.get("sender", ""),
                "senderOpenDingTalkId": raw.get("senderOpenDingTalkId", ""),
                "text": text,
                "msg_type": msg_type,
                "local_type": 1,
                "local_id": local_id,
            }

    # === 阶段 2：按聚合结果生成 chat_payload 和 markdown 导出（按首次出现顺序）===
    chats: list[dict[str, Any]] = []
    total_messages = 0
    noise_count = 0
    work_count = 0
    personal_count = 0
    sorted_cids = sorted(conv_buckets.keys(), key=lambda c: conv_buckets[c]["first_seen"])

    for new_idx, cid in enumerate(sorted_cids, start=1):
        bucket = conv_buckets[cid]
        title = bucket["title"]
        single_chat = bucket["single_chat"]

        # ⭐ 跳过噪声 chat（不进入 bundle、report、md 导出）
        if _is_noise_chat(title):
            noise_count += 1
            continue

        bucket_hint = _guess_bucket(title, single_chat)
        if bucket_hint == "work":
            work_count += 1
        else:
            personal_count += 1
        # messages_by_id 是 dict，按 timestamp 排序后转 list
        messages: list[dict[str, Any]] = sorted(
            bucket["messages_by_id"].values(),
            key=lambda m: m.get("timestamp", 0),
        )
        # 真正的 convId（去掉占位前缀）
        real_conv_id = cid if not cid.startswith("__no_conv_id_") else ""

        chat_payload = {
            "chat": title,
            "openConversationId": real_conv_id,
            "is_group": not single_chat,
            "is_official": False,
            "bucket_hint": bucket_hint,
            "unread": 0,
            "last_message": messages[-1]["text"] if messages else "",
            "msg_type": messages[-1]["msg_type"] if messages else "文本",
            "sender": messages[-1]["sender"] if messages else "",
            "timestamp": messages[-1]["timestamp"] if messages else 0,
            "time": messages[-1]["time"][:16] if messages else "",
            "message_count": len(messages),
            "messages": messages,
            "stats": None,
            "failures": None,
        }

        # 写单个会话 markdown
        md_lines = [
            f"# {'群聊' if not single_chat else '私聊'}：{title}",
            f"- 日期：{date_str}",
            f"- 类型：{'群聊' if not single_chat else '私聊'}",
            f"- 归类：{bucket_hint}",
            f"- 消息数：{len(messages)}",
            "",
            "---",
            "",
        ]
        for m in messages:
            prefix = f"[{m['time'][:16]}]"
            if m["sender"]:
                md_lines.append(f"- {prefix} {m['sender']}: {m['text']}")
            else:
                md_lines.append(f"- {prefix} {m['text']}")

        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", title).strip()[:50]
        if not safe_name:
            safe_name = f"chat_{new_idx:03d}"
        md_path = chat_dir / f"{new_idx:03d}_{safe_name}.md"
        md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        chat_payload["markdown_export"] = f"chat_exports/{md_path.name}"

        chats.append(chat_payload)
        total_messages += len(messages)

    bundle = {
        "meta": {
            "bundle_version": 1,
            "date": date_str,
            "start_time": start_time,
            "end_time": end_time,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chat_count": len(chats),
            "message_count": total_messages,
            "work_chat_count": work_count,
            "personal_chat_count": personal_count,
            "noise_chat_count": noise_count,
            "official_chat_count": 0,
            "message_limit_per_chat": message_limit_per_chat,
        },
        "chats": chats,
    }

    bundle_path = root / "dingtalk_bundle.json"
    prompt_path = root / "dingtalk_daily_summary_prompt.md"
    template_path = root / "report_template.json"

    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt_path.write_text(build_prompt_template(date_str), encoding="utf-8")
    template_path.write_text(json.dumps(build_report_template(date_str), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 统计：从 DWS list-all 翻页后的原始 conversation 数 vs 去重后的真实独立会话数
    raw_conv_count = len(all_conversations)
    unique_chat_count = len(chats)
    pagination_merged = max(0, raw_conv_count - unique_chat_count)
    if pagination_merged > 0:
        print(f"[dingtalk-daily] [OK] Dedup merged: raw {raw_conv_count} conversation objects -> {unique_chat_count} unique chats (merged {pagination_merged} pagination slices)", flush=True)

    return {
        "date": date_str,
        "output_dir": str(root),
        "bundle_path": str(bundle_path),
        "prompt_path": str(prompt_path),
        "report_template_path": str(template_path),
        "chat_count": unique_chat_count,
        "raw_conv_count": raw_conv_count,
        "pagination_merged": pagination_merged,
        "message_count": total_messages,
        "official_chat_count": 0,
    }


# ── 钉钉工作纪要专用的 report / prompt 模板─────────────────────────────────────────────
def build_report_template(date_str: str) -> dict[str, Any]:
    return {
        "date": date_str,
        "title": "钉钉工作纪要日报",
        "disclaimer": "内容由AI生成，以下内容基于当天真实钉钉聊天记录整理，请对关键事实和建议自行复核",
        "overview": {
            "work": [{"group": "群名", "summary": "一句话总结本群今日讨论要点"}],
            "personal": [{"person": "私聊对象", "summary": "一句话总结本私聊今日要点"}],
        },
        "work": {
            "handled_items": [{"chat": "群名", "item": "事项内容", "action": "我的处理情况", "result": "结果"}],
            "key_decisions": [{"chat": "群名", "decision": "决策内容", "decision_makers": "决策人", "impact": "后续影响"}],
            "projects": [{"project": "项目/任务名", "chat": "群名", "progress": "今日进展", "status": "当前状态", "next_step": "下一步"}],
            "reply_needed": [{"priority": "紧急程度", "chat": "群名", "need_reply": "对方需要我回复什么", "suggested_reply": "建议回复"}],
            "risks": [{"chat": "群名", "issue": "问题", "impact": "可能影响", "suggestion": "建议处理"}],
        },
        "personal": {
            "highlights": [{"person": "私聊对象", "content": "事项内容", "need_reply": "需要回复", "suggestion": "处理建议"}],
            "reply_needed": [{"priority": "紧急程度", "person": "私聊对象", "need_reply": "对方需要我回复什么", "suggested_reply": "建议回复"}],
        },
        "tomorrow": {
            "work": [{"priority": "优先级", "chat": "群名", "task": "明天要做什么", "reason": "原因"}],
            "personal": [{"priority": "优先级", "person": "私聊对象", "task": "明天要做什么", "reason": "原因"}],
        },
        "context_gaps": {
            "work": [{"chat": "群名", "reviewed_scope": "已回看范围", "missing_info": "缺少的信息", "impact": "对判断的影响"}],
            "personal": [{"person": "私聊对象", "reviewed_scope": "已回看范围", "missing_info": "缺少的信息", "impact": "对判断的影响"}],
        },
    }


def build_prompt_template(date_str: str) -> str:
    schema = json.dumps(build_report_template(date_str), ensure_ascii=False, indent=2)
    return f"""# 钉钉工作纪要日报自动化提示词

你的任务：阅读同目录下 `dingtalk_bundle.json` 中 {date_str} 的真实钉钉聊天记录，生成一个结构化的 `report.json` 并渲染成浅色长图。

## 私聊消息筛选规则
私聊会话（bucket_hint=personal）：分析该私聊的全部消息。

## 群聊消息筛选规则

对于每个群聊会话，请先筛选出【和我有关的消息】，共 4 类：

**① 我发过的消息**
- sender（发送者）字段等于我的名字/昵称的消息

**② @我的消息**
- content 内容中包含我的名字、昵称，或 @我的消息
- 或 sender 中提到我

**③ @所有人的消息**
- content 内容中包含 @所有人、@all、或 @ALL
- 这类通知属于重要公共信息，需要纳入纪要

**④ 上下文关联消息**
- 对于上述 ① ② ③ 中的每条消息，往上和往下追溯 10 条消息
- 作为该消息的前置上下文，保证内容连贯不断层

**最终请只基于上述 ① ② ③ ④ 类消息生成纪要。**

群里与我无关的纯讨论（不属于上述任何一类）请忽略，不必强行提取。

## 数据范围
本报告基于「和我有关的群聊消息 + 完整的私聊消息」生成，群里未 @我且我也未参与讨论的内容不纳入。如需查看全量讨论，请查阅原始聊天记录。

## 分析边界
- 只使用 `dingtalk_bundle.json` 里的真实聊天记录。
- `bucket_hint` 字段区分会话类型：`work`=群聊，`personal`=私聊。
- 系统通知、营销推送默认忽略，除非包含明确待办、风险或重要决策。
- 不要编造聊天记录里没有的信息。
- 某板块无内容时允许留空数组。

## JSON 结构
严格输出为下面这个 JSON 结构，不要额外包裹 markdown：

```json
{schema}
```
"""


# ── 钉钉群聊工作纪要专用的 report / prompt 模板 ────────────────────────────────


# ── 渲染引擎─────────────────────────
def _load_font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _make_draw() -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(Image.new("RGB", (FULL_WIDTH, 10), BG))


def _line_height(draw: ImageDraw.ImageDraw, font) -> int:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return bbox[3] - bbox[1]


def _wrap_text(draw: ImageDraw.ImageDraw, text: Any, font, max_width: int) -> list[str]:
    raw = str(text or "")
    paragraphs = raw.splitlines() or [""]
    lines: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if not current or draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines or [""]


def _draw_multiline(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, text: Any, font, fill: str, spacing: int = 10) -> int:
    lines = _wrap_text(draw, text, font, width)
    line_h = _line_height(draw, font)
    cursor_y = y
    for line in lines:
        draw.text((x, cursor_y), line, font=font, fill=fill)
        cursor_y += line_h + spacing
    return cursor_y - y - (spacing if lines else 0)


def _measure_multiline(draw: ImageDraw.ImageDraw, width: int, text: Any, font, spacing: int = 10) -> int:
    lines = _wrap_text(draw, text, font, width)
    if not lines:
        return 0
    return len(lines) * _line_height(draw, font) + (len(lines) - 1) * spacing


def _ensure_rows(rows: list[dict[str, Any]] | None, fallback: str, columns: list[str]) -> list[dict[str, Any]]:
    if rows:
        return rows
    row = {column: "-" for column in columns}
    row[columns[0]] = fallback
    return [row]


def _draw_numbered_list(draw: ImageDraw.ImageDraw | None, x: int, y: int, width: int, items: list[str], font, fill: str) -> int:
    helper = draw or _make_draw()
    line_h = _line_height(helper, font)
    cursor_y = y
    for index, item in enumerate(items, start=1):
        number = f"{index}."
        number_width = int(helper.textlength(number, font=font)) + 18
        item_height = _measure_multiline(helper, width - number_width, item, font, spacing=8)
        if draw:
            draw.text((x, cursor_y), number, font=font, fill=fill)
            _draw_multiline(draw, x + number_width, cursor_y, width - number_width, item, font, fill, spacing=8)
        cursor_y += max(line_h, item_height) + 18
    return cursor_y - y


def _measure_table(draw: ImageDraw.ImageDraw, rows: list[dict[str, Any]], columns: list[tuple[str, str, int]], fonts: dict[str, Any], width: int) -> tuple[int, list[dict[str, Any]]]:
    padding_x = 14
    padding_y = 12
    gap = 24
    total_weight = sum(weight for _, _, weight in columns)
    inner_width = width - gap * (len(columns) - 1)
    layouts: list[dict[str, Any]] = []
    total_height = 0
    for row in rows:
        # 兼容字符串行（如 fallback "今天未发现明确已处理群聊事项"）
        if isinstance(row, str):
            row = {columns[0][0]: row}
        cell_layouts = []
        row_height = 0
        for key, _, weight in columns:
            column_width = int(inner_width * weight / total_weight)
            text_width = max(40, column_width - padding_x * 2)
            cell_height = _measure_multiline(draw, text_width, row.get(key, ""), fonts["body"], spacing=8)
            row_height = max(row_height, cell_height + padding_y * 2)
            cell_layouts.append({"key": key, "width": column_width, "text_width": text_width})
        layouts.append({"cells": cell_layouts, "row_height": row_height, "row": row})
        total_height += row_height
    header_height = _line_height(draw, fonts["header"]) + padding_y * 2 + 8
    total_height += header_height + 8 * (len(rows) + 1)
    return total_height, layouts


def _draw_table(draw: ImageDraw.ImageDraw | None, x: int, y: int, width: int, columns: list[tuple[str, str, int]], rows: list[dict[str, Any]], fonts: dict[str, Any]) -> int:
    helper = draw or _make_draw()
    table_height, layouts = _measure_table(helper, rows, columns, fonts, width)
    if not draw:
        return table_height
    padding_y = 12
    gap = 24
    total_weight = sum(weight for _, _, weight in columns)
    inner_width = width - gap * (len(columns) - 1)
    cursor_x = x
    header_height = _line_height(draw, fonts["header"]) + padding_y * 2 + 8
    for _, label, weight in columns:
        column_width = int(inner_width * weight / total_weight)
        draw.text((cursor_x, y), label, font=fonts["header"], fill=FG)
        cursor_x += column_width + gap
    draw.line((x, y + header_height - 8, x + width, y + header_height - 8), fill=LINE, width=1)
    cursor_y = y + header_height
    for layout in layouts:
        cursor_x = x
        for cell in layout["cells"]:
            _draw_multiline(draw, cursor_x, cursor_y + padding_y, cell["text_width"], layout["row"].get(cell["key"], ""), fonts["body"], FG, spacing=8)
            cursor_x += cell["width"] + gap
        cursor_y += layout["row_height"]
        draw.line((x, cursor_y, x + width, cursor_y), fill=LINE, width=1)
    return table_height


def _table_markdown(title: str, columns: list[tuple[str, str, int]], rows: list[dict[str, Any]]) -> list[str]:
    headers = [label for _, label, _ in columns]
    keys = [key for key, _, _ in columns]
    lines = [f"### {title}", "", "| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        if isinstance(row, str):
            row = {keys[0]: row}
        lines.append("| " + " | ".join(str(row.get(key, "")).replace("\n", "<br>") for key in keys) + " |")
    lines.append("")
    return lines


# ── 技能一专用：全量渲染（work + personal）────────────────────────────────────
def report_to_markdown_full(report: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('title', '钉钉工作纪要日报')} | {report.get('date', '')}",
        "",
        report.get("disclaimer", ""),
        "",
        "## 一、工作总览",
        "",
        "### 群聊总览",
    ]
    for item in report.get("overview", {}).get("work", []) or ["今天未识别到明确群聊重点事项。"]:
        if isinstance(item, dict):
            lines.append(f"1. {item.get('group', '')}：{item.get('summary', '')}")
        else:
            lines.append(f"1. {item}")

    personal_items = report.get("overview", {}).get("personal", []) or []
    if personal_items:
        lines.extend(["", "### 私聊总览"])
        for item in personal_items:
            if isinstance(item, dict):
                lines.append(f"1. {item.get('person', '')}：{item.get('summary', '')}")
            else:
                lines.append(f"1. {item}")

    work = report.get("work", {})
    personal = report.get("personal", {})
    tomorrow = report.get("tomorrow", {})
    context_gaps = report.get("context_gaps", {})


    lines.extend(["", "## 二、群聊总结", ""])
    lines.extend(_table_markdown("今日已处理群聊事项", [
        ("chat", "对话/群聊", 18), ("item", "事项", 28), ("action", "我的处理情况", 28), ("result", "结果", 26),
    ], _ensure_rows(work.get("handled_items"), "今天未发现明确已处理群聊事项", ["chat", "item", "action", "result"])))
    lines.extend(_table_markdown("今日群聊关键决策", [
        ("chat", "对话/群聊", 18), ("decision", "决策内容", 30), ("decision_makers", "决策人", 18), ("impact", "后续影响", 24),
    ], _ensure_rows(work.get("key_decisions"), "今天未发现明确关键决策", ["chat", "decision", "decision_makers", "impact"])))
    lines.extend(_table_markdown("工作任务/项目进展", [
        ("project", "项目/任务", 18), ("chat", "对话/群聊", 18), ("progress", "今日进展", 28), ("status", "当前状态", 18), ("next_step", "下一步", 18),
    ], _ensure_rows(work.get("projects"), "今天未发现明确项目推进记录", ["project", "chat", "progress", "status", "next_step"])))
    lines.extend(_table_markdown("群聊需要我回复但尚未回复", [
        ("priority", "紧急程度", 12), ("chat", "对话/群聊", 18), ("need_reply", "对方需要我回复什么", 34), ("suggested_reply", "建议回复", 36),
    ], _ensure_rows(work.get("reply_needed"), "今天未发现明确群聊待回复项", ["priority", "chat", "need_reply", "suggested_reply"])))
    lines.extend(_table_markdown("工作风险、延误、争议或信息不清", [
        ("chat", "对话/群聊", 18), ("issue", "问题", 28), ("impact", "可能影响", 22), ("suggestion", "建议处理", 32),
    ], _ensure_rows(work.get("risks"), "今天未发现明确群聊风险项", ["chat", "issue", "impact", "suggestion"])))

    lines.extend(["", "## 三、私聊总结", ""])
    lines.extend(_table_markdown("私聊重点事项", [
        ("person", "对话/群聊", 18), ("content", "事项内容", 30), ("need_reply", "需要回复", 12), ("suggestion", "处理建议", 40),
    ], _ensure_rows(personal.get("highlights"), "今天未发现明确私聊重点事项", ["person", "content", "need_reply", "suggestion"])))
    lines.extend(_table_markdown("私聊待回复", [
        ("priority", "紧急程度", 12), ("person", "对话/群聊", 18), ("need_reply", "对方需要我回复什么", 34), ("suggested_reply", "建议回复", 36),
    ], _ensure_rows(personal.get("reply_needed"), "今天未发现明确私聊待回复项", ["priority", "person", "need_reply", "suggested_reply"])))

    lines.extend(["## 四、工作待办", ""])
    lines.extend(_table_markdown("群聊待办建议优先处理", [
        ("priority", "优先级", 12), ("chat", "对话/群聊", 18), ("task", "明天要做什么", 34), ("reason", "原因", 36),
    ], _ensure_rows(tomorrow.get("work"), "今天未识别到群聊类明日优先事项", ["priority", "chat", "task", "reason"])))
    lines.extend(_table_markdown("私聊待办建议优先处理", [
        ("priority", "优先级", 12), ("person", "对话/群聊", 18), ("task", "明天要做什么", 34), ("reason", "原因", 36),
    ], _ensure_rows(tomorrow.get("personal"), "今天未识别到私聊类明日优先事项", ["priority", "person", "task", "reason"])))

    lines.extend(["", "## 五、上下文不完整说明", ""])
    lines.extend(_table_markdown("群聊上下文不完整说明", [
        ("chat", "对话/群聊", 18), ("reviewed_scope", "已回看范围", 20), ("missing_info", "缺少的信息", 28), ("impact", "对判断的影响", 34),
    ], _ensure_rows(context_gaps.get("work"), "群聊今天没有发现明显上下文缺失的问题", ["chat", "reviewed_scope", "missing_info", "impact"])))
    lines.extend(_table_markdown("私聊上下文不完整说明", [
        ("person", "对话/群聊", 18), ("reviewed_scope", "已回看范围", 20), ("missing_info", "缺少的信息", 28), ("impact", "对判断的影响", 34),
    ], _ensure_rows(context_gaps.get("personal"), "私聊今天没有发现明显上下文缺失的问题", ["person", "reviewed_scope", "missing_info", "impact"])))

    return "\n".join(lines).strip() + "\n"


def _format_overview_items(items: list[Any], name_key: str, fallback: str) -> list[str]:
    """把 overview 列表项归一化成字符串：dict 项 -> "name：summary"，str 项保持不变。

    与 report_to_markdown_full 里的 isinstance 分支保持一致，保证 PNG / MD 文案一致。
    """
    if not items:
        return [fallback]
    out: list[str] = []
    for it in items:
        if isinstance(it, dict):
            name = it.get(name_key, "") or ""
            summary = it.get("summary", "") or ""
            out.append(f"{name}：{summary}" if name else summary)
        else:
            out.append(str(it))
    return out


# ── 技能一专用：全量渲染（work + personal）────────────────────────────────────
def render_report_full(report: dict[str, Any], output_dir: str, base_name: str | None = None, width: int = FULL_WIDTH) -> dict[str, Any]:
    if base_name is None:
        date_str = report.get("date", "")
        base_name = f"钉钉工作纪要日报-{date_str}"
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    measure_draw = _make_draw()
    fonts = {
        "title": _load_font(34, bold=True),
        "section": _load_font(24, bold=True),
        "subsection": _load_font(20, bold=True),
        "body": _load_font(17),
        "header": _load_font(18, bold=True),
    }

    overview = report.get("overview", {})
    work = report.get("work", {})
    personal = report.get("personal", {})
    tomorrow = report.get("tomorrow", {})
    context_gaps = report.get("context_gaps", {})


    blocks = [
        ("list", "一、工作总览", "群聊总览", _format_overview_items(overview.get("work"), "group", "今天未识别到明确群聊重点事项。")),
        ("list", None, "私聊总览", _format_overview_items(overview.get("personal"), "person", "今天未识别到明确私聊重点事项。")),
        ("table", "二、群聊总结", "今日已处理群聊事项", [
            ("chat", "对话/群聊", 18), ("item", "事项", 28), ("action", "我的处理情况", 28), ("result", "结果", 26),
        ], _ensure_rows(work.get("handled_items"), "今天未发现明确已处理群聊事项", ["chat", "item", "action", "result"])),
        ("table", None, "今日群聊关键决策", [
            ("chat", "对话/群聊", 18), ("decision", "决策内容", 30), ("decision_makers", "决策人", 18), ("impact", "后续影响", 24),
        ], _ensure_rows(work.get("key_decisions"), "今天未发现明确关键决策", ["chat", "decision", "decision_makers", "impact"])),
        ("table", None, "工作任务/项目进展", [
            ("project", "项目/任务", 18), ("chat", "对话/群聊", 18), ("progress", "今日进展", 28), ("status", "当前状态", 18), ("next_step", "下一步", 18),
        ], _ensure_rows(work.get("projects"), "今天未发现明确项目推进记录", ["project", "chat", "progress", "status", "next_step"])),
        ("table", None, "群聊需要我回复但尚未回复", [
            ("priority", "紧急程度", 12), ("chat", "对话/群聊", 18), ("need_reply", "对方需要我回复什么", 34), ("suggested_reply", "建议回复", 36),
        ], _ensure_rows(work.get("reply_needed"), "今天未发现明确群聊待回复项", ["priority", "chat", "need_reply", "suggested_reply"])),
        ("table", None, "工作风险、延误、争议或信息不清", [
            ("chat", "对话/群聊", 18), ("issue", "问题", 28), ("impact", "可能影响", 22), ("suggestion", "建议处理", 32),
        ], _ensure_rows(work.get("risks"), "今天未发现明确群聊风险项", ["chat", "issue", "impact", "suggestion"])),
        ("table", "三、私聊总结", "私聊重点事项", [
            ("person", "对话/群聊", 18), ("content", "事项内容", 30), ("need_reply", "需要回复", 12), ("suggestion", "处理建议", 40),
        ], _ensure_rows(personal.get("highlights"), "今天未发现明确私聊重点事项", ["person", "content", "need_reply", "suggestion"])),
        ("table", None, "私聊待回复", [
            ("priority", "紧急程度", 12), ("person", "对话/群聊", 18), ("need_reply", "对方需要我回复什么", 34), ("suggested_reply", "建议回复", 36),
        ], _ensure_rows(personal.get("reply_needed"), "今天未发现明确私聊待回复项", ["priority", "person", "need_reply", "suggested_reply"])),
        ("table", "四、工作待办", "群聊待办建议优先处理", [
            ("priority", "优先级", 12), ("chat", "对话/群聊", 18), ("task", "明天要做什么", 34), ("reason", "原因", 36),
        ], _ensure_rows(tomorrow.get("work"), "今天未识别到群聊类明日优先事项", ["priority", "chat", "task", "reason"])),
        ("table", None, "私聊待办建议优先处理", [
            ("priority", "优先级", 12), ("person", "对话/群聊", 18), ("task", "明天要做什么", 34), ("reason", "原因", 36),
        ], _ensure_rows(tomorrow.get("personal"), "今天未识别到私聊类明日优先事项", ["priority", "person", "task", "reason"])),
        ("table", "五、上下文不完整说明", "群聊上下文不完整说明", [
            ("chat", "对话/群聊", 18), ("reviewed_scope", "已回看范围", 20), ("missing_info", "缺少的信息", 28), ("impact", "对判断的影响", 34),
        ], _ensure_rows(context_gaps.get("work"), "群聊今天没有发现明显上下文缺失的问题", ["chat", "reviewed_scope", "missing_info", "impact"])),
        ("table", None, "私聊上下文不完整说明", [
            ("person", "对话/群聊", 18), ("reviewed_scope", "已回看范围", 20), ("missing_info", "缺少的信息", 28), ("impact", "对判断的影响", 34),
        ], _ensure_rows(context_gaps.get("personal"), "私聊今天没有发现明显上下文缺失的问题", ["person", "reviewed_scope", "missing_info", "impact"])),
    ]

    margin_x = 28
    content_width = width - margin_x * 2
    y = 28
    y += _line_height(measure_draw, fonts["title"]) + 18
    y += _measure_multiline(measure_draw, content_width, report.get("disclaimer", ""), fonts["body"], spacing=8)
    y += 28

    for block in blocks:
        kind = block[0]
        section_title = block[1]
        subsection_title = block[2]
        if section_title:
            y += _line_height(measure_draw, fonts["section"]) + 22
        y += _line_height(measure_draw, fonts["subsection"]) + 16
        if kind == "list":
            y += _draw_numbered_list(None, margin_x + 12, y, content_width - 12, block[3], fonts["body"], FG)
            y += 28
        else:
            y += _draw_table(None, margin_x, y, content_width, block[3], block[4], fonts)
            y += 32

    total_height = y + 36
    image = Image.new("RGB", (width, total_height), BG)
    draw = ImageDraw.Draw(image)


    y = 28
    title_text = f"{report.get('title', '钉钉工作纪要日报')} | {report.get('date', '')}"
    draw.text((margin_x, y), title_text, font=fonts["title"], fill=FG)
    y += _line_height(draw, fonts["title"]) + 18
    y += _draw_multiline(draw, margin_x, y, content_width, report.get("disclaimer", ""), fonts["body"], MUTED, spacing=8)
    y += 28

    for block in blocks:
        kind = block[0]
        section_title = block[1]
        subsection_title = block[2]
        if section_title:
            draw.text((margin_x, y), section_title, font=fonts["section"], fill=FG)
            y += _line_height(draw, fonts["section"]) + 22
        draw.text((margin_x, y), subsection_title, font=fonts["subsection"], fill=FG)
        y += _line_height(draw, fonts["subsection"]) + 16
        if kind == "list":
            y += _draw_numbered_list(draw, margin_x + 12, y, content_width - 12, block[3], fonts["body"], FG)
            y += 28
        else:
            y += _draw_table(draw, margin_x, y, content_width, block[3], block[4], fonts)
            y += 32

    full_path = output_root / f"{base_name}.png"
    image.save(full_path)

    markdown_path = output_root / f"{base_name}.md"
    markdown_path.write_text(report_to_markdown_full(report), encoding="utf-8")

    return {
        "image_path": str(full_path),
        "markdown_path": str(markdown_path),
        "height": total_height,
        "width": width,
    }

# ── 向后兼容别名（旧 sidecar 调用 dds.render_report，新文件改名为 render_report_full）──
render_report = render_report_full
