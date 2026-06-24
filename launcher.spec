# -*- mode: python -*-
"""PyInstaller spec for dingtalk_box launcher (Tauri 替代)

v0.3.9：等级 2 安全（PyInstaller + Argon2id + AES-256-GCM）
- .env 内置 Qwen key 改为 enc:v1: 密文
- launcher 启动时调用 key_crypto 解密注入 os.environ
- 等级 3（Nuitka AOT 编译）计划在 v0.3.10+ 升级

Build:
    pyinstaller launcher.spec --clean --noconfirm
    # 产物: dist/dingtalk_box.exe
"""
import sys
from pathlib import Path

ROOT = Path('.').resolve()
block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[str(ROOT)],
    binaries=[
        # 内嵌 dws.exe（可选；若 PATH 上已有可省）
        ('bin/dws.exe', 'bin'),
    ] if (ROOT / 'bin' / 'dws.exe').exists() else [],
    datas=[
        ('src/index.html', 'src'),
        ('src/style.css', 'src'),
        ('src/main.js', 'src'),
        ('src/qrcode.js', 'src'),
        ('src/vendor/markdown-it.min.js', 'src/vendor'),
        ('src/vendor/purify.min.js', 'src/vendor'),
        ('assets/logo.ico', 'assets'),
        ('assets/robot-hero.png', 'assets'),
        ('assets/robot-mini.png', 'assets'),
        # 复用的 dingtalk_daily skill
        ('external/dingtalk_daily_summary.py', 'external'),
        ('external/dingtalk-daily-export.py', 'external'),
        ('external/dingtalk-daily-render.py', 'external'),
        # macOS 端口（S1）：DPAPI 拆 3 个后端文件，PyInstaller 静态分析可能漏
        ('ai_bridge/dpapi.py', 'ai_bridge'),
        ('ai_bridge/dpapi_common.py', 'ai_bridge'),
        ('ai_bridge/dpapi_windows.py', 'ai_bridge'),
        ('ai_bridge/dpapi_macos.py', 'ai_bridge'),
    ],
    hiddenimports=[
        'webview',
        'core.dispatcher',
        'core.dws_runner',
        'core.auth',
        'core.daily_report',
        'core.send',
        'core.paths',
        'core.config',
        'core.logging_setup',
        # v0.3.9：内置 key 加解密（Argon2id + AES-256-GCM）
        'key_crypto',
        'argon2.low_level',
        'argon2._ffi',
        'cryptography.hazmat.primitives.ciphers.aead',
        # v0.3.14：版本号单一真相源（launcher 启动时 evaluate_js 注入 DOM）
        'version',
        # macOS 端口（S1）：Mac 端用 keyring 保护 LLM key
        'keyring',
        'keyring.backends',
        'keyring.backends.macOS',
        'keyring.backends.OS_X',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest'],
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='dingtalk_box',
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI 模式不弹黑窗
    icon='assets/logo.ico',  # v0.3.7-hotfix: 替换 PyInstaller 默认图标
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
