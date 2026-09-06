"""Secrets must not reach evidence or reports, on either side of a comparison."""

from __future__ import annotations

import unittest

from invara.assurance import redaction


class Redaction(unittest.TestCase):
    def test_plain_text_is_untouched(self) -> None:
        text, count = redaction.redact_text("total 3 orders, 2 shipped")
        self.assertEqual((text, count), ("total 3 orders, 2 shipped", 0))

    def test_an_aws_access_key_is_redacted(self) -> None:
        text, count = redaction.redact_text("key AKIAIOSFODNN7EXAMPLE used")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", text)
        self.assertIn("<redacted:", text)
        self.assertEqual(count, 1)

    def test_a_password_assignment_keeps_the_name_and_loses_the_value(self) -> None:
        text, count = redaction.redact_text("connecting with password=hunter2 to db")
        self.assertRegex(text, r"password=<redacted:credential#[0-9a-f]{16}>")
        self.assertNotIn("hunter2", text)
        self.assertEqual(count, 1)

    def test_redaction_is_not_an_equivalence_mechanism(self) -> None:
        """Two different secrets must stay different after redaction; the same secret stays the same."""

        one, _ = redaction.redact_text("password=hunter2")
        same, _ = redaction.redact_text("password=hunter2")
        other, _ = redaction.redact_text("password=hunter3")
        self.assertEqual(one, same)
        self.assertNotEqual(one, other)
        for token in (one, other):
            self.assertNotIn("hunter", token)
            self.assertRegex(token, r"<redacted:credential#[0-9a-f]{16}>")

    def test_the_digest_in_a_token_is_of_the_secret_alone(self) -> None:
        text, _ = redaction.redact_text("key AKIAIOSFODNN7EXAMPLE used")
        self.assertEqual(text, f"key <redacted:aws-access-key#{redaction.fingerprint('AKIAIOSFODNN7EXAMPLE')}> used")
        self.assertEqual(len(redaction.fingerprint("x")), 16)
        self.assertNotEqual(redaction.fingerprint("x"), redaction.fingerprint("y"))

    def test_a_bearer_token_is_redacted(self) -> None:
        text, _ = redaction.redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijk")
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", text)
        self.assertIn("Bearer <redacted:", text)

    def test_a_private_key_block_is_redacted_whole(self) -> None:
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nABCD\n-----END RSA PRIVATE KEY-----"
        text, count = redaction.redact_text("cert:\n" + block + "\nend")
        self.assertNotIn("MIIEow", text)
        self.assertRegex(text, r"<redacted:private-key#[0-9a-f]{16}>")
        self.assertEqual(count, 1)

    def test_a_secret_is_detected_for_the_manifest_gate(self) -> None:
        self.assertTrue(redaction.looks_secret("sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ12345"))
        self.assertTrue(redaction.looks_secret("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijk"))
        self.assertFalse(redaction.looks_secret("UTC"))
        self.assertFalse(redaction.looks_secret("python"))

    def test_nested_values_are_walked(self) -> None:
        value, count = redaction.redact_value({"a": ["token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"], "b": 3})
        self.assertEqual(count, 1)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij", value["a"][0])
        self.assertEqual(value["b"], 3)

    def test_the_input_is_not_mutated(self) -> None:
        original = {"x": "api_key=abcdef123456"}
        redaction.redact_value(original)
        self.assertEqual(original, {"x": "api_key=abcdef123456"})

    def test_short_or_ordinary_words_are_not_treated_as_secrets(self) -> None:
        for text in ("token ring", "the password field is required", "secret garden"):
            self.assertEqual(redaction.redact_text(text), (text, 0), text)


if __name__ == "__main__":
    unittest.main()
