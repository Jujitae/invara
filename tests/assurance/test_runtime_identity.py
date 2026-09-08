"""G: actual producer bytes, not whichever distribution metadata wins lookup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def probe(code, paths, cwd):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, paths)), PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def copy_source(tmp_path, name="site"):
    site = tmp_path / name
    shutil.copytree(ROOT / "src" / "invara", site / "invara", ignore=shutil.ignore_patterns("__pycache__"))
    return site


def metadata(site, version="0.3.0"):
    info = site / ("invara-" + version + ".dist-info")
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: invara\nVersion: " + version + "\n")
    (info / "RECORD").write_text("invara/__init__.py,,\n")
    (info / "entry_points.txt").write_text("[console_scripts]\ninvara = invara.__main__:main\ninvara-mcp = invara.mcp:main\n")
    return info


def test_source_version_is_not_unrelated_host_metadata(tmp_path):
    host = tmp_path / "host"
    metadata(host, "0.1.2")
    result = probe("from invara.assurance.workflow import _tool_versions; import json; print(json.dumps(_tool_versions()))", [ROOT / "src", host], tmp_path)
    assert result["invara"] == "0.3.0"


CAPTURE = "from invara.assurance.identity import capture; import json; print(json.dumps(capture()))"


@pytest.mark.parametrize("surface", ["src", "plugin/src"])
def test_clean_source_and_plugin_bind_complete_surface(tmp_path, surface):
    result = probe(CAPTURE, [ROOT / surface], tmp_path)
    assert result["source_version"] == "0.3.0"
    assert result["root"] == str((ROOT / surface / "invara").resolve())
    assert result["manifest"] == sorted(result["manifest"], key=lambda row: row["path"])
    assert {row["path"] for row in result["manifest"]} == {p.relative_to(ROOT / surface / "invara").as_posix() for p in (ROOT / surface / "invara").rglob("*.py")}
    assert {"intent.py", "intent_cli.py", "intent_view.py"} <= {row["path"] for row in result["manifest"]}
    assert result["git"]["commit"]
    assert result["launcher"]["python"]["sha256"]


def test_wheel_like_control_has_attributable_distribution(tmp_path):
    site = copy_source(tmp_path)
    metadata(site)
    result = probe(CAPTURE, [site], tmp_path)
    assert result["distribution"]["version"] == result["source_version"]
    assert result["git"] is None


@pytest.mark.parametrize("change", [
    "from pathlib import Path; p=Path(compare.__file__); p.write_bytes(p.read_bytes()+b'\\n# changed\\n')",
    "compare.__file__ = str(__import__('pathlib').Path(compare.__file__).with_name('other.py'))",
    "compare.compare.__code__ = (lambda *a, **k: None).__code__",
    "import invara; invara.__path__.append(str(__import__('pathlib').Path(invara.__file__).parent.parent/'other'))",
])
def test_post_freeze_mutation_refused(tmp_path, change):
    site = copy_source(tmp_path)
    result = probe("from invara.assurance.identity import capture, assert_current, IdentityError\nimport invara.assurance.compare as compare\nimport json\nfrozen=capture()\n" + change + "\ntry:\n assert_current(frozen)\nexcept IdentityError as e:\n print(json.dumps({'refused': str(e)}))\nelse:\n print(json.dumps({'refused': False}))", [site], tmp_path)
    assert result["refused"]


def test_mixed_import_origin_refused(tmp_path):
    site = copy_source(tmp_path)
    other = copy_source(tmp_path, "other")
    code = "import invara.assurance, json\ninvara.assurance.__path__.insert(0," + repr(str(other / "invara" / "assurance")) + ")\nfrom invara.assurance import compare\ntry:\n from invara.assurance.identity import capture\n capture()\nexcept Exception as e:\n print(json.dumps({'refused':str(e)}))\nelse:\n print(json.dumps({'refused':False}))"
    assert probe(code, [site], tmp_path)["refused"]


@pytest.mark.parametrize("change", ["version", "entrypoint"])
def test_conflicting_owned_metadata_refused(tmp_path, change):
    site = copy_source(tmp_path)
    info = metadata(site, "0.1.2" if change == "version" else "0.3.0")
    if change == "entrypoint":
        (info / "entry_points.txt").write_text("[console_scripts]\ninvara = other:main\n")
    code = "import json\ntry:\n from invara.assurance.identity import capture\n capture()\nexcept Exception as e:\n print(json.dumps({'refused':str(e)}))\nelse:\n print(json.dumps({'refused':False}))"
    assert probe(code, [site], tmp_path)["refused"]


def session_store(tmp_path):
    from _support import manifest_dict
    from invara.assurance.evidence import Evidence
    from invara.assurance.manifest import Manifest
    from invara.assurance.workflow import Workflow
    evidence = Evidence(tmp_path / "evidence.db")
    flow = Workflow(evidence)
    snap = flow.create(Manifest.from_dict(manifest_dict()), {"SOURCE_ROOT": str(tmp_path), "TARGET_ROOT": str(tmp_path)})
    return evidence, flow, snap


def test_session_and_package_carry_producer_separate_from_inspector(tmp_path):
    from invara.assurance.package import export, inspect
    evidence, flow, snap = session_store(tmp_path)
    try:
        producer = snap.as_dict().get("producer_identity")
        assert producer and producer["implementation_sha256"]
        assert flow.report(snap.session_id)["technical"]["provenance"]["producer_identity"] == producer
        target = tmp_path / "evidence.zip"
        export(evidence, snap.session_id, target)
        result = inspect(target)
        assert result["ok"], result["problems"]
        assert result["producer_identity"] == producer
        assert result["inspector_identity"]["implementation_sha256"] == producer["implementation_sha256"]
        assert result["identity_match"] is True
    finally:
        evidence.close()


def test_malformed_embedded_identity_is_refused():
    from invara.assurance.identity import capture, validate
    identity = capture()
    identity["manifest"][0]["sha256"] = "0" * 64
    assert validate(identity)


def test_workflow_refuses_another_producer(tmp_path):
    from invara.assurance.workflow import WorkflowError
    evidence, flow, snap = session_store(tmp_path)
    try:
        # Test the real continuity check on a copied snapshot, not a forged chain.
        producer = snap.as_dict().get("producer_identity")
        assert producer
        from invara.assurance.identity import assert_current, IdentityError, _digest
        producer["root"] += "-other"
        producer["identity_sha256"] = _digest({k: v for k, v in producer.items() if k != "identity_sha256"})
        with pytest.raises(IdentityError):
            assert_current(producer)
    finally:
        evidence.close()


def test_package_inspection_checks_embedded_structure_even_when_rehashed():
    from invara.assurance.identity import capture, validate, _digest
    original = capture()
    for key, value in (("modules", {}), ("source_version", []), ("runtime_functions_sha256", "bad"), ("launcher", {"python": {"path": "wrong", "sha256": "bad"}})):
        changed = dict(original, **{key: value})
        changed["identity_sha256"] = _digest({k: v for k, v in changed.items() if k != "identity_sha256"})
        assert validate(changed), key


def test_changed_wrapper_refused(tmp_path):
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text("# wrapper identity\n")
    code = "import sys, json\nsys.argv[0]=" + repr(str(wrapper)) + "\nfrom invara.assurance.identity import capture, assert_current, IdentityError\nfrozen=capture()\nfrom pathlib import Path\nPath(sys.argv[0]).write_text('# changed wrapper\\n')\ntry:\n assert_current(frozen)\nexcept IdentityError as e:\n print(json.dumps({'refused': str(e)}))\nelse:\n print(json.dumps({'refused': False}))"
    assert probe(code, [ROOT / "src"], tmp_path)["refused"]


def test_package_replay_distinguishes_relocated_and_changed_verifier(tmp_path):
    from invara.assurance.package import export
    evidence, flow, snap = session_store(tmp_path)
    target = tmp_path / "evidence.zip"
    try:
        export(evidence, snap.session_id, target)
    finally:
        evidence.close()
    site = copy_source(tmp_path)
    code = "from invara.assurance.package import inspect; import json; print(json.dumps(inspect(" + repr(str(target)) + ")))"
    clean = probe(code, [site], tmp_path)
    assert clean["ok"], clean["problems"]
    assert clean["producer_identity"]["root"] != clean["inspector_identity"]["root"]
    changed = site / "invara" / "assurance" / "compare.py"
    changed.write_bytes(changed.read_bytes() + b"\n# independently changed inspector implementation\n")
    result = probe(code, [site], tmp_path)
    assert result["identity_match"] is False
    assert result["ok"] is False
    assert any("producer verifier implementation differs" in p for p in result["problems"])


@pytest.mark.parametrize("mutation", ["compare.compare = lambda *a, **k: None", "from invara.assurance.workflow import Workflow; Workflow._check = lambda *a, **k: None"])
def test_replaced_function_before_creation_is_refused(tmp_path, mutation):
    code = "from invara.assurance.identity import capture, IdentityError\nimport invara.assurance.compare as compare\nimport json\n" + mutation + "\ntry:\n capture()\nexcept IdentityError as e:\n print(json.dumps({'refused': str(e)}))\nelse:\n print(json.dumps({'refused': False}))"
    assert probe(code, [ROOT / "src"], tmp_path)["refused"]


def test_inspector_change_during_read_clears_identity_match(tmp_path):
    from invara.assurance.package import export
    evidence, flow, snap = session_store(tmp_path)
    target = tmp_path / "evidence.zip"
    try:
        export(evidence, snap.session_id, target)
    finally:
        evidence.close()
    site = copy_source(tmp_path)
    code = """import json, zipfile
