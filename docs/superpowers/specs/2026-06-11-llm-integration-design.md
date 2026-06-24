# 大模型集成：可配置厂商 + 加密秘钥（设计稿）

- **日期**：2026-06-11
- **状态**：设计中（待 review）
- **范围**：单次一个 spec，覆盖 ai-bridge 进程 + sidecar 改造 + 前端 modal + 打包

---

## 1. 目标 & 非目标

### 1.1 目标
让用户能在 GUI 内：
1. 从 **10 家云端大模型厂商** 中**选一家**
2. **绑定自己的 API key**
3. 之后「生 成 今 日 日 报」**一键完成**（拉数据 → AI 总结 → 渲染 PNG/MD）
4. 随时**切换厂商**（旧的 key 被覆盖）
5. 切换或失效时**不破坏其他功能**（兜底两步流程）

### 1.2 非目标（YAGNI）
- 多 key 并存 / 同时调多家
- 流式 token 进度
- token 用量统计 / 计费
- 自定义 provider 添加
- macOS / Linux 平台（仅 Windows 11+）
- 提示词模板可编辑
- AI 报告二次编辑 UI

---

## 2. 架构总览

3 个进程：

```
launcher (dingtalk_box.exe, frozen, pywebview+WebView2)
    │  stdio JSON-RPC
    ▼
sidecar (sidecar.exe, frozen, 业务核心)
    │  Popen (一次性) + stdio JSON 1 次往返
    ▼
ai_bridge (ai_bridge.exe, frozen, 新增, 唯一职责：调 LLM)
    │  HTTPS
    ▼
10 家 LLM API（OpenAI Chat Completions 协议）
```

**关键不变量**：
- 密钥从 webview 端 → sidecar（明文，HTTPS-style 隔离）→ DPAPI 加密落盘 → **只由 ai-bridge 读取并解密**（进程退出 key 销毁）
- 任何时刻**只有一个 active provider** + 对应一个 active key
- 没配置时**完全回退**到旧的两步流程（`awaiting_ai` + 前端弹提示「去配置」）

---

## 3. 状态模型（3 个文件）

### 3.1 `%APPDATA%/DingTalkBox/config.yaml`（明文，用户可读）
```yaml
version: 1
corp: { ... }        # 已有
ui: { ... }          # 已有
daily_report: { ... }  # 已有
update: { ... }      # 已有
logging: { ... }     # 已有

# ↓ 新增段 ↓
llm:
  active_provider: null   # 当前激活的 provider id；null = 未配置（首次安装默认值）
  default_model: null     # 该 provider 下的默认模型
  base_url: null          # 可在 UI 覆盖
```

### 3.2 `%APPDATA%/DingTalkBox/llm_secret.bin`（DPAPI 加密二进制）
- 内容：`CryptProtectData(plaintext=api_key, ...)` 输出
- 只有 ai-bridge 调用 `CryptUnprotectData` 解密
- 启动时 sidecar 做轻量解密测试，失败则删除并提示用户重配

