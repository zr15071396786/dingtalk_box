---
title: 钉钉AI助手 macOS 端口设计
date: 2026-06-22
status: design (待用户审核)
target_release: v1.0.0-mac（首次 Mac 发版，独立版本号）
related: [dingtalk-v0.3.10-release]
---

# 钉钉AI助手 macOS 端口设计

## 0. 背景

钉钉AI助手（dingtalk_box）目前只在 Windows 上验证通过（v0.3.10）。用户希望同步支持 macOS（Intel + Apple Silicon），让 Mac 用户也能用同一套功能（拉钉钉消息 → AI 整理日报 → 发送给自己）。

约束：
- **不影响 Windows**：Mac 改动全部走 `if sys.platform == 'darwin': ...` 分支，Windows 端零行为变更。
- **两套系统独立运行**：Windows 用户继续走 Windows 包 + DPAPI；Mac 用户走 Mac 包 + Keychain。跨平台数据不共享。
- **一次性完整发版**：不先做 PoC，直接分阶段交付 Mac 版 v1.0（能登录、能生成日报、能发钉钉）。
- **公司内部渠道**：不上 App Store、不走 TestFlight，Developer ID 公证先不做（首版）。

## 1. 调研结论

### 1.1 dws Mac 版本（关键 blocker，已解除）

钉钉官方仓库 [DingTalk-Real-AI/dingtalk-workspace-cli](https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli) v1.0.39 release 同时发布了 4 个平台的 dws 二进制：

| 平台 | 资产 |
|---|---|
| darwin-amd64 | `dws-darwin-amd64.tar.gz`（Intel Mac） |
| darwin-arm64 | `dws-darwin-arm64.tar.gz`（Apple Silicon） |
| windows-amd64 | `dws-windows-amd64.zip`（已有） |
| windows-arm64 | `dws-windows-arm64.zip` |
| linux-amd64 / arm64 | 顺便发布（暂不用） |

Mac 端口的**核心依赖有解**，可以推进。

### 1.2 现有代码的 Mac 兼容性盘点

| 模块 | 当前实现 | Mac 兼容性 | 改造点 |
|---|---|---|---|
| `paths.data_dir()` | 已含 darwin 分支（`~/Library/Application Support/DingTalkBox`） | ✅ | 无 |
| `paths.dws_exe_path()` | 已含 `sys.platform.startswith('win')` 分支（`dws.exe` vs `dws`） | ⚠️ 部分 | 补 darwin 分支（`dws`，无需扩展名） |
| `paths.user_dws_path()` | 已含 darwin 分支名 | ✅ | 无 |
| `dws_runner.py` | `subprocess.run` 跨平台；`_creationflags()` 已分支 | ✅ | 无 |
| `key_crypto.py`（launcher 内置 .env 加密） | Argon2id + AES-GCM via `cryptography` lib | ✅ | 无（完全跨平台） |
| `pywebview` | 跨平台；Mac 自动用 WebKit via PyObjC | ⚠️ 兼容性 | 测试阶段重点验证键盘快捷键 / 文件对话框 / 右键菜单 |
| `ai_bridge/dpapi.py` | `ctypes.windll.crypt32.CryptProtectData`（Windows-only） | ❌ | **核心改造点**：抽象成接口，新增 Mac 后端走 Keychain |
| `llm_config.py` 的 `set_config` / `resolve_api_key` | 通过 `dpapi.protect_to_file/unprotect_from_file` 间接依赖 Windows | ⚠️ 间接 | 接口签名不变，dpapi 实现层改造 |
| `launcher.py` 启动逻辑 | `__file__.parent` 等 frozen 路径解析 | ⚠️ frozen 模式有 Mac 等价坑 | 复用 `dingtalk-frozen-env-pitfall` 的排查模式 |
| `requirements.txt` | pyyaml / Pillow / pywebview / pyinstaller / argon2-cffi / cryptography | ⚠️ 缺 keyring | Mac 端要加 `keyring>=24.0`（Mac 后端用） |

