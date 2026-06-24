# 钉钉工具箱 v0.3.0 — 重大 UI 重构 设计规格

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 v0.2.1 的单列 pywebview 单页布局重写为横屏 sidebar + main grid 布局（按第二份 Figma-grade 设计系统），彻底删除主题切换器，保留所有现有业务功能。

**Architecture:** 前端单文件重写（src/index.html + src/style.css + src/main.js），后端只删 `get_theme` / `set_theme` RPC 和 `preferences.py` 模块，其他 16 个 RPC 完全保留。

**Tech Stack:** HTML5 + CSS3（Grid/Flex）+ 原生 JavaScript（ES2020）+ pywebview（不变）

---

## 1. 设计原则（来自第二份 spec）

### 1.1 设计语言

- AI Copilot Interface
- Soft Glass Dashboard
- Light + Gradient + Floating Cards
- Human + AI 共存界面
- 企业工具但"非传统 ERP 感"

### 1.2 核心设计原则

- 减少"表格感"，增强"卡片呼吸感"
- AI 模块必须视觉优先级最高
- 所有信息必须"轻提示化"
- 尽量避免硬边框（borderless UI）
- 所有交互必须有"柔反馈"

### 1.3 全程按设计图修改代码

本次重构**不自由发挥**任何视觉元素。所有颜色、字号、间距、阴影、动效、图标必须严格匹配第二份 Figma-grade 设计系统。如有歧义，先问用户。

---

## 2. 布局规范（Layout System）

### 2.1 页面分区

| 区域 | 宽度 | 说明 |
|------|------|------|
| Sidebar | **240px 固定** | 左侧导航 |
| Main Content | 自适应 | 右侧内容 |
| Center Max Width | **1120–1200px 居中** | 大屏限制 |

### 2.2 栅格系统（12 Column）

- Margin: 24px
- Gutter: 16px
- Column: auto fit

### 2.3 间距层级

- 卡片间距: 16 / 24px
- 区块间距: 24 / 32 / 40px（层级递进）

### 2.4 设计 Frame

- Desktop: **1440 × 900**（主设计）
- Safe Area: 24px

### 2.5 布局结构（CSS Grid）

```
┌──────────────────────────────────────────────────────────────┐
│ ┌─aside (240px)──┐  ┌─────── main (auto, max 1120–1200px) ─┐ │
│ │ Logo + 品牌     │  │ ┌──── topbar (60px) ─────────────┐ │ │
│ │                │  │ │ (空)              👤 头像 → 🤖 LLM│ │ │
│ │ ── nav ──      │  │ └────────────────────────────────┘ │ │
│ │ 📊 日报生成     │  │                                       │ │
│ │ 📁 历史报告     │  │ ┌─── welcome hero (180px) ────────┐ │ │
│ │                │  │ │ 用户信息              AI 机器人   │ │ │
│ │ ── 留白 ──     │  │ └────────────────────────────────┘ │ │
│ │                │  │                                       │ │
│ │ ── footer ──   │  │ ┌─── tab content (1fr) ──────────┐ │ │
│ │ 🤖 AI 助手卡    │  │ │ 当前 tab 内容（生成表单/历史） │ │ │
│ │  随时生成日报   │  │ │                                │ │ │
│ │                │  │ └────────────────────────────────┘ │ │
│ │                │  │                                       │ │
│ │  v0.3.0        │  │  ● 就绪                       v0.3.0 │ │
│ └────────────────┘  └───────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 2.6 响应式断点

| 宽度 | 行为 |
|------|------|
| ≥ 1024px | 横屏布局（如上） |
| < 1024px | Sidebar 折叠为汉堡菜单，点击展开抽屉式 overlay |

---

## 3. 颜色系统（Design Tokens，直接写 hex）

### 3.1 主品牌色（AI Blue）

| Token | 值 | 用途 |
|-------|----|----|
| Primary | `#3B82F6` | 按钮、选中态、强调 |
| Hover | `#2563EB` | 按钮 hover |
| Pressed | `#1D4ED8` | 按钮按下 |
| Soft BG | `#EAF2FF` | Primary 背景 tint |

### 3.2 AI 渐变系统（重点视觉资产）

```css
background: linear-gradient(
  135deg,
  #E8F0FF 0%,
  #F5F3FF 50%,
  #EEF7FF 100%
);
```

用途：顶部欢迎卡、AI 助手背景、关键视觉容器。

### 3.3 Glass Card（新组件）