### 3.3 `assets/providers.yaml`（bundled，只读，10 个 preset）
```yaml
providers:
  - id: openai
    name: "OpenAI"
    base_url: "https://api.openai.com/v1"
    default_model: "gpt-4o-mini"
    doc_url: "https://platform.openai.com/api-keys"
  - id: anthropic_claude_compat   # 经自部署 OpenAI 兼容网关（如 OpenRouter 转发）
    name: "Anthropic Claude (自部署 OpenAI 兼容网关)"
    base_url: ""                    # 用户必填：自部署网关地址
    default_model: ""               # 用户必填：claude-3-5-sonnet 等
    doc_url: "https://docs.anthropic.com/en/api/openai-sdk"
    note: "此选项需要用户自备 OpenAI 兼容网关；如无请选 DeepSeek/Moonshot 等云端厂商"
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

> 实际 preset 列表：10 家云端厂商 preset。
> 用户自部署 OpenAI 兼容网关（如 OneAPI / OpenRouter）也可用 `anthropic_claude_compat` 这个"空壳"preset，
> 它要求用户必填 base_url 和 default_model；其他 9 家都用预设值可一键开箱。
> 字段允许用户用任意 base_url 覆盖预设（自部署网关友好）。

---

## 4. 数据流

### 4.1 点「生成日报」+ 已配置 LLM

```
[1] webview: click #btn-generate
[2] window.dtbox.call("generate_daily", {date})
[3] launcher bridge → sidecar (stdio JSON-RPC)
[4] sidecar.daily_report.generate({date})
    ├─ 阶段 1: dds.export_daily_bundle(date, out_dir)
    │     → 写 output/YYYY-MM-DD/{bundle.json, prompt.md, report_template.json}
    │     → emit progress "fetching_chats" 0% → 40%
    ├─ 读 config.yaml: llm.active_provider / llm_secret.bin 存在性
    ├─ emit progress "ai_summarize" 45%  "调用 {provider}..."
    ├─ Popen([ai_bridge.exe,
    │         --bundle-path, bundle_path,
    │         --prompt-path, prompt_path,
    │         --output-dir,   out_dir,
    │         --provider-id,  active_provider],
    │        timeout=130, stdout=PIPE, stderr=PIPE)
    │     │
    │     ▼ ai_bridge.exe 进程
    │     ai_bridge.main():
    │       1. 读 config.yaml → base_url, default_model
    │       2. 读 llm_secret.bin → DPAPI CryptUnprotectData → api_key
    │       3. 读 prompt.md（text） + bundle.json（text）
    │       4. 计算 bundle 长度；> 30k 字符则按 chat 切段，N 个 user message
    │       5. POST {base_url}/chat/completions
    │          headers: Authorization: Bearer {key}
    │          body: {model, messages, response_format:{type:"json_object"},
    │                 max_tokens:4096, temperature:0.3, stream:false}
    │          timeout=120s
    │       6. 解析 choices[0].message.content
    │          - 剥离 ```json ... ``` 包裹
    │          - json.loads；失败则尝试宽松解析
    │       7. validate_report(parsed) → 8 个顶层键 + 嵌套结构
    │       8. 写 output/YYYY-MM-DD/report.json
    │       9. stdout: {"ok":true,"json_path":"...","duration_ms":...} 退出 0
    │     │ 失败 → stdout: {"ok":false,"err_code":...,"err_msg":...} 退出 1
    │     │ 崩溃 → stderr 有内容，exit ≠ 0/1
    │
    ├─ 解析 ai_bridge stdout
    │     - ok=true → 读 report.json
    │     - ok=false → return JSON-RPC error 给前端
    │     - exit 异常 → 包装 stderr → -32099
    │
    ├─ emit progress "ai_summarize" 65%  "AI 分析完成"
    ├─ 阶段 3: dds.render_report(report, out_dir)
    │     → emit "render_png" 70% → "render_md" 95% → "done" 100%
    └─ return {stage:"done", output:{png_path, md_path, json_path}, stats:...}
```

### 4.2 点「生成日报」+ 未配置 LLM（回退）

```
[1-4] 同上到 emit progress "fetching_chats" 40%
[5] 读 config.yaml: active_provider is null
[6] return {stage:"awaiting_ai", bundle_path, prompt_path, report_template_path, ...}
[7] webview 收到 awaiting_ai
     - 弹 modal："AI 未配置，点击 [去配置] 打开设置"
     - 状态条常驻 "⚠ AI 未配置"
[8] 用户点 [去配置] → 打开 ⚙ modal（见 §6）
```

### 4.3 配置 / 测试 / 清除（独立流程，不走 generate_daily）

```
set_llm_config(params)     # 前端 → sidecar
  params: {provider, base_url, default_model, api_key, clear?: false}
  流程：
    1. clear=true → 删除 llm_secret.bin, config.llm.active_provider=null, 落盘
    2. clear=false → 校验 provider 在 preset 中, base_url 非空
    3. DPAPI CryptProtectData(api_key) → 写 llm_secret.bin
    4. config.llm = {active_provider, default_model, base_url} → 落盘 config.yaml
    5. return {ok:true, provider, base_url, default_model}

get_llm_config(params={})  # 前端 → sidecar
  流程：
    1. 读 config.yaml → llm 段
    2. 检查 llm_secret.bin 存在 + 轻量解密测试
       - 成功 → {configured: true, provider, base_url, default_model, requires_key}
       - 失败 → 删除该文件 + config 重置 → {configured: false, ...}
    3. return 结果

