"""main.py — ai_bridge CLI 入口

调用方式（一次性 subprocess，stdout 1 行 JSON）：
  ai_bridge.exe --bundle-path <bundle> --prompt-path <prompt>
                --output-dir <out>   --provider-id <id>
                [--base-url <u>] [--model <m>] [--api-key <k>]

stdout:
  成功：{"ok": true, "json_path": "...", "duration_ms": 1234}
  失败：{"ok": false, "err_code": "...", "err_msg": "...", ...}
退出码：0 成功；1 失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from ai_bridge import dpapi, providers, schema


def _data_dir() -> Path:
    """解析 %APPDATA%/DingTalkBox（开发模式可用 ENV 覆盖）"""
    if "DINGTALK_BOX_DATA_DIR" in os.environ:
        return Path(os.environ["DINGTALK_BOX_DATA_DIR"])
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "DingTalkBox"
    return Path.home() / ".config" / "DingTalkBox"


def _load_config() -> dict:
    cfg_path = _data_dir() / "config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"config.yaml 不存在: {cfg_path}")
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _load_api_key() -> str:
    secret = _data_dir() / "llm_secret.bin"
    return dpapi.unprotect_from_file(str(secret))


def _emit_err(err_code: str, err_msg: str, **extra: Any) -> None:
    payload = {"ok": False, "err_code": err_code, "err_msg": err_msg}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _dump_llm_response(content: str, tag: str) -> str:
    """把 LLM 完整响应 dump 到 %TEMP%/dingtalk_box/llm_dump_<tag>_<ts>.txt
    错误诊断用：raw 字段会截断到 500 字符，文件保留全文。
    """
    try:
        import tempfile
        from datetime import datetime
        d = Path(tempfile.gettempdir()) / "dingtalk_box"
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = d / f"llm_dump_{tag}_{ts}.txt"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except Exception as e:  # noqa: BLE001
        return f"<dump failed: {e}>"


def _emit_ok(json_path: str, duration_ms: int) -> None:
    print(
        json.dumps(
            {"ok": True, "json_path": json_path, "duration_ms": duration_ms},
            ensure_ascii=False,
        ),
        flush=True,
    )


def _build_messages(prompt: str, bundle_text: str, threshold: int = 30_000) -> list[dict]:
    if len(bundle_text) <= threshold:
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"请分析以下聊天记录并输出 report.json：\n\n{bundle_text}"},
        ]
    try:
        bundle_obj = json.loads(bundle_text)
        chats = bundle_obj.get("chats", [])
    except Exception:
        chats = []
    if not chats:
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"请分析以下聊天记录并输出 report.json：\n\n{bundle_text}"},
        ]
    msgs = [{"role": "system", "content": prompt}]
    n = len(chats)
    for i, chat in enumerate(chats):
        msgs.append({
            "role": "user",
            "content": f"## 第 {i+1}/{n} 段\n\n{json.dumps(chat, ensure_ascii=False)}",
        })
    msgs.append({"role": "user", "content": f"请综合以上 {n} 段，输出完整 report.json（严格遵循 schema）。"})
    return msgs


def _find_first_json_object(text: str) -> str | None:
    """找到第一个**配对完整**的 { ... } 对象（处理字符串内的引号/转义）"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair_unescaped_quotes_in_strings(text: str) -> str:
    """LLM 经常在 string value 里放裸 " 字符（比如"全群点赞"分享的图片"），
    破坏 JSON 解析。这个 state machine 找到 string 边界，把内部非转义 " 都转义。

    启发式：碰到 " 时，向后看跳过空白——下一个非空白字符若是 , } ] : 之一，认为是
    真正的 string 结束；否则视为 value 内的裸 "，转义为 \"。
    """
    chars = list(text)
    n = len(chars)
    out = []
    i = 0
    in_string = False
    while i < n:
        ch = chars[i]
        if ch == "\\" and i + 1 < n and in_string:
            out.append(ch)
            out.append(chars[i + 1])
            i += 2
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
                i += 1
                continue
            # in_string=True：判断是否真结束
            j = i + 1
            while j < n and chars[j] in " \t\r\n":
                j += 1
            if j >= n or chars[j] in ",}]]:":
                # 真结束
                in_string = False
                out.append(ch)
                i += 1
            else:
                # 裸 "，转义
                out.append('\\"')
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_ai_content(content: str) -> dict:
    """从 LLM 响应里抠出 report.json。

    兼容以下几种格式（按顺序尝试）：
    1. 纯 JSON（最理想）
    2. ```json ... ``` / ``` ... ``` 代码块包裹的 JSON
    3. 推理模型（DeepSeek-R1 / MiniMax-M3 / Qwen-QwQ）输出
       <think>...</think> 块后再接 JSON
    4. JSON 后有解释/杂讯 → 截到第一个完整 {...} 段
    5. 字符串值里有未转义 "（LLM 常见 bug，state-machine 修复）
    """
    text = content.strip()
    # 1. 剥掉推理模型的 <think>...</think> 块（DeepSeek-R1 / MiniMax-M3 等）
    #    兼容 <think> (MiniMax/Qwen) 和 <think> (DeepSeek) 两种 tag
    #    保底：如果 <think> 块没闭合（LLM 思考过长被截），剥掉空字符串会让后续解析失败
    #    —— 此时不剥，保留全文让 JSON 提取器照常工作
    stripped = re.sub(r"<think\b.*?</think>", "", text, flags=re.DOTALL).strip()
    if stripped:
        text = stripped
    # 2. 找 第一个**配对完整**的 {...}（容错：JSON 外面包了 ```json ... ``` 代码块，
    #    或者 JSON 后还跟了 LLM 解释/杂讯）。
    #    v0.3 修复：之前用 r"```(?:json)?\s*(\{.*?\})\s*```" 提取 ```json``` 块，
    #    但 .*? 非贪婪会截到嵌套对象的第一个 }（比如 "overview": { ... }）就停，
    #    导致 json.loads 看到顶层 { 后到第一个 } 就结束，期望 ',' 却得到 EOF。
    #    改用 _find_first_json_object 找平衡花括号，code fence 内外都能正确处理。
    first = _find_first_json_object(text)
    if first:
        text = first
    # 3. 尝试解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 4. 修复字符串值里的未转义 "（LLM 常见 bug）
    repaired = _repair_unescaped_quotes_in_strings(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # 5. 去掉尾逗号再试
    cleaned = re.sub(r",\s*([}\]])", r"\1", repaired)
    return json.loads(cleaned)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai_bridge")
    parser.add_argument("--bundle-path", required=True)
    parser.add_argument("--prompt-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None, help="兼容旧版；新代码应走 DINGTALK_API_KEY 环境变量")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    start = time.time()
    try:
        prompt = Path(args.prompt_path).read_text(encoding="utf-8")
        bundle_text = Path(args.bundle_path).read_text(encoding="utf-8")

        if args.base_url and args.model:
            base_url = args.base_url.rstrip("/")
            model = args.model
        else:
            preset = providers.get_by_id(args.provider_id)
            base_url = (args.base_url or preset["base_url"]).rstrip("/")
            # 模型 ID：优先用 args.model，否则取 preset.models[0].id
            if args.model:
                model = args.model
            else:
                models = preset.get("models") or []
                if not models:
                    raise ValueError(f"provider {args.provider_id!r} 没有预设模型")
                model = models[0]["id"]

        # api_key 优先级：环境变量 DINGTALK_API_KEY > --api-key 命令行 > DPAPI 落盘文件
        # 走环境变量避免在进程列表/事件日志里泄露 key
        api_key = os.environ.get("DINGTALK_API_KEY") or args.api_key
        if api_key:
            pass  # 显式提供 → 用
        else:
            try:
                api_key = _load_api_key()
            except FileNotFoundError as e:
                _emit_err("DPAPI_FAIL", f"密钥文件不存在: {e}")
                return 1
            except Exception as e:
                _emit_err("DPAPI_FAIL", f"DPAPI 解密失败: {e}")
                return 1

        messages = _build_messages(prompt, bundle_text)
        body = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            # 推理模型（DeepSeek-R1 / MiniMax-M3 / Qwen-QwQ）需要大量 token 思考
            # 之前 4096 完全不够 —— LLM 想完 5300+ token 后 JSON 一个字没输出就被截
            "max_tokens": 16384,
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{base_url}/chat/completions"

        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=args.timeout)
        except httpx.TimeoutException as e:
            _emit_err("TIMEOUT", f"{args.timeout}s timeout: {e}")
            return 1
        except httpx.HTTPError as e:
            _emit_err("HTTP_5XX", f"网络错误: {e}")
            return 1

        if resp.status_code in (401, 403):
            _emit_err("HTTP_401", f"Unauthorized (status={resp.status_code})")
            return 1
        if resp.status_code == 429:
            _emit_err("HTTP_429", "rate limited")
            return 1
        if resp.status_code >= 500:
            _emit_err("HTTP_5XX", f"AI 服务异常 (status={resp.status_code}): {resp.text[:200]}")
            return 1
        if resp.status_code != 200:
            _emit_err("HTTP_5XX", f"unexpected status {resp.status_code}: {resp.text[:200]}")
            return 1

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            _emit_err("PARSE_FAIL", f"无法解析 LLM 响应: {e}")
            return 1

        # 空 content 单独识别（MiniMax/DeepSeek/Qwen 等偶发返空，区别于真解析错）
        if not content or not content.strip():
            finish_reason = data.get("choices", [{}])[0].get("finish_reason", "?")
            _emit_err(
                "LLM_EMPTY",
                f"AI 返回空内容（finish_reason={finish_reason}）",
                raw=repr(content)[:300],
            )
            return 1

        # 检查 finish_reason — 如果是 "length" 说明被 max_tokens 截断
        # （用户最常见的坑：推理模型想太久了，max_tokens 不够）
        finish_reason = data.get("choices", [{}])[0].get("finish_reason", "?")
        truncated = (finish_reason == "length")
        content_len = len(content)

        try:
            report_obj = _parse_ai_content(content)
        except Exception as e:
            # PARSE_FAIL 时把完整 LLM 响应 dump 到磁盘，
            # 错误信息附带文件路径，方便用户直接看完整内容
            dump_path = _dump_llm_response(content, "parse_fail")
            extra_hint = ""
            if truncated:
                extra_hint = (
                    f"⚠️ LLM 输出被 max_tokens 截断（finish_reason=length, "
                    f"content={content_len} 字符）。原因：推理模型思考过长。"
                    f"已在请求中把 max_tokens 设为 16384，重试即可。"
                )
            _emit_err(
                "PARSE_FAIL",
                f"AI 返回非 JSON: {e} | finish_reason={finish_reason}, "
                f"content={content_len}字符{extra_hint} | 完整响应已写入: {dump_path}",
                raw=content[:2000],
                dump_path=dump_path,
                finish_reason=finish_reason,
                truncated=truncated,
            )
            return 1

        is_valid, errors = schema.validate_report(report_obj)
        if not is_valid:
            missing = [e.split("缺少字段:")[-1].strip() for e in errors if "缺少字段" in e]
            _emit_err("SCHEMA_FAIL", "; ".join(errors), missing=missing)
            return 1

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "report.json"
        json_path.write_text(
            json.dumps(report_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        duration_ms = int((time.time() - start) * 1000)
        _emit_ok(str(json_path), duration_ms)
        return 0

    except FileNotFoundError as e:
        _emit_err("DPAPI_FAIL", f"文件不存在: {e}")
        return 1
    except Exception as e:
        _emit_err("CRASH", f"未捕获异常: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run())
