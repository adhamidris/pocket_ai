# Architecting Financial Intelligence: A Comprehensive Technical Analysis of Document Understanding and RAG Pipelines for Complex Tabular Data

## 1. Introduction: The Structural Semantic Gap in Financial AI

In the rapidly evolving landscape of enterprise Artificial Intelligence, the automated extraction and semantic understanding of financial documents represent a distinct and formidable frontier. While Natural Language Processing (NLP) has achieved near-human parity in comprehending unstructured prose, financial data resides primarily in semi-structured formats—specifically, complex, multi-column tables found in prospectuses, annual reports (10-Ks), custodial fee schedules, and invoices. These documents are not merely collections of text; they are topological maps where spatial relationships define semantic meaning. For a human analyst, a number in a cell derives its meaning from its row header, column header, and often a hierarchical super-header spanning multiple columns. For a standard machine learning model, this layout is frequently reduced to a serialized stream of tokens, destroying the two-dimensional relationships that constitute the data's essence.

The challenge is exacerbated when designing Retrieval-Augmented Generation (RAG) systems. Standard RAG architectures rely on "chunking"—splitting documents into smaller segments for vectorization. When applied to financial tables, traditional chunking methods act as a shredder, separating numerical values from the labels that give them meaning. A "0.50%" management fee becomes a floating vector in high-dimensional space, indistinguishable from a "0.50%" interest rate or a "0.50%" tax bracket once severed from its structural context. This phenomenon, often referred to as "text soup," leads to high retrieval latency, hallucination, and fundamentally unreliable financial insights.

This report provides an exhaustive, expert-level analysis of the technologies available in 2024 and 2025 to bridge this structural-semantic gap. We dissect the performance of Deep Learning Table Extraction models (such as TATR, UniTable, and PaddleOCR), benchmark the capabilities of commercial Document AI platforms (Azure, AWS, Google), and evaluate the emerging dominance of Vision Language Models (VLMs) like GPT-4o and Claude 3.5 Sonnet. Furthermore, we delineate schema-aware chunking strategies that are critical for production-grade financial RAG systems. The synthesis of this research points toward hybrid architectures that leverage deterministic layout analysis for structural grounding and probabilistic Large Language Models (LLMs) for semantic reasoning.

## 2. Theoretical Framework of Table Extraction and Evaluation Metrics

To rigorously compare the efficacy of modern extraction systems, one must first understand the theoretical underpinnings of Table Structure Recognition (TSR) and the metrics used to evaluate success. The transition from heuristic-based methods to deep learning has necessitated new ways of quantifying "accuracy."

### 2.1 The Evolution of Table Recognition Architectures

Historically, table extraction relied on rule-based systems (like the open-source libraries Camelot or Tabula) that utilized "separators" and XY-cut algorithms. These methods, while computationally inexpensive, are brittle; they fail catastrophically when faced with borderless tables, skewed scans, or the complex nested headers typical of financial fee schedules.

The modern era of TSR is defined by three primary deep learning paradigms:

**Object Detection-Based:** Adapted from Computer Vision (CV), these models (like Faster R-CNN or Mask R-CNN) treat table rows and columns as "objects" to be detected. They predict bounding boxes but often struggle with the precise alignment required for dense financial grids.

**Graph-Based:** These approaches model table cells as nodes in a graph, with edges representing spatial relationships (top, bottom, left, right). While theoretically sound, they are computationally intensive.

**Transformer-Based (Seq2Seq & Hybrid):** The current State-of-the-Art (SOTA). These models, such as Microsoft's Table Transformer (TATR) and UniTable, leverage the self-attention mechanism of Transformers to learn the global context of the table. They treat the table structure prediction as a sequence generation task (outputting HTML/LaTeX tags) or a set prediction task (outputting cell coordinates).

### 2.2 Critical Evaluation Metrics: TEDS and GriTS

In the domain of financial table extraction, simple "character accuracy" is insufficient. A model might recognize every character correctly (100% OCR accuracy) but fail to identify that a specific cell belongs to the "2023" column rather than the "2022" column. To address this, the academic and industrial communities have standardized on structural metrics.

#### 2.2.1 Tree-Edit-Distance-Based Similarity (TEDS)

