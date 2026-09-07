"""A self-contained evidence package, and its inspection without the store.

``export`` writes one zip file holding everything a session recorded: the
manifests it was defined by, every observation (raw runs, normalized
records, comparisons, freezes, blind-spot scans, reports), the event
history, the verdict the workflow computed at export time, and an index.
Every record carries the content address it had in the store.

``inspect`` opens such a package with no database and no session and asks
the only question that matters six months later: does this package still
support the claim it makes? It recomputes every content address, replays
the event history through the same reducer, re-normalizes the raw records
and re-runs every recorded comparison from them with the same pure
comparator, re-derives the final verdict from the recorded results, and
reports the first thing that disagrees. What it cannot do is run the
programs again: the package holds observations, not the systems, and says
so.

No new dependency: ``zipfile`` and ``json`` from the standard library.
"""

from __future__ import annotations

from .. import exact_json as json
import os
import shutil
import struct
import tempfile
import zlib
import zipfile
from pathlib import Path
from typing import Any, Mapping

from . import claims, proof, session, identity
from . import coverage as coverage_module
from . import report as report_module
from .compare import compare
from .evidence import Evidence
from .http_boundary import observation_problems
from .. import chain
from .manifest import Manifest, content_digest
from .normalize import policy_set_digest, uncovered_volatile_empirical
from .records import COMPARISON_VERSION, OBSERVATION_VERSION

__all__ = ["PACKAGE_VERSION", "export", "inspect"]

PACKAGE_VERSION = "invara.assurance.package/4"
_NOTE = (
    "program execution is not replayed: the package holds the recorded observations, not the systems; "
    "content addresses, the event chain, every comparison and the final verdict are recomputed from those records. "
    "The package is not signed: an inspection that finds no problem shows that the package agrees with itself "
    "and with its own raw records (what was recorded), not who recorded it; a package rewritten consistently "
    "from edited raw records is indistinguishable from a genuine one. The history must end with the export that "
    "produced the package, and every record must be accounted for by that history (a comparison by a claim, a raw "
    "run by a comparison or the capture, a normalized record and a blind-spot scan by the freeze); the store-wide "
    "problems the verdict counted are carried from the exporting store in that export event and cannot be re-checked "
    "here, and a package cut back to an earlier, internally complete state with every later record removed is "
    "indistinguishable from a genuine earlier export without an externally held receipt"
)
_REQUIRED_KINDS = ("verdict", "report", "links", "integrity")

