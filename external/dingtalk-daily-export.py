#!/usr/bin/env python3
"""
钉钉工作纪要日报 — 导出 CLI
对应 wechat-cli daily-export 命令
用法: python dingtalk-daily-export.py --date 2026-06-08 --output-dir ./dingtalk_daily
"""
import sys
import os
from pathlib import Path

# 把核心库加入路径（支持 hyphens 命名的模块文件）
sys.path.insert(0, str(Path(__file__).parent))

from dingtalk_daily_summary import export_daily_bundle
from datetime import datetime
import argparse

def main():
    parser = argparse.ArgumentParser(description="导出某一天全部活跃钉钉会话的聊天 bundle")
    parser.add_argument("--date", default=None, help="导出日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--output-dir", default="dingtalk_daily", help="输出根目录")
    parser.add_argument("--message-limit-per-chat", default=0, type=int, help="每个会话最多导出多少条消息，0 表示不限")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    target_dir = str(Path(args.output_dir) / date_str)

    result = export_daily_bundle(
        date_str=date_str,
        output_dir=target_dir,
        message_limit_per_chat=args.message_limit_per_chat,
    )

    print()
    print(f"日期: {result['date']}")
    print(f"输出目录: {result['output_dir']}")
    print(f"会话数: {result['chat_count']}")
    print(f"消息数: {result['message_count']}")
    print(f"bundle: {result['bundle_path']}")
    print(f"prompt: {result['prompt_path']}")
    print(f"report 模板: {result['report_template_path']}")

if __name__ == "__main__":
    main()