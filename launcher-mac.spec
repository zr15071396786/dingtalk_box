# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for dingtalk_box launcher (macOS)

S3 macOS 端口。

与 launcher.spec（Windows）的差异：
- icon='assets/logo.icns'（Mac .icns，不是 .ico）
- entitlements_file='assets/entitlements.mac.plist'（网络/文件/disable-library-validation）
- info_plist：Mac .app 的 Info.plist 字段（CFBundleDisplayName/LSMinimumSystemVersion 等）
- hiddenimports 加 pywebview cocoa 平台 + PyObjC 框架（PyInstaller 静态分析漏）
- bundle_identifier='com.internal.dingtalkbox'（v0.3.15 内部分发）
- 不打包 bin/dws（S2 install.py 首次启动从 GitHub release 下载）

Build:
    pyinstaller launcher-mac.spec --clean --noconfirm
    # 产物: dist/dingtalk_box  (EXE) + dist/dingtalk_box.app  (BUNDLE 包裹)

Build universal2（含子进程 lipo）：见 scripts/build_macos.sh
"""
import sys
from pathlib import Path

ROOT = Path('.').resolve()
block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[str(ROOT)],
    binaries=[
        # Mac 不打包 dws（install.py 启动时从 GitHub release 下载到
        # ~/Library/Application Support/DingTalkBox/bin/dws）。此处保留
        # bin/dws 可选打包路径供开发者本地调试。
        ('bin/dws', 'bin'),
    ] if (ROOT / 'bin' / 'dws').exists() else [],
    datas=[
        ('src/index.html', 'src'),
        ('src/style.css', 'src'),
        ('src/main.js', 'src'),
        ('src/qrcode.js', 'src'),
        ('src/vendor/markdown-it.min.js', 'src/vendor'),
        ('src/vendor/purify.min.js', 'src/vendor'),
        ('assets/logo.icns', 'assets'),
        ('assets/robot-hero.png', 'assets'),
        ('assets/robot-mini.png', 'assets'),
        # 复用的 dingtalk_daily skill
        ('external/dingtalk_daily_summary.py', 'external'),
        ('external/dingtalk-daily-export.py', 'external'),
        ('external/dingtalk-daily-render.py', 'external'),
        # macOS 端口（S1）：DPABI 三后端，PyInstaller 静态分析漏
        ('ai_bridge/dpapi.py', 'ai_bridge'),
        ('ai_bridge/dpapi_common.py', 'ai_bridge'),
        ('ai_bridge/dpapi_windows.py', 'ai_bridge'),
        ('ai_bridge/dpapi_macos.py', 'ai_bridge'),
        # 默认 .env（含 enc:v1: 密文 Qwen key，首次启动种子复制到 APPDATA 等价位置）
        ('.env', '.'),
    ] if (ROOT / '.env').exists() else [
        # 没有 .env 时只打资源
        ('src/index.html', 'src'),
        ('src/style.css', 'src'),
        ('src/main.js', 'src'),
        ('src/qrcode.js', 'src'),
        ('src/vendor/markdown-it.min.js', 'src/vendor'),
        ('src/vendor/purify.min.js', 'src/vendor'),
        ('assets/logo.icns', 'assets'),
        ('assets/robot-hero.png', 'assets'),
        ('assets/robot-mini.png', 'assets'),
        ('external/dingtalk_daily_summary.py', 'external'),
        ('external/dingtalk-daily-export.py', 'external'),
        ('external/dingtalk-daily-render.py', 'external'),
        ('ai_bridge/dpapi.py', 'ai_bridge'),
        ('ai_bridge/dpapi_common.py', 'ai_bridge'),
        ('ai_bridge/dpapi_windows.py', 'ai_bridge'),
        ('ai_bridge/dpapi_macos.py', 'ai_bridge'),
    ],
    hiddenimports=[
        # pywebview Mac cocoa 平台
        'webview',
        'webview.platforms.cocoa',
        # PyObjC（pywebview cocoa 依赖；PyInstaller 静态分析漏）
        'objc',
        'pyobjc_framework_Cocoa',
        'pyobjc_framework_WebKit',
        'pyobjc_framework_AppKit',
        'pyobjc_framework_Foundation',
        # core/*
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
        # v0.3.14：版本号单一真相源
        'version',
        # macOS 端口（S1）：Mac 端用 Keychain 保护 LLM key
        'keyring',
        'keyring.backends',
        'keyring.backends.macOS',
        'keyring.backends.OS_X',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'PySide2', 'PySide6', 'gtk', 'wx'],
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
    console=False,  # Mac .app 不显示终端
    icon='assets/logo.icns',
    disable_windowed_traceback=False,
    target_arch=None,                # CLI 覆盖（--target-arch arm64/x86_64）
    codesign_identity=None,          # v0.3.15 不签；S6 接 Developer ID
    entitlements_file='assets/entitlements.mac.plist',
    bundle_identifier='com.internal.dingtalkbox',
    info_plist={
        'CFBundleDisplayName': '钉钉AI助手',
        'CFBundleName': 'dingtalk_box',
        'CFBundleShortVersionString': '0.3.15',
        'CFBundleVersion': '0.3.15',
        # 2026-06 修复"已损坏"：PyInstaller BUNDLE 经常漏这两个字段
        # CFBundleSupportedPlatforms 缺 → macOS 15 启动报"damaged"
        'CFBundleSupportedPlatforms': ['MacOSX'],
        'CFBundlePackageType': 'APPL',
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'NSRequiresAquaSystemAppearance': 'False',
        'LSMinimumSystemVersion': '11.0',
        'NSAppleEventsUsageDescription': '钉钉AI助手需要调用系统事件以完成自动化操作。',
        'NSLocalNetworkUsageDescription': '钉钉AI助手通过本地网络与 sidecar 子进程通信。',
        'LSApplicationCategoryType': 'public.app-category.productivity',
    },
)

# 2026-06：补 BUNDLE() 把 EXE 包成 .app（PyInstaller 不会从 EXE 自动生成 .app）
app = BUNDLE(
    exe,
    name='dingtalk_box.app',
    icon='assets/logo.icns',
    bundle_identifier='com.internal.dingtalkbox',
)