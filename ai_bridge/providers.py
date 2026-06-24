"""providers.py — 读 bundled providers.yaml，提供 preset 查找

Public API:
  load_providers() -> list[dict]
  get_by_id(pid: str) -> dict
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _bundled_providers_yaml() -> Path:
    """解析 providers.yaml 路径（frozen / 开发双模式）"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", ""))
    else:
        # ai_bridge/providers.py → 项目根
        base = Path(__file__).resolve().parent.parent
    return base / "assets" / "providers.yaml"


@lru_cache(maxsize=1)
def load_providers() -> list[dict[str, Any]]:
    """读 bundled providers.yaml，返回 list of dict"""
    p = _bundled_providers_yaml()
    if not p.is_file():
        raise FileNotFoundError(f"providers.yaml 不存在: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    plist = data.get("providers", [])
    if not isinstance(plist, list):
        raise ValueError("providers.yaml 格式错：providers 字段不是 list")
    return plist


def get_by_id(pid: str) -> dict[str, Any]:
    """按 id 查 provider；未知抛 ValueError"""
    for p in load_providers():
        if p.get("id") == pid:
            return p
    raise ValueError(f"未知 provider id: {pid!r}")
