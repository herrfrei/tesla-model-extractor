import zipfile
from pathlib import Path

import pytest

from tesla_model_extractor.service import (
    ExtractError,
    PackInvalid,
    build_packs,
    export_glb,
    find_vehicles,
    open_source,
    pack_targets,
)
from tesla_model_extractor.unreal.scene import ExportOptions


def test_open_recovered_dir(recovered: Path, rules_dir: Path):
    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    assert s.kind == "recovered" and not s.is_pack and s.recovered == recovered
    rows = {r.id: r for r in s.vehicles()}
    kiwi = rows["kiwi"]
    assert kiwi.present and kiwi.name == "Kiwi test car" and kiwi.model_keys == ["modelk"]
    assert kiwi.wheels == ["Orbit19"] and kiwi.default_wheel == "Orbit19"
    assert [r.present for r in s.vehicles() if r.id != "kiwi"] == [False] * (len(rows) - 1)
    assert s.default_vehicle() is not None and s.default_vehicle().id == "kiwi"
    assert "SolidBlack" in s.paints()


def test_open_rejects_non_bundle(tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(ExtractError, match="not a zip-based bundle"):
        open_source(bad, allow_download=False)


def test_bundle_without_gdre_is_an_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))  # no cached GDRE download
    monkeypatch.delenv("GDRE_TOOLS", raising=False)
    apk = tmp_path / "Tesla_4.60.0.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/godot/project.binary", b"x")
    with pytest.raises(ExtractError, match="needs GDRE Tools"):
        open_source(apk, gdre_path=str(tmp_path / "missing"), allow_download=False)


def test_find_vehicles(recovered: Path, rules_dir: Path):
    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    assert [v.id for v in find_vehicles(s, ["testcar"])] == ["kiwi"]
    with pytest.raises(ExtractError, match="unknown or missing vehicle 's'"):
        find_vehicles(s, ["s"])


def test_build_packs_and_open_the_pack(recovered: Path, rules_dir: Path, tmp_path: Path):
    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    out = build_packs(s, find_vehicles(s, ["kiwi"]), tmp_path / "packs", output_is_target=False)
    assert len(out) == 1 and out[0].validation.ok
    assert out[0].target == tmp_path / "packs" / "tesla-view-pack-kiwi.zip" and out[0].target.exists()
    assert out[0].summary()["models"] == ["kiwi"]

    pack = open_source(out[0].target, accept_pack=True)
    assert pack.is_pack and [r.id for r in pack.vehicles()] == ["kiwi"]
    assert pack.vehicles()[0].wheels == ["Orbit19"]
    assert "SolidBlack" in pack.paints()


def test_pack_target_naming(recovered: Path, rules_dir: Path, tmp_path: Path):
    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    vs = find_vehicles(s, ["kiwi"])
    assert pack_targets(vs, tmp_path / "x.zip")[0][1] == tmp_path / "x.zip"
    assert pack_targets(vs, tmp_path / "d", as_dir=True)[0][1] == tmp_path / "d"
    named = pack_targets(vs, tmp_path / "d", as_dir=True, app_version="4.60.0", output_is_target=False)
    assert named[0][1] == tmp_path / "d" / "tesla-view-pack-kiwi-4.60.0"


def test_oversized_pack_raises(recovered: Path, rules_dir: Path, tmp_path: Path):
    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    with pytest.raises(PackInvalid) as ex:
        build_packs(s, find_vehicles(s, ["kiwi"]), tmp_path, max_mib=0.0001, output_is_target=False)
    assert ex.value.output.validation.errors


def test_export_glb_from_recovered_and_pack(recovered: Path, rules_dir: Path, tmp_path: Path):
    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    res = export_glb(s, ["kiwi"], ExportOptions(), tmp_path / "glb", cables=True)
    assert [o.model for o in res.outputs] == ["kiwi"] and res.outputs[0].glb.exists()
    assert res.summary()["vehicles"][0]["cables"] == ["CCS"]

    pack = build_packs(s, find_vehicles(s, ["kiwi"]), tmp_path / "packs", output_is_target=False)[0].target
    ps = open_source(pack, accept_pack=True)
    res2 = export_glb(ps, None, ExportOptions(), tmp_path / "glb2")
    assert res2.outputs[0].glb.read_bytes() == res.outputs[0].glb.read_bytes()
    with pytest.raises(ExtractError, match="pack contains"):
        export_glb(ps, ["nope"], ExportOptions(), tmp_path / "glb3")


def test_first_fit_counts_shared_files_once():
    from tesla_model_extractor.service import first_fit

    mib = 1024 * 1024
    entries = {
        "a": {"manifest.json": 1000, "A.glb": 40 * mib, "wheels/x": 20 * mib},
        "b": {"manifest.json": 1000, "B.glb": 30 * mib, "wheels/x": 20 * mib},  # shares the wheels with a
        "c": {"manifest.json": 1000, "C.glb": 45 * mib},
        "d": {"manifest.json": 1000, "D.glb": 5 * mib},
    }
    # a + b = 90 MiB because the wheels count once, d still fits next to them, c needs a zip of its own
    assert first_fit(entries, 100 * mib) == [["a", "b", "d"], ["c"]]
    assert first_fit(entries, 1000 * mib) == [["a", "b", "c", "d"]]
    assert first_fit(entries, 51 * mib) == [["a"], ["b"], ["c", "d"]]  # a alone is over: it still gets a zip


def test_split_keeps_one_zip_when_it_fits(recovered: Path, rules_dir: Path, tmp_path: Path):
    from tesla_model_extractor.service import plan_zip_groups

    s = open_source(recovered, rules_dir=rules_dir, allow_download=False)
    vs = find_vehicles(s, ["kiwi"])
    groups, built = plan_zip_groups(s, vs, None, 100)
    assert [[v.id for v in g] for g in groups] == [["kiwi"]] and built is not None
    lines: list[str] = []
    out = build_packs(s, vs, tmp_path, split=True, output_is_target=False, notify=lambda lvl, m: lines.append(m))
    assert [o.target.name for o in out] == ["tesla-view-pack-kiwi.zip"] and out[0].validation.ok
    assert any(m.startswith("wrote ") for m in lines) and any(m.startswith("kiwi: ") for m in lines)
