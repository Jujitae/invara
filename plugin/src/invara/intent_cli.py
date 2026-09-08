"""Explicit local review, submission, and evidence commands for intent mappings."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from . import intent
from .runner import DEFAULT_TIMEOUT_S


def _run(args):
    try:
        root = Path(args.root)
        if args.intent_command == "prepare":
            from .intent_view import render_review
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            result = intent.prepare(proposal, root=root, output=Path(args.out))
            (Path(args.out) / "REVIEW.html").write_text(render_review(result, language=args.language), encoding="utf-8")
        elif args.intent_command == "seal":
            result = intent.seal_review(Path(args.review), Path(args.confirmation), root=root, db=Path(args.db))
        else:
            from .intent_view import render_report
            if args.intent_command == "judge":
                result = intent.judge_review(Path(args.review), root=root, db=Path(args.db), timeout_s=args.timeout)
            else:
                result = intent.read_report(Path(args.review), root=root, db=Path(args.db), verdict_seq=args.verdict_seq)
            (Path(args.review) / "INVARA-RESULT.html").write_text(render_report(result, language=args.language), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.intent_command in ("prepare", "seal"):
            return 0
        if result["intent_status"] == "CHECKED_CONDITIONS_MET":
            return 0
        return 1 if result["intent_status"] == "NOT_MET" else 2
    except (intent.IntentError, OSError, ValueError) as exc:
        print(json.dumps({"status": "REFUSED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


def register(sub):
    parser = sub.add_parser("intent", help="review promises and read bound local evidence")
    commands = parser.add_subparsers(dest="intent_command", required=True)
    prepare = commands.add_parser("prepare", help="freeze a proposal and write REVIEW.html without sealing")
    prepare.add_argument("proposal")
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--language", choices=("ko", "en"), default="ko")
    prepare.set_defaults(func=_run)
    for name in ("seal", "judge", "report"):
        command = commands.add_parser(name, help={"seal": "seal with a matching local confirmation", "judge": "execute checks and record observations", "report": "read historical evidence without executing checks"}[name])
        command.add_argument("review")
        command.add_argument("--root", required=True)
        command.add_argument("--db", required=True)
        if name == "seal":
            command.add_argument("--confirmation", required=True)
        else:
            command.add_argument("--language", choices=("ko", "en"), default="ko")
        if name == "judge":
            command.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
        if name == "report":
            command.add_argument("--verdict-seq", type=int, help="read this recorded sequence instead of latest")
        command.set_defaults(func=_run)
