from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the Azure AI Search index used for knowledge retrieval."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--embedding-dim",
            type=int,
            default=None,
            help="Override embedding dimension (defaults to settings.EMBED_DIM).",
        )

    def handle(self, *args, **options) -> None:
        try:
            from apps.rag.integrations.azure_ai_search import AzureAISearchConfig, ensure_index
        except Exception as exc:
            raise CommandError(f"Azure AI Search dependency missing: {exc}") from exc

        config = AzureAISearchConfig.from_settings()
        if not config:
            raise CommandError(
                "Azure AI Search is not configured. Set AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_ADMIN_KEY, and AZURE_SEARCH_INDEX_NAME."
            )

        embedding_dim = options.get("embedding_dim")
        if embedding_dim is None:
            embedding_dim = int(getattr(settings, "EMBED_DIM", 384) or 384)
        if not isinstance(embedding_dim, int) or embedding_dim <= 0:
            raise CommandError("--embedding-dim must be a positive integer")

        ensure_index(config=config, embedding_dim=embedding_dim)
        self.stdout.write(self.style.SUCCESS(f"Azure AI Search index ensured: {config.index_name} (dim={embedding_dim})"))