```css
background: rgba(255,255,255,0.72);
backdrop-filter: blur(12px);
border: 1px solid rgba(255,255,255,0.4);
```

### 3.4 中性色（Neutral System）

| 级别 | 颜色 | 用途 |
|------|------|------|
| 900 | `#111827` | 主标题 |
| 700 | `#374151` | 正文 |
| 500 | `#6B7280` | 辅助文字 |
| 300 | `#D1D5DB` | 分隔线 |
| 100 | `#F3F4F6` | 输入框背景、表格分隔 |

### 3.5 状态色

| 状态 | 颜色 |
|------|------|
| Success | `#22C55E` |
| Warning | `#F59E0B` |
| Danger | `#EF4444` |
| Info | `#3B82F6` |

### 3.6 背景色

| 用途 | 值 |
|------|---|
| 页面背景 | `#F7F9FC` |
| 卡片背景 | `#FFFFFF` |
| 输入框背景 | `#F9FAFB` |
| Hover 背景 | `#F2F6FF` |

---

## 4. 字体系统（Typography）

### 4.1 字体栈

```css
font-family: "PingFang SC", "Microsoft YaHei", "HarmonyOS Sans",
             Inter, "SF Pro", system-ui, sans-serif;
```

### 4.2 Type Scale（6 级）

| 层级 | 字号 | 字重 | 用途 |
|------|------|------|------|
| Display | **28px** | 600 | 欢迎标题（"你好，张瑞"）|
| H1 | 24px | 600 | 页面主标题 |
| H2 | 20px | 600 | 模块标题 |
| H3 | 16px | 500 | 卡片标题 |
| Body | 14px | 400 | 正文 |
| Caption | 12px | 400 | 辅助信息 |

---

## 5. 圆角与阴影（Shape System）

### 5.1 圆角

| 元素 | 圆角 |
|------|------|
| Button | 10px |
| Card | 16px |
| Modal | 20px |
| Tag | 999px（胶囊）|

### 5.2 阴影

```css
/* 卡片默认 */
box-shadow: 0 2px 10px rgba(0,0,0,0.04);

/* 悬浮 */
box-shadow: 0 10px 30px rgba(0,0,0,0.08);

/* AI 主卡片 */
box-shadow: 0 12px 40px rgba(59,130,246,0.15);
```

---

## 6. 核心组件设计

### 6.1 用户欢迎卡（Hero AI Card）

- **结构**：`[Avatar] 你好，张瑞 👋 / 公司信息 / 状态 Tag（已登录）` 左侧 + `[AI Robot Illustration]` 右侧
- **背景**：AI 渐变 + blur light
- **右侧 AI 机器人浮动**（hover bounce）
- **左侧信息对齐 baseline**
- **尺寸**：Height 180px / Padding 24px / Radius 16px
- **阴影**：AI 主卡片阴影

### 6.2 AI 生成日报模块（核心）

- **标题**：`生成日报 ✨ AI 助手`
- **日期选择器**：240px 宽 / 40px 高 / 圆角输入框 / 日历 icon / 默认当天
- **说明提示**：轻灰文字（Caption）
- **主按钮**：
  - height: 44px
  - border-radius: 12px
  - background: `linear-gradient(90deg, #3B82F6, #6366F1)`
  - color: white
  - font: 14px / 600
  - **Hover**: scale(1.02) + 阴影增强
  - **Loading**: "AI 生成中..." + loading dots + shimmer sweep

### 6.3 历史报告列表（Table Card）

- **结构**：卡片标题 + 表格行
- **Row**：`日期 | 文件大小 | 操作`
- **Row 设计**：height 48px / hover 浅蓝背景 / 分隔线 1px `#F3F4F6`
- **操作按钮**：
  - 查看（ghost button）
  - 发送（primary outline）

### 6.4 AI 助手入口卡（Sidebar 底部）

- **小型卡片**：机器人头像 + 文案"随时帮你生成日报"
- **特性**：floating 动效（2px 上下浮动）+ glow pulse（5–8s 周期）+ hover scale
- **位置**：Sidebar 底部

---

## 7. 图标系统（Icon Style）

- **风格**：线性 + 圆角 + **1.8px stroke** + rounded caps + no sharp corners
- **来源**：Lucide Icons + Phosphor Icons
- **实现**：内嵌 SVG（避免 emoji 渲染不一致）
- **复用**：[src/icon.svg](src/icon.svg)（如存在）或 inline SVG

