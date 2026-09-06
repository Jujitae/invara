"""The evidence store: content-addressed, chained, and unable to forget."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _support import manifest_dict, observation
from invara import store
from invara.assurance import evidence as ev
from invara.assurance import manifest as m

T0 = 1_750_000_000.0


class Sandbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "verify.db"
        self.store = ev.Evidence(self.db)
        self.manifest = m.Manifest.from_dict(manifest_dict())

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class Manifests(Sandbox):
    def test_a_manifest_is_stored_under_its_digest(self) -> None:
        digest = self.store.record_manifest(self.manifest, at=T0)
        self.assertEqual(digest, self.manifest.digest())
        self.assertEqual(self.store.load_manifest(digest), self.manifest)

    def test_recording_the_same_manifest_twice_is_one_row(self) -> None:
        self.store.record_manifest(self.manifest, at=T0)
        self.store.record_manifest(self.manifest, at=T0 + 1)
        self.assertEqual(self.store.verify()["counts"]["assurance_manifest"], 1)

    def test_a_missing_manifest_is_a_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.store.load_manifest("0" * 64)


class Observations(Sandbox):
    def test_raw_and_normalized_records_are_content_addressed(self) -> None:
        raw = observation({"cli": {"exit_code": 0}})
        digest = self.store.record_observation("s1", "raw", "baseline:before:c1:1", raw, at=T0)
        self.assertEqual(digest, m.content_digest(raw))
        self.assertEqual(self.store.load_observation(digest), raw)
        again = self.store.record_observation("s1", "raw", "baseline:before:c1:1", raw, at=T0 + 5)
        self.assertEqual(again, digest)
        self.assertEqual(len(self.store.observations("s1")), 1)

    def test_observations_are_listed_by_session_kind_and_prefix(self) -> None:
        self.store.record_observation("s1", "raw", "baseline:before:c1:1", observation({"cli": {"exit_code": 0}}), at=T0)
        self.store.record_observation("s1", "raw", "search:before:m1:1", observation({"cli": {"exit_code": 1}}), at=T0)
        self.store.record_observation("s1", "normalized", "baseline:before:c1:1", {"record_version": "x", "probes": {}}, at=T0)
        self.store.record_observation("s2", "raw", "baseline:before:c1:1", observation({"cli": {"exit_code": 2}}), at=T0)
        self.assertEqual(len(self.store.observations("s1")), 3)
        self.assertEqual(len(self.store.observations("s1", kind="raw")), 2)
        rows = self.store.observations("s1", kind="raw", prefix="baseline:")
        self.assertEqual([row["run_key"] for row in rows], ["baseline:before:c1:1"])
        self.assertEqual(rows[0]["record"]["probes"]["cli"]["exit_code"], 0)


class Events(Sandbox):
    def test_events_are_appended_in_order_with_their_payload(self) -> None:
        self.store.append_event("s1", "created", "", "CREATED", {"kind": "assure"}, at=T0)
        self.store.append_event("s1", "capture_started", "CREATED", "BASELINE_CAPTURING", {}, at=T0 + 1)
        events = self.store.events("s1")
        self.assertEqual([e["event"] for e in events], ["created", "capture_started"])
        self.assertEqual(events[0]["payload"], {"kind": "assure"})
        self.assertEqual(events[1]["from_state"], "CREATED")
        self.assertEqual(self.store.sessions(), ["s1"])

    def test_events_of_one_session_do_not_leak_into_another(self) -> None:
        self.store.append_event("s1", "created", "", "CREATED", {}, at=T0)
        self.store.append_event("s2", "created", "", "CREATED", {}, at=T0)
        self.assertEqual(len(self.store.events("s1")), 1)
        self.assertEqual(self.store.sessions(), ["s1", "s2"])


class Integrity(Sandbox):
    def _fill(self) -> None:
        self.store.record_manifest(self.manifest, at=T0)
        self.store.record_observation("s1", "raw", "baseline:before:c1:1", observation({"cli": {"exit_code": 0}}), at=T0)
        self.store.append_event("s1", "created", "", "CREATED", {}, at=T0)

    def test_a_fresh_store_verifies(self) -> None:
        self._fill()
        result = self.store.verify()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["counts"], {"assurance_manifest": 1, "assurance_observation": 1, "assurance_event": 1})

    def test_editing_a_stored_observation_is_detected_twice(self) -> None:
        """Both the chain and the content address must object."""

        self._fill()
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE assurance_observation SET record_json = ?", (json.dumps({"forged": True}),))
        connection.commit()
        connection.close()
        result = self.store.verify()
        self.assertFalse(result["ok"])
        self.assertTrue(any("hash mismatch" in p for p in result["problems"]), result)
        self.assertTrue(any("content address" in p for p in result["problems"]), result)

    def test_deleting_an_event_breaks_the_chain(self) -> None:
        self._fill()
        self.store.append_event("s1", "frozen", "CREATED", "BASELINE_FROZEN", {}, at=T0 + 1)
        connection = sqlite3.connect(self.db)
        connection.execute("DELETE FROM assurance_event WHERE seq = 1")
        connection.commit()
        connection.close()
        result = self.store.verify()
        self.assertFalse(result["ok"])
        self.assertTrue(any("chain break" in p for p in result["problems"]), result)

    def test_a_corrupted_store_fails_closed(self) -> None:
        self.store.close()
        self.db.write_bytes(b"this is not a database")
        with self.assertRaises(ev.EvidenceError):
            ev.Evidence(self.db)

    def test_a_dropped_table_fails_closed(self) -> None:
        self._fill()
        self.store.close()
        connection = sqlite3.connect(self.db)
        connection.execute("DROP TABLE assurance_event")
        connection.commit()
        connection.close()
        reopened = ev.Evidence(self.db)
        try:
            with self.assertRaises(ev.EvidenceError):
                reopened.events("s1")
        finally:
            reopened.close()

    def test_it_shares_the_verifier_database_and_its_chain_rule(self) -> None:
        """One local file, one hashing rule, no second truth system."""

        self._fill()
        connection = store.connect(self.db)
        try:
            self.assertTrue(store.verify(connection)["ok"])
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            connection.close()
        self.assertTrue({"contract", "verdict", "assurance_manifest", "assurance_observation", "assurance_event"} <= names)
        source = (Path(ev.__file__)).read_text(encoding="utf-8")
        self.assertIn("from .. import chain", source)
        self.assertNotIn("hashlib.sha256", source)


if __name__ == "__main__":
    unittest.main()
