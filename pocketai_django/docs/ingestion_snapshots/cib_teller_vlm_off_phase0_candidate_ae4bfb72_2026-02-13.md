# Ingestion Snapshot: cib_teller_vlm_off_phase0_candidate

- upload_id: `ae4bfb72-d3f6-48c1-81bf-69797ac074c3`
- business_profile_id: `52885074-caa8-4983-aaf7-31bf3283bffd`
- captured_at: `2026-02-13T17:30:35.590035+00:00`
- table_count: `1`
- table_data_row_count: `23`
- row_chunk_count: `23`
- multi_scope_rate: `0.913`
- ambiguous_scope_rate: `0.087`
- inferred_scope_rate: `0.1304`
- table_bbox_coverage_ratio: `0.6012`
- residual_text_ratio: `1.0`
- table_row_unique_evidence_count: `20`

## Settings

- AZURE_DOCUMENT_INTELLIGENCE_API_VERSION: `2024-11-30`
- AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH: `documentintelligence`
- RAG_TABLE_VLM_ENABLED: `False`
- RAG_TABLE_VLM_CONFIDENCE_THRESHOLD: `0.8`
- RAG_PDF_TABLE_EXTRACTOR: `auto`

## Chunk Counts

- page_blocks/None/None: `4`
- table_annotation/None/None: `1`
- table_row/row/None: `23`
- table_summary/summary/None: `1`

## Focus Scope Metrics

- row_chunk_count: `12`
- multi_scope_count: `12`
- multi_scope_rate: `1.0`
- ambiguous_scope_count: `0`
- ambiguous_scope_rate: `0.0`
- inferred_scope_count: `0`
- inferred_scope_rate: `0.0`
- explicit_scope_count: `12`
- explicit_scope_rate: `1.0`
- scope_cardinality_histogram: `{'2': 12}`

## Focus Rows

- row 6: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=EGP
- row 7: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=USD
- row 8: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=EGP
- row 9: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=USD
- row 10: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=EGP
- row 11: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=USD
- row 12: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=EGP
- row 13: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=USD
- row 14: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=EGP
- row 15: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=USD
- row 16: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=EGP
- row 17: applies_to=['tariff', 'wealth'] mode=explicit_cells fee=USD

## Term Hits

- USD 5: `1` hits
- USD 2: `5` hits
- same day value date: `6` hits
- Cost per Bag: `1` hits
