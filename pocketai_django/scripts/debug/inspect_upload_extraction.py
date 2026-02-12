from apps.accounts.models import BusinessProfile
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadTable,
)

# Get the upload
cib = BusinessProfile.objects.filter(name__icontains='CIB').first()
if not cib:
    print("❌ CIB business not found")
    exit()

upload = KnowledgeUpload.objects.filter(
    business_profile=cib,
    file_detail__filename__icontains='cc'
).select_related('file_detail', 'text_detail').first()

if not upload:
    print("❌ Upload not found")
    print("Available uploads:")
    for u in KnowledgeUpload.objects.filter(business_profile=cib)[:10]:
        print(f"  - {u.display_name} ({u.source_type})")
    exit()

print(f"✅ Found upload: {upload.display_name}")
print(f"   Status: {upload.status}")

# Get extracted text
text_detail = upload.text_detail
if not text_detail:
    print("❌ No text extracted yet")
    exit()

extracted_text = text_detail.content
print(f"\n📄 Extracted text length: {len(extracted_text)} characters")

# Find the SPECIFIC table Claude extracted (World/Platinum/Heya Cards/Titanium)
print("\n🔍 Looking for World/Platinum/Titanium table...")

# Search for the table by looking for unique identifiers
search_terms = [
    "WorldPlatinumHeya CardsTitanium",  # As it appears in Claude's output
    "World\tPlatinum\tHeya Cards\tTitanium",  # Tab-separated version
    "Platinum\tHeya Cards\tTitanium"  # Partial match
]

table_found = False
table_start_idx = -1

for term in search_terms:
    if term in extracted_text:
        table_start_idx = extracted_text.find(term)
        table_found = True
        print(f"✅ Found table using pattern: '{term[:30]}...'")
        break

# Try alternative: search for section with "EGP 3,500" or "EGP 8,000" (Titanium/World Elite prices)
if not table_found:
    print("⚠️  Direct match not found, searching for Titanium/World Elite pricing...")
    if "EGP 3,500" in extracted_text or "EGP 8,000" in extracted_text:
        # Find the section
        idx_3500 = extracted_text.find("EGP 3,500")
        idx_8000 = extracted_text.find("EGP 8,000")
        
        if idx_3500 != -1:
            table_start_idx = max(0, idx_3500 - 500)  # Go back to capture headers
            table_found = True
            print("✅ Found via EGP 3,500 (Titanium)")
        elif idx_8000 != -1:
            table_start_idx = max(0, idx_8000 - 500)
            table_found = True
            print("✅ Found via EGP 8,000 (World Elite)")

if table_found:
    # Extract a larger snippet (2000 chars to capture full table)
    snippet = extracted_text[table_start_idx:table_start_idx + 2000]
    
    print("\n" + "="*80)
    print("📊 EXTRACTED TABLE (Your Engine):")
    print("="*80)
    print(snippet)
    print("="*80)
    
    print("\n🔍 Comparison with Claude's output:")
    print("\nClaude extracted:")
    print("- Card types: World, Platinum, Heya Cards, Titanium, World Elite")
    print("- Issuance fees: EGP 450, EGP 450, EGP 700, EGP 3,500, EGP 8,000")
    print("- Interest rate: 3.99% (all cards)")
    print("- Grace period: 55 days (World/Platinum/Heya), 45 days (Titanium), 55 days (World Elite)")
    
    print("\nYour engine extracted:")
    # Check for these specific values
    checks = [
        ("EGP 450", "Issuance fee for World/Platinum"),
        ("EGP 700", "Issuance fee for Heya Cards"),
        ("EGP 3,500", "Issuance fee for Titanium"),
        ("EGP 8,000", "Issuance fee for World Elite"),
        ("45 days", "Titanium grace period"),
        ("World Elite", "Card type"),
        ("Heya Cards", "Card type")
    ]
    
    for value, description in checks:
        if value in snippet:
            print(f"   ✅ {description}: '{value}' found")
        else:
            print(f"   ❌ {description}: '{value}' NOT found")
            
else:
    print("\n❌ Could not locate the World/Platinum/Titanium table")
    print("\n💡 Showing what IS in the extracted text:")
    print(f"   First 1000 chars:\n{extracted_text[:1000]}")
    print(f"\n   Content around 'Card Type':")
    if "Card Type" in extracted_text:
        idx = extracted_text.find("Card Type")
        print(extracted_text[idx:idx+1500])

# Also check if tabs are preserved in the relevant section
if table_found and "\t" in snippet:
    print("\n✅ Tab structure preserved in this section")
    lines_with_tabs = [l for l in snippet.split("\n") if "\t" in l][:10]
    print(f"   Sample tab-delimited lines ({len(lines_with_tabs)}):")
    for line in lines_with_tabs[:5]:
        print(f"   {repr(line)}")




