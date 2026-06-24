# 钉钉工具箱 · 主题切换（云白 / 暖樱 / 星河）

- **日期**：2026-06-19
- **状态**：设计中（待 review）
- **范围**：单 spec，覆盖前端 `style.css` / `index.html` / `main.js` + sidecar `preferences.py` / `config.py` / `dispatcher.py`
- **备份**：当前 UI 已存档到 git tag `v0.1.0-pre-redesign`（commit `d302f45`），可 `git checkout v0.1.0-pre-redesign` 回滚

---

## 1. 目标 & 非目标

### 1.1 目标

让钉钉工具箱提供 3 套视觉主题，用户可在右上角一键切换并持久化：

| 主题 id | 中文名 | 取自 | 气质 |
|---------|--------|------|------|
| `cloud` | ☁️ 云白 | Vercel | 现代极简、白底黑墨、晨光感 |
| `sakura` | 🌸 暖樱 | Notion | 暖紫友好、温暖、生产力 |
| `galaxy` | 🌌 星河 | Linear | 暗色专业、薰衣草蓝、夜猫子 |

**默认主题：`cloud`（云白）**

行为：
1. 首次启动默认云白
2. 右上角图标按钮 → 下拉切换，瞬间生效（无需 reload）
3. 主题选择持久化到 sidecar config.yaml，重启 / 重装都保留
4. 切主题不影响数据、不影响 dws 登录态、不影响 LLM 配置

### 1.2 非目标（YAGNI）

- 不做主题导入 / 导出 / 分享
- 不做第 4 个主题（先收敛到 3 个）
- 不做用户自定颜色
- 不做主题切换动画 / transition（避免性能 + 复杂度）
- 不做暗黑模式自动跟随系统（始终手动）
- 不做侧边栏 / 抽屉 / 弹窗等布局变化（只换色 / 字体 / 圆角 / 间距 token）
- 不做 Notion / Linear / Vercel 商标露出（不显示「Vercel-style」等字样，避免商标问题）

---

## 2. 架构总览

不增加新进程，只改前端 CSS / JS + sidecar 1 个新模块 + 2 个新 RPC：

```
[前端 src/index.html]
  <html data-theme="cloud">    ← 默认；JS 启动后会被覆盖为 saved theme
  <header class="titlebar">
    <span class="title">🔧 钉钉工具箱</span>
    <div class="titlebar-actions">
      <button id="btn-theme" title="切换主题">🎨</button>  ← 新增
      <button id="btn-llm-settings" …>…</button>           ← 从 user-card 移上来
    </div>
  </header>

[前端 src/style.css]  (~909 行 → ~1100 行：CSS variables 化)
  :root[data-theme="cloud"]   { --bg-canvas: #ffffff; … }   ← 新增
  :root[data-theme="sakura"]  { --bg-canvas: #ffffff; … }
  :root[data-theme="galaxy"]  { --bg-canvas: #010102; … }
  其他选择器全部改用 var(--xxx)

[前端 src/main.js]
  async function initTheme() {
    let theme = "cloud";
    try {
      const r = await call("get_theme", {});
      if (r.theme) theme = r.theme;
    } catch {}
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("dt.theme", theme);  // 兜底
    wireThemeSwitcher();  // 绑下拉事件
  }
  async function switchTheme(t) {
    document.documentElement.dataset.theme = t;  // 瞬间生效
    localStorage.setItem("dt.theme", t);
    try { await call("set_theme", { theme: t }); } catch {}
  }

[sidecar/core/preferences.py]  (NEW, ~40 行)
  VALID = {"cloud", "sakura", "galaxy"}
  DEFAULT = "cloud"
  def get_theme() -> str: …
  def set_theme(theme: str) -> None: …    # 校验 + 写 config.yaml
  # 读不到/损坏 → 返回 DEFAULT

[sidecar/core/config.py]   (复用，无改动)
  config.get("preferences.theme", "cloud")   # 走现有 YAML 读写

[sidecar/core/dispatcher.py]
  +2 RPC: "get_theme" → preferences.get_theme
          "set_theme" → preferences.set_theme
```

