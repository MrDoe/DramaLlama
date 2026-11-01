# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Drama Llama graphical installer.

The installer executable only needs Qt (for the wizard); the application
payload (``src/``, ``web/dist``, ``vendor/``, the Suno bridge and the installer
package itself) is bundled as data and copied into the install prefix when the
user runs the wizard.

Usage (normally through ``scripts/build_installer.py``):

    DRAMA_LLAMA_PAYLOAD=build/installer/payload \
        python -m PyInstaller installer/packaging/pyinstaller.spec
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
PAYLOAD = Path(
    os.environ.get("DRAMA_LLAMA_PAYLOAD") or (ROOT / "build" / "installer" / "payload")
).resolve()

if not (PAYLOAD / "src" / "drama_studio").is_dir():
    raise SystemExit(
        f"installer payload not staged at {PAYLOAD}; run scripts/build_installer.py"
    )

icon_candidates = [
    ROOT / "build" / "installer" / "icon" / "dramallama.ico",
    ROOT / "installer" / "packaging" / "dramallama.ico",
]
ICON = next((str(path) for path in icon_candidates if path.is_file()), None)

a = Analysis(
    [str(ROOT / "installer" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(PAYLOAD), "payload")],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DramaLlama-Setup",
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
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DramaLlama-Setup",
)
