#!/usr/bin/env python
"""
Diagnostic script to check PDF table ingestion status.

Usage:
    python manage.py shell < scripts/diagnose_upload.py
    
Or with a search term:
    echo "SEARCH_TERM='Fees'" | cat - scripts/diagnose_upload.py | python manage.py shell
"""
import sys

# Search for the document
SEARCH_TERM = "Fees and Charges Credit Cards"  # Change this if needed

from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
)

print("\n" + "=" * 70)
print("PDF TABLE INGESTION DIAGNOSTIC")
print("=" * 70)

# Find matching uploads
uploads = KnowledgeUpload.objects.filter(
    source_name__icontains=SEARCH_TERM
) | KnowledgeUpload.objects.filter(
    display_name__icontains=SEARCH_TERM
)

if not uploads.exists():
    print(f"\n❌ No uploads found matching: '{SEARCH_TERM}'")
    print("\nTry searching with a different term, or check if the document was uploaded.")
    sys.exit(1)

print(f"\n📄 Found {uploads.count()} upload(s) matching '{SEARCH_TERM}':\n")

for upload in uploads[:5]:  # Limit to 5 results
    print("-" * 70)
    print(f"Upload ID: {upload.id}")
    print(f"Name: {upload.source_name or upload.display_name}")
    print(f"Status: {upload.status}")
    print(f"Created: {upload.created_at}")
    
    # Check ingestion metadata
    ing_meta = upload.ingestion_metadata or {}
    print(f"\n📋 Ingestion Metadata:")
    print(f"   Format: {ing_meta.get('format', 'unknown')}")
    print(f"   Pages: {ing_meta.get('page_count', 'unknown')}")
    
    # Check for pdfplumber status
    pdfplumber_meta = ing_meta.get("pdfplumber", {})
    if pdfplumber_meta:
        print(f"   pdfplumber: {pdfplumber_meta}")
    else:
        print(f"   pdfplumber: not run or no tables detected")
    
    # Check table extraction
    extraction_meta = ing_meta.get("extraction", {})
    table_source = extraction_meta.get("table_source", ing_meta.get("table_source"))
    if table_source:
        print(f"   Table source: {table_source}")
    
    # Count tables
    tables = KnowledgeUploadTable.objects.filter(upload=upload)
    table_count = tables.count()
    print(f"\n📊 Structured Tables: {table_count}")
    
    if table_count > 0:
        for table in tables[:3]:  # Show first 3 tables
            row_count = KnowledgeUploadTableRow.objects.filter(table=table).count()
            columns = table.column_schema or []
            print(f"   - Table '{table.title or 'Untitled'}': {row_count} rows, {len(columns)} columns")
            if columns:
                print(f"     Columns: {columns[:5]}{'...' if len(columns) > 5 else ''}")
    
    # Count and analyze chunks
    chunks = KnowledgeUploadChunk.objects.filter(upload=upload)
    chunk_count = chunks.count()
    print(f"\n📦 Chunks: {chunk_count}")
    
    # Analyze chunk types
    chunk_stats = {
        "text": 0,
        "table_parent": 0,
        "table_row": 0,
        "table_preview": 0,
        "other_table": 0,
        "missing_table_id": 0,
    }
    
    for chunk in chunks:
        meta = chunk.metadata or {}
        is_table = meta.get("is_table_chunk")
        role = meta.get("table_chunk_role")
        has_table_id = bool(meta.get("table_id"))
        
        if not is_table:
            chunk_stats["text"] += 1
        elif role == "parent":
            chunk_stats["table_parent"] += 1
            if not has_table_id:
                chunk_stats["missing_table_id"] += 1
        elif role == "row":
            chunk_stats["table_row"] += 1
        elif role == "preview" or meta.get("is_table_preview"):
            chunk_stats["table_preview"] += 1
        else:
            chunk_stats["other_table"] += 1
    
    print(f"   Text chunks: {chunk_stats['text']}")
    print(f"   Table parent chunks: {chunk_stats['table_parent']}")
    print(f"   Table row chunks: {chunk_stats['table_row']}")
    print(f"   Table preview chunks: {chunk_stats['table_preview']}")
    if chunk_stats["other_table"]:
        print(f"   Other table chunks: {chunk_stats['other_table']}")
    if chunk_stats["missing_table_id"]:
        print(f"   ⚠️  Missing table_id: {chunk_stats['missing_table_id']}")
    
    # Show sample of table chunks content
    table_chunks = chunks.filter(metadata__is_table_chunk=True)[:2]
    if table_chunks:
        print(f"\n📝 Sample table chunk content:")
        for tc in table_chunks:
            print(f"\n   --- Chunk {tc.chunk_index} (role={tc.metadata.get('table_chunk_role')}) ---")
            content = (tc.content or "")[:500]
            for line in content.split("\n")[:10]:
                print(f"   {line[:80]}")
            if len(tc.content or "") > 500:
                print(f"   ... (truncated)")
    
    # Diagnosis
    print(f"\n🔍 DIAGNOSIS:")
    if table_count == 0:
        print("   ❌ No structured tables extracted from PDF")
        print("   → pdfplumber may have failed to detect table structure")
        print("   → Consider: Azure Document Intelligence for better OCR")
    elif chunk_stats["table_row"] == 0:
        print("   ⚠️  Tables exist but NO row chunks were created")
        print("   → The 'table_schema_chunking' feature may not be enabled")
        print("   → Or document was ingested before row chunking was added")
        print("   → Try re-ingesting the document")
    elif chunk_stats["missing_table_id"] > 0:
        print("   ⚠️  Some parent chunks missing table_id metadata")
        print("   → Row expansion cannot work without table_id linkage")
        print("   → This is a bug in ingestion - notify developer")
    else:
        print("   ✅ Table structure looks correct")
        print("   → Issue may be in retrieval logic, not ingestion")
        print(f"   → {chunk_stats['table_row']} row chunks should be expandable")

print("\n" + "=" * 70)
print("END DIAGNOSTIC")
print("=" * 70 + "\n")