test_llm_connection(params)  # 前端 → sidecar（可独立调，也可从 modal 内点 [🔌 测试连接]）
  params: {provider?, base_url?, default_model?, api_key?}
           （不传则用已保存的；传则用临时值测试，不落盘）
  流程：
    1. 若无 api_key 参数 → 报 "需要先填 key"
    2. Popen(ai_bridge.exe --mode test --provider-id ... --api-key ...)
    3. ai_bridge 用传入的临时参数发最小请求
    4. 返回 {ok, latency_ms, model, err_code?, err_msg?}
```

### 4.4 bundle 分段策略

```
total_chars = len(bundle.json)
if total_chars <= 30_000:
    messages = [system(prompt), user("请分析以下聊天记录并输出 report.json：\n\n" + bundle)]
else:
    chat_blocks = split_bundle_by_chat(bundle)   # 按 conversation 切
    messages = [system(prompt)]
    for i, block in enumerate(chat_blocks):
        messages.append(user(f"## 第 {i+1}/{len(chat_blocks)} 段聊天记录\n\n" + block))
    messages.append(user("请综合以上所有段，输出完整 report.json。"))
```

---

## 5. 进程边界与通信

| 进程 | 启动方式 | 通信 | 生命周期 |
|---|---|---|---|
| launcher | 用户双击 | 渲染 webview | 长驻 |
| sidecar | launcher 启动子进程 | stdio JSON-RPC | 长驻（随 launcher 退出） |
| ai_bridge | sidecar 临时 Popen | stdin/stdout JSON，**1 次往返** | 一次性，调用完即退 |

**ai_bridge stdout 协议（最简）**：
```jsonc
// 成功
{"ok": true, "json_path": "C:\\...\\report.json", "duration_ms": 8421}

// 失败
{"ok": false, "err_code": "TIMEOUT", "err_msg": "120s timeout"}
{"ok": false, "err_code": "HTTP_401", "err_msg": "Unauthorized"}
{"ok": false, "err_code": "PARSE_FAIL", "err_msg": "...", "raw": "AI 返回前 500 字符"}
{"ok": false, "err_code": "SCHEMA_FAIL", "err_msg": "missing work.handled_items", "missing": ["work.handled_items"]}
{"ok": false, "err_code": "DPAPI_FAIL", "err_msg": "..."}
```

**err_code 完整集合**（sidecar 包装成 JSON-RPC code）：
- `TIMEOUT` → -32010
- `HTTP_401` / `HTTP_403` → -32011
- `HTTP_429` → -32012
- `HTTP_5XX` → -32013
- `PARSE_FAIL` → -32014
- `SCHEMA_FAIL` → -32015
- `DPAPI_FAIL` → -32009
- `OLLAMA_DOWN` → -32016 （保留，10 个云端 preset 都不触发，但 schema 上仍可表达）
- `CRASH` → -32099 （进程崩，stderr 内容在 err_msg）

---

## 6. UI：⚙ AI 模型配置 modal

### 6.1 入口

用户卡片右侧（dws 状态行末尾加齿轮图标 ⚙ 按钮）：

```html
<button id="btn-llm-settings" title="AI 模型配置">⚙</button>
```

### 6.2 Modal 结构

```html
<div id="llm-modal" class="modal hidden">
  <div class="modal-box">
    <div class="modal-title">
      🤖 AI 模型配置
      <button class="close" id="btn-close-llm">✕</button>
    </div>
    
    <div class="form-row">
      <label for="llm-provider">厂商</label>
      <select id="llm-provider"><!-- 10 个 option 动态填充 --></select>
    </div>
    
    <div class="form-row">
      <label for="llm-base-url">Base URL</label>
      <input type="text" id="llm-base-url" />
    </div>
    
    <div class="form-row">
      <label for="llm-model">默认模型</label>
      <input type="text" id="llm-model" />
    </div>
    
    <div class="form-row">
      <label for="llm-key">API Key</label>
      <div class="key-row">
        <input type="password" id="llm-key" autocomplete="off" />
        <button id="btn-toggle-key" type="button">👁 显示</button>
      </div>
    </div>
    
    <div class="form-row hint">
      <span id="llm-doc-link"></span>  <!-- 显示厂商文档链接 -->
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

