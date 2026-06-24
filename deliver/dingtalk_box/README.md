# 钉钉AI助手（dingtalk_box）v0.3.15

> 公司内部、绿色版单目录、面向全公司员工的钉钉 AI 生产力工具
> 核心能力：拉取当天钉钉消息 → AI 整理成长图日报 → 发回自己

---

## 1. 版本信息

| 项 | 值 |
|----|----|
| 版本 | **v0.3.15**（commit ac87f4b，tag v0.3.15） |
| 构建 | 2026-06-22 |
| 平台 | **Windows 10 / 11**（macOS 端口 spec 已完成，待开发） |
| 架构 | 3 进程：launcher.exe + sidecar.exe + ai_bridge.exe |
| 图标 | `assets/logo.ico`（8 尺寸：24/32/48/64/96/128/192/256，multi-resolution） |

---

## 2. 核心功能

### 2.1 AI 日报（核心）
- **今日日报**：选日期 → 点"生 成 日 报"→ 约 1-2 分钟 → 自动渲染 PNG + MD → 弹出发送确认
- **生成 X 日报**：选历史日期 → 进度卡显示（clock 图标 + 渐变文字）→ 完成后弹发送
- **历史日报**：列出已生成的日报 → 点"预览"（MD 内嵌 markdown-it 渲染，ol/ul 序号正常显示；PNG 调系统工具打开）

### 2.2 内置 AI
- **6 个 Qwen 模型可选**（默认 qwen3.5-flash，按速度/能力分组）：
  - qwen3.5-flash（**默认**，极速便宜）
  - qwen3.6-flash（增强版极速）
  - qwen3.6-plus（增强版）
  - qwen3.7-plus（通用主力）
  - qwen3.7-max（全栈 Agent 旗舰，最强）
  - qwen-long（百万上下文）
- **内置默认 key**：开箱即用，**不需要自己申请 API key**
- **用户自定义 key**：在 AI 配置页填自己的 key → 自动用 Windows DPAPI 加密保存
- **v0.3.10 修复**：切模型 + 不动 key 保存 → 不会把脱敏占位串当成新 key 写进去（diff 检测 + skip_key）
- **优先级**：用户自定义 key > 内置 key

### 2.3 预览
- **MD 文件**：内嵌 markdown-it 渲染（GitHub 风格，表格 / 有序列表 / 无序列表 / 代码块 / 引用 / 链接 / 图片），DOMPurify XSS 兜底
- **PNG 文件**：调系统默认图片查看器打开（与历史日报"打开"按钮行为一致）
- 关闭预览：ESC 键 / 点击 modal 背景

### 2.4 视觉
- 横屏布局：左侧 sidebar（菜单）+ 右侧 main grid
- 单主题（v0.3.0 起统一），3 层背景 + Glass 效果 + 4 层卡片
- 顶栏：钉钉品牌图标（蓝底白闪电）+ sun + user
- 机器人插图：完整机器人 SVG / PNG

---

## 3. 系统要求

| 项 | 要求 |
|----|------|
| OS | Windows 10 1809+ / Windows 11 |
| 架构 | x64 |
| 内存 | 4GB+ |
| 磁盘 | 200MB+ 可用 |
| 钉钉 | 已登录（dws CLI 状态） |
| 网络 | 能访问 dws 阿里云 API + DashScope |

---

## 4. 快速开始

### 4.1 第一次用

```
1. 解压 dingtalk_box_v0.3.10.zip 到任意目录（桌面 / D 盘都行，不要放 ProgramFiles）
2. 双击 dingtalk_box.exe
   → 首次启动自动生成 %APPDATA%\DingTalkBox\.env（内置 key 种子复制）
3. 工具里点"登录钉钉" → 自动开浏览器 → 在钉钉 App 扫码 → 状态栏显示"已登录"
4. 顶部选日期（默认今天）→ 点"生 成 日 报"
   → 约 1-2 分钟 → 弹"📤 发送钉钉"确认框
5. 点确认 → 钉钉收到文件消息（就是您自己）
```

### 4.2 用自己的 API Key（可选）

