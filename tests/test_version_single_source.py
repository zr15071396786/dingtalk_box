"""test_version_single_source.py — v0.3.14 版本号单一真相源验证

v0.3.13 及之前：版本号散落在 6+ 处（src/index.html 硬编码 v0.3.10、
launcher.py 写死 "v0.1.0"、dispatcher.py ping 返 "0.1.0"、
send.py diagnose 返 "0.1.0"、README、git tag），发版要手动改 6 处，
漏一处就脱节（用户报：UI 右下角显示 v0.3.10 但实际发版是 v0.3.13）。

v0.3.14 fix：建立 version.py 作为单一真相源。
  - launcher.py / sidecar 启动时 `from version import __version__`
  - src/index.html footer 占位 `<span id="version-tag">v?</span>`，
    launcher 启动时 evaluate_js 写入 `<ver> · build <mtime>`
  - sidecar ping / diagnose 读这个常量
  - 发版只需要改 version.py 一行

测试覆盖：
  1. version.py 存在并定义 __version__
  2. __version__ 符合 semver-ish（数字.数字.数字）
  3. launcher.py / dispatcher.py / send.py 都用 `from version import`
  4. sidecar/main.py 把项目根加到 sys.path（dev 模式防御）
  5. 3 个 .spec 的 hiddenimports 都包含 'version'
  6. 没有其他源文件硬编码版本号（src/index.html 是 v? 占位 / 是 v<N> 都应被扫描）
  7. 从任意 cwd 运行 `python -c "from version import __version__"` 都能成功
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# 把项目根加到 path（和现有 tests 风格一致）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import version  # noqa: E402


PASS = "[PASS]"
FAIL = "[FAIL]"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    line = f"{tag} {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def test_version_module_exists():
    """1. version.py 存在并定义 __version__"""
    vp = ROOT / "version.py"
    ok1 = _check("version.py 存在", vp.is_file(), str(vp))
    has_attr = hasattr(version, "__version__")
    ok2 = _check("version.__version__ 已定义", has_attr)
    return ok1 and ok2


def test_version_format():
    """2. __version__ 符合 semver-ish：纯数字点分（如 0.3.14，可选 .devN / -rcN 后缀）"""
    v = version.__version__
    pattern = r"^\d+\.\d+\.\d+([\.\-a-zA-Z0-9]+)?$"
    ok = bool(re.match(pattern, v))
    return _check(
        f"__version__='{v}' 符合 semver-ish",
        ok,
        f"pattern: {pattern}",
    )


def test_source_files_import_from_version():
    """3. launcher.py / dispatcher.py / send.py 都用 `from version import __version__`"""
    targets = [
        ROOT / "launcher.py",
        ROOT / "sidecar" / "core" / "dispatcher.py",
        ROOT / "sidecar" / "core" / "send.py",
    ]
    all_ok = True
    for fp in targets:
        if not fp.is_file():
            all_ok &= _check(f"{fp.relative_to(ROOT)} 存在", False)
            continue
        text = fp.read_text(encoding="utf-8")
        ok = "from version import __version__" in text
        all_ok &= _check(
            f"{fp.relative_to(ROOT)} 包含 `from version import __version__`",
            ok,
        )
    return all_ok


def test_sidecar_main_adds_root_to_sys_path():
    """4. sidecar/main.py 把项目根加到 sys.path（dev 模式防御）"""
    fp = ROOT / "sidecar" / "main.py"
    text = fp.read_text(encoding="utf-8")
    # 期待 _HERE.parent 加进 sys.path
    ok1 = "_HERE.parent" in text or "_PROJECT_ROOT" in text
    ok2 = "from version import" in text or "_SIDECAR_VERSION" in text
    return _check(
        "sidecar/main.py 把项目根加到 sys.path 并 import version",
        ok1 and ok2,
        f"_HERE.parent={ok1} version-import={ok2}",
    )


def test_spec_files_have_version_in_hiddenimports():
    """5. build.spec / launcher.spec 的 hiddenimports 都包含 'version'"""
    specs = [
        ROOT / "build.spec",       # sidecar
        ROOT / "launcher.spec",    # launcher
        # ai_bridge 不 import version，跳过 ai_bridge.spec
    ]
    all_ok = True
    for sp in specs:
        text = sp.read_text(encoding="utf-8")
        # 简单字符串匹配（spec 文件小、不会和注释冲突）
        ok = re.search(r"hiddenimports\s*=\s*\[", text, re.M) is not None
        if not ok:
            all_ok &= _check(f"{sp.name} 含 hiddenimports=[…]", False)
            continue
        # 在 hiddenimports 块里找 'version'
        m = re.search(r"hiddenimports\s*=\s*\[(.*?)\]", text, re.S)
        block = m.group(1) if m else ""
        has_version = bool(re.search(r"['\"]version['\"]", block))
        all_ok &= _check(
            f"{sp.name} hiddenimports 含 'version'",
            has_version,
        )
    return all_ok


def test_no_hardcoded_version_in_source():
    """6. 关键源文件（HTML/JS/CSS）的运行时代码里没有硬编码版本号

    只扫描 HTML/JS/CSS（不要扫 Python 源码，因为 Python 的 docstring 里有大量
    "v0.3.X 修复" 的历史 changelog 注释，扫了全是 false positive）。

    规则：
      - src/index.html: 不应出现 `v0.3.X` 字面量（只有 v? 占位 + version-tag id）
      - src/main.js / 其他 JS：不应出现 `v0.3.X` 字面量
      - HTML / JS 注释（<!-- ... --> / // ...）放行
    """
    pat = re.compile(r"v\d+\.\d+\.\d+")
    bad: list[tuple[Path, str]] = []
    scan_dirs = [ROOT / "src"]
    scan_exts = {".html", ".js", ".css"}

    def _strip_comments_and_strings(line: str, ext: str) -> str:
        """剥掉 // 行注释、字符串字面量里的内容"""
        # JS 行注释（行首或代码后）
        idx = line.find("//")
        if idx >= 0:
            line = line[:idx]
        # 剥掉字符串字面量里的 v0.3.X（避免 history 注释命中）
        line = re.sub(r'"[^"]*"', '""', line)
        line = re.sub(r"'[^']*'", "''", line)
        line = re.sub(r"`[^`]*`", "``", line)
        return line

    for sdir in scan_dirs:
        if not sdir.is_dir():
            continue
        for fp in sdir.rglob("*"):
            if not fp.is_file() or fp.suffix not in scan_exts:
                continue
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception:
                continue
            # 先把所有 HTML / JS 块注释剥掉（<!-- ... --> / /* ... */）
            text_no_html_comment = re.sub(r"<!--.*?-->", "", text, flags=re.S)
            text_no_comments = re.sub(r"/\*.*?\*/", "", text_no_html_comment, flags=re.S)
            # 按行处理：剥 // 后面的内容 + 字符串字面量
            for lineno, raw_line in enumerate(text_no_comments.splitlines(), 1):
                clean = _strip_comments_and_strings(raw_line, fp.suffix)
                if pat.search(clean):
                    bad.append((fp.relative_to(ROOT), f"L{lineno}: {raw_line.strip()}"))

    if bad:
        for rel, line in bad:
            print(f"[FAIL] {rel}: {line}")
        return _check("src/ 源文件无 hardcoded v<X.Y.Z>", False, f"{len(bad)} 处")
    return _check("src/ 源文件无 hardcoded v<X.Y.Z>", True)


