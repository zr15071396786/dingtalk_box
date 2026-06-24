# -*- mode: python -*-
"""PyInstaller spec for ai_bridge

v0.3.9：ai_bridge 通过 os.environ 读明文 key（launcher 已解密），
ai_bridge 自身不需 key_crypto 模块。

Build (项目根目录下):
    pyinstaller ai_bridge.spec --clean --noconfirm
    产物: dist/ai_bridge.exe
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
        # macOS 端口（S1）：DPAPI 拆 3 个后端文件，PyInstaller 静态分析可能漏
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
        # macOS 端口（S1）：Mac 端用 keyring 保护 LLM key
        'keyring',
        'keyring.backends',
        'keyring.backends.macOS',
        'keyring.backends.OS_X',
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
    excludes=['tkinter', 'unittest'],
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
    console=True,
    icon='assets/logo.ico',  # v0.3.7-hotfix: 替换 PyInstaller 默认图标
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
