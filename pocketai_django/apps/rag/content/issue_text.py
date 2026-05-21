from __future__ import annotations

from apps.knowledge.models import KnowledgeUpload


class ContentIssueTextMixin:

    @staticmethod
    def _render_issue_text(upload: KnowledgeUpload, *, max_issues: int = 5) -> str:
        issues_manager = getattr(upload, "issues", None)
        if not hasattr(issues_manager, "all"):
            return ""
        issues = list(issues_manager.all())[:max_issues]
        if not issues:
            return ""
        lines = ["[Ingestion Issues]"]
        for issue in issues:
            location = []
            if issue.page:
                location.append(f"page {issue.page.page_number}")
            if issue.table:
                location.append(f"table {issue.table.order_index}")
            if issue.table_row:
                location.append(f"row {issue.table_row.row_index}")
            if issue.table_cell:
                location.append(f"cell {issue.table_cell.column_index}")
            location_str = " • ".join(location)
            lines.append(f"- {issue.severity.upper()} {issue.issue_code}: {issue.description} ({location_str or 'no location'})")
        return "\n".join(lines)
