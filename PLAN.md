# AI Key and AI Port compatibility and open-source product roadmap

Build an independent, maintainable open-source processor that implements the native Protect workflows of both AI Key and AI Port, with a local web application for providers, models, cameras and operations. The target is observable behavior in declared compatibility profiles. Identical proprietary model outputs and universal version compatibility are not established.

This is a development plan, not a claim that the features below are available. The implementation issues are the work queue. Each must carry acceptance evidence before it closes.

<!-- tracking-issue -->
Tracking issue: [AI Key and AI Port parity and open-source product roadmap](https://github.com/Olli0103/unifi-ai-key-emulator/issues/1). See the [dependency-linked issue index](docs/planning/issues.md).

## Current evidence

As of 22 September 2026, the Apple container deployment has native adoption, control, credential rotation, disabled factory login and reconnect evidence. Protect 7.3.60 on a UDM Pro Max accepted one automatic G5 Flex event caption from a configured OpenAI model, persisted it and displayed it after a full page reload. The earlier adoption test used Protect 7.3.56. See [build results](BUILD-RESULT.md) and [description acceptance](docs/basic-descriptions.md).

That single-event permit is consumed. Continuous processing and automatic all-camera discovery are not deployed. No AI Port profile has been adopted or verified. Native search, structured recognition, speech, legacy detection and NAS operation remain unverified or unimplemented. The existing E5 query adapter and database preparation are not proof of native retrieval. Static research on Protect 7.2.105 and AI Key 2.2.8 is version-specific evidence, not proof of every 7.3.60 contract.

The repository is public but has no license at the time this plan is written. An open-source license and distribution review are part of the product work below.

## Work order and gates

| Step | Work | Exit evidence |
| --- | --- | --- |
| 0 | Freeze separate AI Key and AI Port compatibility profiles, the threat model and evaluation method. Probe AI Port adoption/event ingress and native search early. Begin license/provenance work. | Versioned contracts, sanitized fixtures, explicit feasibility decisions and a benchmark method. |
| 1 | Camera registry, durable all-camera scheduling, provider/model configuration, authenticated admin API and control site. | Newly discovered eligible cameras need no source edits; captions persist on two native camera families; limits survive restart; operators change settings through the site. |
| 2 | Structured detections, second-stage verification, basic Find Anything/image search and index migrations. | Native results and filtered retrieval survive reload/restart and meet the chosen quality thresholds. |
| 3 | Deep session understanding, speech, plate/face recognition, optional enhancement and AI query alarms. | Each native workflow has separate persistence, failure and quality evidence. |
| 4 | AI Port compatible camera pairing, stream handling, detection, recognition and recording through a proven native bridge. | Separate native adoption, correct original-camera timeline, supported PTZ/recording behavior, caption, search result and test alarm. |
| 5 | Mac/NAS recovery, capacity and security qualification. | Backup/restore, upgrades/rollback, 72-hour soak and seven-day pilot pass. |
| Product | Licensing, contribution and security reporting, onboarding, signed releases, independent beta and maintained 1.0. | A new user can install, configure, update, recover and report problems from public documentation. |

These are dependency gates, not delivery estimates. The product work runs alongside implementation. Legacy and search feasibility start early because either can change the design. Implementations that depend on an unproven protocol remain blocked until that protocol is established.

## Native compatibility contract

Track each feature as implemented, fixture-tested, native-verified, unsupported or `needs_evidence`. Record Protect version, camera family, processor profile, model identity and the exact acceptance result. Keep adoption state stable through restart and upgrade. Do not advertise a capability solely because the UI accepts its flag.

Every feature requires both a successful native workflow and a meaningful negative case. A callback returning HTTP 200 proves transport acceptance only. Storage, timeline association, search and alarm behavior require their own checks. Preserve native camera intelligence when adding processor results; a caption-only result must not erase or falsely complete another recognition task.

## All cameras and AI Port coverage

Discover all cameras through the Protect inventory and refresh additions, renames, removals, offline states and capability changes. The registry shows every camera with per-feature eligibility and a reason for unavailable features. An explicit all-camera policy admits newly discovered eligible cameras automatically. Invalid or stale inventory cannot grant new processing scope.

The initial operating policy is 12 new paid captions per rolling hour across the entire installation. It is not 12 per camera or a statement of vendor-equivalent throughput. Persist reservations before inference, account for uncertain charged attempts, deduplicate across restart and schedule fairly across cameras. Bound queue and journal growth. A fixed journal that fills and silently stops processing is unacceptable.

Protect imposes task deadlines. Defer work only when the verified native deadline allows it; otherwise report a supported busy/budget outcome and a visible skip. The paid-caption limit must not disable local base detection or time-critical alarms.

AI Port compatibility is a separate product profile, not a hidden helper inside the AI Key emulator. It needs its own persistent identity, certificate, adoption and reconnect lifecycle so Protect can show and manage both roles without identity collisions. One deployment may run either or both profiles, but each profile keeps separate state and capability claims.

The AI Port profile covers native Protect pairing for supported G3, G4/G5 and ONVIF cameras; automatic HQ/LQ stream selection; bounded RTSP/ONVIF ingest; ONVIF PTZ pass-through; motion, person, vehicle, animal and speech detections; vehicle color/type classification; face and plate recognition; zones; clock alignment; reconnect; and compatible edge recording. It must preserve existing G4/G5 PTZ and camera settings. Camera-count, resolution, frame-rate, aspect-ratio and model restrictions are explicit capability policies based on measured resources and the selected compatibility profile.

Software can reproduce supported Protect workflows, but it cannot provide the physical AI Port's PoE power output or Ethernet ports. Cameras need their own network and power. Edge recording requires actual storage plus verified native recording behavior; advertising a software profile alone does not provide it.

First prove AI Port adoption, camera association and native event ingress. A generated detection must appear on the original camera timeline and recording in Protect. An independent dashboard or arbitrary database write does not satisfy this requirement. Then implement the full stream and inference path and feed its smart detections into the AI Key profile for descriptions, search and alarms. If a native contract cannot be proved, record the blocker and keep the requested parity goal open. See the [AI Key FAQ](https://help.ui.com/hc/en-us/articles/29221435686039-UniFi-AI-Key-Setup-and-FAQs) and [AI Port FAQ](https://help.ui.com/hc/en-us/articles/28315005177239-Protect-AI-Port-FAQs).

## Providers, models and control site

Use independent model assignments for vision descriptions, text embeddings, paired image/text embeddings, detection/reverification, OCR/plates, speech, recognition/ReID, reranking and optional enhancement. An API that supports vision does not necessarily support embeddings or audio. Keep the existing OpenAI, Ollama and compatible adapters; add other providers only for roles they actually support.

List models when a provider exposes a catalog, allow an explicit model ID and test actual input/output support with synthetic data. Distinguish catalog availability from tested compatibility. Retain model revision, preprocessing and output shape. Never switch to another remote provider silently.

The control site uses accessible forms and navigation, with English as the first supported language and strings prepared for translation. It is the supported configuration path for both profiles and has eight areas:

- Overview: separate AI Key and AI Port adoption/connection state, enabled features, errors, queue and cost/budget usage.
- Devices and adoption: profile identity, controller trust, adoption readiness, pairing status and recovery actions.
- Cameras: automatic inventory, Protect or ONVIF source, AI Port pairing, stream/PTZ/recording status, eligibility, policies, zones, last successful work and skipped jobs.
- Providers and models: endpoints, secret replacement, role assignments and connection/inference tests.
- Processing and budgets: feature switches, confidence thresholds, global/per-feature limits, priorities and failure policy.
- Search and indexes: model/index identity, rebuild progress, coverage, cutover and rollback.
- Privacy and retention: legal permissions, per-camera audio and identity controls, processing destinations, media and identity retention, and deletion status.
- Maintenance: configuration history, backup/restore, update status and sanitized diagnostics.

Separate the admin listener from both emulated device services. Loopback HTTP is acceptable for development; LAN administration uses authenticated HTTPS. Provide first-run administrator setup without default credentials. The site manages controller trust, profile enablement, adoption readiness, cameras and ONVIF credentials, providers, role-specific models, feature policies, thresholds, zones, budgets, indexes, retention, backups, updates and sanitized diagnostics. Validate, preview and apply configuration atomically with revision checks and rollback. Preserve both device identities. Drain or version affected jobs. Secrets are server-side references with write-only replacement; saved values never return to the browser.

Search needs special care. Static evidence contains different legacy CLIP image/text and newer E5 session contracts. Determine which native path the target Protect version actually uses before choosing an encoder. Equal embedding width does not mean compatible meaning. Keep query/document/image encoders matched; do not pad or relabel vectors to satisfy a schema. Model changes create an isolated index generation with rebuild, validation, cutover and rollback. Explain any historical material that cannot be reindexed.

## Security and detection quality

The security baseline covers controller TLS pins and client authentication, separation of controller/provider/admin credentials, CSRF and origin checks, bounded requests and media decoding, SSRF/redirect/DNS controls, secret redaction, prompt injection and least-privilege containers. Treat video, provider replies and model output as untrusted input. Test endpoint misconfiguration and credential-routing failures. Add dependency scanning, SBOMs, model hashes/licenses, private diagnostic handling and recovery tests. Review the assembled system before LAN use or release; unit tests alone are insufficient.

Map retention and deletion for frames, transcripts, recognition templates, indexes and backups. Audio and identity recognition are opt-in per camera; identity templates stay local by default. Show remote processing destinations, protect stored secrets, and support key rotation without breaking adoption. Keep an explicit privacy/data policy and test its enforcement.

Protection quality is measured separately from application security. Build a consented or synthetic corpus covering daylight/night, glare, rain, blur, occlusion, pets, multiple objects and negative events. Track precision/recall, false alarms per camera-day, missed critical events, plate accuracy, speech error, retrieval ranking, event-to-alert latency and cost. Set numeric thresholds from an initial baseline before tuning. Keep evaluation data and tuning data separate.

Use the same inputs for comparison with a real AI Key when a reference device or suitable reference results are available. Until then, equivalence and "best in class" remain `needs_evidence`. Public product material reports measured results. A model's marketing name is not an acceptance test.

Vendor references also need versioning. The AI Key FAQ lists 1,000 detections/hour, while the [technical specifications](https://techspecs.ui.com/unifi/cameras-nvrs/ai-key) list 1,800. Audio eligibility differs between the FAQ and the [camera capability guide](https://help.ui.com/hc/en-us/articles/360058867233-UniFi-Protect-Cameras-AI-Detections-and-Facial-Recognition). Record these contradictions and verify actual behavior rather than promising one unqualified number or camera list.

## Becoming an open-source product

1. Select an OSI-approved license with the maintainer, add SPDX metadata/notices, and audit independent implementation provenance plus dependency/model distribution rights. Apache-2.0 is a candidate, not an adopted license. Choose durable project/package naming and retain clear unofficial attribution.
2. Publish contribution, conduct, review and sign-off rules. Establish a working private security-reporting route, support/version policy and maintainers who accept those responsibilities. Coding-agent output receives the same review as other contributions.
3. Deliver first-run setup and public installation, provider, compatibility, troubleshooting, update, backup, restore and uninstall documentation. Include a synthetic demo that cannot contact a real controller.
4. Automate clean builds and tests, ARM64/AMD64 packaging, signed versioned artifacts, SBOM/provenance/checksums and rollback checks. Untrusted PR jobs receive no release secrets.
5. Ship an explicitly experimental alpha, then run an opt-in beta with at least two independent installations. Collect sanitized diagnostics only, with no default telemetry or media upload. Publish failures and compatibility limits alongside successes.
6. Release 1.0 only against explicit AI Key and AI Port native compatibility contracts, security/quality gates and recovery evidence. Maintain regression checks for Protect and model updates, migration paths and a support policy the maintainers can sustain.

An open-source alpha can precede full feature parity. A release cannot silently drop an unproven native feature and still claim the requested AI Key or AI Port behavior. Licensing, security reporting and distribution rights must be settled before presenting an alpha as a usable open-source product.

## Claude and contributor handoffs

Claude Opus reviewed published source and generic roadmap requirements, including open-source product readiness. The reviewed plan includes separate privacy/retention work and explicit license and recovery gates. Its review is advisory; native acceptance remains the integration owner's responsibility. Contributors receive one dependency-ready issue, a public commit, bounded file ownership, a shared contract and synthetic fixtures. They return a branch/PR, tests and explicit gaps. Keep credentials, private streams, raw captures and deployment state outside coding-agent handoffs.

Start independent work on the versioned contract manifest, security design, and license/provenance inventory. Then split provider configuration, camera inventory and evaluation work after their contracts are agreed. The UI follows the admin API schema. Protect adoption, protocol capture, controller policy changes, native checks and deployment have one integration owner. See [handoff instructions](docs/planning/claude-handoff.md).
