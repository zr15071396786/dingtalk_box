"""launcher.py — 钉钉AI助手主入口（Tauri 替代层）

职责：
1. 启动 python-sidecar 子进程（stdin/stdout JSON-RPC）
2. 启动一个 read 线程，从 sidecar stdout 读帧，分发响应 / 事件
3. 打开 pywebview 窗口，加载本地 HTML
4. 通过 JS 桥（window.dtbox.*）暴露：
   - call(method, params)         同步等 sidecar 返回（Promise）
   - onProgress(cb)               订阅 progress 事件
   - openPath(path)               调系统默认程序打开本地路径
   - exit()                       关闭工具

启动：
    python launcher.py
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

# ── 内置 key 解密（v0.3.9 安全加固）────────────────────────────
# .env 里的 DINGTALK_BOX_DEFAULT_QWEN_KEY 在 v0.3.9 起改为 enc:v1: 密文格式
# 启动时由 key_crypto 解密后注入 os.environ，sidecar/ai_bridge 看到的仍是明文
try:
    from key_crypto import decrypt_builtin_key as _decrypt_builtin_key
except Exception as _e:  # pragma: no cover - 仅打包异常时
    _decrypt_builtin_key = None  # type: ignore
    print(f"[launcher] 警告：key_crypto 加载失败：{_e}", file=sys.stderr, flush=True)

# ── stderr 兜底（frozen console=False 模式下 sys.stderr 不可写）────
# PyInstaller 在 console=False 时会把 sys.stderr 替换成无效句柄：
# 写它会抛 OSError: [Errno 22] Invalid argument。这里包一层：原
# stderr 写失败/缺失时把日志落到 %TEMP%/dingtalk_box/launcher.log，
# 调用方代码（print(..., file=sys.stderr) / sys.stderr.write/flush）
# 完全不用改。
class _SafeStderr:
    """容错 stderr：原句柄可用时透传；不可用时落日志文件。"""

    _log_fp: Any = None

    def __init__(self, original):
        self._orig = original

    def _open_log(self):
        if _SafeStderr._log_fp is not None:
            return _SafeStderr._log_fp
        try:
            from tempfile import gettempdir
            log_dir = Path(gettempdir()) / "dingtalk_box"
            log_dir.mkdir(parents=True, exist_ok=True)
            _SafeStderr._log_fp = open(
                log_dir / "launcher.log",
                "a",
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            try:
                _SafeStderr._log_fp = open(
                    os.devnull, "w", encoding="utf-8", errors="replace"
                )
            except Exception:
                _SafeStderr._log_fp = None
        return _SafeStderr._log_fp

    def write(self, s):
        if self._orig is not None:
            try:
                return self._orig.write(s)
            except Exception:
                pass
        fp = self._open_log()
        if fp is not None:
            try:
                return fp.write(s)
            except Exception:
                pass
        return 0

    def flush(self):
        if self._orig is not None:
            try:
                self._orig.flush()
                return
            except Exception:
                pass
        fp = self._open_log()
        if fp is not None:
            try:
                fp.flush()
            except Exception:
                pass


# 装上兜底：开发模式 sys.stderr 正常 → 透传；frozen console=False
# → 落日志文件，print(file=sys.stderr) 不再炸 OSError [Errno 22]
sys.stderr = _SafeStderr(sys.stderr)

# ── 项目根与 sidecar 入口 ───────────────────────────────────────────
# frozen (PyInstaller) 模式下 __file__ 指向 _MEIPASS 临时目录；开发模式下指向源码目录。
# 实际部署时 sidecar.exe 必须在 launcher.exe 同目录，所以以 sys.executable 为准。
if getattr(sys, "frozen", False):
    # 打包后：launcher.exe 所在目录就是 ROOT（找 sidecar.exe 用）
    ROOT = Path(sys.executable).resolve().parent
    # 但前端资源在 _MEIPASS（PyInstaller 解压的临时目录）
    ASSETS_ROOT = Path(getattr(sys, "_MEIPASS", str(ROOT)))
else:
    # 开发模式：launcher.py 所在目录
    ROOT = Path(__file__).resolve().parent
    ASSETS_ROOT = ROOT

SIDECAR_DIR = ROOT / "sidecar"
SIDECAR_MAIN = SIDECAR_DIR / "main.py"
SIDECAR_EXE = ROOT / "sidecar.exe"  # frozen launcher 同目录
HTML_DIR = ASSETS_ROOT / "src"
INDEX_HTML = HTML_DIR / "index.html"


# ── .env 加载 ────────────────────────────────────────────────────────
# .env 里写敏感配置，比如内置 Qwen 默认 key。
# 优先级（高 → 低）：
#   1. %APPDATA%/DingTalkBox/.env    用户自定义（首次启动从嵌入的 .env 复制，可编辑换 key）
#   2. ROOT/.env                     dev 模式 / frozen exe 同目录
#   3. _MEIPASS/.env                 frozen launcher 嵌入的默认 .env
# 首次启动时如果 APPDATA 没有 .env，从最高优先级的源复制过去。
_ENV_LOADED = False


def _dotenv_candidates() -> list[Path]:
    """按优先级返回 .env 候选路径（[用户自定义, dev/同目录, frozen 嵌入]）"""
    out: list[Path] = []
    # 1. 用户自定义
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata) / "DingTalkBox" / ".env")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "DingTalkBox" / ".env")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        out.append(Path(xdg) / "DingTalkBox" / ".env")
    # 2. dev 模式 / frozen exe 同目录
    out.append(ROOT / ".env")
    # 3. frozen 嵌入（_MEIPASS/.env）
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(Path(meipass) / ".env")
    return out


def _parse_dotenv(path: Path) -> None:
    """解析 .env 注入 os.environ（已存在的 KEY 不覆盖）

    v0.3.9+：对值以 "enc:v1:" 开头的敏感 key（当前仅 DINGTALK_BOX_DEFAULT_QWEN_KEY）
    就地解密后再注入。解密失败抛错 → 工具启动直接失败（不静默用错 key）。

    v0.3.13+ 防御：注释行里如果混了 KEY=VALUE 模式（手抖把注释和 key 粘到同一行
    的常见错误），不再静默跳过整行 → 打印警告 + 仍然尝试提取 KEY=VALUE 解析。
    历史 bug：v0.3.9-v0.3.12 deliver/.env 文件 # 注释和 DINGTALK_BOX_DEFAULT_QWEN_KEY=
    粘在同一行（缺 LF），导致整行被当注释跳过、env var 没注入、上线后用户报"未配置模型"
    排查 30 分钟。
    """
    import re
    _KV_RE = re.compile(r"([A-Z_][A-Z0-9_]*)=(\S+)")
    # 需解密的敏感 key 白名单（其他 .env 值原样透传）
    _ENCRYPTED_KEYS = frozenset({"DINGTALK_BOX_DEFAULT_QWEN_KEY"})
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # 防御：如果整行以 # 开头，但行内还有 KEY=VALUE 模式 → 警告并尝试提取
            if line.startswith("#"):
                kv_in_comment = _KV_RE.search(line)
                if kv_in_comment:
                    print(
                        f"[launcher] ⚠️  .env 注释行里发现 KEY=VALUE 模式（{kv_in_comment.group(0)[:40]}...），"
                        f"\n         .env 缺少换行符导致注释和 key 粘在同一行。"
                        f"\n         将尝试提取并解析，但请尽快修复 {path}。",
                        file=sys.stderr, flush=True,
                    )
                    # 提取第一个 KEY=VALUE 走正常解析路径
                    key = kv_in_comment.group(1)
                    value = kv_in_comment.group(2)
                else:
                    continue
            else:
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
            # 去掉外层引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            # v0.3.9 加密 key 解密
            if (
                key in _ENCRYPTED_KEYS
                and value.startswith("enc:v1:")
                and _decrypt_builtin_key is not None
            ):
                try:
                    value = _decrypt_builtin_key(value)
                    print(
                        f"[launcher] .env 已解密内置 key：{key}",
                        file=sys.stderr, flush=True,
                    )
                except RuntimeError as e:
                    print(
                        f"[launcher] ❌ .env 内置 key 解密失败：{e}\n"
                        f"         请检查 {path} 中的 {key} 是否被修改/损坏。\n"
                        f"         工具无法继续启动。",
                        file=sys.stderr, flush=True,
                    )
                    raise
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as e:
        print(f"[launcher] .env 读取失败：{e}", file=sys.stderr, flush=True)


def _load_dotenv() -> None:
    """读 .env 注入 os.environ（已存在的 KEY 不覆盖）

    首次启动：把找到的最高优先级源复制到 %APPDATA%/DingTalkBox/.env，
    让用户能编辑该文件覆盖默认 key（清空 = 用默认 key）。
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    candidates = _dotenv_candidates()
    if not candidates:
        return

    # 找第一个存在的 .env
    found = next((p for p in candidates if p.is_file()), None)
    if not found:
        return

    # 首次启动：把 found 复制到 candidates[0]（用户可编辑位置）
    user_env = candidates[0]
    if found != user_env and not user_env.exists():
        try:
            user_env.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, user_env)
            print(
                f"[launcher] 首次启动：已复制默认 .env 到 {user_env}\n"
                f"         用户可编辑该文件改/清空 key（清空 = 用默认内置 key）",
                file=sys.stderr, flush=True,
            )
        except OSError as e:
            print(f"[launcher] 复制 .env 到 APPDATA 失败：{e}", file=sys.stderr, flush=True)

    _parse_dotenv(found)