**结论**：路径 / dws 调度 / pywebview / 加密基线 4 类已基本兼容，**唯一需要新写的代码是 DPAPI 的 Mac 后端**。

## 2. 架构决策

### 2.1 DPAPI 抽象层设计

把 `ai_bridge/dpapi.py` 从"Windows DPAPI 实现"升级为"平台无关接口 + Windows/Mac 后端"：

```python
# ai_bridge/dpapi.py（重构后）
from .dpapi_common import SecretBackend

def get_backend() -> SecretBackend:
    """根据当前平台返回对应后端（单例进程内缓存）"""
    if sys.platform.startswith("win"):
        from .dpapi_windows import WindowsDpapiBackend
        return _CACHED_BACKEND or WindowsDpapiBackend()
    if sys.platform == "darwin":
        from .dpapi_macos import MacosKeychainBackend
        return _CACHED_BACKEND or MacosKeychainBackend()
    raise RuntimeError(f"不支持的平台: {sys.platform}")

# 保留旧的函数名 protect / unprotect / protect_to_file / unprotect_from_file
# 作为对老的 call site 的兼容 facade，内部转发到 get_backend()
def protect_to_file(plaintext: str, path: str) -> None:
    get_backend().protect_to_file(plaintext, path)
# ... 其他函数同理
```

```python
# ai_bridge/dpapi_common.py
from typing import Protocol

class SecretBackend(Protocol):
    """平台无关的密钥保护后端接口"""
    def protect_to_file(self, plaintext: str, path: str) -> None: ...
    def unprotect_from_file(self, path: str) -> str: ...
```

```python
# ai_bridge/dpapi_macos.py（新增）
import keyring
CREDENTIAL_SERVICE = "DingTalkBox"
CREDENTIAL_KEY_PREFIX = "llm_secret_"

class MacosKeychainBackend:
    """Mac 端用 Keychain Services（通过 `keyring` 库）保护用户 LLM key

    设计要点：
    - Keychain 项名 = "DingTalkBox_llm_secret_<uuid>"，避免多用户/多机冲突
    - 第一次 protect 时生成 UUID + 存 Keychain，把 UUID 写入文件
    - unprotect 时读文件的 UUID → 查 Keychain
    - Keychain 不可用（无 GUI session / SSH-only）→ 走 fallback AES 文件加密，
      仍写到同一文件，前 16 bytes 是 "AES1:" 标识 + nonce
    """

    def protect_to_file(self, plaintext: str, path: str) -> None:
        from pathlib import Path
        import uuid
        key_id = str(uuid.uuid4())
        try:
            keyring.set_password(CREDENTIAL_SERVICE, key_id, plaintext)
            storage_method = "keychain"
        except Exception:
            # Keychain 不可用 → AES fallback
            from .dpapi_macos_fallback import AesFileBackend
            storage_method = "aes"
            # ...aes 后端自己处理
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{storage_method}:{key_id}\n", encoding="utf-8")

    def unprotect_from_file(self, path: str) -> str:
        from pathlib import Path
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"secret file not found: {path}")
        line = p.read_text(encoding="utf-8").strip()
        method, key_id = line.split(":", 1)
        if method == "keychain":
            v = keyring.get_password(CREDENTIAL_SERVICE, key_id)
            if not v:
                raise RuntimeError("Keychain 条目不存在或被删除")
            return v
        elif method == "aes":
            from .dpapi_macos_fallback import AesFileBackend
            return AesFileBackend().unprotect_with_id(key_id, path)
        else:
            raise RuntimeError(f"未知 storage method: {method}")
```

**为什么不直接用 `keyring.set_password` 后 ID 写哪？**：
- Keychain 不存 UUID，只存"service + username → password"映射
- 我们的 llm_secret.bin 文件存在文件系统，需要 UUID 关联才能找回 Keychain 项
- 一份文件（业务标识）+ 一份 Keychain 项（密钥本体）= 跨重启可恢复

