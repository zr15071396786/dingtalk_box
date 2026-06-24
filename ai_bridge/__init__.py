"""ai_bridge — 独立 LLM 调用进程

唯一职责：从 stdin/argv 接收任务，调用大模型，写 report.json，stdout 输出结果。
不持有任何业务状态（除单次调用的 LLM key 在内存中）。
"""
