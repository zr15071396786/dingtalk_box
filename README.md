# 钉钉AI助手（dingtalk_box）v0.3.15

> 公司内部、绿色版单文件 .exe、面向全公司员工的钉钉生产力工具集
> 当前主功能：日报生成（拉取当天钉钉消息 → AI 分析 → 渲染长图 → 发送给目标）

## 0. 版本

| 版本 | 日期 | 主要变化 |
|------|------|----------|
| v0.3.15 | 2026-06-22 | 群聊消息只过滤与我有关的消息（4 类规则 + 上下文追溯 10 条 + 数据范围段） |
| v0.3.14 | 2026-06-22 | 版本号单一真相源（version.py + UI 右下角 evaluate_js 注入 + sidecar ping/diagnose） |
| v0.3.13 | 2026-06-22 | 修复「未配置模型」+ launcher _parse_dotenv 防御性解析（注释行里正则找 KEY=VALUE） |
| v0.3.12 | 2026-06-22 | 修复首次启动死循环崩溃（config save↔ensure_config 循环 + pathlib 3.14 frozen _str bug） |
| v0.3.11 | 2026-06-22 | fix(send) 修复误发到置顶单聊（userId 严格匹配 search，禁 fallback） |
| v0.3.10 | 2026-06-22 | 登录流简化（dws 自动开浏览器，去前端 QR）+ 4 项交互修复 + mini 机器人动效 + 错误卡 UI 重做 + 千问模型列表精简 + LLM 配置 diff 检测 skip_key + MD 预览 ol/ul 序号恢复 |
| v0.3.9  | 2026-06-21 | .env 内置 Qwen key 加密（Argon2id + AES-256-GCM） |
| v0.3.8  | 2026-06-20 | frozen launcher .env 修复（APPDATA 优先 + 3 级 fallback）+ 替换 logo.ico |
| v0.3.7  | 2026-06-19 | MD 内嵌 markdown-it + Qwen 7 模型内置默认 key + Glass 视觉系统 |
| v0.3.0  | 2026-06-12 | 横屏 sidebar + main grid 重构 + 主题切换器彻底删除（单主题） |

完整发版记录见 `docs/superpowers/specs/` 和 `.claude/projects/dingtalk_box/memory/dingtalk-v*.md`。

---

## 1. AI 模型配置

工具支持 **10 家云端大模型厂商**，用户可在 GUI 内选择并绑定 API Key：

- OpenAI / Anthropic Claude (自部署 OpenAI 兼容网关) / DeepSeek / Moonshot Kimi/智谱 GLM / 阿里百炼 DashScope / 字节豆包 / 讯飞星火 / 百度千帆

**配置步骤**：
1. 点用户卡片右侧的 ⚙ 按钮
2. 选择厂商（自动填充 base_url + 默认模型）
3. 填入 API Key
5. 点 [💾 保存]

**安全说明**：
- 用户 API Key 用 **Windows DPAPI** 加密后存到 `%APPDATA%/DingTalkBox/llm_secret.bin`
- 同台机器同用户可解密；跨机器/跨用户变废
- 切换厂商会覆盖旧 Key（不可恢复）
- 清除配置后 Key 立即从磁盘删除
- **v0.3.9+**：内置默认 Qwen key 在 `.env` 中以密文存储（Argon2id + AES-256-GCM），launcher 启动时解密注入 `os.environ`，反编译验证 0 泄露

---

## 2. 架构

```
┌─────────────────────────────────────────────────────────┐
│  dingtalk_box.exe  (launcher.py — PyInstaller onefile)  │
│  ┌───────────────────────────────────────────────────┐  │
│  │ WebView (HTML/CSS/JS — src/)                      │  │
│  │   window.dtbox.call(method, params)               │  │
│  │   // 前端 ↔ launcher: pywebview bridge            │  │
│  └────────────────────┬──────────────────────────────┘  │
│                       │ JSON-RPC over stdio              │
│  ┌────────────────────▼──────────────────────────────┐  │
│  │ sidecar.exe  (sidecar/main.py — stdio JSON-RPC)   │  │
│  │  - core/dispatcher.py (20+ methods)               │  │
│  │  - core/auth.py         (登录/取消/corp 校验)     │  │
│  │  - core/daily_report.py (拉取+AI+渲染)            │  │
│  │  - core/send.py         (drive 上传+send)         │  │
│  │  - core/llm_config.py   (10 厂商配置管理)          │  │
│  │  - core/install.py      (dws 安装引导)            │  │
│  └────────────────────┬──────────────────────────────┘  │
│                       │ subprocess                       │
│  ┌────────────────────▼──────────────────────────────┐  │
│  │ ai_bridge.exe  (ai_bridge/main.py — LLM 子进程)   │  │
│  │  - 独立进程隔离 LLM 调用，方便加密 .exe 重打包     │  │
│  └────────────────────┬──────────────────────────────┘  │
│                       │ subprocess                       │
│  ┌────────────────────▼──────────────────────────────┐  │
│  │ dws.exe  (公司内部 CLI — OAuth + 消息/文件 API)   │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**关键设计**：
- 3 进程完全独立打包（PyInstaller onefile × 3），互不影响升级
- launcher（GUI）只做窗口管理 + 调起 sidecar；业务逻辑全在 sidecar
- 登录走 **dws 自动开系统浏览器**
- 主题：v0.3.0 起**单主题**（无主题切换器），所有颜色直接 hex 不用 CSS vars

数据目录：`%APPDATA%/DingTalkBox/`
日志：`%APPDATA%/DingTalkBox/logs/`
输出：`%APPDATA%/DingTalkBox/output/YYYY-MM-DD/`

---

## 3. 启动

### 3.1 开发模式（推荐）

```bash
# 装依赖
pip install -r requirements.txt

