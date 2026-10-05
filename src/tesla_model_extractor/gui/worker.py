"""Background threads for the slow parts (GDRE download and recovery, building, exporting)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from ..bundle import BundleError, detect
from ..gdre import Gdre, GdreCancelled, recovered_cache_dir
from ..service import (
    ExtractError,
    PackInvalid,
    Session,
    build_packs,
    export_glb,
    find_vehicles,
    locate_gdre,
    open_source,
)
from ..unreal.packsource import is_pack
from .form import GLB, FormState

PACKAGE_LOGGER = logging.getLogger("tesla_model_extractor")


class _Emitter(QObject):
    line = Signal(str, str)


class _LogBridge(logging.Handler):
    """Forwards the package's log records to the window; Qt queues the signal across threads."""

    def __init__(self, emitter: _Emitter):
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        level = "warning" if record.levelno >= logging.WARNING else "info"
        self.emitter.line.emit(level, record.getMessage())


class Task(QThread):
    log = Signal(str, str)  # level, message
    progress = Signal(int, int)  # done, total (total 0 = busy without a known end)
    succeeded = Signal(object)
    failed = Signal(str, object)  # message, details (PackInvalid output or None)

    def __init__(self, state: FormState, parent: QObject | None = None):
        super().__init__(parent)
        self.state = state
        self.cancelled = False
        self._emitter = _Emitter()
        self._emitter.line.connect(self.log)

    def notify(self, level: str, msg: str) -> None:
        self.log.emit(level, msg)

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> None:
        handler = _LogBridge(self._emitter)
        level = logging.DEBUG if self.state.verbose else logging.INFO
        handler.setLevel(level)
        PACKAGE_LOGGER.addHandler(handler)
        previous = PACKAGE_LOGGER.level
        PACKAGE_LOGGER.setLevel(level)
        try:
            self.succeeded.emit(self.work())
        except PackInvalid as e:
            msg = "The pack failed validation:\n" + "\n".join(f"• {x}" for x in e.output.validation.errors)
            self.failed.emit(msg + (f"\n\nTip: {e.hint}." if e.hint else ""), e.output)
        except GdreCancelled:
            self.failed.emit("Cancelled.", None)
        except ExtractError as e:
            self.failed.emit("Cancelled." if self.cancelled else str(e), None)
        except Exception as e:  # surfaced in the window instead of killing the thread silently
            logging.getLogger(__name__).exception("task failed")
            self.failed.emit(f"{type(e).__name__}: {e}", None)
        finally:
            PACKAGE_LOGGER.removeHandler(handler)
            PACKAGE_LOGGER.setLevel(previous)

    def work(self) -> Any:
        raise NotImplementedError


class AnalyzeTask(Task):
    """Open the source: download GDRE if needed, unpack and recover the bundle (cached), read the catalog."""

    def __init__(self, state: FormState, parent: QObject | None = None):
        super().__init__(state, parent)
        self.gdre: Gdre | None = None
        self.cache: Path | None = None

    def cancel(self) -> None:
        super().cancel()
        if self.gdre is not None:
            self.gdre.terminate()

    def _download_progress(self, done: int, total: int) -> None:
        self.progress.emit(done, total)

    def work(self) -> Session:
        src = Path(self.state.source)
        if not src.exists():
            raise ExtractError(f"{src} does not exist")
        keep: Path | None = None
        if not is_pack(src):
            if src.is_file():
                try:
                    version = detect(src).app_version
                except (BundleError, OSError) as e:
                    raise ExtractError(str(e)) from e
                keep = recovered_cache_dir(src, version)
                self.cache = keep.parent
                if not (keep / "project.godot").exists():
                    self.notify("info", "first look at this bundle: this takes a few minutes, later runs reuse it")
            self.gdre = locate_gdre(
                self.state.gdre_path or None,
                allow_download=not self.state.no_download,
                progress=self._download_progress,
                notify=self.notify,
            )
            if self.cancelled and self.gdre is not None:
                self.gdre.terminate()
        self.progress.emit(0, 0)
        try:
            return open_source(
                src,
                keep_recovered=keep,
                gdre=self.gdre,
                allow_download=False,  # the lookup above already honoured "never download"; do not try again
                rules_dir=self.state.rules_dir or None,
                accept_pack=True,
                drop_godot_root=True,
                notify=self.notify,
            )
        except (ExtractError, GdreCancelled):
            # a half-written recovery would otherwise be reused next time
            if self.cancelled and self.cache is not None:
                shutil.rmtree(self.cache, ignore_errors=True)
            raise


class ExportTask(Task):
    def __init__(self, state: FormState, session: Session, parent: QObject | None = None):
        super().__init__(state, parent)
        self.session = session

    def work(self) -> list[dict[str, Any]]:
        st = self.state
        out = Path(st.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        self.progress.emit(0, 0)
        if st.output_kind == GLB:
            result = export_glb(
                self.session,
                st.models,
                st.export_options(),
                out,
                wheels=st.wheel,
                separate_wheels=st.separate_wheels,
                cables=st.cables,
                notify=self.notify,
            )
            for w in result.warnings:
                self.notify("warning", w)
            rows = []
            for o in result.outputs:
                for w in o.warnings:
                    self.notify("warning", f"{o.model}: {w}")
                extra = [f"{o.animations} animations"]
                if o.wheels:
                    extra.append(f"{len(o.wheels)} wheel GLBs")
                if o.cables:
                    extra.append(f"{len(o.cables)} cables")
                rows.append({"path": o.glb, "bytes": o.glb_bytes, "detail": ", ".join(extra), "warnings": o.warnings})
            return rows
        packs = build_packs(
            self.session,
            find_vehicles(self.session, st.models),
            out,
            wheels=st.pack_wheels_spec(),
            bundle=st.one_zip,
            split=st.one_zip and st.split,
            as_dir=st.as_dir,
            max_mib=st.max_mib,
            output_is_target=False,
            notify=self.notify,
        )
        rows = []
        for p in packs:
            size = p.stats.raw_bytes if st.as_dir else p.stats.zip_bytes
            rows.append({"path": p.target, "bytes": size, "detail": ", ".join(p.models), "warnings": p.warnings})
        return rows
