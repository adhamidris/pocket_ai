# Ingestion Snapshot: CIB Teller (VLM OFF)

- upload_id: `ae4bfb72-d3f6-48c1-81bf-69797ac074c3`
- job_id: `7036ee79-d899-4b87-8ab9-64c3f35a1077`
- issues_count: `0`
- table_count: `1`
- chunk_counts: `{'page_blocks/None/None': 4, 'table_annotation/None/supporting': 1, 'table_row/row/drill_down': 23, 'table_summary/summary/primary': 1}`
- detected_via: `azure_di`
- row_count: `24`
- rows_6_17_patterns: `{(('tariff', 'wealth'), 'explicit_cells'): 12}`

## Term Hits
- `Cost per Bag`: [{'chunk_index': 2, 'content_source': 'page_blocks', 'table_chunk_role': None, 'table_row_index': None}]
- `T+5`: [{'chunk_index': 25, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 22}, {'chunk_index': 27, 'content_source': 'table_summary', 'table_chunk_role': 'summary', 'table_row_index': None}]
- `T+3`: [{'chunk_index': 23, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 20}, {'chunk_index': 24, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 21}, {'chunk_index': 27, 'content_source': 'table_summary', 'table_chunk_role': 'summary', 'table_row_index': None}]
- `same day value date`: [{'chunk_index': 21, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 18}, {'chunk_index': 22, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 19}, {'chunk_index': 23, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 20}, {'chunk_index': 24, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 21}, {'chunk_index': 25, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 22}, {'chunk_index': 27, 'content_source': 'table_summary', 'table_chunk_role': 'summary', 'table_row_index': None}]
- `USD 5`: [{'chunk_index': 10, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 7}]
- `USD 2`: [{'chunk_index': 6, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 3}, {'chunk_index': 12, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 9}, {'chunk_index': 16, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 13}, {'chunk_index': 18, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 15}, {'chunk_index': 20, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 17}]