# ADR 0001: Fan-out for CEP, fallback for CNPJ

**Status:** accepted · **Date:** 2026-11-09

## Context
Each resource has several public sources with different profiles:

| | CEP (BrasilAPI, ViaCEP, OpenCEP) | CNPJ (BrasilAPI, CNPJ.ws, ReceitaWS) |
|---|---|---|
| Quota | high / no documented limit | ~3 req/min on the secondary ones |
| Data | **complementary** (coordinates only in BrasilAPI; IBGE code/area code in ViaCEP) | **redundant** (same origin: the Federal Revenue) |
| Latency | 50–700 ms | 100 ms – 2 s |

## Decision
- **CEP:** call every source **in parallel** under a global deadline and merge field by field by precedence (BrasilAPI > ViaCEP > OpenCEP). Each field's provenance is recorded, and disagreements on `cidade`/`uf` produce a warning.
- **CNPJ:** a **fallback chain**. The next source is only called if the previous one fails. A 404 also continues the chain, because the databases lag behind each other differently.

## Consequences
- CEP: latency = the slowest source (capped by the deadline), the most complete record, and natural fault tolerance.
- CNPJ: on the happy path, one call per lookup, preserving quotas; in the worst case, latencies add up.
- The company address is enriched by **reusing** the CEP aggregator, which brings its own cache.

## Rejected alternatives
- *Hedged requests* for CNPJ (fire the second source after X ms): improves tail latency but spends quota. Worth adding if BrasilAPI is often slow.
- Fallback for CEP: we would lose coordinates or the IBGE code whenever the first source answered with an incomplete record.