# Export and inspection share these bounds. Inspection treats a package as
# untrusted input; the limits stay below ZIP64 so the classic EOCD is checked before
# ``zipfile`` allocates the central-directory listing or expands a member.
MAX_ARCHIVE_ENTRIES = 4_096
MAX_CENTRAL_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 72 * 1024 * 1024
MAX_MEMBER_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_MEMBER_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_AGGREGATE_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_AGGREGATE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_INSPECTION_PROBLEMS = 100
_MEMBER_READ_CHUNK = 64 * 1024
_EOCD = struct.Struct("<4s4H2LH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_MAX_SEARCH = _EOCD.size + 65_535
_CENTRAL_DIRECTORY = struct.Struct("<4s4B4HL2L5H2L")
_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"


class _ArchiveResourceError(ValueError):
    pass


class _ProblemCollector(list[str]):
    """Keep inspection output bounded while retaining the first concrete findings."""

    def __init__(self, limit: int = MAX_INSPECTION_PROBLEMS) -> None:
        super().__init__()
        self.limit = limit
        self.omitted = 0

    def append(self, problem: str) -> None:
        if len(self) < self.limit - 1:
            super().append(problem)
            return
        self.omitted += 1
        marker = f"{self.omitted} additional inspection problems omitted after the first {self.limit - 1}"
        if len(self) == self.limit - 1:
            super().append(marker)
        else:
            self[-1] = marker

    def extend(self, problems: Any) -> None:
        for problem in problems:
            self.append(str(problem))


def _archive_preflight_problem(path: Path) -> str | None:
    """Read only the bounded EOCD tail before asking ``zipfile`` for its listing."""

    archive_bytes = path.stat().st_size
    if archive_bytes > MAX_ARCHIVE_BYTES:
        return f"archive has {archive_bytes} bytes; limit {MAX_ARCHIVE_BYTES}"
    if archive_bytes < _EOCD.size:
        return None  # the normal BadZipFile path gives the more useful diagnosis
    tail_size = min(archive_bytes, _EOCD_MAX_SEARCH)
    tail_start = archive_bytes - tail_size
    with path.open("rb") as handle:
        handle.seek(tail_start)
        tail = handle.read(tail_size)
    offset = tail.rfind(_EOCD_SIGNATURE)
    if offset < 0 or len(tail) - offset < _EOCD.size:
        return None
    _, disk, directory_disk, entries_on_disk, entries, directory_bytes, directory_offset, comment_bytes = _EOCD.unpack_from(tail, offset)
    if any(value == sentinel for value, sentinel in ((entries_on_disk, 0xFFFF), (entries, 0xFFFF), (directory_bytes, 0xFFFFFFFF), (directory_offset, 0xFFFFFFFF))):
        return "ZIP64 metadata is outside the bounded evidence-package format"
    if disk != 0 or directory_disk != 0 or entries_on_disk != entries:
        return "multi-disk ZIP metadata is outside the bounded evidence-package format"
    if entries > MAX_ARCHIVE_ENTRIES:
        return f"archive declares {entries} entries; limit {MAX_ARCHIVE_ENTRIES}"
    if directory_bytes > MAX_CENTRAL_DIRECTORY_BYTES:
        return f"archive central directory declares {directory_bytes} bytes; limit {MAX_CENTRAL_DIRECTORY_BYTES}"
    if offset + _EOCD.size + comment_bytes > len(tail):
        return None  # malformed layout is handled by zipfile without expanding a member
    eocd_offset = tail_start + offset
    if directory_offset + directory_bytes != eocd_offset:
        return "central directory offset/size does not end at the EOCD"

    # EOCD counts are attacker-controlled. Walk the already byte-bounded central
    # directory with constant memory and re-check every size before ZipFile creates
    # one Python object per entry.
    compressed_total = 0
    uncompressed_total = 0
    actual_entries = 0
    remaining = directory_bytes
    with path.open("rb") as handle:
        handle.seek(directory_offset)
        while remaining:
            if remaining < _CENTRAL_DIRECTORY.size:
                return "central directory ends in a partial entry"
            header = handle.read(_CENTRAL_DIRECTORY.size)
            if len(header) != _CENTRAL_DIRECTORY.size:
                return "central directory is truncated"
            (
                signature,
                _create_version,
                _create_system,
                _extract_version,
                _reserved,
                _flag_bits,
                _compression,
                _modified_time,
                _modified_date,
                _crc,
                compressed,
                uncompressed,
                filename_bytes,
                extra_bytes,
                member_comment_bytes,
                member_disk,
                _internal_attributes,
                _external_attributes,
                local_header_offset,
            ) = _CENTRAL_DIRECTORY.unpack(header)
            if signature != _CENTRAL_DIRECTORY_SIGNATURE:
                return "central directory contains an invalid entry signature"
            variable_bytes = filename_bytes + extra_bytes + member_comment_bytes
            record_bytes = _CENTRAL_DIRECTORY.size + variable_bytes
            if record_bytes > remaining:
                return "central directory entry extends beyond the declared directory"
            if member_disk != 0 or any(value == 0xFFFFFFFF for value in (compressed, uncompressed, local_header_offset)):
                return "ZIP64 or multi-disk member metadata is outside the bounded evidence-package format"
            actual_entries += 1
            if actual_entries > MAX_ARCHIVE_ENTRIES:
                return f"central directory contains more than {MAX_ARCHIVE_ENTRIES} entries"
            if compressed > MAX_MEMBER_COMPRESSED_BYTES:
                return f"central directory entry {actual_entries} declares {compressed} compressed bytes; per-member limit {MAX_MEMBER_COMPRESSED_BYTES}"
            if uncompressed > MAX_MEMBER_UNCOMPRESSED_BYTES:
                return f"central directory entry {actual_entries} declares {uncompressed} uncompressed bytes; per-member limit {MAX_MEMBER_UNCOMPRESSED_BYTES}"
            if compressed == 0 and uncompressed > 0:
                return f"central directory entry {actual_entries} declares {uncompressed} uncompressed bytes from zero compressed bytes"
            if compressed > 0 and uncompressed > compressed * MAX_COMPRESSION_RATIO:
                return f"central directory entry {actual_entries} declares compression ratio {uncompressed / compressed:.1f}:1; limit {MAX_COMPRESSION_RATIO}:1"
            compressed_total += compressed
            uncompressed_total += uncompressed
            if compressed_total > MAX_AGGREGATE_COMPRESSED_BYTES:
                return f"central directory declares {compressed_total} compressed bytes in aggregate; limit {MAX_AGGREGATE_COMPRESSED_BYTES}"
            if uncompressed_total > MAX_AGGREGATE_UNCOMPRESSED_BYTES:
                return f"central directory declares {uncompressed_total} uncompressed bytes in aggregate; limit {MAX_AGGREGATE_UNCOMPRESSED_BYTES}"
            handle.seek(variable_bytes, 1)
            remaining -= record_bytes
    if actual_entries != entries:
        return f"EOCD declares {entries} entries but the central directory contains {actual_entries}"
    return None


def _member_resource_problem(info: zipfile.ZipInfo) -> str | None:
    """Return the first fixed-bound violation declared by one central-directory row."""

    compressed = int(info.compress_size)
    uncompressed = int(info.file_size)
    name = info.filename
    if compressed < 0 or uncompressed < 0:
        return f"archive member {name!r} declares a negative size"
    if compressed > MAX_MEMBER_COMPRESSED_BYTES:
        return f"archive member {name!r} declares {compressed} compressed bytes; per-member limit {MAX_MEMBER_COMPRESSED_BYTES}"
    if uncompressed > MAX_MEMBER_UNCOMPRESSED_BYTES:
        return f"archive member {name!r} declares {uncompressed} uncompressed bytes; per-member limit {MAX_MEMBER_UNCOMPRESSED_BYTES}"
    if compressed == 0 and uncompressed > 0:
        return f"archive member {name!r} declares {uncompressed} uncompressed bytes from zero compressed bytes"
    if compressed > 0 and uncompressed > compressed * MAX_COMPRESSION_RATIO:
        return f"archive member {name!r} declares compression ratio {uncompressed / compressed:.1f}:1; limit {MAX_COMPRESSION_RATIO}:1"
    return None


def _archive_resource_problem(infos: list[zipfile.ZipInfo]) -> str | None:
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        return f"archive contains {len(infos)} entries; limit {MAX_ARCHIVE_ENTRIES}"
    compressed_total = 0
    uncompressed_total = 0
    for info in infos:
        problem = _member_resource_problem(info)
        if problem is not None:
            return problem
        compressed_total += int(info.compress_size)
        uncompressed_total += int(info.file_size)
        if compressed_total > MAX_AGGREGATE_COMPRESSED_BYTES:
            return f"archive members declare {compressed_total} compressed bytes in aggregate; limit {MAX_AGGREGATE_COMPRESSED_BYTES}"
        if uncompressed_total > MAX_AGGREGATE_UNCOMPRESSED_BYTES:
            return f"archive members declare {uncompressed_total} uncompressed bytes in aggregate; limit {MAX_AGGREGATE_UNCOMPRESSED_BYTES}"
    return None


def _read_member_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytearray:
    """Expand one preflighted member without reading beyond its declared/fixed bound."""

    limit = min(int(info.file_size), MAX_MEMBER_UNCOMPRESSED_BYTES)
    payload = bytearray()
    with archive.open(info, "r") as stream:
        while True:
            remaining = limit - len(payload)
            chunk = stream.read(min(_MEMBER_READ_CHUNK, remaining + 1))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > limit:
                raise _ArchiveResourceError(
                    f"archive member {info.filename!r} expanded beyond its declared or configured limit of {limit} bytes"
                )
    if len(payload) != int(info.file_size):
        raise _ArchiveResourceError(
            f"archive member {info.filename!r} expanded to {len(payload)} bytes, not its declared {info.file_size} bytes"
        )
    return payload


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in text)