Proposed by IBM researchers in the context of the PubTabNet dataset, TEDS evaluates the similarity between the tree structure of the predicted HTML and the ground truth HTML. It accounts for both the structural integrity (tags like `<tr>`, `<td>`, colspan) and the text content.

- **Simple TEDS:** Measures only the structural skeleton.
- **Complex TEDS:** Measures both structure and cell content.

A TEDS score of 90% implies that 90% of the tree structure requires no editing to match the ground truth. This metric is the gold standard for comparing models like TableFormer and TATR.

#### 2.2.2 Grid Table Similarity (GriTS)

While TEDS is powerful, it can be overly sensitive to minor HTML variations that do not affect semantic meaning. GriTS evaluates tables as a 2D matrix (grid) of content, assessing the Topology (row/column adjacency) and Content separately. This is particularly relevant for financial RAG, where the adjacency of a fee label to its value is more important than the specific HTML tag used to render it.

#### 2.2.3 Field-Level Precision/Recall (F1)

For commercial benchmarks, theoretical tree similarity is often replaced by pragmatic Field-Level F1 scores. This measures the extraction of specific key-value pairs (e.g., "Invoice Total," "Vendor Name"). In financial contexts, this is often the "downstream utility" metric—regardless of how the model understood the table, did it extract the correct dollar amount?

## 3. Deep Learning Table Extraction Models: The Open Source Frontier (2023–2025)

The open-source ecosystem offers powerful, cost-effective alternatives to commercial APIs, provided that engineering teams are capable of hosting and fine-tuning these architectures. The period from 2023 to 2025 has seen a consolidation around Transformer-based architectures.

### 3.1 Microsoft Table Transformer (TATR)

TATR, introduced by Microsoft Research, adapts the DETR (DEtection TRansformer) architecture for table extraction. It represented a watershed moment by moving away from heuristic anchors toward learned object queries.

**Architecture:** TATR splits the problem into two distinct models:
- **Table Detection (TD):** A ResNet-backed Transformer detects the table boundary.
- **Table Structure Recognition (TSR):** A second Transformer predicts the internal structure (rows, columns, spanning cells).

**Mechanism:** Unlike CNN-based detectors that scan pixels, TATR utilizes a fixed set of "object queries" that attend to the image features globally. This allows the model to "reason" about the table structure holistically—understanding that a row typically extends across the full width of a table.

**Performance:** TATR established the baseline performance on the PubTables-1M dataset. However, its "two-stage" nature introduces a bottleneck: if the detection step clips a border, the structure recognition fails. Benchmarks indicate exact match accuracy on the ICDAR-2013 benchmark improves from 42% to 65% when trained on specialized financial datasets like FinTabNet, highlighting the model's dependency on domain-specific training data.

**Limitations:** The reliance on bounding boxes requires complex post-processing to reconstruct the final table into a usable format (like HTML or CSV). In dense financial tables, the bounding box regression can sometimes drift, leading to misaligned columns.

### 3.2 PaddleOCR and PP-StructureV3

Baidu's PaddleOCR framework has emerged as a formidable competitor, particularly for "production-ready" open-source deployments. The PP-StructureV3 module is specifically engineered for document parsing.

**Architecture (SLANet):** PP-StructureV3 utilizes SLANet (Structure Layout Analysis Network). This is a lightweight, Structure-Aware network that treats table recognition as a sequence generation problem. It integrates a Coordinate Regression head to pinpoint cell locations while simultaneously generating the HTML tokens.

**Performance:** On the PubTabNet evaluation set, PP-StructureV3 reports a TEDS score of 95.89%, a significant improvement over earlier baselines like EDD (88.30%). It is also highly optimized for inference speed, achieving processing times of ~766ms per image on CPU, making it viable for high-volume, cost-sensitive pipelines.

**Vision-Language Integration:** The latest iterations (PaddleOCR-VL) incorporate Vision-Language Model (VLM) components, using a dynamic resolution visual encoder (NaViT-style) to handle the high resolution required for scanning dense financial fee tables.

**Multilingual Support:** While initially Chinese-centric, recent benchmarks on the "OmniDocBench" show PP-StructureV3 achieving competitive performance on English documents, with TEDS scores comparable to or exceeding specialized commercial tools in certain configurations.

### 3.3 UniTable: The Unified Foundation Model

Introduced in 2024, UniTable represents the shift toward "Foundation Models" for tables. It posits that table extraction tasks (detection, structure, content) should not be siloed but trained jointly.