**关键不变量**：
- `<html data-theme="cloud">` 硬编码默认值（即使 JS 没跑也对）
- 后端 enum 校验：非法值抛 ValueError，不写盘
- 切换是「先 UI 后 RPC」，UI 永远不阻塞
- localStorage + config.yaml 双写：localStorage 防止页面 reload 闪，config.yaml 防止重装丢

---

## 3. 数据流

### 3.1 boot 流程

```
[launcher.exe] 启动
    │
    ▼
[sidecar.exe] 启动 → 读 config.yaml
    │
    ▼
[浏览器 / pywebview] 加载 index.html
    │
    │  <html data-theme="cloud">   ← 硬编码默认（保证首屏不白屏/不黑屏）
    │  加载 style.css → cloud 主题生效
    │
    ▼
[main.js] 启动 → call("get_theme", {})
    │
    ▼
[sidecar dispatcher] → preferences.get_theme()
    │
    ▼ 读 config.yaml preferences.theme
    │ （不存在/损坏 → "cloud"）
    │
    ▼ 返 { theme: "sakura" }
    │
[main.js] document.documentElement.dataset.theme = "sakura"
    │ localStorage.setItem("dt.theme", "sakura")
    ▼
   主题切换完成（无 reload）
```

### 3.2 用户切换主题

```
[用户点击 🎨 按钮] → 显示下拉
[用户点击 🌸 暖樱]
    │
    ▼
[main.js] switchTheme("sakura")
    │
    │ document.documentElement.dataset.theme = "sakura"  ← 瞬间换肤
    │ localStorage.setItem("dt.theme", "sakura")
    │ await call("set_theme", { theme: "sakura" })
    │
    ▼
[sidecar dispatcher] → preferences.set_theme("sakura")
    │
    │ enum check: "sakura" in {cloud, sakura, galaxy}  ✓
    │ config.load() → data["preferences"]["theme"] = "sakura"
    │ config.save(data)
    │
    ▼ 返 { ok: true, theme: "sakura" }
    │
[main.js] 关下拉，toast("✓ 已切换到 🌸 暖樱", "ok", 1500)
```

### 3.3 config.yaml 结构（after）

```yaml
version: 1
llm:
  active_provider: MiniMax
  model: MiniMax-M2
preferences:           ← 新段
  theme: sakura        ← 新字段
```

老 config 没有 `preferences` 段时，`config.get("preferences.theme", "cloud")` 返回 `"cloud"`，无需手动 merge。

---

## 4. 模块拆分

### 4.1 src/style.css（重写）

新增 3 个 token 块 + swatch 配色：

```css
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

/* 云白（Vercel）— 默认 */
:root[data-theme="cloud"] { /* 同 :root，不重复；保留可读性 */
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

/* 暖樱（Notion）*/
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
  --header-bg: #0a1530;        /* navy */
  --header-fg: #ffffff;
  --success: #1aae39;
  --border: #e5e3df;
  --font-sans: 'Notion Sans', 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  --shadow-card: 0 1px 2px rgba(0,0,0,.04);
}

/* 星河（Linear）— 暗色 */
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
```

**所有现有选择器改为 var(--xxx)**：
- `.titlebar { background: var(--header-bg); color: var(--header-fg); }`
- `.card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--card-radius); box-shadow: var(--shadow-card); }`
- `button.primary { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-radius: var(--btn-radius); }`
- `body { background: var(--bg-canvas); color: var(--ink-primary); font-family: var(--font-sans); }`
- `.text-muted { color: var(--ink-muted); }`
- `.border { border-color: var(--border); }`
- 等（约 50 处替换）

**下拉 swatch 配色（硬编码，不走 CSS variables —— 它们是「预览」而非「主题本身」）**：
```css
.theme-swatch[data-swatch="cloud"]   { background: linear-gradient(135deg, #ffffff 50%, #171717 50%); }
.theme-swatch[data-swatch="sakura"]  { background: linear-gradient(135deg, #5645d4 50%, #0a1530 50%); }
.theme-swatch[data-swatch="galaxy"]  { background: linear-gradient(135deg, #010102 50%, #5e6ad2 50%); }
```

