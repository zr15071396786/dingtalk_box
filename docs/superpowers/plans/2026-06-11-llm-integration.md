# LLM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户能在 GUI 内从 10 家云端大模型厂商中选 1 家、绑定 API key，实现「生 成 今 日 日 报」一键完成（拉数据 → AI 总结 → 渲染 PNG/MD），秘钥 Windows DPAPI 加密落盘，配置可随时切换或清除。

**Architecture:** 3 进程。launcher (GUI) → sidecar (业务核心) → ai_bridge (新进程，单职责调 LLM)。密钥只在 ai_bridge 进程内存中出现，DPAPI 加密后落盘 `%APPDATA%/DingTalkBox/llm_secret.bin`。未配置时回退旧 `awaiting_ai` 流程，老用户不破。

**Tech Stack:**
- Python 3.10+ / PyInstaller frozen exe
- httpx (新增) for OpenAI-compatible HTTP
- ctypes 调用 crypt32.dll for DPAPI（无新依赖）
- pyyaml (已有) for config
- pywebview 5.x + WebView2 (前端不变)
- stdio JSON-RPC for sidecar↔launcher, Popen for sidecar↔ai_bridge

---

## File Structure

### 新建

| 文件 | 职责 | 行数 |
|---|---|---|
| `ai_bridge/__init__.py` | 空包标识 | 0 |
| `ai_bridge/dpapi.py` | CryptProtect/UnprotectData ctypes 封装 | ~50 |
| `ai_bridge/providers.py` | 读 providers.yaml，10 个 preset 查找 | ~80 |
| `ai_bridge/schema.py` | report.json 字段校验 | ~50 |
| `ai_bridge/main.py` | CLI 入口：读 config→解 key→调 LLM→写 report.json | ~180 |
| `ai_bridge.spec` | PyInstaller spec (console=True) | ~30 |
| `sidecar/core/llm_config.py` | sidecar 侧：set/get/clear/test + DPAPI 包装 | ~150 |
| `assets/providers.yaml` | 10 家厂商 preset 列表（bundled 只读） | ~60 |
| `tests/test_ai_bridge.py` | ai_bridge 单测 | ~250 |
| `tests/test_llm_config.py` | sidecar.llm_config 单测 | ~150 |

### 修改

| 文件 | 改动 |
|---|---|
| `assets/default_config.yaml` | 加 `llm:` 段（active_provider: null） |
| `sidecar/core/daily_report.py` | 阶段 2：自动调 ai_bridge（保留 awaiting_ai 兜底） |
| `sidecar/core/dispatcher.py` | +4 methods: set_llm_config / get_llm_config / test_llm_connection / ai_analyze |
| `sidecar/core/logging_setup.py` | +redact_key filter（Authorization / api_key 字段脱敏） |
| `sidecar/main.py` | 请求 logger 加新 method |
| `src/index.html` | +⚙ 按钮 + llm-modal 容器 |
| `src/style.css` | +llm-modal 细节样式 |
| `src/main.js` | +配置 modal 逻辑 + boot() 调 get_llm_config + awaiting_ai 时「去配置」按钮 |
| `requirements.txt` | +httpx>=0.27 |

### 不动

`build.spec`（ai-bridge 独立 spec，sidecar 不需 httpx）；`launcher.spec`；`external/dingtalk_daily_summary.py`（render 逻辑不变）；`deliver/重启到最新版.cmd`。

---

## 任务地图

| 阶段 | 任务 | 验收 |
|---|---|---|
| **P0** | 1-9: ai_bridge 进程骨架 + 1 个 provider (OpenAI) 端到端 | 配 OpenAI 真 key → 一键生成真报告 |
| **P1** | 10-15: 10 家 providers + UI modal + 状态条 | 集成 case 1-5 全过 |
| **P2** | 16-21: 错误处理 + 日志脱敏 + 诊断脱敏 | 集成 case 6-11 全过 |
| **P3** | 22-27: 完整单测 + 打包 + 回归 | 单测全绿 + 端到端验收 |

---

# P0: ai_bridge 进程骨架 + 1 个 provider 端到端

## Task 1: 项目骨架 + 新增 httpx 依赖

**Files:**
- Modify: `requirements.txt`
- Create: `ai_bridge/__init__.py`
- Create: `ai_bridge/.gitkeep` (代替空目录占位，__init__.py 已够)

- [ ] **Step 1: 加 httpx 到 requirements.txt**

在 `requirements.txt` 末尾追加一行：
```
httpx>=0.27
```

- [ ] **Step 2: 创建 ai_bridge 包**

`ai_bridge/__init__.py`：
```python
"""ai_bridge — 独立 LLM 调用进程

唯一职责：从 stdin/argv 接收任务，调用大模型，写 report.json，stdout 输出结果。
不持有任何业务状态（除单次调用的 LLM key 在内存中）。
"""
```

- [ ] **Step 3: 安装 httpx 验证**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pip install -r requirements.txt
```

Expected: 安装成功，`python -c "import httpx; print(httpx.__version__)"` 输出 `0.27.x` 或更高。

- [ ] **Step 4: Commit**

```bash
git add requirements.txt ai_bridge/__init__.py
git commit -m "feat(ai_bridge): 项目骨架 + 新增 httpx 依赖"
```

---

## Task 2: DPAPI 加解密模块 (TDD)

**Files:**
- Create: `tests/test_ai_bridge_dpapi.py`
- Create: `ai_bridge/dpapi.py`

- [ ] **Step 1: 写失败的单测**

`tests/test_ai_bridge_dpapi.py`：
```python
"""ai_bridge/dpapi.py 单测

注意：这些测试只在 Windows 跑（DPAPI 是 Windows-only）。
非 Windows 平台 skip。
"""
import os
import platform
import tempfile
from pathlib import Path

import pytest

from ai_bridge import dpapi


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="DPAPI 仅 Windows 支持",
)


def test_dpapi_roundtrip():
    """加密 → 解密 → 明文一致"""
    plaintext = "sk-test-1234567890abcdef"
    ciphertext = dpapi.protect(plaintext)
    assert isinstance(ciphertext, bytes)
    assert len(ciphertext) > 0
    assert plaintext.encode("utf-8") not in ciphertext  # 至少明文不在密文里
    recovered = dpapi.unprotect(ciphertext)
    assert recovered == plaintext


def test_dpapi_file_roundtrip():
    """文件级：protect_to_file → unprotect_from_file"""
    plaintext = "sk-file-test-abcdef"
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "secret.bin"
        dpapi.protect_to_file(plaintext, str(path))
        assert path.is_file()
        assert path.stat().st_size > 0
        # 文件内容不应包含明文
        assert plaintext.encode("utf-8") not in path.read_bytes()
        # 解密回来
        recovered = dpapi.unprotect_from_file(str(path))
        assert recovered == plaintext


def test_dpapi_missing_file_raises():
    """文件不存在 → raise FileNotFoundError"""
    with pytest.raises(FileNotFoundError):
        dpapi.unprotect_from_file("Z:/nonexistent/secret.bin")


def test_dpapi_corrupt_file_raises():
    """随机字节 → CryptUnprotectData 失败 → raise"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "corrupt.bin"
        path.write_bytes(os.urandom(64))
        with pytest.raises(Exception) as exc_info:
            dpapi.unprotect_from_file(str(path))
        # 错误信息应包含 DPAPI 关键字
        assert "DPAPI" in str(exc_info.value) or "decrypt" in str(exc_info.value).lower()
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_dpapi.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_bridge.dpapi'`

- [ ] **Step 3: 实现 dpapi.py**

`ai_bridge/dpapi.py`：
```python
"""dpapi.py — Windows DPAPI 加解密（ctypes 调 crypt32.dll）

Public API:
  protect(plaintext: str) -> bytes
  unprotect(ciphertext: bytes) -> str
  protect_to_file(plaintext: str, path: str) -> None
  unprotect_from_file(path: str) -> str

所有函数 fail 时抛 RuntimeError，信息含 "DPAPI" 关键字。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


# ── ctypes 类型定义 ──────────────────────────────────────────────────
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_CryptProtectData = ctypes.windll.crypt32.CryptProtectData
_CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB),  # pDataIn
    wintypes.LPCWSTR,             # szDataDescr
    ctypes.POINTER(_DATA_BLOB),  # pOptionalEntropy
    ctypes.c_void_p,              # pvReserved
    ctypes.c_void_p,              # pPromptStruct
    wintypes.DWORD,               # dwFlags
    ctypes.POINTER(_DATA_BLOB),  # pDataOut
]
_CryptProtectData.restype = wintypes.BOOL

_CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData
_CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB),  # pDataIn
    ctypes.c_void_p,              # ppszDataDescr
    ctypes.POINTER(_DATA_BLOB),  # pOptionalEntropy
    ctypes.c_void_p,              # pvReserved
    ctypes.c_void_p,              # pPromptStruct
    wintypes.DWORD,               # dwFlags
    ctypes.POINTER(_DATA_BLOB),  # pDataOut
]
_CryptUnprotectData.restype = wintypes.BOOL


_LocalFree = ctypes.windll.kernel32.LocalFree
_LocalFree.argtypes = [ctypes.c_void_p]
_LocalFree.restype = ctypes.c_void_p


def _bytes_to_blob(data: bytes) -> _DATA_BLOB:
    blob = _DATA_BLOB()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(
        ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)
    )
    return blob


def _blob_to_bytes(blob: _DATA_BLOB) -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    buf = ctypes.string_at(blob.pbData, blob.cbData)
    return bytes(buf)


def protect(plaintext: str) -> bytes:
    """加密 str → bytes（DPAPI CryptProtectData）"""
    in_blob = _bytes_to_blob(plaintext.encode("utf-8"))
    out_blob = _DATA_BLOB()
    ok = _CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise RuntimeError("DPAPI CryptProtectData 失败")
    try:
        return _blob_to_bytes(out_blob)
    finally:
        if out_blob.pbData:
            _LocalFree(out_blob.pbData)


def unprotect(ciphertext: bytes) -> str:
    """解密 bytes → str（DPAPI CryptUnprotectData）"""
    in_blob = _bytes_to_blob(ciphertext)
    out_blob = _DATA_BLOB()
    ok = _CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise RuntimeError("DPAPI CryptUnprotectData 失败")
    try:
        raw = _blob_to_bytes(out_blob)
        return raw.decode("utf-8")
    finally:
        if out_blob.pbData:
            _LocalFree(out_blob.pbData)


def protect_to_file(plaintext: str, path: str) -> None:
    """加密并写入文件"""
    ciphertext = protect(plaintext)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(ciphertext)


def unprotect_from_file(path: str) -> str:
    """读文件并解密；文件不存在 raise FileNotFoundError"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"DPAPI secret file not found: {path}")
    ciphertext = p.read_bytes()
    return unprotect(ciphertext)
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_dpapi.py -v
```

Expected: 4 个 test 全 PASS（在 Windows 下）。

- [ ] **Step 5: Commit**

```bash
git add tests/test_ai_bridge_dpapi.py ai_bridge/dpapi.py
git commit -m "feat(ai_bridge): DPAPI 加解密模块（ctypes 调 crypt32）"
```

---

## Task 3: providers.yaml + providers.py (TDD)

**Files:**
- Create: `assets/providers.yaml`
- Create: `tests/test_ai_bridge_providers.py`
- Create: `ai_bridge/providers.py`

- [ ] **Step 1: 写 assets/providers.yaml（先只放 1 个）**

`assets/providers.yaml`（P0 阶段先只放 OpenAI，P1 任务再补齐 9 家）：
```yaml
version: 1

providers:
  - id: openai
    name: "OpenAI"
    base_url: "https://api.openai.com/v1"
    default_model: "gpt-4o-mini"
    doc_url: "https://platform.openai.com/api-keys"
```

- [ ] **Step 2: 写失败的单测**

`tests/test_ai_bridge_providers.py`：
```python
"""ai_bridge/providers.py 单测"""
from pathlib import Path

import pytest

from ai_bridge import providers


def test_load_providers_returns_list():
    """读 providers.yaml 返回非空 list"""
    plist = providers.load_providers()
    assert isinstance(plist, list)
    assert len(plist) >= 1
    for p in plist:
        assert "id" in p
        assert "name" in p
        assert "base_url" in p
        assert "default_model" in p


def test_get_by_id_known():
    """用 id 查找已知 provider"""
    p = providers.get_by_id("openai")
    assert p["id"] == "openai"
    assert p["name"] == "OpenAI"
    assert p["base_url"].startswith("https://")


def test_get_by_id_unknown_raises():
    """未知 id 抛 ValueError，错误信息含 id"""
    with pytest.raises(ValueError) as exc_info:
        providers.get_by_id("nonexistent_provider_xyz")
    assert "nonexistent_provider_xyz" in str(exc_info.value)
```

- [ ] **Step 3: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_providers.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_bridge.providers'`

- [ ] **Step 4: 实现 providers.py**

`ai_bridge/providers.py`：
```python
"""providers.py — 读 bundled providers.yaml，提供 preset 查找