**AES fallback 加密方案**（D4 决策点的具体实现）：
- Keychain 写入失败时（SSH-only / 无 GUI session）启用
- 密钥派生：`scrypt(password=本机 serial + user, salt=固定 APP_SALT, n=2^15)` 派生 32 字节主密钥
- 加密：主密钥 + 文件内容 → AES-256-GCM（nonce 每次随机 12 bytes，前缀写入文件）
- 文件格式：`AES1:<base64(nonce)><base64(ciphertext+tag)>` 一行字符串
- 安全性低于 Keychain（密钥来自机器可读取的硬件信息），但仍比明文好

**Windows 端保持不变**：`dpapi_windows.py` 就是现有 `dpapi.py` 的实现 + 同样的 `SecretBackend` 接口。

### 2.2 dws 安装流程

Mac 端 install.py 改动（仿照 Windows 流程）：

```python
# sidecar/core/install.py（Mac 端要新增/改的逻辑）
def install_dws_macos() -> str:
    """下载 dws-darwin-<arch>.tar.gz → 解压到 ~/Library/Application Support/DingTalkBox/bin/dws → chmod +x"""
    arch = "arm64" if platform.machine() == "arm64" else "amd64"
    url = f"https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/latest/download/dws-darwin-{arch}.tar.gz"
    # 下载 + tar xzf + chmod 755
    # ...
    return str(target_path)
```

启动检测顺序（`dws_exe_path()`）：
1. `shutil.which("dws")`（PATH）
2. `~/Library/Application Support/DingTalkBox/bin/dws`（user install）
3. `~/.local/bin/dws`（用户手动）
4. frozen `_MEIPASS/bin/dws` 或 `项目根/bin/dws`（开发 / 内置）

### 2.3 构建产物策略

**每平台独立 spec + 独立产物，不共用 PyInstaller 命令。**

```
dist/                                # 构建目录（.gitignore）
├── sidecar.exe                      # Windows
├── dingtalk_box.exe                 # Windows launcher
├── ai_bridge.exe                    # Windows
├── DingTalkBox.app/                 # macOS launcher
│   └── Contents/
│       ├── Info.plist
│       ├── MacOS/DingTalkBox
│       └── Resources/
├── sidecar-macos                    # macOS sidecar（PyInstaller onefile 单二进制）
└── ai_bridge-macos                  # macOS ai_bridge（PyInstaller onefile 单二进制）

bin/                                 # dev / onefile 用的最小二进制
├── dws.exe                          # Windows（已存在）
├── dws                              # macOS（新）
├── python-sidecar.exe               # Windows（gitignored）
├── python-sidecar-macos             # macOS（gitignored）
└── ...

deliver/
├── dingtalk_box/                    # Windows 包（已存在）
│   ├── dingtalk_box.exe
│   ├── sidecar.exe
│   ├── ai_bridge.exe
│   └── 使用说明.txt
└── dingtalk_box_mac/                # macOS 包（新）
    ├── DingTalkBox.app/
    ├── sidecar-macos
    ├── ai_bridge-macos
    ├── 使用说明.txt（Mac 版，单独写）
    └── README.md（Mac 版）
```

**架构策略：universal2（推荐默认）**

```
universal2 单 .app：lipo 把 arm64 + amd64 合并
- 单文件大小约 ×1.8（90 MB → 165 MB，zip 后 ~80 MB）
- 用户无需选架构
- 项目 macOS 11+ 全部覆盖（arm64 是 Big Sur+ 默认）

备选：amd64 / arm64 各打一份 → 用户选 → 多一份维护成本
```

### 2.4 Gatekeeper 策略（公司内部渠道默认）

**未签名 + ad-hoc 签名 → 用户首次启动右键 → 打开**：

- **未签名**：用户双击弹"无法打开，因为来自身份不明的开发者"
- **ad-hoc 签名**（`codesign --sign - --deep DingTalkBox.app`）：消除部分警告，但仍需首次手动确认
- **Developer ID + 公证**：消除所有警告，但需 Apple Developer 账号（$99/年）