from pathlib import Path
from invara.assurance.package import inspect
import invara.assurance.compare as comparator
original = zipfile.ZipFile.open
changed = False
def reading(archive, *args, **kwargs):
 global changed
 if not changed:
  path = Path(comparator.__file__)
  path.write_bytes(path.read_bytes() + b'\\n# mutation during archive read\\n')
  changed = True
 return original(archive, *args, **kwargs)
zipfile.ZipFile.open = reading
print(json.dumps(inspect(TARGET)))
""".replace("TARGET", repr(str(target)))
    result = probe(code, [site], tmp_path)
    assert result["ok"] is False
    assert result["verdict"] is None
    assert result["identity_match"] is False


def test_unknown_module_is_refused_before_import(tmp_path):
    site = copy_source(tmp_path)
    sentinel = tmp_path / "unwanted-import"
    (site / "invara" / "surprise.py").write_text("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('ran')\n")
    code = "from invara.assurance.identity import capture, IdentityError\nimport json\ntry:\n capture()\nexcept IdentityError as e:\n print(json.dumps({'refused': str(e)}))\nelse:\n print(json.dumps({'refused': False}))"
    result = probe(code, [site], tmp_path)
    assert result["refused"]
    assert not sentinel.exists()


def test_c_invocation_does_not_claim_an_unexecuted_file(tmp_path):
    (tmp_path / "-c").write_text("this is not the Python command string")
    result = probe(CAPTURE, [ROOT / "src"], tmp_path)
    assert result["launcher"]["invocation"] == "-c"
    assert result["launcher"]["wrapper"] is None
