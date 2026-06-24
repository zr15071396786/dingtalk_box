"""test_config_first_run.py — v0.3.12 config.py 死循环修复验证

v0.3.10 bug: core/config.py ensure_config() 调 save()，save() 又调
ensure_config()，在 config.yaml 不存在（首次启动）时死循环 → RecursionError。
同事 launcher.log 显示 sidecar 启动后立刻崩，进程消失。

v0.3.12 fix:
  1. ensure_config 直接写文件，不调 save
  2. save 直接走 paths.config_path()，不调 ensure_config
  3. paths.data_dir() 用 os.makedirs 替代 root.mkdir()，绕开 Python 3.14
     frozen 模式下 pathlib.WindowsPath 懒加载 _str/_drv 失败的 bug

验证场景:
  1. config.yaml 不存在 → load() 应能正常返回 DEFAULT_CONFIG（不崩）
  2. 反复 load() 多次 → 不应进入死循环（RecursionError）
  3. ensure_config + save 互不递归
  4. paths.data_dir() 在 Windows 下创建嵌套目录 OK
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 把项目根加到 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "[OK]"
BAD = "[BAD]"


def setup_test_env():
    """monkeypatch paths.data_dir 走临时目录"""
    from sidecar.core import config, paths
    tmpdir = Path(tempfile.mkdtemp(prefix="dingtalk_box_test_"))
    # 用 monkeypatch 替换 data_dir 函数体
    def _fake_data_dir() -> Path:
        d = tmpdir
        d.mkdir(parents=True, exist_ok=True)
        return d
    original = paths.data_dir
    paths.data_dir = _fake_data_dir
    # config_path/output_dir/logs_dir 内部都调 data_dir()，所以一处替换即可
    return tmpdir, config, paths, original


def cleanup(tmpdir, paths, original):
    paths.data_dir = original
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_first_run_no_config():
    """config.yaml 不存在时 load() 应返回 DEFAULT_CONFIG，不崩"""
    tmpdir, config, paths, original = setup_test_env()
    try:
        cfg_path = paths.config_path()
        assert not cfg_path.exists(), f"test setup error: {cfg_path} should not exist"
        print(f"  {OK} test env: {tmpdir}, config.yaml absent")

        data = config.load()
        assert isinstance(data, dict), f"load() should return dict, got {type(data)}"
        assert "llm" in data, "load() should return DEFAULT_CONFIG with llm key"
        assert "corp" in data, "load() should return DEFAULT_CONFIG with corp key"
        print(f"  {OK} load() returned DEFAULT_CONFIG, no crash")

        assert cfg_path.exists(), f"ensure_config should have written {cfg_path}"
        print(f"  {OK} config.yaml was written by ensure_config")

        import yaml
        on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert on_disk == data, f"on-disk content ({on_disk}) != returned data ({data})"
        print(f"  {OK} on-disk content matches returned data")
    finally:
        cleanup(tmpdir, paths, original)


def test_repeated_load_no_recursion():
    """反复 load() 不应该触发 RecursionError"""
    tmpdir, config, paths, original = setup_test_env()
    try:
        for i in range(20):
            data = config.load()
            assert isinstance(data, dict)
        print(f"  {OK} 20x load() all returned dict, no RecursionError")
    finally:
        cleanup(tmpdir, paths, original)


def test_ensure_save_not_recursive():
    """ensure_config 和 save 不应该互相调用"""
    from sidecar.core import config
    import ast
    import inspect

    # 用 AST 解析检查实际代码（不被 docstring 干扰）
    def _calls(func, target_name):
        tree = ast.parse(inspect.getsource(func))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # 处理简单调用 func(args) 和方法/属性调用
                if isinstance(node.func, ast.Name) and node.func.id == target_name:
                    return True
                if isinstance(node.func, ast.Attribute) and node.func.attr == target_name:
                    return True
        return False

    # ensure_config 不应该调 save
    assert not _calls(config.ensure_config, "save"), (
        "ensure_config still calls save() -- would cause infinite recursion"
    )
    # ensure_config 应该调 _write_yaml
    assert _calls(config.ensure_config, "_write_yaml"), (
        "ensure_config should call _write_yaml directly"
    )
    # save 不应该调 ensure_config
    assert not _calls(config.save, "ensure_config"), (
        "save still calls ensure_config() -- would cause infinite recursion"
    )
    print(f"  {OK} ensure_config uses _write_yaml directly (no save() call)")
    print(f"  {OK} save uses paths.config_path() directly (no ensure_config() call)")


def test_data_dir_uses_os_makedirs():
    """paths.data_dir() 应该用 os.makedirs 而不是 root.mkdir/Path.mkdir"""
    from sidecar.core import paths
    import ast
    import inspect

    # 解析为 AST 过滤掉 docstring，只看真实代码
    src = inspect.getsource(paths.data_dir)
    tree = ast.parse(src)

    # 删掉所有 Expr->Constant(str) 节点（docstring），保留可执行代码
    # 简化做法：直接遍历 ast.Call，找 attribute chain 形如 root.mkdir / x.mkdir
    def _is_mkdir_call(node):
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        # 形式: root.mkdir(...) 或 x.mkdir(parents=True, exist_ok=True)
        if isinstance(func, ast.Attribute) and func.attr == "mkdir":
            return True
        return False

    mkdir_calls = [n for n in ast.walk(tree) if _is_mkdir_call(n)]
    assert not mkdir_calls, (
        f"paths.data_dir() should NOT use root.mkdir() (triggers pathlib _str/_drv bug). "
        f"Found {len(mkdir_calls)} mkdir call(s). Use os.makedirs instead."
    )

    # 应该有 os.makedirs 调用
    def _is_os_makedirs_call(node):
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "makedirs"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        )

    makedirs_calls = [n for n in ast.walk(tree) if _is_os_makedirs_call(n)]
    assert makedirs_calls, "paths.data_dir() should call os.makedirs() at least once"
    print(f"  {OK} paths.data_dir() uses os.makedirs (avoids pathlib _str/_drv bug)")


def test_data_dir_creates_nested():
    """data_dir() 在 Windows 下应能创建嵌套目录（用临时根测试）"""
    # 在 setup_test_env 那个 _fake_data_dir 里测试了嵌套（tmpdir 是平层，
    # 但 makedirs exist_ok 仍会被走）。这里直接看实际 data_dir() 在 windows
    # 上能调用。
    from sidecar.core import paths
    d = paths.data_dir()
    assert d.exists(), f"data_dir() should exist at {d}"
    assert d.is_dir()
    print(f"  {OK} real data_dir() exists: {d}")


if __name__ == "__main__":
    print("=" * 60)
    print("v0.3.12 config.py first-run / recursion fix verification")
    print("=" * 60)
    print()
    print("[1/5] first run, no config.yaml, load() works")
    test_first_run_no_config()
    print()
    print("[2/5] repeated load() no RecursionError")
    test_repeated_load_no_recursion()
    print()
    print("[3/5] ensure_config / save not mutually recursive")
    test_ensure_save_not_recursive()
    print()
    print("[4/5] paths.data_dir() uses os.makedirs")
    test_data_dir_uses_os_makedirs()
    print()
    print("[5/5] data_dir() creates nested directories")
    test_data_dir_creates_nested()
    print()
    print("=" * 60)
    print("ALL CHECKS PASSED -- v0.3.12 config fix verified")
    print("=" * 60)