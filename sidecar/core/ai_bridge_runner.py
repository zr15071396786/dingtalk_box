"""sidecar/core/ai_bridge_runner.py — 调 ai_bridge 子进程的统一入口

所有需要调 LLM 的地方（生成日报、测试连接）都走这里：
- Popen ai_bridge.exe（frozen） 或 `python -m ai_bridge`（dev）
- 读 stdout 1 行 JSON，返回 (returncode, parsed_payload)
- HTTP_429 自动指数退避重试（默认 3 次：1 + 2 retry）

为什么必须走子进程：
- 进程内 `from ai_bridge import main` 会让 ai_bridge 的 stdout 输出
  （{"ok": false, "err_code": ...}）污染 sidecar 自身的 JSON-RPC stdout 帧，
  还会让 ai_bridge 的 print() 异常被 sidecar 误吃。
- 子进程隔离 + capture_output 是唯一干净方案。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def run_ai_bridge(
    bundle_path: str,
    prompt_path: str,
    output_dir: str,
    provider_id: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: int = 130,
    retries: int = 1,
) -> tuple[int, dict]:
    """Popen 调一次 ai_bridge 子进程，HTTP_429 自动重试。

    Args:
        retries: 总尝试次数（1 = 不重试；3 = 1 + 2 retry）。429 之外的错误不重试。
    Returns:
        (returncode, parsed_payload_dict) — payload 来自 ai_bridge stdout 那 1 行 JSON。
        若 stdout 解析失败，payload 是 {"ok": False, "err_code": "CRASH", "err_msg": "..."}。
    """
    cmd = [
        "--bundle-path", bundle_path,
        "--prompt-path", prompt_path,
        "--output-dir", output_dir,
        "--provider-id", provider_id,
    ]
    if model:
        cmd += ["--model", model]
    if base_url:
        cmd += ["--base-url", base_url]
    cmd += ["--timeout", str(timeout)]

    # 安全：api_key 走环境变量 DINGTALK_API_KEY（不进命令行 → 进程列表不暴露）
    env_overrides: dict[str, str] = {}
    if api_key:
        env_overrides["DINGTALK_API_KEY"] = api_key

    # 找 ai_bridge.exe / ai_bridge module
    if getattr(sys, "frozen", False):
        bridge_exe = Path(sys.executable).parent / "ai_bridge.exe"
        if not bridge_exe.exists():
            return 1, {"ok": False, "err_code": "CRASH", "err_msg": f"ai_bridge.exe 不存在: {bridge_exe}"}
        run_args: list[str] = [str(bridge_exe), *cmd]
    else:
        # 开发模式：ai_bridge 是 package，必须跑 main 子模块
        run_args = [sys.executable, "-m", "ai_bridge.main", *cmd]

    last_rc = 1
    last_payload: dict[str, Any] = {"ok": False, "err_code": "CRASH", "err_msg": "未执行"}
    # 合并环境变量：默认 + 覆盖（DINGTALK_API_KEY 等敏感字段走这里）
    proc_env = {**os.environ, **env_overrides} if env_overrides else None
    for attempt in range(1, max(1, retries) + 1):
        try:
            proc = subprocess.run(
                run_args,
                capture_output=True, text=True, timeout=timeout + 10,
                env=proc_env,
            )
        except subprocess.TimeoutExpired:
            last_rc = 1
            last_payload = {
                "ok": False, "err_code": "TIMEOUT",
                "err_msg": f"ai_bridge 子进程 {timeout + 10}s 未返回",
            }
        except Exception as e:  # noqa: BLE001
            last_rc = 1
            last_payload = {"ok": False, "err_code": "CRASH", "err_msg": f"启动 ai_bridge 失败: {e}"}
        else:
            last_rc = proc.returncode
            stdout = (proc.stdout or "").strip()
            if stdout:
                try:
                    last_payload = json.loads(stdout.splitlines()[-1])
                except json.JSONDecodeError:
                    last_payload = {
                        "ok": False, "err_code": "CRASH",
                        "err_msg": f"ai_bridge stdout 非 JSON: {stdout[:200]}",
                    }
            else:
                last_payload = {
                    "ok": False, "err_code": "CRASH",
                    "err_msg": f"ai_bridge 无 stdout 输出 (stderr: {(proc.stderr or '')[:200]})",
                }

        # 只对 429 / LLM_EMPTY 重试（瞬时问题）；401/5xx/parse fail 不重试
        retryable = {"HTTP_429", "LLM_EMPTY"}
        if last_payload.get("err_code") not in retryable:
            break
        if attempt < max(1, retries):
            wait = 2 ** attempt  # 2s, 4s
            time.sleep(wait)

    return last_rc, last_payload
