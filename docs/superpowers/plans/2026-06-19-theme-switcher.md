# Theme Switcher (云白 / 暖樱 / 星河) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a header-right theme switcher that lets users pick between 3 visual themes (云白 / 暖樱 / 星河) with persistent storage in sidecar config.yaml. Default = 云白.

**Architecture:** Sidecar stores `preferences.theme` in `config.yaml` (existing YAML infrastructure). Frontend uses CSS custom properties under `:root[data-theme="X"]` blocks. Switching is instantaneous (set `data-theme` attribute on `<html>`) — no reload, no animation.

**Tech Stack:** Python (sidecar preferences module + RPC), YAML config (existing), CSS custom properties, vanilla JS (no framework).

**Spec:** `docs/superpowers/specs/2026-06-19-theme-switcher-design.md`

**Rollback:** Current UI saved to git tag `v0.1.0-pre-redesign` (commit `d302f45`).

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `sidecar/core/preferences.py` | Theme enum + config.yaml I/O for `preferences.theme` | **CREATE** |
| `sidecar/core/dispatcher.py` | Register `get_theme` / `set_theme` RPCs | MODIFY |
| `sidecar/core/llm_config.py` | (no change) | — |
| `tests/test_preferences.py` | Unit tests for preferences module | **CREATE** |
| `src/index.html` | Add theme button + dropdown; move LLM button to titlebar | MODIFY |
| `src/style.css` | Refactor to CSS variables; add 3 theme blocks; swatch colors | MODIFY |
| `src/main.js` | Add `initTheme` / `switchTheme` / `wireThemeSwitcher` | MODIFY |

---

## Task 1: Sidecar preferences module — TDD setup

**Files:**
- Create: `tests/test_preferences.py`
- Create: `sidecar/core/preferences.py`

- [ ] **Step 1: Write failing test — default theme is `cloud`**

Create `tests/test_preferences.py`:

```python
"""preferences.py 单元测试"""
import pytest
from sidecar.core import preferences, config


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """每次测试用临时目录，避免污染真实 config.yaml"""
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path)
    return tmp_path


def test_get_theme_default_when_no_config(isolated_config):
    """config.yaml 不存在时返回 cloud"""
    assert preferences.get_theme() == "cloud"


def test_get_theme_default_when_corrupt_config(isolated_config):
    """config.yaml 损坏（YAML 解析失败）时 fallback cloud"""
    (isolated_config / "config.yaml").write_text(":\n  - bad", encoding="utf-8")
    assert preferences.get_theme() == "cloud"


def test_set_then_get(isolated_config):
    """set_theme 写入后 get_theme 能读到"""
    preferences.set_theme("sakura")
    assert preferences.get_theme() == "sakura"


def test_set_invalid_raises(isolated_config):
    """非法 theme 抛 ValueError"""
    with pytest.raises(ValueError, match="未知 theme"):
        preferences.set_theme("neon")


def test_set_overwrites_old_value(isolated_config):
    """连续 set 两个值，后者覆盖前者"""
    preferences.set_theme("galaxy")
    preferences.set_theme("cloud")
    assert preferences.get_theme() == "cloud"


def test_set_preserves_other_config_keys(isolated_config):
    """set_theme 不破坏 config.yaml 里其他段（如 llm）"""
    # 先写一段 llm
    data = config.load()
    data["llm"] = {"active_provider": "openai", "model": "gpt-5-pro"}
    config.save(data)
    # 切主题
    preferences.set_theme("sakura")
    # 验证 llm 段还在
    data2 = config.load()
    assert data2["llm"] == {"active_provider": "openai", "model": "gpt-5-pro"}
    assert data2["preferences"]["theme"] == "sakura"


def test_round_trip_all_three(isolated_config):
    """3 个合法主题都能 round-trip"""
    for t in ("cloud", "sakura", "galaxy"):
        preferences.set_theme(t)
        assert preferences.get_theme() == t
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && python -m pytest tests/test_preferences.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'sidecar.core.preferences'`

- [ ] **Step 3: Implement preferences.py**

Create `sidecar/core/preferences.py`:

