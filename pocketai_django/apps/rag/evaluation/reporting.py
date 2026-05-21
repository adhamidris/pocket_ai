from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from django.utils import timezone

from apps.rag.knowledge_search import KnowledgeSnippet
from apps.rag.observability.logging import rag_log

if TYPE_CHECKING:
    from apps.rag.evaluation.types import EvaluationReport


class EvaluationReportingMixin:

    @staticmethod
    def _snippet_token_count(snippet: KnowledgeSnippet) -> int:
        content = (snippet.content or "").strip()
        if not content:
            content = (snippet.summary or "").strip()
        return len(content.split()) if content else 0

    @staticmethod
    def _snippet_previews(snippets: Sequence[KnowledgeSnippet]) -> tuple[Mapping[str, Any], ...]:
        previews: list[Mapping[str, Any]] = []
        for snippet in snippets:
            content = (snippet.content or "").strip()
            summary = (snippet.summary or "").strip()
            preview_source = content or summary
            if len(preview_source) > 400:
                preview_source = f"{preview_source[:400]}..."
            previews.append(
                {
                    "snippet_id": str(snippet.id),
                    "upload_id": str(snippet.upload_id) if snippet.upload_id else None,
                    "title": snippet.title,
                    "summary": summary[:200] + "..." if len(summary) > 200 else summary,
                    "preview": preview_source,
                    "search_stage": snippet.search_stage,
                    "is_table_chunk": bool(snippet.is_table_chunk),
                }
            )
        return tuple(previews)


    def _export_reports(self, reports: Sequence[EvaluationReport], destination: Path) -> None:
        payload = {
            "generated_at": timezone.now().isoformat(),
            "reports": [
                {
                    "slug": report.slug,
                    "business_id": str(report.business_id),
                    "metrics": report.metrics,
                    "latencies": report.latencies,
                    "status": report.status,
                    "violations": report.violations,
                    "fixtures": list(report.fixtures),
                    "feedback_cases": report.feedback_cases,
                    "observations": [asdict(obs) for obs in report.observations],
                }
                for report in reports
            ],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2))
        rag_log(
            "eval.export",
            {"path": destination},
            indent=1,
        )