**首版默认**：未签名 + ad-hoc 签名 + 文档说明（"首次启动右键→打开"）。后续看用户量决定是否升级 Developer ID。

### 2.5 跨平台数据迁移策略

**不做迁移**：
- DPAPI key 绑 Windows user，Keychain item 绑 Mac user，跨平台无意义
- Mac 用户首次启动"无 key"，引导他重新配置（已有 UI 流程）
- 日报数据（output/）理论上可移植（纯文件），但 Mac 用户从 0 开始，无历史数据

**Mac 端首次启动额外流程**：
- 检测到 `%APPDATA%` 不存在（Mac 本身就没有）→ 跳过 Windows 数据迁移检查
- 检测 Keychain 不可用 → 告警用户（首次启动会要求输入密码授权 Keychain 写入）

## 3. 构建环境

### 3.1 推荐：GitHub Actions macos-latest runner

| 方案 | 成本 | 调试友好度 | 自动化 |
|---|---|---|---|
| Mac mini 本地 | 一次性硬件 ~5000 RMB | ⭐⭐⭐ | 手动 |
| **GitHub Actions** | $0.08/min，arm64 + amd64 runner 月 1000 次免费 | ⭐⭐（远程调试） | ✅ |
| 云 Mac（MacStadium） | $50+/月 | ⭐ | ✅ |

**默认推荐 GitHub Actions**：成本最低、CI 化、arm64 + amd64 runner 都有。**本地仅需 1 台 Mac 一次性调试用**（启动 dev.py 看一眼 webview 行为，跑一次端到端即可，后续代码改动都通过 CI 验证）。

### 3.2 PyInstaller Mac spec 改动

```python
# launcher-mac.spec（新增）
a = Analysis(
    ['launcher.py'],
    pathex=[str(ROOT)],
    binaries=[
        ('bin/dws', 'bin'),  # Mac 版 dws（不带扩展名）
    ],
    datas=[...],  # 同 Windows
    hiddenimports=[
        # Mac 特有
        'keyring.backends.macOS',
        'keyring.backends.OS_X',
        'PyObjCTools',
        # ... 其他同 Windows
    ],
)

exe = EXE(
    pyz, a.scripts,
    name='DingTalkBox',  # .app bundle 名
    ...
)

app = BUNDLE(
    exe,
    name='DingTalkBox.app',
    icon='assets/logo.icns',  # Mac .icns 格式
    bundle_identifier='com.internal.dingtalkbox',
    info_plist={
        'CFBundleDisplayName': '钉钉AI助手',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': 'True',
        # ...
    },
)
```

**sidecar-mac.spec / ai_bridge-mac.spec**：单二进制，无 .app bundle，类比 Windows 的 build.spec / ai_bridge.spec。

### 3.3 GitHub Actions workflow（示例）

```yaml
# .github/workflows/build-mac.yml
name: macOS Build

on:
  push:
    branches: [master]
    paths:
      - 'sidecar/**'
      - 'ai_bridge/**'
      - 'launcher.py'
      - 'src/**'
      - 'assets/**'
      - 'requirements.txt'
      - '*.spec'
  workflow_dispatch:

jobs:
  build-universal2:
    runs-on: macos-latest  # GitHub 默认 arm64
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt pyinstaller
      # 构建 arm64
      - run: pyinstaller launcher-mac-arm64.spec
      # 切到 Intel runner 构建 amd64
      # ... 或用 lipo 合并（同一 runner 编译两份，分别 ARCHFLAGS=arm64 / x86_64）
      - run: lipo -create -output dist/DingTalkBox dist/DingTalkBox.arm64 dist/DingTalkBox.amd64
      - run: ditto -c -k --sequesterRsrc --keepParent dist/DingTalkBox.app deliver/dingtalk_box_mac/DingTalkBox.app
      - uses: actions/upload-artifact@v4
        with:
          name: DingTalkBox-mac-universal2
          path: deliver/dingtalk_box_mac.zip
```

