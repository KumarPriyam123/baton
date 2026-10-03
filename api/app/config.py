from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    env: Literal["dev", "test", "prod"] = Field("dev", validation_alias="ENV")
    database_url: str = Field(validation_alias="DATABASE_URL")
    test_database_url: str | None = Field(None, validation_alias="TEST_DATABASE_URL")
    demo_mode: bool = Field(False, validation_alias="DEMO_MODE")
    session_ttl_hours: int = Field(12, ge=1, validation_alias="SESSION_TTL_HOURS")
    fault_notify_fail_rate: float = Field(
        0.0, ge=0.0, le=1.0, validation_alias="FAULT_NOTIFY_FAIL_RATE"
    )
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        "info", validation_alias="LOG_LEVEL"
    )

    @model_validator(mode="after")
    def refuse_demo_and_faults_in_prod(self) -> Self:
        """Demo accounts and fault injection must never be reachable in production."""
        if self.env != "prod":
            return self
        enabled = []
        if self.demo_mode:
            enabled.append("DEMO_MODE")
        if self.fault_notify_fail_rate > 0:
            enabled.append("FAULT_NOTIFY_FAIL_RATE")
        if enabled:
            raise ValueError(f"ENV=prod refuses to start with {', '.join(enabled)} enabled")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