### 6.3 交互规则

| 动作 | 行为 |
|---|---|
| 打开 modal | `get_llm_config` 拉当前值；key 字段**始终为空**（明文 key 永不出现在已有状态显示中），仅显示"已配置/未配置" |
| 切厂商下拉 | 自动填 base_url 和 model，**清空 key 输入框**（强制重绑），更新 doc 链接 |
| 👁 显示 | 切换 key 输入框 password/text |
| 🔌 测试连接 | 调 `test_llm_connection`，传当前 modal 表单值；不落盘 |
| 💾 保存 | 调 `set_llm_config`；成功后 toast "✓ 已保存"，状态条更新 |
| 🗑 清除 | 二次确认 → 调 `set_llm_config({clear:true})`；成功后状态条变 "⚠ AI 未配置" |
| 关闭 modal | 不自动保存（任何未保存修改会弹"放弃修改？"） |

### 6.4 状态条

启动时 `boot()` 调 `get_llm_config`：
- `configured: true` → 状态条 `就绪 · AI 已配置 (DeepSeek)` 绿色
- `configured: false` → 状态条 `⚠ AI 未配置（点 ⚙ 配置后可一键生成）` 黄色

---

## 7. 错误处理

### 7.1 ai_bridge → sidecar 错误矩阵

| 场景 | ai_bridge.err_code | sidecar JSON-RPC code | 前端展示 |
|---|---|---|---|
| llm_secret.bin 不存在 | — | 不调 ai_bridge | "AI 未配置" + 状态条 |
| DPAPI 解密失败 | DPAPI_FAIL | -32009 | "密钥文件损坏，请重新配置" |
| httpx TimeoutException | TIMEOUT | -32010 | "AI 调用超时（120s）" + 详情 |
| HTTP 401 / 403 | HTTP_401 | -32011 | "API key 无效或已过期" |
| HTTP 429 | HTTP_429 | -32012 | "AI 限流，请稍后重试" + Retry-After 头 |
| HTTP 5xx | HTTP_5XX | -32013 | "AI 服务异常（{status}）" |
| AI 返回非 JSON | PARSE_FAIL | -32014 | "AI 输出格式异常" + 详情（前 500 字） |
| 字段缺失 / 类型错 | SCHEMA_FAIL | -32015 | "AI 输出缺字段：{field}" + 列表 |
| （保留位）Ollama 错码 | OLLAMA_DOWN | -32016 | 10 家云端均不会触发，schema 预留 |
| ai_bridge 进程崩（exit≠0） | CRASH | -32099 | "AI 子进程异常" + stderr 摘要 |

### 7.2 日志脱敏

`sidecar/core/logging_setup.py` 加 `redact_key` filter：
- 匹配 `Authorization: Bearer <key>` → `Authorization: Bearer {key[:4]}***`
- 匹配 `"api_key": "<key>"` → `"api_key": "{key[:4]}***"`
- `diagnose` 导出的 config 永远**不读** `llm_secret.bin`

### 7.3 前端错误卡片

复用现有 `#error-card` 结构 + `code: -32xxx` 体系：
- `error-msg`: 用户友好提示
- `error-detail`: JSON（err_code, err_msg, provider, duration_ms, raw[:500]）
- `btn-retry`: 重新点生成
- `btn-diagnose`: 跳到诊断 modal（仍不带 key）

---

## 8. 文件改动清单

### 8.1 新增

```
ai_bridge/
├── __init__.py                  # 空
├── main.py                      # CLI 入口（~120 行）
├── dpapi.py                     # CryptProtect/Unprotect ctypes 封装（~50 行）
├── providers.py                 # 读 providers.yaml，10 个 preset 查找（~80 行）
└── schema.py                    # report.json 字段校验（~50 行）

ai_bridge.spec                  # PyInstaller spec（~30 行，console=True）

sidecar/core/
└── llm_config.py                # set/get/clear/test + DPAPI 包装（~150 行）

assets/
└── providers.yaml               # 10 个厂商 preset 列表（只读 bundled）

tests/
└── test_ai_bridge.py            # 单测（~250 行）
```

### 8.2 修改

