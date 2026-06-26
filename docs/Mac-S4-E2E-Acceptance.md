# Mac S4 端到端验收清单（commit 3c8416b+）

> **目的**：在 Mac 真机上把 Mac 版「钉钉AI助手」从源码一路跑到日报全流程出图，验证 S3 构建管道产出的 `.app` 能用。
> **预计耗时**：30–60 分钟（首次编译 10–15 分钟，之后 lipo 增量 <2 分钟）。
> **环境要求**：macOS 11.0+，能跑 Xcode Command Line Tools，有 Python 3.13+，能访问 GitHub（拉代码 + 拉 dws release）。

---

## 0. 一次性环境准备（首次需要，后续可跳过）

```bash
# 0.1 Xcode CLI（自带 sips / iconutil / codesign / lipo）
xcode-select --install

# 0.2 Python 3.13（系统自带可能是 3.9，用 brew 装）
brew install python@3.13
export PATH="/opt/homebrew/opt/python@3.13/bin:$PATH"   # Apple Silicon
# Intel: export PATH="/usr/local/opt/python@3.13/bin:$PATH"
python3.13 --version    # 应输出 Python 3.13.x

# 0.3 装项目依赖
cd <clone 下来的 dingtalk_box 根>
git checkout macos-port
python3.13 -m pip install -r requirements.txt
python3.13 -m pip install "pyinstaller>=6.0"
```

---

## 1. 拉代码 + 编译 universal2

```bash
# 1.1 拉代码（如果还没拉）
git clone <你的 repo URL> dingtalk_box
cd dingtalk_box
git checkout macos-port

# 1.2 编译（arm64 + x86_64 + lipo 合并）
./scripts/build_macos.sh universal2
```

**期望输出（最后几行）**：
```
[build_macos] === build arm64 ===
...
[build_macos] === build x86_64 ===
...
[build_macos] === lipo merge ===
Architectures in the fat file: ... are: x86_64 arm64
...
[build_macos] done. 产物: deliver/dingtalk_box_mac/
dingtalk_box.app/
sidecar
ai_bridge
```

**失败排查**：
| 现象 | 原因 | 处理 |
|---|---|---|
| `sips: command not found` | 没装 Xcode CLI | 跑 `xcode-select --install` |
| `iconutil: ... cannot find` | `assets/logo.png` 不存在且 .ico 也失败 | 手动放一张 PNG 到 `assets/logo.png` (1024x1024 推荐) |
| `pyinstaller: command not found` | pip 装到了别的 python | 用 `python3.13 -m pip install pyinstaller`，然后 `python3.13 -m PyInstaller ...` |
| `--target-arch` flag 报 unknown | pyinstaller < 6.0 | `python3.13 -m pip install --upgrade "pyinstaller>=6.0"` |
| `KeyringNotFoundError` 在 build 阶段 | macOS keyring 缺失（极少见） | `pip install keyring` |

---

## 2. 验证产物

```bash
# 2.1 .app 是 arm64 + x86_64
lipo -info deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/dingtalk_box

# 2.2 子进程是 universal2
lipo -info deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/sidecar
lipo -info deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/ai_bridge

# 2.3 .app 内部结构
find deliver/dingtalk_box_mac/dingtalk_box.app -maxdepth 4 -type f | head -30
# 期望看到:
#   Contents/Info.plist
#   Contents/MacOS/dingtalk_box
#   Contents/MacOS/sidecar
#   Contents/MacOS/ai_bridge
#   Contents/Resources/...

# 2.4 Info.plist 字段
/usr/libexec/PlistBuddy -c "Print :CFBundleDisplayName" deliver/dingtalk_box_mac/dingtalk_box.app/Contents/Info.plist
# 期望: 钉钉AI助手
/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" deliver/dingtalk_box_mac/dingtalk_box.app/Contents/Info.plist
# 期望: 11.0
/usr/libexec/PlistBuddy -c "Print :NSHighResolutionCapable" deliver/dingtalk_box_mac/dingtalk_box.app/Contents/Info.plist
# 期望: true
```

**期望**：`sidecar` 和 `ai_bridge` 都是 `Architectures in the fat file: ... are: x86_64 arm64`。

---

## 3. Gatekeeper 首次放行

> ⚠️ 第一次双击 `.app` 必被 Gatekeeper 拦，需要手动放行（**ad-hoc 签名 + 未公证 = 标准行为**）。

1. Finder 里双击 `deliver/dingtalk_box_mac/dingtalk_box.app`
2. 弹窗：「无法打开"dingtalk_box"，因为它来自身份不明的开发者」
3. **系统设置** → **隐私与安全性** → 向下滚到最下 → 看到「已阻止打开 dingtalk_box，因为开发者无法验证」→ 点 **仍要打开**
4. 再次弹窗，再点 **仍要打开**
5. 之后启动不再弹

**替代 CLI 放行**（一条命令搞定）：
```bash
xattr -dr com.apple.quarantine deliver/dingtalk_box_mac/dingtalk_box.app
codesign --force --deep --sign - --entitlements assets/entitlements.mac.plist deliver/dingtalk_box_mac/dingtalk_box.app
open deliver/dingtalk_box_mac/dingtalk_box.app
```

---

## 4. 主链路验收

