"""Role → permission mapping. Checked server-side on every mutating endpoint."""

from __future__ import annotations

from copperbelt.models.identity import Role

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {}

_VIEW = {
    "documents:read", "search:run", "features:read", "projects:read", "sources:read", "providers:read",
}
_REVIEW = _VIEW | {"claims:review", "features:review", "assessments:review"}
_ANALYST = _REVIEW | {
    "documents:upload", "documents:process", "claims:write", "features:write", "projects:write",
    "assessments:write", "aoi:write", "sources:write", "providers:use",
}
_ADMIN = _ANALYST | {
    "members:manage", "documents:delete", "documents:grant", "audit:read", "jobs:manage",
    "assessments:approve", "providers:check", "documents:read_confidential",
}
_OWNER = _ADMIN | {"org:manage"}

ROLE_PERMISSIONS.update(
    {
        Role.viewer: frozenset(_VIEW),
        Role.reviewer: frozenset(_REVIEW),
        Role.analyst: frozenset(_ANALYST),
        Role.admin: frozenset(_ADMIN),
        Role.owner: frozenset(_OWNER),
    }
)


def has_permission(role: Role, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS[role]
