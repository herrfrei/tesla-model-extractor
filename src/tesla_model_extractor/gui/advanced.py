"""Advanced (manual) mode of the desktop app: every option of both exports on one page."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import GDRE_VERSION
from ..gdre import recovered_cache_root
from ..service import Session, VehicleRow
from .form import GLB, PACK, FormState
from .widgets import (
    APP_NAME,
    BUNDLE_SUFFIXES,
    HA_UPLOAD,
    OWNERSHIP,
    DropZone,
    append_log,
    browse_row,
    clear_recovery_cache,
    default_output,
    dir_size,
    heading,
    hint,
    human,
    log_view,
    stop_task,
    uses_cache,
)
from .worker import AnalyzeTask, ExportTask, Task

COLS = ["Vehicle", "id", "API model", "Wheels", "Size", "Status"]


class AdvancedView(QWidget):
    """Every option on one page (the manual mode)."""

    def __init__(
        self,
        settings: QSettings,
        on_simple: Callable[[], None] | None = None,
        on_cache_cleared: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.settings = settings
        self.on_cache_cleared = on_cache_cleared
        self.session: Session | None = None
        self.rows: list[VehicleRow] = []
        self.task: Task | None = None
        self._last_output: Path | None = None
        self._source = ""
        self._pending: FormState | None = None  # saved paint / wheel / brakes, applied once a session has the lists
        state = FormState.from_json(str(self.settings.value("form", "") or ""))
        if not state.output_dir:
            state.output_dir = default_output()

        content = QWidget()
        page = QVBoxLayout(content)
        page.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(heading(APP_NAME))
        top.addStretch(1)
        self.simple_btn = QPushButton("Back to the simple wizard")
        self.simple_btn.setVisible(on_simple is not None)
        if on_simple is not None:
            self.simple_btn.clicked.connect(on_simple)
        top.addWidget(self.simple_btn)
        page.addLayout(top)
        page.addWidget(
            hint(
                "Turns the 3D cars inside your copy of the Tesla app into GLB files (Blender, Unreal, three.js, …) or "
                "an asset pack for the Tesla View Home Assistant card."
            )
        )
        page.addWidget(self._source_box())
        page.addWidget(self._vehicle_box())
        page.addWidget(self._output_box())
        page.addWidget(self._advanced_box())
        page.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(scroll)
        splitter.addWidget(self._run_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setChildrenCollapsible(False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)
        splitter.setSizes([780, 160])

        self.apply_state(state)
        self._set_analyzed(False)
        self._refresh_command()

    # ---------- sections ----------
    def _source_box(self) -> QGroupBox:
        box = QGroupBox("1. Tesla app bundle")
        lay = QVBoxLayout(box)
        self.drop = DropZone(self.set_source, self.choose_source)
        lay.addWidget(self.drop)
        row = QHBoxLayout()
        self.folder_btn = QPushButton("Use a recovered project folder…")
        self.folder_btn.setToolTip("A folder kept with --keep-recovered, or an extracted assets/godot folder")
        self.folder_btn.clicked.connect(self.choose_folder)
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.setDefault(True)
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.clicked.connect(self.analyze)
        row.addWidget(self.analyze_btn)
        lay.addLayout(row)
        self.source_info = hint(
            "Get the bundle from a device you own, for example with an APK exporter app or adb. The first analysis "
            "of a bundle takes a few minutes and needs about 2 GB of free disk space; later runs reuse it."
        )
        lay.addWidget(self.source_info)
        lay.addWidget(hint(OWNERSHIP))
        return box

    def _vehicle_box(self) -> QGroupBox:
        box = QGroupBox("2. Vehicles")
        self.vehicle_box = box
        lay = QVBoxLayout(box)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(220)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.itemChanged.connect(self._vehicles_changed)
        self.table.cellClicked.connect(self._toggle_row)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        all_btn.clicked.connect(lambda: self._check_all(True))
        none_btn = QPushButton("Select none")
        none_btn.clicked.connect(lambda: self._check_all(False))
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch(1)
        self.selection_info = QLabel("")
        row.addWidget(self.selection_info)
        lay.addLayout(row)
        return box

    def _output_box(self) -> QGroupBox:
        box = QGroupBox("3. Output")
        self.output_box = box
        lay = QVBoxLayout(box)
        self.kind_glb = QRadioButton("GLB files: Blender, Unreal Engine, Unity, three.js, any glTF viewer")
        self.kind_pack = QRadioButton("Home Assistant asset pack (for the Tesla View card)")
        self.kind_group = QButtonGroup(box)
        self.kind_group.addButton(self.kind_glb)
        self.kind_group.addButton(self.kind_pack)
        lay.addWidget(self.kind_glb)
        lay.addWidget(self.kind_pack)
        self.kind_glb.toggled.connect(self._kind_changed)

        self.stack = QStackedWidget()
        self.pages = [self._glb_page(), self._pack_page()]
        for page in self.pages:
            self.stack.addWidget(page)
        lay.addWidget(self.stack)

        form = QFormLayout()
        self.output_edit = QLineEdit()
        out_row, out_btn = browse_row(self.output_edit)
        out_btn.clicked.connect(self.choose_output)
        self.output_edit.textChanged.connect(self._refresh_command)
        form.addRow("Save to folder", out_row)
        lay.addLayout(form)

        run = QHBoxLayout()
        run.addStretch(1)
        self.export_btn = QPushButton("Export")
        ef = self.export_btn.font()
        ef.setBold(True)
        self.export_btn.setFont(ef)
        self.export_btn.setMinimumWidth(160)
        self.export_btn.clicked.connect(self.export)
        run.addWidget(self.export_btn)
        lay.addLayout(run)
        return box

    def _glb_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(20, 6, 0, 6)
        self.paint = QComboBox()
        self.paint.setToolTip("--paint: colour from the app's paint table")
        form.addRow("Paint", self.paint)

        variants = QWidget()
        vl = QHBoxLayout(variants)
        vl.setContentsMargins(0, 0, 0, 0)
        self.v_perf = QCheckBox("Performance")
        self.v_rhd = QCheckBox("Right-hand drive")
        self.v_seats = QCheckBox("7 seats")
        self.plate = QComboBox()
        self.plate.addItem("EU plate", "eu")
        self.plate.addItem("US plate", "us")
        for x in (self.v_perf, self.v_rhd, self.v_seats, self.plate):
            vl.addWidget(x)
        vl.addStretch(1)
        variants.setToolTip("--variant: which look gets baked into the GLB (where the vehicle has it)")
        form.addRow("Look", variants)

        self.wheel = QComboBox()
        self.wheel.setToolTip("--wheels: wheel placed under the wheel pivots")
        form.addRow("Wheels", self.wheel)
        self.brakes = QComboBox()
        self.brakes.setToolTip("--brakes: brake caliper set")
        form.addRow("Brakes", self.brakes)

        extras = QWidget()
        el = QVBoxLayout(extras)
        el.setContentsMargins(0, 0, 0, 0)
        self.separate_wheels = QCheckBox("Also write every wheel of the family as its own GLB")
        self.cables = QCheckBox("Also write the charge cables as GLBs")
        self.keep_all = QCheckBox("Keep every part (variants are flagged, not removed)")
        self.keep_normal_y = QCheckBox("Keep the normal maps' green channel (do not flip)")
        for x in (self.separate_wheels, self.cables, self.keep_all, self.keep_normal_y):
            el.addWidget(x)
        form.addRow("Extras", extras)

        self.yaw = QDoubleSpinBox()
        self.yaw.setRange(-360, 360)
        self.yaw.setSuffix("°")
        self.yaw.setDecimals(1)
        self.yaw.setToolTip("--yaw: extra rotation about the up axis")
        self.yaw.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        form.addRow("Rotate", self.yaw)

        for x in (self.v_perf, self.v_rhd, self.v_seats, self.separate_wheels, self.cables, self.keep_all):
            x.toggled.connect(self._refresh_command)
        self.keep_normal_y.toggled.connect(self._refresh_command)
        for c in (self.paint, self.plate, self.wheel, self.brakes):
            c.currentIndexChanged.connect(self._refresh_command)
        self.yaw.valueChanged.connect(self._refresh_command)
        return w

    def _pack_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(20, 6, 0, 6)
        wheels = QWidget()
        wl = QVBoxLayout(wheels)
        wl.setContentsMargins(0, 0, 0, 0)
        self.pw_family = QRadioButton("The wheels of each vehicle's family (recommended)")
        self.pw_all = QRadioButton("Every wheel in the app")
        self.pw_custom = QRadioButton("Only these:")
        self.pw_group = QButtonGroup(w)
        for b in (self.pw_family, self.pw_all, self.pw_custom):
            self.pw_group.addButton(b)
            wl.addWidget(b)
            b.toggled.connect(self._pack_wheels_changed)
        self.pw_list = QListWidget()
        self.pw_list.setMaximumHeight(130)
        self.pw_list.itemChanged.connect(self._refresh_command)
        wl.addWidget(self.pw_list)
        form.addRow("Wheels", wheels)

        self.one_zip = QCheckBox("One zip for all selected vehicles (default: one zip per vehicle)")
        self.split = QCheckBox("…but split it into several zips when one would go over the size limit")
        self.as_dir = QCheckBox("Write an unzipped folder instead of a zip (for card development)")
        form.addRow("Packaging", self.one_zip)
        form.addRow("", self.split)
        form.addRow("", self.as_dir)
        self.max_mib = QDoubleSpinBox()
        self.max_mib.setRange(1, 10000)
        self.max_mib.setDecimals(0)
        self.max_mib.setSuffix(" MiB")
        self.max_mib.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        form.addRow("Size limit", self.max_mib)
        form.addRow("", hint(f"Home Assistant accepts uploads up to 100 MiB. Upload the zip in {HA_UPLOAD}."))
        for x in (self.one_zip, self.split, self.as_dir):
            x.toggled.connect(self._refresh_command)
        for x in (self.one_zip, self.as_dir):
            x.toggled.connect(self._packaging_changed)
        self.max_mib.valueChanged.connect(self._refresh_command)
        return w

    def _packaging_changed(self, *_: Any) -> None:
        self.split.setEnabled(self.one_zip.isChecked() and not self.as_dir.isChecked())

    def _advanced_box(self) -> QGroupBox:
        box = QGroupBox("Advanced")
        box.setCheckable(True)
        box.setChecked(False)
        outer = QVBoxLayout(box)
        inner = QWidget()
        outer.addWidget(inner)
        box.toggled.connect(inner.setVisible)
        inner.setVisible(False)
        form = QFormLayout(inner)

        self.gdre_edit = QLineEdit()
        self.gdre_edit.setPlaceholderText(f"downloaded automatically (GDRE Tools v{GDRE_VERSION})")
        row, btn = browse_row(self.gdre_edit)
        btn.clicked.connect(self.choose_gdre)
        form.addRow("GDRE Tools", row)
        self.no_download = QCheckBox("Never download GDRE Tools")
        form.addRow("", self.no_download)
        self.rules_edit = QLineEdit()
        self.rules_edit.setPlaceholderText("folder with extra <codename>.yaml rules (optional)")
        row, btn = browse_row(self.rules_edit)
        btn.clicked.connect(self.choose_rules)
        form.addRow("Extra rules", row)
        self.verbose = QCheckBox("Verbose log")
        form.addRow("", self.verbose)

        cache = QWidget()
        cl = QHBoxLayout(cache)
        cl.setContentsMargins(0, 0, 0, 0)
        self.cache_label = QLabel()
        self.cache_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cl.addWidget(self.cache_label, 1)
        clear = QPushButton("Clear cache")
        clear.clicked.connect(self.clear_cache)
        cl.addWidget(clear)
        form.addRow("Recovered bundles", cache)

        cmd = QWidget()
        ml = QHBoxLayout(cmd)
        ml.setContentsMargins(0, 0, 0, 0)
        self.command = QLineEdit()
        self.command.setReadOnly(True)
        self.command.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        ml.addWidget(self.command, 1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.command.text()))
        ml.addWidget(copy)
        form.addRow("Same as CLI", cmd)

        for e in (self.gdre_edit, self.rules_edit):
            e.textChanged.connect(self._refresh_command)
        self.no_download.toggled.connect(self._refresh_command)
        self._refresh_cache()
        return box

    def _run_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.status = QLabel("Choose your Tesla app bundle to begin.")
        row.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        row.addWidget(self.progress)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)

        self.results = QListWidget()
        self.results.setVisible(False)
        self.results.setMaximumHeight(120)
        self.results.itemDoubleClicked.connect(self._open_result)
        lay.addWidget(self.results)
        res_row = QHBoxLayout()
        self.result_hint = QLabel()
        self.result_hint.setWordWrap(True)
        res_row.addWidget(self.result_hint, 1)
        self.open_btn = QPushButton("Open output folder")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self.open_output)
        res_row.addWidget(self.open_btn)
        lay.addLayout(res_row)

        self.log = log_view()
        self.log.setMinimumHeight(70)
        lay.addWidget(self.log, 1)
        return w

    # ---------- state ----------
    def apply_state(self, s: FormState) -> None:
        self.drop.show_path(s.source)
        (self.kind_glb if s.output_kind == GLB else self.kind_pack).setChecked(True)
        self.output_edit.setText(s.output_dir)
        self.v_perf.setChecked(s.performance)
        self.v_rhd.setChecked(s.rhd)
        self.v_seats.setChecked(s.seats_7)
        self.plate.setCurrentIndex(max(0, self.plate.findData(s.plate)))
        self._pending = s
        self.separate_wheels.setChecked(s.separate_wheels)
        self.cables.setChecked(s.cables)
        self.keep_all.setChecked(s.keep_all)
        self.keep_normal_y.setChecked(s.keep_normal_y)
        self.yaw.setValue(s.yaw)
        {"all": self.pw_all, "custom": self.pw_custom}.get(s.pack_wheels, self.pw_family).setChecked(True)
        self.one_zip.setChecked(s.one_zip)
        self.split.setChecked(s.split)
        self.as_dir.setChecked(s.as_dir)
        self.max_mib.setValue(s.max_mib)
        self.gdre_edit.setText(s.gdre_path)
        self.no_download.setChecked(s.no_download)
        self.rules_edit.setText(s.rules_dir)
        self.verbose.setChecked(s.verbose)
        self._source = s.source
        self._fill_combos()
        self._kind_changed()
        self._packaging_changed()

    def state(self) -> FormState:
        return FormState(
            source=self._source,
            output_kind=GLB if self.kind_glb.isChecked() else PACK,
            output_dir=self.output_edit.text().strip(),
            models=self.checked_models(),
            paint=self.paint.currentData() or "",
            performance=self.v_perf.isChecked(),
            rhd=self.v_rhd.isChecked(),
            seats_7=self.v_seats.isChecked(),
            plate=self.plate.currentData() or "eu",
            wheel=self.wheel.currentData() or "default",
            brakes=self.brakes.currentData() or "default",
            separate_wheels=self.separate_wheels.isChecked(),
            cables=self.cables.isChecked(),
            keep_all=self.keep_all.isChecked(),
            keep_normal_y=self.keep_normal_y.isChecked(),
            yaw=self.yaw.value(),
            pack_wheels="all" if self.pw_all.isChecked() else "custom" if self.pw_custom.isChecked() else "family",
            pack_wheel_names=[
                self.pw_list.item(i).text()
                for i in range(self.pw_list.count())
                if self.pw_list.item(i).checkState() == Qt.CheckState.Checked
            ],
            one_zip=self.one_zip.isChecked(),
            split=self.split.isChecked(),
            as_dir=self.as_dir.isChecked(),
            max_mib=self.max_mib.value(),
            gdre_path=self.gdre_edit.text().strip(),
            no_download=self.no_download.isChecked(),
            rules_dir=self.rules_edit.text().strip(),
            verbose=self.verbose.isChecked(),
        )

    def checked_models(self) -> list[str]:
        out = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def _set_analyzed(self, ok: bool) -> None:
        for w in (self.vehicle_box, self.output_box):
            w.setEnabled(ok)
        if ok:
            self._update_export_enabled()

    def _busy(self, busy: bool, cancellable: bool = False) -> None:
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy and cancellable)
        self.cancel_btn.setEnabled(True)
        self.analyze_btn.setEnabled(not busy and bool(self._source))
        self.drop.setEnabled(not busy)
        self.folder_btn.setEnabled(not busy)
        self.vehicle_box.setEnabled(not busy and self.session is not None)
        self.output_box.setEnabled(not busy and self.session is not None)
        if busy:
            self.progress.setRange(0, 0)

    # ---------- source ----------
    def choose_source(self) -> None:
        start = str(Path(self._source).parent) if self._source else str(self.settings.value("last_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose the Tesla app bundle",
            start,
            "Tesla app bundles (*.apks *.apkm *.xapk *.apk *.zip);;All files (*)",
        )
        if path:
            self.set_source(path)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a recovered project folder")
        if path:
            self.set_source(path)

    def set_source(self, path: str) -> None:
        p = Path(path)
        if p.is_file() and p.suffix.lower() not in BUNDLE_SUFFIXES:
            QMessageBox.warning(
                self, APP_NAME, f"{p.name} is not a Tesla app bundle (.apks, .apkm, .xapk, .apk) or asset pack (.zip)."
            )
            return
        self._source = str(p)
        self.settings.setValue("last_dir", str(p.parent))
        self.drop.show_path(self._source)
        self.session = None
        self.rows = []
        self.table.setRowCount(0)
        self._set_analyzed(False)
        self.analyze_btn.setEnabled(True)
        self.status.setText("Click Analyze to read the bundle.")
        self._refresh_command()

    def analyze(self) -> None:
        if not self._source or self.task is not None:
            return
        self.session = None
        self.results.setVisible(False)
        self.open_btn.setVisible(False)
        self.result_hint.clear()
        self._append("info", f"analyzing {self._source}")
        self.status.setText("Analyzing… (the first time for a bundle takes a few minutes)")
        self._start(AnalyzeTask(self.state(), self), cancellable=True)

    def _analyzed(self, session: Session) -> None:
        self.session = session
        self.rows = sorted(session.vehicles(), key=lambda v: not v.present)
        self._fill_table()
        self._fill_combos()
        if session.catalog is not None:
            for w in session.catalog.warnings:
                self._append("warning", w)
        if session.is_pack:
            self.kind_glb.setChecked(True)
        self.kind_pack.setEnabled(not session.is_pack)
        present = sum(1 for r in self.rows if r.present)
        what = {"pack": "asset pack", "recovered": "recovered project", "godot_root": "Godot project"}.get(
            session.kind, "app bundle"
        )
        version = f", app version {session.app_version}" if session.app_version else ""
        self.status.setText(f"Read the {what}: {present} vehicles available{version}. Pick vehicles below.")
        self.source_info.setText(f"{Path(session.source).name}: {what}{version}.")
        self._set_analyzed(True)
        self._refresh_cache()

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        default = self.session.default_vehicle() if self.session else None
        visible = min(len(self.rows), 14)
        self.table.setMinimumHeight(self.table.horizontalHeader().height() + 30 * max(visible, 4) + 4)
        for r, v in enumerate(self.rows):
            name = QTableWidgetItem(v.name)
            name.setData(Qt.ItemDataRole.UserRole, v.id)
            flags = Qt.ItemFlag.ItemIsUserCheckable
            if v.present:
                flags |= Qt.ItemFlag.ItemIsEnabled
            name.setFlags(flags)
            name.setCheckState(Qt.CheckState.Checked if default and v.id == default.id else Qt.CheckState.Unchecked)
            wheels = f"{len(v.wheels)} ({v.default_wheel})" if v.wheels else "-"
            cells = [
                name,
                QTableWidgetItem(v.id),
                QTableWidgetItem(", ".join(v.model_keys) or "-"),
                QTableWidgetItem(wheels),
                QTableWidgetItem(f"{v.size_bytes / 1e6:.1f} MB" if v.size_bytes else "-"),
                QTableWidgetItem("ok" if v.present else "not in bundle"),
            ]
            cells[2].setToolTip(v.api)
            for c, item in enumerate(cells):
                if c:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled if v.present else Qt.ItemFlag.NoItemFlags)
                self.table.setItem(r, c, item)
            self.table.setRowHeight(r, 30)
        self.table.blockSignals(False)
        self._vehicles_changed()

    def _toggle_row(self, row: int, col: int) -> None:
        item = self.table.item(row, 0)
        if col == 0 or item is None or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)

    def _check_all(self, on: bool) -> None:
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._vehicles_changed()

    def _vehicles_changed(self, *_: Any) -> None:
        n = len(self.checked_models())
        self.selection_info.setText(f"{n} selected")
        self._fill_combos()
        self._update_export_enabled()
        self._refresh_command()

    def _update_export_enabled(self) -> None:
        self.export_btn.setEnabled(self.session is not None and bool(self.checked_models()))

    def _selected_rows(self) -> list[VehicleRow]:
        ids = set(self.checked_models())
        return [r for r in self.rows if r.id in ids]

    def _fill_combos(self) -> None:
        """Paints, wheels and brake sets on offer depend on the session and the selected vehicles."""
        pending = self._pending
        keep_paint = (pending.paint if pending else None) or self.paint.currentData() or ""
        keep_wheel = (pending.wheel if pending else None) or self.wheel.currentData() or "default"
        keep_brakes = (pending.brakes if pending else None) or self.brakes.currentData() or "default"
        keep_custom = set(pending.pack_wheel_names) if pending else set(self.state().pack_wheel_names)
        if self.session is not None:
            self._pending = None
        sel = self._selected_rows()
        paints = self.session.paints() if self.session else []
        wheels = sorted({w for r in sel for w in r.wheels})
        sets = sorted({b for r in sel for b in r.brake_sets})
        all_wheels = self.session.all_wheels() if self.session else []

        def fill(combo: QComboBox, items: list[tuple[str, str]], keep: str) -> None:
            combo.blockSignals(True)
            combo.clear()
            for label, data in items:
                combo.addItem(label, data)
            combo.setCurrentIndex(max(0, combo.findData(keep)))
            combo.blockSignals(False)

        fill(self.paint, [("App default", "")] + [(p, p) for p in paints], keep_paint)
        fill(self.wheel, [("Vehicle default", "default"), ("No wheels", "none")] + [(w, w) for w in wheels], keep_wheel)
        fill(self.brakes, [("Vehicle default", "default"), ("No brakes", "none")] + [(b, b) for b in sets], keep_brakes)

        self.pw_list.blockSignals(True)
        self.pw_list.clear()
        for name in all_wheels:
            item = QListWidgetItem(name)
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if name in keep_custom else Qt.CheckState.Unchecked)
            self.pw_list.addItem(item)
        self.pw_list.blockSignals(False)
        self._pack_wheels_changed()

    def _kind_changed(self, *_: Any) -> None:
        current = 0 if self.kind_glb.isChecked() else 1
        self.stack.setCurrentIndex(current)
        # a QStackedWidget is as tall as its tallest page; let only the visible one count
        for i, page in enumerate(self.pages):
            policy = QSizePolicy.Policy.Preferred if i == current else QSizePolicy.Policy.Ignored
            page.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self.stack.adjustSize()
        self._refresh_command()

    def _pack_wheels_changed(self, *_: Any) -> None:
        self.pw_list.setVisible(self.pw_custom.isChecked())
        self._refresh_command()

    def _refresh_command(self, *_: Any) -> None:
        if not hasattr(self, "command"):
            return
        args = ["tesla-model-extract", *self.state().cli_args()]
        self.command.setText(" ".join(shlex.quote(a) for a in args))

    # ---------- export ----------
    def choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save to folder", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def export(self) -> None:
        if self.session is None or self.task is not None:
            return
        state = self.state()
        if not state.models:
            QMessageBox.information(self, APP_NAME, "Tick at least one vehicle.")
            return
        if not state.output_dir:
            self.choose_output()
            state = self.state()
            if not state.output_dir:
                return
        self._save()
        self.results.clear()
        self.results.setVisible(False)
        self.open_btn.setVisible(False)
        self.result_hint.clear()
        self._last_output = Path(state.output_dir)
        what = "GLB files" if state.output_kind == GLB else "asset pack"
        self.status.setText(f"Exporting {what} for {', '.join(state.models)} …")
        self._append("info", f"exporting {what} to {state.output_dir}")
        self._start(ExportTask(state, self.session, self))

    def _exported(self, rows: list[dict[str, Any]]) -> None:
        self.results.clear()
        for r in rows:
            warn = f", {len(r['warnings'])} warnings (see log)" if r["warnings"] else ""
            item = QListWidgetItem(f"{Path(r['path']).name}   {human(r['bytes'])}, {r['detail']}{warn}")
            item.setData(Qt.ItemDataRole.UserRole, str(r["path"]))
            item.setToolTip(str(r["path"]))
            self.results.addItem(item)
        self.results.setVisible(bool(rows))
        self.open_btn.setVisible(True)
        if self.kind_pack.isChecked():
            self.result_hint.setText(f"Upload the zip in Home Assistant: {HA_UPLOAD}.")
        else:
            self.result_hint.setText("Each vehicle has its own folder with the GLB and an unreal.json sidecar.")
        self.status.setText(f"Done: {len(rows)} file{'s' if len(rows) != 1 else ''} written.")

    def open_output(self) -> None:
        if self._last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output)))

    def _open_result(self, item: QListWidgetItem) -> None:
        p = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent if p.is_file() else p)))

    # ---------- tasks ----------
    def _start(self, task: Task, cancellable: bool = False) -> None:
        self.task = task
        task.log.connect(self._append)
        task.progress.connect(self._progress)
        task.succeeded.connect(self._task_ok)
        task.failed.connect(self._task_failed)
        task.finished.connect(self._task_done)
        self._busy(True, cancellable)
        self.simple_btn.setEnabled(False)
        task.start()

    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))
            self.status.setText(f"Downloading GDRE Tools… {human(done)} of {human(total)}")
        else:
            self.progress.setRange(0, 0)

    def _task_ok(self, result: Any) -> None:
        if isinstance(self.task, AnalyzeTask):
            self._analyzed(result)
        else:
            self._exported(result)

    def _task_failed(self, message: str, details: Any) -> None:
        self._append("error", message)
        self.status.setText(message.splitlines()[0])
        if message != "Cancelled.":
            QMessageBox.critical(self, APP_NAME, message)

    def _task_done(self) -> None:
        self.task = None
        self._busy(False)
        self.simple_btn.setEnabled(True)
        self._update_export_enabled()

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.cancel_btn.setEnabled(False)
            self.status.setText("Cancelling…")

    def _append(self, level: str, msg: str) -> None:
        append_log(self.log, level, msg)

    # ---------- advanced ----------
    def choose_gdre(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "GDRE Tools binary")
        if path:
            self.gdre_edit.setText(path)

    def choose_rules(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Folder with extra rules")
        if path:
            self.rules_edit.setText(path)

    def _refresh_cache(self) -> None:
        root = recovered_cache_root()
        self.cache_label.setText(f"{root}  ({human(dir_size(root))})")

    def clear_cache(self) -> None:
        if self.task is None and clear_recovery_cache(self):
            if self.on_cache_cleared is not None:
                self.on_cache_cleared()
            else:
                self.forget_cached_session()

    def forget_cached_session(self) -> None:
        """Drop the session when its recovered project was in the cache that just got deleted."""
        self._refresh_cache()
        if not uses_cache(self.session):
            return
        self.session = None
        self.rows = []
        self.table.setRowCount(0)
        self._set_analyzed(False)
        self.analyze_btn.setEnabled(bool(self._source))
        self.status.setText("Cache cleared. Click Analyze to read the bundle again.")

    # ---------- lifecycle ----------
    def _save(self) -> None:
        self.settings.setValue("form", self.state().to_json())

    def adopt(self, source: str, session: Session | None) -> None:
        """Take over what the wizard already opened, so switching modes does not analyze again."""
        if source:
            self.set_source(source)
        if session is not None:
            self._analyzed(session)

    def shutdown(self) -> None:
        self._save()
        stop_task(self.task, self.settings)