工具里点"AI 配置"：
- 选厂商：阿里云百炼（DashScope）
- 选模型（默认 qwen3.5-flash）
- 填自己的 API key → 点"测试连接"（绿勾 OK）
- → key 自动 DPAPI 加密保存

> 不填也行。**默认用内置 Qwen key**，开箱即用。
>
> v0.3.10 提示：切换模型时**不必重新填 key**——只改 model、不动 key 直接保存即可，工具会用你原有的 key（不会把脱敏占位串当成新 key 写进去）。

### 4.3 卸载

直接删除 `deliver/dingtalk_box/` 整个目录 + `%APPDATA%\DingTalkBox\` 用户数据目录。**无注册表 / 服务残留**。

---

## 5. 用户数据目录

```
%APPDATA%\DingTalkBox\
├── .env                                       # 首次启动从 exe 旁 .env 自动种子复制（v0.3.9+ 是密文 enc:v1:）
├── config.yaml                                # 首次启动由 sidecar 写入（默认值 hardcode 在 config.py）
├── llm_secret.bin                             # 用户自定义 API Key（v0.3.10 DPAPI 加密，绑 Windows 账号）
├── output\
│   └── YYYY-MM-DD\
│       ├── dingtalk_bundle.json               # 拉取的原始数据
│       ├── report.json                        # 结构化报告
│       ├── 钉钉工作纪要日报-YYYY-MM-DD.png    # 渲染长图
│       └── 钉钉工作纪要日报-YYYY-MM-DD.md     # Markdown
├── logs\
│   ├── sidecar-YYYY-MM-DD.log
│   ├── dws-YYYY-MM-DD.log
│   └── daily_report-YYYY-MM-DD.log
├── cache\
├── bin\                                       # dws 二进制（如已安装）
└── update\                                    # 升级包暂存（未实现）
```

---

## 6. 内置 Qwen 模型速查（v0.3.10）

| 模型 ID | 场景 | 速度 | 质量 |
|---------|------|------|------|
| qwen3.5-flash | **默认** · 通用 / 速度优先 | ⚡⚡⚡ | ★★★ |
| qwen3.6-flash | 增强版 · 速度优先 | ⚡⚡⚡ | ★★★★ |
| qwen3.6-plus | 增强版 · 平衡 | ⚡⚡ | ★★★★ |
| qwen3.7-plus | 通用主力 | ⚡⚡ | ★★★★ |
| qwen3.7-max | 全栈 Agent · 最强 | ⚡ | ★★★★★ |
| qwen-long | 超长聊天记录（百万上下文） | ⚡⚡ | ★★★★ |

**建议**：日报生成用默认 qwen3.5-flash（速度优先）；长聊天记录用 qwen-long；追求质量用 qwen3.7-max。

> v0.3.10 起去掉了 qwen-flash（额度耗尽）、qwen3-coder、qwen3-vl、qwq-plus 等 7 个模型。
> 当前是你账号实测可用的 6 个，按 3.5 → 3.6 → 3.7 → long 分组排列。

---

## 7. 常见问题

**Q: 启动后白屏 / 闪退？**
A: 检查 Windows 版本（需 10 1809+）。右键 dingtalk_box.exe → "以管理员身份运行"试一次。

**Q: 状态栏显示"未登录"？**
A: 工具里点"登录钉钉" → 自动开浏览器 → 钉钉 App 扫码。token 本机缓存约 7 天。

**Q: 提示"未识别当前登录人，请先 dws auth login"？**
A: 同上，重新扫码登录。

**Q: 提示"DPAPI 解密失败" / "密钥已备份为 .bak"？**
A: 第一次用不会。说明你换过 Windows 账号 → 进 AI 配置 → 重新填 API key。

**Q: 启动后没有自动绑定 Qwen 模型？**
A: v0.3.8+ 首次启动会自动把 exe 旁的 .env 复制到 `%APPDATA%\DingTalkBox\.env`。v0.3.9+ 内置 key 是密文（`enc:v1:` 前缀，Argon2id + AES-256-GCM 加密），工具启动时自动解密注入，正常看不到明文。如启动报错，按"诊断"按钮复制错误日志给负责人。

**Q: 切换模型后保存，再生成日报报错"HTTP 401"？**
A: v0.3.10 已修复：切模型不动 key 保存 → diff 检测 + skip_key → 不破坏 secret 文件。如果还报错，说明你保存的 key 本身有问题（账号失效 / key 撤销），进 AI 配置 → 重新填。

**Q: 生成的图发不出去？**
A: 看错误卡片的"详情"或底部"完整响应路径"（dump: 开头）→ 把那段文字发给工具负责人。

**Q: 能发给其他人吗？**
A: 不能。本工具只发给您当前登录的钉钉账号自己。需分享从钉钉手动转发。

**Q: 日报保存在哪？**
A: 工具顶部"打开文件夹" → `%APPDATA%\DingTalkBox\output\YYYY-MM-DD\`。

---

## 8. 技术栈（开发者）

```
launcher.py      Python 3.14 + pywebview 5 (HTML/CSS/JS)
  ↓ JSON-RPC (stdio)
