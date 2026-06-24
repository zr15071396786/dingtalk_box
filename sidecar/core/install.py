"""install.py — dws.exe 一键安装

场景：把工具分发给同 corp 同事，他们的机器上没有 dws。
本模块负责：
- check_install()：报告 dws 当前状态（装了 / 没装但内嵌可装 / 都没有）
- install_dws()：把 frozen _MEIPASS/bin/dws.exe copy 到 %APPDATA%/DingTalkBox/bin/dws.exe

为什么必须 copy 到 %APPDATA%（不是直接用 _MEIPASS 里的）：
1. frozen PyInstaller 的 _MEIPASS 在打包时已固化到 exe 资源里，运行时只读
2. 用户想升级 dws 时只换 %APPDATA% 下那个文件
3. 工具重装/升级不影响已装 dws
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import logging_setup, paths

LOG = logging_setup.setup("install")


# ── dws 解析辅助 ───────────────────────────────────────────────────────
def _bundled_dws_path() -> Path | None:
    """frozen _MEIPASS/bin/dws.exe 或 开发模式 项目根/bin/dws.exe"""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        p = meipass / "bin" / "dws.exe"
        if p.is_file():
            return p
        # _MEIPASS/bin/ 不在 → 试 exe 旁边
        alt = Path(sys.executable).parent / "ai_bridge.exe"  # sanity check
        return None
    # 开发模式：项目根/bin/dws.exe
    root = Path(__file__).resolve().parent.parent.parent
    p = root / "bin" / "dws.exe"
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


# ── 1. check_install ──────────────────────────────────────────────────
def check_install(_params: dict) -> dict:
    """报告 dws 当前状态"""
    resolved = paths.dws_exe_path()
    bundled = _bundled_dws_path()

    installed = False
    path: str | None = None
    version: str | None = None
    try:
        # shutil.which / 显式路径都可能通过；尝试 `--version`
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

    return {
        "installed": installed,
        "path": path,
        "version": version,
        "installable": installable,
        "bundled_path": str(bundled) if bundled else None,
        "bundled_size": bundled_size,
        "target_path": str(target),
    }


# ── 2. install_dws ────────────────────────────────────────────────────
def install_dws(_params: dict) -> dict:
    """把内嵌 dws 装到 %APPDATA%/DingTalkBox/bin/dws.exe"""
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
            # 试着写一小段验证可写
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
        # 清理半截 tmp（如果有）
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

    # rename 覆盖（Windows 上 Path.replace 原子）
    try:
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001
        # replace 失败时 tmp 还在 → 清理
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

    # 校验：跑一次 --version
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
