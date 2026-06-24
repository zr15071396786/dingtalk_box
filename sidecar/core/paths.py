"""paths.py — 路径解析与沙箱保护

约定：
- 用户数据根目录：%APPDATA%/DingTalkBox/  （Windows） / ~/.config/DingTalkBox/ （非 Windows）
- 子目录：config.yaml、output/、logs/、update/、cache/
- 任何 file_path 参数（来自前端）必须 resolve 后位于 output/ 之下，否则 -32006
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "DingTalkBox"


def data_dir() -> Path:
    """跨平台用户数据根目录

    v0.3.12 修复：用 os.makedirs 替代 root.mkdir(parents=True, exist_ok=True)。
    原因：Python 3.14 + PyInstaller frozen 模式下，pathlib.WindowsPath 的
    str() / drive 内部会懒加载 _str / _drv 属性；这个懒加载在 frozen
    进程的某些状态下会失败，抛 AttributeError: 'WindowsPath' object has
    no attribute '_str'。在同事机器上（v0.3.10/v0.3.11 首次启动），
    反复 mkdir 失败 + config.save 死循环，最终触发 RecursionError 进程崩溃。

    os.makedirs 走 win32 API，不触发 pathlib 内部属性，绕开 bug。
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        root = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP_NAME
    try:
        os.makedirs(str(root), exist_ok=True)
    except OSError:
        # 如果目录已存在等无害错误，pass；致命权限错误留到后续调用
        pass
    return root


def config_path() -> Path:
    return data_dir() / "config.yaml"


def output_dir() -> Path:
    p = data_dir() / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def update_dir() -> Path:
    p = data_dir() / "update"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def output_for_date(date: str) -> Path:
    p = output_dir() / date
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_under_output(p: Path) -> bool:
    """检查路径是否在 output/ 沙箱内（用于路径遍历保护）"""
    try:
        p = p.resolve()
        root = output_dir().resolve()
        return root == p or root in p.parents
    except Exception:
        return False


def dws_exe_path() -> str:
    """dws.exe 解析顺序（用户装的优先，方便后续升级）：
    1. PATH 上的 dws
    2. %APPDATA%/DingTalkBox/bin/dws.exe （install.py 装的位置）
    3. ~/.local/bin/dws.exe
    4. frozen _MEIPASS/bin/dws.exe / 项目内 bin/dws.exe （开发模式）
    """
    import shutil
    found = shutil.which("dws")
    if found:
        return found
    user = user_dws_path()
    if user.exists():
        return str(user)
    local = Path.home() / ".local" / "bin" / ("dws.exe" if sys.platform.startswith("win") else "dws")
    if local.exists():
        return str(local)
    bundled = Path(__file__).resolve().parent.parent.parent / "bin" / ("dws.exe" if sys.platform.startswith("win") else "dws")
    if bundled.exists():
        return str(bundled)
    return shutil.which("dws") or "dws"


def user_dws_path() -> Path:
    """dws 的一键安装目标位置（%APPDATA%/DingTalkBox/bin/dws.exe）"""
    name = "dws.exe" if sys.platform.startswith("win") else "dws"
    return data_dir() / "bin" / name


def augment_path_with_dws() -> None:
    """v0.3.3 修复：把 dws 所在目录 append 到 os.environ['PATH']。

    为什么需要：
    dds（external/dingtalk_daily_summary.py）调 dws 时用的是裸命令名
    `subprocess.run(["dws", ...])`，依赖 `dws` 在系统 PATH 上能解析。
    sidecar 自己的 dws_runner 走 dws_exe_path() 多路径查找没问题，但
    dds 绕过了 dws_runner 直接 spawn。
    sidecar 启动时一次性把 dws 目录加到 PATH，dds 就能找到 dws 了。

    行为：
    - dws 解析优先顺序不变（dws_exe_path 仍然走 4 步查找，PATH 只是其中一步）
    - 所有子进程继承 sidecar 的环境变量 → 裸 "dws" 也能解析
    - 不重复添加（已在 PATH 中则跳过）
    - frozen / dev 模式都生效
    """
    dws = dws_exe_path()
    if not dws or not Path(dws).is_file():
        return  # dws 还没装（首次启动 + 没点一键安装），先不动 PATH
    dws_dir = str(Path(dws).resolve().parent)
    cur = os.environ.get("PATH", "")
    # Windows PATH 用 ; 分隔；POSIX 用 :
    sep = ";" if sys.platform.startswith("win") else ":"
    parts = [p for p in cur.split(sep) if p]
    if dws_dir in parts:
        return  # 已加过（避免重复膨胀）
    parts.append(dws_dir)
    os.environ["PATH"] = sep.join(parts)