sidecar/         Python 3.14 + stdlib only
  ├─ dispatcher.py     (15+ RPC methods)
  ├─ dws_runner.py     (dws.exe 子进程封装)
  ├─ auth.py           (登录 / corp 校验 · v0.3.10 改 dws 自动开浏览器)
  ├─ daily_report.py   (拉取 + AI + 渲染)
  ├─ send.py           (drive 上传 + send)
  └─ llm_config.py     (DPAPI + env var 双源 · v0.3.10 加 diff 检测 skip_key)
  ↓ subprocess
ai_bridge.exe    Python 3.14 (AI 调用隔离子进程)
  ↓ HTTP
DashScope API    (Qwen 模型 · v0.3.10 精简到 6 个)
```

**前端 vendor**（PyInstaller datas）：
- `src/vendor/markdown-it.min.js` (14.1.0, 121KB)
- `src/vendor/purify.min.js` (DOMPurify 3.1.6, 21KB)

**安全**：
- 路径沙箱：`is_under_output()` 校验，PathTraversal 错误码 -32006
- API key 不入 git（`.env` 在 `.gitignore`）
- API key 不入 exe（`launcher.spec` datas 不含 `.env`）
- XSS 双保险：markdown-it + DOMPurify
- DPAPI 加密用户自定义 key（v0.3.9 起 .env 内置 key 也是密文 enc:v1:）

---

## 9. 已知限制

| 项 | 状态 |
|----|------|
| macOS | 🚧 设计 spec 已完成（commit 65d51cb，5-7 周开发周期），待启动 |
| 自动更新 | ❌ 未实现（手动覆盖 exe） |
| 代码签名 | ❌ 未签（IT 安全策略） |
| 启动加载时间 | ~3 秒（pywebview cold start） |
| 跨用户迁移 | 用户自定义 key 绑 Windows 账号（换账号要重配） |
| dws 免费额度 | 阿里云 DashScope 部分模型有免费额度，耗尽后需用户自配 key |

---

## 10. 升级路径

**v0.3.14 → v0.3.15**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- **群聊消息现在只过滤与你有关的消息**（之前群里所有消息都被 AI 纳入分析 → 日报里充斥大量与你无关的纯讨论，例：别人讨论午餐 / 闲聊 / 其他人的项目）
  - 新规则（在 `build_prompt_template` 的 prompt 里加 4 类筛选规则 + 数据范围段）：
    1. **我发过的消息**（sender == 我的名字/昵称）
    2. **@我的消息**（content 含我的名字/昵称/或 @我的标记）
    3. **@所有人的消息**（content 含 @所有人 / @all / @ALL）
    4. **上下文关联消息**（上述每条往前追溯 10 条，保证内容连贯）
  - 最终只基于 ①②③④ 类消息生成纪要；群里与我无关的纯讨论请忽略
  - 私聊会话（bucket_hint=personal）不受影响，仍然分析全部消息
  - 新增「数据范围」段说明本报告的边界（"本报告基于「和我有关的群聊消息 + 完整的私聊消息」生成，群里未 @我且我也未参与讨论的内容不纳入"）
- 用户视角：日报内容更聚焦，只看跟我有关的事；群里闲聊/纯讨论不再占用日报篇幅
- 验收：dev 模式下生成 1 群 + 1 私聊的合成 bundle（覆盖 4 类规则 + 与我无关场景），AI 按规则筛无误伤

**v0.3.13 → v0.3.14**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- **修复 UI 右下角版本号永远落后于实际版本**：之前版本号散落在 6+ 处（src/index.html 硬编码 v0.3.10、launcher.py 写死 "v0.1.0"、sidecar ping 返 "0.1.0"、send diagnose 返 "0.1.0"、README、git tag），发版要手动改 6 处，漏一处就脱节
  - 症状：用户报「右下角显示 v0.3.10 但实际我已经升级到 v0.3.13」
  - 修复：建立 `version.py` 作为单一真相源 `__version__ = "0.3.14"`，所有运行时（launcher / sidecar ping / sidecar diagnose）都 `from version import __version__` 读它
  - UI 渲染：`src/index.html` footer 占位 `<span id="version-tag">v?</span>`，launcher 启动时 `evaluate_js` 注入 `v0.3.14 · build 2026-06-22 15:45`（版本 + 二进制 mtime）
  - 配套：
    - `sidecar/main.py` 把项目根加进 `sys.path`（dev 模式防御 + 让 sidecar 子进程也能 import version）
    - `build.spec` / `launcher.spec` 把 `version` 加进 `hiddenimports`（PyInstaller frozen 模式打包）
    - `tests/test_version_single_source.py`（10/10 通过）：version.py 存在 + semver 格式 + 3 个 .py 都用 `from version import` + sidecar/main.py 加 ROOT 到 sys.path + 2 个 .spec 都有 hiddenimports + src/ 无 hardcoded 版本号 + dev 模式 PYTHONPATH=ROOT 可 import + dispatcher.ping 用 _VERSION + send.diagnose 用 _VERSION + index.html 有 id="version-tag"
  - 发版检查清单（v0.3.14 起）：**只需要改 version.py 一行 + 重打 3 exe**

**v0.3.12 → v0.3.13**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- 修复「未配置模型」静默 bug：deliver/.env 文件里 # 注释和 `DINGTALK_BOX_DEFAULT_QWEN_KEY=enc:v1:...` 之前粘在同一行（缺 LF），launcher 把整行当注释跳过 → env var 没注入 → sidecar 拿到空 key → UI 显示"未配置模型"
- 修复 1：直接修正 deliver/.env（注释和 key 之间加换行）
- 修复 2（防御）：launcher `_parse_dotenv` 检测注释行内是否含 KEY=VALUE 模式 → 有 → 打印警告 + 提取 key=value 继续解析，不再静默跳过
- 用户视角："未配置模型" → 重启工具即可看到内置 qwen3.5-flash 已自动绑定
- 排查回顾：这个 bug 自 v0.3.9 加密 key 改造后某次手抖粘错行就一直存在，v0.3.10/v0.3.11/v0.3.12 全员受影响；首次启动成功 + 登录成功 + 缺模型 = 三段式症状的典型代表

**v0.3.11 → v0.3.12**：
- **必升级**：v0.3.10/v0.3.11 首次启动在 Python 3.14 + PyInstaller frozen 环境下崩溃（任务管理器看到进程立刻消失）。原因有两层：
  1. `sidecar/core/config.py` 的 `save()` 调 `ensure_config()`，`ensure_config()` 又调 `save()`，在 config.yaml 不存在时死循环 → `RecursionError`
  2. `paths.data_dir()` 用 `root.mkdir(...)` 触发 Python 3.14 frozen 模式下 `pathlib.WindowsPath` 懒加载 `_str / _drv` 失败的 bug
- 修复：拆出 `_write_yaml()` 帮助函数，`ensure_config` 和 `save` 都直接走它，不再互相依赖；`paths.data_dir()` 改用 `os.makedirs(str(root), exist_ok=True)`
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- **回归测试 5/5 + 4/4 全绿**（tests/test_config_first_run.py + tests/test_send_self_recipient.py）

**v0.3.10 → v0.3.11**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- send.py 修复：用 `contact user search` 按 userId 严格匹配拿自己的 `openDingTalkId`，用 `--open-dingtalk-id` 发文件（取代之前误用 `singleChat=true` 的会话 ID 作为 `--group`，结果 dws 把 ID 路由为 `send_personal_message` 发给那个 1v1 会话的另一方）
- userId 严格匹配（不 fallback 到 search 第一条），避免重名误发
- 仅 `sidecar/core/send.py` 一个文件改动

**v0.3.9 → v0.3.10**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- 千问模型列表从 7 个精简到 6 个（移除 qwen-flash 额度耗尽 + qwen3-coder/qwen3-vl/qwq-plus 不在套餐）
- 错误卡 UI 重做：去掉冗余 x 按钮 / 重检 dws / 详情-重试-诊断按钮，标题改红色 #EF4444，去硬红边框
- 登录流简化：dws 自动开浏览器授权（不再用前端 QR canvas）
- LLM 配置 diff 检测：切模型不动 key 时不再把脱敏占位串当成新 key 写
- MD 预览 ol/ul 序号恢复显示（之前全局 CSS 重置压掉了 list-style）

**v0.3.8 → v0.3.9**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- `.env` 内置 key 从明文 `sk-xxx` 改为密文 `enc:v1:...`（用户无感，工具自动解密）
- 用户自定义 key 不受影响（DPAPI 文件保留）
- 唯一可见变化：`cat .env` 看不到明文 key

**v0.3.7 → v0.3.8**：
- 用户数据目录结构**未变**，直接覆盖 exe 即可
- 内置 key 升级后**不变**（所有用户共享同一个默认 key）
- 用户自定义 key 不受影响（DPAPI 文件保留）
- 唯一可见变化：3 个 .exe 的图标从 PyInstaller 默认改为 `logo.ico`

回滚：v0.3.9 / v0.3.8 / v0.3.7 / v0.3.6 / v0.3.3 / v0.3.2 / v0.3.1 / v0.3.0 / v0.1.0-pre-redesign

---

## 11. 关键决策记录

### v0.3.15（commit ac87f4b，tag v0.3.15）

**群聊消息只过滤与你有关的消息**（之前群里所有消息都被 AI 纳入分析 → 日报里充斥大量与你无关的纯讨论，例：别人讨论午餐 / 闲聊 / 其他人的项目）：

- **痛点**：用户日报里有 30%-50% 内容是群里与你无关的纯讨论（别人闲聊、别人讨论的项目），读起来很费力；要扫一遍才找到自己的事项
- **方案对比**：
  - **方案 A**（采用）：在 AI prompt 里加 4 类筛选规则，让 AI 自己按规则筛
    - 优点：实现极简（prompt 30 行），AI 理解后直接过滤；上下文追溯 10 条解决"消息被剪掉后看不懂"的连贯性问题
    - 缺点：依赖 AI 遵循 prompt（不是 100% 可靠，但对 qwen 系列已 dev 验收通过）
  - **方案 B**（否决）：在 sidecar 端做消息过滤（Python 解析 bundle 删掉不相关消息后再给 AI）
    - 优点：确定性 100%，不依赖 AI
    - 缺点：实现复杂（要写中文名匹配 / @消息识别 / 10 条上下文追溯算法），维护成本高；方案 A 已经够用
  - **方案 C**（否决）：用户手动选择"只看我 @的消息"开关
    - 优点：用户控制
    - 缺点：体验差（用户每次生成都要选一次）；"我发过"和"@所有人"也该看
- **修复**（`external/dingtalk_daily_summary.py:390` 的 `build_prompt_template` 函数内）：
  - 在 `## 分析边界` 之前插入 4 段：`## 私聊消息筛选规则` / `## 群聊消息筛选规则` / `## 数据范围`
  - 私聊规则：私聊（bucket_hint=personal）分析全部消息
  - 群聊规则：4 类（我发过 / @我 / @所有人 / 上下文 10 条）
  - 数据范围：明确报告边界（"群里未 @我且我也未参与讨论的内容不纳入"）
