import pytest
from pydantic import ValidationError

from app.config import Settings

DB = "postgresql+asyncpg://u:p@localhost/db"


def test_defaults_are_safe() -> None:
    settings = Settings(database_url=DB)
    assert settings.env == "dev"
    assert settings.demo_mode is False
    assert settings.fault_notify_fail_rate == 0.0
    assert settings.session_ttl_hours == 12


def test_prod_refuses_to_start_with_demo_mode() -> None:
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        Settings(database_url=DB, env="prod", demo_mode=True)


def test_prod_refuses_to_start_with_fault_injection() -> None:
    with pytest.raises(ValidationError, match="FAULT_NOTIFY_FAIL_RATE"):
        Settings(database_url=DB, env="prod", fault_notify_fail_rate=0.3)


def test_prod_names_every_offending_variable() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(database_url=DB, env="prod", demo_mode=True, fault_notify_fail_rate=0.5)
    assert "DEMO_MODE" in str(excinfo.value)
    assert "FAULT_NOTIFY_FAIL_RATE" in str(excinfo.value)


def test_prod_starts_when_demo_and_faults_are_off() -> None:
    settings = Settings(database_url=DB, env="prod", demo_mode=False, fault_notify_fail_rate=0)
    assert settings.env == "prod"


def test_prod_guard_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("DEMO_MODE", "true")
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        Settings()


@pytest.mark.parametrize("env", ["dev", "test"])
def test_demo_and_faults_are_allowed_outside_prod(env: str) -> None:
    settings = Settings(database_url=DB, env=env, demo_mode=True, fault_notify_fail_rate=0.3)
    assert settings.demo_mode is True
