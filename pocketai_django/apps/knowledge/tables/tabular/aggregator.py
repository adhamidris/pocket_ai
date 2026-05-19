from __future__ import annotations

from apps.knowledge.tables.tabular.dataset_common import IngestionDatasetCommonMixin
from apps.knowledge.tables.tabular.dataset_delimited import IngestionDelimitedDatasetMixin
from apps.knowledge.tables.tabular.dataset_jsonl import IngestionJsonlDatasetMixin
from apps.knowledge.tables.tabular.dataset_xls import IngestionXlsDatasetMixin
from apps.knowledge.tables.tabular.dataset_xlsx import IngestionXlsxDatasetMixin
from apps.knowledge.tables.tabular.delimited import IngestionDelimitedFilesMixin
from apps.knowledge.tables.tabular.spreadsheet_common import IngestionSpreadsheetCommonMixin
from apps.knowledge.tables.tabular.spreadsheet_xls import IngestionXlsExtractionMixin


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
