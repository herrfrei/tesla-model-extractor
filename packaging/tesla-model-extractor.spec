# PyInstaller spec for the desktop app. Build from the repository root:
#   python packaging/make_icon.py build && pyinstaller --noconfirm packaging/tesla-model-extractor.spec
# Output in dist/: Linux `tesla-model-extractor`, Windows `tesla-model-extractor.exe` (both one file),
# macOS `Tesla Model Extractor.app` (PyInstaller does not support one-file .app bundles).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from tesla_model_extractor import __version__  # noqa: E402

NAME = "tesla-model-extractor"
APP_NAME = "Tesla Model Extractor"
ICON_DIR = ROOT / "build"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
icon = ICON_DIR / ("icon.icns" if IS_MAC else "icon.ico")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=collect_data_files("tesla_model_extractor"),  # rules/*.yaml
    hiddenimports=[],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=NAME,
        console=False,
        upx=False,
        icon=str(icon) if icon.exists() else None,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="io.github.koenhendriks.tesla-model-extractor",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=NAME,
        # Linux keeps a console so the CLI pass-through prints; Windows gets a GUI-subsystem exe (no console window)
        console=not IS_WIN,
        upx=False,  # UPX-packed executables trip antivirus heuristics
        icon=str(icon) if IS_WIN and icon.exists() else None,
    )