- **验收**（dev 模式）：
  - 合成 1 群（8 条消息覆盖所有 4 类 + 2 条与我无关的纯讨论）+ 1 私聊（2 条）
  - AI 按规则筛无误伤，与我无关的"今天吃啥"类闲聊被自动排除
  - `@所有人 周五前交周报` 被正确纳入纪要
- **私聊影响**：零（私聊规则保持"分析全部消息"）
- **可调点**：如果觉得"上下文 10 条"太多/太少，可以调 `external/dingtalk_daily_summary.py:415` 的数字；如果觉得 AI 误伤了某些边缘消息，可以在 prompt 里加例外条款

### v0.3.14（commit 588f01c，tag v0.3.14）

**建立版本号单一真相源**（UI 右下角永远落后于实际版本，6+ 处分散维护）：

- **痛点**：发版时版本号要同步改 6+ 处（src/index.html 硬编码、launcher.py 写死 "v0.1.0"、sidecar dispatcher ping 返 "0.1.0"、sidecar send diagnose 返 "0.1.0"、README.md、git tag）。v0.3.10 → v0.3.13 的发版过程中，src/index.html 一直停在 v0.3.10 没更新、sidecar 一直返 "0.1.0" 没更新，用户报「右下角显示 v0.3.10 但我已经升级到 v0.3.13」，排了 10 分钟才意识到是源码遗漏。