---

## 8. 动效系统（Motion Design）

### 8.1 基础动效

| 动作 | 动效 |
|------|------|
| hover | `scale(1.02)` + 阴影增强 |
| click | `scale(0.98)` |
| card enter | `fade + translateY(6px)` |

### 8.2 AI 特效（重点）

- **AI 生成按钮**：shimmer sweep（横向光带扫过）+ gradient moving
- **AI 卡片**：soft floating（2px 上下浮动循环）+ glow pulse（5–8s 周期）
- **AI 机器人**：hover bounce

### 8.3 动效实现

- 所有动效用 CSS transition / animation 实现
- 不用 JS 动画库
- 默认 duration: 200-300ms
- easing: `cubic-bezier(0.4, 0, 0.2, 1)`

---

## 9. 信息层级

视觉优先级：

1. **AI 生成按钮**（最强视觉权重）
2. 当前日期模块
3. 用户欢迎卡
4. 历史记录
5. 侧边辅助入口

---

## 10. 适配规范

| 分辨率 | 行为 |
|--------|------|
| 1920+ | 宽屏优化，内容 max-width 1120–1200px 居中 |
| **1440 × 900** | 最佳 |
| 1280 × 720 | 最小（不溢出） |
| < 1024px | Sidebar 折叠为汉堡菜单 |

---

## 11. 错误处理（4 层）

| 层级 | 场景 | 呈现 | 持续 | 操作 |
|------|------|------|------|------|
| L1 Toast | 普通提示 | 右下角浮窗（Glass Card） | 3s | 自动消失 |
| L2 Inline | 表单错误 | 输入框下方红字 + 红 border | 持续 | 重新输入 |
| L3 Card | 功能错误 | tab 内错误卡（红 border + 重试） | 持续 | 关闭/重试/诊断 |
| L4 Modal | corp 不匹配 | 全屏阻断 modal | 持续 | 重新登录/退出 |

**L1 Toast 设计**：
- 位置：右下角（24px 边距）
- 样式：Glass Card + radius 12px + 阴影
- 颜色：success/warning/error/info（对应状态色左边框）
- 动画：fade + translateY(6px) 进入

**L3 Error Card 设计**：
```css
background: #FFFFFF;
border-left: 4px solid #EF4444;
border-radius: 12px;
padding: 16px 20px;
box-shadow: 0 2px 10px rgba(0,0,0,0.04);
```

**L4 保留 v0.2.1 行为**：全屏 modal + 半透明遮罩 + 不可点空白关闭

---

## 12. 数据流与状态管理

### 12.1 tab 切换机制

- **纯 CSS 控制可见性**：每个 nav tab 对应 `<section class="tab-pane" data-tab="x">`，用 `[data-tab].active` 选择器显示
- **JS 只同步状态**：点击 nav 项 → 给对应 `.tab-pane` 加 `.active` + nav-item 加 `.active`
- **不引入 router**：pywebview 单页，不用 hash/history 路由

### 12.2 数据获取策略

| Tab | 何时加载 | RPC |
|-----|----------|-----|
| 日报生成 | 进入应用 | `get_login_status` / `validate_corp` |
| 历史报告 | 进入 tab 时 + tab 重新激活时刷新 | `list_history` |

### 12.3 全局状态（持续）

- 登录状态
- LLM 配置（modal 打开时拉取）

### 12.4 模块间通信

- 自定义事件：`window.dispatchEvent(new CustomEvent('daily-generated', { detail: ... }))` → 历史 tab 监听自动刷新
- 不引入 EventBus 框架

---

## 13. 测试策略

### 13.1 单元测试

- **保留**：所有非主题相关测试（约 52 个）
- **删除**：`tests/test_preferences.py`（7 个主题测试）→ 整个文件
- **目标**：≥ 52 个测试全过

### 13.2 集成测试（手动 smoke test）

按 16 个 RPC 列表逐个调一次验证：
- 登录: get_login_status / trigger_login / trigger_logout / validate_corp
- 日报: generate_daily / send_to_dingtalk / list_history / open_output
- 诊断: diagnose / read_text_file
- dws: check_dws_install / install_dws
- LLM: get_llm_config / get_daily_config / set_llm_config / test_llm_connection / ai_analyze / list_providers

### 13.3 视觉测试（用户手动）

按设计稿逐项核对（见第 6 节组件设计）

### 13.4 响应式测试

- 默认 1440×900 / 最小 1280×720 / < 1024px 折叠

