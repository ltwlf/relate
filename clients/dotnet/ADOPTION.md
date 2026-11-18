# Adopt the .NET client over the typed C ABI

Status: Proposed

## Context

.NET applications need an embedded client with the same engine, typed values,
errors and transaction semantics as the existing language clients. An existing
MIT-licensed implementation provides an idiomatic managed API, source-generated
native bindings and contract tests in C#, F# and Visual Basic. Its standalone
build downloads an older Windows-only native release.

## Decision

Adopt that implementation under clients/dotnet and retain its license and
attribution. Keep its database, connection and transaction API, managed value
mapping and materialized results. Use the documented C ABI v2 exclusively;
there is no second protocol or engine implementation.

Target .NET 8 or later. Build and test against the repository's own native
library on Linux, macOS and Windows. Local builds take an explicit native
library path or the workspace build output, without downloading a release.
Local packages identify the native runtime they contain. Registry publication
and distribution of a combined multi-platform package are separate work.

Safe handles own databases and connections. Connection operations, including
transaction validation, are serialized; a database outlives its native children.
Borrowed result values are copied before their native owner is destroyed.
Reject embedded NUL in C-string inputs rather than silently truncating them;
length-delimited string parameter values retain embedded NUL.

Preserve resolved namespace, query type, counters, notifications, plans, timings
and bookmarks. Document the existing managed limits, including materialized
results, date ranges, tick precision and cancellation only before execution.
Run the adopted three-language tests and documented examples against the same
native source revision in CI.

## Alternatives considered

- Rewrite the client: rejected because the contributed implementation already
  supplies the required managed API and reusable contract coverage.
- Retain the downloaded Windows release: rejected because integration tests
  would not validate this repository's current engine and other host platforms.
- Wrap a network protocol: rejected because embedded clients share the C ABI.

## Consequences

The repository gains a .NET surface, documentation and native compatibility
coverage while retaining the upstream implementation's identity and licensing.
Managed results consume memory proportional to the final answer; callers must
bound queries where that matters. Async calls schedule synchronous native work
and do not interrupt a statement already running. NuGet publication requires
separate coordination of package ownership and release packaging.