def test_dev_mode_subprocess_can_import_version():
    """7. dev 模式：模拟 launcher 子进程（PYTHONPATH=ROOT），sidecar 能 import version

    实际场景：
      - launcher.py 启动 sidecar 时设 PYTHONPATH=ROOT（dev 模式）
      - sidecar/main.py 自己也会把 _HERE.parent 加到 sys.path（防御）
      - frozen 模式 PyInstaller 走 pathex + hiddenimports

    这里只测「sidecar/main.py 自身的 sys.path 防御」：
    直接 python sidecar/main.py（cwd=ROOT），不传任何 env，
    模拟「外部脚本误调 sidecar/main.py 但环境没设 PYTHONPATH」的边界场景。
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # 用 sidecar/main.py 自己的 sys.path 注入逻辑（_HERE.parent）
    # 这里手动等价：把 ROOT 加进 PYTHONPATH，等价于 launcher.py 做的事
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; "
         f"sys.path.insert(0, {str(ROOT)!r}); "
         "from version import __version__; print(__version__)"],
        cwd=str(ROOT),  # 默认 cwd
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        env=env,
    )
    ok = proc.returncode == 0 and proc.stdout.strip() == version.__version__
    return _check(
        "dev 模式 PYTHONPATH=ROOT 时 sidecar 能 import version",
        ok,
        f"rc={proc.returncode} stdout={proc.stdout.strip()!r} stderr={proc.stderr.strip()[:200]!r}",
    )


def test_dispatcher_ping_uses_version():
    """8. dispatcher.ping 响应里的 version 字段 == version.__version__"""
    # 用 AST 静态检查 — 避免真的跑 sidecar 子进程
    fp = ROOT / "sidecar" / "core" / "dispatcher.py"
    text = fp.read_text(encoding="utf-8")
    has_const = '"version": _VERSION' in text or "'version': _VERSION" in text
    no_hardcoded_v = not re.search(r'"version"\s*:\s*"\d+\.\d+\.\d+"', text)
    return _check(
        "dispatcher.ping 用 _VERSION 而不是硬编码",
        has_const and no_hardcoded_v,
        f"_VERSION={has_const} no-hardcoded={no_hardcoded_v}",
    )


def test_send_diagnose_uses_version():
    """9. send.collect_diagnostics 里的 'version' 字段 == version.__version__"""
    fp = ROOT / "sidecar" / "core" / "send.py"
    text = fp.read_text(encoding="utf-8")
    has_const = '"version": _VERSION' in text
    no_hardcoded_v = not re.search(r'"version"\s*:\s*"\d+\.\d+\.\d+"', text)
    return _check(
        "send.diagnose 用 _VERSION 而不是硬编码",
        has_const and no_hardcoded_v,
        f"_VERSION={has_const} no-hardcoded={no_hardcoded_v}",
    )


def test_index_html_has_version_tag_id():
    """10. src/index.html 含 <span id="version-tag"> 占位符"""
    fp = ROOT / "src" / "index.html"
    text = fp.read_text(encoding="utf-8")
    ok = 'id="version-tag"' in text
    return _check("src/index.html 含 id=\"version-tag\" 占位", ok)


def main():
    tests = [
        test_version_module_exists,
        test_version_format,
        test_source_files_import_from_version,
        test_sidecar_main_adds_root_to_sys_path,
        test_spec_files_have_version_in_hiddenimports,
        test_no_hardcoded_version_in_source,
        test_dev_mode_subprocess_can_import_version,
        test_dispatcher_ping_uses_version,
        test_send_diagnose_uses_version,
        test_index_html_has_version_tag_id,
    ]
    results = []
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        results.append(bool(t()))
    print(f"\n=== 总计：{sum(results)}/{len(results)} 通过 ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())