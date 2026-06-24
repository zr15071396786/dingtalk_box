#!/usr/bin/env python3
"""
钉钉工作纪要日报 — 渲染 CLI
对应 wechat-cli daily-render 命令
用法: python dingtalk-daily-render.py report.json --output-dir ./output --base-name daily
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dingtalk_daily_summary import render_report
import argparse

def main():
    parser = argparse.ArgumentParser(description="把 report.json 渲染成 markdown 和浅色长图")
    parser.add_argument("report_json", help="report.json 路径")
    parser.add_argument("--output-dir", default=None, help="输出目录，默认 report.json 所在目录")
    parser.add_argument("--base-name", default=None, help="输出文件名前缀，默认钉钉工作纪要日报-{日期}")
    parser.add_argument("--width", default=1568, type=int, help="图片宽度")
    args = parser.parse_args()

    report_path = Path(args.report_json)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output_root = args.output_dir or str(report_path.parent)

    result = render_report(
        report,
        output_dir=output_root,
        base_name=args.base_name,
        width=args.width,
    )

    print()
    print(f"完整图片: {result['image_path']}")
    print(f"Markdown: {result['markdown_path']}")

if __name__ == "__main__":
    main()