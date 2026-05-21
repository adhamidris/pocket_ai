from __future__ import annotations

from django.http import HttpRequest

from apps.conversations.portal_turn.events import PORTAL_TURN_EVENTS_NOTIFY_CHANNEL


def _parse_turn_since_seq(request: HttpRequest) -> int:
    raw = request.GET.get("since") or request.GET.get("since_seq") or ""
    if not raw:
        raw = request.META.get("HTTP_LAST_EVENT_ID", "")
    try:
        value = int(str(raw).strip())
        return value if value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _parse_session_since_id(request: HttpRequest) -> str | None:
    raw = request.GET.get("since") or request.GET.get("since_id") or ""
    if not raw:
        raw = request.META.get("HTTP_LAST_EVENT_ID", "")
    raw = str(raw or "").strip()
    return raw or None


def _open_portal_turn_listen_connection():
    """
    Open a dedicated Postgres connection for LISTEN/NOTIFY.

    Why:
    - The portal turn runner appends events in the background and persists them to Postgres.
    - Streaming should be push-based (LISTEN/NOTIFY), not DB-polling, to feel like modern token streaming.
    - We intentionally do not reuse Django's ORM connection for LISTEN to avoid interfering with request queries.
    """
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        from django.db import connections

        db = connections["default"].settings_dict
        options = db.get("OPTIONS") if isinstance(db.get("OPTIONS"), dict) else {}
        connect_kwargs: dict[str, object] = {
            "dbname": db.get("NAME") or "",
            "user": db.get("USER") or "",
            "password": db.get("PASSWORD") or "",
            "host": db.get("HOST") or "",
            "port": db.get("PORT") or "",
        }
        for key in (
            "sslmode",
            "sslrootcert",
            "sslcert",
            "sslkey",
            "sslcrl",
            "application_name",
        ):
            value = options.get(key)
            if value:
                connect_kwargs[key] = value
        conn = psycopg2.connect(**connect_kwargs)  # type: ignore[arg-type]
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cursor:
            cursor.execute(f"LISTEN {PORTAL_TURN_EVENTS_NOTIFY_CHANNEL}")
        return conn
    except Exception:  # pragma: no cover - best effort only
        return None
