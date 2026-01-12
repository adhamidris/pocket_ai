# RAG Data Analysis - Snippet vs Table Aggregate

## Data Comparison Summary

### Query: "what product holds code of 20666?"

---

## 1. Search Snippet Data

**Metrics:**
- char_count: 4,036
- token_estimate: 1,009
- read_state: 'full'
- is_table_chunk: False

**Content Structure:**
```
Purchasing_Data_Purchasing_Sales_Data_Csv: 1,606.50
- Private Hospi Del2: 1,606.50
- ك.عملاء اسكندرية2: 141.75
...
Identifiers: purchasing_data_purchasing_sales_data_csv, 606, private, hospi, del2, 141

Purchasing_Data_Purchasing_Sales_Data_Csv: ازموراب 20 مجم 2 شريط
- column_2: ازموراب 20 مجم 2 شريط
- Product Code: 20667
...
Identifiers: purchasing_data_purchasing_sales_data_csv, column_2, product, code, 20667, private

Purchasing_Data_Purchasing_Sales_Data_Csv: ازموراب 40 مجم 14 كبسولة
- column_2: ازموراب 40 مجم 14 كبسولة
- Product Code: 20666      ← TARGET DATA IS HERE
- Private Hospi Alex: 2
- Private Hospi Del2: 1
...
```

**Issues with Snippet:**
1. ❌ **Multiple products mixed together** (20666, 20667, 33740, etc.)
2. ❌ **Raw text format** - not structured key:value pairs
3. ❌ **Identifiers suffix** - confusing metadata mixed with data
4. ❌ **Column names inconsistent** - "column_2" vs actual product name
5. ❌ **No clear row boundaries** - hard to isolate single product

---

## 2. Table Aggregate Data

**Metrics:**
- char_count: 4,556 (MORE than snippet!)
- token_estimate: 1,139
- contribution_rows: 102
- match_column: "Product Code"
- match_count: 1

**Content Structure (structured JSON):**
```json
{
  "status": "ok",
  "rows": [
    {
      "cells": [
        {"column": "Product Name", "value": "ازموراب 40 مجم 14 كبسولة"},
        {"column": "Product Code", "value": "20666"},
        {"column": "Category", "value": "..."},
        {"column": "Strength", "value": "40 مجم"},
        {"column": "Pack Size", "value": "14 كبسولة"}
      ],
      "contributions": [...102 distribution entries...]
    }
  ],
  "match_count": 1,
  "total_matches": 1
}
```

**Advantages of Table Aggregate:**
1. ✅ **Single isolated row** - only the matching product
2. ✅ **Structured cells** - clear column:value pairs
3. ✅ **Filtered by match_column** - precise matching
4. ✅ **Clean JSON format** - easy for LLM to parse
5. ✅ **Consistent schema** - same format every time

---

## Root Cause of Hallucination

When we told the LLM to skip `read_knowledge` and use snippets:

1. **Snippet contains MULTIPLE products** (20666, 20667, 33740...)
2. **LLM had to parse raw text** to find the right one
3. **Mixed signals** - "Product Code: 20666" appears, but also confusing identifiers
4. **No clear boundaries** - hard to know which data belongs to which product
5. **LLM confusion** → incorrect extractions → HALLUCINATIONS

vs.

When LLM uses `read_knowledge`:
1. **Single matching row** returned
2. **Clean structured cells**
3. **No ambiguity** - one product, clear columns
4. **Easy extraction** → ACCURATE ANSWERS

---

## Key Insight

The snippet is NOT equivalent to table aggregate. They serve different purposes:

| Aspect | Snippet | Table Aggregate |
|--------|---------|-----------------|
| Purpose | Discovery (find relevant sources) | Extraction (get precise data) |
| Content | Raw chunk text with multiple records | Single filtered row |
| Structure | Unstructured text | Structured JSON cells |
| Filtering | None - chunk level | Row-level match on identifier |
| Accuracy | Low (mixed data) | High (isolated record) |

---

## Solution Options

### Option A: Keep Current Behavior (Recommended for now)
- Status quo: search → read → answer
- Benefits: reliable, accurate
- Cost: extra tool call latency (~6s)

### Option B: Improve Snippet Quality (Best long-term)
- During ingestion: create row-level snippets instead of chunk-level
- Each product gets its own searchable mini-document
- Snippet would already be filtered and structured
- Trade-off: larger index, more storage

### Option C: Snippet-Only Sufficiency Signals (Lightweight)
- Backend flags obvious incompleteness (truncation/partial tables/empty previews)
- `read_required` is advisory; LLM decides whether to read more
- Trade-off: fewer hard guarantees, but avoids brittle keyword gates

### Option D: Hybrid with Confidence Score
- Return snippet with `confidence: high|medium|low`
- LLM decides based on confidence whether to read more
- Trade-off: LLM still has to interpret raw snippet

---

## Recommendation

**Short-term (updated):** Keep `read_required` as a snippet-only insufficiency hint (advisory) and remove forced reads, so the LLM decides when more evidence is needed.

**Long-term (Option B):** Consider row-level indexing during ingestion:
- Each product row becomes its own searchable chunk
- Alias search directly returns the structured row
- No need for table aggregate in simple lookups
- Would achieve 1 tool call without sacrificing accuracy

---

## Questions to Investigate Further

1. How are chunks created during ingestion? Can we make smaller chunks?
2. Is table row data available at ingestion time to create per-row snippets?
3. What's the storage/index cost of row-level chunks?
4. Are there cases where current chunking is actually better (multi-product queries)?
