from __future__ import annotations

import logging
from typing import Any

from apps.accounts.models import BusinessProfile
from apps.mcp.models import McpConnection, McpConnectionAuditEvent

logger = logging.getLogger(__name__)


def _log_mcp_audit(
    *,
    business: BusinessProfile,
    connection: McpConnection | None,
    actor,
    action: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        McpConnectionAuditEvent.objects.create(
            business_profile=business,
            connection=connection,
            connection_id_snapshot=(connection.id if connection else None),
            actor_user=actor if getattr(actor, "is_authenticated", False) else None,
            action=action,
            description=description or "",
            metadata=metadata or {},
        )
    except Exception:  # pragma: no cover
        logger.exception("mcp_audit_write_failed action=%s business=%s connection=%s", action, business.id, getattr(connection, "id", None))
