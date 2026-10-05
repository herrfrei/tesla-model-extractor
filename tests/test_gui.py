import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings, Qt  # noqa: E402

from tesla_model_extractor.cli import _parser, export_options  # noqa: E402
from tesla_model_extractor.gui.advanced import AdvancedView  # noqa: E402
from tesla_model_extractor.gui.form import GLB, PACK, FormState  # noqa: E402
from tesla_model_extractor.gui.window import MainWindow  # noqa: E402
from tesla_model_extractor.gui.wizard import ANALYZE, EXPORT, PICK, SAVE, VEHICLES  # noqa: E402
from tesla_model_extractor.service import open_source  # noqa: E402


def _cli_options(state: FormState):
    return export_options(_parser().parse_args(state.cli_args()))


@pytest.mark.parametrize(
    "state",
    [
        FormState(source="b.apks", models=["kiwi"]),
        FormState(source="b.apks", models=["kiwi"], paint="SolidBlack", performance=True, rhd=True, plate="us"),
        FormState(source="b.apks", wheel="none", brakes="none", keep_all=True, keep_normal_y=True, yaw=90),
        FormState(source="b.apks", wheel="Orbit19", brakes="performance", seats_7=True, yaw=-12.5),
        FormState(source="b.apks", paint="PearlWhite", paint_brightness=2.5),
        FormState(source="b.apks", paint_brightness=1.0),
    ],
)
def test_form_matches_cli(state: FormState):
    assert state.export_options() == _cli_options(state)


def test_pack_cli_args():
    s = FormState(source="b.apks", output_kind=PACK, models=["a", "b"], output_dir="out", one_zip=True, max_mib=50)
    s.pack_wheels, s.pack_wheel_names = "custom", ["Orbit19", "Orbit20"]
    args = _parser().parse_args(s.cli_args())
    assert args.cmd == "extract" and args.models == "a,b" and args.output == "out"
    assert args.bundle and not args.dir and args.max_size == 50 and args.wheels == "Orbit19,Orbit20"
    s.pack_wheel_names = []
    assert s.pack_wheels_spec() == "family"


def test_form_json_roundtrip_skips_bundle_fields():
    s = FormState(source="x.apks", models=["kiwi"], paint="Red", yaw=45, output_kind=PACK, as_dir=True)
    back = FormState.from_json(s.to_json())
    assert back.source == "" and back.models == []
    assert back.paint == "Red" and back.yaw == 45 and back.output_kind == PACK and back.as_dir
    assert FormState.from_json("not json") == FormState()
    assert FormState.from_json('{"yaw": "x", "cables": 1, "max_mib": 20}').max_mib == 20.0


@pytest.fixture
def main(qtbot, tmp_path: Path, monkeypatch) -> MainWindow:
    import tesla_model_extractor.gdre as gdre

    def no_download(*a, **k):
        raise AssertionError("a test tried to download GDRE Tools")

    monkeypatch.setattr(gdre, "download", no_download)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    return w


@pytest.fixture
def window(main: MainWindow) -> AdvancedView:
    main.show_advanced()
    return main.advanced


def test_window_fills_from_session(window: AdvancedView, recovered: Path, rules_dir: Path):
    assert not window.output_box.isEnabled()
    window.set_source(str(recovered))
    window._analyzed(open_source(recovered, rules_dir=rules_dir, allow_download=False))
    assert window.output_box.isEnabled() and window.export_btn.isEnabled()
    assert window.checked_models() == ["kiwi"]
    paints = [window.paint.itemData(i) for i in range(window.paint.count())]
    assert paints[0] == "" and "SolidBlack" in paints
    wheels = [window.wheel.itemData(i) for i in range(window.wheel.count())]
    assert wheels == ["default", "none", "Orbit19"]
    # vehicles that are not in the bundle cannot be ticked
    for r in range(window.table.rowCount()):
        item = window.table.item(r, 0)
        enabled = bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        assert enabled == (item.data(Qt.ItemDataRole.UserRole) == "kiwi")
    window._check_all(False)
    assert window.checked_models() == [] and not window.export_btn.isEnabled()