### 4.1 启动 + dws 安装

1. `.app` 启动后，pywebview 窗口应出现（标题：钉钉AI助手）
2. 首次启动会触发 dws 下载（**install.py → GitHub release → SHA256 校验**）
3. 终端 log 应看到类似：
   ```
   [install_dws] 未找到 dws，正在下载 darwin-arm64 vX.Y.Z ...
   [install_dws] 校验 SHA256 ...
   [install_dws] 解压到 ~/Library/Application Support/DingTalkBox/bin/dws ...
   [install_dws] OK
   ```

**期望**：dws 下载成功，控制台无报错。

**dws 安装位置验证**：
```bash
ls -la "$HOME/Library/Application Support/DingTalkBox/bin/dws"
"$HOME/Library/Application Support/DingTalkBox/bin/dws" --version
```

### 4.2 dws 登录

1. UI 里点「登录钉钉」
2. 自动开浏览器（DWS 官方登录页）
3. 钉钉 App 扫码确认
4. 浏览器跳回，UI 显示「已登录」

**期望**：UI 显示你的钉钉昵称和头像。

**失败排查**：
- 浏览器没开：检查 `~/.config/DingTalk/dws/` 路径是否被防火墙拦
- 扫码成功但 UI 没反应：检查 `~/Library/Application Support/DingTalkBox/cache/dws_release.json` 是否有缓存污染

### 4.3 日报全链路

1. UI 里点「生成日报」（或选日期 + 生成）
2. 后台跑：`dws chat message list-all` → `dingtalk_daily_summary` → AI 分析 → `dingtalk-daily-render` → PNG
3. 终端 log 应看到：
   ```
   [daily_report] Step 1: 导出 bundle ...
   [daily_report] Step 2: AI 分析 ...
   [daily_report] Step 3: 渲染 PNG ...
   [daily_report] 完成: 钉钉工作纪要日报-YYYY-MM-DD.png
   ```

**期望**：
- 浏览器里能看到预览
- `~/Library/Application Support/DingTalkBox/output/钉钉工作纪要日报-YYYY-MM-DD.png` 存在
- 文件大小 > 100KB（小于这个说明渲染失败）
- 双击 PNG 在 Preview.app 能打开

### 4.4 Keychain 验证（保护 LLM key）

1. UI 里改一下 LLM 配置（换 provider / 改 model）
2. 关掉 `.app`，再打开
3. 配置仍在

**背后验证**：
```bash
# Mac 端 LLM key 走 Keychain（不是 llm_secret.bin）
security find-generic-password -s "DingTalkBox" -a "llm_secret"
# 期望: 输出 keychain item 详情（用户名/服务名匹配）
```

**失败排查**：
- Keychain 弹窗没出来：检查 `~/.config/DingTalkBox/ai_bridge/` 是否被 sandbox 拦
- 改完关掉再开丢了：Mac 后端的 `~/.config/DingTalkBox/ai_bridge/dpapi_macos.py` 有 bug，需排查

---

## 5. 故障排查参考

### 5.1 pywebview 启动黑屏

```bash
# 终端直接跑二进制看完整 log
./deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/dingtalk_box
```

期望看到 `[sidecar] spawn ...` 之类的 log。**黑屏 + 无 log** 通常是 entitlements 缺 `disable-library-validation`，WebKit 框架加载失败。

### 5.2 Console.app 查日志

```bash
# 启动 .app 后
open /Applications/Utilities/Console.app
# 搜 "dingtalk_box" 或 "pywebview"
```

### 5.3 完全清理重装

```bash
rm -rf "$HOME/Library/Application Support/DingTalkBox/"
rm -rf "$HOME/Library/Caches/DingTalkBox/"
xattr -dr com.apple.quarantine deliver/dingtalk_box_mac/dingtalk_box.app
open deliver/dingtalk_box_mac/dingtalk_box.app
```

---

## 6. 验收通过标准

| # | 项 | 通过条件 | 实际 |
|---|---|---|---|
| 1 | `./scripts/build_macos.sh universal2` 无错 | exit 0 + lipo 产物含 arm64+x86_64 | ☐ |
| 2 | `.app` 双击启动不闪退 | 窗口出现 | ☐ |
| 3 | Gatekeeper 放行后能启动 | 见 §3 | ☐ |
| 4 | dws 自动下载安装成功 | `bin/dws --version` 有输出 | ☐ |
| 5 | dws 扫码登录成功 | UI 显示已登录 | ☐ |
| 6 | 日报全链路出图 | PNG 生成且可打开 | ☐ |
| 7 | Keychain 持久化 | 改完配置关掉重开仍在 | ☐ |

**7/7 通过 = S4 完成**。任何一项挂了，把现象 + Console.app log 贴回群。

---

## 7. 回滚

如果 Mac 版有阻塞性 bug 想退回 Windows 版本：

- Windows 版 release 是独立的 `dingtalk_box.zip`（v0.3.15 在 `deliver/` 下），与 Mac 版本无耦合
- Mac 安装是独立的 `~/Library/Application Support/DingTalkBox/`，删除即可

```bash
rm -rf "$HOME/Library/Application Support/DingTalkBox/"
rm -rf deliver/dingtalk_box_mac/
```
<!-- trigger: 2026-06-26 11:09:16 -->
