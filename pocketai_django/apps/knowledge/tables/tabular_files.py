from __future__ import annotations

from apps.knowledge.tables.dataset_delimited import IngestionDelimitedDatasetMixin
from apps.knowledge.tables.dataset_common import IngestionDatasetCommonMixin
from apps.knowledge.tables.dataset_jsonl import IngestionJsonlDatasetMixin
from apps.knowledge.tables.dataset_xls import IngestionXlsDatasetMixin
from apps.knowledge.tables.dataset_xlsx import IngestionXlsxDatasetMixin
from apps.knowledge.tables.delimited_files import IngestionDelimitedFilesMixin
from apps.knowledge.tables.spreadsheet_common import IngestionSpreadsheetCommonMixin
from apps.knowledge.tables.spreadsheet_xls import IngestionXlsExtractionMixin


class IngestionTabularFilesMixin(
    IngestionDatasetCommonMixin,
    IngestionDelimitedDatasetMixin,
    IngestionDelimitedFilesMixin,
    IngestionXlsxDatasetMixin,
    IngestionXlsDatasetMixin,
    IngestionJsonlDatasetMixin,
    IngestionSpreadsheetCommonMixin,
    IngestionXlsExtractionMixin,
):
    pass