# 启动 GUI（含 src/ 改动自动 reload）
python dev.py
```

`dev.py` 会：
- 启动 sidecar 子进程
- 创建 webview 窗口加载 src/index.html
- 后台 watchdog 监控 src/ 改动 → 调 window.evaluate_js("location.reload()")
- Ctrl+C 干净退出（关 webview + sidecar）

### 3.2 不开 GUI 启动（只跑 launcher 流程）

```bash
python launcher.py
```

### 3.3 打包（发版用）

```bash
# 1. 打包 sidecar
pyinstaller build.spec --clean --noconfirm
cp dist/sidecar.exe deliver/dingtalk_box/sidecar.exe

# 2. 打包 launcher（带 GUI）
pyinstaller launcher.spec --clean --noconfirm
cp dist/dingtalk_box.exe deliver/dingtalk_box/dingtalk_box.exe

# 3. 打包 ai_bridge
pyinstaller ai_bridge.spec --clean --noconfirm
cp dist/ai_bridge.exe deliver/dingtalk_box/ai_bridge.exe

# 4. smoke test
echo '{"id":"1","method":"ping","params":{}}' | ./deliver/dingtalk_box/sidecar.exe
# 期望: {"id":"1","result":{"ok":true,"version":"0.1.0"}}
```

详见 `.claude/projects/dingtalk_box/memory/dingtalk-pyinstaller-build.md`。

---

## 4. 用户数据目录

```
%APPDATA%/DingTalkBox/
├── config.yaml                              # 首次启动由 sidecar 写入（v0.3.9+ DEFAULT_CONFIG hardcode）
├── llm_secret.bin                           # 用户 API Key（DPAPI 加密）
├── dws-credentials/                         # dws 登录态（dws 自己管）
├── output/
│   └── YYYY-MM-DD/
│       ├── dingtalk_bundle.json              # 拉取的原始数据
│       ├── report.json                       # 结构化报告
│       ├── 钉钉工作纪要日报-YYYY-MM-DD.png   # 长图
│       └── 钉钉工作纪要日报-YYYY-MM-DD.md    # Markdown
├── logs/
│   ├── sidecar-YYYY-MM-DD.log
│   ├── dws-YYYY-MM-DD.log
│   └── daily_report-YYYY-MM-DD.log
├── cache/
└── update/                                  # 升级包暂存
```

---

## 5. 调试技巧

- **看 sidecar 日志**：`tail -f "$APPDATA/DingTalkBox/logs/sidecar-$(date +%F).log"`

- **看 dws 调用日志**：`tail -f "$APPDATA/DingTalkBox/logs/dws-$(date +%F).log"`

- **手动调 sidecar**：
  
  ```bash
  echo '{"id":"1","method":"ping","params":{}}' | python sidecar/main.py
  ```

---

## 6. 设计文档

| 文档 | 路径 |
|------|------|
| 架构 + 通信协议 | `docs/superpowers/specs/2026-06-11-llm-integration-design.md` |
| UI 重写设计 | `docs/superpowers/specs/2026-06-19-major-ui-rewrite-design.md` |
| dws CLI 接入 | `docs/superpowers/specs/2026-06-18-dws-cli-access-approval-design.md` |
| 主题架构 | `.claude/projects/dingtalk_box/memory/dingtalk-theme-architecture.md` |
| 设计选型 | `.claude/projects/dingtalk_box/memory/dingtalk-design-system-choices.md` |
| PyInstaller 打包 | `.claude/projects/dingtalk_box/memory/dingtalk-pyinstaller-build.md` |

---

## 7. 已知限制

- macOS/Linux：未实现（PyInstaller frozen 仅 Windows 验证过）
- 自动更新：未实现
- 签名：不签名
- 等级 2 加密（v0.3.9）：`.env` 内置 key 可被会 pycdc 的人解，等级 3 Nuitka 计划后续

---

## 8. 测试

- dev 模式启动 + 5 项登录交互验证（v0.3.10）
- 改 src/ 看 watchdog 自动 reload 是否正常
- frozen 端到端：3 个 .exe 启动 → login → list_history → generate_daily
