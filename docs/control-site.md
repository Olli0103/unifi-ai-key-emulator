# Local control site

The control site is a separate loopback HTTP service for the AI Key and AI Port configuration files. It has administrator sign-in, a signed session, CSRF protection, revision-checked saves and write-only API-key replacement. It currently manages the vision provider, model and endpoint for AI Key descriptions and AI Port object detections. OpenAI, Claude/Anthropic, Ollama and an OpenAI-compatible endpoint are selectable. The AI Port form also manages classes, score threshold, event cap and API-request cap. It does not manage embeddings, speech, face/plate models, adoption, camera pairing or service restarts yet. Those controls remain open work under [issue #17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

With optional pinned Protect inventory settings, the signed-in site has a read-only Cameras page. Each refresh fetches the current inventory and shows connected G3–G5 targets and whether each camera MAC is in this instance's validated local stream allowlist. An allowlisted camera is not necessarily paired or streaming in Protect. The page never enables processing or sends camera media. If Protect is unavailable or its version changes, it shows an error instead of stale camera data.

The site can run on the host while each processor runs in a container. Give it the host paths to the two private configuration files. It writes new key files into the corresponding host state folder with mode 600 and stores the processor-visible path in the config. AI Key reads its processor state path from `runtime.state_dir`; pass AI Port's processor path with `--aiport-runtime-state-dir`. Both processor mounts must expose the host state folders at those paths. Never expose the current loopback listener through a LAN port publish or reverse proxy; LAN administration needs authenticated HTTPS and a separate security review.

Initialize the administrator credential in a local terminal. The command prompts without echoing the password and refuses to overwrite an existing administrator:

```sh
local-aikey-control init --state-dir /private/admin-state
```

For unattended installation, add `--generate`. This writes a mode-600 `admin-bootstrap-password` file and prints only its path. Move the password into your password manager and delete the plaintext bootstrap file after signing in; keep the password record and signing key. No API key or administrator password is printed by the site.

Start the site after both profile configs exist:

```sh
local-aikey-control run --state-dir /private/admin-state \
  --aikey-config /private/aikey-state/config.json \
  --aiport-config /private/aiport-state/config.json \
  --aiport-runtime-state-dir /state --port 8765 \
  --inventory-controller CONSOLE_PRIVATE_IPV4 \
  --inventory-api-key-file PRIVATE_PROTECT_API_KEY_FILE \
  --inventory-web-trust-file PRIVATE_WEB_TRUST_JSON \
  --inventory-web-cert-file PRIVATE_PINNED_WEB_CERT
```

Open `http://127.0.0.1:8765/` on the same host. The AI Port form appears when its config has a `paired_streams` pool. Selecting OpenAI requires a new key or an existing key reference for the same provider and endpoint. A new key never returns to the browser. Saving replaces only the provider settings, preserves the AI Key identity and AI Port identity and paired streams, and displays that the affected service needs a restart. Restart through the deployment's service manager after checking the saved settings. The site does not restart, unpair or re-adopt cameras.

API-backed AI Port detection is still experimental. The motion gate, object output validation and durable request cap have synthetic tests, but real-camera object locations, score calibration and saved Protect events are `needs_evidence`. Do not treat a successful settings save as proof that the selected model is suitable or that Protect accepted its events. See [AI Port candidate](aiport-candidate.md) for the backend configuration and current native evidence.
