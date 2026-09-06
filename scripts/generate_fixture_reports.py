#!/usr/bin/env python3
"""Regenerate the sample reports under docs/transformation-assurance/samples/.

Runs the proving fixtures through the real workflow — a governed repair of
the ugly shop (the valid refactor accepted, the wrong one rejected) and the
finite-domain proof for both rewrites — and writes the reports they
produce. Nothing is fabricated: every number in the samples came out of a
run of this script. The sandbox directory the fixtures ran in is replaced
by the token ``<sandbox>`` so the samples read the same on every machine;
the samples README says so.

    python scripts/generate_fixture_reports.py            # writes the samples
    python scripts/generate_fixture_reports.py --out DIR  # somewhere else
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invara.assurance import evidence as ev  # noqa: E402
from invara.assurance import manifest as m  # noqa: E402
from invara.assurance import report as report_module  # noqa: E402
from invara.assurance import workflow as wf  # noqa: E402

FIXTURES = ROOT / "fixtures"
SAMPLES = ROOT / "docs" / "transformation-assurance" / "samples"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def manifest_dict(name: str, **over: object) -> dict:
    data = json.loads((FIXTURES / "manifests" / name).read_text(encoding="utf-8"))
    for key in ("source_system", "target_system"):
        command = data[key].get("command")
        if command and command[0] == "python":
            command[0] = sys.executable
    data.update(over)
    return data


def _variants(path: str) -> tuple[str, ...]:
    return (path, path.replace("\\", "/"), path.replace("\\", "\\\\"))


def scrub(text: str, sandbox: Path) -> str:
    """Replace every machine-specific path: the sandbox, the interpreter, the home directory."""

    for variant in _variants(str(sandbox)):
        text = text.replace(variant, "<sandbox>")
    for variant in _variants(sys.executable):
        text = text.replace(variant, "<python>")
    for variant in _variants(str(Path.home())):
        text = text.replace(variant, "<home>")
    return text


def write_report(data: dict, stem: str, out: Path, sandbox: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stem}.json"
    md_path = out / f"{stem}.md"
    json_path.write_bytes(scrub(json.dumps(data, ensure_ascii=False, indent=1, default=str), sandbox).encode("utf-8"))
    md_path.write_bytes(scrub(report_module.markdown(data), sandbox).encode("utf-8"))
    return [json_path, md_path]


def repair_sample(sandbox: Path, out: Path) -> list[Path]:
    repo = sandbox / "shop"
    repo.mkdir()
    for name in ("app.py", "test_app.py"):
        shutil.copyfile(FIXTURES / "ugly_shop" / name, repo / name)
    (repo / ".gitignore").write_bytes(b"__pycache__/\n")
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.name", "Sample", cwd=repo)
    git("config", "user.email", "sample@example.com", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "ugly but working", cwd=repo)

    evidence = ev.Evidence(sandbox / "verify.db")
    try:
        flow = wf.Workflow(evidence, workspace_parent=sandbox / "runs")
        manifest = m.Manifest.from_dict(manifest_dict("shop.json", session_id="shop-repair-sample"))
        flow.repair_init(repo, manifest, workspace=sandbox / "repair")
        flow.characterize("shop-repair-sample", runs=2)
        flow.freeze("shop-repair-sample")
        flow.analyze(
            "shop-repair-sample",
            [
                {"id": "dup-pricing", "kind": "duplicate_implementation", "paths": ["app.py"], "summary": "quote() and commit_order() carry the same pricing loop", "declared_by": "host-agent"},
                {"id": "mixed", "kind": "mixed_responsibilities", "paths": ["app.py"], "summary": "pricing, storage and formatting share one function", "declared_by": "host-agent"},
            ],
        )
        flow.plan(
            "shop-repair-sample",
            [
                {"id": "u1", "objective": "가격 계산·저장 분리 / split pricing and storage out of app.py", "reason": "dup-pricing, mixed", "risk": "low", "owned_paths": ["app.py", "pricing.py", "storage.py", "test_app.py", "test_pricing.py"], "expected_behavior_impact": "none"},
                {"id": "u2", "objective": "할인 기준 정리 / tidy the discount threshold", "reason": "dup-pricing", "risk": "medium", "owned_paths": ["pricing.py"], "expected_behavior_impact": "none"},
            ],
        )
        unit = flow.unit_start("shop-repair-sample", "u1")
        for name in ("app.py", "pricing.py", "storage.py", "test_pricing.py"):
            shutil.copyfile(FIXTURES / "clean_shop" / name, unit / name)
        (unit / "test_app.py").unlink()
        flow.unit_verify("shop-repair-sample", "u1")
        flow.unit_accept("shop-repair-sample", "u1")
        flow.continue_("shop-repair-sample")
        unit2 = flow.unit_start("shop-repair-sample", "u2")
        shutil.copyfile(FIXTURES / "wrong_shop" / "pricing.py", unit2 / "pricing.py")
        flow.unit_verify("shop-repair-sample", "u2")
        flow.unit_reject("shop-repair-sample", "u2", reason="behaviour diverged at the discount boundary")
        flow.finish("shop-repair-sample")
        data = flow.report("shop-repair-sample")
    finally:
        evidence.close()
    return write_report(data, "repair-shop", out, sandbox)


def finite_sample(sandbox: Path, out: Path, target: str, stem: str) -> list[Path]:
    evidence = ev.Evidence(sandbox / f"{stem}.db")
    try:
        flow = wf.Workflow(evidence, workspace_parent=sandbox / "runs")
        data = manifest_dict("finite.json", session_id=stem)
        data["target_system"]["command"] = [sys.executable, target]
        manifest = m.Manifest.from_dict(data)
        flow.create(manifest, {"SOURCE_ROOT": str(FIXTURES / "finite"), "TARGET_ROOT": str(FIXTURES / "finite")})
        flow.characterize(stem)
        flow.freeze(stem)
        flow.prove(stem)
        flow.complete(stem)
        report = flow.report(stem)
    finally:
        evidence.close()
    return write_report(report, stem, out, sandbox)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SAMPLES)
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="invara-samples-") as temporary:
        sandbox = Path(temporary).resolve()
        written: list[Path] = []
        written += repair_sample(sandbox, arguments.out)
        written += finite_sample(sandbox, arguments.out, "target_ok.py", "finite-shipping-proved")
        written += finite_sample(sandbox, arguments.out, "target_bad.py", "finite-shipping-diverged")
    for path in written:
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
