# Search profiles and E5 interface

Search is disabled until `search.enabled` is explicitly true. The implementation
has two distinct profiles; their vectors are not interchangeable:

| Profile | Query contract | Boundary |
| --- | --- | --- |
| `e5-session-v1` | Explicit `NL_PARSE` for `multilingual-e5-small`, 384 values | Session-description space; no CLIP image/text fallback. |
| `clip-basic-v1` | Basic `NL_PARSE` for `clip-ViT-L-14`, 768 values; `IMAGE_SEARCH` returns `imgEmbed` | Requires the configured CLIP service and pinned model revision. The CLIP basic branch defaults a missing model to CLIP; this does not apply to E5-only mode. |

The E5-only path rejects a missing/wrong model and `IMAGE_SEARCH`. No zero,
random, padded or fabricated vector is used as a fallback. With the CLIP profile,
an explicit E5 session query can also be served when a real E5 backend is
configured; this does not turn a CLIP index into an E5 index or prove native
Deep retrieval.

The E5 contract was initially observed in Protect 7.2.105. Later bounded native
**basic** search/image-upload observations on Protect 7.3.74 are recorded in
[the dated native evidence](evidence/native-search-alarm-20261030.md). They
establish the exercised transport/UI path, not complete retrieval quality,
negative UI behavior, Deep acceptance or hardware equivalence. The remainder
of the adapter/backend interface below describes **E5** unless stated otherwise.

## Python interface

```python
from aikey.search import EmbeddingService, SearchService

encoder = EmbeddingService(config["embeddings"])
documents = await encoder.encode_documents(["A red car exits the driveway."])
queries = await encoder.encode_queries(["red car leaving"])
await encoder.close()

search = SearchService(config, state_dir, ssl_context=verified_device_context)
await search.start()
print(search.status)
await search.stop()
```

`encode_documents` and `encode_queries` accept 1 to 32 nonempty strings, each at most 8192 characters, and return one normalized 384-dimensional vector per string. `embed(text, kind="query")` is a convenience method; document kinds are `document` or `passage`. Errors raise `EmbeddingError`.

The document and query backend configuration must be identical. The worker enables description embeddings separately with `worker.description_embeddings: true`. Enabling query search alone does not add embeddings to old descriptions.

## Backends

The HTTP backend calls an explicitly configured local OpenAI-compatible embeddings service:

```json
{
  "embeddings": {
    "backend": "http",
    "base_url": "http://127.0.0.1:8081/v1",
    "model": "intfloat/multilingual-e5-small",
    "timeout_seconds": 8,
    "revision": "operator-recorded-checkpoint-revision"
  }
}
```

It posts to `/embeddings` when the base ends in `/v1`, otherwise `/v1/embeddings`. An optional `endpoint` overrides the full path. `bearer_token_file` reads a secret from a file. Credentials, query strings, fragments, ambiguous escaped paths and redirects are rejected. The client does not inherit proxy settings from the environment.

Loopback is the default boundary. A non-loopback endpoint requires `embeddings.allow_remote: true`. Plain HTTP outside loopback also requires `embeddings.allow_insecure_http: true`. These opt-ins permit the exact configured endpoint; they do not add fallback providers.

Responses must contain indexed vectors in an OpenAI-style `data` array. A supplied incompatible response model, wrong dimensions, nonfinite values or a zero norm fails the request.

The local backend uses a checkpoint that the operator has already placed on disk:

```json
{
  "embeddings": {
    "backend": "sentence-transformers",
    "model": "intfloat/multilingual-e5-small",
    "model_path": "/models/multilingual-e5-small",
    "device": "cpu",
    "revision": "operator-recorded-checkpoint-revision"
  }
}
```

Install the project's optional `models` dependency in its virtual environment or container. The service loads the model lazily in a worker thread, requires local files, and disables remote model code. It does not download weights. The first query may exceed Protect's timeout while the model loads, so warm the configured backend before a controller trial.

Both adapters prepend `query: ` for queries and `passage: ` for descriptions, then enforce L2 normalization. The local encoder uses a 512-token limit. These choices follow the [upstream E5 model card](https://huggingface.co/intfloat/multilingual-e5-small#usage) and [Sentence Transformers API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html). They are this project's explicit profile. The vendor's exact document preprocessing was not recovered, so compatibility with existing vendor-generated vectors is not claimed. Do not mix those indexes.

`search-profile.json` records the selected source, model, revision and prefix rules. Startup refuses a changed identity to prevent silently mixing search spaces. This guard cannot detect weights replaced behind an unchanged path or API URL. Record a revision and use a new isolated index when changing the model.

## Transport and indexing

The service connects to `wss://{controller.host}:{controller.search_port}/wss/nl-search/v1`, default port 7443, using the supplied device SSL context and the normalized MAC in `x-ident`. It uses `VerifiedConnector`, including the optional controller fingerprint check before sending HTTP headers. TLS certificate verification stays required. Startup sends the observed UCP4 echo registration.

Only explicit `NL_PARSE` requests for `multilingual-e5-small` receive E5 results. The response preserves the request ID, returns `txtEmbed`, model and dimension, and empty tag/object-type arrays with `exact_match: false`. The E5-only branch does not parse temporal/tag filters or serve legacy image search. Backend errors return a nonzero error code without embedding data.

The CLIP basic branch additionally parses supported object/time expressions using
the controller timezone and returns its tag/filter fields with `exact_match:
false`. Those fields are not a strict-filter or human-relevance guarantee.
`IMAGE_SEARCH` accepts only the configured controller's HTTPS media-port
`/internal/files/` route, without credentials, query/fragment, traversal or
redirects. It uses verified/pinned TLS, a bounded response and timeout, JPEG/PNG
validation and the existing CLIP region encoder. Failures have fixed private
status categories; no vector is fabricated to hide a failed image query.

The PostgreSQL container profile is documented in [deployment/postgres](../deployment/postgres/README.md). A matching controller must create its task ledger and session, dispatch the description job, accept the callback and migrate/index the remote database. In the inspected controller, HTTP 200 acknowledges the callback before persistence, unknown task IDs are dropped, and both dense and BM25 retrieval exclude a null description embedding.

The minimal PostgreSQL image linked above does not install hybrid BM25
extensions or reranking. This query adapter does not implement a reranking
service; a controller mode requiring unavailable components can return no
results even when the dense index exists. Do not infer the provisioned live
NAS stack from that minimal image's dependency list. Local adapter tests cover
framing, verified TLS exchange, validation and shell configuration using named
synthetic fixtures. Those tests alone do not prove model quality, native
migration/UI behavior or NAS deployment; dated native/runtime observations have
their own narrower evidence and limits.