```
sidecar/core/daily_report.py     # 阶段 2：自动调 ai_bridge（保留 awaiting_ai 兜底）
sidecar/core/dispatcher.py       # +4 methods
sidecar/core/logging_setup.py    # +redact_key filter
sidecar/main.py                  # 请求 logger 加新 method

src/index.html                   # +⚙ 按钮 + llm-modal
src/style.css                    # +modal 细节
src/main.js                      # +配置 modal 逻辑 + boot() 调 get_llm_config

requirements.txt                 # +httpx>=0.27

build.spec                       # hiddenimports 不变（ai_bridge 独立 spec）
launcher.spec                    # 不变
```

### 8.3 不变

```
deliver/重启到最新版.cmd         # 继续指向 deliver/dingtalk_box/dingtalk_box.exe
assets/default_config.yaml       # 加 llm: 段（active_provider: null）
external/dingtalk_daily_summary.py  # 不动（render 逻辑不变）
```

---

## 9. 测试方案

### 9.1 单测 `tests/test_ai_bridge.py`（mock httpx，~250 行）

| 用例 | 验证点 |
|---|---|
| `test_dpapi_roundtrip` | encrypt → decrypt 后明文一致 |
| `test_dpapi_missing_file` | 文件不存在不崩，返回明确错误 |
| `test_dpapi_corrupt_file` | 随机字节 → decrypt 失败，错误码 DPAPI_FAIL |
| `test_providers_load` | 10 个 preset 都能加载，字段完整 |
| `test_providers_unknown_id` | 未知 provider → ValueError |
| `test_schema_valid` | 完整 report.json → 通过 |
| `test_schema_missing_top_key` | 缺 `work` → 列出哪些缺 |
| `test_schema_missing_nested` | 缺 `work.handled_items` → 列具体路径 |
| `test_schema_wrong_type` | `overview.work` 不是 list → 报字段+类型 |
| `test_openai_request_built` | POST body 字段对、auth header 对、总是带 `response_format` |
| `test_ai_bridge_success` | mock 200 + 合法 JSON → 写盘成功 |
| `test_ai_bridge_timeout` | mock httpx.TimeoutException → err_code=TIMEOUT |
| `test_ai_bridge_401` | mock 401 → err_code=HTTP_401 |
| `test_ai_bridge_429` | mock 429 + Retry-After → err_code=HTTP_429 |
| `test_ai_bridge_5xx` | mock 502 → err_code=HTTP_5XX |
| `test_ai_bridge_parse_fail` | mock 200 + "```json\n{...}\n```" → 剥离后 parse 成功 |
| `test_ai_bridge_parse_dirty` | mock 200 + 带多余逗号 → 宽松解析 |
| `test_ai_bridge_schema_fail` | mock 200 + JSON 缺 work → err_code=SCHEMA_FAIL |
| `test_ai_bridge_crash` | mock exit 1 + stderr "Traceback..." → err_code=CRASH + 摘要 |
| `test_bundle_split_small` | 10k 字符 → 单 user message |
| `test_bundle_split_large` | 50k 字符 → N 段 user message，最后一段"综合输出" |

### 9.2 集成测试（手动验收）

| # | 场景 | 步骤 | 期望 |
|---|---|---|---|
| 1 | 全新安装 | 启动 GUI | 状态条"⚠ AI 未配置" |
| 2 | 配 DeepSeek | ⚙ → 选 DeepSeek → 填真 key → 🔌 → ✓ → 💾 | 状态条"AI 已配置 (DeepSeek)" |
| 3 | 一键生成 | 点「生成日报」 | 看到真 AI 输出（不是占位） |
| 4 | 切厂商 | ⚙ → 选 OpenAI → key 字段清空 → 填新 key → 💾 | 状态条变 OpenAI，key 字段在 reload 后仍空（防泄露） |
| 5 | 切回 DeepSeek | 切回 → 必须再输 key | 验证旧 key 已删 |
| 6 | 错 key | 填 "sk-fake" | 🔌 → ✗ 401；保存后点生成 → 错误卡片 |
| 7 | 无网络 | 拔网线 / 关 wifi | 🔌 → ✗ TIMEOUT；点生成 → 错误卡片显示超时 |
| 8 | 杀进程 | 极端：taskkill ai_bridge.exe mid-call | 错误卡片显示 "AI 子进程异常" + stderr |
| 9 | 诊断导出 | 🔍 诊断 | JSON 里**不应包含** api_key 任何字节 |
| 10 | 切换 + 失效 | DPAPI 解密失败（模拟改 llm_secret.bin 为随机字节后启动） | 状态条 "AI 配置已失效，请重新配置"；旧文件被自动删 |
| 11 | 回退流程 | 主动点 [🗑 清除] → 点生成 | 走 awaiting_ai 流程，前端弹"去配置" |

