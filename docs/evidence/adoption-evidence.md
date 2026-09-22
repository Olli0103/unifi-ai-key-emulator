# AI Key adoption evidence

Static inspection on 2026-09-22 supports building an independent AI Key protocol implementation that uses Protect's normal administrator adoption flow. In the inspected path, trust comes from a controller-issued token and a pinned TLS certificate. No manufacturer-signed device certificate, hardware challenge, serial-number check, or MAC OUI check appears in that path. Acceptance by an unmodified running controller remains `needs_evidence`.

This finding covers adoption. It does not establish that a replacement can complete every AI job, populate Find Anything, or survive upgrades.

## Scope and reproducibility

Only public package files were read. Firmware code was not executed. No controller or local device was contacted, adopted, modified, or scanned. Native ARM64 binaries were inspected with Apple's LLVM objdump. Source offsets below are zero-based UTF-8 byte offsets in the unmodified minified `service.js`.

| Artifact | Version | SHA-256 |
| --- | --- | --- |
| AI Key firmware tar | 2.2.8, OrinNX256 | `f7cd2e12cbe45f1a9c781bb13da8a0c7c23c66d325f8c8dd1bbc162c08f59081` |
| `ui-websocketd` | Package 0.1.47.1 | `829e1e376649fe81c1636cd9ca2aa69b8bf5602f91c9fe3b49477a18003cc503` |
| `ui-httpd` | Package 0.1.7 | `778af590c3a5c72f3ce9710e60df25354b488d079f5974f7aa48e71ca823d2a7` |
| `ubntbox` | Package 0.1.7 | `e16b4d5bae3b440221b7a20fc2bbee19fa732af451c8d787ff6d76956bc23807` |
| Protect backend `service.js` | Protect 7.2.105 | `a7370e9a1b35db67d56104268841b9c2ba16342befb750ffef6654ee2b07bfdb` |