**Architecture:** UniTable unifies the training objectives into a single language modeling task. It uses a purely pixel-level input and outputs a serialized sequence (HTML) directly.

**Self-Supervised Pretraining (SSP):** A key differentiator is its training regime. UniTable is pretrained on massive datasets (2 million images) using Masked Tabular Image Modeling (MTIM). This allows the model to learn the "grammar" of tables from unannotated data before being fine-tuned on labeled datasets like FinTabNet.

**Benchmark Dominance:** UniTable currently holds SOTA metrics on several benchmarks. On SynthTabNet, it achieved an accuracy of 99.18%, drastically outperforming prior methods. On FinTabNet, which specifically consists of financial reports, UniTable demonstrates superior handling of the sparse, borderless tables common in 10-K filings.

**Implication:** For greenfield projects in 2025, UniTable offers the most "modern" architecture, reducing the engineering overhead of linking multiple models (detection + recognition + OCR) into a single, cohesive inference step.

### 3.4 TableFormer

TableFormer addresses a specific weakness in previous models: the disconnect between structure prediction and cell content bounding boxes.

**Mechanism:** It introduces a "Cell BBox Decoder" that predicts explicit bounding boxes alongside the HTML structure tags. This is crucial for RAG pipelines because it allows the system to highlight exactly where in the source PDF a specific number came from (traceability).

**Performance:** It achieves a TEDS score of 96.75% on PubTabNet. Its rigorous handling of safety and bounding boxes makes it a strong candidate for compliance-heavy workflows where every extracted number must be visually verifiable against the source PDF.

### 3.5 Comparative Summary of Deep Learning Models

The following table synthesizes the performance metrics and architectural characteristics of the leading open-source models as of 2025.

| Model Framework | Architecture Paradigm | TEDS Score (PubTabNet) | Primary Strength | Critical Weakness for Finance |
|-----------------|----------------------|------------------------|------------------|-------------------------------|
| TATR (Microsoft) | Transformer (DETR) | ~91-93% | Proven baseline; widely understood architecture. | Two-stage error propagation; complex post-processing. |
| PP-StructureV3 | Seq2Seq (SLANet) | 95.89% | High inference speed; excellent merged cell handling. | Training pipeline complexity (PaddlePaddle ecosystem). |
| UniTable | Unified Foundation | ~97-99% | SOTA generalization; direct HTML output. | High computational cost for training; newer ecosystem. |
| TableFormer | Transformer + BBox | 96.75% | Strong traceability (BBox + Content linkage). | Requires strict HTML ground truth for fine-tuning. |

## 4. Commercial Document AI: The Cloud Wars

While open-source models offer control, commercial APIs provide managed infrastructure and SLAs. In 2024 and 2025, the differentiation between Azure, AWS, and Google has sharpened, with each provider adopting distinct strategies regarding generative AI integration.

### 4.1 Azure Document Intelligence (The Layout Leader)

Microsoft's Azure Document Intelligence (formerly Form Recognizer) has consistently maintained a lead in "Layout Analysis" for complex documents.

**Hybrid Architecture:** Azure employs a high-precision optical model combined with a layout analysis engine. It does not merely perform OCR; it reconstructs the document object model (DOM).