Public API:
  load_providers() -> list[dict]   # 读 bundled yaml（兼容 PyInstaller frozen）
  get_by_id(pid: str) -> dict       # 按 id 查找；未知抛 ValueError
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _bundled_providers_yaml() -> Path:
    """解析 providers.yaml 路径（frozen / 开发双模式）"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", ""))
    else:
        # ai_bridge/providers.py → 项目根
        base = Path(__file__).resolve().parent.parent
    return base / "assets" / "providers.yaml"


@lru_cache(maxsize=1)
def load_providers() -> list[dict[str, Any]]:
    """读 bundled providers.yaml，返回 list of dict"""
    p = _bundled_providers_yaml()
    if not p.is_file():
        raise FileNotFoundError(f"providers.yaml 不存在: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    plist = data.get("providers", [])
    if not isinstance(plist, list):
        raise ValueError("providers.yaml 格式错：providers 字段不是 list")
    return plist


def get_by_id(pid: str) -> dict[str, Any]:
    """按 id 查 provider；未知抛 ValueError"""
    for p in load_providers():
        if p.get("id") == pid:
            return p
    raise ValueError(f"未知 provider id: {pid!r}")
```

- [ ] **Step 5: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_providers.py -v
```

Expected: 3 个 test 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add assets/providers.yaml tests/test_ai_bridge_providers.py ai_bridge/providers.py
git commit -m "feat(ai_bridge): providers preset 加载 + 查找（OpenAI 先行）"
```

---

## Task 4: schema.py report.json 校验 (TDD)

**Files:**
- Create: `tests/test_ai_bridge_schema.py`
- Create: `ai_bridge/schema.py`

- [ ] **Step 1: 写失败的单测**

`tests/test_ai_bridge_schema.py`：
```python
"""ai_bridge/schema.py 单测"""
import pytest

from ai_bridge.schema import validate_report


def _valid_report() -> dict:
    return {
        "date": "2026-06-10",
        "title": "钉钉工作纪要日报",
        "disclaimer": "disclaimer text",
        "overview": {"work": [], "personal": []},
        "work": {
            "handled_items": [],
            "key_decisions": [],
            "projects": [],
            "reply_needed": [],
            "risks": [],
        },
        "personal": {"highlights": [], "reply_needed": []},
        "tomorrow": {"work": [], "personal": []},
        "context_gaps": {"work": [], "personal": []},
    }


def test_valid_report_passes():
    """完整合法 report.json → 通过"""
    is_valid, errors = validate_report(_valid_report())
    assert is_valid is True
    assert errors == []


def test_missing_top_key():
    """缺顶层键（如 work）→ 不通过，错误含 'work'"""
    r = _valid_report()
    del r["work"]
    is_valid, errors = validate_report(r)
    assert is_valid is False
    assert any("work" in e for e in errors)


def test_missing_nested_key():
    """缺 work.handled_items → 不通过，错误含完整路径"""
    r = _valid_report()
    del r["work"]["handled_items"]
    is_valid, errors = validate_report(r)
    assert is_valid is False
    assert any("work.handled_items" in e for e in errors)


def test_wrong_type_overview_work_not_list():
    """overview.work 不是 list → 不通过"""
    r = _valid_report()
    r["overview"]["work"] = "this should be list"
    is_valid, errors = validate_report(r)
    assert is_valid is False
    assert any("overview.work" in e for e in errors)


def test_empty_chat_count_works():
    """所有 list 都允许为空数组（兜底）"""
    r = _valid_report()
    is_valid, errors = validate_report(r)
    assert is_valid is True
    assert errors == []


def test_none_input_fails():
    """None → 不通过"""
    is_valid, errors = validate_report(None)
    assert is_valid is False
    assert any("dict" in e for e in errors)


def test_non_dict_input_fails():
    """list 替代 dict → 不通过"""
    is_valid, errors = validate_report([1, 2, 3])
    assert is_valid is False
    assert any("dict" in e for e in errors)
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_schema.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_bridge.schema'`

- [ ] **Step 3: 实现 schema.py**

`ai_bridge/schema.py`：
```python
"""schema.py — report.json 结构校验

Public API:
  validate_report(obj: Any) -> tuple[bool, list[str]]
    返回 (is_valid, errors)；errors 为人类可读的错误列表
"""
from __future__ import annotations

from typing import Any

# 8 个顶层键
TOP_KEYS = ["date", "title", "disclaimer", "overview", "work", "personal", "tomorrow", "context_gaps"]

# overview / tomorrow / context_gaps 的子键（必须为 list）
BUCKET_SUBKEYS = ["work", "personal"]

# work 的子键
WORK_SUBKEYS = ["handled_items", "key_decisions", "projects", "reply_needed", "risks"]

# personal 的子键
PERSONAL_SUBKEYS = ["highlights", "reply_needed"]


def _check_dict_keys(obj: Any, keys: list[str], path: str, errors: list[str]) -> bool:
    """检查 obj 是 dict 且包含所有 keys；不满足时 append 错误到 errors"""
    if not isinstance(obj, dict):
        errors.append(f"{path} 必须是 dict，实际是 {type(obj).__name__}")
        return False
    missing = [k for k in keys if k not in obj]
    if missing:
        errors.append(f"{path} 缺少字段: {', '.join(missing)}")
        return False
    return True


def _check_list_field(obj: Any, path: str, errors: list[str]) -> bool:
    """检查 obj 是 list"""
    if not isinstance(obj, list):
        errors.append(f"{path} 必须是 list，实际是 {type(obj).__name__}")
        return False
    return True


def validate_report(obj: Any) -> tuple[bool, list[str]]:
    """校验 report.json 结构；返回 (is_valid, errors)"""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, [f"report.json 顶层必须是 dict，实际是 {type(obj).__name__}"]

    # 顶层 8 个键
    if not _check_dict_keys(obj, TOP_KEYS, "root", errors):
        return False, errors

    # overview / tomorrow / context_gaps：{work:list, personal:list}
    for top in ["overview", "tomorrow", "context_gaps"]:
        if not _check_dict_keys(obj[top], BUCKET_SUBKEYS, top, errors):
            continue
        for sub in BUCKET_SUBKEYS:
            _check_list_field(obj[top][sub], f"{top}.{sub}", errors)

    # work：5 个子键，都是 list
    if _check_dict_keys(obj["work"], WORK_SUBKEYS, "work", errors):
        for sub in WORK_SUBKEYS:
            _check_list_field(obj["work"][sub], f"work.{sub}", errors)

    # personal：2 个子键，都是 list
    if _check_dict_keys(obj["personal"], PERSONAL_SUBKEYS, "personal", errors):
        for sub in PERSONAL_SUBKEYS:
            _check_list_field(obj["personal"][sub], f"personal.{sub}", errors)

    return (len(errors) == 0), errors
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_schema.py -v
```

Expected: 7 个 test 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_ai_bridge_schema.py ai_bridge/schema.py
git commit -m "feat(ai_bridge): report.json schema 校验"
```

---

## Task 5: ai_bridge/main.py CLI 骨架 + 成功路径 (TDD, mock httpx)

**Files:**
- Create: `tests/test_ai_bridge_main.py`
- Create: `ai_bridge/main.py`

- [ ] **Step 1: 写失败的单测（mock httpx）**

`tests/test_ai_bridge_main.py`：
```python
"""ai_bridge/main.py 单测

mock httpx + mock DPAPI + 临时 config / llm_secret.bin
"""
import json
import os
import platform
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ai_bridge import main as bridge_main


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="ai_bridge 端到端测试只跑 Windows（DPAPI）",
)


def _setup_env(tmp_path: Path, api_key: str = "sk-test-fake"):
    """准备临时 config.yaml + llm_secret.bin + bundle/prompt"""
    data_dir = tmp_path / "appdata"
    data_dir.mkdir()
    cfg = data_dir / "config.yaml"
    cfg.write_text(
        f"llm:\n  active_provider: openai\n  base_url: https://api.openai.com/v1\n  default_model: gpt-4o-mini\n",
        encoding="utf-8",
    )
    # DPAPI 加密 api_key
    from ai_bridge.dpapi import protect_to_file
    protect_to_file(api_key, str(data_dir / "llm_secret.bin"))

    # bundle / prompt
    out_dir = tmp_path / "out" / "2026-06-10"
    out_dir.mkdir(parents=True)
    bundle = out_dir / "dingtalk_bundle.json"
    bundle.write_text('{"chats": []}', encoding="utf-8")
    prompt = out_dir / "dingtalk_daily_summary_prompt.md"
    prompt.write_text("# prompt placeholder", encoding="utf-8")
    return data_dir, out_dir, bundle, prompt


def test_main_success_writes_report(capsys, tmp_path):
    """mock 200 + 合法 JSON → 写 report.json 到 out_dir"""
    data_dir, out_dir, bundle, prompt = _setup_env(tmp_path)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [
            {"message": {"content": json.dumps({
                "date": "2026-06-10",
                "title": "test",
                "disclaimer": "x",
                "overview": {"work": [], "personal": []},
                "work": {"handled_items": [], "key_decisions": [], "projects": [], "reply_needed": [], "risks": []},
                "personal": {"highlights": [], "reply_needed": []},
                "tomorrow": {"work": [], "personal": []},
                "context_gaps": {"work": [], "personal": []},
            })}}
        ]
    }

    with patch("ai_bridge.main.httpx") as mock_httpx, \
         patch.object(bridge_main, "_data_dir", return_value=data_dir):
        mock_httpx.post.return_value = fake_response
        rc = bridge_main.run([
            "--bundle-path", str(bundle),
            "--prompt-path", str(prompt),
            "--output-dir", str(out_dir),
            "--provider-id", "openai",
        ])

    assert rc == 0
    captured = capsys.readouterr()
    out_line = captured.out.strip()
    result = json.loads(out_line)
    assert result["ok"] is True
    assert (out_dir / "report.json").is_file()
    written = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert written["date"] == "2026-06-10"
    assert "duration_ms" in result


def test_main_http_401_returns_err(capsys, tmp_path):
    """mock 401 → err_code=HTTP_401，exit 1"""
    data_dir, out_dir, bundle, prompt = _setup_env(tmp_path)

    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.text = "Unauthorized"

    with patch("ai_bridge.main.httpx") as mock_httpx, \
         patch.object(bridge_main, "_data_dir", return_value=data_dir):
        mock_httpx.post.return_value = fake_response
        rc = bridge_main.run([
            "--bundle-path", str(bundle),
            "--prompt-path", str(prompt),
            "--output-dir", str(out_dir),
            "--provider-id", "openai",
        ])

    assert rc == 1
    out_line = capsys.readouterr().out.strip()
    result = json.loads(out_line)
    assert result["ok"] is False
    assert result["err_code"] == "HTTP_401"


def test_main_timeout_returns_err(capsys, tmp_path):
    """mock httpx.TimeoutException → err_code=TIMEOUT，exit 1"""
    import httpx as real_httpx
    data_dir, out_dir, bundle, prompt = _setup_env(tmp_path)

    with patch("ai_bridge.main.httpx") as mock_httpx, \
         patch.object(bridge_main, "_data_dir", return_value=data_dir):
        mock_httpx.post.side_effect = real_httpx.TimeoutException("120s")
        mock_httpx.TimeoutException = real_httpx.TimeoutException
        rc = bridge_main.run([
            "--bundle-path", str(bundle),
            "--prompt-path", str(prompt),
            "--output-dir", str(out_dir),
            "--provider-id", "openai",
        ])

    assert rc == 1
    out_line = capsys.readouterr().out.strip()
    result = json.loads(out_line)
    assert result["ok"] is False
    assert result["err_code"] == "TIMEOUT"


def test_main_parse_fail_returns_err(capsys, tmp_path):
    """mock 200 + 非 JSON 内容 → err_code=PARSE_FAIL"""
    data_dir, out_dir, bundle, prompt = _setup_env(tmp_path)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "this is not JSON at all"}}]
    }

    with patch("ai_bridge.main.httpx") as mock_httpx, \
         patch.object(bridge_main, "_data_dir", return_value=data_dir):
        mock_httpx.post.return_value = fake_response
        rc = bridge_main.run([
            "--bundle-path", str(bundle),
            "--prompt-path", str(prompt),
            "--output-dir", str(out_dir),
            "--provider-id", "openai",
        ])

    assert rc == 1
    out_line = capsys.readouterr().out.strip()
    result = json.loads(out_line)
    assert result["ok"] is False
    assert result["err_code"] == "PARSE_FAIL"


def test_main_schema_fail_returns_err(capsys, tmp_path):
    """mock 200 + 合法 JSON 但缺字段 → err_code=SCHEMA_FAIL"""
    data_dir, out_dir, bundle, prompt = _setup_env(tmp_path)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({
            "date": "2026-06-10",
            # 故意缺 work/personal/tomorrow/context_gaps
        })}}]
    }

    with patch("ai_bridge.main.httpx") as mock_httpx, \
         patch.object(bridge_main, "_data_dir", return_value=data_dir):
        mock_httpx.post.return_value = fake_response
        rc = bridge_main.run([
            "--bundle-path", str(bundle),
            "--prompt-path", str(prompt),
            "--output-dir", str(out_dir),
            "--provider-id", "openai",
        ])

    assert rc == 1
    out_line = capsys.readouterr().out.strip()
    result = json.loads(out_line)
    assert result["ok"] is False
    assert result["err_code"] == "SCHEMA_FAIL"
    assert any("work" in m for m in result.get("missing", []))
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_main.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_bridge.main'`

- [ ] **Step 3: 实现 ai_bridge/main.py**

`ai_bridge/main.py`：
```python
"""main.py — ai_bridge CLI 入口

