# dws CLI 访问权限申请流程（设计稿）

- **日期**：2026-06-18
- **状态**：设计中（待 review）
- **范围**：单次一个 spec，覆盖 sidecar `auth.py` + 前端 `login-modal` 改造 + 状态机

---

## 1. 目标 & 非目标

### 1.1 目标
当用户所在企业 **未开启 dws CLI 访问权限** 时，让工具能：
1. **自动检测** 这一状态（dws auth status 报 `cli_access_enabled: false`）
2. **引导用户完成申请**（复用 dws 自带的 device flow，弹浏览器到 dws 申请页）
3. **轮询审批结果**（管理员审批通过后，自动恢复登录态、继续后续操作）
4. **不破坏现有登录流程**（已开启 CLI 访问的企业，行为完全不变）

### 1.2 非目标（YAGNI）
- 管理员后台的二次开发（只引导用户去 dws 自带页面，不在工具内做审批 UI）
- 多 corp 切换的 CLI 权限管理
- 申请被拒后的申诉流程（仅显示提示 + 让用户重新发起申请）
- 跨 corp 共享 token / 离线 token 缓存
- macOS / Linux 平台（仅 Windows 11+，与现有 dws 部署一致）
- "申请进度通知"（如审批通过后给用户发钉钉消息）——dws 本身没这能力

---

## 2. 架构总览

不增加新进程，只改两个模块：

```
launcher (dingtalk_box.exe, frozen)
    │  JSON-RPC: trigger_login / get_login_status
    ▼
sidecar (sidecar.exe, frozen)
    │  subprocess.run(["dws", "auth", "login", "--device"])   ← 新增 device flow 调用
    │  subprocess.run(["dws", "auth", "status"])                ← 扩展返回值
    ▼
dws.exe (1.0.34+)
    │  HTTP
    ▼
DingTalk OAuth (含 corp CLI 访问权限校验)
```

**关键不变量**：
- **复用 dws 自带流程** —— 不自己实现 OAuth 申请页、不解析 HTML，只调 `dws auth login --device` 拿 device_url + user_code，然后 webbrowser.open
- **后端不持有状态** —— 每次轮询都重新调 `dws auth status`，sidecar 内只在函数局部缓存最近一次结果
- **状态机唯一** —— 前端维护 `loginState`，sidecar 只暴露只读 API
- **现有用户零感知** —— 已登录 / 已开启 CLI 访问的 corp 不出现新 modal、不增加任何日志

---

## 3. 数据流

### 3.1 trigger_login 响应（扩展）

**现状**：
```json
{
  "ok": true,
  "qr_url": "https://login.dingtalk.com/oauth/...",
  "hint": "请用手机钉钉扫描浏览器中的二维码登录"
}
```

**扩展后**：
```json
{
  "ok": true,
  "status": "need_user_auth" | "need_admin_approval" | "login_complete",
  "qr_url": "https://login.dingtalk.com/oauth/...",      // status=need_user_auth
  "device_url": "https://login.dingtalk.com/oauth/device?code=XYZ",  // status=need_admin_approval
  "user_code": "ABCD-EFGH",                                // status=need_admin_approval
  "message": "需要企业管理员授权 dws CLI 访问权限"         // status=need_admin_approval
}
```

### 3.2 get_login_status 响应（扩展）

**现状**：
```json
{
  "logged_in": true,
  "user": { "user_id": "...", "name": "...", "avatar": "..." },
  "corp": { "corp_id": "ding..." }
}
```

**扩展后**：
```json
{
  "logged_in": true,
  "cli_access_enabled": true,           // 新增：corp 是否开启 dws CLI 访问
  "cli_access_status": "enabled" | "pending" | "not_enabled" | "denied" | "unknown",
  "cli_access_message": "...",          // 可选：dws 返回的详细原因（供前端展示）
  "user": { ... },
  "corp": { ... }
}
```

### 3.3 新增 RPC：poll_cli_approval（轮询专用）

**请求**：`{}`（无参数）
**响应**：
```json
{
  "enabled": true | false,
  "status": "enabled" | "pending" | "not_enabled" | "denied" | "unknown",
  "changed": true,                      // 与上次轮询相比是否变化
  "message": "..."                       // 变化原因（用于 toast）
}
```

`poll_cli_approval` 与 `get_login_status` 的区别：
- `get_login_status`：返回完整用户态（heavy，每次都查 user / corp / avatar）
- `poll_cli_approval`：只查 `dws auth status` 的 `cli_access_*` 字段（light，10s 一次轮询无压力）

---

## 4. 模块拆分

### 4.1 sidecar/core/auth.py

| 函数 | 改动 |
|------|------|
| `get_login_status` | 调 dws auth status，解析 `cli_access_enabled` 等字段塞到返回 dict |
| `trigger_login` | 改为先调 `dws auth login --device`，从 stdout 抓 device_url + user_code，状态标记为 `need_admin_approval` |
| `poll_cli_approval` | **新增**：只查 dws auth status 的 cli 字段，与模块内缓存对比，返回 changed 标志 |
| `_parse_dws_status` | **新增**：从 dws auth status JSON 解析 cli_access_* 字段，兼容字段缺失（fallback `unknown`） |

### 4.2 sidecar/core/dispatcher.py

| 改动 | 位置 |
|------|------|
| 注册 `poll_cli_approval` → `auth.poll_cli_approval` | dispatcher 方法表 |

### 4.3 src/index.html

| 改动 | 位置 |
|------|------|
| `login-modal` 内部加 `<div id="login-admin-approval" hidden>` 分支 | login-modal 节点内 |
| 含：大标题、状态文案、device_url 显示、4 个按钮（在浏览器打开 / 复制链接 / 我已申请 / 重新检测） | — |

