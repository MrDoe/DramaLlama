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

# PySide6 6.5+ loads several xcb helper libraries at runtime via dlopen, so
# PyInstaller does not collect them automatically. Bundle every one that is
# present on the build host so the AppImage runs on fresh systems (Ubuntu,
# RHEL, ...) that ship none of them.
_extra_binaries = []
if os.name == "posix":
    import glob as _glob

    _lib_dirs = (
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib64",
        "/usr/lib",
    )
    _patterns = (
        "libxcb-cursor.so*",
        "libxcb-xkb.so*",
        "libxcb-util.so*",
        "libxcb-image.so*",
        "libxcb-render-util.so*",
        "libxcb-keysyms.so*",
        "libxcb-icccm.so*",
        "libxcb-shape.so*",
        "libxcb-randr.so*",
        "libxcb-xinerama.so*",
        "libxcb-sync.so*",
        "libxcb-xfixes.so*",
        "libxkbcommon.so*",
        "libxkbcommon-x11.so*",
    )
    _seen: set[str] = set()
    for _d in _lib_dirs:
        for _pat in _patterns:
            for _path in _glob.glob(os.path.join(_d, _pat)):
                if os.path.isfile(_path) and _path not in _seen:
                    _seen.add(_path)
                    _extra_binaries.append((_path, "."))

a = Analysis(
    [str(ROOT / "installer" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=_extra_binaries,
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