The public firmware URL inspected was [AI Key 2.2.8](https://fw-download.ubnt.com/data/AI-Key/7dbe-OrinNX256-2.2.8-d4ed6b70-79dc-4fdd-8451-4f51d21f3773.tar). The conclusions are specific to these files. Other research in this task references Protect 7.3.60 documentation; that is a different version from the inspected 7.2.105 backend.

Source locations are relative to the independently downloaded packages:

- Firmware: selected extracted packages from AI Key 2.2.8.
- Controller: `usr/share/unifi-protect/app/service.js` inside Protect 7.2.105.
- Local analysis artifacts included disassemblies of `ui-websocketd`, `ui-httpd` and `ubntbox`, plus text extracts of controller functions. These artifacts and vendor binaries are not distributed in this repository.

The annotated disassembly files are navigation aids. Their string comments were generated heuristically; the findings below use the underlying instructions and referenced strings.

## Observed provisioning flow

1. The device exposes discovery and management identity. `ubntbox` contains IPv4 UDP discovery on port 10001 and multicast address `233.89.188.1`. `ui-httpd` exposes `/api/info`, whose JSON includes type, sysid, version, MAC, uptime, PoE type, storage size, and feature flags.
2. Protect's ordinary adoption endpoint checks the requesting user's create permission for the device collection. It looks up the selected device by ID. The adoption workflow also checks whether a different console owns the device.
3. Protect creates a random 32-character token, expires it after one hour, and binds it to the selected MAC and initiating user. It sends management configuration to the device's adoption API with device credentials, controller hosts, token, `protocol:"wss"`, `mode:0`, and console identity/name.
4. `ui-httpd` accepts a JSON POST to `/api/adopt`, verifies the local device username/password, removes those credentials from the stored configuration, validates the configuration, and writes `/tmp/adopt.json`. The watchdog starts the WebSocket worker using the configuration. Layer-3 adoption separately accepts a host and constructs a controller address ending in `:7442`.
5. The device opens TLS/WebSocket to a configured controller host and presents its certificate plus UCP4 headers. The controller either recognizes its pinned certificate or verifies the freshly issued token and saves the presented certificate fingerprint.
6. The device also pins the controller's TLS fingerprint and persists adopted state. Reconnection therefore needs stable device identity, retained private key/certificate, retained configuration, and a matching controller certificate.

The full UDP discovery packet schema was not reconstructed. The layer-2 discovery-to-device-record path also remains `needs_evidence`. The UCP verifier itself can create a recognized device record before rejecting an unadopted connection, but that alone does not prove the complete adoption UI behavior.

## Observed controller acceptance gates

The complete `verifyUcpClient` function starts at byte 2376145 in `service.js`, module 26438. Its relevant order is:

| Gate | Observed behavior |
| --- | --- |
| Rate limit | Rejects an excessive connection rate. |
| TLS certificate | Requires a fingerprint obtained from `req.socket.getPeerCertificate()`. |
| Headers | Requires identity, subprotocol, type, and mode. Mode defaults to `"0"`. |
| Mode | Rejects every value except `"0"`. |
| Subprotocol | Accepts only `ucp4` or `updates`. |
| Model | Looks up the supplied sysid in device manifests, with a type-based fallback. Unknown device type fails. |
| Identity | Normalizes the supplied MAC and finds or creates the model's device record. |
| Initial UCP connection | Rejects a record that is not adopted when there is no token. |
| Known certificate | Accepts a matching stored device fingerprint. |
| Token | Validates token and MAC, then records adopted state and the TLS fingerprint. |
| Existing record without a fingerprint | An already-adopted record can receive its initial fingerprint here. This is observed legacy/state-handling behavior, not a proposed provisioning route. |
| Remaining cases | Rejects fingerprint mismatch. |

Normal token verification removes expired tokens, requires exact token equality and matching MAC when one is bound, and consumes a successful token. A valid reconnect with the same pinned fingerprint takes the earlier certificate branch, so token consumption is not a reconnect prohibition.

The enclosing TLS server also matters. `initWssServer`, module 3802, byte 348330, creates its HTTPS server with `requestCert:true` and `rejectUnauthorized:false`, then installs this verifier. The middleware wires `createClientVerifier` to `initWssServer` at bytes 1587285 and 1587378. This server asks for a client certificate and delegates trust to the token/fingerprint logic; no manufacturer CA verification occurs in this inspected chain. A replacement still needs a real TLS certificate and its private key. Header strings cannot replace TLS possession of that key.

Other exact controller references:

| Function or data | Module / byte offset | Evidence |
| --- | --- | --- |
| `adoptDevices` | 25541 / 2219033 module start | User create permission and device-ID lookup. |
| `adopt` | 76167 / 6246790 module start | Normal management body, local device credentials, MAC-bound token, mode 0. |
| `getToken` | 74079 / 6082154 module start | 32 random characters, one-hour expiry. |
| `verifyToken` | 97158 / 8178330 module start | Expiry cleanup, token/MAC comparison, token consumption. |
| `verifyTokenAndAdopt` | 37600 / 3295038 function start | Token validation followed by saved adopted state and fingerprint. |
| `isConsoleAuthorizedForDevice` | 75482 / 6195266 module start | Existing console ownership check. |
| `getDeviceManifest` | 5822277 function-name start | Maps known sysid to model class. |
| `AI_PROCESSOR_SYSIDS` | 56865 / 4735453 export declaration | Includes `0xa5f0` mapped to `UP-AI-KEY`. |
| `normalizeMAC` | 7685858 function assignment | Separator removal and uppercase normalization, no OUI check. |
| `createClientVerifier` | 1626104 export declaration | Routes UCP4 and updates connections into `verifyUcpClient`. |

## Observed device behavior

Addresses are ELF virtual addresses in the hashed binaries above.

| Binary / function | Address | Evidence |
| --- | --- | --- |
| `ubntbox`, `init_mcast_sock` | `0xb4e0` | IPv4 UDP socket, port 10001 at `0xb57c`, multicast `233.89.188.1`. |
| `ui-httpd`, `httpd_args_parse` | `0x39b0` | Default HTTP 8000 and HTTPS 8080. Its service supplies certificate/key paths. |
| `ui-httpd`, `route__api_adopt` | `0x6a50` | JSON POST, credential verification, configuration validation, temporary adoption file. |
| `ui-httpd`, `json_auth_verify` | `0x53f0` | Local username/password check, followed by removal of those fields. |
| `ui-httpd`, `json_adopt_verify` | `0x54e0` | Requires hosts and protocol. |
| `ui-httpd`, `route__api_adopt_layer3` | `0x6c80` | Host-based provisioning builds WSS target on 7442. |
| `ui-httpd`, `process_watchdog_monitor` | `0x3d50` | Starts WebSocket worker from adoption config; handles persistent adopted configuration. |
| `ui-websocketd`, `ucpv4_cfg_parse` | `0xaac0` | Reads protocol, host/hosts, token, optional key, controller, fingerprint and mode. |
| `ui-websocketd`, `ucpv4_prepare_header_fields` | `0xcd50` | Sends UCP4, adopted state, sysid, type, identity and IP; adds token when set. |
| `ui-websocketd`, `ucpv4_connect` | `0xa210` | Builds controller URL and supplies `/etc/httpd/server.crt` plus `server.key`. |
| `ui-websocketd`, establishment handler | `0x9c20` | Verifies peer fingerprint, marks adopted, then synchronizes time and invokes callback. |
| `ui-websocketd`, `ucpv4_verify_peer_fingerprint` | `0xcf10` | Obtains 32-byte peer fingerprint; stores first value or compares retained value. |
| `ui-websocketd`, `ucpv4_adopted` | `0xe5e0` | Saves adopted state and configuration. No hardware challenge in this function. |

`syswrapper.sh` in `ui-websocketd-0.1.47.1-Linux.deb/data/usr/local/bin` supplies direct certificate-generation evidence at lines 1277-1293. It generates an RSA-2048 key and self-signed X.509 certificate with OpenSSL, then concatenates certificate and key into `server.pem`. Certificate renewal helpers follow. A comment in the inference gateway calls the PEM certificate "NVR-issued". That wording conflicts with this generation code and should not be treated as proof of a manufacturer or controller issuance requirement.

The apparent mode conflict is resolved. The stock worker emits `x-mode:1` and `x-key` only when its configuration contains a key. Otherwise it emits `x-mode:0`. Protect 7.2.105's normal adoption payload supplies mode 0 and no key. Its receiver rejects mode 1. The optional key branch's broader purpose was not established and is not needed for the observed normal flow.

## Inference and remaining evidence

An independent implementation can probably complete native adoption without a genuine AI Key hardware secret. This inference follows from the local self-signed certificate, the explicit TLS server configuration, and the complete token/fingerprint verifier. No missing cryptographic manufacturer credential has been found in that chain.

That statement does not prove that no other component ever validates device identity. Discovery, store/model validators, later device-information responses, license or capability handling, and newer versions were not exhaustively inspected. The code paths examined use a claimed model/sysid and normalized MAC; no OUI, serial, or hardware-attestation gate appeared there.

The normal prototype should use its own test identity and generated key, expose the required management endpoint, appear as a separate candidate device, and receive its token through an authorized administrator adoption action. Reusing a real device's identity, editing controller records, acquiring another device's token, or forging forwarded certificate headers is outside this validation path.

The smallest useful offline fixture would exercise fresh adoption with a synthetic controller-issued token, expiry and MAC mismatch rejection, token consumption, pinned-certificate reconnect, wrong-certificate rejection, no-token pre-adoption rejection, and mode-1 rejection. It must distinguish a fresh token branch from an already-pinned reconnect. Such a fixture validates our reconstruction only. The next interoperability evidence must come from an isolated controller test through the ordinary adoption flow, followed by required device-info/state exchanges and one real AI job. None of those live acceptance checks has run here.
