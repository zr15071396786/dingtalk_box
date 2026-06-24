"""开发模式 dev 脚本

跳过 PyInstaller，直接用 Python 跑 launcher.py + 监控 src/ 改动自动 reload。

用法:
    python dev.py

行为:
- 启动 sidecar 子进程
- 创建 webview 窗口加载 src/index.html
- 后台 watchdog 监控 src/ 改动 → 调 window.evaluate_js("location.reload()")
- Ctrl+C 干净退出（关 webview + sidecar）

依赖（与 launcher.py 相同）:
    pip install pywebview pillow pyyaml
"""
import sys
import os
import time
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 把项目根加进 PYTHONPATH —— 这样 sidecar 子进程启动后能 import
# ai_bridge（PyInstaller 打包时这个 import 是隐式可达的，裸跑没有）
_sep = ";" if sys.platform == "win32" else ":"
_existing = os.environ.get("PYTHONPATH", "")
if _existing:
    os.environ["PYTHONPATH"] = f"{ROOT}{_sep}{_existing}"
else:
    os.environ["PYTHONPATH"] = str(ROOT)

# 复用 launcher 的入口逻辑，但不通过 PyInstaller
import launcher  # noqa: E402


class _ReloadHandler(FileSystemEventHandler):
    """src/ 内 .html / .css / .js 改动 → 通知 webview reload。"""
    def __init__(self, window_getter):
        self._get_window = window_getter
        self._last_reload = 0.0
        self._debounce = 0.4  # 秒：连写多次只触发 1 次 reload

    def _maybe_reload(self, path: str):
        if not (path.endswith(".html") or path.endswith(".css") or path.endswith(".js")):
            return
        now = time.time()
        if now - self._last_reload < self._debounce:
            return
        self._last_reload = now
        win = self._get_window()
        if win is None:
            return
        try:
            win.evaluate_js("location.reload()")
            print(f"[dev] reload: {Path(path).name}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[dev] reload failed: {e}", file=sys.stderr, flush=True)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._maybe_reload(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self._maybe_reload(event.src_path)


def main() -> int:
    # 启动 watchdog 监控 src/，但 webview 窗口由 launcher.main() 创建
    # —— 用一个 ref holder 在 reload 时拿到 window 实例
    window_holder = {"win": None}

    # monkey-patch：launcher.main() 创建 webview 后把 window 写进 holder
    import webview as _wv

    _orig_create_window = _wv.create_window

    def _patched_create_window(*args, **kwargs):
        win = _orig_create_window(*args, **kwargs)
        window_holder["win"] = win
        return win

    _wv.create_window = _patched_create_window

    # 监控 src/
    observer = Observer()
    handler = _ReloadHandler(lambda: window_holder["win"])
    src_dir = ROOT / "src"
    observer.schedule(handler, str(src_dir), recursive=True)
    observer.start()
    print(f"[dev] watching {src_dir} (Ctrl+C to stop)", file=sys.stderr, flush=True)

    try:
        return launcher.main()
    finally:
        observer.stop()
        observer.join(timeout=2)


if __name__ == "__main__":
    sys.exit(main())
