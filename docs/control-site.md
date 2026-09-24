# Local control site

The control site is a separate loopback HTTP service for the AI Key and AI Port configuration files. It has administrator sign-in, a signed session, CSRF protection, revision-checked saves and write-only API-key replacement. It currently manages the vision provider, model and endpoint for AI Key descriptions and AI Port object detections. The AI Port form also manages classes, score threshold, event cap and API-request cap. It does not manage embeddings, speech, face/plate models, adoption, camera pairing or service restarts yet. Those controls remain open work under [issue #17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17). Claude is not yet an implemented provider.

Run the site in the same filesystem namespace as both processor configurations, with each profile's private state directory mounted at the same absolute path seen by its processor. New key files are written into that profile's state directory with mode 600. Never expose the current loopback listener through a LAN port publish or reverse proxy; LAN administration needs authenticated HTTPS and a separate security review.

Initialize the administrator credential in a local terminal. The command prompts without echoing the password and refuses to overwrite an existing administrator:

```sh
local-aikey-control init --state-dir /private/admin-state
```

Start the site after both profile configs exist:

```sh
local-aikey-control run --state-dir /private/admin-state \
  --aikey-config /private/aikey-state/config.json \
  --aiport-config /private/aiport-state/config.json --port 8765
```

Open `http://127.0.0.1:8765/` on the same host. The AI Port form appears when its config has a `paired_streams` pool. Selecting OpenAI requires a new key or an existing key reference for the same provider and endpoint. A new key never returns to the browser. Saving replaces only the provider settings, preserves the AI Key identity and AI Port identity and paired streams, and displays that the affected service needs a restart. Restart through the deployment's service manager after checking the saved settings. The site does not restart, unpair or re-adopt cameras.

API-backed AI Port detection is still experimental. The motion gate, object output validation and durable request cap have synthetic tests, but real-camera object locations, score calibration and saved Protect events are `needs_evidence`. Do not treat a successful settings save as proof that the selected model is suitable or that Protect accepted its events. See [AI Port candidate](aiport-candidate.md) for the backend configuration and current native evidence.
