# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for StressLess on macOS.

Run from the StressLess project directory:
    pyinstaller --clean --noconfirm StressLess.spec
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project_dir = Path(SPECPATH)

# Bundle the application's read-only resources. Do not bundle the writable
# data directory; main.py saves logs in ~/Library/Application Support/StressLess.
asset_names = [
    'chime.mp3', 'pop.mp3',
    'audioon.png', 'audiooff.png',
    'taiyaki-removebg-preview.png', 'taiyaki.png',
    'quit.png', 'trackingoff.png', 'trackingon.png',
    'videoon.png', 'videooff.png',
]
# Optional assets used by some versions of main.py.
optional_names = ['foodlog.png', 'spin.mp3']
missing = [name for name in asset_names if not (project_dir / name).is_file()]
if missing:
    raise FileNotFoundError('Missing required StressLess assets: ' + ', '.join(missing))
added_files = [(str(project_dir / name), '.') for name in asset_names]
added_files += [(str(project_dir / name), '.') for name in optional_names if (project_dir / name).is_file()]

mp_datas, mp_binaries, mp_hiddenimports = collect_all('mediapipe')

a = Analysis(
    [str(project_dir / 'main.py')],
    pathex=[str(project_dir)],
    binaries=mp_binaries,
    datas=added_files + mp_datas,
    hiddenimports=mp_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StressLess',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='StressLess',
)
app = BUNDLE(
    coll,
    name='StressLess.app',
    icon=None,
    bundle_identifier='com.stressless.app',
    info_plist={
        'NSCameraUsageDescription': 'StressLess uses your camera for real-time gesture tracking and habit awareness.',
        'NSHighResolutionCapable': True,
    },
)