- **根因**：版本号本质是个全局配置项，但被当作字面量散落在源码里。每发一次版就手动 grep + sed 6 处，漏一处就脱节。

- **方案对比**：
  - **方案 A（最简）**：继续 grep + sed 6 处 + 加个 lint 检查
    - 优点：零架构改动
    - 缺点：6+ 处仍分散，下次漏一处 lint 也兜不住（lint 只能 catch 字面量，不能 catch launcher 自己的版本渲染逻辑）
  - **方案 B（采用）**：建 `version.py` 作为单一真相源
    - 优点：所有运行时（launcher 渲染 / sidecar ping / sidecar diagnose）都 `from version import __version__` 读它，发版只改一行；UI footer 是 launcher 启动时 `evaluate_js` 注入（不依赖 HTML 硬编码）
    - 缺点：要让 PyInstaller 在 frozen 模式能 import `version`（加 hiddenimports + 把项目根加到 sys.path）
  - **方案 C（否决）**：build 时 sed 替换一个模板占位符
    - 优点：HTML 也能批量替换
    - 缺点：sed 替换是脆弱的（引号/转义/字符集），debug 噩梦

- **修复**：
  1. 新建 `version.py`（项目根），只一行 `__version__ = "0.3.14"`
  2. `launcher.py` 启动时 `from version import __version__` + `evaluate_js("document.getElementById('version-tag').textContent = 'v0.3.14 · build <mtime>'")`
  3. `src/index.html:364` 把硬编码 `<span class="version">v0.3.10</span>` 改成 `<span id="version-tag" class="version">v?</span>`
  4. `sidecar/core/dispatcher.py` 把 ping 响应里的 `"version": "0.1.0"` 改成 `"version": _VERSION`（`_VERSION = from version import __version__`）
  5. `sidecar/core/send.py` 把 `collect_diagnostics` 里的 `"version": "0.1.0"` 改成 `"version": _VERSION`
  6. `sidecar/main.py` 把 `_HERE.parent`（项目根）加到 `sys.path`（dev 模式防御 + 让 sidecar 子进程也能 import version）
  7. `build.spec` / `launcher.spec` 的 `hiddenimports` 加 `'version'`（PyInstaller frozen 模式打包）
  8. 端到端验证：frozen sidecar.exe `ping` 返 `{"ok":true,"version":"0.3.14"}`，`diagnose` 返 `app.version=0.3.14` ✓

