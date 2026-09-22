# E5 search contract

The implemented `e5-session-v1` profile sends real text embeddings over Protect's UCP4 search connection. It targets the 384-dimensional session-description contract observed in Protect 7.2.105. The target is Protect 7.3.56 on a UDM Pro Max. That exact version and a working adoption/search session remain unverified. Search is disabled until `search.enabled` is explicitly true.

This implementation does not produce the legacy 768-dimensional CLIP image embeddings. It rejects requests with a missing model, `clip-ViT-L-14`, and `IMAGE_SEARCH`. No zero, random, padded, or fabricated vector is used as a fallback.

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

It posts to `/embeddings` when the base ends in `/v1`, otherwise `/v1/embeddings`. An optional `endpoint` overrides the full path. `bearer_token_file` reads a secret from a file. Credentials or query strings in the URL and redirects are rejected. Responses must contain indexed vectors in an OpenAI-style `data` array. A supplied incompatible response model, wrong dimensions, nonfinite values or a zero norm fails the request.

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

Only explicit `NL_PARSE` requests for `multilingual-e5-small` receive E5 results. The response preserves the request ID, returns `txtEmbed`, model and dimension, and empty tag/object-type arrays with `exact_match: false`. Temporal parsing, tag extraction and legacy image search are not implemented. Backend errors return a nonzero error code without embedding data.

The PostgreSQL container profile is documented in [deployment/postgres](../deployment/postgres/README.md). A matching controller must create its task ledger and session, dispatch the description job, accept the callback and migrate/index the remote database. In the inspected controller, HTTP 200 acknowledges the callback before persistence, unknown task IDs are dropped, and both dense and BM25 retrieval exclude a null description embedding.

Hybrid BM25 extensions and reranking are not implemented. Controllers expecting them may return no results even when the dense index exists. Local tests cover adapters, framing, verified TLS WebSocket exchange, validation and shell configuration. They use named synthetic fixtures and do not prove real model quality, PostgreSQL migration, Protect UI behavior or NAS deployment.