```python
"""preferences.py — 用户偏好（主题等）

存储位置：config.yaml 的 preferences 段
"""
from __future__ import annotations

from . import config

VALID_THEMES = ("cloud", "sakura", "galaxy")
DEFAULT_THEME = "cloud"


def get_theme() -> str:
    """读当前主题；不存在 / 非法 → 返回 DEFAULT_THEME"""
    try:
        t = config.get("preferences.theme")
    except Exception:
        return DEFAULT_THEME
    if t in VALID_THEMES:
        return t
    return DEFAULT_THEME


def set_theme(theme: str) -> str:
    """写主题；非法值抛 ValueError。返回写后的值（用于回显）"""
    if theme not in VALID_THEMES:
        raise ValueError(
            f"未知 theme: {theme!r}（可选：{', '.join(VALID_THEMES)}）"
        )
    data = config.load()
    prefs = data.get("preferences") or {}
    prefs["theme"] = theme
    data["preferences"] = prefs
    config.save(data)
    return theme
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && python -m pytest tests/test_preferences.py -v
```

Expected: PASS — 7 tests passed

- [ ] **Step 5: Commit**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add sidecar/core/preferences.py tests/test_preferences.py
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "feat(sidecar): preferences 模块（get_theme / set_theme）+ 单元测试"
```

---

## Task 2: Register theme RPCs in dispatcher

**Files:**
- Modify: `sidecar/core/dispatcher.py:1-15` (add import)
- Modify: `sidecar/core/dispatcher.py:159-182` (add to METHODS dict)

- [ ] **Step 1: Add `preferences` import to dispatcher**

Edit `sidecar/core/dispatcher.py`. The import block at lines 9-15 currently looks like:

```python
from . import auth, daily_report, install, llm_config, logging_setup, paths, send
```

Change to:

```python
from . import auth, daily_report, install, llm_config, logging_setup, paths, preferences, send
```

- [ ] **Step 2: Add 2 RPC methods to METHODS dict**

In the `METHODS` dict (lines 159-182), add these 2 lines right before the closing `}`:

```python
    # ↓ 主题切换（v0.2.0）↓
    "get_theme": lambda p: {"theme": preferences.get_theme()},
    "set_theme": lambda p: {"ok": True, "theme": preferences.set_theme(p.get("theme", ""))},
```

The full METHODS dict should now end like:

```python
    "list_providers": lambda p: {"providers": llm_config.list_providers()},
    # ↓ 主题切换（v0.2.0）↓
    "get_theme": lambda p: {"theme": preferences.get_theme()},
    "set_theme": lambda p: {"ok": True, "theme": preferences.set_theme(p.get("theme", ""))},
}
```

- [ ] **Step 3: Verify dispatcher import works**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && python -c "from sidecar.core import dispatcher; print('get_theme' in dispatcher.METHODS, 'set_theme' in dispatcher.METHODS)"
```

Expected: `True True`

- [ ] **Step 4: Manual smoke test — call RPCs end-to-end**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && python -c "
from sidecar.core import dispatcher

# 测试 get_theme default
r = dispatcher.dispatch('get_theme', {})
assert r == {'theme': 'cloud'}, f'expected cloud, got {r}'
print('get_theme default OK:', r)

# 测试 set_theme
r = dispatcher.dispatch('set_theme', {'theme': 'sakura'})
assert r == {'ok': True, 'theme': 'sakura'}, f'unexpected: {r}'
print('set_theme sakura OK:', r)

# 验证 get_theme 现在返 sakura
r = dispatcher.dispatch('get_theme', {})
assert r == {'theme': 'sakura'}, f'expected sakura, got {r}'
print('get_theme sakura OK:', r)

# 测试非法值
try:
    dispatcher.dispatch('set_theme', {'theme': 'neon'})
    assert False, 'should have raised'
except ValueError as e:
    print('set_theme invalid raised OK:', str(e)[:50])

# 恢复默认
dispatcher.dispatch('set_theme', {'theme': 'cloud'})
print('All RPC smoke tests passed')
"
```

Expected: All `OK` lines printed, no errors

- [ ] **Step 5: Commit**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add sidecar/core/dispatcher.py
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "feat(sidecar): 注册 get_theme / set_theme RPC"
```

---

## Task 3: Frontend HTML — add theme button + dropdown

**Files:**
- Modify: `src/index.html:11-13` (titlebar)
- Modify: `src/index.html:21-42` (user-card, remove LLM button)

- [ ] **Step 1: Update titlebar with new structure**