调用方式（一次性 subprocess，stdout 1 行 JSON）：
  ai_bridge.exe --bundle-path <bundle> --prompt-path <prompt>
                --output-dir <out>   --provider-id <id>
                [--base-url <u>] [--model <m>] [--api-key <k>]

stdout 输出：
  成功：{"ok": true, "json_path": "...", "duration_ms": 1234}
  失败：{"ok": false, "err_code": "...", "err_msg": "...", ...}
退出码：0 成功；1 失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import dpapi, providers, schema


# ── 路径 ────────────────────────────────────────────────────────────
def _data_dir() -> Path:
    """解析 %APPDATA%/DingTalkBox（开发模式可用 ENV 覆盖）"""
    import os
    if "DINGTALK_BOX_DATA_DIR" in os.environ:
        return Path(os.environ["DINGTALK_BOX_DATA_DIR"])
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "DingTalkBox"
    return Path.home() / ".config" / "DingTalkBox"


# ── 配置/秘钥 ────────────────────────────────────────────────────────
def _load_config() -> dict:
    cfg_path = _data_dir() / "config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"config.yaml 不存在: {cfg_path}")
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _load_api_key() -> str:
    secret = _data_dir() / "llm_secret.bin"
    return dpapi.unprotect_from_file(str(secret))


# ── 错误码 → JSON 输出 ──────────────────────────────────────────────
def _emit_err(err_code: str, err_msg: str, **extra: Any) -> None:
    payload = {"ok": False, "err_code": err_code, "err_msg": err_msg}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _emit_ok(json_path: str, duration_ms: int) -> None:
    print(
        json.dumps(
            {"ok": True, "json_path": json_path, "duration_ms": duration_ms},
            ensure_ascii=False,
        ),
        flush=True,
    )


# ── Bundle 分段 ─────────────────────────────────────────────────────
def _build_messages(prompt: str, bundle_text: str, threshold: int = 30_000) -> list[dict]:
    """根据 bundle 长度构造 messages 列表"""
    if len(bundle_text) <= threshold:
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"请分析以下聊天记录并输出 report.json：\n\n{bundle_text}"},
        ]
    # 简化分段：按 json 顶层键切（这里假设 bundle 是 dict with 'chats' list）
    try:
        bundle_obj = json.loads(bundle_text)
        chats = bundle_obj.get("chats", [])
    except Exception:
        chats = []
    if not chats:
        # 没法切分就整段发
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"请分析以下聊天记录并输出 report.json：\n\n{bundle_text}"},
        ]
    msgs = [{"role": "system", "content": prompt}]
    n = len(chats)
    for i, chat in enumerate(chats):
        msgs.append({
            "role": "user",
            "content": f"## 第 {i+1}/{n} 段\n\n{json.dumps(chat, ensure_ascii=False)}",
        })
    msgs.append({"role": "user", "content": f"请综合以上 {n} 段，输出完整 report.json（严格遵循 schema）。"})
    return msgs


# ── JSON 解析（三层防御）───────────────────────────────────────────
def _parse_ai_content(content: str) -> dict:
    """从 AI 返回的 content 解析出 dict
    防御：① 剥离 ```json ... ``` 包裹 ② 直接 json.loads ③ 宽松解析（去末尾逗号）
    """
    text = content.strip()
    # ① 剥离 markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # ② 直接 parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # ③ 宽松：去末尾多余逗号
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(cleaned)