- **测试**（10/10 通过）：`tests/test_version_single_source.py`
  1. version.py 存在 + `__version__` 已定义
  2. `__version__` 符合 semver-ish（`^\d+\.\d+\.\d+([\.\-a-zA-Z0-9]+)?$`）
  3. launcher.py / dispatcher.py / send.py 都用 `from version import __version__`
  4. sidecar/main.py 把项目根加进 sys.path 且 import version
  5. build.spec / launcher.spec hiddenimports 含 `'version'`
  6. src/ 下 HTML/JS/CSS 运行时代码无 hardcoded `v0.3.X` 字面量（HTML/JS 注释行放行）
  7. dev 模式 PYTHONPATH=ROOT 时 sidecar 能 import version
  8. dispatcher.ping 用 `_VERSION` 而非硬编码
  9. send.diagnose 用 `_VERSION` 而非硬编码
  10. src/index.html 含 `id="version-tag"` 占位

- **收益**：
  - 发版检查清单从 6 处手动核对 → 1 处 `version.py`
  - UI 右下角版本号永远 = 实际版本（启动时由 launcher 注入）
  - 第三方 RPC 调用方拿到的 `version` 字段也 = 实际版本（ping / diagnose）

### v0.3.13（commit e4fe47a，tag v0.3.13）

