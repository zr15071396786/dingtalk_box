# -*- mode: python -*-
"""PyInstaller spec for dingtalk_box sidecar

v0.3.9：sidecar 通过 os.environ 读明文 key（launcher 已解密），
sidecar 自身不需 key_crypto 模块。

Build (项目根目录下):
    pyinstaller build.spec --clean --noconfirm
    cp dist/sidecar.exe bin/python-sidecar.exe

把 dws.exe 一并打入 _MEIPASS/bin/，运行时通过 sys._MEIPASS 找到。
"""
import sys
from pathlib import Path

ROOT = Path('.').resolve()
block_cipher = None

a = Analysis(
    ['sidecar/main.py'],
    pathex=[str(ROOT)],
    binaries=[
        # (源, 目标子目录)
        ('bin/dws.exe', 'bin'),
    ] if (ROOT / 'bin' / 'dws.exe').exists() else [],
    datas=[
        # 把 default config 一起打入
        # ai_bridge 需要 bundled providers.yaml
        ('assets/providers.yaml', 'assets'),
        # 复用的 dingtalk_daily skill
        ('external/dingtalk_daily_summary.py', 'external'),
        ('external/dingtalk-daily-export.py', 'external'),
        ('external/dingtalk-daily-render.py', 'external'),
        # ai_bridge 整个包
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
        # v0.3.14：版本号单一真相源（dispatcher.ping + send.diagnose 用）
        'version',
        # ai_bridge 整个包（sidecar 通过它调 LLM）
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
        # httpx（_test_llm_connection 用）
        'httpx',
        'httpx._transports',
        'httpcore',
        'h2',
        # macOS 端口（S1）：Mac 端 DPAPI 后端用 keyring 保护用户 LLM key
        # Windows 端不会 import（dpapi.py 平台分发），但装上无副作用
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
    name='sidecar',
    debug=False,
    strip=False,
    upx=False,
    console=True,  # 保留 stdio
    icon='assets/logo.ico',  # v0.3.7-hotfix: 替换 PyInstaller 默认图标
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
