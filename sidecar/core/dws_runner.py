"""dws_runner.py — dws.exe 子进程封装

负责：
- 调 dws CLI（subprocess）
- 统一超时 / 退出码 / stderr 处理
- 抛出自定义异常映射到 JSON-RPC error code
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import logging_setup, paths


# ── 异常 ───────────────────────────────────────────────────────────────
class DwsError(Exception):
    code = -32004
    def __init__(self, message: str, returncode: int = -1, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.data = {"returncode": returncode, "stderr": (stderr or "")[:500]}


class DwsTimeout(DwsError):
    code = -32003
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message, returncode=-1, stderr=stderr)


# ── 核心调用 ───────────────────────────────────────────────────────────
def _creationflags() -> int:
    if sys.platform.startswith("win"):
        return subprocess.CREATE_NO_WINDOW
    return 0


def run(
    args: Iterable[str],
    *,
    timeout: int = 120,
    check: bool = True,
    input_data: Optional[str] = None,
    lenient_output: bool = False,
) -> dict[str, Any]:
    """调 dws <args...> --format json，返回解析后的 dict

    出错时：
    - 超时 → DwsTimeout
    - 非零退出 → DwsError（error.data.returncode + error.data.stderr）
    - JSON 解析失败 → 默认 raise；lenient_output=True 时返回 {"_raw_output": text}

    lenient_output 用于 fire-and-forget 命令（如 auth logout），dws 实际可能返回
    普通文本而非 JSON；只要 returncode=0 就当作成功。
    """
    logger = logging_setup.setup("dws")
    dws = paths.dws_exe_path()
    cmd = [dws, *args, "--format", "json"]
    start = time.time()
    logger.info("dws call", extra={"cmd": cmd[:6] + (["...truncated"] if len(cmd) > 6 else []), "timeout": timeout})
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_creationflags(),
            input=input_data,
            stdin=subprocess.DEVNULL,  # dws 不要从 sidecar 的 stdin 读
        )
    except subprocess.TimeoutExpired as e:
        elapsed = int((time.time() - start) * 1000)
        logger.warning("dws timeout", extra={"elapsed_ms": elapsed, "cmd": list(args)[:3]})
        raise DwsTimeout(f"dws 超时（{timeout}s）", stderr=str(e)) from e
    except FileNotFoundError as e:
        logger.error("dws not found", extra={"path": dws})
        raise DwsError(f"未找到 dws：{dws}", returncode=127, stderr=str(e)) from e

    elapsed = int((time.time() - start) * 1000)
    logger.info("dws done", extra={
        "elapsed_ms": elapsed,
        "returncode": proc.returncode,
        "stdout_bytes": len(proc.stdout or ""),
    })

    if check and proc.returncode != 0:
        raise DwsError(
            f"dws 退出 {proc.returncode}：{(proc.stderr or '').strip()[:200]}",
            returncode=proc.returncode,
            stderr=proc.stderr or "",
        )

    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": True, "_empty": True, "returncode": proc.returncode}
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        if lenient_output and proc.returncode == 0:
            # fire-and-forget 命令：returncode=0 就算成功，原始文本带回给调用方
            return {"_raw_output": out, "returncode": 0, "_lenient": True}
        raise DwsError(f"dws 输出非 JSON：{e}", returncode=proc.returncode, stderr=out[:500]) from e