### 9.3 回归

- 现有 `buildStubReport` 占位流程在没配 LLM 时仍工作
- 自检脚本 `launcher.py._self_test` 不变（不调 LLM）
- `dist/sidecar.exe` 和 `dist/dingtalk_box.exe` 重新打包后，原有功能（dws 拉消息 / 登录 / 发送 / 历史）不受影响

---

## 10. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| 1 | bundle 太大，token 超限 | 内置分段策略（>30k 字符按 chat 切块，N 段 user + 末尾"综合输出"） |
| 2 | AI 幻觉 | prompt 硬约束"严禁编造" + 给真实 chat_count 当锚点 + 渲染时检测"待 AI 填充"回退 |
| 3 | PyInstaller 漏 httpx 依赖 | ai_bridge.spec 显式列 `httpx`, `httpx._transports`, `httpcore`, `h2` |
| 4 | DPAPI 在 frozen 模式 | ctypes 调 crypt32；先实测 Win11/Win10；失败时回退到 keystore 提示重输 |
| 5 | DPAPI 不能跨用户/跨机器 | 诊断导出时检测首次启动标记；解密失败自动删 + 提示重配 |
| 6 | 国产 LLM 不严守 response_format | prompt 要求"直接 JSON 无 markdown" + 正则剥离 + 宽松 JSON 解析 |
| 7 | provider 模型名变更 | providers.yaml 集中管理；UI 可覆盖 default_model |
| 8 | bundle 全是系统消息 | ai-bridge 检测 `chat_count==0` → 跳过 LLM，写兜底 report |
| 9 | LLM 调用阻塞 sidecar | ai-bridge timeout=120s；sidecar Popen timeout=130s 兜底 |
| 10 | ai_bridge.exe 找不到 | 启动时 sidecar 显式检查，缺失 → 状态条"工具不完整" |

---

## 11. 迁移路径

### 11.1 新装用户
1. 安装新包 → 首次启动 sidecar
2. config.yaml 不存在 → 复制 bundled default（含 `llm:` 段，`active_provider: null`）
3. llm_secret.bin 不存在 → 不动
4. 状态条"⚠ AI 未配置"

### 11.2 老用户升级
1. 旧 config.yaml 存在但无 `llm:` 段
2. sidecar 启动时检测 → 自动 merge 追加 `llm: {active_provider: null, default_model: null, base_url: null}`
3. 旧 corp / ui / daily_report / update / logging 段**完全保留**

### 11.3 配置失效（DPAPI 解不开）
1. sidecar 启动时轻量解密测试
2. 失败 → 删 llm_secret.bin + config.llm.active_provider = null
3. 状态条 "AI 配置已失效，请重新配置"

### 11.4 回退
- `active_provider: null` = 走老路（`awaiting_ai` + 占位 stub）
- 整套 LLM 功能 fail，工具仍能跑（只损失"真 AI 总结"这一项）

---

## 12. 实现优先级

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | ai_bridge 进程骨架 + DPAPI + 1 个 provider（OpenAI）端到端 | 配 OpenAI 真 key → 一键生成真报告 |
| P1 | 10 家 providers preset + 切厂商 + UI modal + 状态条 | 集成 case 1-5 全过 |
| P2 | 错误处理矩阵 + 日志脱敏 + 诊断导出脱敏 | 集成 case 6-12 全过 |
| P3 | 单测 + 打包 + 回归 | 单测全绿，老功能不破 |

---

**审阅要点**：
- 段 2 状态模型是否清晰
- 段 4 数据流是否覆盖所有路径
- 段 6 UI 是否符合预期
- 段 8 文件改动清单是否完整
- 段 9 验收清单是否满足你心里"完成"的定义