**修复「未配置模型」静默 bug**：deliver/.env 文件的 # 注释和 `DINGTALK_BOX_DEFAULT_QWEN_KEY=enc:v1:...` 之前粘在同一行（缺 LF），launcher `_parse_dotenv` 把整行当注释跳过 → env var 没注入 → sidecar 拿到空 key → UI 显示"未配置模型"
- **根因**：v0.3.9 加密 key 改造后某次手抖粘错行 → deliver/.env 一直是错的（v0.3.10/v0.3.11/v0.3.12 全员受影响）
- **症状三段式**：首次启动成功 + 登录成功 + 缺模型 = `.env` 解析静默失败的典型表现
- **修复 1**（立即）：直接修正 deliver/.env（注释和 key 之间加换行）
- **修复 2**（防御）：launcher `_parse_dotenv` 检测注释行内是否含 KEY=VALUE 模式 → 有 → 警告 + 提取 key=value 继续解析，不再静默跳过
- **测试**：新增 `tests/test_launcher_dotenv_defense.py`（6/6 通过）：正常解析、注释行套 KEY=VALUE 提取、纯注释跳过、空行/缺 = 跳过、已有 env 不覆盖、真实密文 buggy .env 解密成功（`sk-87b8f...133d`）

### v0.3.12（commit 6f4ae12，tag v0.3.12）

**修复首次启动崩溃**（首次启动 v0.3.10/v0.3.11 的用户任务管理器看到进程立刻消失）：
- **根因 1**：`sidecar/core/config.py` 的 `save()` ↔ `ensure_config()` 互相调用死循环
  - `ensure_config()` 调 `save(DEFAULT_CONFIG)` 写种子配置
  - `save()` 调 `ensure_config()` 拿 cfg_path
  - 首次启动 config.yaml 不存在 → 无限递归 → `RecursionError`
  - 来源：v0.3.10 commit `38f94ca` 重构（用 hardcode DEFAULT_CONFIG 替代 assets/default_config.yaml）
- **根因 2**：Python 3.14 + PyInstaller frozen 模式下 `pathlib.WindowsPath` 懒加载 `_str / _drv` 失败
  - `paths.data_dir()` 内部 `root.mkdir(...)` 触发 `AttributeError: 'WindowsPath' object has no attribute '_str'`
  - 反复 mkdir 失败 + config 死循环互相放大 → 进程崩溃
- **修复**：
  - 拆出 `_write_yaml()` 帮助函数，`ensure_config` 和 `save` 都直接调它，不再互相依赖
  - `paths.data_dir()` 改用 `os.makedirs(str(root), exist_ok=True)`，绕开 pathlib 内部属性
- **测试**：新增 `tests/test_config_first_run.py`（5/5 通过）：config.yaml 不存在时 load 正常返回 DEFAULT_CONFIG、20x 反复 load 无 RecursionError、AST 验证 save/ensure_config 互不调用、AST 验证 data_dir 用 os.makedirs、嵌套目录创建 OK

### v0.3.11（commit 36ef6e5，tag v0.3.11）