**titlebar 高度**：从 44px 提到 52px（容纳 🎨 + 🤖 两个图标按钮）。pywebview 窗口初始高度维持 600px，主内容区相应缩小 ~8px（不影响可用性）。

### 4.2 src/index.html

新增按钮 + 下拉：

```html
<header class="titlebar">
  <span class="title">🔧 钉钉工具箱</span>
  <div class="titlebar-actions">
    <button id="btn-theme" class="titlebar-btn" title="切换主题" aria-label="切换主题">🎨</button>
    <!-- 移自 user-card 的 LLM 设置按钮 -->
    <button id="btn-llm-settings" class="titlebar-btn" title="配置 AI 模型">🤖</button>
  </div>
</header>

<!-- 主题下拉（绝对定位，挂在 titlebar 下） -->
<div id="theme-menu" class="theme-menu hidden">
  <button class="theme-option" data-theme="cloud">
    <span class="theme-swatch" data-swatch="cloud"></span>
    <span class="theme-name">云白</span>
    <span class="theme-check">✓</span>
  </button>
  <button class="theme-option" data-theme="sakura">
    <span class="theme-swatch" data-swatch="sakura"></span>
    <span class="theme-name">暖樱</span>
    <span class="theme-check">✓</span>
  </button>
  <button class="theme-option" data-theme="galaxy">
    <span class="theme-swatch" data-swatch="galaxy"></span>
    <span class="theme-name">星河</span>
    <span class="theme-check">✓</span>
  </button>
</div>
```

注：LLM 设置按钮从 `user-card` 里移走，避免 header / user-card 重复入口。

### 4.3 src/main.js（新增 ~80 行）

```js
const VALID_THEMES = ["cloud", "sakura", "galaxy"];
const THEME_LABELS = { cloud: "云白", sakura: "暖樱", galaxy: "星河" };

async function initTheme() {
  let theme = "cloud";
  try {
    const r = await call("get_theme", {});
    if (r && r.theme && VALID_THEMES.includes(r.theme)) theme = r.theme;
  } catch (e) { console.warn("get_theme failed, fallback cloud", e); }
  applyTheme(theme, /*persistLocal*/ true);
  wireThemeSwitcher();
}

function applyTheme(theme, persistLocal) {
  document.documentElement.dataset.theme = theme;
  if (persistLocal) localStorage.setItem("dt.theme", theme);
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
  $("btn-theme").addEventListener("click", (e) => {
    e.stopPropagation();
    $("theme-menu").classList.toggle("hidden");
  });
  document.addEventListener("click", (e) => {
    if (!$("theme-menu").contains(e.target) && e.target !== $("btn-theme")) {
      $("theme-menu").classList.add("hidden");
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("theme-menu").classList.add("hidden");
  });
  document.querySelectorAll(".theme-option").forEach((b) => {
    b.addEventListener("click", () => switchTheme(b.dataset.theme));
  });
}
```

在现有 `init()` 函数末尾追加 `await initTheme();`。

### 4.4 sidecar/core/preferences.py（NEW）

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

### 4.5 sidecar/core/dispatcher.py

```python
from . import preferences   # 新增 import

# METHODS 字典新增 2 项：
"get_theme": lambda p: {"theme": preferences.get_theme()},
"set_theme": lambda p: {"ok": True, "theme": preferences.set_theme(p.get("theme", ""))},
```

---

## 5. 状态机

```
            ┌──────────────┐
            │  boot        │
            └──────┬───────┘
                   │ <html data-theme="cloud"> 硬编码首屏
                   │ + initTheme() 异步覆盖
                   ▼
            ┌──────────────┐
            │  steady      │  (主题稳定，UI 正常用)
            └──────┬───────┘
                   │ 用户点 🎨 → 点 🌸
                   ▼
            ┌──────────────┐
            │  switching   │  <html data-theme> 立即换
                   │       │  localStorage.setItem
                   │       │  call set_theme RPC
                   ▼       ▼
            ┌──────────────┐
            │  steady      │  (新主题)
            └──────────────┘
```

