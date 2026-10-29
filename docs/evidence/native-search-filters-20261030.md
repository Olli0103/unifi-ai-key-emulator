# Native basic search/filter observations — 30 October 2026

This is a bounded read-only observation on Protect 7.3.74, not search-quality,
strict object filtering, full camera/date-filter coverage, Deep processing or
hardware-equivalence acceptance. The [machine-readable record](native-search-filters-20261030.json)
contains only public query strings, counts, timings and booleans. No native
camera/event/detection IDs, descriptions, images, credentials or private query
text are exported.

## Bound and request contract

The authenticated existing browser context retained the original native
identity/pairing baseline. One one-use observation admitted at most **three
search GETs**, with a 12-second client deadline and 1-MiB response-text bound
per request, no redirects, uploads or configuration changes. The observation
is terminal; do not reset it or turn it into an extended benchmark. Before/after
native baseline reads are separate from those three search requests.

After aggregation, this observation's raw result references were released from
its browser marker while preserving its terminal/count/aggregate summary and the
original identity baseline. This is not physical erasure, browser-cache cleanup
or deletion of controller/provider/log/backup copies.

The initial `GET /proxy/protect/api/detection-nls` used `query=a car` and
`minSimilarity=35`. It returned HTTP 400 in 15 ms. The safely classified error
referred to parsing the request query. The already recorded native contract
uses **`searchText`**, not `query`, for this basic endpoint (unlike session
search). This client-helper mismatch is preserved, not labeled a model failure
or a demonstrated application defect. No unknown error text is published.

The two remaining requests used that documented parameter and the same integer
minimum similarity of 35. No uncertain request was automatically replayed;
the first rejected request remains in the denominator of search GET attempts.
The observation neither measures nor claims the number of underlying encoder
calls, provider-reported charges or zero host/runtime-counter/log writes.

## Observed results

| Search | HTTP / browser API elapsed | Returned detections | Native type metadata |
| --- | --- | ---: | --- |
| `a car` | 200 / 526 ms | 50 | 50 vehicle |
| `person yesterday` | 200 / 244 ms | 50 | 47 person, 1 vehicle, 2 animal |

For the dated query, the returned numeric `searchCondition.startTime/endTime`
exactly matched the browser's local-yesterday window. The browser timezone
matched Europe/Berlin. Every returned numeric `detectedAt` was inside that
declared half-open interval. No individual detection timestamp was exported.

This supports that **one** local-day window and the availability/type composition
of these bounded results. It does **not** establish a strict person-only filter:
three returned entries have other native types. Native type metadata is not
human visual ground truth, precision/recall or an explanation of why those
entries ranked. Do not change `exact_match`, tune a prompt/threshold, remove
those entries or describe the result as 100% relevant. No negative query or
camera filter was tested; the three-request bound was not extended to add them.
The default result cap is not a full index/camera inventory or coverage total.

Timings are browser request-to-JSON-read elapsed values, not isolated model
runtime, cold inference, native event-to-alert latency, percentiles or an SLA.

## Preservation and source boundary

Both native baseline reads retained one Key, four Ports, nine original pairings
and five adopted/connected processors, with original Key/Port identities
unchanged. These reads do not prove recording playback, absence of every
internal mutation or all database-row equality.

The previously verified deployed processor source is
`5a459230c68809835f1781d006b62030d9694059`; see the separate
[five-processor source/backup proof](key-port-batch165-20261030.md). This search
observation did not rehash all runtime modules, redeploy a container, reset a
scope, change a provider/model/key/profile, upload an image or issue a new
camera-processing task. The existing configured encoder/search path was used.
It does not activate the separate administration features merged afterward.

The earlier [public person-presence pilot](public-person-pilot-20261029.md)
remains frozen at 7/12 usable/correct and five errors. These text-search reads
are not additional person-detector cases, new gold labels, a rerun, a repair
of that score or evidence for its separate fallback adapter.

Issues #21/#22/#7 remain open for their outstanding relevance/negative-query,
camera/restart, fresh native Deep and representative quality requirements.
