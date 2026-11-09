"""Typed configuration, loaded from environment variables (RAIOX_ prefix)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Plan = Literal["free", "pro", "demo"]
API_KEY_PREFIX = "rx_live_"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAIOX_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    redis_url: str = "redis://localhost:6379/0"
    redis_timeout_s: float = Field(default=3.0, gt=0)
    cache_prefix: str = "raiox:v1"
    admin_token: SecretStr = SecretStr("change-me")

    # --- Upstream sources ---------------------------------------------------------
    brasilapi_url: str = "https://brasilapi.com.br/api"
    viacep_url: str = "https://viacep.com.br/ws"
    opencep_url: str = "https://opencep.com/v1"
    ibge_url: str = "https://servicodados.ibge.gov.br/api/v1"
    cnpjws_url: str = "https://publica.cnpj.ws"
    receitaws_url: str = "https://receitaws.com.br/v1"

    # --- Resilience ----------------------------------------------------------------
    upstream_timeout_s: float = Field(default=4.0, gt=0)
    upstream_retries: int = Field(default=2, ge=0)
    retry_base_delay_s: float = Field(default=0.2, ge=0)
    retry_max_delay_s: float = Field(default=2.0, ge=0)
    aggregate_deadline_s: float = Field(default=6.0, gt=0)
    # Hard cap for optional enrichment (IBGE): it must never dominate response time.
    enrichment_deadline_s: float = Field(default=2.0, gt=0)
    breaker_failure_threshold: int = Field(default=5, ge=1)
    breaker_reset_timeout_s: float = Field(default=30.0, gt=0)

    # --- Cache (seconds) -----------------------------------------------------------
    ttl_cep_s: int = 7 * 24 * 3600
    ttl_cnpj_s: int = 24 * 3600
    ttl_ibge_s: int = 30 * 24 * 3600
    ttl_negative_s: int = 3600
    # Results built while a source was failing transiently expire fast, so they heal.
    ttl_degraded_s: int = 300
    ttl_stale_s: int = 30 * 24 * 3600
    cache_lock_ttl_s: float = 10.0
    cache_lock_wait_s: float = 7.0

    # --- Inbound rate limit (per API key) ------------------------------------------
    rate_limit_window_s: int = Field(default=60, ge=1)
    rate_limit_free: int = Field(default=30, ge=1)
    rate_limit_pro: int = Field(default=300, ge=1)
    # Shared by every visitor of the public demo, so it is deliberately low.
    rate_limit_demo: int = Field(default=20, ge=1)

    # --- Public demo key (seeded idempotently at startup) ---------------------------
    demo_api_key: SecretStr | None = None

    # --- Outbound quota (token bucket per source, req/min) -------------------------
    outbound_cnpjws_per_min: int = Field(default=3, ge=1)
    outbound_receitaws_per_min: int = Field(default=3, ge=1)

    @property
    def rate_limits(self) -> dict[Plan, int]:
        return {
            "free": self.rate_limit_free,
            "pro": self.rate_limit_pro,
            "demo": self.rate_limit_demo,
        }

    @model_validator(mode="after")
    def _require_strong_admin_token_in_prod(self) -> Settings:
        token = self.admin_token.get_secret_value()
        if self.env == "prod" and (token == "change-me" or len(token) < 32):  # noqa: S105
            raise ValueError("RAIOX_ADMIN_TOKEN must be at least 32 characters in production")
        return self

    @model_validator(mode="after")
    def _validate_demo_key(self) -> Settings:
        if self.demo_api_key is None:
            return self
        key = self.demo_api_key.get_secret_value()
        if not key.startswith(API_KEY_PREFIX) or len(key) < len(API_KEY_PREFIX) + 16:
            raise ValueError(
                f"RAIOX_DEMO_API_KEY must start with {API_KEY_PREFIX!r} and have 16+ random chars"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