def test_output_kind_switches_pages(window: AdvancedView, recovered: Path, rules_dir: Path, tmp_path: Path):
    window._analyzed(open_source(recovered, rules_dir=rules_dir, allow_download=False))
    window.kind_pack.setChecked(True)
    assert window.stack.currentIndex() == 1 and window.state().output_kind == PACK
    assert window.command.text().startswith("tesla-model-extract extract ")
    window.kind_glb.setChecked(True)
    assert window.stack.currentIndex() == 0 and window.state().output_kind == GLB


def test_pack_source_forces_glb(window: AdvancedView, recovered: Path, rules_dir: Path, tmp_path: Path):
    from tesla_model_extractor.service import build_packs, find_vehicles

    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    pack = build_packs(s, find_vehicles(s, ["kiwi"]), tmp_path / "p", output_is_target=False)[0].target
    window.kind_pack.setChecked(True)
    window._analyzed(open_source(pack, accept_pack=True))
    assert window.kind_glb.isChecked() and not window.kind_pack.isEnabled()


def test_analyze_and_export_end_to_end(window: AdvancedView, qtbot, recovered: Path, rules_dir: Path, tmp_path: Path):
    window.set_source(str(recovered))
    window.rules_edit.setText(str(rules_dir))
    window.no_download.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))
    window.analyze()
    qtbot.waitUntil(lambda: window.task is None and window.session is not None, timeout=20000)
    assert window.checked_models() == ["kiwi"]
    window.kind_pack.setChecked(True)
    window.export()
    qtbot.waitUntil(lambda: window.task is None, timeout=20000)
    assert window.results.count() == 1
    assert (tmp_path / "out" / "tesla-view-pack-kiwi.zip").exists()
    window.kind_glb.setChecked(True)
    window.export()
    qtbot.waitUntil(lambda: window.task is None, timeout=20000)
    assert (tmp_path / "out" / "kiwi" / "Kiwi.glb").exists()


def _drive_wizard_to_vehicles(main: MainWindow, qtbot, source: Path, rules_dir: Path) -> None:
    main.settings.setValue("form", FormState(rules_dir=str(rules_dir), no_download=True).to_json())
    main.wizard.pick(str(source))  # what a drop or the file dialog does
    assert main.wizard.current() == ANALYZE
    qtbot.waitUntil(lambda: main.wizard.task is None, timeout=20000)


def test_wizard_is_the_default_view(main: MainWindow):
    assert main.views.currentWidget() is main.wizard and main.wizard.current() == PICK
    main.wizard.advanced_btn.click()
    assert main.views.currentWidget() is main.advanced
    main.advanced.simple_btn.click()
    assert main.views.currentWidget() is main.wizard


def test_wizard_end_to_end(main: MainWindow, qtbot, recovered: Path, rules_dir: Path, tmp_path: Path):
    wiz = main.wizard
    _drive_wizard_to_vehicles(main, qtbot, recovered, rules_dir)
    assert wiz.current() == VEHICLES
    assert wiz.table.columnCount() == 3
    names = {wiz.table.item(r, 0).text(): wiz.table.item(r, 2).text() for r in range(wiz.table.rowCount())}
    assert names["Kiwi test car"] == "ready"
    assert wiz.table.item(0, 0).text() == "Kiwi test car"  # present vehicles first
    assert not wiz.next_btn.isEnabled()  # nothing ticked yet
    wiz._toggle_row(0, 1)
    assert wiz.checked_models() == ["kiwi"] and wiz.next_btn.isEnabled()
    wiz.next_btn.click()
    assert wiz.current() == SAVE and "Kiwi test car" in wiz.save_summary.text()
    # back and forth keeps the selection
    wiz.go(VEHICLES)
    assert wiz.checked_models() == ["kiwi"]
    wiz.next_btn.click()

    state = wiz._state()
    assert (state.output_kind, state.pack_wheels, state.one_zip, state.split) == ("pack", "family", True, True)
    assert state.max_mib == 100  # what Home Assistant accepts per upload
    assert state.rules_dir == str(rules_dir)  # advanced settings carry over

    wiz.output_edit.setText(str(tmp_path / "out"))
    wiz.export_btn.click()
    assert wiz.current() == EXPORT
    qtbot.waitUntil(lambda: wiz.task is None, timeout=20000)
    assert wiz.export_title.text() == "Done" and wiz.results.count() == 1
    assert (tmp_path / "out" / "tesla-view-pack-kiwi.zip").exists()
    assert wiz.open_btn.isEnabled() and "Upload the zip" in wiz.export_hint.text()
    log = wiz.export_log.toPlainText()
    assert "converting Kiwi test car" in log and "wrote " in log  # the export streams what it does

    wiz.done_back.click()
    assert wiz.current() == SAVE
    wiz.go(PICK)
    wiz.pick(str(recovered))  # the same bundle again: no new analysis
    assert wiz.current() == VEHICLES and wiz.task is None


