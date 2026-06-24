"""config.py — config.yaml 读写

v0.3.9：去掉 assets/default_config.yaml 依赖
- 所有默认值 hardcode 到 DEFAULT_CONFIG 常量（替代原 yaml 种子）
- _UNLOCKED_CORP 段每次启动强制覆盖到 user config（清理老用户的写死 corpId）
- 首次启动：直接把 DEFAULT_CONFIG 写入 %APPDATA%/DingTalkBox/config.yaml
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from . import paths


_LOG = logging.getLogger("config")


# ── 默认 config（v0.3.9 hardcode，替代原 assets/default_config.yaml） ──────
# 任何字段调整直接改这里 → 第一次启动的用户会拿到新默认值；
# 已有用户：除 corp 段外其他段保留用户的本地设置，corp 段会被 _UNLOCKED_CORP 强制重置。
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "corp": {
        "expected_corp_id": "",
        "expected_corp_name": "",
        "strict": False,
    },
    "ui": {
        "theme": "light",
        "start_minimized": False,
        "close_to_tray": False,
    },
    "daily_report": {
        "default_send_target": {"type": "self"},  # "self" | "user_id" | "open_dingtalk_id"
        "include_sections": ["overview", "work", "personal", "tomorrow"],
        "exclude_chats": [],
    },
    "update": {
        "check_url": "https://it.company.com/dingtalk_box/latest.json",
        "check_on_start": True,
        "auto_download": False,
    },
    "logging": {
        "level": "INFO",
        "max_files": 7,
    },
    "llm": {
        "active_provider": None,
        "model": None,
    },
}


# v0.3.9：corp 段每次启动都强制等于这个值 —— 清理老用户 config 里写死的 corpId。
# 想要重新"限 corp"：改这里 + 改 auth.py 的 strict 校验逻辑（不在本文件范围内）。
_UNLOCKED_CORP: dict[str, Any] = {
    "expected_corp_id": "",
    "expected_corp_name": "",
    "strict": False,
}


def ensure_config() -> Path:
    """确保 user config 存在；首次启动直接写一份 DEFAULT_CONFIG 到 data_dir()。

    v0.3.12 修复：之前此函数调 save()，save() 又调 ensure_config()，
    在 config.yaml 不存在时（首次启动）死循环 → RecursionError。
    现在 ensure_config 自己直接写文件，不依赖 save。

    同步用 os.makedirs 替代 Path.mkdir，绕开 Python 3.14 frozen 模式下
    pathlib.WindowsPath 懒加载 _str / _drv 属性失败的 bug（同事 launcher.log
    中看到 AttributeError + RecursionError 互相放大）。
    """
    cfg = paths.config_path()
    if not cfg.exists():
        _write_yaml(cfg, DEFAULT_CONFIG)
    return cfg


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """直接写 yaml 文件，不依赖 ensure_config（避免循环）。"""
    import os
    try:
        os.makedirs(str(path.parent), exist_ok=True)
    except OSError:
        pass
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _sync_corp_to_unlocked(data: dict) -> bool:
    """v0.3.9：每次启动把 user config 的 corp 段强制重置为「不限 corp」。

    只覆盖 corp 段，不动其他段（ui / llm / daily_report 等保留用户的设置）。
    返回是否做了修改。
    """
    if data.get("corp") == _UNLOCKED_CORP:
        return False
    _LOG.info("sync_corp: resetting user corp %s -> %s", data.get("corp"), _UNLOCKED_CORP)
    data["corp"] = dict(_UNLOCKED_CORP)
    return True


def load() -> dict[str, Any]:
    cfg_path = ensure_config()
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    # 老 config 缺 llm 段 → 自动 merge
    if "llm" not in data:
        data["llm"] = {"active_provider": None, "model": None}
        save(data)
    # v0.3.9：corp 段每次启动自动重置为「不限 corp」
    if _sync_corp_to_unlocked(data):
        save(data)
    return data


def save(data: dict[str, Any]) -> None:
    """写 config.yaml。v0.3.12 修复：之前调 ensure_config() 形成循环，
    现在直接调 paths.config_path()（save 是在已有 config 之后的写操作，
    不需要触发首次启动种子逻辑）。"""
    cfg_path = paths.config_path()
    _write_yaml(cfg_path, data)


def get(key: str, default: Any = None) -> Any:
    data = load()
    if "." not in key:
        return data.get(key, default)
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
