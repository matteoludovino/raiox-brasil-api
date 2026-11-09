# ADR 0002: Per-source timeout, retry and circuit breaker

**Status:** accepted · **Date:** 2026-11-09

## Decision
Every external call goes through `BaseProvider._get_json`, which applies, in order:
1. **Circuit breaker** open → fail immediately (`circuito_aberto`).
2. **Outbound quota** (token bucket) → `cota_esgotada`, which does **not** count as a source failure.
3. **Timeout** per request (`RAIOX_UPSTREAM_TIMEOUT_S`).
4. **Retry** only for transient errors (5xx, network, timeout), with exponential backoff and *full jitter* so clients don't synchronize. 4xx is never retried.

Breaker: `closed → open` after N consecutive failures; after `reset_timeout`, `half_open` lets **one** trial request through. 404/400 count as **success** for the breaker, because the source is healthy and simply doesn't have the data.

## Trade-off: in-process breaker
Breaker state lives in memory per replica. With R replicas, a source may receive up to `R × N` calls before every breaker opens. For a few replicas that is acceptable and avoids a Redis round-trip on every call. With dozens of replicas, the way forward is to move the state into Redis (same Lua pattern as the rate limiter).

## Consequences
- A source going down → after N failures, lookups fail in milliseconds instead of waiting for the timeout (validated: ~8 ms).
- `/ready` exposes the circuit states. Sources being down do **not** take the instance out of the load balancer, because it can still serve from cache.
