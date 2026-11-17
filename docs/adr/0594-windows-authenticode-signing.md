# ADR-0594: Sign the Windows CLI binary with Authenticode

Status: Proposed
Claim: 96e3165fe8152d7f944ebce7950cd0623508d858

## Context

The release pipeline (`.github/workflows/release.yml`, per ADR-0067) builds
the CLI for `x86_64-pc-windows-msvc` on a Windows runner and publishes the
raw executable `relate-windows-amd64.exe` behind the `SHA256SUMS` manifest
authenticated by ADR-0115's SSHSIG signature. The darwin legs sign and
notarize their binaries (ADR-0123); the Windows executable ships unsigned.

Windows consumers consult none of that: an unsigned executable triggers
the "unknown publisher" prompt in Microsoft Defender SmartScreen, is
blocked by common enterprise download policy, and shows no publisher in
file properties. SSHSIG authenticates only the checksum manifest — the
same complementarity ADR-0123 records for Gatekeeper: the platform's own
check assesses the binary, so the binary must carry the platform's own
signature.

One constraint shapes the mechanism: the organization's code-signing
keypairs are non-exportable and held by the certificate authority's key
management (DigiCert Software Trust Manager) — no export can be produced,
so the signing design must drive that remote key management instead.

The decision below was settled by the maintainers before this ADR was
written; this record states it rather than reopening it.

## Decision

**Sign `relate-windows-amd64.exe` at release time with an Authenticode
signature carrying an RFC 3161 timestamp, produced remotely by DigiCert
Software Trust Manager through DigiCert's official signing action, using
one shared composite action in the windows leg of `release.yml`.**

1. **One shared composite action.** A single composite action at
   `.github/actions/windows-sign/action.yml` performs the signing,
   invoked by the windows leg of the `cli` job after the "Rename binary"
   step and before the artifact upload — the same invocation-point
   contract as ADR-0123 — so only signed bytes leave the runner once the
   credentials exist. Scope is the CLI executable only; the FFI DLL,
   wheels, and every other artifact stay unsigned.
2. **Remote signing through DigiCert's official action.** The sign step
   runs DigiCert's public signing action
   (`digicert/code-signing-software-trust-action`) in simple-signing
   mode, pinned by full commit SHA with the release it names — never a
   floating tag, the rule every third-party action in the release
   workflows follows. The action installs and runs nothing but the
   `smctl` client, which authenticates a DigiCert ONE API user from the
   `SM_*` step env (the documented CI contract,
   <https://docs.digicert.com/en/software-trust-manager.html>) and
   signs remotely: no third-party signing tool, no KSP library, no
   Windows certificate-store sync. The private key never leaves
   DigiCert's key management, and no code-signing certificate material
   is decoded to disk anywhere in the action.
3. **Identity: alias selects, fingerprint verifies.** The action selects
   the keypair by its alias — its only keypair input. The expected
   certificate is pinned by its SHA-1 fingerprint, a repository variable
   the runner-side verification enforces, so a renewal under the same
   alias is an explicit fingerprint change, not a silent publisher
   switch.
4. **RFC 3161 timestamp, SHA-256 digest.** Timestamping stays enabled in
   the sign call, so the signature remains valid after the certificate
   expires.
5. **Verify on the runner.** The action verifies the signed executable
   with `Get-AuthenticodeSignature`: the status must be `Valid`, a
   time-stamper certificate must be present, and the signer certificate's
   thumbprint must equal the pinned fingerprint, both compared as
   uppercase hex without spaces. A failed verification fails the leg
   before any upload.
6. **Absent credentials: skip loudly, bytes unchanged.** While the API
   key is unset the action prints a prominent warning and exits 0,
   leaving the binary untouched and the release green — a deliberate
   asymmetry with ADR-0123's fail-closed rule, so the pipeline can run
   before the credentials are acquired; the skip is loud, never silent.
7. **Credentials present: fail closed.** Once the API key is set, a
   missing companion credential, any smctl failure — including a user
   not authorized as a signer on the keypair — or a failed verification
   fails the leg; no unsigned Windows binary ships from a green run.
8. **Secret hygiene.** The API user's client-authentication certificate
   is the one credential smctl needs as a file: the caller decodes it
   from a base64 secret into the runner temp dir and deletes it in an
   `always()` cleanup; the action never writes certificate material to
   disk, and no credential is ever echoed.

## Alternatives considered

- **A local certificate bundle in repository secrets — the exportable
  `.pfx` design this ADR first recorded** — rejected: the keypairs are
  non-exportable and held by the certificate authority's key management,
  so no exportable bundle exists to store; remote signing also keeps the
  signing key off the runner and out of the secret store entirely.
- **A hand-rolled composite action installing and driving `smctl`
  itself, the client's version and SHA-256 pinned as constants** —
  rejected: DigiCert maintains an official action for exactly this
  workflow, and a vendor-maintained action is the surface that tracks
  the service's client tooling. The honest trade-off: the official
  action downloads its client from DigiCert's CDN against a checksum
  from that same origin — weaker than an independent pin — compensated
  by the pinned action ref and the runner-side thumbprint verification,
  which still prove which action ran and that the signed bytes came from
  the pinned certificate.
- **Traditional signtool signing through smctl (KSP library +
  certificate-store sync)** — rejected: it puts the KSP library and a
  synced certificate on the build machine for no capability this use
  needs; simple signing signs the same file types with the client alone.
- **Fail closed on the absent credential, as ADR-0123 does** — rejected:
  that rule landed alongside existing credentials; gating every release
  on credentials not acquired yet would block releases until they exist,
  and the skip path makes the gap visible instead.
- **A different managed signing service (e.g. Azure Trusted Signing)** —
  rejected: the organization's code-signing certificates are issued and
  held by DigiCert's key management; a second provider would mean
  re-issuance and a second credential system for one platform's leg.
- **Sign every Windows artifact (FFI DLL, installers)** — rejected: scope
  was settled as the CLI executable only; extending it requires a new
  ADR referencing this one.
- **Keep the executable unsigned** — rejected: SmartScreen and enterprise
  policy assess the binary, not the manifest, so SSHSIG cannot satisfy
  them — the same argument ADR-0123 records for Gatekeeper.

## Consequences

- While the DigiCert credentials are unset, releases ship an unsigned
  Windows executable — a stated, visible state (a workflow warning on
  every release) that ends the moment they are set.
- Once the credentials exist, the windows leg is fail-closed: a missing
  companion credential, an unauthorized signer, an expired certificate,
  or a failed verification blocks the release before tagging.
- Three secrets and three variables join the load-bearing release
  credentials (API key, client certificate and its password; service
  host, keypair alias, certificate fingerprint); custody, setup, and
  rotation are documented in `RELEASING.md`, mirroring ADR-0115/ADR-0123.
- The runner holds no code-signing certificate material: the private key
  stays in DigiCert's key management, and the one local credential file
  — the client certificate — is created and deleted within the job.
- The windows leg now depends on DigiCert's service being reachable and
  the API user being an authorized signer — an external coupling of the
  same class ADR-0123 records for Apple's notary service. The signing
  action stays on its pinned commit until the SHA is bumped explicitly
  (`RELEASING.md`); the client tool itself then moves with the action.
- The timestamped signature outlives the certificate's validity window;
  a revoked certificate still invalidates signatures made with it, and
  renewal is a service-side change pinned by updating the fingerprint
  variable.
- The published artifact remains the raw executable behind the
  `SHA256SUMS`/SSHSIG contract (ADR-0115): no new release asset, and no
  change to the installer contract (ADR-0068) or `upgrade` (ADR-0069).
