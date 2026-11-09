# ADR 0006: RFC 9457 errors, early validation and observability

**Status:** accepted · **Date:** 2026-11-09

## Errors
Every error response uses `application/problem+json` (RFC 9457): `type`, `title`, `status`, `detail`, `instance` and `request_id`. Validation errors include `errors[]`. Unhandled exceptions become a generic 500 (no stack trace leaked) with a `request_id` to correlate with the logs. Titles and details are in Portuguese, like the rest of the JSON contract.

## Validation before upstream calls
CEP and CNPJ are validated locally (format + check digit) **before** any call, so invalid input never spends third-party quota. The CNPJ validator already implements the **alphanumeric format** (IN RFB 2.229/2024, effective July 2026): positions 1–12 in `[0-9A-Z]`, value = `ord(c) − 48`, same modulo 11. If the sources still reject a valid alphanumeric CNPJ, the API answers 503 with an explicit message instead of pretending it's a 404.

## Observability
- **Pure ASGI** middleware (not `BaseHTTPMiddleware`): accepts the client's `X-Request-ID` if it is safe (regex) or generates one. It is propagated via `contextvars` to the logs, `meta.request_id` and the response header.
- JSON logs (structlog), one access line per request with the route template and duration.
- Prometheus metrics: routes are labeled by **template** (`/v1/cep/{cep}`), never by the raw path, to avoid a cardinality blow-up. There are also per-source latency and outcome, cache events and rate-limit blocks.
