"""sidecar/core/errors.py — 自定义异常体系"""
from __future__ import annotations


class AiBridgeError(Exception):
    """ai-bridge 子进程错误，code 属性对应 JSON-RPC code"""

    def __init__(self, err_code: str, err_msg: str, data: dict | None = None):
        super().__init__(f"[{err_code}] {err_msg}")
        self.err_code = err_code
        self.err_msg = err_msg
        self.data = data or {}


# ai-bridge err_code → JSON-RPC code 映射
AI_ERR_TO_CODE = {
    "DPAPI_FAIL": -32009,
    "TIMEOUT": -32010,
    "HTTP_401": -32011,
    "HTTP_429": -32012,
    "HTTP_5XX": -32013,
    "PARSE_FAIL": -32014,
    "SCHEMA_FAIL": -32015,
    "OLLAMA_DOWN": -32016,  # 保留位
    "LLM_EMPTY": -32017,    # LLM 返了空 content（瞬时问题，可重试）
    "CRASH": -32099,
}
