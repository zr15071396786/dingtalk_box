"""logging_setup.py — JSON 行日志

日志路径：data_dir()/logs/sidecar-YYYY-MM-DD.log
格式：{"ts":..., "level":..., "source":..., "msg":..., ...}
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from . import config, paths

_INITIALIZED = False


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "source": record.name,
            "msg": record.getMessage(),
        }
        # 任何额外字段
        for k, v in record.__dict__.items():
            if k in ("args", "asctime", "created", "exc_info", "exc_text", "filename",
                     "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                     "message", "msg", "name", "pathname", "process", "processName",
                     "relativeCreated", "stack_info", "thread", "threadName", "taskName"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except TypeError:
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup(component: str = "sidecar") -> logging.Logger:
    """初始化日志，返回 root logger

    行为：
    - 设置 TimedRotatingFileHandler，每天一个文件
    - 保留天数从 config.logging.max_files 读
    - 也输出到 stderr（开发模式便于调试）
    """
    global _INITIALIZED
    logger = logging.getLogger(component)
    if _INITIALIZED:
        return logger

    log_level = str(config.get("logging.level", "INFO")).upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    fmt = JsonLineFormatter()

    # 文件
    log_file = paths.logs_dir() / f"{component}-{datetime.now().strftime('%Y-%m-%d')}.log"
    # Opt 4 修复：backupCount 读 config（之前写死 7，config 字段形同虚设）
    backup_count = int(config.get("logging.max_files", 7))
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # stderr
    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    logger.propagate = False
    _INITIALIZED = True
    return logger