### 4.4 src/main.js

| 函数 | 改动 |
|------|------|
| `showLoginModal` | 根据 `status` 决定显示 user-auth 分支还是 admin-approval 分支 |
| `pollLoop` | **新增**：每 10s 调 `poll_cli_approval`，最多 30 次（5 min），status 从 `not_enabled` / `pending` 变 `enabled` 时停 |
| `applyLoginState` | **新增**：根据 cli_access_status 切换 modal 内子视图（未申请 / 等待审批 / 已通过 / 被拒） |
| `openDeviceUrl` | **新增**：调 launcher `window.dtbox.openExternal(device_url)` 打开 URL（用系统默认浏览器） |
| `copyDeviceUrl` | **新增**：调 clipboard API 复制 device_url 到剪贴板 |

### 4.5 src/style.css

| 改动 | 位置 |
|------|------|
| `.login-admin-approval` 样式 | 登录 modal 子视图样式 |
| `.device-code-box`（大字号 user_code 展示框） | 显眼地显示 user_code |
| `.login-status[data-state="pending"]` / `[data-state="denied"]` | 状态点颜色 |

---

## 5. 状态机

```
        ┌──────────────┐
        │  idle        │  (boot 完成，dws status 已知)
        └──────┬───────┘
               │ get_login_status → cli_access_enabled=false
               ▼
   ┌─────────────────────┐
   │ need_admin_approval │  (modal 弹「申请授权」分支，4 个按钮)
   └──────┬──────────────┘
          │ 用户点 [我已申请,等待审批]
          ▼
   ┌─────────────────────┐
   │ pending_approval    │  (启动 pollLoop，禁用 [我已申请]，保留 [重新检测])
   └──────┬──────────────┘
          │ poll → cli_access_status=enabled
          ▼
   ┌─────────────────────┐
   │ approved            │  (关 modal，toast「✓ 已开通，开始使用」)
   └─────────────────────┘

旁路：
- 任意状态点 [重新检测] → 立即调一次 get_login_status，状态重算
- pending_approval 状态下 [重新检测] → 立即调一次 poll_cli_approval
- pollLoop 跑满 30 次还没变 → 状态保留 pending_approval，文案改为「审批较慢，可继续等待或联系管理员」
- poll 收到 status=denied → 切换到「申请被拒」视图，显示原因 + [重新发起申请] 按钮（回到 need_admin_approval）
```

---

## 6. 错误处理

| 场景 | 检测点 | 行为 |
|------|--------|------|
| dws auth status 没有 `cli_access_enabled` 字段 | `_parse_dws_status` | 返回 `cli_access_status: "unknown"`，**不**触发申请流（兜底） |
| dws auth status 字段在 →  false | get_login_status | 返回 `cli_access_enabled: false`，前端按需弹 modal |
| `dws auth login --device` 超时（拿不到 device_url） | trigger_login | 退到现有 fallback：返回 `status: "need_user_auth"`，让用户去终端扫码 |
| 启动 pollLoop 后 dws 进程意外退出 | poll_cli_approval 抛 DwsError | 停 poll，状态保留，UI 显示「检测中断，点重新检测」 |
| 申请被拒 | poll 返回 `status: "denied"` | 切到「被拒」视图，显示 dws 返回的 message |
| 申请过程中 token 过期（refresh_token_valid: false） | get_login_status | 不影响 cli 检测，cli_access_enabled 仍准确；用户后续操作时再触发重新登录 |
| 用户是 corp admin | get_login_status 解析 user.isAdmin | （未来扩展）简化文案：直接给管理员后台 URL，不显示「找管理员」步骤 |
| dws 升级后字段名变了 | `_parse_dws_status` | 兼容：找不到字段就 `unknown`，不报错 |

---

## 7. 测试

### 7.1 单元测试
- `tests/test_auth_cli_status.py`（新增）：
  - mock dws 输出 5 种 cli_access_status，验证 `_parse_dws_status` 解析正确
  - mock 字段缺失 → 返回 `unknown`，不抛异常
- `tests/test_trigger_login_device.py`（新增）：
  - mock `dws auth login --device` 输出，验证 device_url + user_code 正确提取
  - mock 超时场景 → 返回 `status: "need_user_auth"`

### 7.2 集成测试
- `tests/test_dispatcher_poll_cli.py`（新增）：
  - mock dws status 变化，验证 `poll_cli_approval` 的 `changed` 标志正确

### 7.3 手动测试 checklist
- [ ] 在一个 **未开启 CLI 访问** 的 corp 账号下跑 → 应弹「申请授权」modal
- [ ] 复制 device_url → 浏览器能打开
- [ ] 点 [我已申请] → UI 进 pending_approval，10s 后开始轮询
- [ ] 让管理员审批通过 → 5 min 内 modal 自动关，toast「✓ 已开通」
- [ ] 拒绝 → UI 切到「被拒」视图
- [ ] 5 min 没批 → 文案变「审批较慢」
- [ ] **已开启 CLI 访问的 corp** 跑同一流程 → 行为与现状完全一致（无新 modal、无新字段感知）

---

## 8. 实施计划

下一步走 `superpowers:writing-plans` 写详细实施步骤。

---

## 9. 不确定点（待实测确认）

- `dws auth status` 的 JSON 是否真的有 `cli_access_enabled` 字段 → 没确认
- `dws auth login --device` 在未授权 corp 下的具体输出格式 → 没确认
- 审批被拒时 dws 返回的字段名 / message 文案 → 没确认

以上三点基于「合理假设」设计。**实施时第一步是**：用一个未授权 corp 实际跑 `dws auth login --device` 和 `dws auth status`，把真实输出贴回来，再微调 `_parse_dws_status` 和 `trigger_login` 的解析逻辑。
