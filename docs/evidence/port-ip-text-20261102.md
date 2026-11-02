# AI Port address fields require text — 2 November 2026

Two existing helpers passed arbitrary Python objects directly to
`ipaddress.IPv4Address`. That standard-library constructor deliberately accepts
integers, packed four-byte values and existing address objects. It also treats
`True` as integer one. The application contract, however, requires explicit
IPv4 text in private JSON/operator address policies: `true` must not silently
become `0.0.0.1`, nor `3232238081` become `192.168.10.1`.

The fix adds only an explicit string-type guard in the existing candidate
controller/device validator and ingress source validator. Existing parse/error
classes, numeric address/range checks, accepted strings, decoder/network/process
logic and configuration formats otherwise remain unchanged. This is not an
RFC1918-range redesign or ONVIF implementation. No new parsing library is needed.

## Counterexamples and checks

27 direct cases include four formerly coerced nontext forms across both helpers,
three private-text positive controls, actual JSON controller/device and all four
single/pool/live/diagnostic stream-policy paths, ingress rejection before decoder
selection and provision rejection before certificate acquisition/identity
creation. File snapshots remain unchanged on JSON refusal. Each JSON fixture
first validates its complete positive policy before changing only the selected
address; an initially missing pool-detector prerequisite was corrected rather
than counting its unrelated refusal as a type check.

At the unchanged parent, the final fixtures produce **24 failures and three
positive passes**. Parent source imports are verified against a separate owned
public-source stage, not the installed package or modified checkout; the stage
is collected. After the fix all 27 pass. A broader 219-case candidate/ingress/
provision/relay/first-run regression run also passed; the corrected 27-case
fixture was then rerun explicitly. Full exact-head qualification is recorded in
the PR's Mac/Linux receipts, with platform/model skips kept separate.

No actual controller/provider/model/camera request, profile change, identity
creation, service restart, live deployment, new address policy or ONVIF native
claim is introduced by these fixtures. Existing adopted devices stay on their
separately qualified runtime; typed source refusal is not native quality,
recovery, end-to-end installation or general security acceptance.
