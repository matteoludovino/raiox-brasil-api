# ADR 0005: API keys hashed with SHA-256 in Redis

**Status:** accepted · **Date:** 2026-11-09

## Context
The consumers are systems (machine-to-machine), not people. JWT/OAuth2 would add token issuance and refresh with no benefit here.

## Decision
- Key = `rx_live_` + 256 random bits (`secrets.token_urlsafe(32)`). The prefix enables *secret scanning* (GitHub, GitGuardian) and visual identification.
- Redis stores **only `sha256(key)`** → `{id, name, plan, prefix, created_at}`. The plaintext is shown exactly once, at creation.
- **Why not bcrypt/argon2?** Slow hashes protect **low-entropy** secrets (passwords) against dictionary attacks. With 256 random bits, brute force is infeasible even with SHA-256, and a fast hash enables an O(1) lookup by the digest itself, without comparing candidate by candidate.
- The plan (`free`/`pro`) is stored with the key and defines its rate limit.
- Admin: a Bearer token compared with `secrets.compare_digest`. In `prod` the app refuses to start with the default token or one shorter than 32 characters.
- No TTL on credential keys + Redis with `volatile-lru`: only cache entries are evicted under memory pressure. With `appendonly yes`, keys survive restarts.

## Consequences
- Immediate revocation (delete the key).
- A Redis leak does not expose usable keys.
- At larger scale, keys would move to a relational database, with Redis as a verification cache.