## 4. 阶段计划（一次性完整发版，但分阶段提交）

| Stage | 范围 | 工时 | 验收 |
|---|---|---|---|
| **S1 抽象层** | DPAPI 接口化 + Mac Keychain 后端 + Windows 回归测试（确保 Windows 不破） | 2-3 天 | Windows v0.3.10 smoke test 全过；Mac dev.py 起得来 |
| **S2 安装流程** | install.py Mac 端实现（dws 下载安装）；macOS 启动种子 .env | 2 天 | Mac dev.py 启动后 dws 自动下载并 spawn 成功 |
| **S3 构建 + CI** | 写 launcher-mac.spec + sidecar-mac.spec + ai_bridge-mac.spec（每个 spec 配 ARCHFLAGS=arm64 和 =x86_64 两次构建）；GitHub Actions 跑通 lipo 合并；产出 universal2 .app + 单文件 sidecar/ai_bridge | 1 周 | CI 自动出 deliver/dingtalk_box_mac/；frozen .app 启动能 ping 通 |
| **S4 端到端 login + daily report** | Mac 端 login (dws auth login 浏览器跳转) → 拉消息 → AI 整理 → 渲染长图全流程跑通 | 1.5 周 | Mac 上能完整生成一份日报，截图对比 Windows 版无差异 |
| **S5 send + 历史 + LLM 配置回归** | send_to_dingtalk、历史日报列表、MD 预览、LLM 配置 + keychain 保存全功能 | 1.5 周 | Mac 上发日报到钉钉自己收到；切模型 + 不动 key 不破坏 |
| **S6 文档 + 内部渠道** | Mac 版 README + 使用说明 + Gatekeeper 绕过说明；内部下载页 | 2-3 天 | 完整文档就绪；CI 出包可下载 |
| **总计** | | **5-7 周** | |

## 5. 风险与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | pywebview Mac 端键盘快捷键 / 文件对话框 / 右键菜单与 Windows 表现不同 | 中 | S4-S5 重点验证；不一致时按 Mac 习惯修（Cmd 替代 Ctrl、NSMenu 等） |
| R2 | Keychain 在 sandboxed / SSH-only 环境不可用 | 低 | 检测后降级到 AES 文件加密（fallback backend），告警用户但不阻塞 |
| R3 | Gatekeeper 拦截未签名 .app | 中 | 文档：右键→打开；可选 ad-hoc 签名消除部分警告 |
| R4 | PyInstaller Mac .app frozen `_MEIPASS` 路径解析 bug | 中 | Mac launcher.py 启动后跑 self-test；参考 `dingtalk-frozen-env-pitfall` 排查模式 |
| R5 | dws-darwin 二进制签名链问题（macOS Catalina+ 严格化） | 低 | dws 是 GitHub Actions 自动发布，签名链基本可信；首次启动仍可能要右键确认 |
| R6 | Windows 用户已有 DPAPI key 无法迁移到 Mac | 低 | 不迁移；Mac 用户首次启动"无 key"，引导重新配 |
| R7 | universal2 体积 ×1.8 | 低 | zip 压缩率高，传输不是瓶颈 |
| R8 | keyring 库在 Mac PyInstaller 打包漏 hiddenimports | 中 | S3 阶段实测；hiddenimports 加 `keyring.backends.macOS`、`PyObjCTools`、`Foundation` |
| R9 | GitHub Actions macos-latest 偶尔不可用 | 低 | workflow_dispatch 手动触发；本地 Mac 兜底 |
| R10 | Apple Silicon 与 Intel dws 二进制混淆（user_dws_path 误装） | 低 | install.py 按 `platform.machine()` 选对 asset；启动前 sanity check |

## 6. 验证清单

### 6.1 S1 验收

