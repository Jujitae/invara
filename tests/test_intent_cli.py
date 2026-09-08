from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_intent import project, confirmed  # real, bounded project fixture
from invara.__main__ import main


def test_cli_prepares_review_without_sealing_and_then_records_real_report(project, capsys):
    root, output, db, proposal = project
    source = root.parent / "proposal.json"
    source.write_text(json.dumps(proposal), encoding="utf-8")
    assert main(["intent", "prepare", str(source), "--root", str(root), "--out", str(output)]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["schema"] == "invara.intent-review/1"
    assert (output / "REVIEW.html").is_file()
    assert not db.exists()
    confirmation = root.parent / "confirmation.json"
    confirmation.write_text(json.dumps(confirmed(prepared)), encoding="utf-8")
    assert main(["intent", "seal", str(output), "--confirmation", str(confirmation), "--root", str(root), "--db", str(db)]) == 0
    capsys.readouterr()
    assert main(["intent", "judge", str(output), "--root", str(root), "--db", str(db)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["raw_verdict"]["status"] == "PASS"
    assert result["execution_performed"] is True
    assert (output / "INVARA-RESULT.html").is_file()
    assert main(["intent", "report", str(output), "--root", str(root), "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["execution_performed"] is False
