# Ingestion Snapshot: CIB Teller (VLM ON)

- upload_id: `ebca6c00-97b1-4776-85a9-f4b4b1f9e4b0`
- job_id: `913f93e7-1236-4ed4-94bc-c2349b569b85`
- issues_count: `0`
- table_count: `1`
- chunk_counts: `{'page_blocks/None/None': 3, 'table_annotation/None/supporting': 1, 'table_row/row/drill_down': 20, 'table_summary/summary/primary': 1}`
- detected_via: `azure_di+vlm`
- row_count: `21`
- rows_6_17_patterns: `{(('tariff', 'wealth'), 'explicit_cells'): 12}`

## Term Hits
- `Cost per Bag`: [{'chunk_index': 1, 'content_source': 'page_blocks', 'table_chunk_role': None, 'table_row_index': None}]
- `T+5`: [{'chunk_index': 22, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 20}, {'chunk_index': 23, 'content_source': 'table_summary', 'table_chunk_role': 'summary', 'table_row_index': None}]
- `T+3`: [{'chunk_index': 21, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 19}, {'chunk_index': 23, 'content_source': 'table_summary', 'table_chunk_role': 'summary', 'table_row_index': None}]
- `same day value date`: [{'chunk_index': 20, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 18}, {'chunk_index': 21, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 19}, {'chunk_index': 22, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 20}, {'chunk_index': 23, 'content_source': 'table_summary', 'table_chunk_role': 'summary', 'table_row_index': None}]
- `USD 5`: [{'chunk_index': 9, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 7}]
- `USD 2`: [{'chunk_index': 5, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 3}, {'chunk_index': 11, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 9}, {'chunk_index': 15, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 13}, {'chunk_index': 17, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 15}, {'chunk_index': 19, 'content_source': 'table_row', 'table_chunk_role': 'row', 'table_row_index': 17}]