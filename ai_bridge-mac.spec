# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ai_bridge (macOS CLI subprocess)

S3 macOS 端口。

与 ai_bridge.spec（Windows）的差异：
- icon='assets/logo.icns'（Mac .icns，不是 .ico）
- entitlements_file='assets/entitlements.mac.plist'（网络客户端 + Keychain 读 .env 之外不需要文件读写）
- console=True（CLI 子进程，stdout 走 JSON-RPC）
- 产物：dist/ai_bridge（裸 Mach-O，被 lipo merge 进 .app/Contents/MacOS/）

Build:
    pyinstaller ai_bridge-mac.spec --clean --noconfirm --target-arch arm64
    # 产物: dist/ai_bridge
"""
import sys
from pathlib import Path

ROOT = Path('.').resolve()
block_cipher = None

a = Analysis(
    ['ai_bridge/main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ('assets/providers.yaml', 'assets'),
        # S1：DPABI 三后端，PyInstaller 静态分析漏
        ('ai_bridge/dpapi.py', 'ai_bridge'),
        ('ai_bridge/dpapi_common.py', 'ai_bridge'),
        ('ai_bridge/dpapi_windows.py', 'ai_bridge'),
        ('ai_bridge/dpapi_macos.py', 'ai_bridge'),
    ],
    hiddenimports=[
        'ai_bridge',
        'ai_bridge.dpapi',
        'ai_bridge.dpapi_common',
        'ai_bridge.dpapi_windows',
        'ai_bridge.dpapi_macos',
        'ai_bridge.providers',
        'ai_bridge.schema',
        'ai_bridge.main',
        # S1：Mac 端用 keyring 保护 LLM key
        'keyring',
        'keyring.backends',
        'keyring.backends.macOS',
        'keyring.backends.OS_X',
        # httpx（DashScope OpenAI 兼容接口）
        'httpx',
        'httpx._api',
        'httpx._client',
        'httpx._auth',
        'httpx._config',
        'httpx._models',
        'httpx._transports',
        'httpx._transports.default',
        'httpx._urlparse',
        'httpx._utils',
        'httpx._content',
        'httpcore',
        'httpcore._sync',
        'httpcore._sync.connection',
        'httpcore._sync.http',
        'httpcore._sync.http11',
        'h2',
        'h2.connection',
        'h2.config',
        'yaml',
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
    name='ai_bridge',
    debug=False,
    strip=False,
    upx=False,
    console=True,  # CLI 子进程，stdout 走 JSON-RPC（保留 stdio）
    icon='assets/logo.icns',
    disable_windowed_traceback=False,
    target_arch=None,                # CLI 覆盖（--target-arch arm64/x86_64）
    codesign_identity=None,          # v0.3.15 不签
    entitlements_file='assets/entitlements.mac.plist',
)