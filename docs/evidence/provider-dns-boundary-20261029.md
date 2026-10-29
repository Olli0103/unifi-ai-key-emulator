# Async provider DNS boundary — 29 October 2026

Implementation/fixture progress for #3, #8 and #13; not completion of those
issues or a live-network deployment test.

The existing pinned aiohttp transport already rejects legacy numeric IPv4
forms. No new resolver library is needed. `ProviderConnector` adds an
address-range decision to the exact records that aiohttp uses to connect:

- Explicit ordinary global, RFC1918, ULA, shared-address VPN and loopback IPs
  remain available, subject to existing endpoint/role opt-ins.
- `localhost` must resolve only to loopback. Other ASCII DNS names must resolve
  only to ordinary global unicast addresses; private DNS names intentionally
  require an explicit IP instead. TLS identity checks are not relaxed.
- Empty, excessive, mixed public/private, scoped, mapped-private and known
  translation/transition answers fail before HTTP. Reserved/multicast,
  unspecified and link-local destinations are refused even as literals.
- All answer records are checked before any is returned to the transport;
  returned records are detached from mutable resolver-owned dictionaries.
  There is no second lookup after validation. Cached answers are checked too;
  existing pooled sockets still refer to their originally approved peer.
- Host syntax is checked before vision/embedding URL canonicalization can
  map compatibility Unicode to localhost/private destinations. IDNs use their
  ASCII A-label form. Provider credentials remain request-scoped; controller
  clients and their separate certificate/pin boundary are unchanged.

The connector is wired into the Key's vision/speech/recognition/enhancement/
tagging/Deep model session, HTTP embeddings, CLIP, rerank and catalog clients.
The latter three model clients also keep no cookies/environment proxies.
Callers retain their existing deadlines, bounded replies and redirect refusal.
Cancellation is not swallowed as a policy error or retried.

## Evidence and limits

Synthetic DNS fixtures exercise IPv4/IPv6, mixed answers, IPv4-mapped IPv6,
special prefixes, private/public/loopback endpoints, changing answers, malformed
hostnames, excessive/empty replies and cancellation. A real loopback HTTP
demonstration first shows the former ordinary connector delivering a synthetic
role credential to an internal DNS answer; the guarded catalog client makes
no second request. Worker and embedding transport checks likewise deliver no
synthetic media/query payload. A legitimate localhost request retains Host;
real loopback TLS tests retain SNI and certificate verification, and reject an
untrusted certificate before an HTTP request.

This is address-class policy, not DNSSEC, permanent IP pinning, routing/billing
attestation or proof about every possible network translation. Public DNS
answers may change between requests if each new answer still satisfies the
policy. Synchronous AI Port HTTP and face-enrollment helpers use a separate
[guarded transport](provider-sync-boundary-20261029.md) sharing this address
decision; they are not covered by the async connector itself.
No DNS/hosts, Gateway, controller, NAS provider
or running-image configuration changed. Existing clients must receive a
reviewed deployment before this source fix becomes a runtime boundary.