def _dump(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def _export_members(evidence: Evidence, session_id: str, snap: session.Snapshot,
                    verdict: Any, report: dict[str, Any], store_problems: list[str]) -> tuple[list[tuple[str, bytes]], list[dict[str, Any]]]:
    """Serialize and preflight the exact bytes, including index and ZIP overhead."""
    entries: list[dict[str, Any]] = []
    members: list[tuple[str, bytes]] = []
    infos: list[zipfile.ZipInfo] = []
    directory_bytes = 0
    archive_bytes = _EOCD.size

    def stage(name: str, payload: bytes) -> None:
        nonlocal directory_bytes, archive_bytes
        name_bytes = len(name.encode("utf-8"))
        if name_bytes > 65_535:
            raise _ArchiveResourceError("export member name exceeds the ZIP filename limit")
        # Deflate uses the same defaults and raw stream as ZipFile.writestr.
        compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
        compressed = len(compressor.compress(payload)) + len(compressor.flush())
        info = zipfile.ZipInfo(name)
        info.file_size, info.compress_size = len(payload), compressed
        infos.append(info)
        problem = _archive_resource_problem(infos)
        if problem:
            raise _ArchiveResourceError((f"archive resource limit exceeded: {problem}")[:8192])
        directory_bytes += _CENTRAL_DIRECTORY.size + name_bytes
        archive_bytes += 30 + name_bytes + compressed + _CENTRAL_DIRECTORY.size + name_bytes
        if directory_bytes > MAX_CENTRAL_DIRECTORY_BYTES:
            raise _ArchiveResourceError("export central directory exceeds the archive resource limit")
        if archive_bytes > MAX_ARCHIVE_BYTES:
            raise _ArchiveResourceError("export archive bytes exceed the archive resource limit")
        members.append((name, payload))

    def put(name: str, kind: str, data: Any, digest: str | None = None) -> None:
        payload = _dump(data)
        stage(name, payload)
        entries.append({"name": name, "kind": kind, "digest": digest, "bytes": len(payload), "sha256": content_digest(payload.decode("utf-8"))})

    for item in evidence.manifests(session_id):
        manifest = evidence.load_manifest(item["digest"])
        put(f"manifest/{item['digest']}.json", "manifest", {"digest": item["digest"], "seq": item["seq"], "recorded_at": item["recorded_at"], "manifest": manifest.as_dict()}, item["digest"])
    for row in evidence.observations(session_id):
        name = f"observation/{_safe(row['kind'])}/{row['seq']:06d}-{_safe(row['run_key'])}.json"
        put(name, f"observation:{row['kind']}", {"digest": row["digest"], "seq": row["seq"], "kind": row["kind"], "run_key": row["run_key"], "recorded_at": row["recorded_at"], "record": row["record"]}, row["digest"])
    for event in evidence.events(session_id):
        put(f"event/{event['seq']:06d}-{_safe(event['event'])}.json", "event", event)
    put("chain/links.json", "links", {"session_id": session_id, "links": evidence.chain_links(session_id)})
    # the recorded verdict counts every problem of the store it came from, other sessions included; the package says
    # which, and the export event in the hashed history says the same, so this file cannot be emptied on its own
    put("integrity.json", "integrity", {"store_problems": store_problems, "note": "problems Evidence.verify() reported over the whole store at export time, also recorded in the export event of the hashed history; an inspection re-derives the verdict with them, and cannot see the other sessions itself"})
    put("verdict.json", "verdict", verdict.as_dict())
    put("report.json", "report", report)
    index = {
        "package_version": PACKAGE_VERSION,
        "producer_identity_sha256": (snap.producer_identity or {}).get("identity_sha256"),
        "session_id": session_id,
        "kind": snap.kind,
        "state": snap.state,
        "manifest_digest": snap.manifest_digest,
        "baseline_digest": snap.baseline_digest,
        "entries": entries,
        "note": _NOTE,
    }
    stage("index.json", _dump(index))
    return members, entries


def export(evidence: Evidence, session_id: str, out_path: str | Path) -> dict[str, Any]:
    """Publish a self-inspected package, committing its event only after publication.

    The write lock fixes the evidence snapshot and event-chain head. An export event
    participates in the package while still uncommitted. Handled failures, including
    interruption, roll it back and preserve any previous destination.
    """
    from .workflow import Workflow

    connection = evidence.connection
    if connection.in_transaction:
        raise ValueError("export requires an evidence connection without a pending transaction")
    out = Path(out_path)
    temporary: Path | None = None
    backup: Path | None = None
    published = False
    committed = False
    evidence._execute("BEGIN IMMEDIATE")
    try:
        # Refuse cardinality before materializing evidence or serializing its index.
        count = sum(evidence._execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,)).fetchone()[0]
                    for table in ("assurance_manifest", "assurance_observation", "assurance_event")) + 6
        if count > MAX_ARCHIVE_ENTRIES:
            raise _ArchiveResourceError(f"archive resource limit exceeded: export requires {count} entries; limit {MAX_ARCHIVE_ENTRIES}")
        flow = Workflow(evidence)
        snap = flow.snapshot(session_id)
        verdict = flow.verdict(session_id)
        report = flow.report(session_id)
        store_problems = list(evidence.verify()["problems"])
        evidence.append_event(session_id, "exported", snap.state, flow._check(snap, "exported"), {
            "package_version": PACKAGE_VERSION,
            "producer_identity_sha256": (snap.producer_identity or {}).get("identity_sha256"),
            "verdict": {"status": verdict.status, "decided_by": verdict.decided_by, "reason": verdict.reason},
            "store_problems": store_problems,
            "report_digest": content_digest(report),
        }, at=flow.clock(), commit=False)
        snap = flow.snapshot(session_id)
        members, entries = _export_members(evidence, session_id, snap, verdict, report, store_problems)
        out.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
        temporary = Path(name)
        os.close(descriptor)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
        view = inspect(temporary)
        if not view["ok"]:
            problems = _ProblemCollector()
            problems.extend(view["problems"])
            raise ValueError("export self-inspection refused: " + "; ".join(problems)[:8192])
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        if out.exists():
            descriptor, name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".previous", dir=out.parent)
            backup = Path(name)
            os.close(descriptor)
            shutil.copyfile(out, backup)
        os.replace(temporary, out)
        published = True
        connection.commit()
        committed = True
    except BaseException:
        connection.rollback()
        if published and not committed:
            if backup is not None:
                os.replace(backup, out)
            else:
                out.unlink()
        raise
    finally:
        for leftover in (temporary, backup):
            # If restoration itself failed, retain the old delivery for recovery.
            if leftover is None or (leftover == backup and published and not committed):
                continue
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                # Cleanup cannot retroactively turn a committed publication into
                # failure or conceal the original failure. A private staging file
                # may remain when the filesystem refuses its removal.
                pass
    return {"package_version": PACKAGE_VERSION, "path": str(out), "entries": len(entries), "session_id": session_id, "verdict": verdict.status}


