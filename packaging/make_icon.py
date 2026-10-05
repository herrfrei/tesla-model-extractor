"""Render the app icon (tesla_model_extractor/gui/icon.py) into build/icon.png, .ico and .icns for PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from tesla_model_extractor.gui.icon import render  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build")
    out.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])  # noqa: F841 – QImage text/AA rendering wants an application object
    png = out / "icon.png"
    render(1024).save(str(png))
    img = Image.open(png).convert("RGBA")
    img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(out / "icon.icns")
    print(f"wrote {png}, icon.ico, icon.icns to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