**Markdown Export:** A critical feature introduced for RAG pipelines is the ability to export directly to Markdown. This preserves the semantic hierarchy of the document (headers as ##, tables as Markdown tables), which is the optimal format for LLM ingestion.

**Financial Benchmarks:** In independent testing involving complex invoices, Azure achieved a 93% Field Accuracy, significantly outperforming competitors. It is particularly noted for its ability to handle multi-column, nested tables without "jumbling" the reading order—a common failure mode in lower-fidelity OCR engines.

**Pricing:** The pricing is structured per page, typically around $10 per 1,000 pages for the prebuilt layout models. While higher than raw OCR, the reduction in post-processing engineering often justifies the cost for complex financial instruments.

### 4.2 AWS Textract (The High-Volume Workhorse)

AWS Textract focuses on scalability and integration within the AWS ecosystem, though it often trails in precision for non-standard layouts.

**Block-Based Output:** Textract returns a JSON object containing "Blocks" (WORD, LINE, TABLE, CELL). While comprehensive, reassembling these blocks into a coherent semantic structure often requires significant custom logic (e.g., writing complex Python parsers to map CELL blocks to specific merged headers).

**Performance Gap:** Benchmarks indicate a Field Accuracy of ~78% on complex documents, compared to Azure's 93%. Specifically, Textract has been observed to struggle with the "reading order" of dense, multi-column text, sometimes interleaving text from adjacent columns—a fatal error for fee tables.

**Cost Efficiency:** Pricing is competitive (~$15 per 1,000 pages for tables), and for standardized forms (like tax documents), it performs reliably. However, for the "long tail" of heterogeneous financial filings, the accuracy trade-off is significant.

### 4.3 Google Document AI (The Generative Pivot)

Google has aggressively integrated its Gemini models into Document AI, shifting from a pure OCR approach to a "Generative Extraction" model.

**Generative Power:** By utilizing Gemini 1.5 Pro and 2.5 on the backend, Google DocAI can now perform "Zero-Shot" extraction. Instead of defining a rigid template, a user can define a schema, and the model uses its reasoning capabilities to extract data.

**Content Fidelity:** Emerging benchmarks using the SCORE framework suggest that Gemini 2.5 achieves extremely high "Content Fidelity," potentially surpassing traditional OCR systems in semantic accuracy. It excels at interpreting the intent of a table, even if the structural boundaries are ambiguous.

**Historical Weakness:** Previously, Google DocAI struggled with table parsing precision (scoring ~40% on line-item detection in older benchmarks). The pivot to Gemini is a direct response to this, leveraging the VLM's reasoning to overcome the limitations of the older geometric parsers.

**Pricing:** Google utilizes a complex pricing model that can involve "per page" costs ($30/1k for custom extractors) or token-based pricing for the generative models. This variability can make cost prediction harder than Azure's flat rates.

### 4.4 Emerging Challengers: Tensorlake and Reducto

A new class of specialized "RAG-First" parsers has emerged, challenging the generalist cloud providers.

**Tensorlake:** This provider focuses entirely on parsing PDFs into Markdown for LLMs. Their benchmarks claim a TEDS score of 86.79% on the OmniDocBench, surpassing both Azure (78.14%) and AWS (80.75%). Their value proposition is "Structural Preservation"—ensuring that the reading order fed to the LLM perfectly mirrors the visual document.

**Reducto:** Reducto markets itself on "Agentic OCR," using a multi-pass approach where a vision model identifies difficult regions (like complex tables) and a VLM specifically transcribes them. They report up to 20% better performance on internal benchmarks for "long-tail" edge cases compared to major cloud providers.

### 4.5 Comparative Benchmarking Summary

| Provider | Core Strength | Field Accuracy (Complex) | Output Format | Pricing Model |
|----------|--------------|-------------------------|---------------|---------------|
| Azure Document Intelligence | Layout & Structure | 93% | Markdown, JSON | ~$10/1k pages |
| AWS Textract | Scalability & Standard Forms | 78% | Block JSON | ~$15/1k pages (Tables) |
| Google Document AI | Semantic Reasoning (Gemini) | 82% (improving) | Entities, JSON | Hybrid (Page + Token) |
| Tensorlake | Parsing for RAG (Structure) | High (TEDS 86%) | Markdown | ~$10/1k pages |

## 5. Emerging Vision Language Models (VLMs): The Reasoning Layer

The integration of Vision Language Models (VLMs) fundamentally alters the table extraction pipeline. Traditionally, pipelines followed a serial process: OCR → Text → NLP Extraction. VLMs enable a parallel process: Image → Visual Reasoning → Structured Output. This bypasses the serialization errors of OCR entirely.

### 5.1 GPT-4o: The Omni-Modal Standard

OpenAI's GPT-4o (Omni) is currently the benchmark for "Zero-Shot" table extraction.

**Performance:** In "Field Accuracy" benchmarks, GPT-4o (using direct image input) achieved 90.5% accuracy. When combined with a text-layer (hybrid approach), accuracy reached 98%, effectively solving the extraction problem for high-value, low-volume documents.

**Mechanism:** GPT-4o processes the visual features of the table directly. It "sees" that a column is aligned under a specific header, even if the text stream is ambiguous. It can output complex JSON structures matching a user-defined schema without training.

**Latency & Cost:** The trade-off is cost and speed. Processing a high-resolution image of a financial table is significantly more expensive (in terms of tokens) than running a specialized OCR model. GPT-4o is priced at $5.00/1M input tokens, making it a premium solution best reserved for the most difficult pages.

### 5.2 Claude 3.5 Sonnet: The Visual Reasoner

Anthropic's Claude 3.5 Sonnet has distinguished itself with superior "Visual Reasoning" capabilities.

**Chart & Graph Analysis:** Benchmarks consistently show Claude 3.5 Sonnet outperforming GPT-4o in interpreting abstract visual data like charts and graphs. For financial documents that mix tables with trend lines, Claude offers a more holistic understanding.

**Context Window:** With a 200k token context window, Claude can ingest significantly larger portions of a document than the standard GPT-4o context (128k). This allows it to maintain "Global Context"—understanding that a footnote on page 45 modifies a fee table on page 12.

**Artifacts:** Claude's "Artifacts" UI feature (and underlying capability) allows it to generate clean, rendered code or Markdown snippets, making it an excellent engine for converting PDF tables into clean React or HTML components for downstream applications.

**Cost:** Priced at $3.00/1M input tokens, it offers a compelling price-to-performance ratio for heavy reasoning tasks compared to GPT-4o.

### 5.3 Gemini 1.5 Pro: The Long-Context Titan

Google's Gemini 1.5 Pro disrupts the standard RAG paradigm with its massive context window.

**1 Million+ Tokens:** Gemini 1.5 Pro's context window (up to 2 million tokens in some previews) allows for "Whole Document Processing." Theoretically, an entire 100-page prospectus can be fed into the model without chunking.

**Retrieval Capability:** "Needle In A Haystack" benchmarks show Gemini 1.5 Pro has near-perfect recall for finding specific facts within this massive context. This suggests a future where "Chunking" strategies might become obsolete for all but the largest archives.

**Reasoning vs. Accuracy:** While its recall is high, head-to-head comparisons on reasoning benchmarks (like GPQA or MATH) often show it slightly trailing GPT-4o and Claude 3.5 Sonnet in precise instruction following, though the gap is narrowing.

### 5.4 VLM Comparison for Financial Tables

| Feature | GPT-4o | Claude 3.5 Sonnet | Gemini 1.5 Pro |
|---------|--------|-------------------|----------------|
| Input Cost (1M) | $2.50 - $5.00 | $3.00 | $1.25 - $2.50 |
| Context Window | 128k | 200k | 1 Million - 2M |
| Visual Reasoning | High (Structured) | Very High (Nuance) | High (Long Context) |
| Best For | Precision Extraction (JSON) | Charts / Complex Reasoning | Whole Doc Analysis |

## 6. Schema-Aware Chunking Strategies: Solving "Text Soup"

In a RAG pipeline, the "Chunking" strategy dictates the upper bound of retrieval accuracy. If a table is sliced incorrectly, no amount of embedding optimization can recover the lost semantic meaning.

### 6.1 The Failure of Fixed-Size Chunking

Traditional RAG pipelines use "Recursive Character Splitting," cutting text every 500 or 1000 characters. For a financial table, this is disastrous.

**Scenario:** A table row spans characters 900-1100.

**Result:** The splitter cuts at 1000. The first chunk contains the row label ("Management Fee"). The second chunk contains the value ("0.50%").

**Retrieval Failure:** A user searching for "Management Fee" retrieves the first chunk, which contains no value. The LLM hallucinates an answer.

### 6.2 Strategy 1: Markdown-Header-Aware Chunking

The robust solution is to parse the PDF into Markdown first (using Azure or Tensorlake).

**Mechanism:** The chunker utilizes the Markdown structure. It recognizes that a Table is a single atomic unit. It respects headers (#, ##) as logical boundaries.

**Implementation:** Using libraries like LangChain's MarkdownHeaderTextSplitter, the pipeline ensures that a table is never split mid-row. Furthermore, it appends the hierarchy of headers (e.g., "Fund A > Fees > Custodial") to the metadata of the table chunk.

**Impact:** Benchmarks indicate this strategy boosts retrieval accuracy by 5-10% in financial documents by preserving the local context of the data.

### 6.3 Strategy 2: "Chunk Twice, Retrieve Once" (Parent-Child)

This is the "Gold Standard" for complex financial RAG.

**The Problem:** Large tables often exceed the ideal embedding size (e.g., 512 tokens) for dense vector retrieval. Embedding a massive table dilutes the vector, making it hard to match specific queries like "What is the fee for Class A shares?".

**The Solution:**
- **Parent Chunk:** Store the entire table (as Markdown or HTML) in a document store. Do not embed this directly.
- **Child Chunks:** Split the table into smaller, semantically rich units (e.g., individual rows or summaries like "Class A shares fee is 0.50%").
- **Vectorization:** Embed the Child Chunks.
- **Retrieval:** When a user query matches a Child Chunk, the system retrieves the ID of the Parent Chunk and feeds the Parent (the full table) to the LLM.

**Benefit:** This provides the precision of fine-grained search with the context of the full document, ensuring the LLM has all necessary footnotes and column headers to generate a correct answer.

### 6.4 Strategy 3: Multi-Modal "Table-as-Image"

For tables that are visually complex (merged headers, color-coding) where OCR fails, we treat the table as an image.

**Mechanism:**
1. Use a Layout model (TATR/Azure) to detect the table bounding box.
2. Crop the table from the PDF page.
3. Pass the image of the table to a VLM (GPT-4o/Claude) to generate a textual summary or Markdown representation.
4. Embed the text summary for retrieval.
5. Pass the original image to the VLM at generation time.

**Traceability:** This ensures that the model is "looking" at the ground truth pixels, eliminating OCR hallucinations. It is the most expensive but most accurate method for "high-stakes" data.

## 7. Hybrid Pipeline Architectures and Recommendations

Based on the convergence of these technologies, we propose three architectural patterns for production-grade financial RAG pipelines.

### 7.1 Architecture A: The "High-Fidelity" Hybrid (Recommended for Production)

This architecture optimizes for accuracy and traceability, accepting higher API costs as a tradeoff for compliance.

- **Ingestion Layer:** Use Azure Document Intelligence (Layout Model) to convert PDFs to Markdown. This provides the best baseline structure.
- **Refinement Layer:** Implement a "Confidence Check." If the Layout model reports low confidence for a table region, route that specific page crop to GPT-4o via API with a prompt to "Repair this table Markdown."
- **Chunking Layer:** Implement Parent-Child Indexing.
  - **Parent:** Full Markdown Tables.
  - **Child:** Individual rows enriched with the Table Header and Section Header.
- **Retrieval Layer:** Hybrid Search (Vector + Keyword). Use Keyword search for specific entities (e.g., "CUSIP", "Fund Name") and Vector search for semantic queries.
- **Generation Layer:** Claude 3.5 Sonnet (for its 200k context and reasoning) or GPT-4o. Feed the retrieved Parent Chunks (Markdown tables) into the context.

### 7.2 Architecture B: The "Cost-Optimized" Open Source Pipeline

Designed for organizations with massive archives where per-page API costs are prohibitive.

- **Ingestion:** Deploy PaddleOCR (PP-StructureV3) or UniTable on internal GPU clusters. Configure to output HTML.
- **Processing:** Use a Python parsing layer (BeautifulSoup) to clean HTML and convert to JSON (Key-Value pairs).
- **Chunking:** Use Semantic Chunking on the JSON data.
- **Generation:** Llama 3 (Local) or GPT-4o-mini (Low cost API).

### 7.3 Architecture C: The "Context-First" Pipeline

Leveraging Gemini 1.5 Pro's massive context to skip the vector database entirely for specific workflows.

- **Ingestion:** Raw PDF.
- **Process:** Feed the entire document (e.g., 100 pages) into Gemini 1.5 Pro.
- **Prompt:** "Extract all fee tables from this document and output them as a JSON list."
- **Use Case:** Ideal for "Extraction" workflows (converting PDFs to Databases) rather than interactive Q&A (RAG), due to latency and cost per query.

## 8. Conclusion

The domain of financial document understanding has matured from a pattern-matching problem to a reasoning problem. The evidence suggests that "Structure" is the proxy for "Semantics" in financial data. Pipelines that treat tables as text soup are destined to fail.

For 2025, the winning strategy is **Hybridization:**

- **Layout Awareness:** Use discriminative models (Azure/TATR) to find the structure.
- **Visual Reasoning:** Use generative models (GPT-4o/Claude) to understand the nuance.
- **Schema-Aware RAG:** Use hierarchical chunking to preserve the link between data and metadata.

By adopting the High-Fidelity Hybrid Architecture, engineering teams can build RAG systems that do not merely retrieve text, but fundamentally understand the financial topology encoded within their documents.

## Works Cited

1. Benchmarking Table Extraction from Heterogeneous Scientific Documents - arXiv, accessed December 27, 2025, https://arxiv.org/html/2511.16134v1
2. poloclub/unitable: UniTable: Towards a Unified Table Foundation Model - GitHub, accessed December 27, 2025, https://github.com/poloclub/unitable
3. PubTables-1M: Towards comprehensive table extraction from unstructured documents, accessed December 27, 2025, https://www.researchgate.net/publication/363906967_PubTables-1M_Towards_comprehensive_table_extraction_from_unstructured_documents
4. TableFormer: Table Structure Understanding with Transformers - alphaXiv, accessed December 27, 2025, https://www.alphaxiv.org/overview/2203.01017v2
5. TableFormer: Table Structure Understanding With Transformers - OpenReview, accessed December 27, 2025, https://openreview.net/pdf?id=kjZN7kBCit
6. [論文評述] Benchmarking Table Extraction from Heterogeneous Scientific Extraction Documents - Moonlight, accessed December 27, 2025, https://www.themoonlight.io/tw/review/benchmarking-table-extraction-from-heterogeneous-scientific-extraction-documents
7. Benchmarking the Most Reliable Document Parsing API - Tensorlake, accessed December 27, 2025, https://www.tensorlake.ai/blog/benchmarks
8. AWS Textract vs Google, Azure, and GPT-4o: Invoice Extraction Benchmark, accessed December 27, 2025, https://www.businesswaretech.com/blog/research-best-ai-services-for-automatic-invoice-processing
9. Aligning Benchmark Datasets for Table Structure Recognition - ResearchGate, accessed December 27, 2025, https://www.researchgate.net/publication/373231680_Aligning_Benchmark_Datasets_for_Table_Structure_Recognition
10. [2303.00716] Aligning benchmark datasets for table structure recognition - arXiv, accessed December 27, 2025, https://arxiv.org/abs/2303.00716
11. Benchmarking Table Extraction: Multimodal LLMs ... - ACL Anthology, accessed December 27, 2025, https://aclanthology.org/2025.xllm-1.2.pdf
12. PaddlePaddle/PP-Chart2Table - Hugging Face, accessed December 27, 2025, https://huggingface.co/PaddlePaddle/PP-Chart2Table
13. Table Recognition - PaddlePaddle/PaddleOCR - Gitee, accessed December 27, 2025, https://gitee.com/paddlepaddle/PaddleOCR/blob/main/ppstructure/table/README.md
14. PaddleOCR-VL: Boosting Multilingual Document Parsing via a 0.9B Ultra-Compact Vision-Language Model, accessed December 27, 2025, https://ernie.baidu.com/blog/publication/PaddleOCR-VL_Technical_Report.pdf
15. PaddleOCR-VL: Boosting Multilingual Document Parsing via a 0.9B Ultra-Compact Vision-Language Model - arXiv, accessed December 27, 2025, https://arxiv.org/html/2510.14528v4
16. PaddleOCR 3.0 Technical Report - arXiv, accessed December 27, 2025, https://arxiv.org/html/2507.05595v1
17. UniTable: Towards a Unified Framework for Table Structure Recognition via Self-Supervised Pretraining - arXiv, accessed December 27, 2025, https://arxiv.org/html/2403.04822v1
18. UniTable: Towards a Unified Framework for Table Recognition via Self-Supervised Pretraining - arXiv, accessed December 27, 2025, https://arxiv.org/html/2403.04822v2
19. AI Document Extraction on Azure - Options, Comparison & Recommendations for Invoice/Contract Processing - Reddit, accessed December 27, 2025, https://www.reddit.com/r/AZURE/comments/1pq490v/ai_document_extraction_on_azure_options/
20. Azure Document Intelligence in Foundry Tools pricing, accessed December 27, 2025, https://azure.microsoft.com/en-us/pricing/details/ai-document-intelligence/
21. Amazon Textract pricing - AWS, accessed December 27, 2025, https://aws.amazon.com/textract/pricing/
22. Benchmarking Document Parsing (and What Actually Matters) -