def _accounting_problems(snap: session.Snapshot, events: list[dict[str, Any]], observations: list[dict[str, Any]]) -> list[str]:
    """Every record the package holds is accounted for by its history, and every record the history requires is held.

    A comparison record is named by a claim result on record (a historical
    one counts: it named it when it was made); a baseline raw run belongs
    to a capture or the freeze; any other raw run is used by a comparison
    (timed ``perf:`` runs are summarised by the performance claim, which
    names no record, and are not checked); the freeze is the frozen
    baseline; a stored report is the completion's. The freeze requires one
    normalized record per frozen input, derived from the frozen record under
    the frozen policy set, and one blind-spot scan per manifest in force
    from the freeze on, made over exactly the frozen records. A record
    outside those sets, or a required record missing, is a problem: an
    evaluation cut out of the history, or a scan swapped for another's.
    """

    problems: list[str] = []
    frozen = dict(snap.frozen or {})
    named_by_claims = {str(d) for result in snap.claim_results for d in result.get("evidence_digests", [])}
    referenced_raw = {str(obs["record"].get(key)) for obs in observations if obs["kind"] == "comparison" for key in ("source_raw_digest", "target_raw_digest")}
    baseline_digests = {str(d) for capture in snap.captures for digests in (capture.get("raw_digests") or {}).values() for d in digests}
    baseline_digests.update(str(d) for d in frozen.get("record_digests", {}).values())
    policy16 = str(frozen.get("policy_set_digest", ""))[:16]
    expected_normalized = {f"baseline:{frozen.get('source_system_id', '')}:{input_id}:1:{policy16}": input_id for input_id in frozen.get("inputs", [])} if snap.frozen else {}
    # the manifests in force from the freeze on, in event order: each was scanned when it came into force
    scanned: list[str] = []
    digest = ""
    frozen_seen = False
    for event in events:
        payload = event.get("payload") or {}
        name = event.get("event")
        if name == "created":
            digest = str(payload.get("manifest_digest", ""))
        elif name == "baseline_frozen":
            frozen_seen = True
            scanned.append(digest)
        elif name == "manifest_amended":
            digest = str((payload.get("record") or {}).get("new_digest", digest))
            if frozen_seen:
                scanned.append(digest)
    expected_scans = {f"sensitivity:{d[:16]}": d for d in scanned}
    frozen_records = sorted(str(d) for d in frozen.get("record_digests", {}).values())
    seen_normalized: set[str] = set()
    seen_scans: set[str] = set()
    for obs in observations:
        kind, key, address, record = obs["kind"], str(obs["run_key"]), str(obs["digest"]), obs["record"]
        if kind == "comparison":
            if address not in named_by_claims:
                problems.append(f"comparison record {key} is named by no claim result on record: an evaluation whose result is missing from the history")
        elif kind == "raw":
            if key.startswith("baseline:"):
                if address not in baseline_digests:
                    problems.append(f"raw record {key} belongs to no capture or freeze on record")
            elif not key.startswith("perf:") and address not in referenced_raw:
                problems.append(f"raw record {key} is used by no comparison record in the package")
        elif kind == "freeze":
            if record.get("baseline_digest") != snap.baseline_digest:
                problems.append(f"freeze record {key} is not this session's frozen baseline")
        elif kind == "normalized":
            input_id = expected_normalized.get(key)
            if input_id is None:
                problems.append(f"normalized record {key} belongs to no freeze on record")
            else:
                seen_normalized.add(key)
                if record.get("raw_digest") != frozen.get("record_digests", {}).get(input_id) or record.get("policy_set_digest") != frozen.get("policy_set_digest"):
                    problems.append(f"normalized record for input {input_id} is not derived from the frozen record under the frozen policy set")
        elif kind == "sensitivity":
            expected = expected_scans.get(key)
            if expected is None:
                problems.append(f"blind-spot scan {key} belongs to no manifest this session froze or amended")
            else:
                seen_scans.add(key)
                if record.get("manifest_digest") != expected:
                    problems.append(f"blind-spot scan {key} names another manifest than the one it stands for")
                if sorted(str(d) for d in record.get("record_digests", []) or []) != frozen_records:
                    problems.append(f"blind-spot scan {key} was made over other baseline records than this session's frozen baseline")
        elif kind == "report":
            if address != snap.report_digest:
                problems.append(f"stored report {key} is not the report the completion recorded")
    for key, input_id in expected_normalized.items():
        if key not in seen_normalized:
            problems.append(f"normalized baseline record for input {input_id} ({key}) is missing from the package")
    for key, digest in expected_scans.items():
        if key not in seen_scans:
            problems.append(f"blind-spot scan for manifest {digest[:12]} ({key}) is missing from the package")
    return problems


