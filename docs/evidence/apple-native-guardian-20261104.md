# Apple 1.6.1 native command guardian qualification — 4 November 2026

The current signed Apple 1.6.1 ARM64 CLI is 68,535,328 bytes. Its digest-bound
executor was rejected by the guardian's former 64 MiB artifact limit before
any command was spawned, even for a read-only container listing. The normal
fixed-path CLI and tiny synthetic executors did not expose this mismatch.

The executor hash limit is now 128 MiB. Exact SHA-256 verification, canonical
absolute path checks, command allowlisting, writer lease, owner/deadline
collection and durable effect receipts are unchanged. This is not a general
arbitrary-executable interface or a PATH fallback.

## Evidence

- Official [Apple 1.6.1 release](https://github.com/apple/container/releases/tag/1.6.1)
  signed package: SHA-256
  `cd47846819f06478d067060efe21e516b562ac0a9af8eecad6d5fea0b556b200`.
  Apple installer signature/notarization and extracted CLI code signature passed.
- CLI and live server both reported 1.6.1, commit
  `aa8d76b44a834eee3ebf6a0bc577e376f65f6361`, on macOS 27.0.1 ARM64.
  The runtime used its supported custom application/install/log roots as an
  ordinary user. No system installer, privilege change or firewall change.
- A regression using a sparse artifact matching the official CLI size failed
  on the previous source. Correct digest now admits it; changed digest and an
  artifact above 128 MiB still refuse before execution. The executor in these
  three tests is synthetic; actual hashing/admission is exercised.
- The command-guardian and frozen Apple maintenance tests passed: **22 tests**.
- The corrected real guardian used the exact official CLI, pinned SHA-256
  `ae4c0eecb6ad362b67f97e2168709bb6e6dfd7f506c21825df8a01144110d2e1`,
  for an isolated real Alpine ARM64 container: create, start, stop, start,
  stop, delete. All **six** durable effect receipts were read back. Native
  listings separately confirmed each lifecycle state. A private guest-mounted
  marker survived both starts, and its start ledger contained exactly two
  entries. No containers remained afterward.
- The fixture used no network, published ports, controller connections or model
  requests; rootfs was read-only, capabilities dropped, process non-root.

## Remaining acceptance

This is real Apple runtime/guardian lifecycle evidence with an invented test
workload, **not** complete AI Port creation, adoption, pairing, recording
continuity, reboot recovery or quality acceptance. The original instance issue
[#45](https://github.com/Olli0103/unifi-protect-local-ai/issues/45) remains OPEN.
The actual host preflight separately refused an explicit LAN TCP 443 bind for
an ordinary user. No wildcard listener, privilege bypass or port substitution
was used to relabel that boundary successful. A prepared privileged transport
and actual reserved device slot remain separate native prerequisites.

No third-party implementation or private device data was copied into this
change. Same-agent qualification; no independent review is claimed.