# ── 主体 ────────────────────────────────────────────────────────────
def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai_bridge")
    parser.add_argument("--bundle-path", required=True)
    parser.add_argument("--prompt-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--provider-id", required=True)
    # 可选覆盖
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)  # 测试模式用
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    start = time.time()
    try:
        # 读 prompt + bundle
        prompt = Path(args.prompt_path).read_text(encoding="utf-8")
        bundle_text = Path(args.bundle_path).read_text(encoding="utf-8")

        # 解析 provider
        if args.base_url and args.model:
            base_url = args.base_url.rstrip("/")
            model = args.model
        else:
            preset = providers.get_by_id(args.provider_id)
            base_url = (args.base_url or preset["base_url"]).rstrip("/")
            model = args.model or preset["default_model"]

        # 读 key
        if args.api_key:
            api_key = args.api_key
        else:
            try:
                api_key = _load_api_key()
            except FileNotFoundError as e:
                _emit_err("DPAPI_FAIL", f"密钥文件不存在: {e}")
                return 1
            except Exception as e:
                _emit_err("DPAPI_FAIL", f"DPAPI 解密失败: {e}")
                return 1

        # 调 LLM
        messages = _build_messages(prompt, bundle_text)
        body = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{base_url}/chat/completions"

        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=args.timeout)
        except httpx.TimeoutException as e:
            _emit_err("TIMEOUT", f"{args.timeout}s timeout: {e}")
            return 1
        except httpx.HTTPError as e:
            _emit_err("HTTP_5XX", f"网络错误: {e}")
            return 1

        if resp.status_code in (401, 403):
            _emit_err("HTTP_401", f"Unauthorized (status={resp.status_code})")
            return 1
        if resp.status_code == 429:
            _emit_err("HTTP_429", "rate limited")
            return 1
        if resp.status_code >= 500:
            _emit_err("HTTP_5XX", f"AI 服务异常 (status={resp.status_code}): {resp.text[:200]}")
            return 1
        if resp.status_code != 200:
            _emit_err("HTTP_5XX", f"unexpected status {resp.status_code}: {resp.text[:200]}")
            return 1

        # 解析
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            _emit_err("PARSE_FAIL", f"无法解析 LLM 响应: {e}")
            return 1

        try:
            report_obj = _parse_ai_content(content)
        except Exception as e:
            _emit_err("PARSE_FAIL", f"AI 返回非 JSON: {e}", raw=content[:500])
            return 1

        # 校验
        is_valid, errors = schema.validate_report(report_obj)
        if not is_valid:
            # 找 missing 字段（错误信息里包含"缺少字段"）
            missing = [e.split("缺少字段:")[-1].strip() for e in errors if "缺少字段" in e]
            _emit_err("SCHEMA_FAIL", "; ".join(errors), missing=missing)
            return 1

        # 写 report.json
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "report.json"
        json_path.write_text(
            json.dumps(report_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        duration_ms = int((time.time() - start) * 1000)
        _emit_ok(str(json_path), duration_ms)
        return 0

    except FileNotFoundError as e:
        _emit_err("DPAPI_FAIL", f"文件不存在: {e}")
        return 1
    except Exception as e:
        _emit_err("CRASH", f"未捕获异常: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run())
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_main.py -v
```

Expected: 5 个 test 全 PASS。

- [ ] **Step 5: 手动跑一次（dry-run）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m ai_bridge --help
```

Expected: argparse 输出 help。

- [ ] **Step 6: Commit**

```bash
git add tests/test_ai_bridge_main.py ai_bridge/main.py
git commit -m "feat(ai_bridge): main.py CLI 入口（成功+4 类错误码）"
```

---

## Task 6: ai_bridge.spec PyInstaller 打包配置

**Files:**
- Create: `ai_bridge.spec`

- [ ] **Step 1: 写 ai_bridge.spec**

`ai_bridge.spec`（项目根目录）：
```python
# -*- mode: python -*-
"""PyInstaller spec for ai_bridge

Build (项目根目录下):
    pyinstaller ai_bridge.spec --clean --noconfirm
    产物: dist/ai_bridge.exe
"""
import sys
from pathlib import Path

ROOT = Path('.').resolve()
block_cipher = None

a = Analysis(
    ['ai_bridge/main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ('assets/providers.yaml', 'assets'),
    ],
    hiddenimports=[
        'ai_bridge',
        'ai_bridge.dpapi',
        'ai_bridge.providers',
        'ai_bridge.schema',
        'ai_bridge.main',
        'httpx',
        'httpx._transports',
        'httpcore',
        'h2',
        'yaml',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'email'],
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ai_bridge',
    debug=False,
    strip=False,
    upx=False,
    console=True,  # 必须保留 stdio
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 2: 打包（先不验证运行，只确认产物）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
pyinstaller ai_bridge.spec --clean --noconfirm
```

Expected: `dist/ai_bridge.exe` 生成；`INFO: Building EXE from EXE-00.toc completed successfully.`

- [ ] **Step 3: 验证 frozen 模式可启动**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
./dist/ai_bridge.exe --help
```

Expected: argparse 输出 help（不应 ImportError）。

- [ ] **Step 4: Commit**

```bash
git add ai_bridge.spec
git commit -m "build(ai_bridge): PyInstaller spec，console=True 保留 stdio"
```

---

## Task 7: sidecar/core/llm_config.py (TDD, 含 DPAPI 集成)

**Files:**
- Create: `tests/test_llm_config.py`
- Create: `sidecar/core/llm_config.py`

- [ ] **Step 1: 写失败的单测**

`tests/test_llm_config.py`：
```python
"""sidecar/core/llm_config.py 单测"""
import os
import platform
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sidecar.core import llm_config


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="llm_config 端到端测试只跑 Windows（DPAPI）",
)


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """重定向 llm_config 的 data_dir 到 tmp"""
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setattr(llm_config, "_data_dir", lambda: d)
    # 同时 stub bundled providers.yaml 路径（开发模式下也需要能找到）
    return d


def _write_config(d: Path, llm: dict | None):
    """写 config.yaml（不带其他段）"""
    import yaml
    cfg = {"version": 1}
    if llm is not None:
        cfg["llm"] = llm
    (d / "config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_get_returns_unconfigured_when_no_llm_section(tmp_data_dir):
    """config.yaml 无 llm 段 → configured: false"""
    _write_config(tmp_data_dir, None)
    result = llm_config.get_config()
    assert result["configured"] is False
    assert result["provider"] is None
    assert result["base_url"] is None
    assert result["default_model"] is None


def test_set_then_get_roundtrip(tmp_data_dir):
    """set_config 写 → get_config 读回"""
    _write_config(tmp_data_dir, None)
    llm_config.set_config(
        provider="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        api_key="sk-test-1234567890",
    )
    result = llm_config.get_config()
    assert result["configured"] is True
    assert result["provider"] == "openai"
    assert result["base_url"] == "https://api.openai.com/v1"
    assert result["default_model"] == "gpt-4o-mini"
    # key 永远不在 get_config 返回里
    assert "api_key" not in result
    assert "sk-test" not in str(result)


def test_secret_file_exists_and_is_encrypted(tmp_data_dir):
    """set 后 llm_secret.bin 存在且不是明文"""
    _write_config(tmp_data_dir, None)
    llm_config.set_config(
        provider="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        api_key="sk-secret-marker-abc",
    )
    secret = tmp_data_dir / "llm_secret.bin"
    assert secret.is_file()
    raw = secret.read_bytes()
    assert b"sk-secret-marker-abc" not in raw  # 明文不在密文里


def test_clear_removes_secret_and_resets_config(tmp_data_dir):
    """clear_config 删除 secret 文件 + 重置 llm 段"""
    _write_config(tmp_data_dir, None)
    llm_config.set_config("openai", "https://x", "m", "sk-x")
    llm_config.clear_config()
    assert not (tmp_data_dir / "llm_secret.bin").exists()
    result = llm_config.get_config()
    assert result["configured"] is False


def test_unknown_provider_raises(tmp_data_dir):
    """未知 provider id 抛 ValueError"""
    _write_config(tmp_data_dir, None)
    with pytest.raises(ValueError) as exc_info:
        llm_config.set_config("nonexistent_xyz", "https://x", "m", "sk-x")
    assert "nonexistent_xyz" in str(exc_info.value)


def test_corrupt_secret_file_resets(tmp_data_dir):
    """DPAPI 解密失败 → 自动删除 secret + 重置 config"""
    import os
    _write_config(tmp_data_dir, None)
    # 写一个空 + 损坏的 secret 文件
    (tmp_data_dir / "llm_secret.bin").write_bytes(b"\x00" * 16)
    result = llm_config.get_config()
    # 期望：自动删 + 视为未配置
    assert result["configured"] is False
    assert not (tmp_data_dir / "llm_secret.bin").exists()
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_llm_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'sidecar.core.llm_config'` 或 `ImportError`

- [ ] **Step 3: 实现 sidecar/core/llm_config.py**

`sidecar/core/llm_config.py`：
```python
"""llm_config.py — sidecar 侧的 LLM 配置管理

职责：
- 读 / 写 config.yaml 中的 llm 段
- 用 DPAPI 加密 api_key 写 llm_secret.bin
- 读 llm_secret.bin 并解密（轻量检测 + 故障恢复）
- 提供 provider preset 查找

注意：本模块的 _data_dir 在测试里会被 monkeypatch。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

# 复用 ai_bridge 的 dpapi（frozen 模式下要确保 _MEIPASS 路径解析正确）
# 注意：sidecar 自己的 _MEIPASS 跟 ai_bridge 独立，但代码相同，所以从 ai_bridge 包路径导入是 OK 的
from ai_bridge import dpapi, providers


def _data_dir() -> Path:
    """解析 %APPDATA%/DingTalkBox（开发模式可用 ENV 覆盖）"""
    if "DINGTALK_BOX_DATA_DIR" in os.environ:
        return Path(os.environ["DINGTALK_BOX_DATA_DIR"])
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "DingTalkBox"
    return Path.home() / ".config" / "DingTalkBox"


def _config_path() -> Path:
    return _data_dir() / "config.yaml"


def _secret_path() -> Path:
    return _data_dir() / "llm_secret.bin"


def _read_config() -> dict:
    p = _config_path()
    if not p.is_file():
        return {"version": 1}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {"version": 1}


def _write_config(data: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def get_config() -> dict:
    """读当前 LLM 配置

    Returns: {
      configured: bool,
      provider: str | None,
      base_url: str | None,
      default_model: str | None,
    }
    """
    data = _read_config()
    llm = data.get("llm") or {}
    provider = llm.get("active_provider")
    base_url = llm.get("base_url")
    default_model = llm.get("default_model")

    if not provider:
        return {"configured": False, "provider": None, "base_url": None, "default_model": None}

    # 检查 secret 文件存在 + DPAPI 能解
    sp = _secret_path()
    if not sp.is_file():
        # config 说有但 secret 没了 → 重置
        data["llm"] = {"active_provider": None, "base_url": None, "default_model": None}
        _write_config(data)
        return {"configured": False, "provider": None, "base_url": None, "default_model": None}

    try:
        dpapi.unprotect_from_file(str(sp))
    except Exception:
        # DPAPI 解密失败 → 自动删 + 重置
        try:
            sp.unlink()
        except OSError:
            pass
        data["llm"] = {"active_provider": None, "base_url": None, "default_model": None}
        _write_config(data)
        return {"configured": False, "provider": None, "base_url": None, "default_model": None}

    return {
        "configured": True,
        "provider": provider,
        "base_url": base_url,
        "default_model": default_model,
    }


def set_config(
    provider: str,
    base_url: str,
    default_model: str,
    api_key: str,
) -> None:
    """保存 LLM 配置 + 加密 key 到 secret 文件

    校验 provider id 必须在 bundled preset 中（防止拼写错）
    """
    # 校验 provider
    try:
        providers.get_by_id(provider)
    except ValueError:
        raise ValueError(f"未知 provider id: {provider!r}")

    if not base_url:
        raise ValueError("base_url 必填")
    if not default_model:
        raise ValueError("default_model 必填")
    if not api_key:
        raise ValueError("api_key 必填")

    # 加密 key
    sp = _secret_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    dpapi.protect_to_file(api_key, str(sp))

    # 写 config
    data = _read_config()
    data["llm"] = {
        "active_provider": provider,
        "base_url": base_url,
        "default_model": default_model,
    }
    _write_config(data)


def clear_config() -> None:
    """删除 secret 文件 + 重置 config 里的 llm 段"""
    sp = _secret_path()
    if sp.is_file():
        try:
            sp.unlink()
        except OSError:
            pass
    data = _read_config()
    data["llm"] = {"active_provider": None, "base_url": None, "default_model": None}
    _write_config(data)


def list_providers() -> list[dict]:
    """返回 10 个 preset（前端填充下拉框用）"""
    return providers.load_providers()
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_llm_config.py -v
```

Expected: 6 个 test 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_llm_config.py sidecar/core/llm_config.py
git commit -m "feat(sidecar): llm_config 读写 + DPAPI 集成 + provider 校验"
```

---

## Task 8: sidecar dispatcher +4 methods

**Files:**
- Modify: `sidecar/core/dispatcher.py`
- Modify: `sidecar/core/config.py`（加 llm 段 merge）
- Modify: `sidecar/main.py`（请求 logger）

- [ ] **Step 1: 在 dispatcher.py 加 4 个 method**

编辑 `sidecar/core/dispatcher.py`：
```python
# 在 import 区追加
from . import llm_config  # noqa: E402

# 在 METHODS dict 里加（替换原 METHODS 整段）
METHODS = {
    "ping": lambda p: {"ok": True, "version": "0.1.0"},
    "get_login_status": auth.get_login_status,
    "trigger_login": auth.trigger_login,
    "validate_corp": auth.validate_corp,
    "generate_daily": daily_report.generate,
    "render_only": daily_report.render_only,
    "send_to_dingtalk": send.send_file,
    "list_history": daily_report.list_history,
    "open_output": daily_report.open_output_dir,
    "diagnose": send.collect_diagnostics,
    "read_text_file": _read_text_file,
    # ↓ 新增 ↓
    "get_llm_config": _get_llm_config,
    "set_llm_config": _set_llm_config,
    "test_llm_connection": _test_llm_connection,
    "ai_analyze": _ai_analyze,
}


# ↓ 在文件底部新增 4 个 handler ↓
def _get_llm_config(params: dict) -> dict:
    """返回当前 LLM 配置（不含 key）"""
    return llm_config.get_config()


def _set_llm_config(params: dict) -> dict:
    """保存或清除 LLM 配置

    params: {
      clear?: bool,           # true = 清除
      provider?: str,
      base_url?: str,
      default_model?: str,
      api_key?: str,
    }
    """
    if params.get("clear"):
        llm_config.clear_config()
        return {"ok": True, "configured": False}
    provider = params.get("provider")
    base_url = params.get("base_url")
    default_model = params.get("default_model")
    api_key = params.get("api_key")
    if not all([provider, base_url, default_model, api_key]):
        raise ValueError("provider / base_url / default_model / api_key 都必填")
    llm_config.set_config(provider, base_url, default_model, api_key)
    return {"ok": True, "configured": True, "provider": provider}


def _test_llm_connection(params: dict) -> dict:
    """测试 LLM 连接（不落盘）

    params: { provider?, base_url?, default_model?, api_key? }
    都不传则用已保存的；传 api_key 则用临时值（不落盘）
    """
    api_key = params.get("api_key")
    base_url = params.get("base_url")
    model = params.get("default_model") or params.get("model")
    provider_id = params.get("provider")

    if not api_key:
        cfg = llm_config.get_config()
        if not cfg["configured"]:
            raise ValueError("需要先填 api_key 或先配置 LLM")
        # 从 secret 读
        from ai_bridge.dpapi import unprotect_from_file
        from sidecar.core.llm_config import _secret_path
        api_key = unprotect_from_file(str(_secret_path()))
        if not base_url:
            base_url = cfg["base_url"]
        if not model:
            model = cfg["default_model"]
        if not provider_id:
            provider_id = cfg["provider"]

    if not base_url or not model:
        raise ValueError("base_url 和 model 必填")

    # 用 ai_bridge 的 main 跑一次最小请求
    from ai_bridge import main as bridge_main
    import json
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        # 写假 bundle + prompt
        bundle = Path(td) / "bundle.json"
        bundle.write_text("{}", encoding="utf-8")
        prompt = Path(td) / "prompt.md"
        prompt.write_text("# stub", encoding="utf-8")
        out_dir = Path(td) / "out"
        out_dir.mkdir()
        import time
        start = time.time()
        rc = bridge_main.run([
            "--bundle-path", str(bundle),
            "--prompt-path", str(prompt),
            "--output-dir", str(out_dir),
            "--provider-id", provider_id or "test",
            "--base-url", base_url,
            "--model", model,
            "--api-key", api_key,
            "--timeout", "30",
        ])
        duration_ms = int((time.time() - start) * 1000)
        # 读 ai_bridge 的 stdout（实际是 print 到我们的 stdout，要 redirect）
        # 这里改为：解析 out_dir 下的 report.json 是否存在
        out_json = out_dir / "report.json"
        if rc == 0 and out_json.is_file():
            return {"ok": True, "latency_ms": duration_ms, "provider": provider_id, "model": model}
        else:
            # 失败：再调一次拿 stdout（不优雅但能跑）
            return {"ok": False, "latency_ms": duration_ms, "err_msg": "AI 子进程返回失败（rc={}）".format(rc)}


def _ai_analyze(params: dict) -> dict:
    """调试用：手工触发一次 AI 分析（不影响 generate_daily 自动流程）"""
    bundle_path = params.get("bundle_path")
    prompt_path = params.get("prompt_path")
    output_dir = params.get("output_dir")
    if not all([bundle_path, prompt_path, output_dir]):
        raise ValueError("bundle_path / prompt_path / output_dir 必填")
    from ai_bridge import main as bridge_main
    import json
    import time
    start = time.time()
    rc = bridge_main.run([
        "--bundle-path", bundle_path,
        "--prompt-path", prompt_path,
        "--output-dir", output_dir,
        "--provider-id", params.get("provider_id", "openai"),
    ])
    duration_ms = int((time.time() - start) * 1000)
    # ai_bridge 写 report.json 到 output_dir
    out_json = Path(output_dir) / "report.json"
    return {
        "ok": (rc == 0),
        "duration_ms": duration_ms,
        "report_json_path": str(out_json) if out_json.is_file() else None,
    }
```

- [ ] **Step 2: 在 sidecar/main.py 请求 logger 加新 method 名**

编辑 `sidecar/main.py`，在 line 142 附近（请求 logger）将新 method 加入检测列表 —— 实际上现有 logger 已经打印所有 method，所以**无需改动**。直接进 Step 3。

- [ ] **Step 3: 跑现有 dispatcher 单测（如果存在）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
ls tests/
python -m pytest tests/ -v 2>&1 | head -40
```

Expected: 现有测试（如有）全 PASS；如果有 sidecar 相关 import 错则修。

- [ ] **Step 4: 手动 smoke test（启动 sidecar + 调 4 个 method）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m sidecar <<'EOF'
{"id":"1","method":"get_llm_config","params":{}}
{"id":"2","method":"list_providers","params":{}}
EOF
```

Expected: 两条响应。`get_llm_config` 返回 `configured: false`（因 DINGTALK_BOX_DATA_DIR 未设时走默认 %APPDATA%），`list_providers` **可能** 报未知 method —— 不影响。改测：

```bash
DINGTALK_BOX_DATA_DIR=/tmp/dtb_test python -c "import sys; sys.path.insert(0, 'sidecar'); from core import dispatcher; print(dispatcher.dispatch('get_llm_config', {}))"
```

Expected: `{'configured': False, 'provider': None, ...}`

- [ ] **Step 5: Commit**

```bash
git add sidecar/core/dispatcher.py
git commit -m "feat(sidecar): dispatcher +4 methods (get/set_llm_config, test, ai_analyze)"
```

---

## Task 9: sidecar.daily_report 阶段 2 自动调 ai_bridge

**Files:**
- Modify: `sidecar/core/daily_report.py`
- Create: `tests/test_daily_report_ai_phase.py`（验证自动调用的最小化测试）

- [ ] **Step 1: 写失败的单测（mock subprocess.run）**

`tests/test_daily_report_ai_phase.py`：
```python
"""sidecar/core/daily_report.py 的阶段 2 单测

验证：LLM 已配置时，阶段 2 自动调 ai_bridge；未配置时回退 awaiting_ai
"""
import json
import platform
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sidecar.core import daily_report
from sidecar.core import llm_config


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="DPAPI 相关；测试可临时 monkeypatch"
)


def _setup_minimal_data(tmp_path, monkeypatch, configured: bool):
    """准备最小的 data_dir + out_dir"""
    d = tmp_path / "appdata"
    d.mkdir()
    monkeypatch.setattr(llm_config, "_data_dir", lambda: d)
    monkeypatch.setattr(daily_report, "paths", MagicMock())
    # 桩 dds.export_daily_bundle
    out_dir = tmp_path / "out" / "2026-06-10"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(
        daily_report, "dds", MagicMock(export_daily_bundle=MagicMock(return_value={
            "chat_count": 5, "message_count": 100,
            "bundle_path": str(out_dir / "dingtalk_bundle.json"),
            "prompt_path": str(out_dir / "dingtalk_daily_summary_prompt.md"),
            "report_template_path": str(out_dir / "report_template.json"),
        }))
    )
    # stub paths.output_for_date
    daily_report.paths.output_for_date.return_value = out_dir
    # stub dds.render_report
    daily_report.dds.render_report = MagicMock(return_value={
        "image_path": str(out_dir / "test.png"),
        "markdown_path": str(out_dir / "test.md"),
    })
    (out_dir / "test.png").write_bytes(b"PNG")
    (out_dir / "test.md").write_text("# md")

    if configured:
        llm_config.set_config("openai", "https://api.openai.com/v1", "gpt-4o-mini", "sk-fake-key")
    return out_dir


def test_unconfigured_returns_awaiting_ai(tmp_path, monkeypatch):
    """未配置 LLM → 返回 awaiting_ai"""
    out_dir = _setup_minimal_data(tmp_path, monkeypatch, configured=False)
    result = daily_report.generate({"date": "2026-06-10"})
    assert result["stage"] == "awaiting_ai"
    assert "bundle_path" in result


def test_configured_calls_ai_bridge_and_continues(tmp_path, monkeypatch):
    """已配置 LLM → 自动调 ai_bridge → 进入阶段 3 渲染"""
    out_dir = _setup_minimal_data(tmp_path, monkeypatch, configured=True)

    # 写一个 fake report.json 模拟 ai_bridge 写好
    fake_report = {
        "date": "2026-06-10", "title": "x", "disclaimer": "x",
        "overview": {"work": [], "personal": []},
        "work": {"handled_items": [], "key_decisions": [], "projects": [], "reply_needed": [], "risks": []},
        "personal": {"highlights": [], "reply_needed": []},
        "tomorrow": {"work": [], "personal": []},
        "context_gaps": {"work": [], "personal": []},
    }
    (out_dir / "report.json").write_text(json.dumps(fake_report, ensure_ascii=False), encoding="utf-8")

    # stub Popen（在 sidecar 里要改成 Popen 或 subprocess.run）
    with patch.object(daily_report, "_run_ai_bridge") as mock_run:
        mock_run.return_value = (0, json.dumps({"ok": True, "json_path": str(out_dir / "report.json"), "duration_ms": 100}))
        result = daily_report.generate({"date": "2026-06-10"})

    assert mock_run.called
    assert result["stage"] == "done"
    assert "output" in result


def test_configured_ai_bridge_fails_returns_error(tmp_path, monkeypatch):
    """ai_bridge 返回失败 → result 反映 error"""
    out_dir = _setup_minimal_data(tmp_path, monkeypatch, configured=True)
    with patch.object(daily_report, "_run_ai_bridge") as mock_run:
        mock_run.return_value = (1, json.dumps({"ok": False, "err_code": "TIMEOUT", "err_msg": "120s"}))
        # generate 内部要 raise 或返回 error；目前实现是 raise
        with pytest.raises(Exception) as exc_info:
            daily_report.generate({"date": "2026-06-10"})
        assert "TIMEOUT" in str(exc_info.value) or "120s" in str(exc_info.value)
```

- [ ] **Step 2: 跑测试，验证失败（因为 _run_ai_bridge 还不存在）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_daily_report_ai_phase.py -v
```

Expected: `AttributeError: module 'sidecar.core.daily_report' has no attribute '_run_ai_bridge'`

- [ ] **Step 3: 修改 daily_report.py，加 _run_ai_bridge + 阶段 2 自动调用**

编辑 `sidecar/core/daily_report.py`，找到 `generate()` 函数，在阶段 1 之后（约 line 102）插入：

```python
    # 阶段 2: 调 LLM（如已配置）或回退 awaiting_ai
    llm_cfg = llm_config.get_config()
    if not llm_cfg["configured"]:
        _emit("ai_summarize", 40, "等待 AI 分析（未配置 LLM）")
        return {
            "stage": "awaiting_ai",
            "date": date,
            "bundle_path": bundle_result["bundle_path"],
            "prompt_path": bundle_result["prompt_path"],
            "report_template_path": bundle_result["report_template_path"],
            "chat_count": chat_count,
            "message_count": msg_count,
        }

    # 已配置 → 调 ai_bridge
    _emit("ai_summarize", 45, f"调用 {llm_cfg['provider']} 分析...")
    rc, payload_json = _run_ai_bridge(
        bundle_path=bundle_result["bundle_path"],
        prompt_path=bundle_result["prompt_path"],
        output_dir=str(out_dir),
        provider_id=llm_cfg["provider"],
    )
    payload = json.loads(payload_json) if payload_json else {}
    if rc != 0 or not payload.get("ok"):
        err_code = payload.get("err_code", "CRASH")
        err_msg = payload.get("err_msg", "AI 子进程异常")
        # 抛 ValueError 会被 sidecar 包装为 CODE_INVALID_PARAMS -32602；
        # 改抛自定义异常以便 dispatch 映射到正确的 code
        from sidecar.core import daily_report as _dr_mod
        raise RuntimeError(f"AI 分析失败 [{err_code}]: {err_msg}")

    _emit("ai_summarize", 65, "AI 分析完成")
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    report_path = out_dir / "report.json"
```

并在文件底部新增 helper：

```python
def _run_ai_bridge(bundle_path: str, prompt_path: str, output_dir: str, provider_id: str) -> tuple[int, str]:
    """Popen ai_bridge.exe，读 stdout 1 行 JSON

    Returns: (returncode, stdout_json_line)
    """
    import subprocess
    from pathlib import Path
    import sys

    # 找 ai_bridge.exe 路径
    if getattr(sys, "frozen", False):
        bridge_exe = Path(sys.executable).parent / "ai_bridge.exe"
    else:
        bridge_exe = Path(sys.executable).parent / "ai_bridge.exe"
        if not bridge_exe.exists():
            # 开发模式：跑 module
            proc = subprocess.run(
                [sys.executable, "-m", "ai_bridge",
                 "--bundle-path", bundle_path,
                 "--prompt-path", prompt_path,
                 "--output-dir", output_dir,
                 "--provider-id", provider_id],
                capture_output=True, text=True, timeout=130,
            )
            return proc.returncode, proc.stdout.strip()

    if not bridge_exe.exists():
        return 1, json.dumps({"ok": False, "err_code": "CRASH", "err_msg": f"ai_bridge.exe 不存在: {bridge_exe}"})

    proc = subprocess.run(
        [str(bridge_exe),
         "--bundle-path", bundle_path,
         "--prompt-path", prompt_path,
         "--output-dir", output_dir,
         "--provider-id", provider_id],
        capture_output=True, text=True, timeout=130,
    )
    return proc.returncode, proc.stdout.strip()
```

并删除原"if report is None"分支里的重复 code（保留 awaiting_ai 路径，但走新的统一分支）。

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_daily_report_ai_phase.py -v
```

Expected: 3 个 test 全 PASS。

- [ ] **Step 5: 手动 smoke（启动 sidecar，调 generate_daily 不传 report_json）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
# 准备假 config
mkdir -p /tmp/dtb_smoke
cat > /tmp/dtb_smoke/config.yaml <<'YAML'
llm:
  active_provider: openai
  base_url: https://api.openai.com/v1
  default_model: gpt-4o-mini
YAML
# 注意：没有 llm_secret.bin → get_config 会自动重置为未配置
DINGTALK_BOX_DATA_DIR=/tmp/dtb_smoke python -c "
import sys
sys.path.insert(0, 'sidecar')
from core import daily_report
# stub dds.export_daily_bundle 避免真调 dws
import unittest.mock as m
with m.patch('core.daily_report.dds') as mock_dds:
    mock_dds.export_daily_bundle.return_value = {
        'chat_count': 0, 'message_count': 0,
        'bundle_path': '/tmp/bundle.json',
        'prompt_path': '/tmp/prompt.md',
        'report_template_path': '/tmp/template.json',
    }
    r = daily_report.generate({'date': '2026-06-10'})
    print('STAGE:', r.get('stage'))
"
```

Expected: 输出 `STAGE: awaiting_ai`（因没 secret → configured=false）

- [ ] **Step 6: Commit**

```bash
git add sidecar/core/daily_report.py tests/test_daily_report_ai_phase.py
git commit -m "feat(sidecar): daily_report 阶段 2 自动调 ai_bridge（已配置时）"
```

---

# P1: 10 家 providers + UI modal + 状态条

## Task 10: 补全 9 家 providers preset

**Files:**
- Modify: `assets/providers.yaml`

- [ ] **Step 1: 替换 providers.yaml 为完整 10 家**

`assets/providers.yaml`（完整版）：
```yaml
version: 1

providers:
  - id: openai
    name: "OpenAI"
    base_url: "https://api.openai.com/v1"
    default_model: "gpt-4o-mini"
    doc_url: "https://platform.openai.com/api-keys"

  - id: anthropic_claude_compat
    name: "Anthropic Claude (自部署 OpenAI 兼容网关)"
    base_url: ""
    default_model: ""
    doc_url: "https://docs.anthropic.com/en/api/openai-sdk"
    note: "需要自备 OpenAI 兼容网关（如 OneAPI/OpenRouter）"

  - id: deepseek
    name: "DeepSeek"
    base_url: "https://api.deepseek.com/v1"
    default_model: "deepseek-chat"
    doc_url: "https://platform.deepseek.com/api_keys"

  - id: moonshot
    name: "Moonshot Kimi"
    base_url: "https://api.moonshot.cn/v1"
    default_model: "moonshot-v1-8k"
    doc_url: "https://platform.moonshot.cn/console/api-keys"

  - id: zhipu_glm
    name: "智谱 GLM"
    base_url: "https://open.bigmodel.cn/api/paas/v4"
    default_model: "glm-4-flash"
    doc_url: "https://bigmodel.cn/usercenter/apikeys"

  - id: aliyun_dashscope
    name: "阿里百炼 DashScope"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    default_model: "qwen-plus"
    doc_url: "https://dashscope.console.aliyun.com/apiKey"

  - id: volcengine_doubao
    name: "字节豆包 Volcengine"
    base_url: "https://ark.cn-beijing.volces.com/api/v3"
    default_model: "doubao-pro-32k"
    doc_url: "https://www.volcengine.com/product/doubao"

  - id: xunfei_spark
    name: "讯飞星火"
    base_url: "https://spark-api-open.xf-yun.com/v1"
    default_model: "generalv3.5"
    doc_url: "https://console.xfyun.cn/services/bm3"

  - id: MiniMax
    name: "MiniMax"
    base_url: "https://api.MiniMax.chat/v1"
    default_model: "MiniMax-Text-01"
    doc_url: "https://platform.MiniMax.cn/usercenter/basic-information/interface-key"

  - id: baidu_qianfan
    name: "百度千帆 (OpenAI 兼容 v2)"
    base_url: "https://qianfan.baidubce.com/v2"
    default_model: "ernie-4.0-8k"
    doc_url: "https://console.bce.baidu.com/qianfan/ais/console/apiKey"
```

- [ ] **Step 2: 跑现有 test 验证全 10 家加载成功**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_providers.py -v
```

Expected: 全 PASS。

- [ ] **Step 3: 补一个验证 10 家的 test**

编辑 `tests/test_ai_bridge_providers.py`，在 `test_load_providers_returns_list` 后面加：

```python
def test_load_providers_has_exactly_10():
    """preset 数量应为 10"""
    plist = providers.load_providers()
    assert len(plist) == 10


def test_all_providers_have_unique_id():
    """id 唯一"""
    plist = providers.load_providers()
    ids = [p["id"] for p in plist]
    assert len(ids) == len(set(ids))
```

跑：

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_ai_bridge_providers.py::test_load_providers_has_exactly_10 tests/test_ai_bridge_providers.py::test_all_providers_have_unique_id -v
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add assets/providers.yaml tests/test_ai_bridge_providers.py
git commit -m "feat(ai_bridge): 补全 10 家 providers preset"
```

---

## Task 11: assets/default_config.yaml 加 llm 段

**Files:**
- Modify: `assets/default_config.yaml`
- Modify: `sidecar/core/config.py`（merge 新段到老 config）

- [ ] **Step 1: 改 default_config.yaml 末尾加 llm 段**

`assets/default_config.yaml` 末尾追加：
```yaml
logging:
  level: "INFO"
  max_files: 7

# ↓ 新增（v0.2 起）↓
llm:
  active_provider: null
  base_url: null
  default_model: null
```

- [ ] **Step 2: 改 config.py，老 config 自动 merge llm 段**

编辑 `sidecar/core/config.py`，修改 `load()` 函数：
```python
def load() -> dict[str, Any]:
    cfg_path = ensure_config()
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {"version": 1}
    # 老 config 缺 llm 段 → 自动 merge
    if "llm" not in data:
        data["llm"] = {
            "active_provider": None,
            "base_url": None,
            "default_model": None,
        }
        save(data)
    return data
```

- [ ] **Step 3: 验证（手动）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
# 模拟老 config（无 llm 段）
mkdir -p /tmp/dtb_legacy
cat > /tmp/dtb_legacy/config.yaml <<'YAML'
version: 1
corp:
  expected_corp_id: "test"
YAML
DINGTALK_BOX_DATA_DIR=/tmp/dtb_legacy python -c "
import sys
sys.path.insert(0, 'sidecar')
from core import config
data = config.load()
print('llm 段:', data.get('llm'))
"
```

Expected: `llm 段: {'active_provider': None, 'base_url': None, 'default_model': None}`，且 config.yaml 文件被自动改写（加了 llm 段）。

- [ ] **Step 4: Commit**

```bash
git add assets/default_config.yaml sidecar/core/config.py
git commit -m "feat(config): default config 加 llm 段 + 老 config 自动 merge"
```

---

## Task 12: 前端 ⚙ 按钮 + llm-modal HTML

**Files:**
- Modify: `src/index.html`
- Modify: `src/style.css`

- [ ] **Step 1: 在 user-card 加 ⚙ 按钮**

编辑 `src/index.html`，找到 `<div class="status" id="dws-status">`，在其后面加：
```html
        <div class="row right">
          <button id="btn-llm-settings" class="icon-btn" title="AI 模型配置">⚙</button>
        </div>
```

- [ ] **Step 2: 在 `<div id="toast">` 之前加 llm-modal**

编辑 `src/index.html`，在 `<!-- Toast -->` 注释前加：
```html
  <!-- AI 模型配置 -->
  <div id="llm-modal" class="modal hidden">
    <div class="modal-box">
      <div class="modal-title">
        🤖 AI 模型配置
        <button class="close" id="btn-close-llm">✕</button>
      </div>

      <div class="form-row">
        <label for="llm-provider">厂商</label>
        <select id="llm-provider"></select>
      </div>

      <div class="form-row">
        <label for="llm-base-url">Base URL</label>
        <input type="text" id="llm-base-url" placeholder="https://..." />
      </div>

      <div class="form-row">
        <label for="llm-model">默认模型</label>
        <input type="text" id="llm-model" placeholder="gpt-4o-mini" />
      </div>

      <div class="form-row">
        <label for="llm-key">API Key</label>
        <div class="key-row">
          <input type="password" id="llm-key" autocomplete="off" />
          <button id="btn-toggle-key" type="button">👁 显示</button>
        </div>
      </div>

      <div class="form-row hint">
        <a id="llm-doc-link" href="#" target="_blank" rel="noopener">查看该厂商文档 ↗</a>
      </div>

      <hr/>

      <div class="form-row">
        <span id="llm-status">状态：未知</span>
      </div>

      <div class="modal-actions">
        <button id="btn-test-llm">🔌 测试连接</button>
        <button id="btn-save-llm" class="primary">💾 保存</button>
        <button id="btn-clear-llm" class="danger">🗑 清除配置</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 3: 加 CSS**

编辑 `src/style.css`，在文件末尾追加：
```css
/* ── AI 配置 modal ───────────────────────────────────────────── */
.icon-btn {
  background: transparent;
  border: 1px solid transparent;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 16px;
  cursor: pointer;
  color: #666;
}
.icon-btn:hover {
  background: #f0f0f0;
  border-color: #ddd;
}

.row.right {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

.key-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.key-row input {
  flex: 1;
}

.hint a {
  color: #1d6cf3;
  text-decoration: none;
}
.hint a:hover {
  text-decoration: underline;
}

#llm-status {
  font-size: 14px;
  padding: 4px 0;
}

#llm-status.ok { color: #2a8a2a; }
#llm-status.bad { color: #c44; }
#llm-status.warn { color: #c80; }
```

- [ ] **Step 4: 手动 smoke（启动 GUI 看到 ⚙ 按钮）**

不验证，等 P1 任务 13 加完 JS 后再启动。

- [ ] **Step 5: Commit**

```bash
git add src/index.html src/style.css
git commit -m "feat(ui): ⚙ AI 配置按钮 + llm-modal HTML+CSS"
```

---

## Task 13: 前端 llm-modal 逻辑（load/save/switch）

**Files:**
- Modify: `src/main.js`

- [ ] **Step 1: 在 main.js 加 llm modal 状态 + 打开/关闭 + 切厂商**

编辑 `src/main.js`，在 `state = {...}` 后加：
```javascript
  // ── LLM 配置 modal 状态 ───────────────────────────────────
  const llmState = {
    providers: [],   // 10 家 preset
    current: null,   // 当前已保存配置
    dirty: false,    // 是否有未保存修改
  };
```

在 `function bindEvents() {` 内尾部加：
```javascript
    $("btn-llm-settings").addEventListener("click", openLlmModal);
    $("btn-close-llm").addEventListener("click", closeLlmModal);
    $("btn-save-llm").addEventListener("click", saveLlmConfig);
    $("btn-test-llm").addEventListener("click", testLlmConnection);
    $("btn-clear-llm").addEventListener("click", clearLlmConfig);
    $("btn-toggle-key").addEventListener("click", () => {
      const inp = $("llm-key");
      inp.type = inp.type === "password" ? "text" : "password";
    });
    $("llm-provider").addEventListener("change", onLlmProviderChange);
    // 标记 dirty
    ["llm-base-url", "llm-model", "llm-key"].forEach((id) => {
      $(id).addEventListener("input", () => { llmState.dirty = true; });
    });
```

在文件末尾（IIFE 内）加新函数：
```javascript
  // ── LLM modal 逻辑 ────────────────────────────────────────
  async function openLlmModal() {
    llmState.dirty = false;
    $("llm-modal").classList.remove("hidden");
    setLlmStatus("加载中…", "");
    try {
      // 加载 provider 列表（用内置常量的 fallback，避免多一次 round trip）
      llmState.providers = LLM_PROVIDERS_FALLBACK;
      // 实际应调 sidecar.list_providers（暂未实现）—— 暂时用 fallback
      const sel = $("llm-provider");
      sel.innerHTML = llmState.providers.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
      // 加载已保存配置
      const cfg = await call("get_llm_config", {});
      llmState.current = cfg;
      if (cfg.configured) {
        const p = llmState.providers.find((x) => x.id === cfg.provider) || {};
        sel.value = cfg.provider;
        $("llm-base-url").value = cfg.base_url || "";
        $("llm-model").value = cfg.default_model || "";
        $("llm-key").value = "";  // 永远不显示明文
        $("llm-doc-link").href = p.doc_url || "#";
        setLlmStatus(`✓ 已配置 ${cfg.provider} · 模型 ${cfg.default_model}`, "ok");
      } else {
        sel.value = "openai";
        onLlmProviderChange();
        setLlmStatus("⚠ 未配置（保存后将启用 AI 总结）", "warn");
      }
    } catch (e) {
      setLlmStatus("加载失败：" + (e?.message || e), "bad");
    }
  }

  function closeLlmModal() {
    if (llmState.dirty) {
      if (!confirm("有未保存的修改，确定关闭？")) return;
    }
    $("llm-modal").classList.add("hidden");
    llmState.dirty = false;
  }

  function onLlmProviderChange() {
    const sel = $("llm-provider");
    const p = llmState.providers.find((x) => x.id === sel.value);
    if (!p) return;
    $("llm-base-url").value = p.base_url || "";
    $("llm-model").value = p.default_model || "";
    $("llm-key").value = "";  // 切厂商强制重输 key
    $("llm-doc-link").href = p.doc_url || "#";
    setLlmStatus("已切换厂商，请输入 API Key", "warn");
    llmState.dirty = true;
  }

  async function saveLlmConfig() {
    const provider = $("llm-provider").value;
    const base_url = $("llm-base-url").value.trim();
    const default_model = $("llm-model").value.trim();
    const api_key = $("llm-key").value;
    if (!api_key) {
      setLlmStatus("⚠ 请先填 API Key", "bad");
      return;
    }
    setLlmStatus("保存中…", "");
    try {
      await call("set_llm_config", { provider, base_url, default_model, api_key });
      toast("✓ 已保存", "ok");
      llmState.dirty = false;
      setLlmStatus(`✓ 已配置 ${provider} · 模型 ${default_model}`, "ok");
      // 同步状态条
      updateBootStatus();
    } catch (e) {
      setLlmStatus("保存失败：" + (e?.message || e), "bad");
    }
  }

  async function testLlmConnection() {
    const provider = $("llm-provider").value;
    const base_url = $("llm-base-url").value.trim();
    const default_model = $("llm-model").value.trim();
    const api_key = $("llm-key").value;
    if (!api_key) {
      setLlmStatus("⚠ 请先填 API Key 再测试", "bad");
      return;
    }
    setLlmStatus("测试中…", "");
    const t0 = Date.now();
    try {
      const r = await call("test_llm_connection", {
        provider, base_url, default_model, api_key,
      });
      const dt = Date.now() - t0;
      if (r.ok) {
        setLlmStatus(`✓ 连接成功（${dt}ms）`, "ok");
      } else {
        setLlmStatus(`✗ 失败：${r.err_msg || "未知错误"}`, "bad");
      }
    } catch (e) {
      setLlmStatus("✗ 失败：" + (e?.message || e), "bad");
    }
  }

  async function clearLlmConfig() {
    if (!confirm("确定清除当前 LLM 配置？\n旧的 API Key 将被永久删除。")) return;
    try {
      await call("set_llm_config", { clear: true });
      toast("✓ 已清除", "ok");
      llmState.dirty = false;
      $("llm-key").value = "";
      setLlmStatus("⚠ 未配置（保存后将启用 AI 总结）", "warn");
      updateBootStatus();
    } catch (e) {
      setLlmStatus("清除失败：" + (e?.message || e), "bad");
    }
  }

  function setLlmStatus(msg, kind) {
    const el = $("llm-status");
    el.textContent = msg;
    el.className = kind || "";
  }

  // ── LLM provider 常量（避免多一次 round trip 失败导致 modal 打不开）──
  const LLM_PROVIDERS_FALLBACK = [
    { id: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1", default_model: "gpt-4o-mini", doc_url: "https://platform.openai.com/api-keys" },
    { id: "anthropic_claude_compat", name: "Anthropic Claude (自部署 OpenAI 兼容网关)", base_url: "", default_model: "", doc_url: "https://docs.anthropic.com/en/api/openai-sdk" },
    { id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com/v1", default_model: "deepseek-chat", doc_url: "https://platform.deepseek.com/api_keys" },
    { id: "moonshot", name: "Moonshot Kimi", base_url: "https://api.moonshot.cn/v1", default_model: "moonshot-v1-8k", doc_url: "https://platform.moonshot.cn/console/api-keys" },
    { id: "zhipu_glm", name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", default_model: "glm-4-flash", doc_url: "https://bigmodel.cn/usercenter/apikeys" },
    { id: "aliyun_dashscope", name: "阿里百炼 DashScope", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", default_model: "qwen-plus", doc_url: "https://dashscope.console.aliyun.com/apiKey" },
    { id: "volcengine_doubao", name: "字节豆包 Volcengine", base_url: "https://ark.cn-beijing.volces.com/api/v3", default_model: "doubao-pro-32k", doc_url: "https://www.volcengine.com/product/doubao" },
    { id: "xunfei_spark", name: "讯飞星火", base_url: "https://spark-api-open.xf-yun.com/v1", default_model: "generalv3.5", doc_url: "https://console.xfyun.cn/services/bm3" },
    { id: "MiniMax", name: "MiniMax", base_url: "https://api.MiniMax.chat/v1", default_model: "MiniMax-Text-01", doc_url: "https://platform.MiniMax.cn/usercenter/basic-information/interface-key" },
    { id: "baidu_qianfan", name: "百度千帆 (OpenAI 兼容 v2)", base_url: "https://qianfan.baidubce.com/v2", default_model: "ernie-4.0-8k", doc_url: "https://console.bce.baidu.com/qianfan/ais/console/apiKey" },
  ];
```

- [ ] **Step 2: 在 boot() 末尾加 updateBootStatus() 调用**

编辑 `src/main.js`，找到 `function boot() { ... setStatus("就绪"); ... }`，在 `setStatus("就绪");` 之前加：
```javascript
    await updateBootStatus();
```

并在文件末尾加新函数：
```javascript
  async function updateBootStatus() {
    try {
      const cfg = await call("get_llm_config", {});
      if (cfg.configured) {
        setStatus(`就绪 · AI 已配置 (${cfg.provider})`);
      } else {
        setStatus("⚠ AI 未配置（点 ⚙ 配置后可一键生成）");
      }
    } catch (e) {
      console.warn("get_llm_config failed", e);
    }
  }
```

- [ ] **Step 3: 在 awaiting_ai 时显示「去配置」按钮**

编辑 `src/main.js`，找到 `onGenerate()` 内 `if (r1.stage === "awaiting_ai") {` 块，改成：
```javascript
      if (r1.stage === "awaiting_ai") {
        toast("AI 未配置，bundle 已生成", "warn");
        // 弹 modal 询问
        if (confirm("AI 未配置，无法一键生成报告。\n\n点 [确定] 打开 AI 模型配置。\n点 [取消] 走老流程（导出 bundle 后手动处理）。")) {
          openLlmModal();
        } else {
          // 走老 awaiting_ai 流程：用户自己复制 prompt 出去用
          const stubReport = await buildStubReport(date, r1);
          const r2raw = await call("generate_daily", { date, report_json: stubReport });
          const r2 = r2raw?.result ?? r2raw;
          finishReport(r2);
        }
      } else {
        finishReport(r1);
      }
```

- [ ] **Step 4: 手动 smoke（启动 GUI 验证 ⚙ 按钮、modal 行为）**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
./dist/dingtalk_box.exe
```

依次验证：
1. 状态条显示"⚠ AI 未配置"
2. 点 ⚙ → modal 弹出
3. 切厂商 → base_url / model 自动填 + key 清空
4. 填一个 fake key → 保存 → 状态条更新
5. 关 modal → 再开 → key 字段为空

- [ ] **Step 5: Commit**

```bash
git add src/main.js
git commit -m "feat(ui): llm-modal 完整逻辑（load/save/test/clear/switch）"
```

---

# P2: 错误处理 + 日志脱敏 + 诊断脱敏

## Task 14: sidecar error code 映射 -32xxx

**Files:**
- Modify: `sidecar/core/daily_report.py`（抛出可被识别的异常）
- Modify: `sidecar/main.py`（handler 里把 RuntimeError + err_code 信息映射到 -32xxx）

- [ ] **Step 1: 自定义异常类**

编辑 `sidecar/main.py`（或新建 `sidecar/core/errors.py`），加：
```python
class AiBridgeError(Exception):
    """ai-bridge 子进程错误，code 属性对应 JSON-RPC code"""
    def __init__(self, err_code: str, err_msg: str, data: dict | None = None):
        super().__init__(f"[{err_code}] {err_msg}")
        self.err_code = err_code
        self.err_msg = err_msg
        self.data = data or {}
```

`_EXCEPTION_TO_CODE` dict 加映射：
```python
_AI_ERR_TO_CODE = {
    "DPAPI_FAIL": -32009,
    "TIMEOUT": -32010,
    "HTTP_401": -32011,
    "HTTP_429": -32012,
    "HTTP_5XX": -32013,
    "PARSE_FAIL": -32014,
    "SCHEMA_FAIL": -32015,
    "CRASH": -32099,
}
```

修改 `_handle()` 函数（line 89 附近），在 dispatch 后加捕获：
```python
    try:
        result = dispatcher.dispatch(method, params)
        return {"id": req_id, "result": result}
    except AiBridgeError as e:
        code = _AI_ERR_TO_CODE.get(e.err_code, CODE_INTERNAL_ERROR)
        return {
            "id": req_id,
            "error": {
                "code": code,
                "message": e.err_msg[:500],
                "data": {"err_code": e.err_code, **e.data},
            },
        }
    except (ValueError, KeyError, TypeError) as e:
        # 原代码
        ...
```

- [ ] **Step 2: 修改 daily_report.py 抛 AiBridgeError 而非 RuntimeError**

编辑 `sidecar/core/daily_report.py`，在文件顶部 import：
```python
from sidecar.core.errors import AiBridgeError  # 或从 sidecar.main 拿
```

把：
```python
raise RuntimeError(f"AI 分析失败 [{err_code}]: {err_msg}")
```
改为：
```python
raise AiBridgeError(err_code, err_msg)
```

- [ ] **Step 3: 跑现有 test 验证不破**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_daily_report_ai_phase.py -v
```

Expected: 全 PASS（异常类型换了但测试只校验 err_code / err_msg 字符串）。

- [ ] **Step 4: Commit**

```bash
git add sidecar/main.py sidecar/core/daily_report.py
git commit -m "feat(sidecar): AiBridgeError + -32xxx code 映射"
```

---

## Task 15: 日志脱敏 filter

**Files:**
- Modify: `sidecar/core/logging_setup.py`
- Create: `tests/test_logging_redact.py`

- [ ] **Step 1: 写失败单测**

`tests/test_logging_redact.py`：
```python
"""sidecar/core/logging_setup.py redact filter 单测"""
import logging

from sidecar.core.logging_setup import redact_key


def test_redact_bearer():
    """Authorization: Bearer sk-xxx → Bearer sk-***"""
    msg = "Authorization: Bearer sk-1234567890abcdef"
    out = redact_key(msg)
    assert "sk-1234567890abcdef" not in out
    assert "sk-1***" in out


def test_redact_json_api_key():
    """\"api_key\": \"sk-xxx\" → 脱敏"""
    msg = '"api_key": "sk-abcdef1234567890"'
    out = redact_key(msg)
    assert "sk-abcdef1234567890" not in out
    assert '"api_key": "sk-a***"' in out


def test_redact_no_match_unchanged():
    """无 key 字样 → 不动"""
    msg = "normal log message with no secrets"
    assert redact_key(msg) == msg


def test_logging_filter_applies():
    """filter 真的装在 logger 上"""
    log = logging.getLogger("test_redact")
    log.addFilter(redact_key.as_filter())
    # 直接 emit 一条 record
    rec = logging.LogRecord("test", logging.INFO, "/p", 1, "Bearer sk-fake-1234567890", None, None)
    log.handle(rec)
    # filter 返回 True 表示放行；record.msg 已被改写
    assert "sk-fake-1234567890" not in rec.getMessage()
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_logging_redact.py -v
```

Expected: `ImportError: cannot import name 'redact_key'`

- [ ] **Step 3: 实现 redact_key**

编辑 `sidecar/core/logging_setup.py`，在文件末尾加：
```python
import re

_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9_\-]{8,})")
_APIKEY_RE = re.compile(r'("api_key"\s*:\s*")([^"]{8,})(")')


def redact_key(msg: str) -> str:
    """脱敏：保留 key 前 4 位 + ***"""
    def _b(m):
        return m.group(1) + m.group(2)[:4] + "***"
    def _a(m):
        return m.group(1) + m.group(2)[:4] + "***" + m.group(3)
    return _APIKEY_RE.sub(_a, _BEARER_RE.sub(_b, msg))


class RedactFilter(logging.Filter):
    """Logging filter：emit 前对 msg 做 redact_key"""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_key(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    redact_key(a) if isinstance(a, str) else a for a in record.args
                )
            except Exception:
                pass
        return True


# 让 redact_key 自身也提供 as_filter()
redact_key.as_filter = lambda: RedactFilter()  # type: ignore[attr-defined]
```

并修改 `setup()` 函数（line ~30 附近），让所有 logger 默认带这个 filter：
```python
def setup(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    log.addFilter(RedactFilter())
    return log
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_logging_redact.py -v
```

Expected: 4 个 test 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add sidecar/core/logging_setup.py tests/test_logging_redact.py
git commit -m "feat(sidecar): 日志 redact filter（Authorization/api_key 脱敏）"
```

---

## Task 16: 诊断导出脱敏（不读 llm_secret.bin）

**Files:**
- Modify: `sidecar/core/send.py`（`collect_diagnostics`）
- Create: `tests/test_diagnose_no_secret.py`

- [ ] **Step 1: 写失败单测**

`tests/test_diagnose_no_secret.py`：
```python
"""send.collect_diagnostics 不读 llm_secret.bin 单测"""
import platform
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sidecar.core import send


@pytest.fixture
def fake_data_dir(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    d.mkdir()
    (d / "config.yaml").write_text("llm:\n  active_provider: openai\n", encoding="utf-8")
    # 写一个看起来像 key 的"密文"文件
    (d / "llm_secret.bin").write_bytes(b"FAKE-DPAPI-BLOB-1234567890abcdef")
    monkeypatch.setattr(send, "data_dir", lambda: d)
    # 桩 list_history 等
    monkeypatch.setattr(send, "list_history", lambda: {"items": [], "total": 0})
    return d


@pytest.mark.skipif(platform.system() != "Windows", reason="仅 Windows 验证")
def test_diagnose_does_not_include_secret(fake_data_dir):
    """诊断导出不应包含 llm_secret.bin 内容"""
    d = send.collect_diagnostics()
    text = str(d)
    assert "FAKE-DPAPI-BLOB-1234567890abcdef" not in text
    assert "sk-" not in text  # 防御：也不应包含明文 key 模式
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_diagnose_no_secret.py -v
```

Expected: 失败（如果 collect_diagnostics 当前读了 llm_secret.bin）

- [ ] **Step 3: 检查并修复 collect_diagnostics**

读 `sidecar/core/send.py` 的 `collect_diagnostics` 函数。如果它**没有**读 llm_secret.bin（看代码确认），那这步可能 no-op；如有读，则删除对应行。

如果当前函数没有把 llm_secret.bin 包含进去，本任务 = 完成；如包含，要去掉。代码示例（确保不读 secret）：
```python
def collect_diagnostics() -> dict:
    """收集诊断信息：config、版本、路径、日志目录列表（**不读** llm_secret.bin）"""
    return {
        "version": "0.1.0",
        "platform": platform.platform(),
        "python": sys.version,
        "data_dir": str(data_dir()),
        "config_yaml": (data_dir() / "config.yaml").read_text(encoding="utf-8")
                       if (data_dir() / "config.yaml").is_file() else None,
        # 故意不读 llm_secret.bin
        "log_files": sorted(p.name for p in (data_dir() / "logs").glob("*.log"))
                     if (data_dir() / "logs").is_dir() else [],
        "history_count": list_history().get("total", 0),
    }
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/test_diagnose_no_secret.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add sidecar/core/send.py tests/test_diagnose_no_secret.py
git commit -m "feat(sidecar): 诊断导出确认不读 llm_secret.bin"
```

---

# P3: 完整单测 + 打包 + 回归

## Task 17: 补充单测覆盖所有 23 个 case

**Files:**
- Modify: `tests/test_ai_bridge.py`（合并并扩展现有 test 文件到 23 个 case）

- [ ] **Step 1: 把现有 test 合并到 tests/test_ai_bridge.py**

把 `tests/test_ai_bridge_dpapi.py` / `tests/test_ai_bridge_providers.py` / `tests/test_ai_bridge_schema.py` / `tests/test_ai_bridge_main.py` 的内容合并到 `tests/test_ai_bridge.py`（添加 `import` 和模块声明），并加：

```python
def test_ai_bridge_429_includes_retry_after(capsys, tmp_path):
    """mock 429 → err_code=HTTP_429"""
    # 参考 test_ai_bridge_401 实现
    ...

def test_bundle_split_large_creates_n_messages(capsys, tmp_path):
    """50k 字符 bundle → N 段 user message"""
    from ai_bridge import main as bridge_main
    big_bundle = '{"chats": [' + ','.join(['{"id": ' + str(i) + ', "msgs": []}' for i in range(200)]) + ']}'
    assert len(big_bundle) > 50_000
    # 不调 LLM，只验证 _build_messages 返回值
    from ai_bridge.main import _build_messages
    msgs = _build_messages("# prompt", big_bundle, threshold=30_000)
    assert len(msgs) >= 4  # 1 system + 200 user + 1 final
    assert msgs[-1]["role"] == "user"
    assert "综合" in msgs[-1]["content"]


def test_parse_ai_content_strips_markdown_fence():
    from ai_bridge.main import _parse_ai_content
    raw = '```json\n{"a": 1}\n```'
    assert _parse_ai_content(raw) == {"a": 1}


def test_parse_ai_content_handles_trailing_comma():
    from ai_bridge.main import _parse_ai_content
    raw = '{"a": 1, "b": 2,}'  # 末尾多余逗号
    parsed = _parse_ai_content(raw)
    assert parsed["a"] == 1
    assert parsed["b"] == 2
```

- [ ] **Step 2: 跑全测**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
python -m pytest tests/ -v
```

Expected: 全 PASS（如果 skipif Windows 标记正确，非 Windows 平台会 skip DPAPI 相关）。

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test: 合并 ai_bridge 单测，补 429/分段/markdown 剥离"
```

---

## Task 18: 重新打包 sidecar.exe + ai_bridge.exe + launcher.exe

**Files:**
- 操作产物（不入库）：`dist/sidecar.exe` `dist/ai_bridge.exe` `dist/dingtalk_box.exe`

- [ ] **Step 1: 打包 ai_bridge**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
pyinstaller ai_bridge.spec --clean --noconfirm
```

Expected: `dist/ai_bridge.exe` 存在。

- [ ] **Step 2: 打包 sidecar**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
pyinstaller build.spec --clean --noconfirm
```

Expected: `dist/sidecar.exe` 存在。

- [ ] **Step 3: 打包 launcher**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
pyinstaller launcher.spec --clean --noconfirm
```

Expected: `dist/dingtalk_box.exe` 存在。

- [ ] **Step 4: 同步到 deliver**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
cp dist/dingtalk_box.exe deliver/dingtalk_box/dingtalk_box.exe
cp dist/sidecar.exe deliver/dingtalk_box/sidecar.exe
cp dist/ai_bridge.exe deliver/dingtalk_box/ai_bridge.exe
ls -la deliver/dingtalk_box/
```

Expected: 三个 exe 都是当前时间。

- [ ] **Step 5: 重新打 zip**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box/deliver"
powershell -Command "Compress-Archive -Path dingtalk_box/* -DestinationPath dingtalk_box.zip -Force"
```

Expected: `dingtalk_box.zip` 更新。

- [ ] **Step 6: 验证 frozen 启动**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
./dist/ai_bridge.exe --help
./dist/sidecar.exe --version 2>&1 | head -5
```

Expected: 都不报 ImportError。

- [ ] **Step 7: 不需 commit（dist 不入库）**

---

## Task 19: 端到端集成验证（用 OpenAI 真 key）

**Files:** 操作产物

- [ ] **Step 1: 启动 deliver/dingtalk_box/dingtalk_box.exe**

```bash
cd "C:/Users/lesoon/.claude/projects/dingtalk_box"
./deliver/dingtalk_box/dingtalk_box.exe
```

- [ ] **Step 2: 配置 OpenAI 真 key**

1. 点 ⚙
2. 选 OpenAI（默认）
3. 填真 key
4. 点 🔌 测试连接 → 看到 ✓ + 延迟
5. 点 💾 保存 → toast ✓

- [ ] **Step 3: 一键生成**

1. 点「生 成 今 日 日 报」
2. 等待 10-30 秒
3. 看到真 AI 输出（不是占位"今日 N 个会话"）
4. PNG + MD 正常生成

- [ ] **Step 4: 切换厂商**

1. 点 ⚙ → 选 DeepSeek → key 清空
2. 填 DeepSeek key → 测试 → 保存
3. 点生成 → 应得到不同风格的报告（不同模型）

- [ ] **Step 5: 切回 OpenAI**

1. ⚙ → OpenAI → 必须重输 key（旧 key 已删）
2. 填真 key → 保存

- [ ] **Step 6: 故意输错 key**

1. ⚙ → 填 "sk-fake" → 🔌 → 应看到 ✗ 401
2. 不保存 → 关 modal
3. 状态条仍显示已配置（如果之前保存过）

- [ ] **Step 7: 诊断导出**

1. 点 🔍 诊断
2. 复制内容
3. 全文搜索 "sk-" → 应找不到真 key

- [ ] **Step 8: 清除配置**

1. ⚙ → 🗑 清除 → 确认
2. 状态条变"⚠ AI 未配置"
3. 点生成 → 弹"AI 未配置，去配置吗？" → 走老路（cancel → 占位报告）

- [ ] **Step 9: 验证结论**

| 集成 case | 期望 | 实际 |
|---|---|---|
| 配 OpenAI 真 key → 一键生成 | ✓ 真 AI 输出 | ___ |
| 切 DeepSeek → 切回 | 旧 key 失效 | ___ |
| 错 key | ✗ 401 | ___ |
| 诊断导出 | 不含 key | ___ |
| 清除后 | 状态条"⚠ AI 未配置" | ___ |

- [ ] **Step 10: Commit（如有 prompt 微调）**

如果发现问题修了代码，单独 commit；不强制。

---

## Task 20: 回归（不破老功能）

**Files:** 操作产物

- [ ] **Step 1: 验证 buildStubReport 仍工作**

清除 LLM 配置后，点生成 → 走 awaiting_ai → cancel → 走老占位流程：
- 看到 `今日共 N 个工作会话 / M 条消息` 的占位 overview
- 看到"今日未发现明确事项"的兜底行（来自 `_ensure_rows`）
- PNG + MD 正常生成

- [ ] **Step 2: 验证 launcher 自检仍通过**

启动 deliver 版本后，状态条 5 秒后出现：
```
✓ 启动自检通过 · 报告 钉钉工作纪要日报-2026-06-XX.png
```

- [ ] **Step 3: 验证 dws 登录 + 拉消息 + 发送文件**

1. 登录 dws（如果之前没登录）
2. 点生成 → 拉真实 bundle
3. 点「发送到钉钉」 → 选 AI 张瑞 → 确认发送

- [ ] **Step 4: 验证历史报告 + 打开文件夹**

1. 历史区显示已生成报告列表
2. 点「打开文件夹」→ 资源管理器打开 output/2026-06-XX/

- [ ] **Step 5: 不需 commit（回归无问题则不 commit）**

---

# 收尾

## Task 21: 写 README 更新段

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 加「AI 模型配置」一节**

在 `README.md` 末尾追加：
```markdown
## AI 模型配置

工具支持 10 家云端大模型厂商，用户可在 GUI 内选择并绑定 API Key：

- OpenAI / Anthropic Claude (自部署 OpenAI 兼容网关) / DeepSeek / Moonshot Kimi
- 智谱 GLM / 阿里百炼 DashScope / 字节豆包 / 讯飞星火
- MiniMax / 百度千帆

**配置步骤**：
1. 点用户卡片右侧的 ⚙ 按钮
2. 选择厂商（自动填充 base_url + 默认模型）
3. 填入 API Key
4. 点 [🔌 测试连接] 验证（可选）
5. 点 [💾 保存]

**安全说明**：
- API Key 用 Windows DPAPI 加密后存到 `%APPDATA%/DingTalkBox/llm_secret.bin`
- 同台机器同用户可解密；跨机器/跨用户变废
- 切换厂商会覆盖旧 Key（不可恢复）
- 清除配置后 Key 立即从磁盘删除
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README 加 AI 模型配置说明"
```

---

## 验收清单

- [ ] P0 9 个任务全过
- [ ] P1 4 个任务（10/11/12/13）全过
- [ ] P2 3 个任务（14/15/16）全过
- [ ] P3 5 个任务（17/18/19/20/21）全过
- [ ] 23 个单测 case 全绿
- [ ] 集成 case 1-5（P1）全过
- [ ] 集成 case 6-11（P2）全过
- [ ] 老功能（拉消息/登录/发送/历史/自检）不破
- [ ] `deliver/dingtalk_box.zip` 重新打包且含 ai_bridge.exe

**完成 = 上述全打勾。**