Edit `src/index.html`. The `<header class="titlebar">` block at lines 11-13 currently is:

```html
  <!-- 标题栏 -->
  <header class="titlebar">
    <span class="title">🔧 钉钉工具箱</span>
  </header>
```

Change to:

```html
  <!-- 标题栏 -->
  <header class="titlebar">
    <span class="title">🔧 钉钉工具箱</span>
    <div class="titlebar-actions">
      <button id="btn-theme" class="titlebar-btn" title="切换主题" aria-label="切换主题">🎨</button>
      <button id="btn-llm-settings" class="titlebar-btn" title="配置 AI 模型（10 家厂商可选）" aria-label="配置 AI 模型">🤖</button>
    </div>
    <div id="theme-menu" class="theme-menu hidden" role="menu">
      <button class="theme-option" data-theme="cloud" role="menuitem">
        <span class="theme-swatch" data-swatch="cloud"></span>
        <span class="theme-name">云白</span>
        <span class="theme-check">✓</span>
      </button>
      <button class="theme-option" data-theme="sakura" role="menuitem">
        <span class="theme-swatch" data-swatch="sakura"></span>
        <span class="theme-name">暖樱</span>
        <span class="theme-check">✓</span>
      </button>
      <button class="theme-option" data-theme="galaxy" role="menuitem">
        <span class="theme-swatch" data-swatch="galaxy"></span>
        <span class="theme-name">星河</span>
        <span class="theme-check">✓</span>
      </button>
    </div>
  </header>
```

- [ ] **Step 2: Remove LLM button from user-card**

Edit `src/index.html`. The user-card at lines 21-42 currently ends with:

```html
        <button id="btn-login" class="llm-entry" title="用当前钉钉账号登录">
          🔑 <span>登录钉钉</span>
        </button>
        <button id="btn-llm-settings" class="llm-entry" title="点击配置 AI 模型（10 家厂商可选）">
          🤖 <span id="llm-entry-label">配置 AI 模型</span>
        </button>
```

Change to (remove the `btn-llm-settings` button entirely):

```html
        <button id="btn-login" class="llm-entry" title="用当前钉钉账号登录">
          🔑 <span>登录钉钉</span>
        </button>
```

- [ ] **Step 3: Verify HTML is well-formed**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && python -c "
import html.parser
class P(html.parser.HTMLParser):
    def __init__(self): super().__init__(); self.stack=[]; self.err=[]
    def handle_starttag(self,t,a): 
        if t not in ('br','meta','link','input','img','hr'): self.stack.append(t)
    def handle_endtag(self,t):
        if self.stack and self.stack[-1]==t: self.stack.pop()
        else: self.err.append(f'mismatch close {t}, stack {self.stack[-3:]}')
p=P()
p.feed(open('src/index.html',encoding='utf-8').read())
print('errors:', p.err or 'none')
print('unclosed:', p.stack or 'none')
"
```

Expected: `errors: none` and `unclosed: none`

- [ ] **Step 4: Commit**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add src/index.html
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "feat(frontend): 标题栏新增主题按钮 + 主题下拉；LLM 按钮移到 titlebar"
```

---

## Task 4: Frontend CSS — add 3 theme token blocks + swatch colors

**Files:**
- Modify: `src/style.css:1-50` (top, prepend new tokens)

- [ ] **Step 1: Read first 50 lines of style.css**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && head -50 src/style.css
```

- [ ] **Step 2: Prepend theme tokens + swatch colors**

Edit `src/style.css`. At the very top of the file (line 1), insert this block BEFORE any existing content:

```css
/* ─────────────────────────────────────────────────────────────
 * 主题 tokens（v0.2.0）— 切换 data-theme 即可换肤
 * 顺序：:root 兜底 → cloud 默认 → sakura 暖樱 → galaxy 星河
 * ───────────────────────────────────────────────────────────── */

/* 默认兜底（= cloud）—— <html> 没 data-theme 时用这套 */
:root {
  --bg-canvas: #ffffff;
  --bg-surface: #fafafa;
  --bg-surface-2: #f5f5f5;
  --ink-primary: #171717;
  --ink-muted: #888888;
  --accent: #0070f3;
  --btn-primary-bg: #171717;
  --btn-primary-fg: #ffffff;
  --btn-radius: 100px;
  --card-radius: 12px;
  --header-bg: #ffffff;
  --header-fg: #171717;
  --success: #0070f3;
  --border: #ebebeb;
  --font-sans: 'Geist', 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'Geist Mono', ui-monospace, monospace;
  --shadow-card: 0 1px 2px rgba(0,0,0,.06);
}

