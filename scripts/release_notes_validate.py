#!/usr/bin/env python3
"""The output-boundary guard of the release-notes pipeline (ADR-0618):
the agent's publishable artifacts — notes.md and slack-payload.json —
are the one place untrusted pull-request prose can reach a public
surface (the release body, the Slack channel) without a human reading
it. The deny-by-default sandbox (ADR-0125 amendment 3) bounds what the
agent can DO, and the workflow's FIREWORKS_API_KEY scan bounds one
secret — this script bounds the CONTENT, for an end-user audience.

Design: presence-refusal plus real parsers. Nothing here emulates a
markdown or URL grammar with string matching — every decode rule and
prefix match is a bypass surface, and three security rounds proved it
(entity spellings of any character, backslash escapes, angle
destinations, userinfo: ``relatedb.ai@evil.example`` IS evil.example,
where a prefix match sees the allowed host and stops). So:

  - Every literal URL candidate is judged by urllib.parse.urlsplit:
    scheme, userinfo, host and port are the parser's fields. Userinfo
    and ports are refused outright (our notes never carry them), the
    host must match the allowlist exactly, and a github.com URL must
    sit under the project's tree. Candidates are judged EVERYWHERE —
    inside code fences too, because a fenced command is a copy-paste
    target on the release page.
  - Outside code fences, material a renderer WOULD decode — an HTML
    entity sequence, a backslash escape of URL-shaping punctuation —
    is refused by presence alone: a legitimate note never carries one
    there (the prompt forbids raw HTML, links are plain markdown, code
    examples live in fences), and no decode is attempted. Inside a
    fence an entity or escape is literal inert text and is allowed.
    Letter escapes (\\t, \\n in an inline code span) stay allowed: they
    cannot shape a URL.
  - Raw HTML tags are refused outside code fences, any tag name;
    angle autolinks (<https://…>) are exempt — they are URLs and are
    judged by the URL rules.
  - Protocol-relative destinations are refused outright: our links are
    absolute.
  - The payload is decoded by json.loads — a real parser — and every
    string it holds is judged by the same rules. A payload that is not
    valid JSON is a hard failure.
  - Internal reference ids (tracker, proposal, decision record) are
    refused anywhere in either artifact.

Over-matching is always fail-closed; under-matching is what the parsers
foreclose.

Usage: release_notes_validate.py --notes NOTES_FILE [--payload PAYLOAD_FILE]

  --notes     required; must exist and be non-empty
  --payload   optional; missing or empty = skip (the workflow owns when
              the payload must exist, ADR-0465); present but not valid
              JSON is a hard failure

Exit 0 = boundary clean; exit 1 = findings on stderr, one ::error::
line each.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

# The allowlist: the project's own GitHub tree and site, plus the
# RFC 2606 reserved placeholder hosts (nothing can be hosted there, so a
# placeholder URL in an example is never a live external link). host →
# the path prefix the URL must sit under ("/" = any path).
ALLOWED_URLS: dict[str, str] = {
    "github.com": "/relatedb",
    "relatedb.ai": "/",
    "www.relatedb.ai": "/",
    "example.com": "/",
    "example.org": "/",
    "example.net": "/",
    "localhost": "/",
}

# Literal URL candidates: scheme forms (case-insensitive — GitHub
# autolinks HTTPS://) and bare www forms. The character class includes
# everything a URL can carry — @ (userinfo), :, /, backslash — because
# truncating a candidate at any of them hides the real host from the
# parser; only whitespace and the markdown terminators end a run.
URL_CANDIDATE = re.compile(
    r"https?://[^\s)>\"`]+|www\.[A-Za-z0-9][^\s)>\"`]*", re.IGNORECASE
)

# Encodable material outside code fences, refused by presence: an HTML
# entity sequence (any spelling — an entity can spell any character),
# or a backslash escape of URL-shaping punctuation (colon, slash, dot,
# at, backslash). Letter escapes cannot shape a URL and stay allowed.
ENCODED_MATERIAL = re.compile(r"&#?[0-9A-Za-z]+;|\\[.:/@\\]")

RAW_TAG = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9-]*")
AUTO_LINK = re.compile(r"<\s*/?\s*https?", re.IGNORECASE)
PROTO_RELATIVE = re.compile(r"\]\(\s*<?//\w|^\s*\[[^\]]+\]:\s*<?//")

INTERNAL_REF = re.compile(r"\b(LABS|CIP|ADR)-[0-9]+\b")

FENCE = re.compile(r"^\s*(```|~~~)")


def url_allowed(candidate: str) -> bool:
    """Judge one literal URL candidate with a real URL parser. Userinfo
    and ports are refused outright (our notes never carry them), the
    host must match the allowlist exactly, and the path must sit under
    the allowed prefix. Scheme-less www forms are judged as https."""
    text = candidate.strip().rstrip(".,;:!?")
    try:
        parsed = urlsplit(text if "://" in text else f"https://{text}")
        hostname = parsed.hostname  # lowercased by the parser
        port = parsed.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False  # userinfo: everything before @ is credentials
    if port is not None:
        return False
    prefix = ALLOWED_URLS.get(hostname or "")
    if prefix is None:
        return False
    if prefix == "/":
        return True
    # The path prefix compare is case-insensitive: github.com/RELATEDB
    # is the project's tree, redirect and all — the HOST is the trust
    # boundary, the prefix only scopes it.
    path = (parsed.path or "/").lower()
    return path == prefix or path.startswith(prefix + "/")


def split_fences(text: str) -> tuple[list[str], list[str]]:
    """Every line of the text, partitioned into (outside, inside) code
    fences. Fence markers are ``` and ~~~ lines; a fence line belongs to
    neither side."""
    outside: list[str] = []
    inside: list[str] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        (inside if fenced else outside).append(line)
    return outside, inside


def check_text(label: str, text: str, *, fence_aware: bool) -> list[str]:
    """The full policy over one artifact's text. ``fence_aware`` marks
    the notes (markdown: fence rules apply); the payload's strings are
    plain text and are judged wholesale."""
    findings: list[str] = []
    if fence_aware:
        outside, inside = split_fences(text)
        lines = outside + inside
    else:
        outside = inside = text.splitlines()
        lines = outside

    # Literal URLs are judged everywhere — fences included.
    for line in lines:
        for match in URL_CANDIDATE.finditer(line):
            candidate = match.group(0)
            if not url_allowed(candidate):
                findings.append(
                    f"{label} carries a URL outside the project's hosts: {candidate}"
                )

    if fence_aware:
        # Encodable material outside code fences: refused by presence,
        # never decoded.
        for line in outside:
            for match in ENCODED_MATERIAL.finditer(line):
                findings.append(
                    f"{label} carries entity-encoded or escaped material "
                    f"outside a code fence: {match.group(0)}"
                )
        # Raw HTML tags outside code fences, any tag name, angle
        # autolinks exempt (judged as URLs above).
        for line in outside:
            for match in RAW_TAG.finditer(line):
                if not AUTO_LINK.fullmatch(match.group(0)):
                    findings.append(
                        f"{label} carries a raw HTML tag outside a code fence: "
                        f"{match.group(0)}"
                    )
        # Protocol-relative destinations resolve against whatever serves
        # the page; our links are absolute.
        for line in outside:
            if PROTO_RELATIVE.search(line):
                findings.append(
                    f"{label} carries a protocol-relative link destination: "
                    f"{line.strip()}"
                )

    for match in INTERNAL_REF.finditer(text):
        findings.append(f"{label} carries an internal reference: {match.group(0)}")
    return findings


def iter_strings(node: object) -> list[str]:
    """Every string value in a decoded JSON document, recursively."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [s for item in node for s in iter_strings(item)]
    if isinstance(node, dict):
        return [s for value in node.values() for s in iter_strings(value)]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The release-notes output-boundary guard (ADR-0618)"
    )
    parser.add_argument("--notes", required=True, type=Path)
    parser.add_argument("--payload", type=Path, default=None)
    args = parser.parse_args(argv)

    notes = args.notes
    if not notes.is_file():
        print(f"::error::release-notes-validate: '{notes}' does not exist", file=sys.stderr)
        return 1
    notes_text = notes.read_text()
    if not notes_text.strip():
        print(f"::error::release-notes-validate: '{notes}' is empty", file=sys.stderr)
        return 1

    findings = check_text("notes", notes_text, fence_aware=True)

    if args.payload is not None and args.payload.is_file() and args.payload.stat().st_size > 0:
        try:
            payload = json.loads(args.payload.read_text())
        except json.JSONDecodeError as exc:
            print(
                f"::error::release-notes-validate: Slack payload is not valid JSON: {exc}",
                file=sys.stderr,
            )
            return 1
        for string in iter_strings(payload):
            findings.extend(check_text("Slack payload", string, fence_aware=False))

    for finding in findings:
        print(f"::error::release-notes-validate: {finding}", file=sys.stderr)
    if findings:
        print(
            f"::error::release-notes-validate: {len(findings)} boundary "
            "violation(s) — refusing to publish",
            file=sys.stderr,
        )
        return 1
    print(
        "release-notes-validate: output boundary clean "
        "(URLs parsed and allowlisted, no encodable material outside "
        "fences, no raw HTML, no internal references)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
