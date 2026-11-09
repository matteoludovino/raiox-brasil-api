"""Prometheus metrics (single module so collectors are registered exactly once)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "raiox_http_requests_total", "Incoming HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "raiox_http_request_duration_seconds", "Incoming HTTP request latency", ["method", "route"]
)
UPSTREAM_REQUESTS = Counter(
    "raiox_upstream_requests_total", "Calls to external sources", ["source", "outcome"]
)
UPSTREAM_LATENCY = Histogram(
    "raiox_upstream_request_duration_seconds", "External source latency", ["source"]
)
CACHE_EVENTS = Counter("raiox_cache_events_total", "Cache events", ["resource", "event"])
RATE_LIMITED = Counter("raiox_rate_limited_total", "Requests blocked by rate limiting", ["plan"])