旁路：
- set_theme RPC 失败 → UI 已换（localStorage 已存），下次启动 fallback 用 localStorage
- reload / F5 → main.js 启动 → 读 get_theme → 用 saved theme → 不闪
- sidecar 死 / config 损坏 → 默认 cloud

---

## 6. 错误处理

| 场景 | 检测点 | 行为 |
|------|--------|------|
| config.yaml 不存在 | `config.ensure_config()` 复制默认 | preferences 段空 → 返回 cloud |
| config.yaml 损坏（YAML 解析失败） | `config.load()` 异常 | preferences.get_theme 兜底 → 返回 cloud |
| set_theme 收到非法值 | `preferences.set_theme` enum check | 抛 `ValueError` → 返 `-32602 INVALID_PARAMS` → 前端忽略 |
| get_theme RPC sidecar 死了 | main.js try/catch | UI 保持 cloud 默认 |
| localStorage 满了 / 禁用 | main.js 静默 try/catch | 不影响切换主流程 |
| 用户硬编码 data-theme 写错 | CSS `:root[data-theme="xxx"]` 不匹配 → 用 :root 兜底（cloud） | 自动 fallback cloud |

---

## 7. 测试

### 7.1 单元测试（新增 `tests/test_preferences.py`）

```python
def test_get_theme_default(monkeypatch, tmp_path):
    """空 config → 默认 cloud"""
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path)
    assert preferences.get_theme() == "cloud"

def test_set_then_get(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path)
    preferences.set_theme("sakura")
    assert preferences.get_theme() == "sakura"

def test_set_invalid_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="未知 theme"):
        preferences.set_theme("neon")

def test_set_then_recover_from_corrupt(monkeypatch, tmp_path):
    """config.yaml 损坏 → get_theme 不抛，返回 default"""
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text(":\n  - bad", encoding="utf-8")
    assert preferences.get_theme() == "cloud"

def test_set_overwrites_old_value(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path)
    preferences.set_theme("galaxy")
    preferences.set_theme("cloud")
    assert preferences.get_theme() == "cloud"
```

### 7.2 手动测试 checklist

- [ ] 首次启动 → 看到 ☁️ 云白（白底黑墨 pill 按钮）
- [ ] 点 🎨 → 下拉显示 3 个主题，云白前有 ✓
- [ ] 点 🌸 暖樱 → 整个 UI 瞬间换：header 深蓝、按钮变紫、卡片 tint 变暖、字体可能变 Notion Sans
- [ ] 点 🌌 星河 → 全暗色：黑底白字、薰衣草蓝强调、按钮 8px 圆角（非 pill）
- [ ] 重启工具 → 保留上次选择
- [ ] 删 config.yaml 重新启动 → 回到 cloud 默认
- [ ] F5 / reload → 主题不变（无闪）
- [ ] header 上的 🤖 按钮（AI 设置）仍能正常工作（移上来后没破坏功能）

---

## 8. 实施计划

下一步走 `superpowers:writing-plans` 写详细实施步骤。

---

## 9. 不确定点（待实测）

- **中文字体在 3 主题下的实际渲染**：Geist / Notion Sans / SF Pro 都缺中文字形，会 fallback 到系统中文字体。视觉差异主要在英文字符 + 标题字号字距上。中文字号一致。
- **CSS variables 改动量**：现有 style.css 909 行，硬编码颜色 / 圆角 / 阴影散落各处。统计后预估 ~50 处替换。
- **header 高度**：从当前 ~44px 提到 ~52px（容纳两个图标按钮），需要确认 pywebview 窗口最小高度仍合适（当前 600px）。
- **emoji 渲染**：🎨 / 🌸 / 🌌 在不同 Windows 版本可能显示为黑白 / 彩色 / 方形。需要回退方案（标题用纯文字 + 颜色 swatch）。