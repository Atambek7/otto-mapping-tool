"""Runtime mode and fail-closed feature flags for the isolated OttoRestart API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping


class RuntimeConfigurationError(ValueError):
    """Raised when runtime configuration is invalid or ambiguous."""


_GTIN_EVIDENCE_TYPE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,63}$")


class RuntimeMode(str, Enum):
    LEGACY_READ_ONLY = "LEGACY_READ_ONLY"
    DEMO = "DEMO"
    PRODUCTION = "PRODUCTION"
    TEST = "TEST"


def _read_bool(environment: Mapping[str, str], name: str) -> bool:
    value = environment.get(name, "false").strip().casefold()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off", ""}:
        return False
    raise RuntimeConfigurationError(f"{name} must be a boolean value.")


@dataclass(frozen=True)
class RuntimeConfig:
    mode: RuntimeMode = RuntimeMode.LEGACY_READ_ONLY
    local_writes_enabled: bool = False
    otto_read_enabled: bool = False
    aftercool_read_enabled: bool = False
    openai_enabled: bool = False
    product_write_enabled: bool = False
    availability_write_enabled: bool = False
    discovery_schedule_enabled: bool = False
    gtin_approved_evidence_types: frozenset[str] = frozenset()

    @property
    def legacy_mutations_allowed(self) -> bool:
        """Allow old local mutation routes only in explicitly enabled demo/test."""
        return self.mode in {RuntimeMode.DEMO, RuntimeMode.TEST} and self.local_writes_enabled

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> RuntimeConfig:
        raw_mode = environment.get("OTTORESTART_MODE", RuntimeMode.LEGACY_READ_ONLY.value)
        try:
            mode = RuntimeMode(raw_mode.strip().upper())
        except ValueError as error:
            supported = ", ".join(item.value for item in RuntimeMode)
            raise RuntimeConfigurationError(
                f"OTTORESTART_MODE must be one of: {supported}."
            ) from error

        raw_evidence_types = environment.get("OTTORESTART_GTIN_APPROVED_EVIDENCE_TYPES", "")
        evidence_types = frozenset(
            value.strip().upper() for value in raw_evidence_types.split(",") if value.strip()
        )
        if any(not _GTIN_EVIDENCE_TYPE.fullmatch(value) for value in evidence_types):
            raise RuntimeConfigurationError(
                "OTTORESTART_GTIN_APPROVED_EVIDENCE_TYPES contains an invalid evidence type."
            )

        return cls(
            mode=mode,
            local_writes_enabled=_read_bool(environment, "OTTORESTART_LOCAL_WRITES_ENABLED"),
            otto_read_enabled=_read_bool(environment, "OTTORESTART_OTTO_READ_ENABLED"),
            aftercool_read_enabled=_read_bool(environment, "OTTORESTART_AFTERCOOL_READ_ENABLED"),
            openai_enabled=_read_bool(environment, "OTTORESTART_OPENAI_ENABLED"),
            product_write_enabled=_read_bool(environment, "OTTORESTART_PRODUCT_WRITE_ENABLED"),
            availability_write_enabled=_read_bool(environment, "OTTORESTART_AVAILABILITY_WRITE_ENABLED"),
            discovery_schedule_enabled=_read_bool(environment, "OTTORESTART_DISCOVERY_SCHEDULE_ENABLED"),
            gtin_approved_evidence_types=evidence_types,
        )
