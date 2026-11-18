# 0618. Release notes read a harvested PR corpus and fixed area groups

- **Status**: Accepted
Claim: 28c5791dcaa86db8e03bf5558a7862e4d4508761
- **Date**: 2026-11-17
- **Deciders**: the maintainers
- **Amends**: ADR-0124

## Context

ADR-0124 generates release notes with a sandboxed OpenCode run: the agent
reads the released CHANGELOG.md section, corroborates with the git log,
and its section is prepended to GitHub's own auto-generated pull-request
list. A recent minor release exposed three weaknesses: the changelog
section arrived thin (merged pull requests had filed their entries under
an already-released section, where the release-cut rotation never collects
them, and no check caught it); the multi-step git-log corroboration was
prompted but skipped by the pinned router model; and the auto-generated
list — a hundred uncategorized titles carrying internal tracker ids —
dominated the published page.

## Decision

**Feed the generator a deterministically harvested corpus, organize its
output under a fixed area taxonomy, and guard changelog placement at
pull-request time.**

1. **Corpus harvest.** A trusted workflow step (git log over the tag range
   plus one `gh pr list` call) writes, before the agent runs, a corpus
   file of every merged pull request: number, subject, author, labels, and
   the description's Summary, User-facing impact and Example usage
   sections. The sandboxed agent reads the file; its shell allowlist is
   unchanged. A harvest failure with pull requests in range fails the run
   — no silent thin input. Pull-request descriptions are untrusted,
   post-merge-editable text, so the boundary is enforced at both ends:
   the harvest strips markup that renders as nothing on the pull-request
   page (the agent sees what a reviewer saw, no stealth channel), the
   prompt frames the corpus as data to summarize and never instructions
   to obey, and a validation script refuses the artifacts at the output,
   built on real parsers rather than pattern matching: every literal URL
   is parsed as a URL (userinfo, ports and off-allowlist hosts refused;
   code fences included, a fenced command is a copy-paste target),
   encodable markup outside code fences is refused by presence rather
   than decoded, the payload is decoded by a JSON parser and judged the
   same way, and internal reference ids never reach the page — the
   payload classes that would turn the release page into an attack
   surface.
2. **Fixed area groups.** The generated "Changes by area" section
   organizes every corpus entry under a fixed ten-area table — Performance
   & memory; Cypher language & conformance; CLI; Bindings & clients;
   Servers & protocols; Storage & durability; Agent-friendliness;
   Packaging & compliance; Docs & guides; Internal — canonical in the
   prompt. Every entry lands in exactly one group or the Internal rollup,
   empty groups are checked but omitted, internal work is rolled up never
   itemized, and GitHub's auto-generated list stays on the page below as
   the full audit trail.
3. **Model tier.** Generation — judgment-heavy, public-facing prose — runs
   on a pinned strong model, overridable through a `RELEASE_NOTES_MODEL`
   repository variable or secret, the same override shape the review
   workflows use.
4. **Placement guard.** CI refuses a pull request whose CHANGELOG.md diff
   touches anything but the `[Unreleased]` section (its body and its link
   ref); a `changelog-repair` label is the sanctioned escape for
   correcting the historical record. `[Unreleased]` may carry a `### Notes`
   subsection — one line per notable-but-not-user-facing change — which
   rides the rotation into the released section as maintainer record,
   never as release-notes material (Internal-rollup source only).

## Alternatives considered

- **GitHub release-notes configuration with exclusion labels** — rejected:
  it needs pull-request labeling discipline the flow does not have, and it
  filters without curating or grouping.
- **Stripping the auto-generated list from the release body** — rejected:
  per-pull-request links and the new-contributor section stay valuable;
  the full list remains the audit trail below the curated section.
- **Stronger dig instructions, same router model** — rejected: the dig was
  already instructed and skipped; deterministic harvesting removes the
  multi-step shell work instead of hoping it happens.
- **Requiring a changelog entry in every pull request** — rejected: the
  changelog is a curated user-facing document, not an audit log; most
  merged work belongs in no section.
- **A human reading the notes before publish** — rejected: the pipeline's
  contract (ADR-0124) is automatic publish; the output checks bound the
  dangerous payload classes, and the prose-level residual equals the
  trust the changelog always carried — contributor-written summaries.

## Consequences

One extra `gh pr list` call per release run. The prompt is the canonical
home of the area table and of the end-user voice — internal ids and
vocabulary never reach the page, and the output guard refuses the id
classes — a group change edits one file. Misplaced entries
fail at pull-request time instead of thinning a future release's notes;
releases cut before the guard may need the repair label. The released
changelog section stays the primary input; the corpus is the completeness
backstop, and pull-request descriptions become load-bearing for release
notes, so they are worth writing well.
