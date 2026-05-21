from __future__ import annotations

from django.utils.translation import gettext as _

from apps.crm.dashboard.shared import _format_file_size, _mapping_target_label
from apps.crm.models import CrmImportJob


def _build_import_preview(source, *, mapping: dict[str, object], selected_template=None, template_name: str = "") -> dict[str, object]:
    contact_mapping = mapping.get("contact") if isinstance(mapping.get("contact"), dict) else {}
    company_mapping = mapping.get("company") if isinstance(mapping.get("company"), dict) else {}
    mapped_columns = set()
    sections: list[dict[str, object]] = []

    for key, title in (("contact", _("Contact mapping")), ("company", _("Company mapping"))):
        config = contact_mapping if key == "contact" else company_mapping
        column_map = config.get("columns") if isinstance(config, dict) else {}
        entries = []
        if isinstance(column_map, dict):
            for source_column in source.column_snapshot:
                target = column_map.get(source_column)
                if not target:
                    continue
                mapped_columns.add(source_column)
                entries.append({"source": source_column, "target": _mapping_target_label(str(target))})
        external_identity = None
        if key == "contact":
            identity = config.get("external_identity") if isinstance(config, dict) else None
            if isinstance(identity, dict) and identity.get("source_column"):
                mapped_columns.add(str(identity["source_column"]))
                external_identity = {
                    "source": str(identity["source_column"]),
                    "system": str(identity.get("source_system") or "import"),
                    "object_type": str(identity.get("external_object_type") or "contact"),
                    "label": str(identity.get("external_label") or identity["source_column"]),
                }
        sections.append(
            {
                "key": key,
                "title": title,
                "entries": entries,
                "external_identity": external_identity,
            }
        )

    unmapped_columns = [column for column in source.column_snapshot if column not in mapped_columns]
    warnings: list[str] = []
    if not sections[0]["entries"] and not sections[1]["entries"]:
        warnings.append(_("No default field mapping was detected. Consider saving a reusable template before you queue this import."))
    if unmapped_columns:
        warnings.append(
            _("Some columns are currently unmapped. They will be ignored unless you later turn them into custom fields or update the mapping template.")
        )
    if not sections[0]["external_identity"]:
        warnings.append(_("No external ID column was detected, so source-system exact-match updates are not available for this file."))
    preview_rows = [
        {
            "cells": [row.get(column, "") for column in source.column_snapshot],
        }
        for row in list(source.sample_rows or [])
    ]
    return {
        "source": source,
        "mapping": mapping,
        "selected_template": selected_template,
        "template_name": template_name,
        "sections": sections,
        "unmapped_columns": unmapped_columns,
        "warnings": warnings,
        "sample_rows": preview_rows,
        "column_count": len(source.column_snapshot or []),
        "sample_count": len(preview_rows),
        "file_size_label": _format_file_size(source.file_size_bytes),
        "file_format_label": str(source.file_format).upper(),
    }


def _serialize_import_job(job: CrmImportJob) -> dict[str, object]:
    summary = job.summary or {}
    total_processed = sum(int(summary.get(key, 0) or 0) for key in ("created", "updated", "duplicates", "failed", "skipped"))
    return {
        "id": str(job.id),
        "status": job.status,
        "status_label": job.get_status_display(),
        "source_name": job.source_file.original_filename,
        "template_name": job.template.name if job.template_id else "",
        "summary": {
            "created": int(summary.get("created", 0) or 0),
            "updated": int(summary.get("updated", 0) or 0),
            "duplicates": int(summary.get("duplicates", 0) or 0),
            "failed": int(summary.get("failed", 0) or 0),
            "skipped": int(summary.get("skipped", 0) or 0),
        },
        "total_processed": total_processed,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "error_detail": job.error_detail,
    }
