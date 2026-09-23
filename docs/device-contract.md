# Device management and control contract

This implementation covers a bounded AI Key management, control, and discovery profile. It was written independently from static observations of AI Key firmware 2.2.8 and Protect 7.2.105. Live checks with Protect 7.3.56 on a UDM Pro Max confirmed native discovery/adoption, an online control connection, matching time synchronization, management-password rotation and reconnect after a planned restart. The emulator ran in a built Linux ARM64 image under Apple container 1.4.1.

The matching 7.3.56 controller package was unavailable during inspection. Static source conclusions and live checks therefore have different version scopes. Later Protect 7.3.60 trials verified one native on-demand description and automatic captions on G5 Flex and G4 Instant after a full page reload; see [bounded automatic event descriptions](basic-descriptions.md). Search and database interoperability remain unverified; the Mac test had search and PostgreSQL disabled. Full legacy-camera enhancement, face/plate recognition and audio are not implemented. NAS deployment remains untested.

No vendor executable or script is imported or run. The module never dispatches shell commands, changes the host clock, creates operating-system users, or edits a controller database.

## Integration

```python
service = DeviceService(
    config,
    state_dir,
    job_handler=worker.submit,
    tls_context=verified_client_context,
    queue_status=worker.status,
    credential_handler=optional_database_rotator,
)
app = service.create_app()  # Caller owns aiohttp runners/listeners.
await service.start()      # Starts the outbound control connection.
await service.stop()
```

`job_handler` receives the entire RequestAI command body. It must validate supported target paths, reserve bounded queue capacity, and return an object promptly. That return means admitted, not inference complete. The worker owns retrieval, inference, and result callbacks. An exception produces a failed UCP response. The device never interprets a target URI as a shell command or opens the URI itself.

`queue_status` may return the six observed queue fields or the worker's `{queued, active, ...}` counters. The latter map to `UI_TASK_NUM = queued + active`; unsupported legacy queue categories remain zero. With no provider, `getTaskQueueInfo` fails explicitly.

`credential_handler` is an optional async callback receiving the username and new password. It must finish the configured database rotation before returning, and must tolerate retry. Its failure prevents management-password rotation and acknowledgment. Search enabled without this callback causes password rotation to fail explicitly. Neither the callback nor its arguments are logged by this module.

Required configuration sections are `device`, `controller`, and `runtime`. Identity, device address, management credentials, and controller trust come from the caller. Lab mode restricts the controller and requested management bind to loopback. The caller remains responsible for binding the application to the configured address and using its HTTPS server context.

## Routes and connection

| Interface | Implemented behavior |
| --- | --- |
| `GET /api/info` | Returns the observed identity and capability object. No mutation. |
| `POST /api/info` | Controller-compatible JSON request using current local management credentials. Requires HTTPS in device mode. Returns identity/capabilities without echoing credentials or changing adoption state. |
| `POST /api/adopt` | Requires JSON and correct local management credentials. Device mode requires HTTPS. Validates WSS mode 0 and requires a host matching the independently configured controller. Saves a pending token privately and wakes the control client. |
| Control connection | `wss://<configured host>:<control_port>/`, default port 7442, subprotocol `ucp4`. |
| Initial unadopted connection | Omits the token. An ordinary Protect rejection is expected until administrator adoption supplies a token; the inspected verifier may create the candidate record first. The tested setup used host-side discovery and displayed a native candidate with its management address. |
| Successful WebSocket upgrade | Uses the configured UCP4 negotiation profile and sends a `timeSync` request. An upgrade alone does not change adoption state or consume the pending token. |
| Local control confirmation | Requires a successful `timeSync` response matching the current socket's request ID and `t0`, integer-zero `errorCode`, absent/null or empty-string `error`, valid integer `t0`/`t1`/`t2` and ordered server timestamps. With the same pending adoption token and attempt still current, persists adopted state and removes that token. Without a pending token, time sync only updates clock state. |
| Reconnect | Retains device identity and certificate. It sends adopted state without the consumed token. |

The HTTP adoption success response is a sanitized echo. Stock firmware echoes the original request; this implementation omits the password and token from the HTTP response. A local state file records pending configuration with mode 0600 through an atomic replacement. It records rotated management passwords only as salted PBKDF2-SHA256 hashes. Re-adoption of an already-adopted record is rejected; resetting state is a separate explicit local operation.

The time-sync rule is a local confirmation policy. In the 7.3.56 test, local confirmation was corroborated by the native online device display, credential rotation and a successful reconnect. It does not prove acceptance of AI capabilities. The pending token and attempt counter are captured before connecting. A delayed response from an older attempt cannot consume a replacement token, even if the replacement repeats the same token value. A failed confirmation write restores the pending token in memory. Disconnects preserve previously confirmed adoption.

