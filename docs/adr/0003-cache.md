# ADR 0003: Cache-aside with negative caching, single-flight and stale-if-error

**Status:** accepted · **Date:** 2026-11-09

## Decision
One key per resource holding an envelope `{fresh_until, nf, detail, data}`:

- **Physical TTL = logical TTL + stale window.** Between `fresh_until` and physical expiry, the entry is not served normally, but it is used if **every** source fails (`X-Cache: STALE`).
- **Negative caching** (`nf=true`, 1h): 404s are cached too, so non-existent CEPs/CNPJs don't trigger repeated calls.
- **Single-flight:** a `SET lock NX PX` per key. Requests that don't get the lock poll for the value until the owner finishes, protecting sources from a *stampede* on hot keys, across replicas too. The lock is released with a compare-and-delete in Lua, so we never delete someone else's lock.
- **Fail-open:** a Redis error becomes a cache miss, never a client error.
- Versioned keys (`raiox:v1:`): changing the envelope format means bumping the prefix, no migration needed.

TTLs: CEP 7d (rarely changes), CNPJ 24h (registry status changes), IBGE municipality 30d. A result built while a source failed transiently (*degraded*) gets a **5-minute** TTL instead, so a passing outage doesn't freeze incomplete data for days (ADR 0007). The TTL is chosen per value: `get_or_load` accepts a function of the loaded value.

## HTTP
A **weak** `ETag` computed over `data` only, because `meta` changes on every request (request_id, cache status) while the representation is semantically the same. `If-None-Match` → 304. `Cache-Control: private, max-age=<remaining freshness>`, `private` because the response depends on the API key (rate limit headers).

## Alternatives
- Two keys (fresh + stale): twice the writes and a chance of drifting out of sync.
- Background *stale-while-revalidate*: better latency for hot keys, but needs workers. Left as a next step.
