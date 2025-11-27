from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.services.embeddings import warm_rag_embeddings


class Command(BaseCommand):
    help = "Warm the KnowledgeSearchService embedding backend."

    def handle(self, *args, **options):
        warm_rag_embeddings()
        self.stdout.write(self.style.SUCCESS("RAG embedding backend warmed."))
