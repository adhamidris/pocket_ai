
import sys
import uuid
from apps.accounts.models import KnowledgeUpload
from apps.knowledge.knowledge_ingestion import queue_ingestion_job, KnowledgeIngestionService
from apps.accounts.models import KnowledgeUploadChunk, KnowledgeUploadTable

UPLOAD_ID = "5225e253-f5c2-468d-942e-7700264db810"

print("\n" + "=" * 70)
print("RE-INGESTION SCRIPT (FIX)")
print("=" * 70)

try:
    upload = KnowledgeUpload.objects.get(id=UPLOAD_ID)
except KnowledgeUpload.DoesNotExist:
    print(f"\n❌ Upload not found: {UPLOAD_ID}")
    sys.exit(1)

print(f"\n📄 Found upload:")
print(f"   ID: {upload.id}")
print(f"   Name: {upload.source_name or upload.display_name}")
print(f"   Status: {upload.status}")

# Force re-ingest
print(f"\n🔄 Queueing ingestion job...")
job = queue_ingestion_job(upload, trigger="manual_reingest", force=True)
if job:
    print(f"   ✅ Job queued: {job.id}")
else:
    print(f"   ⚠️  No job was queued")

# Process immediately
print(f"\n🔧 Processing ingestion job...")
service = KnowledgeIngestionService()
result = service.process_next_job()

if result:
    print(f"\n📊 INGESTION RESULT:")
    print(f"   Status: {result.status}")
    if result.error:
        print(f"   ❌ Error: {result.error}")
    else:
        print(f"   ✅ Success! Chars: {result.characters}")
else:
    print(f"\n⚠️  No job was processed (check if another worker picked it up)")

# Check results
chunk_count = KnowledgeUploadChunk.objects.filter(upload=upload).count()
table_count = KnowledgeUploadTable.objects.filter(upload=upload).count()
print(f"\n📊 Final Counts:")
print(f"   Chunks: {chunk_count}")
print(f"   Tables: {table_count}")
