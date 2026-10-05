"""Provider-independent evidence and company contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator


class Evidence(BaseModel):
    """An attributed observation; downstream outputs retain these records."""

    model_config = ConfigDict(frozen=True)

    source_url: HttpUrl
    observed_at: datetime
    summary: str

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value


class Company(BaseModel):
    """A company with an upstream-resolved canonical identity."""

    model_config = ConfigDict(frozen=True)

    identity_key: str
    name: str
    evidence: tuple[Evidence, ...]

    @field_validator("identity_key", "name")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("evidence")
    @classmethod
    def require_evidence(cls, value: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        if not value:
            raise ValueError("company must have evidence")
        return value
