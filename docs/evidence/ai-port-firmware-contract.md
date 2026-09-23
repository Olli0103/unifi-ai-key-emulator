# AI Port firmware and first native discovery result

This is a read-only interface record for issue [#6](https://github.com/Olli0103/unifi-ai-key-emulator/issues/6). It does not include vendor code, firmware, credentials, camera media, or a claim of AI Port adoption.

## Provenance

[Ubiquiti's public firmware catalog](https://fw-update.ui.com/api/firmware/1dd26036-3ab7-4e12-bdbb-dc5b5ca9ccfe) identifies product `AI-Port`, platform `all`, version `5.1.12`, with SHA-256 `4bdd8d21533cf1b95746d02fd5a63ab563d320f10c73041ef8be03673dcd3f95`. The package from the catalog's [official download URL](https://fw-download.ubnt.com/data/AI-Port/4dcc-all-5.1.12-8f4dc4b3-0aa7-4c3f-90d3-8aa4c568f29b.bin) matched that hash. It was inspected as data in temporary storage. No vendor binary was executed or added to this repository.

| Interface | Evidence | Limit |
| --- | --- | --- |
| AI Port identity is a separate `aiport` model, named `UVC AI Port`, with sysid `0xa5f1`. | Protect 7.2.105 controller modules `53631`, `19157` and `4973` | Live Protect 7.3.60 accepted one unadopted candidate; adopted identity is `needs_evidence`. |
| The bundled web server listens on HTTPS TCP 443 and routes `/api/*` to its management CGI. The current controller posts adoption data to `/api/1.2/manage`. | AI Port 5.1.12 rootfs `/etc/lighttpd.conf` and `/usr/etc/lighttpd/lighttpd.conf`; Protect module `42278` and camera request module `16436` | Exact management response and credential-rotation lifecycle are `needs_evidence`. |
| AI Port's camera client uses the `/camera/1.0/ws` path and `secure_transfer` WebSocket subprotocol. | AI Port 5.1.12 `ubnt_avclient` strings; Protect modules `19157` and `71420` | Message framing, settings and event payloads are `needs_evidence`. |
| The firmware includes the usual UDP 10001 multicast discovery responder. | AI Port 5.1.12 `ubntbox.static` interface strings | The native candidate test below did not require that responder; its necessity for reconnect or other networks is `needs_evidence`. |
| One outbound, certificate-pinned, unadopted WebSocket upgrade to the controller's device service on TCP 7442 returned HTTP 101. The subsequent Protect 7.3.60 Devices view showed a separate AI Port at the probe's advertised address with status **Click to Adopt** while the existing AI Key remained Online. | Bounded local trial on 23 September 2026; pre- and post-trial read-only Protect API and Devices view | This proves candidate registration only. It does not prove adoption, connection, stream ingestion, detection, or camera pairing. |
| An isolated candidate container held the pinned WebSocket open and answered certificate-validated HTTPS health checks on a temporary host TCP 8443. Protect showed **Click to Pair** and **Connecting**, but a Flur pairing attempt returned **Unable to Pair**. No management request reached the temporary 8443 listener. | Bounded Mac trial and Protect screenshots on 23 September 2026 | The device's fixed host HTTPS 443 endpoint was unavailable. Pairing and adoption remain unproved. |

The probe used a newly generated locally administered MAC and self-signed certificate, separate from the existing AI Key. It sent no token, default password, camera stream or model request. It is not included in the public repository because it carries local addresses and a certificate pin.

## Capacity and container listeners

[Ubiquiti's AI Port FAQ](https://help.ui.com/hc/en-us/articles/28315005177239-Protect-AI-Port-FAQs) gives per-device limits by source and resolution and forbids mixing ONVIF and Protect cameras on one AI Port. The independently written `aikey.aiport_deployment` planner reads the existing private camera preflight, groups connected legacy cameras by source, and reserves capacity using those published limits. If the inventory lacks resolution, it reserves 4K capacity until the stream dimensions are verified. It never auto-pairs a camera.

Every planned AI Port needs its own identity, persistent state and reachable host IP. HTTPS 443 is a **fixed listener per AI Port instance**, not a new TCP port for each camera. The existing AI Key keeps its own HTTPS 8080 listener. A host-side UDP 10001 responder can cover multiple identities if discovery proves necessary, since Apple container's published UDP port does not deliver the LAN multicast sender reliably. The observed outbound controller WebSocket uses TCP 7442. Camera stream transport and any additional ports remain `needs_evidence`; the planner deliberately does not claim a complete publish list before that trace exists.

Run the read-only planner with an inventory from `local-aikey inventory`:

```sh
local-aiport-plan --inventory PRIVATE_CAMERA_PREFLIGHT_JSON \
  --ai-key-ip AI_KEY_LAN_IP --ai-port-ip FIRST_AI_PORT_LAN_IP
```

For current automatic camera detection, the planner can fetch Protect's validated inventory directly through the pinned, read-only integration API:

```sh
local-aiport-plan --controller CONSOLE_PRIVATE_IPV4 \
  --api-key-file PRIVATE_PROTECT_API_KEY_FILE \
  --web-trust-file PRIVATE_WEB_TRUST_JSON \
  --web-cert-file PRIVATE_PINNED_WEB_CERT \
  --ai-key-ip AI_KEY_LAN_IP --ai-port-ip FIRST_AI_PORT_LAN_IP
```

The JSON names the number of instances and Apple container TCP publish bindings. Unassigned instances have no binding, so a deployment controller must refuse to start them until it has distinct, conflict-checked IPs. A container cannot add host-published ports to itself after startup; a host deployment controller must refresh inventory and create or reconcile the required instances. The current planner and unadopted candidate are not yet a functional AI Port profile or an automatic container launcher.

The [isolated candidate service](../aiport-candidate.md) supplies HTTPS management and a persistent unadopted WebSocket, but not a host 443 publish or successful adoption. The next native test must make only that fixed management endpoint reachable, observe the request shape without storing credentials, and implement bounded setup authentication before adopted reconnect. Camera pairing waits for a verified device-side message contract and a recorded rollback for the selected camera.
