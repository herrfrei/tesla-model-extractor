"""Pieces both views of the desktop app use."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..gdre import recovered_cache_root
from ..service import Session
from .worker import Task

APP_NAME = "Tesla Model Extractor"
BUNDLE_SUFFIXES = (".apks", ".apkm", ".xapk", ".apk", ".zip")
HA_UPLOAD = "Settings → Devices & services → Tesla View → Configure → Upload asset pack"
OWNERSHIP = (
    "Use a copy of the Tesla app that you own. This tool never downloads the app. Everything it produces contains "
    "Tesla-owned material: use it for your own car and projects and do not redistribute it."
)


def human(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GiB"
    return f"{n / 1048576:.1f} MiB" if n >= 1048576 else f"{n / 1024:.0f} KiB"


def dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def default_output() -> str:
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return str(Path(docs) / "Tesla models")


def browse_row(edit: QLineEdit, button_text: str = "Browse…") -> tuple[QWidget, QPushButton]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit, 1)
    btn = QPushButton(button_text)
    lay.addWidget(btn)
    return w, btn


def dim(w: QWidget) -> None:
    pal = w.palette()
    pal.setColor(QPalette.ColorRole.WindowText, pal.color(QPalette.ColorRole.PlaceholderText))
    w.setPalette(pal)


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    dim(lbl)
    return lbl


def cache_size() -> int:
    return dir_size(recovered_cache_root())


def uses_cache(session: Session | None) -> bool:
    return bool(session and session.recovered and session.recovered.is_relative_to(recovered_cache_root()))


def clear_recovery_cache(parent: QWidget) -> bool:
    """Ask, then delete every bundle read before. True when the cache was deleted."""
    root = recovered_cache_root()
    size = cache_size()
    if not size:
        return False
    answer = QMessageBox.question(
        parent,
        APP_NAME,
        f"Delete the bundles you analyzed before ({human(size)})?\n\n"
        f"They are kept in {root} so a bundle opens instantly the next time. "
        "After clearing, reading a bundle again takes a few minutes.",
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True


def log_view(placeholder: str = "Progress messages appear here.") -> QPlainTextEdit:
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setMaximumBlockCount(5000)
    view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    view.setPlaceholderText(placeholder)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    return view


def append_log(view: QPlainTextEdit, level: str, msg: str) -> None:
    """Append a line and follow the end, unless the reader scrolled up to look at something."""
    bar = view.verticalScrollBar()
    following = bar.value() >= bar.maximum() - 4
    view.appendPlainText({"warning": "⚠ ", "error": "✗ "}.get(level, "") + msg)
    if following:
        bar.setValue(bar.maximum())


def scroll_to_end(view: QPlainTextEdit) -> None:
    """After a layout change (the view shrank), once Qt has applied it."""
    QTimer.singleShot(0, lambda: view.verticalScrollBar().setValue(view.verticalScrollBar().maximum()))


def heading(text: str, scale: float = 1.6) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setPointSizeF(f.pointSizeF() * scale)
    f.setBold(True)
    lbl.setFont(f)
    return lbl


def stop_task(task: Task | None, settings: QSettings) -> None:
    """Cancel a running task before the window closes."""
    if task is None:
        return
    task.cancel()
    if not task.wait(5000):
        # unpacking a bundle cannot be interrupted; GDRE itself was killed by cancel()
        settings.sync()
        os._exit(0)


DROP_TITLE = "Drop your Tesla app bundle here, or click to choose it"
DROP_SUB = "Tesla_<version>.apks / .apkm / .xapk / .apk"


class DropZone(QFrame):
    def __init__(self, on_path: Any, on_click: Any):
        super().__init__()
        self.on_path = on_path
        self.on_click = on_click
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(96)
        self.setObjectName("dropzone")
        self._style(False)
        lay = QVBoxLayout(self)
        self.title = QLabel(DROP_TITLE)
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub = QLabel(DROP_SUB)
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setWordWrap(True)
        dim(self.sub)
        lay.addStretch(1)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addStretch(1)

    def _style(self, active: bool) -> None:
        border = "palette(highlight)" if active else "palette(mid)"
        self.setStyleSheet(f"#dropzone {{ border: 2px dashed {border}; border-radius: 10px; }}")

    def show_path(self, path: str) -> None:
        if path:
            self.title.setText(Path(path).name)
            self.sub.setText(str(Path(path).parent))
        else:
            self.title.setText(DROP_TITLE)
            self.sub.setText(DROP_SUB)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 – Qt override
        self.on_click()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._style(True)

    def dragLeaveEvent(self, event: Any) -> None:  # noqa: N802
        self._style(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._style(False)
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.on_path(urls[0])
            event.acceptProposedAction()
