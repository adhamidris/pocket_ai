#!/usr/bin/env python
"""
Re-ingest a specific upload by ID or name.

Usage:
    python manage.py shell < scripts/reingest_upload.py
"""
import sys

SEARCH_TERM = "Fees and Charges Credit Cards"  # Change this if needed
UPLOAD_ID = "23e9c2c1-6be7-4cf5-a64f-b13ddcf3ccd7"  # The newer upload

from apps.knowledge.models import KnowledgeUpload
from apps.knowledge.knowledge_ingestion import queue_ingestion_job, KnowledgeIngestionService

print("\n" + "=" * 70)
print("RE-INGESTION SCRIPT")
print("=" * 70)

# Find the upload
try:
    upload = KnowledgeUpload.objects.get(id=UPLOAD_ID)
except KnowledgeUpload.DoesNotExist:
    print(f"\n❌ Upload not found: {UPLOAD_ID}")
    sys.exit(1)

print(f"\n📄 Found upload:")
print(f"   ID: {upload.id}")
print(f"   Name: {upload.source_name or upload.display_name}")
print(f"   Status: {upload.status}")

# Queue the ingestion job
print(f"\n🔄 Queueing ingestion job...")
job = queue_ingestion_job(upload, trigger="manual_reingest", force=True)
if job:
    print(f"   ✅ Job queued: {job.id}")
    print(f"   Job type: {job.job_type}")
    print(f"   Status: {job.status}")
else:
    print(f"   ⚠️  No job was queued (may already be in queue)")

# Now process it immediately
print(f"\n🔧 Processing ingestion job...")
service = KnowledgeIngestionService()

result = service.process_next_job()
if result:
    print(f"\n📊 INGESTION RESULT:")
    print(f"   Job ID: {result.job_id}")
    print(f"   Status: {result.status}")
    print(f"   Characters: {result.characters}")
    if result.error:
        print(f"   ❌ Error: {result.error}")
    else:
        print(f"   ✅ Success!")
else:
    print(f"\n⚠️  No job was processed")

# Now check the results
print(f"\n" + "-" * 70)
print("POST-INGESTION CHECK")
print("-" * 70)

from apps.knowledge.models import (
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
)

# Refresh the upload from DB
upload.refresh_from_db()

# Check ingestion metadata
ing_meta = upload.ingestion_metadata or {}
print(f"\n📋 Ingestion Metadata (after):")
print(f"   Format: {ing_meta.get('format', 'unknown')}")
print(f"   Pages: {ing_meta.get('page_count', 'unknown')}")

pdfplumber_meta = ing_meta.get("pdfplumber", {})
if pdfplumber_meta:
    print(f"   pdfplumber: {pdfplumber_meta}")
else:
    print(f"   pdfplumber: not run or no tables detected")

extraction_meta = ing_meta.get("extraction", {})
if extraction_meta:
    print(f"   extraction: table_count={extraction_meta.get('table_count')}, table_source={extraction_meta.get('table_source')}")

# Count tables and chunks
table_count = KnowledgeUploadTable.objects.filter(upload=upload).count()
chunk_count = KnowledgeUploadChunk.objects.filter(upload=upload).count()

print(f"\n📊 Results:")
print(f"   Structured Tables: {table_count}")
print(f"   Chunks: {chunk_count}")

# Analyze chunk types
if chunk_count > 0:
    chunks = KnowledgeUploadChunk.objects.filter(upload=upload)
    stats = {"text": 0, "table_parent": 0, "table_row": 0, "table_preview": 0}
    for chunk in chunks:
        meta = chunk.metadata or {}
        role = meta.get("table_chunk_role")
        if not meta.get("is_table_chunk"):
            stats["text"] += 1
        elif role == "parent":
            stats["table_parent"] += 1
        elif role == "row":
            stats["table_row"] += 1
        elif role == "preview" or meta.get("is_table_preview"):
            stats["table_preview"] += 1
    
    print(f"   Text chunks: {stats['text']}")
    print(f"   Table parent chunks: {stats['table_parent']}")
    print(f"   Table row chunks: {stats['table_row']}")
    print(f"   Table preview chunks: {stats['table_preview']}")
    
    # Show sample content
    if stats["text"] > 0:
        sample = chunks.filter(metadata__is_table_chunk=False).first()
        if sample:
            print(f"\n📝 Sample text chunk content:")
            for line in (sample.content or "")[:400].split("\n")[:8]:
                print(f"   {line[:80]}")

print("\n" + "=" * 70)
print("END RE-INGESTION")
print("=" * 70 + "\n")
