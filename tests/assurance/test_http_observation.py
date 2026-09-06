"""HTTP transport diagnostics cannot stand in for mandatory HTTP responses."""
from __future__ import annotations

import copy
import socket
import sys
import threading

import pytest

from _support import observation, policy
from test_http_identity import http_manifest
from invara.assurance import coverage
from invara.assurance.compare import compare
from invara.assurance.http_boundary import observation_problems, validate_requests
from invara.assurance.manifest import Manifest, ManifestError
from invara.assurance.normalize import unobtained_observable


REQUESTS = [{"path": "/one"}, {"path": "/two"}]


def raw(responses, *, requests=None, side="before"):
    requests = requests or REQUESTS[:1]
    record = observation({"api": {"responses": [dict(
        request={"method": "GET", "path": req["path"], "body": None}, **response,
    ) for req, response in zip(requests, responses)]}}, system_id=side)
    record.update(http_service_port=12345,
                  http_request_declarations={"api": copy.deepcopy(requests)},
                  http_request_identities={"api": validate_requests(requests, 12345)})
    return record


def response(status=200):
    return {"status": status, "headers": {"content-type": "application/json"},
            "body": '{"error":"application-level"}', "truncated": False}


INVALID = [
    {"error": "ConnectionRefusedError: unavailable"},
    {"error": "ConnectionResetError: reset"},
    {"error": "TimeoutError: timed out"},
    {}, {"body": "missing status"},
    *[dict(response(), status=value) for value in [None, True, "200", 200.0, 0, 99, 600]],
    *[dict(response(), error=value) for value in [None, "", "ConnectionResetError"]],
    dict(response(), body=None),
]


@pytest.mark.parametrize("bad", INVALID)
def test_failed_or_malformed_responses_are_not_observations_or_equal(bad):
    manifest = http_manifest(REQUESTS[:1])
    left, right = raw([bad]), raw([bad], side="after")
    saved = copy.deepcopy(left)
    assert observation_problems(left, manifest)
    assert unobtained_observable(manifest.probes[0], left["probes"]["api"])
    result = compare(left, right, manifest)
    assert result.status == "unverifiable"
    assert not result.equivalent and not result.mandatory_equivalent
    assert result.compared_leaves == 0
    assert left == saved  # retain diagnostics, never rewrite evidence


@pytest.mark.parametrize("bad", INVALID)
@pytest.mark.parametrize("failed_side", ["source", "target"])
def test_one_sided_missing_http_observable_is_mandatory_divergence(bad, failed_side):
    manifest = http_manifest(REQUESTS[:1])
    left, right = raw([response()]), raw([bad], side="after")
    if failed_side == "source":
        left, right = right, left
    result = compare(left, right, manifest)
    assert result.status == "compared"
    assert not result.equivalent and not result.mandatory_equivalent
    assert any(d.mandatory for d in result.divergences)


@pytest.mark.parametrize("responses", [[response(), INVALID[0]], [response()], []])
@pytest.mark.parametrize("delivery", [False, True])
def test_partial_batches_are_unobserved_and_never_partly_proved(responses, delivery):
    manifest = http_manifest(REQUESTS, delivery=delivery)
    left = raw(responses, requests=REQUESTS)
    assert observation_problems(left, manifest)
    result = compare(left, copy.deepcopy(left), manifest)
    assert result.status == "unverifiable"
    assert not result.mandatory_equivalent and result.compared_leaves == 0
    one = compare(raw([response(), response()], requests=REQUESTS), left, manifest)
    assert one.status == "compared" and not one.mandatory_equivalent
    picture = coverage.coverage_map(manifest, [], raw_records=[("c1", left["probes"])], uncovered_volatile=[])
    if responses:
        assert picture["paths"]
        assert set(picture["paths"].values()) == {"UNVERIFIABLE"}
        assert set(picture["causes"].values()) == {"unobtained"}
    else:
        assert picture["probes"]["api"]["state"] == "UNOBSERVED"
        assert not picture["paths"]  # no fabricated coverage leaves


@pytest.mark.parametrize("ignored", ["/api/responses/*/error", "/api/responses", "/api"])
def test_other_probe_or_policy_cannot_make_missing_mandatory_http_pass(ignored):
    data = http_manifest(REQUESTS[:1]).as_dict()
    data["probes"].append({"id": "cli", "adapter": "process", "mandatory": True})
    data["policies"] = [policy("ignore", ignored)]
    if ignored == "/api":
        with pytest.raises(ManifestError, match="probe_ignore"):
            Manifest.from_dict(data)
        return
    manifest = Manifest.from_dict(data)
    bad = raw([INVALID[0]])
    bad["probes"]["cli"] = {"exit_code": 0}
    both = compare(bad, copy.deepcopy(bad), manifest)
    assert both.status == "unverifiable" and both.compared_leaves == 0
    good = raw([response()])
    good["probes"]["cli"] = {"exit_code": 0}
    one = compare(good, bad, manifest)
    assert one.status == "compared" and not one.mandatory_equivalent


