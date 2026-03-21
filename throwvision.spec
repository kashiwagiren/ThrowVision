# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for ThrowVision server
# Build: pyinstaller throwvision.spec
# Output: dist/server/ (onedir, self-contained)

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)   # project root (same dir as this spec)

# ── Hidden imports needed by Flask-SocketIO (threading mode) ─────────────────
hidden = [
    # SocketIO / EngineIO
    'engineio',
    'engineio.async_drivers',
    'engineio.async_drivers.threading',
    'socketio',
    'flask_socketio',
    # Flask internals
    'flask',
    'flask.templating',
    'jinja2',
    'werkzeug',
    'werkzeug.serving',
    'werkzeug.debug',
    # OpenCV
    'cv2',
    # NumPy
    'numpy',
    'numpy.core',
    # Psutil (system stats)
    'psutil',
    # Standard lib aliases sometimes missed
    'email.mime.text',
    'email.mime.multipart',
]

# ── Data files bundled into the dist ─────────────────────────────────────────
# Format: (source_glob_or_path, dest_dir_inside_dist)
datas = [
    # Frontend web app
    (str(ROOT / 'frontend' / 'index.html'),  'frontend'),
    (str(ROOT / 'frontend' / 'app.js'),      'frontend'),
    (str(ROOT / 'frontend' / 'style.css'),   'frontend'),
]

a = Analysis(
    [str(ROOT / 'server.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy unused packages
        'tkinter', 'matplotlib', 'scipy', 'pandas',
        'IPython', 'notebook', 'PIL', 'PIL.Image',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,       # keep console so Electron can pipe stdout/stderr
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
    upx=True,
    upx_exclude=[],
    name='server',          # output folder: dist/server/
)