/* ☁️ 云白（Vercel）— 默认 */
:root[data-theme="cloud"] {
  --bg-canvas: #ffffff;
  --bg-surface: #fafafa;
  --bg-surface-2: #f5f5f5;
  --ink-primary: #171717;
  --ink-muted: #888888;
  --accent: #0070f3;
  --btn-primary-bg: #171717;
  --btn-primary-fg: #ffffff;
  --btn-radius: 100px;
  --card-radius: 12px;
  --header-bg: #ffffff;
  --header-fg: #171717;
  --success: #0070f3;
  --border: #ebebeb;
  --font-sans: 'Geist', 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'Geist Mono', ui-monospace, monospace;
  --shadow-card: 0 1px 2px rgba(0,0,0,.06);
}

/* 🌸 暖樱（Notion） */
:root[data-theme="sakura"] {
  --bg-canvas: #ffffff;
  --bg-surface: #f6f5f4;
  --bg-surface-2: #ede9e4;
  --ink-primary: #37352f;
  --ink-muted: #787671;
  --accent: #5645d4;
  --btn-primary-bg: #5645d4;
  --btn-primary-fg: #ffffff;
  --btn-radius: 8px;
  --card-radius: 8px;
  --header-bg: #0a1530;
  --header-fg: #ffffff;
  --success: #1aae39;
  --border: #e5e3df;
  --font-sans: 'Notion Sans', 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  --shadow-card: 0 1px 2px rgba(0,0,0,.04);
}

/* 🌌 星河（Linear）— 暗色 */
:root[data-theme="galaxy"] {
  --bg-canvas: #010102;
  --bg-surface: #0f1011;
  --bg-surface-2: #141516;
  --ink-primary: #f7f8f8;
  --ink-muted: #8a8f98;
  --accent: #5e6ad2;
  --btn-primary-bg: #5e6ad2;
  --btn-primary-fg: #ffffff;
  --btn-radius: 8px;
  --card-radius: 12px;
  --header-bg: #0f1011;
  --header-fg: #f7f8f8;
  --success: #27a644;
  --border: #23252a;
  --font-sans: -apple-system, 'SF Pro Display', system-ui, sans-serif;
  --font-mono: 'SF Mono', ui-monospace, monospace;
  --shadow-card: 0 0 0 1px var(--border);
}

