# ADR 0004: Sliding window counter (inbound) and token bucket (outbound)

**Status:** accepted · **Date:** 2026-11-09

## Inbound: per API key
A **sliding window counter** in Lua: `usage ≈ previous × (1 − elapsed/window) + current`.

| Algorithm | Accuracy | Memory/client | Problem |
|---|---|---|---|
| Fixed window | low | O(1) | 2× burst at the window boundary |
| Sliding log (ZSET) | exact | O(limit) | expensive for high limits |
| **Sliding window counter** | ~exact | **O(1)** | approximation (assumes uniform traffic in the previous window) |

The script is atomic (no race between GET and INCR) and both keys share the `{id}` *hash tag*, which keeps it Redis Cluster compatible. `Retry-After` is computed by solving for when the previous window's share has decayed enough.

## Outbound: per source
A **token bucket** (capacity = quota/min, continuous refill) in Lua, shared across replicas. It stores **integer milli-tokens**, because floats are converted unpredictably between Lua and Redis. Empty bucket → the fallback chain skips the source, without taking a third-party 429 or opening the breaker.

## Trade-off: the clock
The timestamp comes from the application, which keeps tests deterministic. That requires NTP across replicas: a few seconds of drift slightly skews the windows. The alternative is `redis.call('TIME')` inside the script.

## Redis failure
Rate limiting fails **open** (logs and serves). A Redis outage shouldn't turn into a full API outage, and authentication, which fails closed, still protects the API.
