"""The desktop app's form as plain data, and how it maps onto the pipeline and the equivalent CLI command.

Kept free of Qt so the mapping is testable without a display.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from ..unreal.scene import ExportOptions
from ..validate import DEFAULT_MAX_MIB

GLB, PACK = "glb", "pack"
OPTIONAL_VARIANTS = ("performance", "rhd", "seats_7")


@dataclass
class FormState:
    source: str = ""
    output_kind: str = GLB
    output_dir: str = ""
    models: list[str] = field(default_factory=list)
    # GLB export (`unreal`)
    paint: str = ""  # "" = the app's fallback paint
    performance: bool = False
    rhd: bool = False
    seats_7: bool = False
    plate: str = "eu"  # eu | us
    wheel: str = "default"  # default | none | API wheel name
    brakes: str = "default"  # default | none | set name
    separate_wheels: bool = False
    cables: bool = False
    keep_all: bool = False
    keep_normal_y: bool = False
    yaw: float = 0.0
    # asset pack (`extract`)
    pack_wheels: str = "family"  # family | all | custom
    pack_wheel_names: list[str] = field(default_factory=list)
    one_zip: bool = False
    split: bool = False  # with one_zip: several zips when one would exceed max_mib
    as_dir: bool = False
    max_mib: float = DEFAULT_MAX_MIB
    # advanced
    gdre_path: str = ""
    no_download: bool = False
    rules_dir: str = ""
    verbose: bool = False

    def variants(self) -> frozenset[str]:
        out = {name for name in OPTIONAL_VARIANTS if getattr(self, name)}
        out.add("plate_us" if self.plate == "us" else "plate_eu")
        return frozenset(out)

    def export_options(self) -> ExportOptions:
        return ExportOptions(
            variants=self.variants(),
            keep_all=self.keep_all,
            wheel=None if self.wheel == "none" else self.wheel,
            brakes=None if self.brakes == "none" else self.brakes,
            paint=self.paint or None,
            yaw_deg=self.yaw,
            flip_normal_green=not self.keep_normal_y,
        )

    def pack_wheels_spec(self) -> str:
        if self.pack_wheels == "custom":
            return ",".join(self.pack_wheel_names) or "family"
        return self.pack_wheels

    def cli_args(self) -> list[str]:
        """The `tesla-model-extract` arguments that produce the same output (shown in the app, for scripting)."""
        args: list[str] = ["unreal" if self.output_kind == GLB else "extract", self.source or "<bundle>"]
        args += ["--models", ",".join(self.models)] if self.models else []
        args += ["-o", self.output_dir or "."]
        if self.output_kind == GLB:
            if self.paint:
                args += ["--paint", self.paint]
            variants = sorted(self.variants())
            if variants != ["plate_eu"]:
                args += ["--variant", ",".join(variants)]
            if self.wheel != "default":
                args += ["--wheels", self.wheel]
            if self.brakes != "default":
                args += ["--brakes", self.brakes]
            for flag, on in (
                ("--separate-wheels", self.separate_wheels),
                ("--cables", self.cables),
                ("--keep-all", self.keep_all),
                ("--keep-normal-y", self.keep_normal_y),
            ):
                if on:
                    args.append(flag)
            if self.yaw:
                args += ["--yaw", f"{self.yaw:g}"]
        else:
            if self.pack_wheels_spec() != "family":
                args += ["--wheels", self.pack_wheels_spec()]
            if self.one_zip:
                args.append("--split" if self.split else "--bundle")
            if self.as_dir:
                args.append("--dir")
            if self.max_mib != DEFAULT_MAX_MIB:
                args += ["--max-size", f"{self.max_mib:g}"]
        if self.gdre_path:
            args += ["--gdre", self.gdre_path]
        if self.no_download:
            args.append("--no-download")
        if self.rules_dir:
            args += ["--rules", self.rules_dir]
        return args

    # settings persistence: everything except what belongs to one bundle
    def to_json(self) -> str:
        data = asdict(self)
        for k in ("source", "models"):
            data.pop(k)
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str | None) -> FormState:
        state = cls()
        try:
            data: dict[str, Any] = json.loads(text or "{}")
        except ValueError:
            return state
        if not isinstance(data, dict):
            return state
        for f in fields(cls):
            if f.name in data and f.name not in ("source", "models"):
                default = getattr(state, f.name)
                value = data[f.name]
                if isinstance(default, bool) or isinstance(value, bool):
                    if isinstance(default, bool) and isinstance(value, bool):
                        setattr(state, f.name, value)
                elif isinstance(default, float) and isinstance(value, int | float):
                    setattr(state, f.name, float(value))
                elif isinstance(value, type(default)):
                    setattr(state, f.name, value)
        return state
