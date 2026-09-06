"""One run of one input against one system, observed by every adapter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path

from _support import manifest_dict
from invara.assurance import OBSERVATION_VERSION
from invara.assurance import execute as ex
from invara.assurance import manifest as m

ECHO_APP = textwrap.dedent(
    '''
    import json, os, sqlite3, sys, time
    def main():
        if os.environ.get("INVARA_INPUT"):
            data = json.load(open(os.environ["INVARA_INPUT"], encoding="utf-8"))
        elif len(sys.argv) > 1:
            data = json.loads(sys.argv[1])
        else:
            data = json.load(sys.stdin)
        ws = os.environ["INVARA_WORKSPACE"]
        if "sleep" in data:
            time.sleep(data["sleep"])
        if "write" in data:
            os.makedirs(os.path.join(ws, "out"), exist_ok=True)
            with open(os.path.join(ws, "out", data["write"]), "wb") as fh:
                fh.write(("hello " + data["write"] + "\\n").encode("utf-8"))
        if data.get("remove"):
            os.remove(os.path.join(ws, "seed.txt"))
        if "db" in data:
            con = sqlite3.connect(os.path.join(ws, "app.db"))
            con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, sku TEXT, qty INTEGER)")
            for sku, qty in data["db"]:
                con.execute("INSERT INTO orders (sku, qty) VALUES (?, ?)", (sku, qty))
            con.commit(); con.close()
        if "stderr" in data:
            sys.stderr.write(data["stderr"])
        if "big" in data:
            sys.stdout.write("x" * data["big"]); sys.exit(0)
        if "raw" in data:
            sys.stdout.write(data["raw"]); sys.exit(0)
        out = {"echo": data.get("echo"), "secret": os.environ.get("SECRET"), "mode": os.environ.get("APP_MODE"), "tz": os.environ.get("TZ"),
               "cwd": os.getcwd(), "ws": ws}
        print(json.dumps(out))
        sys.exit(int(data.get("exit", 0)))
    main()
    '''
)

SERVICE_APP = textwrap.dedent(
    '''
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    ITEMS = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if self.path == "/health": return self._send(200, {"ok": True})
            if self.path == "/items": return self._send(200, ITEMS)
            self._send(404, {"error": "no"})
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
            ITEMS.append(body); self._send(201, {"count": len(ITEMS)})
    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
    '''
)


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "app.py").write_text(ECHO_APP, encoding="utf-8")
        (self.root / "service.py").write_text(SERVICE_APP, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def spec(self, item: dict, *, probes=None, delivery="stdin_json", system=None, **over) -> ex.RunSpec:
        system = system or {"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT"}
        data = manifest_dict(
            source_system=system,
            target_system={"same_as_source": True},
            input_domain={"kind": "corpus", "delivery": delivery, "corpus": [dict(item, id=item.get("id", "c1"))]},
            probes=probes or [{"id": "cli", "adapter": "process", "mandatory": True}, {"id": "out", "adapter": "json", "source": "stdout", "mandatory": True}],
            **over,
        )
        manifest = m.Manifest.from_dict(data)
        return ex.RunSpec(system=manifest.source_system, item=manifest.input_domain.corpus[0], manifest=manifest, roots={"SOURCE_ROOT": str(self.root), "TARGET_ROOT": str(self.root)})


class ProcessAdapter(Sandbox):
    def test_a_run_records_exit_code_stdout_and_stderr(self) -> None:
        record = ex.run(self.spec({"input": {"echo": "hi", "stderr": "warn", "exit": 3}}), workspace_parent=self.root)
        self.assertEqual(record["record_version"], OBSERVATION_VERSION)
        self.assertEqual(record["status"], "observed")
        cli = record["probes"]["cli"]
        self.assertEqual(cli["exit_code"], 3)
        self.assertEqual(cli["stderr"], "warn")
        self.assertIn('"echo": "hi"', cli["stdout"])
        self.assertEqual(record["probes"]["out"]["value"]["echo"], "hi")
        self.assertEqual(record["system_id"], "before")
        self.assertEqual(record["input_id"], "c1")

    def test_the_command_runs_without_a_shell_and_from_the_resolved_root(self) -> None:
        record = ex.run(self.spec({"input": {"echo": 1}}), workspace_parent=self.root)
        self.assertEqual(record["command"], [sys.executable, "app.py"])
        self.assertEqual(Path(record["probes"]["out"]["value"]["cwd"]).resolve(), self.root.resolve())
        self.assertEqual(Path(record["root"]).resolve(), self.root.resolve())

    def test_argv_delivery(self) -> None:
        record = ex.run(self.spec({"input": {"echo": [1, 2]}}, delivery="argv_json"), workspace_parent=self.root)
        self.assertEqual(record["probes"]["out"]["value"]["echo"], [1, 2])

    def test_file_delivery(self) -> None:
        record = ex.run(self.spec({"input": {"echo": {"k": "v"}}}, delivery="file_json"), workspace_parent=self.root)
        self.assertEqual(record["probes"]["out"]["value"]["echo"], {"k": "v"})

    def test_the_environment_is_an_allowlist_plus_controlled_values(self) -> None:
        os.environ["SECRET"] = "hunter2"
        try:
            record = ex.run(self.spec({"input": {"echo": 1}}), workspace_parent=self.root)
        finally:
            del os.environ["SECRET"]
        value = record["probes"]["out"]["value"]
        self.assertIsNone(value["secret"])
        self.assertEqual(value["tz"], "UTC")
        env = record["environment"]
        self.assertEqual(env["controlled"]["TZ"], "UTC")
        self.assertEqual(env["controlled"]["PYTHONHASHSEED"], "0")
        self.assertNotIn("SECRET", env["inherited"])
        self.assertIn("wall_clock", env["uncontrollable"])
        self.assertIn("thread_scheduling", env["uncontrollable"])

    def test_declared_env_values_reach_the_child_and_the_record(self) -> None:
        system = {"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT", "env": {"set": {"APP_MODE": "declared"}}}
        record = ex.run(self.spec({"input": {"echo": 1}}, system=system), workspace_parent=self.root)
        self.assertEqual(record["probes"]["out"]["value"]["mode"], "declared")
        self.assertEqual(record["environment"]["controlled"]["APP_MODE"], "declared")

    def test_capture_is_bounded_and_the_full_digest_is_kept(self) -> None:
        system = {"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT", "capture_limit_bytes": 100}
        record = ex.run(self.spec({"input": {"big": 5000}}, system=system), workspace_parent=self.root)
        cli = record["probes"]["cli"]
        self.assertTrue(cli["truncated"]["stdout"])
        self.assertLessEqual(len(cli["stdout"].encode("utf-8")), 100)
        self.assertEqual(len(cli["stdout_digest"]), 64)
        self.assertEqual(cli["stdout_bytes"], 5000)

    def test_a_timeout_is_reported_not_judged(self) -> None:
        record = ex.run(self.spec({"input": {"sleep": 30}}, timeouts={"run_seconds": 1}), workspace_parent=self.root)
        self.assertEqual(record["status"], "timeout")
        self.assertIn("1", record["problems"][0])
        self.assertNotIn("out", record["probes"])

    def test_a_timeout_keeps_bounded_partial_stdout_and_stderr_evidence(self) -> None:
        (self.root / "partial_timeout.py").write_text(
            "import sys, time\n"
            "sys.stdout.buffer.write(b'O' * 96); sys.stdout.flush()\n"
            "sys.stderr.buffer.write(b'E' * 80); sys.stderr.flush()\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        system = {
            "id": "before",
            "kind": "process",
            "command": [sys.executable, "partial_timeout.py"],
            "root": "$SOURCE_ROOT",
            "capture_limit_bytes": 32,
        }
        probes = [{"id": "cli", "adapter": "process", "mandatory": True}]

        record = ex.run(self.spec({"input": {}}, system=system, probes=probes, timeouts={"run_seconds": 0.25}), workspace_parent=self.root)

        self.assertEqual(record["status"], "timeout")
        cli = record["probes"]["cli"]
        self.assertIsNone(cli["exit_code"])
        self.assertEqual(cli["stdout"], "O" * 32)
        self.assertEqual(cli["stderr"], "E" * 32)
        self.assertEqual(cli["stdout_bytes"], 96)
        self.assertEqual(cli["stderr_bytes"], 80)
        self.assertEqual(len(cli["stdout_digest"]), 64)
        self.assertEqual(len(cli["stderr_digest"]), 64)
        self.assertEqual(cli["truncated"], {"stdout": True, "stderr": True})

    @unittest.skipUnless(os.name == "nt", "Windows process-tree containment regression")
    def test_a_timeout_does_not_return_while_a_descendant_can_still_run(self) -> None:
        marker = self.root / "descendant-survived.txt"
        child = "import pathlib,sys,time; time.sleep(1); pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
        (self.root / "tree_timeout.py").write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}, sys.argv[1]])\n"
            "print('PARENT', flush=True)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        system = {
            "id": "before",
            "kind": "process",
            "command": [sys.executable, "tree_timeout.py", str(marker)],
            "root": "$SOURCE_ROOT",
        }
        probes = [{"id": "cli", "adapter": "process", "mandatory": True}]

        with mock.patch.object(ex.subprocess, "run", side_effect=OSError("taskkill unavailable")):
            record = ex.run(self.spec({"input": {}}, system=system, probes=probes, timeouts={"run_seconds": 0.25}), workspace_parent=self.root)
        time.sleep(1.2)

        self.assertEqual(record["status"], "timeout")
        self.assertFalse(marker.exists(), "a timed-out descendant must be dead before INVARA returns")

    @unittest.skipUnless(os.name == "nt", "Windows process-tree containment regression")
    def test_a_timeout_fails_closed_when_tree_death_cannot_be_established(self) -> None:
        real_end_job = ex._end_windows_job

        def stop_but_refuse_to_attest(process) -> None:
            real_end_job(process)
            raise ex.ExecutionError("process-tree death could not be established")

        probes = [{"id": "cli", "adapter": "process", "mandatory": True}]
        with mock.patch.object(ex, "_end_windows_job", side_effect=stop_but_refuse_to_attest):
            record = ex.run(self.spec({"input": {"sleep": 30}}, probes=probes, timeouts={"run_seconds": 0.25}), workspace_parent=self.root)

        self.assertEqual(record["status"], "unverifiable")
        self.assertTrue(any("process-tree death could not be established" in problem for problem in record["problems"]))
        self.assertIn("cli", record["probes"], "partial capture remains evidence on cleanup failure")

    def test_a_missing_executable_is_unrunnable(self) -> None:
        system = {"id": "before", "kind": "process", "command": ["invara-no-such-program-exists"], "root": "$SOURCE_ROOT"}
        record = ex.run(self.spec({"input": {}}, system=system), workspace_parent=self.root)
        self.assertEqual(record["status"], "unrunnable")
        self.assertIn("invara-no-such-program-exists", record["problems"][0])

    def test_captured_text_is_redacted_before_it_is_evidence(self) -> None:
        record = ex.run(self.spec({"input": {"raw": "password=hunter2 done"}}), workspace_parent=self.root)
        self.assertNotIn("hunter2", record["probes"]["cli"]["stdout"])
        self.assertNotIn("hunter2", json.dumps(record))
        self.assertGreaterEqual(record["redactions"], 1)

    def test_two_different_secrets_still_diverge_and_neither_is_persisted(self) -> None:
        """Redaction is confidentiality, not equivalence: the evidence keeps a digest, the comparison keeps the difference."""

        from invara.assurance import compare as cmp

        source = ex.run(self.spec({"input": {"raw": '{"token": "sk-AAAAAAAAAAAAAAAAAAAAAAAA1"}'}}), workspace_parent=self.root)
        target = ex.run(self.spec({"input": {"raw": '{"token": "sk-BBBBBBBBBBBBBBBBBBBBBBBB2"}'}}), workspace_parent=self.root)
        same = ex.run(self.spec({"input": {"raw": '{"token": "sk-AAAAAAAAAAAAAAAAAAAAAAAA1"}'}}), workspace_parent=self.root)
        for record in (source, target, same):
            self.assertNotIn("AAAAAAAAAAAAAAAAAAAAAAAA1", json.dumps(record))
            self.assertNotIn("BBBBBBBBBBBBBBBBBBBBBBBB2", json.dumps(record))
        manifest = self.spec({"input": {}}).manifest
        differs = cmp.compare(source, target, manifest)
        self.assertFalse(differs.equivalent)
        self.assertTrue(any(d.path == "/out/value/token" for d in differs.divergences))
        self.assertTrue(cmp.compare(source, same, manifest).equivalent)

    def test_controlled_environment_values_are_recorded(self) -> None:
        # whole-record redaction is held by test_review2.WholeRecordRedaction
        system = {"id": "before", "kind": "process", "command": [sys.executable, "app.py"], "root": "$SOURCE_ROOT", "env": {"set": {"MODE": "test"}}}
        record = ex.run(self.spec({"input": {"echo": 1}}, system=system), workspace_parent=self.root)
        self.assertEqual(record["environment"]["controlled"]["MODE"], "test")

    def test_the_workspace_is_fresh_per_run_and_recorded(self) -> None:
        one = ex.run(self.spec({"input": {"echo": 1}}), workspace_parent=self.root)
        two = ex.run(self.spec({"input": {"echo": 1}}), workspace_parent=self.root)
        self.assertNotEqual(one["workspace"], two["workspace"])
        self.assertEqual(Path(one["probes"]["out"]["value"]["ws"]).resolve(), Path(one["workspace"]).resolve())
        self.assertFalse(Path(one["workspace"]).exists(), "workspace is removed after the run unless kept")

    def test_timing_is_measured(self) -> None:
        record = ex.run(self.spec({"input": {"echo": 1}}), workspace_parent=self.root)
        self.assertGreaterEqual(record["timing"]["wall_s"], 0.0)


class JsonAdapter(Sandbox):
    def test_a_parse_failure_is_evidence_not_an_exception(self) -> None:
        record = ex.run(self.spec({"input": {"raw": "not json {"}}), workspace_parent=self.root)
        self.assertEqual(record["status"], "observed")
        self.assertIn("parse_error", record["probes"]["out"])
        self.assertNotIn("value", record["probes"]["out"])

    def test_json_can_be_read_from_a_file(self) -> None:
        probes = [
            {"id": "cli", "adapter": "process", "mandatory": True},
            {"id": "doc", "adapter": "json", "source": "file", "path": "$WORKSPACE/out/doc.json", "mandatory": True},
        ]
        item = {"input": {"write": "doc.json"}}
        record = ex.run(self.spec(item, probes=probes), workspace_parent=self.root)
        self.assertIn("parse_error", record["probes"]["doc"])
        missing = ex.run(self.spec({"input": {}}, probes=probes), workspace_parent=self.root)
        self.assertTrue(missing["probes"]["doc"]["missing"])


class FilesystemAdapter(Sandbox):
    PROBES = [
        {"id": "cli", "adapter": "process", "mandatory": True},
        {"id": "files", "adapter": "filesystem", "root": "$WORKSPACE", "mandatory": True},
    ]

    def test_created_files_are_digested_and_removed_ones_listed(self) -> None:
        item = {"input": {"write": "a.txt", "remove": True}, "initial_state": {"files": {"seed.txt": "seed\n", "keep.txt": "k"}}}
        record = ex.run(self.spec(item, probes=self.PROBES), workspace_parent=self.root)
        files = record["probes"]["files"]
        self.assertEqual(sorted(files["entries"]), ["keep.txt", "out/a.txt"])
        self.assertEqual(len(files["entries"]["out/a.txt"]["digest"]), 64)
        self.assertEqual(files["entries"]["out/a.txt"]["size"], len("hello a.txt\n"))
        self.assertEqual(files["removed"], ["seed.txt"])
        self.assertEqual(files["created"], ["out/a.txt"])

    def test_text_content_can_be_kept(self) -> None:
        probes = [self.PROBES[0], dict(self.PROBES[1], content="text")]
        record = ex.run(self.spec({"input": {"write": "b.txt"}}, probes=probes), workspace_parent=self.root)
        self.assertEqual(record["probes"]["files"]["entries"]["out/b.txt"]["text"], "hello b.txt\n")

    def test_a_probe_root_outside_the_workspace_and_system_root_is_refused(self) -> None:
        probes = [self.PROBES[0], dict(self.PROBES[1], root="$WORKSPACE/../..")]
        record = ex.run(self.spec({"input": {}}, probes=probes), workspace_parent=self.root)
        self.assertEqual(record["status"], "malformed")
        self.assertIn("escapes", record["problems"][0])

    def test_symlinks_are_recorded_not_followed(self) -> None:
        probes = [self.PROBES[0], dict(self.PROBES[1], content="text")]
        outside = self.root / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        item = {"input": {"write": "c.txt"}, "initial_state": {"files": {"plain.txt": "p"}}}
        spec = self.spec(item, probes=probes)
        record = ex.run(spec, workspace_parent=self.root, before_run=lambda ws: os.symlink(outside, Path(ws) / "link.txt"))
        entry = record["probes"]["files"]["entries"]["link.txt"]
        self.assertTrue(entry["symlink"])
        self.assertNotIn("text", entry)
        self.assertNotIn("private", json.dumps(record))


class SqliteAdapter(Sandbox):
    PROBES = [
        {"id": "cli", "adapter": "process", "mandatory": True},
        {"id": "db", "adapter": "sqlite", "path": "$WORKSPACE/app.db", "tables": [{"name": "orders", "order": "ordered"}], "mandatory": True},
    ]

    def test_schema_columns_and_rows_are_captured(self) -> None:
        record = ex.run(self.spec({"input": {"db": [["a", 1], ["b", 2]]}}, probes=self.PROBES), workspace_parent=self.root)
        table = record["probes"]["db"]["tables"]["orders"]
        self.assertIn("CREATE TABLE orders", table["schema"])
        self.assertEqual(table["columns"], ["id", "sku", "qty"])
        self.assertEqual(table["rows"], [{"id": 1, "sku": "a", "qty": 1}, {"id": 2, "sku": "b", "qty": 2}])
        self.assertEqual(table["row_order"], "rowid")

    def test_an_unordered_table_is_snapshotted_in_key_order(self) -> None:
        probes = [self.PROBES[0], dict(self.PROBES[1], tables=[{"name": "orders", "order": "unordered", "key": ["sku"]}])]
        record = ex.run(self.spec({"input": {"db": [["b", 2], ["a", 1]]}}, probes=probes), workspace_parent=self.root)
        table = record["probes"]["db"]["tables"]["orders"]
        self.assertEqual([row["sku"] for row in table["rows"]], ["a", "b"])
        self.assertEqual(table["row_order"], "key:sku")

    def test_a_missing_database_or_table_is_an_observation(self) -> None:
        record = ex.run(self.spec({"input": {}}, probes=self.PROBES), workspace_parent=self.root)
        self.assertTrue(record["probes"]["db"]["missing"])
        probes = [self.PROBES[0], dict(self.PROBES[1], tables=[{"name": "ghost"}])]
        record = ex.run(self.spec({"input": {"db": []}}, probes=probes), workspace_parent=self.root)
        self.assertTrue(record["probes"]["db"]["tables"]["ghost"]["missing"])

    def test_the_snapshot_is_read_inside_one_transaction_and_leaves_no_journal(self) -> None:
        record = ex.run(self.spec({"input": {"db": [["a", 1]]}}, probes=self.PROBES), workspace_parent=self.root, keep_workspace=True)
        workspace = Path(record["workspace"])
        try:
            self.assertEqual(sorted(p.name for p in workspace.iterdir()), ["app.db"])
        finally:
            for p in workspace.iterdir():
                p.unlink()
            workspace.rmdir()

    def test_initial_sqlite_state_can_be_seeded(self) -> None:
        item = {
            "input": {},
            "initial_state": {"sqlite": {"path": "app.db", "sql": ["CREATE TABLE orders (id INTEGER PRIMARY KEY, sku TEXT, qty INTEGER)", "INSERT INTO orders (sku, qty) VALUES ('z', 9)"]}},
        }
        record = ex.run(self.spec(item, probes=self.PROBES), workspace_parent=self.root)
        self.assertEqual(record["probes"]["db"]["tables"]["orders"]["rows"], [{"id": 1, "sku": "z", "qty": 9}])


class HttpAdapter(Sandbox):
    def service_spec(self, requests, **over) -> ex.RunSpec:
        system = {
            "id": "before",
            "kind": "service",
            "command": [sys.executable, "service.py"],
            "root": "$SOURCE_ROOT",
            "service": {"ready": {"http": "/health"}},
        }
        probes = [{"id": "api", "adapter": "http", "requests": "$INPUT", "mandatory": True}]
        return self.spec({"input": {"requests": requests}}, probes=probes, delivery="http", system=system, **over)

    def test_a_sequence_of_requests_is_observed_against_a_local_service(self) -> None:
        requests = [
            {"method": "POST", "path": "/items", "body": {"sku": "a"}},
            {"method": "GET", "path": "/items"},
            {"method": "GET", "path": "/nothing"},
        ]
        record = ex.run(self.service_spec(requests), workspace_parent=self.root)
        self.assertEqual(record["status"], "observed", record["problems"])
        responses = record["probes"]["api"]["responses"]
        self.assertEqual([r["status"] for r in responses], [201, 200, 404])
        self.assertEqual(responses[1]["json"], [{"sku": "a"}])
        self.assertEqual(responses[0]["request"]["method"], "POST")
        self.assertEqual(responses[0]["headers"]["content-type"], "application/json")
        self.assertIn("exit_code", record["service"])
        self.assertIsNotNone(record["service"]["exit_code"], "the service is stopped after the run")

    def test_a_request_to_a_non_loopback_host_is_outside_the_boundary(self) -> None:
        with self.assertRaises(m.ManifestError):
            self.service_spec([{"method": "GET", "url": "http://example.com/"}])

    def test_a_service_that_never_becomes_ready_is_unrunnable(self) -> None:
        system = {
            "id": "before",
            "kind": "service",
            "command": [sys.executable, "app.py"],
            "root": "$SOURCE_ROOT",
            "service": {"ready": {"http": "/health"}},
        }
        probes = [{"id": "api", "adapter": "http", "requests": "$INPUT", "mandatory": True}]
        spec = self.spec({"input": {"requests": []}}, probes=probes, delivery="http", system=system, timeouts={"service_ready_seconds": 2})
        record = ex.run(spec, workspace_parent=self.root)
        self.assertEqual(record["status"], "unrunnable")
        self.assertIn("ready", record["problems"][0])


class Placeholders(unittest.TestCase):
    def test_every_placeholder_resolves(self) -> None:
        text = ex.resolve("$SOURCE_ROOT/x $TARGET_ROOT/y $WORKSPACE/z $PORT", {"SOURCE_ROOT": "/s", "TARGET_ROOT": "/t"}, workspace="/w", port=8080)
        self.assertEqual(text, "/s/x /t/y /w/z 8080")

    def test_an_unresolved_placeholder_is_refused(self) -> None:
        with self.assertRaises(ex.ExecutionError):
            ex.resolve("$SOURCE_ROOT", {}, workspace="/w", port=None)


if __name__ == "__main__":
    unittest.main()
