# QA Corpus Index

Generated: 2026-03-10 (Africa/Cairo)

This directory centralizes the manual QA sets for the `/documents/` corpus so you can use them directly for random portal testing and response-quality review.

## Files

- `cib_rag_qa_set.md`
  - Existing CIB QA set copied here for convenience.
  - This file was not regenerated in this pass; it remains a legacy QA file.
- `non_cib_documents_rag_qa_set.md`
  - Large manual QA set for the non-CIB business PDFs and DOCX files under `/documents/business/`.
  - Re-audited in this pass to keep it grounded in the original source documents.
- `spreadsheet_validation_rag_qa_set.md`
  - QA set for the spreadsheet and CSV files under `/documents/spreadsheet_validation/` plus the California invoice workbooks.
  - Grounded in the original workbook/CSV files.

## Notes

- The new QA sets generated in this pass are grounded in the local source documents only.
- They were authored from the original PDFs, DOCX files, XLSX files, and CSV files on disk, not from portal answers or ingested chunks/entities.
- PDF sources use page references like `p.1`.
- DOCX sources use paragraph/table anchors where page numbers are not stable.
- Spreadsheet sources use sheet names plus row/field anchors.
- The legacy file `documents/business/plans/sba_sample_business_plan_we_can_do_it.doc` is not included in this pass because it needs a separate extraction/review path.
