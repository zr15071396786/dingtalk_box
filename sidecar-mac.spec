# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for sidecar (macOS CLI subprocess)

S3 macOS 端口。

与 build.spec（Windows）的差异：
- 不打包 bin/dws（install.py 启动时从 GitHub release 下载；spec binaries 空）
- console=True（CLI 子进程，stdout 走 JSON-RPC）
- icon='assets/logo.icns'
- entitlements_file='assets/entitlements.mac.plist'
- 不需要 .app bundle，产物是裸 Mach-O 二进制 dist/sidecar

Build:
    pyinstaller sidecar-mac.spec --clean --noconfirm --target-arch arm64
    # 产物: dist/sidecar
"""
import sys
from pathlib import Path

ROOT = Path('.').resolve()
block_cipher = None

a = Analysis(
    ['sidecar/main.py'],
    pathex=[str(ROOT)],
    binaries=[],  # Mac 上不打包 dws（启动时下载）
    datas=[
        ('assets/providers.yaml', 'assets'),
        # 复用的 dingtalk_daily skill
        ('external/dingtalk_daily_summary.py', 'external'),
        ('external/dingtalk-daily-export.py', 'external'),
        ('external/dingtalk-daily-render.py', 'external'),
        # ai_bridge 整个包（sidecar 通过它调 LLM）
        ('ai_bridge/__init__.py', 'ai_bridge'),
        ('ai_bridge/dpapi.py', 'ai_bridge'),
        ('ai_bridge/dpapi_common.py', 'ai_bridge'),
        ('ai_bridge/dpapi_windows.py', 'ai_bridge'),
        ('ai_bridge/dpapi_macos.py', 'ai_bridge'),
        ('ai_bridge/providers.py', 'ai_bridge'),
        ('ai_bridge/schema.py', 'ai_bridge'),
        ('ai_bridge/main.py', 'ai_bridge'),
    ],
    hiddenimports=[
        'core.dispatcher',
        'core.dws_runner',
        'core.auth',
        'core.daily_report',
        'core.send',
        'core.paths',
        'core.config',
        'core.logging_setup',
        'core.llm_config',
        'core.errors',
        'core.ai_bridge_runner',
        # v0.3.14：版本号单一真相源
        'version',
        # ai_bridge 整个包
        'ai_bridge',
        'ai_bridge.dpapi',
        'ai_bridge.dpapi_common',
        'ai_bridge.dpapi_windows',
        'ai_bridge.dpapi_macos',
        'ai_bridge.providers',
        'ai_bridge.schema',
        'ai_bridge.main',
        # bundled skill 隐式依赖
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'yaml',
        # httpx
        'httpx',
        'httpx._transports',
        'httpcore',
        'h2',
        # macOS 端口（S1）：Mac 端 DPABI 后端
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
    name='sidecar',
    debug=False,
    strip=False,
    upx=False,
    console=True,  # CLI 子进程，stdout 走 JSON-RPC（保留 stdio）
    icon='assets/logo.icns',
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file='assets/entitlements.mac.plist',
)