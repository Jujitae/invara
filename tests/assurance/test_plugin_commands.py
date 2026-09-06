"""The plugin commands drive the real protocol, not a mood."""

from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

from invara.__main__ import build_parser

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "plugin" / "commands"
INVOCATION = re.compile(r"invara (assure|repair) ([a-z][a-z-]*)")


def subcommands(group: str) -> set[str]:
    parser = build_parser()
    top = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    inner = next(action for action in top.choices[group]._actions if isinstance(action, argparse._SubParsersAction))
    return set(inner.choices)


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, "no frontmatter"
    return dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)


class RepairCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (COMMANDS / "repair.md").read_text(encoding="utf-8")

    def test_it_exists_with_a_description(self) -> None:
        self.assertTrue(frontmatter(self.text)["description"].strip())

    def test_every_invoked_subcommand_exists(self) -> None:
        for group, name in INVOCATION.findall(self.text):
            self.assertIn(name, subcommands(group), f"{group} {name} is not a real subcommand")

    def test_the_protocol_steps_appear_in_order(self) -> None:
        order = ["repair init", "repair characterize", "repair freeze", "repair analyze", "repair plan", "repair unit-start", "repair unit-verify", "repair unit-accept", "repair unit-reject", "repair continue", "repair finish", "repair report"]
        positions = [self.text.find(step) for step in order]
        self.assertTrue(all(p >= 0 for p in positions), dict(zip(order, positions)))
        self.assertEqual(positions, sorted(positions), "the steps are presented in protocol order")

    def test_it_runs_the_bundled_package_without_a_network(self) -> None:
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/src", self.text)
        self.assertIn("python -m invara", self.text)
        for word in ("pip install", "http://", "https://"):
            self.assertNotIn(word, self.text, f"the command must not depend on {word}")

    def test_it_states_the_rules_a_repairer_must_not_break(self) -> None:
        for phrase in ("unit-verify", "worktree", "HUMAN_REVIEW", "UNVERIFIABLE", "amend", "never"):
            self.assertIn(phrase, self.text)
        self.assertIn("PASS", self.text)
        self.assertTrue(re.search(r"(do not|never|cannot).{0,80}(declare|assert|claim).{0,40}PASS", self.text, re.I | re.S), "the repairer is told it cannot declare its own PASS")

    def test_it_puts_the_plain_language_result_in_front_of_the_user(self) -> None:
        self.assertIn("기능 유지", self.text)
        self.assertIn("report", self.text)


class AssureCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (COMMANDS / "assure.md").read_text(encoding="utf-8")

    def test_it_exists_with_a_description(self) -> None:
        self.assertTrue(frontmatter(self.text)["description"].strip())

    def test_every_invoked_subcommand_exists(self) -> None:
        for group, name in INVOCATION.findall(self.text):
            self.assertIn(name, subcommands(group), f"{group} {name} is not a real subcommand")

    def test_the_steps_appear_in_order(self) -> None:
        order = ["assure init", "assure characterize", "assure freeze", "assure compare", "assure search", "assure verify", "assure report"]
        positions = [self.text.find(step) for step in order]
        self.assertTrue(all(p >= 0 for p in positions), dict(zip(order, positions)))
        self.assertEqual(positions, sorted(positions))

    def test_it_explains_every_claim_status(self) -> None:
        for status in ("PROVED_WITHIN_DECLARED_DOMAIN", "PRESERVED_WITHIN_ENVELOPE", "NO_DIVERGENCE_FOUND", "DIVERGED", "UNVERIFIABLE", "HUMAN_REVIEW"):
            self.assertIn(status, self.text)


class ThePluginReadme(unittest.TestCase):
    def test_it_names_the_commands_it_ships(self) -> None:
        readme = (ROOT / "plugin" / "README.md").read_text(encoding="utf-8")
        for command in ("/invara:doctor", "/invara:repair", "/invara:assure"):
            self.assertIn(command, readme)
        self.assertIn("Six tools", readme, "the six-tool MCP surface is unchanged")

    def test_every_command_file_is_named_in_the_readme(self) -> None:
        readme = (ROOT / "plugin" / "README.md").read_text(encoding="utf-8")
        for path in COMMANDS.glob("*.md"):
            self.assertIn(f"/invara:{path.stem}", readme, f"plugin/README.md does not mention /invara:{path.stem}")


if __name__ == "__main__":
    unittest.main()
