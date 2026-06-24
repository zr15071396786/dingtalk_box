"""version.py — 单一版本号真相源

v0.3.14 起建立：之前版本号散落在 6+ 处（src/index.html 硬编码、launcher.py、
sidecar/core/dispatcher.py、sidecar/core/send.py、README、git tag），
每次发版要手动改 6 处，漏一处就脱节（用户报：UI 右下角显示 v0.3.10 但
实际发版是 v0.3.13）。

现在：
- launcher.py / sidecar 启动时 `from version import __version__`
- src/index.html footer 占位 `<span id="version-tag">v?</span>`，
  launcher 启动时 evaluate_js 写入 `<ver> · build <mtime>`
- sidecar ping 返回 `"version": __version__`（不再硬编码 "0.1.0"）
- sidecar send RPC 也读这个常量

**每次发版只需要改这一行。**
"""
from __future__ import annotations

__version__ = "0.3.15"