def _rebuild_report(
    snap: session.Snapshot,
    manifest: Manifest,
    verdict: claims.FinalVerdict,
    observations: list[dict[str, Any]],
    uncovered_volatile: list[str],
) -> dict[str, Any]:
    """The report as the workflow would build it from these records: the same code, the package's own evidence.

    Everything the workflow feeds the report builder is in the package: the
    snapshot the history reduces to (before its export event), the manifest
    in force, the verdict, the frozen baseline records for the coverage map,
    the volatility of the stored runs, the blind-spot scan of the manifest in
    force and the record counts. Only the provenance block (tool versions,
    platform, store path, chain heads) comes from the machine that exported,
    and the comparison leaves it out.
    """

    prefix = f"baseline:{manifest.source_system.id}:"
    raw_records = [
        (obs["run_key"][len(prefix):-2], obs["record"]["probes"])
        for obs in observations
        if obs["kind"] == "raw" and obs["run_key"].startswith(prefix) and obs["run_key"].endswith(":1") and obs["record"].get("status") == "observed"
    ]
    scan = next((obs["record"] for obs in observations if obs["kind"] == "sensitivity" and obs["run_key"] == f"sensitivity:{manifest.digest()[:16]}"), None)
    results = report_module.counted_results(snap.verdict_results(), snap.frozen or {}, observations)
    picture = coverage_module.coverage_map(
        manifest,
        results,
        raw_records=raw_records,
        uncovered_volatile=uncovered_volatile,
        manifest_digest=snap.manifest_digest,
        baseline_digest=snap.baseline_digest,
        units=[dict(record, id=unit_id) for unit_id, record in snap.units.items()],
        blind_spots=[entry["path"] for entry in (scan or {}).get("blind", [])] + [entry["path"] for entry in (scan or {}).get("placeholder", [])],
    )
    counts = {kind: sum(1 for obs in observations if obs["kind"] == kind) for kind in ("raw", "normalized", "comparison", "freeze")}
    return report_module.build(
        snap,
        manifest,
        verdict,
        provenance={},
        extras={"observation_counts": counts},
        results=results,
        uncovered_volatile=uncovered_volatile,
        coverage_map=picture,
        sensitivity=dict(scan) if scan is not None else None,
        observations=observations,
    )


def _first_difference(expected: Any, actual: Any, path: str = "", *, ignore: tuple[str, ...] = ()) -> str | None:
    """The dotted path of the first place ``actual`` disagrees with ``expected``, or None; ``ignore`` names subtrees left out."""

    if path in ignore:
        return None
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            child = f"{path}.{key}" if path else str(key)
            if child in ignore:
                continue
            if key not in actual:
                return f"{child} (missing)"
            if key not in expected:
                return f"{child} (unexpected)"
            found = _first_difference(expected[key], actual[key], child, ignore=ignore)
            if found is not None:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path} (length {len(actual)}, rebuilt {len(expected)})"
        for index, (left, right) in enumerate(zip(expected, actual)):
            found = _first_difference(left, right, f"{path}[{index}]", ignore=ignore)
            if found is not None:
                return found
        return None
    return None if expected == actual else (path or "report")


