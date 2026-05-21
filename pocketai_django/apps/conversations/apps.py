from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.conversations"
    verbose_name = "Conversations"

    def ready(self) -> None:
        # Register portal session event publishers (Redis Streams) for Phase 4.
        # Import side effects are intentional.
        from .portal_session import signals  # noqa: F401
