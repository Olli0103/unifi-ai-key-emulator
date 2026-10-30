# Model-role contract (#8)

Roles are separate data contracts, not permission to send any model every kind
of home-camera data. A catalog ID is availability, not measured capability.
Saving settings does not load/download a model, start a processor, reprocess
events or change an index. Each enabled feature retains its camera, origin,
privacy, budget and deadline gates.

## Roles and adapters

The authenticated **Saved model role registry** (`/model-registry`) reads the
saved Key/Port assignments, including roles that are not editable in the model
forms. An unconfigured role is shown as such, not offered as an implemented
activation action. The two device profiles deliberately have different face
contracts; equal-looking vector shapes are not interchangeable model identities.

| Role | Configuration/adapter | Accepted contract and change boundary |
| --- | --- | --- |
| Vision descriptions | Key `inference`; OpenAI Responses, Anthropic Messages, Ollama or compatible chat | Image inputs and complete bounded text, separate model key; model preview/save and retained undo. No silent provider/model fallback. |
| Text embeddings | Key `embeddings`; HTTP or local sentence-transformers E5 | `multilingual-e5-small`, 384 finite nonzero normalized values and matching profile. An API description model cannot replace it. Encoder/source/revision changes require index migration. |
| Paired image/text embeddings | Key `find_anything.clip_server` | `clip-ViT-L-14`, 768 values and the pinned shared encoder identity. Model/source changes remain outside ordinary settings/undo and require #18. |
| Detection | Port `live_pool_detector`; explicit vision API or pinned local RF-DETR | Valid classes, finite bounded boxes/scores and configured thresholds. API model selection is independent of Key captions; local checkpoints use their existing hash/load gate. |
| Reverification | Key CLIP verification or Port local `score_server` | Separate local verdict/score paths, not a description-as-detection conversion. Uses the feature's explicit fixed contract; not an arbitrary API-model selector. |
| OCR/LPR | Port optional `ocr_provider_config`, only `plate_cameras` | Own vision adapter/model/endpoint/key and exact unambiguous `plate` string/null reply. Existing crop checks and request budget apply. Without a separate assignment, the legacy detection/plate sharing is explicitly labeled, not silently called independent. |
| Speech | Key `speech_to_text`; official OpenAI or explicitly permitted local compatible transcription | Generated-silence test and bounded timestamped segments through its own key/endpoint/model. Scope is retained separately. Vision-only adapters and unsupported speech parameters are refused. |
| Person Re-ID | Key `deep_understanding.reid_server` | `person-reidentification-retail-0288`, exactly **256 source values**, then normalization/zero padding to **512 stored values**. Exact model/dimension metadata and strict JSON are checked before any vector callback. |
| Face recognition | Key local SFace service; Port pinned local face detector/embedder | Key **128-value SFace**, versus Port **512-value face embeddings**. Neither is a Re-ID or E5 vector. Model/template changes use the separate feature procedure; there is no misleading generic model-edit form. |
| Reranking | Key `deep_understanding.rerank_server` | Separate local query/document request, one finite score per document, bounded timeout and failure without a replacement result. Server-managed model/weights are not invented by the registry. |
| Optional enhancement | Key `face_enhancement.server` | Separate opt-in local image adapter, bounded decoded/re-encoded JPEG derivative, original retained. Not a caption/embedding adapter or a generic editable model role. |

Adapters are accepted only in their implemented roles. For example, adding
Anthropic vision support does not create an Anthropic embeddings, transcription
or biometric-identification API. Manual IDs remain available for the supported
API adapters; they are never silently substituted from a catalog or fallback.
Fixed protocol/index models are not ordinary model-ID edits.

## Configuration identity versus measured capability

Registry schema `aikey-model-registry/1` supplies a role-specific **selection ID**
for each configured saved assignment. It binds the declared backend/model,
source, supported generation parameters, expected output contract/dimensions
and declared revision. Source URLs/paths are hashed, not returned. Keys,
credential-file references and camera scopes are excluded, so a credential
rotation does not pretend to change an encoder. Configuration revisions still
bind preview/confirmation to the complete saved file.

A selection ID is **not** an index-compatibility fingerprint, proof of loaded
weights, a server's internal model identity or a quality result. Server-managed
models with no reported ID/revision remain unknown. The registry does not read
referenced models/keys or query a backend; `weights_verified` remains false for
this read. Configured local model hashes are declarations here; their existing
runtime loaders perform verification. The existing search-profile guard, not
the registry, controls the pinned index identity. No implicit profile upgrade,
weights assumption or reindex occurs from a registry read.

Supported catalogs and explicitly acknowledged one-use synthetic image/silence
tests remain separate actions. Their bounded results concern one API path and
generated input, not general modality, provider billing/retention, native
persistence or quality. The editable configured OCR role uses only its own
catalog/probe credential, with the same session/role/revision grant boundaries.
Unimplemented initial role/camera setup is not presented as an enabled action.

## Admission, changes and recovery

Vision, speech and embedding adapters reject unsupported provider parameters
instead of retaining a value they would silently ignore. Existing role policy
and secret-reference fields are distinct from provider request parameters.
Known incompatible adapters/models, dimensions, zero/nonfinite vectors and
malformed outputs fail at configuration/role or typed result boundaries before
they can replace usable settings or enter the corresponding result/index flow.
This does not infer an opaque API model's capabilities from its name.

The model-only UI previews the affected role and saved old/new model, unchanged
provider/key/permissions and index, current-job behavior and any new restart
requirement. No model call or file save occurs during preview. Confirmation
rechecks the revision; a concurrent save is refused. A no-op adds no new restart
requirement and does not claim an earlier saved change is already active.
Existing explicit apply clients remain revision checked; a human viewing a page
is not an authentication factor. Atomic history/last-known-good undo and
uncertain-outcome behavior remain intact, including both retained OCR/detection
keys. Existing native identities/pairings are not model settings.

## Acceptance evidence and limits

- [Role previews](../tests/test_role_change_preview.py), [existing model/undo
  checks](../tests/test_role_models.py) and [uncertain acknowledgements](../tests/test_admin_uncertain_outcomes.py).
- [Independent OCR](../tests/test_ocr_role.py): actual isolated key/route/model
  decisions, strict output, existing budget denial, raw policy preservation,
  undo-key refusal and owned HTTP catalog/one-use probe.
- [Unsupported role parameters](../tests/test_role_parameter_admission.py),
  [embedding config admission](../tests/test_embedding_config_admission.py),
  [vision adapters](../tests/test_providers.py), [speech](../tests/test_speech_to_text.py),
  [E5](../tests/test_search.py) and [CLIP](../tests/test_clip_client.py).
- [Fixed Re-ID boundary](../tests/test_role_vector_admission.py): 13 previous
  failures for foreign dimensions/models, omitted/ill-typed metadata, error and
  duplicate JSON fields; canonical/nonfinite/zero controls are retained.
  Generic vector normalization is not an encoder-identity check.
- [Registry](../tests/test_model_role_registry.py): all requested roles, distinct
  profile dimensions, versioned non-secret selection IDs, no referenced-file or
  model contact, unknown weights not imputed, authenticated read-only HTML.

These are source/fixture/owned HTTP results. Exact frozen-head full checks and
any native qualification are recorded separately in the integration PR before
merge. No real cloud inference, private footage, enrollment, new camera grant,
controller operation or deployment is performed by this contract. Native plate,
face, Deep, enhancement, retrieval, quality and endurance acceptance stays in
its feature issues; #8 is not a substitute for those results.
