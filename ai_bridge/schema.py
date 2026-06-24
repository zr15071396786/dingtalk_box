"""schema.py — report.json 结构校验

Public API:
  validate_report(obj) -> tuple[bool, list[str]]
"""
from __future__ import annotations

from typing import Any

TOP_KEYS = ["date", "title", "disclaimer", "overview", "work", "personal", "tomorrow", "context_gaps"]
BUCKET_SUBKEYS = ["work", "personal"]
WORK_SUBKEYS = ["handled_items", "key_decisions", "projects", "reply_needed", "risks"]
PERSONAL_SUBKEYS = ["highlights", "reply_needed"]


def _check_dict_keys(obj: Any, keys: list[str], path: str, errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{path} 必须是 dict，实际是 {type(obj).__name__}")
        return False
    missing = [k for k in keys if k not in obj]
    if missing:
        errors.append(f"{path} 缺少字段: {', '.join(missing)}")
        return False
    return True


def _check_list_field(obj: Any, path: str, errors: list[str]) -> bool:
    if not isinstance(obj, list):
        errors.append(f"{path} 必须是 list，实际是 {type(obj).__name__}")
        return False
    return True


def validate_report(obj: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, [f"report.json 顶层必须是 dict，实际是 {type(obj).__name__}"]

    if not _check_dict_keys(obj, TOP_KEYS, "root", errors):
        return False, errors

    for top in ["overview", "tomorrow", "context_gaps"]:
        if not _check_dict_keys(obj[top], BUCKET_SUBKEYS, top, errors):
            continue
        for sub in BUCKET_SUBKEYS:
            _check_list_field(obj[top][sub], f"{top}.{sub}", errors)

    if _check_dict_keys(obj["work"], WORK_SUBKEYS, "work", errors):
        for sub in WORK_SUBKEYS:
            _check_list_field(obj["work"][sub], f"work.{sub}", errors)

    if _check_dict_keys(obj["personal"], PERSONAL_SUBKEYS, "personal", errors):
        for sub in PERSONAL_SUBKEYS:
            _check_list_field(obj["personal"][sub], f"personal.{sub}", errors)

    return (len(errors) == 0), errors
