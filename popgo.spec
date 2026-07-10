# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Acer PopGo Companion (Windows / Linux / macOS)."""
import sys
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

block_cipher = None

datas = []
binaries = []
hiddenimports = [
    "hid",
    "customtkinter",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "pystray",
    "pystray._base",
    "ble_battery",
    "mouse_device",
]

# Bundle package data (themes, assets)
for pkg in ("customtkinter", "pystray"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

try:
    binaries += collect_dynamic_libs("hidapi")
except Exception:
    pass

# Platform-specific tray backends + Windows BLE (winsdk)
if sys.platform == "win32":
    hiddenimports += [
        "pystray._win32",
        "winreg",
        "winsdk",
        "winsdk.windows.devices.bluetooth",
        "winsdk.windows.devices.bluetooth.genericattributeprofile",
        "winsdk.windows.storage.streams",
    ]
    try:
        d, b, h = collect_all("winsdk")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
elif sys.platform == "darwin":
    hiddenimports += ["pystray._darwin"]
else:
    hiddenimports += ["pystray._xorg", "pystray._appindicator"]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="AcerPopGoCompanion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS .app bundle (also produced when running on Darwin)
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="AcerPopGoCompanion.app",
        icon=None,
        bundle_identifier="com.ksanjeev284.acerpopgocompanion",
        info_plist={
            "CFBundleName": "Acer PopGo Companion",
            "CFBundleDisplayName": "Acer PopGo Companion",
            "CFBundleShortVersionString": "1.4.0",
            "CFBundleVersion": "1.4.0",
            "NSHighResolutionCapable": True,
            "LSHumanReadableCopyright": "MIT License",
        },
    )
