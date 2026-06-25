# Mac S5 辅助功能回归清单

> **前置**：S4 主链路验收 7/7 通过。
> **目的**：覆盖 S4 没碰的 send / 历史 / LLM 配置路径，确保 Mac 端功能完整性。
> **预计耗时**：20–40 分钟。

---

## 1. 发送消息（send.py 路径）

### 1.1 私聊发给自己

> 这是 v0.3.11 修复回归（之前误发到置顶单聊的对方）。

1. UI 里打开「会话列表」，找到「自己」（置顶的 userId 一致的那个）
2. 点发送消息（任意文本，比如「Mac 端测试 S5.1.1」）
3. 期望：消息出现在自己的钉钉 App 里（不是置顶单聊的对方）

**验收**：
- 自己的钉钉 App 收到这条消息（注意是「文件传输助手」或自己的「我的电脑」之类的入口）
- 不是发给任何其他用户

### 1.2 私聊发给群成员

1. 选一个群，点群成员列表，找一个真人
2. 点「发消息」发文本（任意）
3. 期望：对方收到（你可以在自己另一个钉钉账号验证）

### 1.3 群聊发文本

1. 选一个 2 人以上群
2. 发文本 + @某成员
3. 期望：群里其他人都能看到，@的那位有红点提醒

### 1.4 发送图片（PNG）

1. UI 里选本地 PNG
2. 期望：图片正常显示在群里，不被 Mac 端的 NSSavePanel 拦

**失败排查**：
| 现象 | 原因 | 处理 |
|---|---|---|
| 「找不到会话」 | dws cookie 过期，回到 S4 §4.2 重登 | |
| 发送 0 字节 | entitlements 缺 `files.user-selected.read-write` | 加进 entitlements.mac.plist + 重 build |
| 自己的钉钉没收到 | v0.3.11 回归，回 `sidecar/core/send.py` 检查 userId 严格匹配 | |

---

## 2. 历史会话列表

### 2.1 加载会话列表

1. UI 里点「会话列表」
2. 期望：分页加载出来（每页 50 条）
3. 期望：会话按最近消息时间倒序

### 2.2 滚动到底翻页

1. 滚到底部
2. 期望：自动加载下一页（hasMore=true）
3. 期望：dws `chat conversation list` 多次调用

### 2.3 过滤

1. 选「只看群聊」「只看私聊」「只看未读」三档切换
2. 期望：UI 立刻过滤显示

### 2.4 搜索

1. 输入关键词（如群名 / 人名）
2. 期望：实时过滤会话

**失败排查**：
- 「无更多」 立即出现：dws `hasMore` 字段解析 bug
- 过滤不生效：前端 filter 与 dws chatType 不一致

---

## 3. LLM 配置（ai_bridge + providers）

### 3.1 增

1. UI 里「LLM 配置」点新增
2. 选 provider（如「通义千问」）
3. 填 model + API key
4. 测连通性（点「测试」）
5. 期望：返回 200 + 「连接成功」

### 3.2 改

1. 改某个 provider 的 model 名（比如 qwen-plus → qwen-turbo）
2. 期望：保存成功
3. 关掉 `.app`，重开
4. 期望：改动仍在

### 3.3 删

1. 删一个不用的 provider
2. 期望：列表里消失，session localStorage 也清掉

### 3.4 Keychain 落盘验证

> Mac 端 LLM key 走 Keychain，不走文件（v0.3.9 的 Windows 加密方案不适用于 Mac）。

```bash
# 看 keychain 里有没有这条
security find-generic-password -s "DingTalkBox"
# 期望: 列出 keychain item
```

如果 keychain 里没这条，配置没真正落盘 → 关掉重开会丢。

**修改 keychain 项的 helper**：
```bash
# 删（用于重测）
security delete-generic-password -s "DingTalkBox" -a "llm_secret"
```

### 3.5 多 key 切换

1. 加 2 个 provider key（比如「工作」「个人」标签）
2. 切换 active key
3. 期望：下次 AI 调用走新 key
4. 关掉重开，active key 仍记

---

## 4. 日报多日期

> S4 §4.3 验过一天，现在验多日期 + 边界。

### 4.1 昨天日期

1. 选昨天
2. 期望：能拉到昨天消息（前提：昨天登录过钉钉）

### 4.2 一周前

1. 选一周前
2. 期望：dws 能拉到（无 hasMore 限制）

