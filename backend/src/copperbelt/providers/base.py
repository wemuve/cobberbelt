"""External provider framework.

Status vocabulary (never inferred from configuration alone):

* ``unconfigured``          – required credentials/settings absent; calls are refused.
* ``configured_unverified`` – settings present, no successful live check recorded yet.
* ``live_verified``         – last real check against the provider succeeded.
* ``unreachable``           – last check could not reach the provider (network/DNS/TLS).
* ``error``                 – provider reached but rejected the request (auth, quota, licence).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select

from copperbelt.db import system_session
from copperbelt.errors import ProviderUnavailable
from copperbelt.models.ops import ProviderCheck, ProviderUsage


@dataclass
class CheckResult:
    status: str
    detail: str
    latency_ms: int | None = None


@dataclass
class ProviderInfo:
    id: str
    name: str
    purpose: str
    requires: list[str]
    licence_notes: str
    docs_url: str
    capabilities: list[str] = field(default_factory=list)


class Provider:
    info: ProviderInfo
    transport: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport here

    def configured(self) -> bool:
        raise NotImplementedError

    def missing_configuration(self) -> list[str]:
        return [] if self.configured() else list(self.info.requires)

    def client(self, **kw: Any) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), transport=self.transport, **kw)

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderUnavailable(
                f"{self.info.name} is not configured (missing: {', '.join(self.missing_configuration())})",
                code="provider_unconfigured",
            )

    def _check(self) -> CheckResult:
        raise NotImplementedError

    def check(self) -> CheckResult:
        """Perform a real call and persist the outcome."""
        if not self.configured():
            result = CheckResult("unconfigured", f"Missing: {', '.join(self.missing_configuration())}")
        else:
            t0 = time.monotonic()
            try:
                result = self._check()
            except httpx.TransportError as exc:
                result = CheckResult("unreachable", f"{type(exc).__name__}: {exc}")
            result.latency_ms = int((time.monotonic() - t0) * 1000)
        with system_session() as s:
            s.add(ProviderCheck(provider=self.info.id, status=result.status, detail=result.detail[:4000],
                                latency_ms=result.latency_ms))
        return result

    def status(self) -> dict[str, Any]:
        with system_session() as s:
            last = s.scalar(select(ProviderCheck).where(ProviderCheck.provider == self.info.id)
                            .order_by(ProviderCheck.checked_at.desc()).limit(1))
        if not self.configured():
            status = "unconfigured"
        elif last is None or last.status == "unconfigured":
            status = "configured_unverified"
        else:
            status = last.status
        return {
            "id": self.info.id, "name": self.info.name, "purpose": self.info.purpose, "status": status,
            "missing_configuration": self.missing_configuration(), "capabilities": self.info.capabilities,
            "licence_notes": self.info.licence_notes, "docs_url": self.info.docs_url,
            "last_check": {"status": last.status, "detail": last.detail, "latency_ms": last.latency_ms,
                           "checked_at": last.checked_at.isoformat()} if last else None,
        }


def record_usage(provider: str, operation: str, units: float, unit_name: str, *, org_id=None,  # noqa: ANN001
                 cost_usd: float | None = None, detail: dict | None = None) -> None:
    with system_session() as s:
        s.add(ProviderUsage(org_id=org_id, provider=provider, operation=operation, units=units, unit_name=unit_name,
                            estimated_cost_usd=cost_usd, detail=detail or {}))