def inspect(package_path: str | Path) -> dict[str, Any]:
    """Recompute everything the package claims, with nothing but the package."""

    problems = _ProblemCollector()
    checks = {"content_addresses": 0, "events_replayed": 0, "chain_rows": 0, "references_resolved": 0, "claims_cross_checked": 0, "comparisons_recomputed": 0, "comparisons_agree": 0, "verdict_agrees": False, "report_rebuilt": False}
    try:
        inspector_identity = identity.capture()
    except identity.IdentityError as error:
        return {"ok": False, "problems": [str(error)], "checks": checks, "verdict": None, "note": _NOTE, "inspector_identity": None, "identity_match": False}
    manifests: dict[str, Manifest] = {}
    manifest_dicts: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    store_problems: list[str] = []
    kinds_seen: dict[str, int] = {}
    recorded_verdict: dict[str, Any] | None = None
    report_data: dict[str, Any] | None = None
    index: dict[str, Any] = {}
    archive_path = Path(package_path)
    preflight_problem = _archive_preflight_problem(archive_path)
    if preflight_problem is not None:
        return {"ok": False, "problems": [f"archive resource limit exceeded: {preflight_problem}"], "checks": checks, "verdict": None, "note": _NOTE}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            resource_problem = _archive_resource_problem(infos)
            if resource_problem is not None:
                return {"ok": False, "problems": [f"archive resource limit exceeded: {resource_problem}"], "checks": checks, "verdict": None, "note": _NOTE}
            # One archive, one meaning: before any member is believed, the archive listing itself is held to a single
            # canonical member set. A name that appears twice is read differently by different readers (this library
            # takes the last central-directory entry, a streaming reader the first), so no duplicate is ever read here;
            # a member the index does not name, more than one index, a name that is not a plain relative path, or two
            # names that differ only in case (one file after extraction on a case-insensitive filesystem) are each an
            # ambiguity, and an ambiguous package supports nothing.
            listed = [info.filename for info in infos]
            infos_by_name: dict[str, zipfile.ZipInfo] = {info.filename: info for info in infos}
            occurrences: dict[str, int] = {}
            for name in listed:
                occurrences[name] = occurrences.get(name, 0) + 1
            duplicates = {name for name, count in occurrences.items() if count > 1}
            if occurrences.get("index.json", 0) > 1:
                return {"ok": False, "problems": [f"index.json appears {occurrences['index.json']} times in the archive: the package has no single index"], "checks": checks, "verdict": None, "note": _NOTE}
            names = set(occurrences)
            if "index.json" not in names:
                return {"ok": False, "problems": ["index.json is missing"], "checks": checks, "verdict": None, "note": _NOTE}
            for name in sorted(duplicates):
                problems.append(f"archive member {name!r} appears {occurrences[name]} times: which bytes a reader gets depends on the reader")
            for name in sorted(names):
                parts = name.split("/")
                if name.startswith("/") or "\\" in name or any(part in ("", ".", "..") for part in parts):
                    problems.append(f"archive member {name!r} is not a plain relative path (an alias, a directory entry or an escape): refused")
            by_case: dict[str, list[str]] = {}
            for name in names:
                by_case.setdefault(name.casefold(), []).append(name)
            for group in sorted(group for group in by_case.values() if len(group) > 1):
                problems.append(f"archive members {', '.join(sorted(group))} differ only in case: one file after extraction on a case-insensitive filesystem")
            index = json.loads(_read_member_bounded(archive, infos_by_name["index.json"]).decode("utf-8"))
            if index.get("package_version") != PACKAGE_VERSION:
                problems.append(f"unknown package version {index.get('package_version')!r}")
            indexed: dict[str, int] = {}
            for entry in index.get("entries", []):
                indexed[str(entry.get("name"))] = indexed.get(str(entry.get("name")), 0) + 1
            for name, count in sorted(indexed.items()):
                if count > 1:
                    problems.append(f"the index lists {name!r} {count} times")
            if "index.json" in indexed:
                problems.append("the index lists itself as an entry")
            for name in sorted(names - set(indexed) - {"index.json"}):
                problems.append(f"archive member {name!r} is not in the index: every member of a package is accounted for by its index")
            for entry in index.get("entries", []):
                name = entry["name"]
                if name not in names:
                    problems.append(f"{entry.get('kind', 'entry')} entry missing from the archive: {name}")
                    continue
                if name in duplicates:
                    continue  # named above; no duplicate is ever read
                payload = _read_member_bounded(archive, infos_by_name[name])
                if content_digest(payload.decode("utf-8")) != entry.get("sha256"):
                    problems.append(f"{name}: bytes differ from the index (sha256)")
                if entry.get("bytes") != len(payload):
                    problems.append(f"{name}: size differs from the index (bytes)")
                data = json.loads(payload.decode("utf-8"))
                kind = str(entry.get("kind", ""))
                kinds_seen[kind.split(":", 1)[0]] = kinds_seen.get(kind.split(":", 1)[0], 0) + 1
                if kind == "manifest":
                    digest = data["digest"]
                    if content_digest(data["manifest"]) != digest:
                        problems.append(f"manifest {digest[:12]}: content address does not match its manifest")
                    if entry.get("digest") != digest:
                        problems.append(f"{name}: the index names a different digest than the entry carries")
                    checks["content_addresses"] += 1
                    manifest_dicts[digest] = data["manifest"]
                    try:
                        manifests[digest] = Manifest.from_dict(data["manifest"])
                    except Exception as error:  # noqa: BLE001 - a package manifest that no longer validates is a finding
                        problems.append(f"manifest {digest[:12]}: does not validate: {error}")
                elif kind.startswith("observation:"):
                    if content_digest(data["record"]) != data["digest"]:
                        problems.append(f"observation {data['kind']} {data['run_key']}: content address does not match its record ({name})")
                    if entry.get("digest") != data["digest"]:
                        problems.append(f"{name}: the index names a different digest than the entry carries")
                    checks["content_addresses"] += 1
                    observations.append(data)
                elif kind == "event":
                    events.append(data)
                elif kind == "links":
                    links.extend(dict(link) for link in data.get("links", []))
                elif kind == "integrity":
                    store_problems = [str(p) for p in data.get("store_problems", [])]
                elif kind == "verdict":
                    recorded_verdict = data
                elif kind == "report":
                    report_data = data
    except _ArchiveResourceError as error:
        return {"ok": False, "problems": [f"archive resource limit exceeded: {error}"], "checks": checks, "verdict": None, "note": _NOTE}
    except zipfile.BadZipFile as error:
        return {"ok": False, "problems": [f"not a zip archive: {error}"], "checks": checks, "verdict": None, "note": _NOTE}

    if not manifests:
        problems.append("no manifest in the package")
    current_digest = str(index.get("manifest_digest", ""))
    if current_digest and current_digest not in manifests:
        problems.append(f"the manifest in force ({current_digest[:12]}) is not in the package")
    for required in _REQUIRED_KINDS:
        if kinds_seen.get(required, 0) != 1:
            problems.append(f"{required} entry missing from the package" if not kinds_seen.get(required) else f"more than one {required} entry in the package")
    if not events:
        problems.append("no event in the package")

    # the event chain: every event hashes to its recorded hash, and every row links to the one before it
    session_id = str(index.get("session_id", ""))
    events.sort(key=lambda e: int(e.get("seq", 0)))
    rows = sorted([("event", e) for e in events] + [("link", l) for l in links], key=lambda item: int(item[1].get("seq", 0)))
    previous: str | None = None
    previous_seq: int | None = None
    for row_kind, row in rows:
        seq = int(row.get("seq", 0))
        if previous is not None and row.get("prev_hash") != previous:
            problems.append(f"event chain broken between seq {previous_seq} and seq {seq}: a record was removed or rewritten")
        if row_kind == "event":
            payload = {
                "session_id": session_id,
                "event": row.get("event"),
                "from_state": row.get("from_state"),
                "to_state": row.get("to_state"),
                "payload_json": chain.canonical_json(row.get("payload", {})),
                "recorded_at": float(row.get("recorded_at", 0.0)),
            }
            if chain.chain_hash(str(row.get("prev_hash", "")), payload) != row.get("record_hash"):
                problems.append(f"event seq {seq} ({row.get('event')}): the payload does not match its recorded hash")
        checks["chain_rows"] += 1
        previous = str(row.get("record_hash", ""))
        previous_seq = seq

    # the history ends with the export that produced this package, and the free files agree with what that export
    # recorded in the hashed history: the verdict, the store-wide problems it counted, the report
    export_payload: dict[str, Any] = {}
    if events and events[-1].get("event") == "exported":
        export_payload = dict(events[-1].get("payload") or {})
        if export_payload.get("package_version") != PACKAGE_VERSION:
            problems.append(f"the export event names package version {export_payload.get('package_version')!r}, not this package's")
        recorded_at_export = dict(export_payload.get("verdict") or {})
        if recorded_verdict is not None and any(recorded_verdict.get(key) != recorded_at_export.get(key) for key in ("status", "decided_by", "reason")):
            problems.append(f"verdict.json ({recorded_verdict.get('status')}/{recorded_verdict.get('decided_by')}) differs from the verdict recorded at export ({recorded_at_export.get('status')}/{recorded_at_export.get('decided_by')})")
        if [str(p) for p in export_payload.get("store_problems", []) or []] != store_problems:
            problems.append("integrity.json differs from the store-wide problems recorded at export")
        if report_data is not None and content_digest(report_data) != export_payload.get("report_digest"):
            problems.append("report.json differs from the report recorded at export")
    elif events:
        problems.append("the event history does not end with the export that produced this package")

    # the event history must still reduce
    snap: session.Snapshot | None = None
    try:
        snap = session.reduce(events, session_id=session_id)
        checks["events_replayed"] = len(events)
        if snap.state != index.get("state"):
            problems.append(f"the event history reduces to {snap.state}, the index says {index.get('state')}")
        if snap.kind != index.get("kind"):
            problems.append(f"the event history is a {snap.kind} session, the index says {index.get('kind')}")
        if (snap.manifest_digest or "") != current_digest:
            problems.append("the manifest in force according to the event history differs from the index")
        if (snap.baseline_digest or None) != (index.get("baseline_digest") or None):
            problems.append("the frozen baseline according to the event history differs from the index")
    except session.SessionCorrupt as error:
        problems.append(f"the event history does not reduce: {error}")

    producer_identity = snap.producer_identity if snap is not None else None
    identity_problems = identity.validate(producer_identity)
    problems.extend(identity_problems)
    identity_match = False
    if not identity_problems:
        identity_match = all(producer_identity[key] == inspector_identity[key] for key in ("source_version", "implementation_sha256", "runtime_functions_sha256"))
        if not identity_match:
            problems.append("producer verifier implementation differs from current inspector/replayer; replay is not attributable to the producer implementation")
        producer_digest = producer_identity["identity_sha256"]
        if index.get("producer_identity_sha256") != producer_digest or export_payload.get("producer_identity_sha256") != producer_digest:
            problems.append("producer verifier identity differs between creation, export and index")
        if report_data is not None and report_data.get("technical", {}).get("provenance", {}).get("producer_identity") != producer_identity:
            problems.append("report producer verifier identity differs from the event history")
        if report_data is not None and report_data.get("technical", {}).get("provenance", {}).get("tool_versions", {}).get("invara") != producer_identity["source_version"]:
            problems.append("report tool version conflicts with producer verifier source version")

    # every digest the evidence names must resolve inside the package
    obs_by_digest = {obs["digest"]: obs for obs in observations}
    raw_keys = {obs["run_key"] for obs in observations if obs["kind"] == "raw"}
    if snap is not None:
        frozen = snap.frozen or {}
        if snap.baseline_digest:
            if not any(obs["kind"] == "freeze" and obs["record"].get("baseline_digest") == snap.baseline_digest for obs in observations):
                problems.append("the frozen baseline record is not in the package")
            for input_id, digest in sorted(dict(frozen.get("record_digests", {})).items()):
                if digest in obs_by_digest:
                    checks["references_resolved"] += 1
                else:
                    problems.append(f"baseline record for input {input_id} ({str(digest)[:12]}) is not in the package")
            runs = int((snap.captures[-1] if snap.captures else {}).get("runs", 1) or 1)
            prefix = f"baseline:{frozen.get('source_system_id', '')}:"
            for input_id in frozen.get("inputs", []):
                for run in range(1, runs + 1):
                    if f"{prefix}{input_id}:{run}" not in raw_keys:
                        problems.append(f"baseline run {run} of input {input_id} is missing from the package")
        for result in snap.verdict_results():
            named = [str(d) for d in result.get("evidence_digests", [])]
            missing = [d for d in named if d not in obs_by_digest]
            for digest in missing:
                problems.append(f"claim {result.get('claim_id')}: evidence {digest[:12]} is not in the package")
            checks["references_resolved"] += len(named) - len(missing)
            if result.get("kind") not in claims.COMPARING_KINDS:
                continue
            checks["claims_cross_checked"] += 1
            comparisons = [obs_by_digest[d]["record"] for d in named if d in obs_by_digest and obs_by_digest[d]["kind"] == "comparison"]
            diverging = [rec for rec in comparisons if not rec.get("mandatory_equivalent", True) or rec.get("status") != "compared"]
            status = result.get("status")
            if status in (claims.PRESERVED_WITHIN_ENVELOPE, claims.PROVED_WITHIN_DECLARED_DOMAIN, claims.NO_DIVERGENCE_FOUND):
                if diverging:
                    problems.append(f"claim {result.get('claim_id')}: recorded as {status} but {len(diverging)} of {len(comparisons)} comparison record(s) it names show a mandatory divergence or an incomplete comparison")
                elif result.get("kind") in ("corpus_equivalence", "finite_domain_proof") and not comparisons:
                    problems.append(f"claim {result.get('claim_id')}: recorded as {status} but names no comparison record in the package")
            elif status == claims.DIVERGED and comparisons and not diverging:
                problems.append(f"claim {result.get('claim_id')}: recorded as diverged but none of the {len(comparisons)} comparison record(s) it names shows a mandatory divergence")
            if result.get("kind") == "finite_domain_proof" and status == claims.PROVED_WITHIN_DECLARED_DOMAIN:
                # a proof is held to its evidence here as it was before it was recorded: every member bound, by
                # the digest of its exact input, to the source and target executions its comparison was made from,
                # and its source execution is the member's frozen baseline record
                under = manifests.get(str(result.get("manifest_digest", "")))
                if under is None or under.input_domain.finite is None:
                    problems.append(f"claim {result.get('claim_id')}: recorded as {status} under a manifest that is not in the package or declares no finite domain")
                else:
                    problems.extend(
                        proof.binding_problems(
                            under.input_domain.finite,
                            named,
                            lambda digest: obs_by_digest[digest]["record"] if digest in obs_by_digest else None,
                            source_id=under.source_system.id,
                            target_id=under.target_system.id,
                            label=str(result.get("claim_id")),
                            max_members=under.budgets.finite_max_members,
                            frozen_record_digests={str(k): str(v) for k, v in dict(frozen.get("record_digests", {})).items()},
                        )
                    )
            if result.get("kind") == "counterexample_search":
                # a search names every comparison it made: the runs it counts and the records it names agree
                coverage = result.get("coverage") or {}
                runs, shrink_runs = int(coverage.get("runs", 0) or 0), int(coverage.get("shrink_runs", 0) or 0)
                if len(named) != runs + shrink_runs:
                    problems.append(f"claim {result.get('claim_id')}: names {len(named)} comparison record(s) but its coverage counts {runs} runs and {shrink_runs} shrink runs")

    # every record is accounted for by the history: a comparison by a claim result on record, a raw run by the
    # capture (baseline) or by a comparison, a freeze by the frozen baseline, a stored report by the completion;
    # the freeze also names exactly which normalized records and blind-spot scans must be here, and over what
    if snap is not None:
        problems.extend(_accounting_problems(snap, events, observations))

    # every comparison, recomputed from the raw records under the manifest it names (exclusions and budgets shape a
    # comparison as much as the policy set does, so the policy-set digest alone would pick the wrong manifest)
    raw_by_digest = {obs["digest"]: obs["record"] for obs in observations if obs["kind"] == "raw"}
    if current_digest in manifests:
        for raw in raw_by_digest.values():
            problems.extend(observation_problems(raw, manifests[current_digest], require_responses=False))
    for obs in observations:
        if obs["kind"] != "comparison":
            continue
        stored = obs["record"]
        source = raw_by_digest.get(stored.get("source_raw_digest"))
        target = raw_by_digest.get(stored.get("target_raw_digest"))
        manifest = manifests.get(str(stored.get("manifest_digest", "")))
        if manifest is None:
            problems.append(f"comparison {obs['run_key']}: the manifest it was made under is not in the package")
            continue
        if policy_set_digest(manifest.accepted_policies()) != stored.get("policy_set_digest"):
            problems.append(f"comparison {obs['run_key']}: its policy set is not the policy set of the manifest it names")
            continue
        if source is None or target is None:
            problems.append(f"comparison {obs['run_key']}: its raw records are not in the package")
            continue
        checks["comparisons_recomputed"] += 1
        recomputed = compare(source, target, manifest).as_dict()
        # the stored record is the comparator's output plus its record version;
        # every field the comparator produces must come back identical
        keys = sorted(k for k in recomputed if recomputed.get(k) != stored.get(k))
        if not keys and stored.get("record_version", COMPARISON_VERSION) == COMPARISON_VERSION:
            checks["comparisons_agree"] += 1
        else:
            problems.append(f"comparison {obs['run_key']}: recomputed result differs from the recorded one in {', '.join(keys) or 'record_version'}")

    # the verdict, re-derived from the recorded results under the manifest in force
    verdict_view: dict[str, Any] | None = None
    if snap is not None and current_digest in manifests:
        manifest = manifests[current_digest]
        results = [claims.ClaimResult.from_dict(r) for r in snap.verdict_results()]
        baseline_runs: dict[str, list[dict[str, Any]]] = {}
        prefix = f"baseline:{manifest.source_system.id}:"
        for obs in observations:
            if obs["kind"] == "raw" and obs["run_key"].startswith(prefix) and obs["record"].get("status") == "observed" and obs["record"].get("record_version") == OBSERVATION_VERSION:
                head, _, run = obs["run_key"][len(prefix):].rpartition(":")
                baseline_runs.setdefault(head, []).append(obs["record"])
        volatile: set[str] = set()
        for records in baseline_runs.values():
            if len(records) > 1:
                volatile.update(uncovered_volatile_empirical([r["probes"] for r in records], manifest.policies, [{"workspace": r.get("workspace"), "root": r.get("root")} for r in records]))
        recomputed_verdict = claims.final_verdict(
            [claim.as_dict() for claim in manifest.claims],
            results,
            integrity_problems=list(problems) + list(store_problems),
            constraint_breaks=[],
            human_review=[f"{item.id}: {item.reason}" for item in manifest.human_review],
            post_divergence_amendments=list(snap.post_divergence),
            weakening_amendments=list(snap.weakening),
            volatile_unaccepted=sorted(volatile),
            manifest_digest=snap.manifest_digest,
            baseline_digest=snap.baseline_digest,
        )
        verdict_view = recomputed_verdict.as_dict()
        if recorded_verdict is None:
            problems.append("no recorded verdict in the package")
        else:
            agrees = (recomputed_verdict.status, recomputed_verdict.decided_by) == (recorded_verdict.get("status"), recorded_verdict.get("decided_by"))
            checks["verdict_agrees"] = agrees
            if not agrees:
                problems.append(f"recomputed verdict {recomputed_verdict.status}/{recomputed_verdict.decided_by} differs from the recorded {recorded_verdict.get('status')}/{recorded_verdict.get('decided_by')}")
        # the user-facing report is what a reader trusts: it is rebuilt here from the package's own records with the
        # code that wrote it (the snapshot before the export event, the verdict the records support with the carried
        # store problems, the coverage map from the frozen records, the scan) and compared field by field, provenance
        # aside; a headline, a question, a claim's strength or a coverage line that says more than the records do is named
        if report_data is not None:
            before_export = events[:-1] if events and events[-1].get("event") == "exported" else events
            try:
                snap_before = session.reduce(before_export, session_id=session_id) if before_export else None
            except session.SessionCorrupt:
                snap_before = None
            if snap_before is not None:
                verdict_for_report = claims.final_verdict(
                    [claim.as_dict() for claim in manifest.claims],
                    [claims.ClaimResult.from_dict(r) for r in snap_before.verdict_results()],
                    integrity_problems=list(store_problems),
                    constraint_breaks=[],
                    human_review=[f"{item.id}: {item.reason}" for item in manifest.human_review],
                    post_divergence_amendments=list(snap_before.post_divergence),
                    weakening_amendments=list(snap_before.weakening),
                    volatile_unaccepted=sorted(volatile),
                    manifest_digest=snap_before.manifest_digest,
                    baseline_digest=snap_before.baseline_digest,
                )
                try:
                    rebuilt = _rebuild_report(snap_before, manifest, verdict_for_report, observations, sorted(volatile))
                except Exception as error:  # noqa: BLE001 - a report that cannot be rebuilt from these records is a finding, not a crash
                    problems.append(f"report.json could not be rebuilt from the package's own records: {type(error).__name__}: {error}")
                else:
                    difference = _first_difference(rebuilt, report_data, ignore=("technical.provenance",))
                    if difference is None:
                        checks["report_rebuilt"] = True
                    else:
                        problems.append(f"report.json differs from the report rebuilt from the package's own records at {difference}")
                        verdict_view = claims.final_verdict(
                            [claim.as_dict() for claim in manifest.claims],
                            results,
                            integrity_problems=list(problems) + list(store_problems),
                            constraint_breaks=[],
                            human_review=[f"{item.id}: {item.reason}" for item in manifest.human_review],
                            post_divergence_amendments=list(snap.post_divergence),
                            weakening_amendments=list(snap.weakening),
                            volatile_unaccepted=sorted(volatile),
                            manifest_digest=snap.manifest_digest,
                            baseline_digest=snap.baseline_digest,
                        ).as_dict()
    try:
        identity.assert_current(inspector_identity)
    except identity.IdentityError as error:
        problems.append(f"inspector changed during replay: {error}")
        verdict_view = None
        identity_match = False
    return {"ok": not problems, "problems": problems, "checks": checks, "verdict": verdict_view, "recorded_verdict": recorded_verdict, "store_problems": store_problems, "session_id": index.get("session_id"), "note": _NOTE, "producer_identity": producer_identity, "inspector_identity": inspector_identity, "identity_match": identity_match}
