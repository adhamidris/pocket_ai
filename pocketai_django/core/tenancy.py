from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from django.db import connection

logger = logging.getLogger(__name__)


def _current_setting(name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        row = cursor.fetchone()
    return row[0] if row else None


def _set_setting(name: str, value: str | None) -> None:
    with connection.cursor() as cursor:
        if value is None:
            cursor.execute(f"RESET {name}")
        else:
            cursor.execute(f"SET {name} = %s", [value])


@contextmanager
def tenant_context(business_id: object | None, *, bypass: bool = False) -> Iterator[None]:
    """
    Scope the database session to a specific tenant for RLS enforcement.

    Uses SET/RESET so the context can span multiple transactions without
    holding a long-lived atomic block.
    """
    if connection.vendor != "postgresql":
        yield
        return
    tenant_value = str(business_id) if business_id else None
    prev_tenant = _current_setting("app.current_tenant")
    prev_bypass = _current_setting("app.tenant_bypass")

    if tenant_value != prev_tenant:
        _set_setting("app.current_tenant", tenant_value)
        logger.debug(
            "tenant_context.set tenant=%s prev=%s bypass=%s",
            tenant_value,
            prev_tenant,
            bypass,
        )
    if bypass:
        _set_setting("app.tenant_bypass", "1")

    try:
        yield
    finally:
        if bypass:
            _set_setting("app.tenant_bypass", prev_bypass)
        if tenant_value != prev_tenant:
            _set_setting("app.current_tenant", prev_tenant)
            logger.debug(
                "tenant_context.restored tenant=%s from=%s",
                prev_tenant,
                tenant_value,
            )


@contextmanager
def tenant_bypass() -> Iterator[None]:
    """Allow cross-tenant access for trusted admin workflows."""
    with tenant_context(None, bypass=True):
        yield
