"""sidecar.core — 钉钉工具箱 Python sidecar 业务核心包

模块：
- dispatcher:  JSON-RPC method router
- dws_runner:  dws.exe 子进程封装
- auth:        登录态 + corp 校验
- daily_report: 日报 bundle 导出、渲染、历史
- send:        发送文件到钉钉
- paths:       路径解析与沙箱
- config:      config.yaml 读写
- logging_setup: JSON 行日志
"""