The default `controller.control_profile` is `ucp4` and requires a negotiated `ucp4` subprotocol. The explicit `device-service` profile permits an absent subprotocol response header only when the handshake carries a pending adoption token or the device was already confirmed adopted. It requires an explicit controller certificate pin in addition to TLS verification. A different named subprotocol remains rejected. Both profiles require binary UCP framing and the same time-sync confirmation before consuming a pending token. The `device-service` profile passed the native 7.3.56 adoption and reconnect checks; the frontend did not return a selected subprotocol. An immediate transport reset preserves pending adoption. Diagnostics record a numeric close code without transport exception text; abnormal closure 1006 triggers exponential reconnect delays, capped at 30 seconds. The service does not interpret a WebSocket upgrade or normal iterator termination as adoption evidence.

New configurations use management username `ui` and a random private password. In inspected Protect 7.2.105, AI processors inherit username `ui`, and the ordinary adoption route forwards a password override without a username override. Supply the generated private password when the controller offers a credentialed adoption flow. For a flow that only submits factory credentials, [temporary factory enrollment](factory-enrollment.md) provides an explicit window of at most ten minutes without replacing the generated password. That enrollment path passed native 7.3.56 adoption and credential rotation; the tested UI had no custom-password field. Existing explicit usernames are preserved; they need a compatible controller username override or deliberate local configuration alignment before adoption. The disconnected information path uses POST JSON in 7.2.105 controller modules 86525 and 92444; the firmware information handler calls `json_auth_verify` at address `0x6858`.

The control headers are `x-ident`, `x-type`, `x-sysid`, `x-ip`, `x-version`, `x-mode:0`, `x-adopted`, and a pending `x-token` when present. The configured client certificate is supplied by the TLS context. The device never accepts controller host redirection from the adoption payload.

`VerifiedConnector` requires certificate verification. It also requires either hostname verification or an explicit SHA-256 certificate pin. Its connection hook checks the optional leaf pin after TLS verification and before aiohttp receives the transport for an HTTP write. Redirects on the control connection are rejected before a second request. Tests verify that a bad pin sends no HTTP request or token and that a redirect cannot forward a token.

The health response includes `device.management` counters for credentialed information and adoption requests, rejection categories, and accepted pending configurations. `last_adoption_result` contains a fixed label only. These in-memory counters reset when the process restarts; they contain no request bodies or credentials. An accepted HTTP adoption request is not proof that the subsequent control connection was accepted.

## UCP requests

UCP frames use the separately implemented two-record JSON codec. Responses correlate by request ID and carry timestamp, response type, error, and errorCode. Unsupported commands return Linux `ENOTSUP`, code 95, with an empty body. Malformed frames close the WebSocket; unsupported event messages are ignored without claiming execution. Duplicate requests with identical bytes share the original result within a bounded in-memory cache. Reusing an ID with changed content is rejected.

| Action | Result or effect |
| --- | --- |
| `getInfo` | `{type,sysid,version,mac,uptime,poeType,storageSize,featureFlags}`. Uptime is local monotonic elapsed time. PoE defaults to `unknown`, dedicated storage size to zero. |
| `getTaskQueueInfo` | `UI_AUDIO_RAM`, `UI_AUTO_FACE_ENHANCE`, `UI_AUTO_RAM`, `UI_AUTO_STT`, `UI_MANUAL_FACE_ENHANCE`, `UI_TASK_NUM`, supplied by the queue provider. |
| `setConsoleInfo` | Saves the bounded controller metadata locally and echoes the body. It does not edit PostgreSQL access rules. |
| `setInfo` | Supports only `{hostname}` as local logical metadata. No OS hostname change. |
| `updateTimezone` | Saves `{timezone}` as local logical metadata. No OS timezone change. |
| `changeUserPassword` | Verifies `{username,passwordOld,passwordNew}`, invokes the optional database callback first, persists the new management-password hash, then echoes the body. |
| `RequestAI` | Validates the wrapper `{targetUri,timeoutMs,payload,resUrl?}`, calls the admission handler, then echoes the body. Job completion arrives through the worker's separate callback. |
| Everything else | Explicit error. Reboot, factory reset, firmware installation, SSH management, support uploads, hardware statistics, and legacy AI command families are not pretended to work. |

The client sends `timeSync` with `{t0: epoch_milliseconds}` after connection. A matching response containing `{t0,t1,t2}` updates a reported offset only; it never changes host time.