**修复 send.py fallback 误发到置顶单聊**（同事用工具给同事发图片/文档后消息发给了列表置顶的 1v1 单聊的另一方）：
- dws 1.0.34+ `contact user me` 不返 `openDingTalkId`，`--user` 不支持富媒体（image/file）
- v0.3.10 旧 fallback：`list-top-conversations` 第一个 `singleChat=true` 会话作为 `--group`，但 dws 实际把 `singleChat=true` 路由为 `send_personal_message` 发给那个 1v1 的另一方（不是自己）
- 修复：`contact user search --keyword <自己的名字>` 按 **userId 严格匹配** 拿自己的 `openDingTalkId`，用 `--open-dingtalk-id` 发文件
- **关键**：userId 不匹配时**不允许 fallback 到第一条 search 结果**（避免重名误发），匹配不上直接 raise
- 仅 `sidecar/core/send.py` 一个文件改动

### v0.3.10（commit 02bf8b9，tag v0.3.10）

按 phase 分 4 个 commit：

- **Phase A 登录流简化**（5b91145）：钉钉 App 扫屏幕 QR 识别不到 → 改 dws 自动开浏览器授权 + 用户在浏览器扫码
  - 新增 `cancel_login` RPC 修 dedup bug（取消登录后下次点登录浏览器没自动打开）
  - 动效用 mini 机器人 + 文字点循环（用户否决了呼吸 / 旋转方案）
- **Phase B 错误卡 UI 重做**（344ba7e）：去掉 x 按钮 / 重检 dws / 详情-重试-诊断 / 红边框，只保留标题（红色 #EF4444 + ⚠️ SVG）
- **Phase C 千问模型精简**（344ba7e）：账号 qwen-flash 额度耗尽 + qwen3-plus 未开通 → 6 个实测可用的模型，3.6 抱团在 3.7 上、long 垫底
- **Phase D LLM 配置 UX bug**（344ba7e）：用户切模型 + 不动 key + 保存 → 把 `cfg.key_masked`（脱敏占位串 `sk-8...133d`）当成新 key 存进 DPAPI → qwen 401
  - 修复：前端 `llmState.originalKey` 弹窗打开瞬间记下 + diff 检测 → `skip_key` flag → 后端跳过 protect_to_file
  - 配套：`resolve_api_key` 加空检查（DPAPI 文件在但内容空时 fall through 到 .env 内置 key）
- **Phase E MD 预览 ol/ul 序号恢复**（13ecb24）：全局 `ul,ol { list-style:none }` 压掉了 markdown-it 渲染的 ol/ul marker → `.markdown-body ol/ul` 加 `list-style: revert` 恢复

### v0.3.9（commit d223412，tag v0.3.9）
- **.env 内置 key 加密**（等级 2：Argon2id + AES-256-GCM）
  - 算法：Argon2id (time=3, memory=64MB, parallel=4) + AES-256-GCM
  - 主密钥：拆 3 段 + reverse 顺序拼接（在 `key_crypto.py`）
  - 启动链路：launcher 读 .env → 命中白名单 + `enc:v1:` 前缀 → `key_crypto.decrypt_builtin_key()` → 注入 `os.environ`
  - sidecar / ai_bridge 看到的是明文（通过 `os.environ` 继承），无需改它们
  - 验证：`strings dingtalk_box.exe` 找不到明文 key；`cat .env` 看到密文
  - **已知局限**：PyInstaller 反编译能看到主密钥（等级 2）；等级 3（Nuitka AOT 编译）计划 v0.3.11+ 评估
  - **用户体验 0 变化**：启动开销 < 100ms

### v0.3.8（commit b433ab5，tag v0.3.8）
- **frozen launcher 修复**：APPDATA 优先 + 3 级 fallback + 首次启动种子复制
  - v0.3.7 commit 漏测 frozen launcher 端到端，导致发版后用户启动找不到 .env、Qwen 7 模型无法自动绑定
  - 修复：launcher.py `_load_dotenv()` 改 3 级 fallback（APPDATA > ROOT > _MEIPASS）
  - 首次启动：把 ROOT/.env 复制到 APPDATA，**之后用户改 ROOT/.env 不影响已部署用户**
- **替换 exe 图标**：3 个 .spec 加 `icon='assets/logo.ico'`，替换 PyInstaller 默认蓝黄图标

---

*构建：2026-06-22 · HEAD: ac87f4b · tag: v0.3.15*