@pytest.mark.parametrize("status", [200, 204, 301, 304, 400, 404, 500, 503])
def test_http_application_error_statuses_remain_real_observations(status):
    manifest = http_manifest(REQUESTS[:1])
    left = raw([response(status)])
    assert observation_problems(left, manifest) == []
    assert unobtained_observable(manifest.probes[0], left["probes"]["api"]) is None
    assert compare(left, copy.deepcopy(left), manifest).mandatory_equivalent


@pytest.mark.parametrize("shape", ["refusal", "reset", "timeout"])
def test_real_loopback_transport_failures_remain_diagnostics(shape):
    from invara.assurance import execute as ex
    manifest = http_manifest(REQUESTS[:1])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    done = threading.Event()
    worker = None
    if shape != "refusal":
        listener.listen()
        listener.settimeout(2)
        def serve():
            connection, _ = listener.accept()
            with connection:
                if shape == "timeout":
                    done.wait(1)
        worker = threading.Thread(target=serve)
        worker.start()
    try:
        result, problem = ex._probe_http(manifest.probes[0], REQUESTS[:1], listener.getsockname()[1], .05, 1024)
        assert problem is None  # adapter captured the failed attempt
        assert "error" in result["responses"][0]
        assert "status" not in result["responses"][0]
        assert unobtained_observable(manifest.probes[0], result)
    finally:
        done.set()
        if worker:
            worker.join(2)
            assert not worker.is_alive()
        listener.close()


def test_http_gap_on_one_finite_member_prevents_any_partial_proof():
    from invara.assurance import proof
    from test_proof import domain
    manifest = http_manifest(REQUESTS[:1])
    def evaluate(item):
        record = raw([INVALID[0] if item.input["n"] == 2 else response()])
        record["input_id"] = item.id
        return compare(record, copy.deepcopy(record), manifest)
    result = proof.prove(domain(n=[1, 2, 3]), evaluate, max_members=10)
    assert result.status == "UNVERIFIABLE"
    assert result.members_compared == 2
    assert "no partial proof" in result.reason


SERVICE = '''import http.server, os, socket
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/fail" and FAIL:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.send_response(200 if self.path == "/health" else STATUS)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")
http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
'''


def local_workflow(tmp_path, before_fail, after_fail, *, status=200, partial=False):
    requests = ([{"path": "/ok"}] if partial else []) + [{"path": "/fail"}]
    data = http_manifest(requests, delivery=True).as_dict()
    data["budgets"] = {"stability_runs": 1}
    roots = {}
    for side, key, fail in [("source_system", "SOURCE_ROOT", before_fail), ("target_system", "TARGET_ROOT", after_fail)]:
        root = tmp_path / key
        root.mkdir()
        (root / "service.py").write_text(f"FAIL={fail!r}\nSTATUS={status}\n" + SERVICE, encoding="utf-8")
        roots[key] = str(root)
        data[side]["command"] = [sys.executable, "service.py"]
        data[side]["root"] = "$" + key
        data[side]["service"]["ready"] = {"http": "/health"}
    return Manifest.from_dict(data), roots


@pytest.mark.parametrize("partial", [False, True])
def test_local_transport_loss_cannot_freeze_or_reach_pass(tmp_path, partial):
    from invara.assurance.evidence import Evidence
    from invara.assurance.workflow import Workflow, WorkflowError
    manifest, roots = local_workflow(tmp_path, True, True, partial=partial)
    store = Evidence(tmp_path / "verify.db")
    try:
        flow = Workflow(store)
        with pytest.raises(WorkflowError):
            flow.run(manifest, roots)
        picture = flow.coverage(manifest.session_id)
        assert "TESTED" not in picture["paths"].values()
        assert "PROVED" not in picture["paths"].values()
        assert flow.snapshot(manifest.session_id).state == "BASELINE_CAPTURING"
    finally:
        store.close()


@pytest.mark.parametrize("status", [200, 301, 404, 503])
@pytest.mark.parametrize("after_fail", [False, True])
def test_local_workflow_report_package_replay_and_cli_agree(tmp_path, capsys, status, after_fail):
    from invara.assurance.evidence import Evidence
    from invara.assurance.workflow import Workflow
    from invara.assurance.package import export, inspect
    from invara.__main__ import main, EXIT_OK
    manifest, roots = local_workflow(tmp_path, False, after_fail, status=status, partial=True)
    store = Evidence(tmp_path / "verify.db")
    try:
        flow = Workflow(store)
        result = flow.run(manifest, roots)
        assert result["verdict"]["status"] == ("BLOCK" if after_fail else "PASS")
        claim = flow.snapshot(manifest.session_id).active_claim_results()[0]
        assert claim["status"] == ("DIVERGED" if after_fail else "PRESERVED_WITHIN_ENVELOPE")
        destination = tmp_path / "evidence.zip"
        export(store, manifest.session_id, destination)
        checked = inspect(destination)
        assert checked["ok"], checked["problems"]
        assert checked["checks"]["verdict_agrees"]
        assert checked["checks"]["comparisons_agree"] > 0
        assert main(["assure", "inspect", str(destination)]) == EXIT_OK
        assert "INSPECT OK" in capsys.readouterr().out
    finally:
        store.close()
