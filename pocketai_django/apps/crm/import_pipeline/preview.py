from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from django.core.exceptions import ValidationError

from apps.accounts.models import BusinessProfile, User
from apps.crm.import_pipeline.config import PREVIEW_ROW_LIMIT, PREVIEW_SAMPLE_LIMIT
from apps.crm.import_pipeline.parsing import _load_rows, _validate_uploaded_file
from apps.crm.models import CrmImportSourceFile, CrmImportSourceFormat


@dataclass(frozen=True)
class ImportPreview:
    columns: Sequence[str]
    sample_rows: Sequence[dict[str, str]]
    total_rows: int


def detect_source_format(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".xlsx":
        return CrmImportSourceFormat.XLSX
    if suffix == ".csv":
        return CrmImportSourceFormat.CSV
    raise ValidationError({"source_file": "Only CSV and XLSX imports are supported."})


def store_import_source_file(*, business_profile: BusinessProfile, uploaded_by: User | None, uploaded_file) -> CrmImportSourceFile:
    _validate_uploaded_file(uploaded_file)
    file_format = detect_source_format(getattr(uploaded_file, "name", ""))
    preview = build_preview(uploaded_file, file_format=file_format)
    uploaded_file.seek(0)
    source = CrmImportSourceFile.objects.create(
        business_profile=business_profile,
        uploaded_by=uploaded_by,
        original_filename=getattr(uploaded_file, "name", "crm-import"),
        file_format=file_format,
        file=uploaded_file,
        file_size_bytes=int(getattr(uploaded_file, "size", 0) or 0),
        column_snapshot=list(preview.columns),
        sample_rows=list(preview.sample_rows),
    )
    return source


def build_preview(uploaded_file, *, file_format: str) -> ImportPreview:
    rows = list(_load_rows(uploaded_file, file_format=file_format, limit=PREVIEW_ROW_LIMIT))
    columns = tuple(rows[0].keys()) if rows else tuple()
    return ImportPreview(columns=columns, sample_rows=tuple(rows[:PREVIEW_SAMPLE_LIMIT]), total_rows=len(rows))