---

## 14. 迁移清单

### 14.1 删除

| 位置 | 删除内容 |
|------|----------|
| `sidecar/core/dispatcher.py` | `from . import preferences`；`get_theme` / `set_theme` RPC |
| `sidecar/core/preferences.py` | **整个文件** |
| `src/index.html` | 🎨 按钮；`#theme-menu`；`<html data-theme="cloud">`；旧 titlebar |
| `src/style.css` | `:root[data-theme="x"]` 块；titlebar / theme-menu 样式 |
| `src/main.js` | `initTheme` / `applyTheme` / `switchTheme` / `wireThemeSwitcher`；`VALID_THEMES` / `THEME_LABELS`；boot 末尾 `await initTheme()` |
| `tests/test_preferences.py` | **整个文件** |

### 14.2 修改

| 文件 | 改动 |
|------|------|
| `src/index.html` | 重写为 sidebar + main grid |
| `src/style.css` | 重写为单主题 + 设计 token + Glass Card + 动效 |
| `src/main.js` | 删主题逻辑 + 加 tab 切换 + 保留业务逻辑 |

### 14.3 保留

- sidecar/core/* (除 preferences.py 外)
- dispatcher.py 其他 16 个 RPC
- ai_bridge/* / launcher.py / assets/*
- tests/* 52 个测试
- build.spec / launcher.spec

### 14.4 发版

- 重打 sidecar.exe + dingtalk_box.exe + zip
- Tag `v0.3.0`
- 回滚 tag 保留: `v0.2.1` / `v0.2.0` / `v0.1.0-pre-redesign`

---

## 15. 不做什么（YAGNI）

- **不做** 多主题切换（已删除）
- **不做** router（pywebview 单页够用）
- **不做** EventBus 框架（CustomEvent 够用）
- **不做** JS 动画库（CSS transition / animation 够用）
- **不做** SVG icon 库打包（直接 inline）
- **不做** 模板中心 / 设置中心 / 关于我们 nav 项（用户确认删除）
- **不做** AI 聊天弹窗（AI 助手 mini 卡仅装饰）
- **不做** 响应式 < 1024px 的双布局（统一汉堡菜单）

---

## 16. 验收清单

### 16.1 视觉验收（按设计稿逐项）

- [ ] Sidebar 240px 固定，左侧
- [ ] Logo + 品牌区在 sidebar 顶部
- [ ] Nav 只 2 项：📊 日报生成 / 📁 历史报告
- [ ] 顶栏右侧顺序：👤 头像 → 🤖 LLM 配置
- [ ] Welcome 卡片 180px 高，AI 渐变背景，Glass 效果
- [ ] 用户信息左对齐，AI 机器人右浮动
- [ ] 日报生成按钮：渐变蓝 + 44px 高 + 圆角 12px
- [ ] 历史报告：表格卡 + 行高 48px + hover 浅蓝
- [ ] AI 助手 mini 卡：sidebar 底部 + floating + glow pulse
- [ ] 颜色严格匹配第 3 节 token
- [ ] 字体严格匹配第 4 节 type scale
- [ ] 圆角严格匹配第 5 节
- [ ] 阴影严格匹配第 5 节
- [ ] 图标线性 1.8px stroke（Lucide / Phosphor）

### 16.2 动效验收

- [ ] hover: scale(1.02) + 阴影增强
- [ ] click: scale(0.98)
- [ ] card enter: fade + translateY(6px)
- [ ] AI 生成按钮: shimmer sweep
- [ ] AI 卡片: soft floating 2px
- [ ] AI 机器人: hover bounce

### 16.3 功能验收

- [ ] 所有 16 个 RPC 正常工作
- [ ] L1/L2/L3/L4 错误处理正常
- [ ] tab 切换保留滚动位置（v0.2.1 行为保留）

### 16.4 响应式验收

- [ ] 默认 1440×900 横屏布局完整
- [ ] 1280×720 内容不溢出
- [ ] 1920+ 内容居中 max-width 1120-1200px
- [ ] < 1024px sidebar 折叠为汉堡菜单

### 16.5 测试验收

- [ ] ≥ 52 个单元测试全过
- [ ] 16 个 RPC smoke test 全过
- [ ] 视觉验收清单全过
- [ ] 响应式验收清单全过

---

**版本**: v0.3.0
**日期**: 2026-06-19
**回滚路径**: v0.2.1 / v0.2.0 / v0.1.0-pre-redesign