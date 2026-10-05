"""The default view of the desktop app: a step-by-step wizard that makes a Home Assistant asset pack.

Choose bundle → analyze (starts by itself) → tick vehicles → choose a folder → export. Everything else is fixed to
what most people want (one zip, the wheels of each vehicle's family, no size limit); the advanced mode has the rest.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..service import Session, VehicleRow
from ..validate import DEFAULT_MAX_MIB
from .form import PACK, FormState
from .widgets import (
    APP_NAME,
    BUNDLE_SUFFIXES,
    HA_UPLOAD,
    OWNERSHIP,
    DropZone,
    append_log,
    browse_row,
    cache_size,
    clear_recovery_cache,
    default_output,
    heading,
    hint,
    human,
    log_view,
    scroll_to_end,
    stop_task,
    uses_cache,
)
from .worker import AnalyzeTask, ExportTask, Task

PICK, ANALYZE, VEHICLES, SAVE, EXPORT = range(5)
STEPS = ["Bundle", "Analyze", "Vehicles", "Save", "Done"]


def _buttons(*widgets: QWidget | None) -> QHBoxLayout:
    """A bottom button row; `None` marks where the stretch goes."""
    row = QHBoxLayout()
    for w in widgets:
        if w is None:
            row.addStretch(1)
        else:
            row.addWidget(w)
    return row


class WizardView(QWidget):
    def __init__(
        self,
        settings: QSettings,
        on_advanced: Callable[[str, Session | None], None],
        on_cache_cleared: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.settings = settings
        self.on_advanced = on_advanced
        self.on_cache_cleared = on_cache_cleared
        self.session: Session | None = None
        self.source = ""
        self.task: Task | None = None
        self.output: Path | None = None
        self._checked: set[str] = set()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.addWidget(heading(APP_NAME))
        lay.addWidget(
            hint("Makes a Home Assistant asset pack for the Tesla View card from your own copy of the Tesla app.")
        )
        self.steps = QLabel()
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.steps)
        self.pages = QStackedWidget()
        for build in (self._pick_page, self._analyze_page, self._vehicles_page, self._save_page, self._export_page):
            self.pages.addWidget(build())
        lay.addWidget(self.pages, 1)
        self.go(PICK)

    # ---------- pages ----------
    def _pick_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(heading("Choose your Tesla app bundle", 1.2))
        self.drop = DropZone(self.pick, self.choose_file)
        self.drop.setMinimumHeight(200)
        lay.addWidget(self.drop, 1)
        self.pick_error = QLabel()
        self.pick_error.setWordWrap(True)
        self.pick_error.setStyleSheet("color: #e5534b;")
        self.pick_error.setVisible(False)
        lay.addWidget(self.pick_error)
        lay.addWidget(
            hint(
                "Get the bundle (Tesla_<version>.apks / .apkm / .xapk / .apk) from a device you own, for example with "
                "an APK exporter app. Analyzing starts as soon as you drop it."
            )
        )
        lay.addWidget(hint(OWNERSHIP))
        choose = QPushButton("Choose file…")
        choose.clicked.connect(self.choose_file)
        self.advanced_btn = QPushButton("Go to advanced manual mode")
        self.advanced_btn.setToolTip("Every option: GLB files for Blender / Unreal, paints, wheels, separate packs …")
        self.advanced_btn.clicked.connect(lambda: self.on_advanced(self.source, self.session))
        self.clear_btn = QPushButton()
        self.clear_btn.setToolTip(
            "Delete the bundles you analyzed before. They are kept so a bundle opens instantly the next time."
        )
        self.clear_btn.clicked.connect(self.clear_cache)
        lay.addLayout(_buttons(self.advanced_btn, self.clear_btn, None, choose))
        return w

    def _analyze_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.analyze_title = heading("Reading the bundle…", 1.2)
        lay.addWidget(self.analyze_title)
        lay.addWidget(
            hint(
                "The first time a bundle is read takes a few minutes and needs about 2 GB of free disk space. "
                "The result is kept, so the same bundle opens instantly next time."
            )
        )
        self.analyze_bar = QProgressBar()
        self.analyze_bar.setRange(0, 0)
        lay.addWidget(self.analyze_bar)
        self.analyze_status = QLabel()
        self.analyze_status.setWordWrap(True)
        lay.addWidget(self.analyze_status)
        self.analyze_log = log_view()
        lay.addWidget(self.analyze_log, 1)
        back = QPushButton("Back")
        back.setToolTip("Stop reading this bundle and choose another one")
        back.clicked.connect(self.cancel_analyze)
        lay.addLayout(_buttons(back, None))
        return w

    def _vehicles_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(heading("Which cars do you want?", 1.2))
        self.vehicles_info = hint("")
        lay.addWidget(self.vehicles_info)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Vehicle", "Size", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.cellClicked.connect(self._toggle_row)
        lay.addWidget(self.table, 1)
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.go(PICK))
        self.select_all = QPushButton("Select all")
        self.select_all.clicked.connect(self._check_all)
        self.count = QLabel()
        self.next_btn = QPushButton("Next")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(lambda: self.go(SAVE))
        lay.addLayout(_buttons(back, self.select_all, None, self.count, self.next_btn))
        return w

    def _save_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(heading("Where should the export go?", 1.2))
        self.output_edit = QLineEdit(str(self.settings.value("wizard/output_dir", "") or default_output()))
        row, browse = browse_row(self.output_edit)
        browse.clicked.connect(self.choose_output)
        lay.addWidget(row)
        self.save_summary = QLabel()
        self.save_summary.setWordWrap(True)
        self.save_summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.save_summary)
        lay.addWidget(hint("Need GLB files for Blender or Unreal, or other options? Use the advanced manual mode."))
        lay.addStretch(1)
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.go(VEHICLES))
        self.export_btn = QPushButton("Export")
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self.export)
        lay.addLayout(_buttons(back, None, self.export_btn))
        return w

    def _export_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.export_title = heading("Exporting…", 1.2)
        lay.addWidget(self.export_title)
        self.export_bar = QProgressBar()
        self.export_bar.setRange(0, 0)
        lay.addWidget(self.export_bar)
        self.export_status = QLabel()
        self.export_status.setWordWrap(True)
        lay.addWidget(self.export_status)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self.open_output)
        self.results.setMaximumHeight(110)
        self.results.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVisible(False)
        lay.addWidget(self.results)
        self.export_log = log_view()
        lay.addWidget(self.export_log, 1)
        self.export_hint = QLabel()
        self.export_hint.setWordWrap(True)
        self.export_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.export_hint)
        self.done_back = QPushButton("Back")
        self.done_back.clicked.connect(lambda: self.go(SAVE))
        self.restart = QPushButton("Start over")
        self.restart.clicked.connect(self.start_over)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(self.open_output)
        lay.addLayout(_buttons(self.done_back, self.restart, None, self.open_btn))
        return w

    # ---------- navigation ----------
    def go(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        parts = []
        for i, name in enumerate(STEPS):
            text = f"{i + 1}. {name}"
            parts.append(f"<b>{text}</b>" if i == page else f"<span style='color: gray'>{text}</span>")
        self.steps.setText(" &nbsp;›&nbsp; ".join(parts))
        if page == SAVE:
            self._fill_summary()
        elif page == PICK:
            self._refresh_cache()

    def current(self) -> int:
        return self.pages.currentIndex()

    def start_over(self) -> None:
        self.session = None
        self.source = ""
        self.drop.show_path("")
        self.go(PICK)

    def _refresh_cache(self) -> None:
        size = cache_size()
        self.clear_btn.setText(f"Clear cache ({human(size)})")
        self.clear_btn.setVisible(size > 0)

    def clear_cache(self) -> None:
        if self.task is None and clear_recovery_cache(self):
            if self.on_cache_cleared is not None:
                self.on_cache_cleared()
            else:
                self.forget_cached_session()

    def forget_cached_session(self) -> None:
        """Drop the session when its recovered project was in the cache that just got deleted."""
        if uses_cache(self.session):
            self.session = None
        self._refresh_cache()

    # ---------- step 1 + 2: choose and analyze ----------
    def choose_file(self) -> None:
        start = str(Path(self.source).parent) if self.source else str(self.settings.value("last_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the Tesla app bundle", start, "Tesla app bundles (*.apks *.apkm *.xapk *.apk);;All files (*)"
        )
        if path:
            self.pick(path)

    def pick(self, path: str) -> None:
        if self.task is not None:
            return
        p = Path(path)
        if p.is_file() and p.suffix.lower() not in BUNDLE_SUFFIXES:
            self._pick_failed(f"{p.name} is not a Tesla app bundle (.apks, .apkm, .xapk or .apk).")
            return
        self.pick_error.setVisible(False)
        self.settings.setValue("last_dir", str(p.parent))
        if self.session is not None and path == self.source:
            self.go(VEHICLES)
            return
        self.source = path
        self.session = None
        self._checked = set()
        self.drop.show_path(path)
        self.analyze_title.setText(f"Reading {p.name}…")
        self.analyze_status.setText("Starting…")
        self.analyze_log.clear()
        append_log(self.analyze_log, "info", f"reading {path}")
        self.analyze_bar.setRange(0, 0)
        self.go(ANALYZE)
        self._start(AnalyzeTask(self._state(), self), self._analyzed, self._analyze_failed)

    def cancel_analyze(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.analyze_status.setText("Stopping…")
        else:
            self.go(PICK)

    def _analyzed(self, session: Session) -> None:
        if session.is_pack:
            self._pick_failed(
                "This is an asset pack that was already made. To turn it into GLB files, use the advanced manual mode."
            )
            return
        self.session = session
        rows = sorted(session.vehicles(), key=lambda v: not v.present)
        self._fill_table(rows)
        present = sum(1 for r in rows if r.present)
        version = f" (app version {session.app_version})" if session.app_version else ""
        self.vehicles_info.setText(
            f"{present} cars found in {Path(self.source).name}{version}. Tick the ones you want."
        )
        self.go(VEHICLES)

    def _analyze_failed(self, message: str) -> None:
        self.session = None
        if message == "Cancelled.":
            self.go(PICK)
        else:
            self._pick_failed(f"Could not read this bundle: {message}")

    def _pick_failed(self, message: str) -> None:
        self.pick_error.setText(message)
        self.pick_error.setVisible(True)
        self.go(PICK)

    # ---------- step 3: vehicles ----------
    def _fill_table(self, rows: list[VehicleRow]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, v in enumerate(rows):
            name = QTableWidgetItem(v.name)
            name.setData(Qt.ItemDataRole.UserRole, v.id)
            flags = Qt.ItemFlag.ItemIsUserCheckable
            if v.present:
                flags |= Qt.ItemFlag.ItemIsEnabled
            name.setFlags(flags)
            name.setCheckState(Qt.CheckState.Checked if v.id in self._checked else Qt.CheckState.Unchecked)
            size = QTableWidgetItem(f"{v.size_bytes / 1e6:.1f} MB" if v.size_bytes else "-")
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            status = QTableWidgetItem("ready" if v.present else "not in this bundle")
            for item in (size, status):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled if v.present else Qt.ItemFlag.NoItemFlags)
            for c, item in enumerate((name, size, status)):
                self.table.setItem(r, c, item)
            self.table.setRowHeight(r, 32)
        self.table.blockSignals(False)
        self._selection_changed()

    def checked_models(self) -> list[str]:
        out = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def _toggle_row(self, row: int, col: int) -> None:
        item = self.table.item(row, 0)
        if col == 0 or item is None or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        on = item.checkState() != Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _enabled_items(self) -> list[QTableWidgetItem]:
        items = (self.table.item(r, 0) for r in range(self.table.rowCount()))
        return [i for i in items if i is not None and i.flags() & Qt.ItemFlag.ItemIsEnabled]

    def _check_all(self) -> None:
        enabled = self._enabled_items()
        on = not all(i.checkState() == Qt.CheckState.Checked for i in enabled)
        self.table.blockSignals(True)
        for item in enabled:
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_: Any) -> None:
        models = self.checked_models()
        self._checked = set(models)
        self.count.setText(f"{len(models)} selected" if models else "Tick at least one car")
        self.next_btn.setEnabled(bool(models))
        enabled = self._enabled_items()
        all_on = bool(enabled) and all(i.checkState() == Qt.CheckState.Checked for i in enabled)
        self.select_all.setText("Select none" if all_on else "Select all")

    # ---------- step 4 + 5: save and export ----------
    def choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save the asset pack in", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _names(self) -> list[str]:
        ids = set(self.checked_models())
        return [v.name for v in (self.session.vehicles() if self.session else []) if v.id in ids]

    def _fill_summary(self) -> None:
        names = self._names()
        cars = ", ".join(names) if len(names) <= 4 else f"{', '.join(names[:3])} and {len(names) - 3} more"
        self.save_summary.setText(
            f"<p>You get <b>one Home Assistant asset pack</b> (a .zip file) with: {cars}.</p>"
            "<p>It includes the wheels of each car's family. Home Assistant accepts up to 100 MiB per upload, so "
            "if the cars together would be larger, they are spread over as few zips as needed. Upload the zip(s) "
            f"in Home Assistant under <i>{HA_UPLOAD}</i>.</p>"
        )

    def _state(self) -> FormState:
        """Wizard choices on top of the advanced mode's saved settings (GDRE location, extra rules …)."""
        base = FormState.from_json(str(self.settings.value("form", "") or ""))
        return FormState(
            source=self.source,
            output_kind=PACK,
            output_dir=self.output_edit.text().strip(),
            models=self.checked_models(),
            pack_wheels="family",
            one_zip=True,
            split=True,
            as_dir=False,
            max_mib=DEFAULT_MAX_MIB,
            gdre_path=base.gdre_path,
            no_download=base.no_download,
            rules_dir=base.rules_dir,
            verbose=base.verbose,
        )

    def export(self) -> None:
        if self.session is None or self.task is not None:
            return
        state = self._state()
        if not state.output_dir:
            self.choose_output()
            state = self._state()
            if not state.output_dir:
                return
        self.settings.setValue("wizard/output_dir", state.output_dir)
        self.output = Path(state.output_dir)
        self.export_title.setText("Exporting…")
        self.export_status.setText("Building the asset pack. This takes a moment.")
        self.export_hint.clear()
        self.results.clear()
        self.results.setVisible(False)
        self.export_log.clear()
        append_log(self.export_log, "info", f"exporting {', '.join(state.models)} to {state.output_dir}")
        self.export_bar.setVisible(True)
        for b in (self.done_back, self.restart, self.open_btn):
            b.setEnabled(False)
        self.go(EXPORT)
        self._start(ExportTask(state, self.session, self), self._exported, self._export_failed)

    def _exported(self, rows: list[dict[str, Any]]) -> None:
        self.export_title.setText("Done")
        n = len(rows)
        self.export_status.setText(f"{n} zip{'s' if n != 1 else ''} saved in {self.output}")
        self.results.setVisible(bool(rows))
        for r in rows:
            item = QListWidgetItem(f"{Path(r['path']).name}   ({human(r['bytes'])})")
            item.setToolTip(str(r["path"]))
            self.results.addItem(item)
        text = (
            f"Upload the zip in Home Assistant: {HA_UPLOAD}."
            if n == 1
            else f"The cars did not fit in one 100 MiB upload, so there are {n} zips. Upload each of them in Home "
            f"Assistant: {HA_UPLOAD}."
        )
        self.export_hint.setText(text)
        scroll_to_end(self.export_log)

    def _export_failed(self, message: str) -> None:
        self.export_title.setText("The export failed")
        self.export_status.setText(message)
        append_log(self.export_log, "error", message)
        scroll_to_end(self.export_log)

    def open_output(self, *_: Any) -> None:
        if self.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output)))

    # ---------- tasks ----------
    def _start(self, task: Task, ok: Callable[[Any], None], failed: Callable[[str], None]) -> None:
        self.task = task
        self.advanced_btn.setEnabled(False)
        task.log.connect(self._log)
        task.progress.connect(self._progress)
        task.succeeded.connect(ok)
        task.failed.connect(lambda message, _details: failed(message))
        task.finished.connect(self._task_done)
        task.start()

    def _task_done(self) -> None:
        self.task = None
        self.advanced_btn.setEnabled(True)
        self.export_bar.setVisible(False)
        for b in (self.done_back, self.restart, self.open_btn):
            b.setEnabled(True)
        self.open_btn.setEnabled(self.results.count() > 0)

    def _log(self, level: str, msg: str) -> None:
        if self.current() == ANALYZE:
            if level == "info":
                self.analyze_status.setText(msg)
            append_log(self.analyze_log, level, msg)
        elif self.current() == EXPORT:
            append_log(self.export_log, level, msg)

    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.analyze_bar.setRange(0, 1000)
            self.analyze_bar.setValue(int(done * 1000 / total))
            self.analyze_status.setText(f"Downloading GDRE Tools (needed once)… {human(done)} of {human(total)}")
        else:
            self.analyze_bar.setRange(0, 0)

    def shutdown(self) -> None:
        stop_task(self.task, self.settings)
