# Raio-X Brasil API

Aggregator API for Brazilian public data: **CEP** (postal codes) and **CNPJ** (company registry). It consumes six external sources, normalizes their different formats and merges them into a single response. Each response also says **where every field came from** and **what went wrong** when something fails.

> Portfolio project focused on backend decisions: resilience against unstable sources, caching with controlled consistency, protection of third-party quotas, authentication and observability.

![Python](https://img.shields.io/badge/python-3.13-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.16x-009688) ![Redis](https://img.shields.io/badge/Redis-7.4-red) ![coverage](https://img.shields.io/badge/coverage-95%25-brightgreen) ![mypy](https://img.shields.io/badge/mypy-strict-informational)

> **Language note:** code, docs and commits are in English. The **JSON contract** (field names, status values, error messages and warnings) is in Portuguese, because the API serves Brazilian data to Brazilian clients and mirrors the official terminology (`logradouro`, `razao_social`, `situacao_cadastral`…).

---

## What it does

| Endpoint | Sources | Strategy |
|---|---|---|
| `GET /v1/cep/{cep}` | BrasilAPI · ViaCEP · OpenCEP + IBGE | **Parallel fan-out + field-by-field merge** |
| `GET /v1/empresas/{cnpj}` | BrasilAPI · CNPJ.ws · ReceitaWS + CEP aggregator | **Fallback chain** + address enrichment |
| `POST/GET/DELETE /admin/api-keys` | — | Key management (admin token) |
| `GET /health` · `/ready` · `/metrics` | — | Liveness, readiness (+ circuit states) and Prometheus |

Real example (`GET /v1/cep/01310-100`, trimmed). The first call took ~0.9 s; the second, served from cache, **~5 ms**:

```jsonc
{
  "data": {
    "cep": "01310-100",
    "logradouro": "Avenida Paulista",
    "codigo_ibge": "3550308",
    "ddd": "11",
    "coordenadas": { "latitude": -23.5475, "longitude": -46.63611 },
    "municipio": { "nome": "São Paulo", "mesorregiao": "Metropolitana de São Paulo", "regiao": "Sudeste" },
    "proveniencia": { "logradouro": "brasilapi", "codigo_ibge": "viacep", "coordenadas": "brasilapi" }
  },
  "meta": {
    "cache": "MISS",
    "fontes": [
      { "nome": "brasilapi", "status": "ok", "latencia_ms": 165.8 },
      { "nome": "viacep",    "status": "ok", "latencia_ms": 359.5 },
      { "nome": "opencep",   "status": "ok", "latencia_ms": 50.7 },
      { "nome": "ibge",      "status": "ok", "latencia_ms": 445.2, "detalhe": "cache MISS" }
    ],
    "avisos": [],
    "request_id": "d9c5f0a7f60b4bd2ac15bbfd3e389a61"
  }
}
```

With BrasilAPI down, the same lookup still returns 200: `coordenadas` is `null` (only BrasilAPI provided it), `meta.fontes` shows `brasilapi: erro` and `meta.avisos` explains the partial response.

---

## Architecture

```mermaid
flowchart LR
    C[Client] -->|X-API-Key| MW[Middleware<br/>request id · metrics · logs]
    MW --> AUTH[API key auth<br/>SHA-256 in Redis]
    AUTH --> RL[Rate limit<br/>sliding window Lua]
    RL --> SVC{Service}

    SVC -->|CEP| CACHE1[(Cache<br/>single-flight<br/>stale-if-error)]
    CACHE1 -->|miss| FAN[Parallel fan-out<br/>global deadline]
    FAN --> B1[BrasilAPI]
    FAN --> V1[ViaCEP]
    FAN --> O1[OpenCEP]
    FAN --> MERGE[Field-by-field merge<br/>+ provenance]
    MERGE --> IBGE[IBGE<br/>30-day cache]

    SVC -->|CNPJ| CACHE2[(Cache)]
    CACHE2 -->|miss| CHAIN[Fallback chain]
    CHAIN --> B2[BrasilAPI] -.failed.-> W2[CNPJ.ws<br/>token bucket 3/min] -.failed.-> R2[ReceitaWS<br/>token bucket 3/min]
    CHAIN --> ENR[Enrich address<br/>reuses CEP aggregator]

    subgraph "Per source"
      direction TB
      T[timeout] --> RT[retry + jitter] --> CB[circuit breaker]
    end
```

```
src/raiox/
├── main.py            # app factory + dependency wiring (lifespan)
├── core/              # typed config, JSON logs, RFC 9457 errors, ASGI middleware
├── security/          # API keys (generation, hashing, verification)
├── infra/             # cache, rate limiting (Lua), circuit breaker, retry, metrics
├── providers/         # one adapter per source → normalizes into the domain
├── domain/            # Pydantic models, CEP/CNPJ validators, merge
├── services/          # orchestration: fan-out/merge (CEP), fallback (CNPJ)
└── api/               # routes, dependencies, response building (ETag/304)
```

Layers only depend "inwards": `api → services → providers/infra → domain`. Adapters know nothing about caching or inbound HTTP, and services know nothing about each source's format.

---

## Engineering decisions

Each decision has an ADR in [`docs/adr/`](docs/adr) with context, alternatives and trade-offs.

### 1. Fan-out for CEP, fallback for CNPJ ([ADR 0001](docs/adr/0001-fan-out-vs-fallback.md))
CEP sources are free and **complementary**: BrasilAPI has coordinates, ViaCEP has the IBGE code and area code. Calling all of them in parallel costs the latency of the slowest one, capped by a deadline, and yields the most complete record. CNPJ sources have **~3 req/min quotas** and share the same origin (the Federal Revenue), so fan-out would only burn quota. There, the next source is only called if the previous one fails, and the unused ones show up as `ignorada` (skipped) in `meta`.

### 2. Per-source resilience ([ADR 0002](docs/adr/0002-resilience.md))
- **Timeout** per call and a global **deadline** on the fan-out: one slow source can't hold the response.
- **Retry only on transient errors** (5xx, network, timeout), with exponential backoff and *full jitter*. 4xx is never retried. Quota-limited sources get `retries=0`, because the fallback chain already plays that role.
- **Circuit breaker** per source (`closed → open → half_open`). A 404 does **not** count as a failure, since the source did answer. Validated live: with the sources down, the circuits open and subsequent requests fail in **~8 ms** without touching the network.
- **Partial responses**: one working source is enough. Whatever is missing goes into `meta.avisos`.

### 3. Caching ([ADR 0003](docs/adr/0003-cache.md))
- **Cache-aside** on Redis with versioned keys (`raiox:v1:...`) and per-resource TTLs: CEP 7d, CNPJ 24h, municipality 30d.
- **Negative caching** (404 for 1h): non-existent CEPs/CNPJs don't hammer the sources.
- **Single-flight** with `SET NX` on Redis: concurrent requests for the same key trigger **one** trip to the sources (tested with 10 concurrent requests). Since the lock lives in Redis, this also holds across replicas.
- **Stale-if-error**: each entry stores `fresh_until` and lives beyond it. If every source fails, the last copy is served with `X-Cache: STALE` and a warning.
- **HTTP caching**: weak `ETag` over the data, `If-None-Match → 304`, and `Cache-Control: private, max-age=<remaining freshness>`.
- **Fail-open**: Redis being down doesn't break lookups, it only disables the cache.

### 4. Rate limiting and quotas ([ADR 0004](docs/adr/0004-rate-limit.md))
- **Inbound:** *sliding window counter* in **atomic Lua**, per API key and plan (`demo`: 20/min, `free`: 30/min, `pro`: 300/min). O(1) memory per client, versus O(limit) for a sorted-set log. `RateLimit-*` and `Retry-After` headers.
- **Outbound:** per-source *token bucket* (CNPJ.ws and ReceitaWS: 3/min), shared across replicas. When the bucket is empty, the chain skips the source instead of taking a third-party 429. An exhausted local quota does **not** open the circuit, because the limit was ours, not the source's.

### 5. Authentication ([ADR 0005](docs/adr/0005-api-keys.md))
- API key in the `X-API-Key` header (shows up in Swagger's *Authorize*), with an `rx_live_` prefix that makes secret scanning easy.
- Redis stores only the **SHA-256**. The key has 256 random bits, so a slow hash (bcrypt) adds no security and lookup stays O(1). The plaintext key is shown exactly once.
- Admin via a Bearer token compared in constant time. In `prod`, the app **refuses to start** with a weak token.
- Redis runs with `volatile-lru`: under memory pressure only cache entries (with a TTL) are evicted, never the keys.

### 6. Errors, failures and observability ([ADR 0006](docs/adr/0006-errors-and-observability.md))
- Every error follows **RFC 9457** (`application/problem+json`) with a `request_id`.
- **Local validation before spending quota**: CEP and CNPJ with check digits, **including the alphanumeric CNPJ** (IN RFB 2.229/2024, effective July 2026).
- Structured JSON logs with a propagated `request_id`, and Prometheus metrics per route (template, no cardinality blow-up), per source, for cache and for rate limiting.

| If this goes down... | Behavior | Why |
|---|---|---|
| 1 CEP source | 200 partial + warning | other sources cover for it |
| every source | 200 `STALE` or 503 | an old copy beats no answer |
| IBGE | 200 without `municipio` + warning | enrichment is optional |
| Redis (cache) | lookup proceeds without cache | cache is an optimization |
| Redis (rate limit) | serves without limiting (fail-open) | availability over strictness |
| Redis (auth) | 503 (fail-closed) | security over availability |

---

## Running it

**Docker (recommended):**
```bash
cp .env.example .env          # set RAIOX_ADMIN_TOKEN
make up                       # API at http://localhost:8000, docs at /docs

# create an API key
curl -X POST localhost:8000/admin/api-keys \
  -H "Authorization: Bearer $RAIOX_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"name":"my-app","plan":"free"}'

curl -i localhost:8000/v1/cep/01310-100 -H "X-API-Key: rx_live_..."
curl -i localhost:8000/v1/empresas/00.000.000/0001-91 -H "X-API-Key: rx_live_..."
```

**Local (uv):**
```bash
make install
docker run -d -p 6379:6379 redis:7.4-alpine
make dev
```

**Deploying to Fly.io:** the repo ships a [`fly.toml`](fly.toml) (São Paulo region, auto-stop when idle, `/health` check).
```bash
fly launch --no-deploy --copy-config          # creates the app from fly.toml
fly redis create                              # Upstash Redis on Fly's private network
fly secrets set \
  RAIOX_ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  RAIOX_DEMO_API_KEY="$(python -c "import secrets; print('rx_live_' + secrets.token_urlsafe(24))")" \
  RAIOX_REDIS_URL="redis://default:...@....upstash.io:6379"
fly deploy
```
Set the secrets **before** the first deploy: with `RAIOX_ENV=prod` the app refuses to boot with a weak admin token.

**Public demo key:** when `RAIOX_DEMO_API_KEY` is set, the key is registered at startup with the low-limit `demo` plan. Registration is idempotent and self-heals if Redis is reset while the app is running, so the key published in a README stays valid. Rotating the secret revokes the old key on the next boot.

**Simulating failures:** point a source at an invalid host in `.env` (e.g. `RAIOX_BRASILAPI_URL=https://x.invalid`) and recreate the container. The warnings show up in `meta`, and `/ready` reports the circuit states.

## Quality

```bash
make check   # ruff + mypy --strict + pytest (85% minimum coverage)
```

- **79 tests**, **95% coverage**, with no network calls at all: sources are mocked with `respx` and Redis with `fakeredis` (Lua scripts included).
- They cover merging and source disagreement, circuit breaker transitions, retry, the sliding window with a controlled clock, the token bucket, concurrent single-flight, stale-if-error, negative caching, ETag/304, per-plan rate limiting, the key lifecycle, the fan-out deadline and the alphanumeric CNPJ.
- GitHub Actions CI: lint → types → tests → image build.

## Next steps

- OpenTelemetry (per-source traces) on top of the metrics
- Distributed circuit breaker (state in Redis) for many replicas
- Batch endpoint (`POST /v1/empresas:batch`) with semaphore-bounded fan-out
- Webhooks for company registry status changes (CNPJ)
- Background refresh (*stale-while-revalidate*) for hot keys

## Sources

[BrasilAPI](https://brasilapi.com.br) · [ViaCEP](https://viacep.com.br) · [OpenCEP](https://opencep.com) · [IBGE Localidades](https://servicodados.ibge.gov.br/api/docs/localidades) · [CNPJ.ws](https://docs.cnpj.ws) · [ReceitaWS](https://receitaws.com.br)

MIT License.