Capability flags need care. Protect 7.2.105 fills several missing flags with enabled values. The emulator therefore explicitly disables `supportFaceEnhancement`, `supportRetroactiveProcessing`, `supportAiSummary`, `supportRecognizeAnything`, `supportFaceRecognition`, and `supportLicensePlateRecognition` using `{enabled:false,version:"v1"}`. It defaults `supportDeepMode` and `supportVlm` to false and `aiMode` to `basic`. Explicit `device.feature_flags` overrides are possible, but do not prove the corresponding implementation works.

That controller version normalizes VLM/deep support from `supportDeepMode ?? supportVlm` and writes the result into both flags. There is no verified way in this profile to advertise independent VLM and deep support through those two fields. Advertising full native search before database and embedding interoperability has been demonstrated would be misleading.

## Database credential handoff

The inspected controller has no separate `getDbCredential`, `setDbCredential`, or `setClientCertificate` command. Its password rotation uses the ordinary `changeUserPassword` request. Stock firmware then rotates the PostgreSQL `unifi-protect` role before updating the device's `ui` or `ubnt` account. A prior console capability explicitly set to false preserves the provisioning password; unknown capability rotates because password change precedes the first `setConsoleInfo`.

Protect follows with `{controller:{...,supportsDbCredential:true}}`. Stock firmware keeps the rotated database password and updates PostgreSQL access rules for the controller's advertised addresses. Protect connects to PostgreSQL with its current stored device password, using a provisioning fallback only after an invalid-password failure.

Consequently, a stand-alone PostgreSQL container with an unrelated configured password is not a working native search integration. This build's optional credential callback is the integration point. PostgreSQL TLS, privileges, migration extensions, controller address restrictions, and actual migration/query acceptance remain separate acceptance checks.

## Optional discovery

`DiscoveryService(config, info_provider=service.get_info, adopted_provider=lambda: service.status["adopted"])` supplies async `start()` and `stop()` methods plus a status property. It is disabled unless `discovery.enabled` is true. Configuration supports `bind`, `port`, `multicast`, `interface_ip`, `allowed_controller_ips`, and an optional platform string.

The default bind is loopback and the default port is UDP 10001. Non-loopback operation is restricted to Linux device mode. Multicast membership in `233.89.188.1` requires explicit device mode and enablement. The responder sends no periodic advertisements and accepts queries only from configured controller IPv4 addresses. It rate limits replies per allowed source. Names requiring DNS resolution must supply explicit allowed controller IPv4 addresses.

Only two read-only query shapes are implemented:

- `01 00 00 00`, version 1 information query with empty payload.
- Version 1 command 4 with a six-byte payload equal to this emulator's MAC.

Replies retain the query command and encode a big-endian 16-bit payload length. Each field has a one-byte tag and big-endian 16-bit length. Implemented fields are interface MAC plus IPv4, device MAC, uptime, hostname, platform, factory-default flag, firmware version, and sysid. The inspected ARM64 firmware stores sysid as a raw little-endian 16-bit value. Uptime and factory-default flag are big-endian 32-bit values. The factory-default value is one before adoption and zero afterward.

This is an intentionally bounded discovery profile. Version 0, version 2, mutation opcodes, optional GUID/controller UUID fields, DDC capability bits, and Wi-Fi fields are unsupported. The Mac test demonstrated native candidate discovery and address display using a host-side responder. That result does not establish multicast forwarding through Apple container networking or discovery on the NAS.

## Static evidence

The [research source index](evidence/README.md) records the public package provenance. Vendor packages and private runtime records are not bundled here.

- AI Key `ui-websocketd` 0.1.47.1: `ucpv4_route_on_request` at ELF address `0x10480`, getInfo format string `0x14ab0`; `aikey_route_on_request` at `0x9280`; RequestAI handler `0x7960`; `ucpv4_echo_response` at `0xe130`; time-sync request builder `0xd4f0`.
- `syswrapper.sh`: RequestAI parsing and dispatch lines 3907-4003; credential rotation and console capability handling lines 804-977. The emulator does not execute these routines.
- Protect 7.2.105 backend modules 80745 for AI Key info and capability defaults, 73397 for queue counts, 83767 for console-info payload, 41386 and 42220 for RequestAI dispatch, 53507 for time synchronization, and 63092/12280 for PostgreSQL credential selection.
- `ubntbox` 0.1.7: `mcast_sock_process` at `0xc820`, empty information query dispatch `0xcb40`, response send `0xcc48`; `fill_info_response` at `0xc2f0`; TLV builder `0xb810`; sysid emission `0xbc30`; matching decoder `device_parse` at `0x6b60`, sysid read `0x6e80`. These sender and decoder paths agree on byte order.

Local tests cover serialization against explicit byte fixtures, authenticated management adoption, password rotation, state reload, duplicate command handling, admission errors, verified TLS control exchange, pin rejection before HTTP, redirect refusal, disabled discovery, and real loopback UDP query/reply. Passing them establishes local behavior only.
