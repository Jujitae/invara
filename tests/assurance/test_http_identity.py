"""H repair: refusal before transport and endpoint-bound HTTP observations."""
from __future__ import annotations

import copy
import http.server
import socket
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from _support import manifest_dict, observation
from invara.assurance import execute as ex
from invara.assurance.compare import compare
from invara.assurance.manifest import Manifest, ManifestError, Probe


def http_manifest(requests, *, delivery=False):
    return Manifest.from_dict(manifest_dict(
        source_system={"id": "before", "kind": "service", "command": ["python", "service.py"], "service": {"ready": {"tcp": True}}},
        target_system={"same_as_source": True},
        input_domain={"kind": "corpus", "delivery": "http" if delivery else "stdin_json", "corpus": [{"id": "c1", "input": {"requests": requests}}]},
        probes=[{"id": "api", "adapter": "http", "requests": "$INPUT" if delivery else requests}],
    ))


INVALID = [
    {"url": "https://127.0.0.1/"}, {"url": "ftp://127.0.0.1/"},
    {"url": "javascript://127.0.0.1/"}, {"url": "HTTP://127.0.0.1/"},
    {"url": "hTtP://127.0.0.1/"}, {"url": "//127.0.0.1/"},
    {"url": " http://127.0.0.1/"}, {"url": "ht\ttp://127.0.0.1/"},
    {"url": "http://user@127.0.0.1/"}, {"url": "http://127.0.0.1/#fragment"},
    {"url": "http://127.0.0.1/", "path": "/other"},
    {"url": "http://127.0.0.1:/"}, {"url": "http://127.0.0.1:0/"},
    {"url": "http://127.0.0.1:65536/"}, {"url": "http://127.0.0.1:080/"},
    {"url": "http://localhost/"}, {"url": "http://[::1]suffix/"},
    {"path": "//other/"}, {"path": "http://127.0.0.1/"},
    {"path": "/a#fragment"}, {"path": "/a\\b"}, {"path": "/a\nb"},
    {"path": "/", "headers": {"Host": "other"}},
    {"path": "/", "method": "CONNECT"},
]


@pytest.mark.parametrize("req", INVALID)
@pytest.mark.parametrize("delivery", [False, True])
def test_manifest_refuses_unsupported_or_ambiguous_http_requests(req, delivery):
    with pytest.raises(ManifestError):
        http_manifest([req], delivery=delivery)