def test_wizard_hands_its_session_to_advanced_mode(main: MainWindow, qtbot, recovered: Path, rules_dir: Path):
    _drive_wizard_to_vehicles(main, qtbot, recovered, rules_dir)
    session = main.wizard.session
    main.wizard.go(PICK)
    main.wizard.advanced_btn.click()
    assert main.advanced.session is session and main.advanced.output_box.isEnabled()


def test_wizard_rejects_packs_and_other_files(
    main: MainWindow, qtbot, recovered: Path, rules_dir: Path, tmp_path: Path
):
    from tesla_model_extractor.service import build_packs, find_vehicles

    other = tmp_path / "notes.txt"
    other.write_text("x")
    main.wizard.pick(str(other))
    assert main.wizard.current() == PICK and "not a Tesla app bundle" in main.wizard.pick_error.text()

    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    pack = build_packs(s, find_vehicles(s, ["kiwi"]), tmp_path / "p", output_is_target=False)[0].target
    _drive_wizard_to_vehicles(main, qtbot, pack, rules_dir)
    assert main.wizard.current() == PICK and "advanced manual mode" in main.wizard.pick_error.text()


def test_clear_cache_from_the_wizard(main: MainWindow, recovered: Path, rules_dir: Path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from tesla_model_extractor.gdre import recovered_cache_root

    wiz = main.wizard
    assert wiz.clear_btn.isHidden()  # nothing analyzed yet
    cached = recovered_cache_root() / "4.60.0-abc" / "project"
    cached.mkdir(parents=True)
    (cached / "big.bin").write_bytes(b"x" * 3 * 1048576)
    wiz.go(PICK)
    assert not wiz.clear_btn.isHidden() and wiz.clear_btn.text() == "Clear cache (3.0 MiB)"

    session = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    session.recovered = cached  # as if the wizard had read a bundle into the cache
    wiz.session = session
    main.show_advanced(str(recovered), session)
    main.show_wizard()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    wiz.clear_btn.click()
    assert cached.exists() and wiz.session is session

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    wiz.clear_btn.click()
    assert not recovered_cache_root().exists()
    assert wiz.session is None and wiz.clear_btn.isHidden()
    assert main.advanced.session is None and not main.advanced.output_box.isEnabled()
    assert "(0 KiB)" in main.advanced.cache_label.text()


def test_clear_cache_keeps_sessions_outside_the_cache(main: MainWindow, recovered: Path, rules_dir: Path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from tesla_model_extractor.gdre import recovered_cache_root

    (recovered_cache_root() / "x").mkdir(parents=True)
    (recovered_cache_root() / "x" / "f").write_bytes(b"x")
    session = open_source(recovered, rules_dir=rules_dir, allow_download=False)  # a folder the user picked
    main.show_advanced(str(recovered), session)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    main.advanced.clear_cache()
    assert not recovered_cache_root().exists() and main.advanced.session is session