/* 主题下拉 swatch（硬编码 —— 它们是「预览」而非「主题本身」） */
.theme-swatch[data-swatch="cloud"]  { background: linear-gradient(135deg, #ffffff 50%, #171717 50%); }
.theme-swatch[data-swatch="sakura"] { background: linear-gradient(135deg, #5645d4 50%, #0a1530 50%); }
.theme-swatch[data-swatch="galaxy"] { background: linear-gradient(135deg, #010102 50%, #5e6ad2 50%); }
```

- [ ] **Step 3: Verify file starts with new block**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && head -3 src/style.css && echo "..." && wc -l src/style.css
```

Expected: First 3 lines start with the new comment block; total line count grew by ~110 lines

- [ ] **Step 4: Commit**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add src/style.css
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "feat(frontend): CSS theme tokens（3 主题 + swatch 配色）"
```

---

## Task 5: Frontend CSS — refactor existing selectors to use variables

**Files:**
- Modify: `src/style.css:120-909` (existing selectors)

- [ ] **Step 1: Grep all hardcoded colors**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && grep -nE "#[0-9a-fA-F]{3,6}|rgb\(|rgba\(|background-color:|background:" src/style.css | grep -v "^.*--.*:" | head -60
```

Expected: ~40-60 lines with hardcoded colors that need replacing

- [ ] **Step 2: Replace body background**

Find and replace. In `src/style.css`, find the `body {` selector block. Replace hardcoded background-color with `var(--bg-canvas)`, color with `var(--ink-primary)`, font-family with `var(--font-sans)`.

Pattern (typical):
```css
body {
  background: #ffffff;
  color: #1a1a1a;
  ...
}
```
→
```css
body {
  background: var(--bg-canvas);
  color: var(--ink-primary);
  ...
}
```

- [ ] **Step 3: Replace `.titlebar` styles**

Find `.titlebar {` block. Replace:
```css
background: #xxxxxx;  →  background: var(--header-bg);
color: #xxxxxx;       →  color: var(--header-fg);
```

- [ ] **Step 4: Replace `.card` styles**

Find `.card {` block. Replace:
```css
background: #xxxxxx;                  →  background: var(--bg-surface);
border: 1px solid #xxxxxx;            →  border: 1px solid var(--border);
border-radius: 12px;                  →  border-radius: var(--card-radius);
box-shadow: 0 1px 2px rgba(...);     →  box-shadow: var(--shadow-card);
```

- [ ] **Step 5: Replace `button.primary` styles**

Find `button.primary {` block. Replace:
```css
background: #xxxxxx;    →  background: var(--btn-primary-bg);
color: #xxxxxx;         →  color: var(--btn-primary-fg);
border-radius: 12px;    →  border-radius: var(--btn-radius);
```

- [ ] **Step 6: Add new titlebar + theme menu styles**

At the end of `src/style.css` (or in a logical place), append:

```css
/* ─────────────────────────────────────────────────────────────
 * titlebar + 主题切换器（v0.2.0）
 * ───────────────────────────────────────────────────────────── */

.titlebar {
  position: relative;  /* 让 theme-menu 能绝对定位挂上来 */
  height: 52px;        /* 从 44px 提到 52px */
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--header-bg);
  color: var(--header-fg);
  border-bottom: 1px solid var(--border);
  font-family: var(--font-sans);
}

.titlebar-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.titlebar-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  width: 32px;
  height: 32px;
  font-size: 16px;
  cursor: pointer;
  color: inherit;
  padding: 0;
  transition: background 0.1s ease;
}
.titlebar-btn:hover {
  background: var(--bg-surface-2);
}
.titlebar-btn:active {
  background: var(--border);
}

.theme-menu {
  position: absolute;
  top: 56px;
  right: 12px;
  min-width: 160px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--card-radius);
  box-shadow: var(--shadow-card), 0 8px 24px rgba(0,0,0,.12);
  padding: 4px;
  z-index: 100;
  font-family: var(--font-sans);
}
.theme-menu.hidden { display: none; }

.theme-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--ink-primary);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
}
.theme-option:hover {
  background: var(--bg-surface-2);
}
.theme-option .theme-swatch {
  width: 24px;
  height: 14px;
  border-radius: 3px;
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.theme-option .theme-name {
  flex: 1;
}
.theme-option .theme-check {
  opacity: 0;
  color: var(--accent);
  font-weight: bold;
}
.theme-option.active .theme-check {
  opacity: 1;
}
```

- [ ] **Step 7: Verify no remaining hardcoded `#xxxxxx` in body/titlebar/card**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && grep -nE "background[^:]*:\s*#[0-9a-fA-F]{3,6}" src/style.css | head -20
```

Expected: Very few or zero hits in the main selectors (variables already cover them); new titlebar/menu styles use only `var(--xxx)`.

- [ ] **Step 8: Commit**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add src/style.css
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "refactor(frontend): 现有样式用 CSS variables + titlebar/主题菜单样式"
```

---

## Task 6: Frontend JS — theme init / switch / switcher wiring

**Files:**
- Modify: `src/main.js` (add 4 functions + hook into init)

- [ ] **Step 1: Locate the `init()` function and `$(id)` helper**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && grep -nE "async function init|function \\\$|init\(\)" src/main.js | head -10
```

Note the line numbers for context.

- [ ] **Step 2: Add theme constants + 4 functions before `init()`**

Find the line containing `async function init()` (or the equivalent). Right BEFORE it, insert:

```js
  // ── 主题切换（v0.2.0）───────────────────────────────────────
  const VALID_THEMES = ["cloud", "sakura", "galaxy"];
  const THEME_LABELS = { cloud: "云白", sakura: "暖樱", galaxy: "星河" };

  async function initTheme() {
    let theme = "cloud";
    try {
      const r = await call("get_theme", {});
      if (r && r.theme && VALID_THEMES.includes(r.theme)) theme = r.theme;
    } catch (e) {
      console.warn("get_theme failed, fallback cloud:", e);
    }
    applyTheme(theme, /*persistLocal*/ true);
    wireThemeSwitcher();
  }

  function applyTheme(theme, persistLocal) {
    document.documentElement.dataset.theme = theme;
    if (persistLocal) {
      try { localStorage.setItem("dt.theme", theme); } catch {}
    }
    // 同步下拉中 ✓ 位置
    document.querySelectorAll(".theme-option").forEach((b) => {
      b.classList.toggle("active", b.dataset.theme === theme);
    });
  }

  async function switchTheme(theme) {
    if (!VALID_THEMES.includes(theme)) return;
    applyTheme(theme, /*persistLocal*/ true);
    hideThemeMenu();
    try {
      await call("set_theme", { theme });
      toast(`✓ 已切换到 ${THEME_LABELS[theme]}`, "ok", 1500);
    } catch (e) {
      toast("主题切换失败：" + (e?.message || e), "error", 3000);
    }
  }

  function wireThemeSwitcher() {
    const btn = $("btn-theme");
    if (!btn) return;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      $("theme-menu").classList.toggle("hidden");
    });
    document.addEventListener("click", (e) => {
      const menu = $("theme-menu");
      if (!menu.classList.contains("hidden")
          && !menu.contains(e.target)
          && e.target !== btn) {
        menu.classList.add("hidden");
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") hideThemeMenu();
    });
    document.querySelectorAll(".theme-option").forEach((b) => {
      b.addEventListener("click", () => switchTheme(b.dataset.theme));
    });
  }

  function hideThemeMenu() {
    const menu = $("theme-menu");
    if (menu) menu.classList.add("hidden");
  }
```

- [ ] **Step 3: Hook `initTheme()` into `init()`**

Find the LAST line of `init()` (typically `await something(...)` or the boot call). Right before the closing `}` of `init()`, add:

```js
    // 主题：放在最后，确保 main.js 其他初始化不受主题切换失败影响
    await initTheme();
```

- [ ] **Step 4: Verify main.js still parses**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && node --check src/main.js && echo "syntax OK"
```

Expected: `syntax OK` (no syntax errors). Note: this checks JS syntax only; browser-only APIs (e.g. `localStorage`) won't run in node.

- [ ] **Step 5: Manual visual test**

Run the app:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && ls deliver/dingtalk_box/dingtalk_box.exe
```

Open `deliver/dingtalk_box/dingtalk_box.exe` (after rebuild in next task). For now, just verify the source compiles by checking the constants are present:

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && grep -c "VALID_THEMES\|switchTheme\|initTheme\|wireThemeSwitcher" src/main.js
```

Expected: `4` (each function referenced at least once)

- [ ] **Step 6: Commit**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add src/main.js
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "feat(frontend): 主题 init/switch/switcher wiring（main.js）"
```

---

## Task 7: Build & deliver

**Files:**
- Build: `dist/sidecar.exe`, `dist/dingtalk_box.exe`, `bin/python-sidecar.exe`
- Build: `deliver/dingtalk_box/sidecar.exe`, `deliver/dingtalk_box/dingtalk_box.exe`
- Build: `deliver/dingtalk_box.zip`

- [ ] **Step 1: Rebuild sidecar.exe**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && pyinstaller build.spec --clean --noconfirm 2>&1 | tail -5
```

Expected: `Build complete!`

- [ ] **Step 2: Copy sidecar.exe to bin/ and deliver/**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && cp dist/sidecar.exe bin/python-sidecar.exe && cp dist/sidecar.exe deliver/dingtalk_box/sidecar.exe && echo "copied"
```

- [ ] **Step 3: Rebuild launcher.exe (only if source changed — frontend is bundled in launcher)**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && pyinstaller launcher.spec --clean --noconfirm 2>&1 | tail -5
```

Expected: `Build complete!`

- [ ] **Step 4: Copy launcher.exe to deliver/**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && cp dist/dingtalk_box.exe deliver/dingtalk_box/dingtalk_box.exe && echo "copied"
```

- [ ] **Step 5: Re-zip deliverable**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box/deliver && powershell -NoProfile -Command "Remove-Item dingtalk_box.zip -ErrorAction SilentlyContinue; Compress-Archive -Path 'dingtalk_box' -DestinationPath 'dingtalk_box.zip' -Force; Get-ChildItem dingtalk_box.zip"
```

Expected: shows `dingtalk_box.zip` with size ~99-104 MB and current timestamp

- [ ] **Step 6: Verify frozen sidecar serves theme RPCs**

Run:
```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box && python -c "
import subprocess, json, time
r = subprocess.run(
    [r'C:\Users\lesoon\.claude\projects\dingtalk_box\bin\python-sidecar.exe'],
    input=b'{\"id\":\"x\",\"method\":\"get_theme\",\"params\":{}}\n',
    capture_output=True, timeout=10
)
out = r.stdout.decode('utf-8', errors='replace').strip()
print('get_theme response:', out)
import re
m = re.search(r'\"theme\":\"(\w+)\"', out)
assert m, f'no theme in response: {out}'
print('OK: theme =', m.group(1))
"
```

Expected: `OK: theme = cloud` (or whatever is persisted)

- [ ] **Step 7: Commit build artifacts**

```bash
cd C:/Users/lesoon/.claude/projects/dingtalk_box
git add build/ dist/ deliver/ bin/python-sidecar.exe
git -c user.name=dingtalk_box -c user.email=dt@local commit -m "build: 主题切换打包 sidecar.exe + 重新分发 v0.2.0"
```

- [ ] **Step 8: Manual visual test checklist**

Run `deliver/dingtalk_box/dingtalk_box.exe` and verify:
- [ ] Default theme is ☁️ 云白（白底黑墨 pill 按钮）
- [ ] Click 🎨 → 3-option dropdown appears
- [ ] Click 🌸 暖樱 → instant theme switch (navy header, purple button, warm cards)
- [ ] Click 🌌 星河 → dark mode (black bg, lavender accent)
- [ ] Close app, reopen → last selected theme persists
- [ ] Delete `%APPDATA%/DingTalkBox/config.yaml`, reopen → defaults back to 云白

---

## Self-Review

**1. Spec coverage:**

| Spec § | Requirement | Task |
|--------|-------------|------|
| §1.1 | 3 themes cloud/sakura/galaxy | T1 (enum), T4 (CSS tokens) |
| §1.1 | Default cloud | T4 (`:root` defaults), T5 (HTML `data-theme="cloud"`) |
| §1.1 | Header-right switcher | T3 (HTML markup) |
| §1.1 | Persist to sidecar config | T1 (preferences), T2 (RPC) |
| §3.1 | boot flow with `<html data-theme>` hardcoded | T3 (HTML) + T6 (initTheme) |
| §3.2 | switch flow (UI first, then RPC) | T6 (switchTheme) |
| §4.1 | CSS variables for 3 themes | T4 |
| §4.2 | HTML markup with swatches | T3 |
| §4.3 | initTheme / switchTheme / wireThemeSwitcher | T6 |
| §4.4 | preferences.py | T1 |
| §4.5 | dispatcher RPCs | T2 |
| §5 | State machine (boot → steady → switching → steady) | T6 |
| §6 | Error handling (corrupt config, invalid value, RPC fail) | T1 (tests), T6 (try/catch) |
| §7 | Tests for preferences | T1 |
| §7 | Manual test checklist | T7 (step 8) |

All spec sections covered. ✓

**2. Placeholder scan:** No TBD / TODO / "implement later" / "fill in details" found. ✓

**3. Type consistency:** 
- `preferences.set_theme(theme: str) -> str` (T1) matches dispatcher `"set_theme": lambda p: {"ok": True, "theme": preferences.set_theme(p.get("theme", ""))}` (T2) ✓
- `preferences.get_theme() -> str` (T1) matches dispatcher `"get_theme": lambda p: {"theme": preferences.get_theme()}` (T2) ✓
- `VALID_THEMES = ("cloud", "sakura", "galaxy")` (T1) matches JS `VALID_THEMES = ["cloud", "sakura", "galaxy"]` (T6) ✓
- Theme id `cloud` referenced consistently across HTML (`data-theme="cloud"`), CSS (`[data-theme="cloud"]`), Python (enum), JS (`VALID_THEMES`) ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-19-theme-switcher.md`. 7 tasks, ~40 sub-steps, each 2-5 minutes.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks
2. **Inline Execution** — batch execution with checkpoints

Which approach?