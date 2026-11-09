# ADR 0007: Time-boxed IBGE enrichment with a local snapshot fallback

**Status:** accepted · **Date:** 2026-11-09

## Context
The first deployment (Render, US East) showed that the IBGE API **does not answer requests from outside Brazil**: every call hung until the timeout. Two design flaws surfaced with it:

1. Enrichment ran *after* the fan-out, outside its deadline and with retries: 3 × 4 s = **~15 s** for the first lookup.
2. The incomplete result (no municipality) was cached for the full CEP TTL: **7 days** of missing data caused by a transient failure.

## Decision
- **Hard deadline for enrichment** (`RAIOX_ENRICHMENT_DEADLINE_S`, 2 s). The IBGE provider gets no retries and a timeout just below the deadline, so it times out on its own and feeds the circuit breaker, instead of being silently cancelled by the outer deadline (which the breaker would never see).
- **Local snapshot fallback.** `src/raiox/data/ibge_municipios.json` holds all ~5,570 municipalities (600 KB, compact columnar format), built by `make snapshot` with the **same parser** as the live provider, so both can't drift. Live IBGE stays the primary source; the snapshot answers when it fails, and `meta.fontes` says so (`ibge: erro`, `ibge:snapshot: ok`).
- **Short TTL for degraded results** (`RAIOX_TTL_DEGRADED_S`, 5 min). A result is *degraded* when a source failed transiently (error, timeout, open circuit, quota) **and** that made the data incomplete. A failed CNPJ source followed by a successful fallback is not degraded (same origin, same data); a snapshot-covered municipality is not degraded either.

## Why a snapshot is acceptable here
Municipalities are reference data: a handful of changes per decade. Live data adds little freshness, and the snapshot makes the API independent of where it's hosted.

## Consequences
- Worst case with IBGE unreachable: ~2 s (deadline) for the first lookups, then ~0 once the IBGE circuit opens. Measured: 15 s → 2.2 s → 0.5 s.
- The snapshot needs a periodic refresh (`make snapshot`, from a Brazilian IP). A brand-new municipality missing from it degrades gracefully (`municipio: null` + warning, short TTL).
