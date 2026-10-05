"""Main window of the desktop app: the wizard by default, the advanced (manual) mode on request."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from .. import __version__
from ..service import Session
from .advanced import AdvancedView
from .icon import app_icon
from .widgets import APP_NAME
from .wizard import WizardView

WIZARD_SIZE = (780, 640)
ADVANCED_SIZE = (980, 940)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(app_icon())
        self.settings = settings if settings is not None else QSettings("tesla-model-extractor", "desktop")
        self.wizard = WizardView(self.settings, self.show_advanced, self.cache_cleared)
        self.advanced = AdvancedView(self.settings, self.show_wizard, self.cache_cleared)
        self.views = QStackedWidget()
        self.views.addWidget(self.wizard)
        self.views.addWidget(self.advanced)
        self.setCentralWidget(self.views)
        self.resize(*WIZARD_SIZE)

    def show_advanced(self, source: str = "", session: Session | None = None) -> None:
        if source and source != self.advanced._source or (session is not None and self.advanced.session is not session):
            self.advanced.adopt(source, session)
        self.views.setCurrentWidget(self.advanced)
        self._grow_to(*ADVANCED_SIZE)

    def show_wizard(self) -> None:
        if self.advanced.task is None:
            self.views.setCurrentWidget(self.wizard)

    def cache_cleared(self) -> None:
        self.wizard.forget_cached_session()
        self.advanced.forget_cached_session()

    def _grow_to(self, w: int, h: int) -> None:
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            w, h = min(w, screen.width()), min(h, screen.height())
        self.resize(max(self.width(), w), max(self.height(), h))

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        self.wizard.shutdown()
        self.advanced.shutdown()
        super().closeEvent(event)


def run(argv: list[str] | None = None, source: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1] if argv is None else argv)
    assert isinstance(app, QApplication)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("tesla-model-extractor")
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    if source and Path(source).exists():
        win.wizard.pick(source)
    return app.exec()