def _resolve_sidecar_cmd() -> list[str]:
    """优先用编译版 sidecar.exe（生产环境），开发模式 fallback 到源码。"""
    if SIDECAR_EXE.is_file():
        return [str(SIDECAR_EXE)]
    # 兜底：源码模式（仅开发用）
    return [sys.executable, str(SIDECAR_MAIN)]


# ── Sidecar 进程管理 ────────────────────────────────────────────────
class Sidecar:
    """spawn sidecar 子进程，提供 call() 与 event 回调"""

    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None
        self._pending: dict[str, "_Pending"] = {}  # id → pending
        self._lock = threading.Lock()
        self._event_queues: list[queue.Queue] = []
        self._stdout_thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self._read_buffer = ""

    def start(self) -> None:
        _load_dotenv()  # 把 .env 注入到当前进程环境，再 copy 给 sidecar
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        # 源码模式（dev）下让 sidecar 能找到 ai_bridge / key_crypto 等顶级模块
        if not SIDECAR_EXE.is_file():
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{ROOT}{os.pathsep}{existing}" if existing else str(ROOT)
        # frozen 模式下 launcher 自己没 console，subprocess 的 stdio 句柄需显式 CREATE_NO_WINDOW
        if sys.platform.startswith("win"):
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            creationflags = 0
        # 注意：不传 text/encoding，手动按 utf-8 字节读写，避免 frozen 进程 stdio wrap 出错
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,  # 字节流无缓冲
            creationflags=creationflags,
            env=env,
            cwd=str(ROOT),
        )
        self._stdout_thread = threading.Thread(target=self._read_loop, name="sidecar-stdout", daemon=True)
        self._stdout_thread.start()
        threading.Thread(target=self._read_stderr_loop, name="sidecar-stderr", daemon=True).start()

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            for raw in self.proc.stdout:
                if self._stopped.is_set():
                    break
                # raw 是 bytes（bufsize=0）
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                self._read_buffer += raw
                while "\n" in self._read_buffer:
                    line, self._read_buffer = self._read_buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch_frame(frame)
        except (OSError, ValueError) as e:
            sys.stderr.write(f"[launcher] sidecar stdout closed: {e}\n")
        finally:
            self._fail_all_pending(RuntimeError("sidecar 进程已退出"))

    def _read_stderr_loop(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            for raw in self.proc.stderr:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8", errors="replace")
                    except Exception:
                        raw = ""
                sys.stderr.write(f"[sidecar] {raw}")
                sys.stderr.flush()
        except Exception:
            pass

    def _dispatch_frame(self, frame: dict) -> None:
        if "event" in frame:
            # 事件帧，广播到所有订阅队列
            with self._lock:
                qs = list(self._event_queues)
            for q in qs:
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    pass
            return
        # 响应帧：关联到 pending
        req_id = frame.get("id")
        if not req_id:
            return
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is not None:
            pending.set_result(frame)
        else:
            sys.stderr.write(f"[launcher] 收到未关联响应: {frame}\n")

    def _fail_all_pending(self, exc: Exception) -> None:
        with self._lock:
            pendings = list(self._pending.items())
            self._pending.clear()
        for _, p in pendings:
            p.set_exception(exc)

    def call(self, method: str, params: dict | None = None, timeout: float = 600) -> dict:
        """同步等待 sidecar 返回（会阻塞直到响应或超时）"""
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("sidecar 进程未运行")
        req_id = str(uuid.uuid4())
        pending = _Pending()
        with self._lock:
            self._pending[req_id] = pending
        frame = {"id": req_id, "method": method, "params": params or {}}
        try:
            payload = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError) as e:
            with self._lock:
                self._pending.pop(req_id, None)
            raise RuntimeError(f"sidecar 写入失败：{e}") from e
        return pending.get(timeout=timeout)

    def subscribe_events(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._event_queues.append(q)
        return q

    def unsubscribe_events(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._event_queues:
                self._event_queues.remove(q)

    def shutdown(self) -> None:
        self._stopped.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.kill()
                except Exception:
                    pass


class _Pending:
    __slots__ = ("_event", "_exc", "_result")
    def __init__(self):
        self._event = threading.Event()
        self._exc: Exception | None = None
        self._result: dict | None = None

    def set_result(self, r: dict) -> None:
        self._result = r
        self._event.set()

    def set_exception(self, e: Exception) -> None:
        self._exc = e
        self._event.set()

    def get(self, timeout: float) -> dict:
        if not self._event.wait(timeout=timeout):
            raise TimeoutError("sidecar 响应超时")
        if self._exc is not None:
            raise self._exc
        return self._result  # type: ignore[return-value]


# ── SidecarError ─────────────────────────────────────────────────────
class SidecarError(Exception):
    def __init__(self, error_obj: dict):
        self.code = error_obj.get("code")
        self.message = error_obj.get("message", "")
        self.data = error_obj.get("data") or {}
        super().__init__(f"[{self.code}] {self.message}")


# ── WebView 桥（pywebview 的 js_api） ───────────────────────────────
class Bridge:
    """暴露给 JS 的 dtbox.* 桥"""

    def __init__(self, sidecar: Sidecar):
        self.sidecar = sidecar
        self._event_q: queue.Queue | None = None
        self._event_thread: threading.Thread | None = None
        # 用户活动检测：每次用户调 method 就更新时间戳；self-test 看到最近
        # 有活动就停手，避免阻塞用户调用
        self._user_active_at = 0.0
        # 自检豁免：self-test 自己的调用不应触发「用户活动」退避
        self._suppress_user_active = False

    def call(self, method: str, params: Any) -> dict:
        """JS → Python：同步等 sidecar 返回（pywebview 自动转 JSON）"""
        # 用户调任何 method 都算「活动」，让自检退避
        # （自检线程会临时把 _suppress_user_active 置 True，不更新此戳）
        if not self._suppress_user_active:
            self._user_active_at = time.time()
        # pywebview 在某些版本下把单一参数包成 {params: ...}
        if isinstance(params, dict) and "params" in params and len(params) == 1 and not isinstance(params.get("params"), dict):
            # 不展开，保持原样
            pass
        try:
            resp = self.sidecar.call(method, params if isinstance(params, dict) else {})
        except TimeoutError as e:
            return {"error": {"code": -32003, "message": f"sidecar 响应超时：{e}"}}
        except Exception as e:
            return {"error": {"code": -32000, "message": str(e)}}
        if "error" in resp:
            # 把 error 直接抛回前端（前端按 error.code 处理）
            err = resp["error"]
            return {"error": err}
        return {"result": resp.get("result", {})}

    def openPath(self, path: str) -> None:
        """用系统默认程序打开本地文件/文件夹"""
        if not path:
            return
        p = Path(path)
        if not p.exists():
            return
        if sys.platform.startswith("win"):
            try:
                os.startfile(str(p))  # type: ignore[attr-defined]
                return
            except OSError:
                pass
        try:
            webbrowser.open(p.as_uri())
        except Exception:
            pass

    def openExternal(self, url: str) -> bool:
        """用系统默认浏览器打开外部 URL（HTTP/HTTPS）

        用途：Bug 1 修复 —— dws auth login 返回的「URL」其实是登录跳转链接，
        浏览器打开那个 URL 才能看到 QR 二维码（在浏览器页面里）。
        """
        if not url or not url.lower().startswith(("http://", "https://")):
            return False
        try:
            return webbrowser.open(url, new=2)  # new=2 强制新窗口/标签
        except Exception:
            return False

    def startProgressForwarder(self, window) -> None:
        """从 sidecar 拉事件 → 通过 window.evaluate_js 推给前端"""
        if self._event_thread is not None:
            return
        q = self.sidecar.subscribe_events()
        self._event_q = q
        def loop():
            while True:
                try:
                    frame = q.get(timeout=1)
                except queue.Empty:
                    continue
                if frame is None:
                    break
                if frame.get("event") == "progress":
                    js = f"window.dispatchEvent(new CustomEvent('dtbox:progress', {{detail: {json.dumps(frame['data'], ensure_ascii=False)}}}))"
                    try:
                        window.evaluate_js(js)
                    except Exception:
                        pass
        t = threading.Thread(target=loop, name="event-forwarder", daemon=True)
        t.start()
        self._event_thread = t

    def exit(self) -> None:
        """前端请求退出"""
        # 通过一个事件循环回调实现
        import webview as _wv
        _wv.destroy_window()


# ── 入口 ───────────────────────────────────────────────────────────
def main() -> int:
    # 1. 启动 sidecar
    cmd = _resolve_sidecar_cmd()
    print(f"[launcher] starting sidecar: {' '.join(cmd)}", file=sys.stderr)
    sidecar = Sidecar(cmd)
    try:
        sidecar.start()
    except Exception as e:
        print(f"[launcher] failed to start sidecar: {e}", file=sys.stderr)
        return 1

    # 健康检查
    try:
        r = sidecar.call("ping", {}, timeout=30)
        print(f"[launcher] ping ok: {r}", file=sys.stderr)
    except Exception as e:
        print(f"[launcher] ping failed: {e}", file=sys.stderr)
        sidecar.shutdown()
        return 2

    # 2. 启动 webview
    import webview

    bridge = Bridge(sidecar)

    # 注册一个最小的事件 API：JS 端 window.dtbox.onProgress(cb) 实际订阅 window 事件
    index_url = INDEX_HTML.resolve().as_uri()
    print(f"[launcher] opening webview: {index_url}", file=sys.stderr)

    window = webview.create_window(
        title="钉钉AI助手",
        url=index_url,
        width=1440,
        height=900,
        min_size=(1280, 800),
        resizable=True,
        js_api=bridge,
    )

    def on_loaded():
        bridge.startProgressForwarder(window)
        # 注入 onProgress 桥
        window.evaluate_js("""
        (function(){
          window.dtbox = window.dtbox || {};
          // call: 走 pywebview JS API
          window.dtbox.call = function(method, params){
            if (window.pywebview && window.pywebview.api) {
              return window.pywebview.api.call(method, params || {});
            }
            throw new Error("pywebview api 未就绪");
          };
          window.dtbox.onProgress = function(cb){
            window.addEventListener('dtbox:progress', function(e){ cb(e.detail); });
          };
          window.dtbox.openPath = function(p){
            // 通过 pywebview bridge 调用 Python
            if (window.pywebview && window.pywebview.api) {
              window.pywebview.api.openPath(p);
            } else if (window.dtbox._openPath) {
              window.dtbox._openPath(p);
            }
          };
          window.dtbox.openExternal = function(url){
            // Bug 1: dws auth login 抓的 URL 用系统浏览器打开
            if (window.pywebview && window.pywebview.api && window.pywebview.api.openExternal) {
              return window.pywebview.api.openExternal(url);
            }
            // fallback：临时塞个 a 标签触发
            try { window.open(url, '_blank'); return true; } catch(e) { return false; }
          };
          window.dtbox.exit = function(){
            if (window.pywebview && window.pywebview.api) {
              window.pywebview.api.exit();
            } else if (window.dtbox._exit) {
              window.dtbox._exit();
            }
          };
        })();
        """)
        # 把 launcher dist 自己的 mtime 注入 footer，让用户一眼能确认是哪个版本
        # v0.3.14：版本号从 version.__version__ 单一真相源读取，
        # 不再硬编码 "v0.1.0"（之前这个版本号 3 个版本没更新过，src/index.html 也没改）
        try:
            from version import __version__ as _VERSION
            launcher_mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(Path(sys.executable).stat().st_mtime))
            # 用 json.dumps 安全转义，避免版本号含特殊字符时 break JS 字符串
            ver_text = f"v{_VERSION} · build {launcher_mtime}"
            window.evaluate_js(
                f"document.getElementById('version-tag').textContent = {json.dumps(ver_text, ensure_ascii=False)};"
            )
        except Exception as _e:
            print(f"[launcher] 版本号注入失败：{_e}", file=sys.stderr, flush=True)
        # self-test：仅 ping（验证 dtbox.call 通路 + sidecar 响应）；
        # 每 200ms 检查一次 bridge._user_active_at —— 用户一开 modal / 点按钮
        # 就立刻退避，让出 sidecar 的 stdin 队列。
        # 自检自己的 ping 不算「用户活动」（bridge._suppress_user_active 豁免），
        # 否则会被自己的 ping 误判成用户活动而自杀。
        # 旧版曾调 generate_daily 跑完整链路 → 单 RPC 可占 90s+，用户的
        # list_providers 被卡在 stdin 队列里，modal 永远停在「加载中…」。
        def _self_test():
            bridge._suppress_user_active = True
            try:
                js_init = (
                    "window.__self_test_result = 'pending';\n"
                    "(async()=>{\n"
                    "    try {\n"
                    "        const t0 = Date.now();\n"
                    "        const r = await window.dtbox.call('ping', {});\n"
                    "        // bridge.call 返 {result: ...} 包装，要看 r.result.ok\n"
                    "        const ok = !!(r && r.result && r.result.ok);\n"
                    "        window.__self_test_result = JSON.stringify({ok, dt_ms: Date.now() - t0});\n"
                    "    } catch (e) {\n"
                    "        window.__self_test_result = JSON.stringify({err: String(e && e.message || e)});\n"
                    "    }\n"
                    "})();\n"
                )
                window.evaluate_js(js_init)
                deadline = time.time() + 5
                while time.time() < deadline:
                    # 用户一活动就退避（自检豁免已设，自己的 ping 不算）
                    if time.time() - bridge._user_active_at < 1.0:
                        print("[launcher] self-test: user active, aborting", file=sys.stderr, flush=True)
                        return
                    res = window.evaluate_js("window.__self_test_result")
                    if res and res != "pending":
                        print(f"[launcher] self-test ping: {res}", file=sys.stderr, flush=True)
                        return
                    time.sleep(0.2)
                print("[launcher] self-test ping TIMEOUT (5s)", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[launcher] self-test failed: {e!r}", file=sys.stderr, flush=True)
            finally:
                bridge._suppress_user_active = False
        threading.Thread(target=_self_test, daemon=True).start()

    window.events.loaded += on_loaded

    try:
        webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        print("[launcher] shutting down", file=sys.stderr)
        sidecar.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