- [ ] Windows dev.py 启动 → set_llm_config 正常 → 加密文件被 DPAPI 保护 → restart 后能解出来
- [ ] Mac dev.py 启动（Mac 本地）→ set_llm_config 正常 → Keychain 出现对应条目 → restart 后能解出来
- [ ] Windows frozen exe 跑通 S1 验收（确保 Windows 不破）

### 6.2 S3 验收

- [ ] GitHub Actions 自动出 deliver/dingtalk_box_mac/DingTalkBox.app (universal2)
- [ ] `file DingTalkBox.app/Contents/MacOS/DingTalkBox` 显示 Mach-O universal
- [ ] .app 双击启动能 ping 通 sidecar
- [ ] dws 二进制被自动下载安装到 `~/Library/Application Support/DingTalkBox/bin/dws`

### 6.3 S4-S5 验收

- [ ] Mac 端 login：浏览器跳转 → 用户授权 → dws 缓存 token → 完成登录
- [ ] Mac 端 daily report：拉当天消息 → AI 整理 → 渲染 PNG/MD
- [ ] Mac 端 send：日报通过 dws 发到钉钉 → Mac 端自己收到
- [ ] Mac 端 LLM 配置：保存 → Keychain 出现条目 → restart 后能用
- [ ] Mac 端切换模型 + 不动 key → 不破坏 secret
- [ ] Mac 端 pywebview 快捷键（Cmd+C/V/Q/W）、文件对话框表现正常

### 6.4 S6 验收

- [ ] Mac 版 README.md 就绪
- [ ] Mac 版使用说明.txt 写完
- [ ] 内部下载页 / 文档含 Gatekeeper 绕过说明

## 7. 决策项（建议默认值 + 你的确认）

| # | 决策 | 推荐默认 | 备选 |
|---|---|---|---|
| D1 | 构建机 | **GitHub Actions macos-latest** | Mac mini 本地 / 云 Mac |
| D2 | 架构策略 | **universal2（一份）** | amd64 / arm64 分两份 |
| D3 | Gatekeeper | **未签名 + ad-hoc + 文档说明** | Developer ID + 公证 |
| D4 | Keychain 不可用 fallback | **AES 文件加密（macOS 密钥派生）** | 直接报错让用户重启 |
| D5 | Mac 版本号 | **v1.0.0-mac（独立，不复用 Windows 版本）** | 跟 Windows 同步 v0.3.11 |
| D6 | 公司内部下载页 | **暂不提供（仅出 .zip 包 + 内部 IM 群发）** | 自建静态页 / 内网部署 |

## 8. 范围外（YAGNI）

- Mac App Store 发布（sandbox 限制会破坏 dws 子进程调用）
- 跨平台数据迁移（DPAPI/Keychain 绑定 OS user，无意义）
- Mac Touch Bar 支持（用户没提）
- macOS 12 以下版本（universal2 起 macOS 11，Apple Silicon 起 macOS 11）
- 自动更新（v0.3.x Windows 版都没做，Mac 端不做）

## 9. 关联文档

- `dingtalk-v0.3.10-release.md` — Windows 端当前发版状态（Mac 端以此为基线）
- `dingtalk-frozen-env-pitfall.md` — frozen 模式路径解析坑排查模式（Mac 端会复用）
- `dingtalk-pyinstaller-build.md` — PyInstaller 打包流程（Mac 版对照改）
- `dingtalk-key-encryption.md` — .env key 加密方案（Mac 端 Argon2id + AES-GCM 同样适用）

## 10. 待用户确认

请审核后告知：
1. 上述 D1-D6 决策项默认值是否接受？任何一项要改请指出。
2. 阶段计划 S1-S6 是否合理？是否要调阶段顺序 / 合并 / 拆分？
3. 风险表 R1-R10 是否需要补充？
4. 是否要进入 writing-plans 阶段（生成每阶段的可执行 plan）？

确认后我会调 writing-plans skill，按 S1-S6 出 6 个独立 plan，每个 plan 含任务分解 + 测试用例 + 验收标准。