@pytest.mark.parametrize("req", INVALID)
def test_executor_refuses_before_constructing_transport(req, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid declaration reached transport creation")
    monkeypatch.setattr(ex.http.client, "HTTPConnection", forbidden)
    probe = Probe("api", "http", True, {"requests": [req]})
    result, problem = ex._probe_http(probe, [req], 12345, 1, 1024)
    assert problem
    assert not result["responses"]


@contextmanager
def local_server(host):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.server.targets.append((self.command, self.path, self.headers["Host"]))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
    class Server(http.server.HTTPServer):
        address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    server = Server((host, 0), Handler)
    server.targets = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("host,authority", [("127.0.0.1", "127.0.0.1"), ("::1", "[::1]")])
def test_local_http_records_executed_endpoint_and_exact_request_target(host, authority):
    with local_server(host) as server:
        port = server.server_port
        url = f"http://{authority}:{port}/hello%20world?q=one%2Ftwo"
        request = {"url": url}
        probe = http_manifest([request]).probes[0]
        result, problem = ex._probe_http(probe, [request], port, 2, 1024)
        assert problem is None
        assert result["responses"][0]["status"] == 200
        identity = result["request_identities"][0]
        assert identity["scheme"] == "http"
        assert identity["host"] == host
        assert identity["port"] == port
        assert identity["method"] == "GET"
        assert identity["target"] == "/hello%20world?q=one%2Ftwo"
        assert identity["declared_url"] == url
        assert identity["url"] == url
        assert server.targets == [("GET", "/hello%20world?q=one%2Ftwo", f"{authority}:{port}")]


def test_comparison_cannot_use_http_observations_without_endpoint_identity():
    manifest = http_manifest([{"path": "/"}])
    source = observation({"api": {"responses": [{"request": {"method": "GET", "path": "/", "body": None}, "status": 200, "body": "{}"}]}})
    target = copy.deepcopy(source)
    target["system_id"] = "after"
    result = compare(source, target, manifest)
    assert result.status == "unverifiable"
    assert any("HTTP" in p for p in result.problems)


def execute_local(tmp_path):
    from test_execute import SERVICE_APP
    (tmp_path / "service.py").write_text(SERVICE_APP, encoding="utf-8")
    data = http_manifest([{"method": "GET", "path": "/items"}], delivery=True).as_dict()
    for name in ("source_system", "target_system"):
        data[name]["command"] = [sys.executable, "service.py"]
        data[name]["service"]["ready"] = {"http": "/health"}
    manifest = Manifest.from_dict(data)
    roots = {"SOURCE_ROOT": str(tmp_path), "TARGET_ROOT": str(tmp_path)}
    return manifest, roots


def test_different_allocated_ports_preserve_response_comparison_and_raw_provenance(tmp_path):
    manifest, roots = execute_local(tmp_path)
    records = [ex.run(ex.RunSpec(system, manifest.input_domain.corpus[0], manifest, roots)) for system in (manifest.source_system, manifest.target_system)]
    for record in records:
        assert record["status"] == "observed", record["problems"]
        endpoint = record["http_request_identities"]["api"][0]
        assert endpoint["port"] == record["http_service_port"]
        assert endpoint["host"] == "127.0.0.1"
        assert endpoint["scheme"] == "http"
        assert endpoint["target"] == "/items"
    assert compare(*records, manifest).mandatory_equivalent
    # Altering transport evidence is never an accepted comparison policy.
    for field, value in [("scheme", "https"), ("host", "::1"), ("port", 1), ("method", "POST"), ("target", "/other"), ("declared_url", "http://127.0.0.1/")]:
        altered = copy.deepcopy(records[1])
        altered["http_request_identities"]["api"][0][field] = value
        assert compare(records[0], altered, manifest).status == "unverifiable", field
    altered = copy.deepcopy(records[1])
    altered["probes"]["api"]["responses"][0]["request"]["path"] = "/other"
    assert compare(records[0], altered, manifest).status == "unverifiable"


def test_absolute_url_uses_http_default_port_and_never_inherits_service_port(monkeypatch):
    from invara.assurance.http_boundary import request_identity
    assert request_identity({"url": "http://127.0.0.1/"})["port"] == 80
    def forbidden(*args, **kwargs):
        raise AssertionError("port mismatch reached transport")
    monkeypatch.setattr(ex.http.client, "HTTPConnection", forbidden)
    probe = Probe("api", "http", True, {})
    result, problem = ex._probe_http(probe, [{"url": "http://127.0.0.1/"}], 12345, 1, 1024)
    assert problem and not result["responses"]


def test_entire_request_batch_is_validated_before_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid batch reached transport")
    monkeypatch.setattr(ex.http.client, "HTTPConnection", forbidden)
    probe = Probe("api", "http", True, {})
    result, problem = ex._probe_http(probe, [{"path": "/"}, {"url": "https://127.0.0.1/"}], 12345, 1, 1024)
    assert problem and not result["responses"]


def test_expired_deadline_does_not_record_unattempted_endpoint_identity():
    probe = Probe("api", "http", True, {})
    result, problem = ex._probe_http(probe, [{"path": "/"}], 12345, 1, 1024, deadline=time.perf_counter() - 1)
    assert problem.startswith("timed out")
    assert result["responses"] == []
    assert result["request_identities"] == []


@pytest.mark.parametrize("path", ["//other/", "http://127.0.0.1/", "/a#fragment", "/a\nb"])
def test_readiness_target_obeys_same_origin_form_rules(path):
    data = http_manifest([{"path": "/"}]).as_dict()
    data["source_system"]["service"]["ready"] = {"http": path}
    with pytest.raises(ManifestError):
        Manifest.from_dict(data)


def test_mutated_request_refused_before_service_start_or_readiness(tmp_path, monkeypatch):
    manifest, roots = execute_local(tmp_path)
    manifest.input_domain.corpus[0].input["requests"] = [{"url": "https://127.0.0.1/"}]
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("invalid declaration reached service launch")
    monkeypatch.setattr(ex.subprocess, "Popen", forbidden)
    record = ex.run(ex.RunSpec(manifest.source_system, manifest.input_domain.corpus[0], manifest, roots))
    assert record["status"] != "observed"
    assert record["problems"]
    assert not attempts


def test_http_endpoint_evidence_survives_report_export_and_offline_inspection(tmp_path, capsys):
    import json
    import zipfile
    from invara.assurance.evidence import Evidence
    from invara.assurance.workflow import Workflow
    from invara.assurance.package import export, inspect
    manifest, roots = execute_local(tmp_path)
    evidence = Evidence(tmp_path / "http.db")
    try:
        flow = Workflow(evidence)
        flow.create(manifest, roots)
        flow.characterize(manifest.session_id, runs=2)
        flow.freeze(manifest.session_id)
        flow.compare(manifest.session_id)
        report = flow.report(manifest.session_id)
        assert report["technical"]["http_transport"]["supported_scheme"] == "http"
        destination = tmp_path / "http.zip"
        export(evidence, manifest.session_id, destination)
        result = inspect(destination)
        assert result["ok"], result["problems"]
        from invara.__main__ import main, EXIT_OK
        assert main(["assure", "inspect", str(destination)]) == EXIT_OK
        assert "INSPECT OK" in capsys.readouterr().out
        with zipfile.ZipFile(destination) as package:
            raws = [json.loads(package.read(name))["record"] for name in package.namelist() if name.startswith("observation/raw/")]
        assert len(raws) >= 3
        for raw in raws:
            assert raw["http_request_identities"]["api"][0]["port"] == raw["http_service_port"]
    finally:
        evidence.close()


@pytest.mark.parametrize("scheme", ["https", "ftp", "javascript"])
def test_cli_refuses_unsupported_scheme_manifest_before_execution(tmp_path, capsys, scheme):
    import json
    from invara.__main__ import main, EXIT_REFUSED
    data = http_manifest([{"path": "/"}]).as_dict()
    data["probes"][0]["requests"] = [{"url": f"{scheme}://127.0.0.1/"}]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code = main(["assure", "init", "--manifest", str(path), "--source-root", str(tmp_path), "--target-root", str(tmp_path), "--db", str(tmp_path / "evidence.db")])
    assert code == EXIT_REFUSED
    assert "bad_http_request" in capsys.readouterr().out