### 4.3 节假日 / 没消息

1. 选个长假日期
2. 期望：UI 显示「无消息」，不崩

### 4.4 切换 AI provider

1. 在 LLM 配置切到不同的 provider
2. 重跑同一天日报
3. 期望：用新 provider 出图（渲染风格可能略变，但内容应相似）

---

## 5. Mac 特定 edge case

### 5.1 退出后再启动

1. Cmd + Q 退出 .app
2. 双击启动
3. 期望：直接恢复上次会话列表（不用重新登录）
4. 期望：keychain key 仍在

### 5.2 系统休眠唤醒

1. 让 Mac 进睡眠
2. 唤醒
3. 期望：pywebview 仍可点（不卡死）
4. 期望：dws 进程还活着

### 5.3 切换 Wi-Fi

1. 拔网线 / 切到别的 Wi-Fi
2. 期望：UI 显示「网络断开」
3. 恢复网络
4. 期望：自动恢复，不需要重启

### 5.4 Dock 多窗口

1. UI 里点「在新窗口打开」
2. 期望：macOS Dock 上有多个钉钉AI助手图标
3. 关一个不影响另一个

### 5.5 通知中心

1. 触发一条新消息（让同事发你）
2. 期望：macOS 通知中心弹出（如果 entitlements 配了推送）
3. 注：v0.3.15 没配推送，这条可能不通过

---

## 6. 性能 baseline

| 操作 | Mac（M1 Air） | Mac（M2 Pro） | Windows 参考值 |
|---|---|---|---|
| 冷启动到窗口出现 | < 3s | < 2s | ~2s |
| 日报全链路（1000 条消息） | < 30s | < 20s | ~25s |
| 会话列表加载（200 条） | < 5s | < 3s | ~4s |
| 发送文本 | < 1s | < 1s | < 1s |

**实测** 填在下面，超过 2x 参考值 = 性能回归。

| 操作 | 实测 |
|---|---|
| 冷启动 | |
| 日报全链路 | |
| 会话列表 | |
| 发送文本 | |

---

## 7. 验收通过标准

| # | 项 | 通过条件 | 实际 |
|---|---|---|---|
| 1.1 | 私聊发给自己 | 自己的钉钉收到 | ☐ |
| 1.2 | 私聊发群成员 | 对方收到 | ☐ |
| 1.3 | 群聊发文本+@ | 群里可见 | ☐ |
| 1.4 | 发图片 | 群里可见图片 | ☐ |
| 2.1-2.4 | 会话列表加载/翻页/过滤/搜索 | 全可用 | ☐ |
| 3.1-3.5 | LLM 配置增改删 + Keychain | 全可用，重启不丢 | ☐ |
| 4.1-4.4 | 多日期 + AI 切换 | 全可用 | ☐ |
| 5.1-5.5 | Mac edge case | 不崩、不卡 | ☐ |
| 6 | 性能 | 2x Windows 内 | ☐ |

**9 项全过 = S5 完成**。

---

## 8. 故障汇总 → 给我贴什么

如果有任何一项挂了，把这些信息贴回群：

```bash
# 1. .app 启动 log（终端跑）
./deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/dingtalk_box 2>&1 | head -100

# 2. dws 状态
"$HOME/Library/Application Support/DingTalkBox/bin/dws" --version
"$HOME/Library/Application Support/DingTalkBox/bin/dws" auth status

# 3. 文件系统
ls -la "$HOME/Library/Application Support/DingTalkBox/"

# 4. Keychain（如果有 key 问题）
security find-generic-password -s "DingTalkBox"

# 5. Console.app log（如果有 WebKit 崩）
log show --predicate 'process == "dingtalk_box"' --last 30m | tail -50

# 6. Mac 型号 + 系统版本
sw_vers
uname -m
```

---

## 9. 不在 S5 范围（已知 v0.3.15 限制）

- ❌ **代码签名**：v0.3.15 不签（ad-hoc 签名只消除部分警告），Gatekeeper 首次仍需手动放行
- ❌ **公证（notarization）**：未做，企业内部分发可用（不走 Mac App Store）
- ❌ **自动更新**：Mac 版本不走 Windows 的 update.exe 通道
- ❌ **多语言**：UI 是中文（与 Windows 一致）
- ❌ **推送通知**：未配（v0.3.15 没接 UNUserNotificationCenter）

以上都是 S6 文档里会说明的「已知限制」，不是 bug。