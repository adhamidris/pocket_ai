# Plan

1. Capture plan + business POV doc
2. Refactor search ref shaping for tables
3. Upgrade read behavior for table refs
4. Harden stream markdown/block persistence
5. Add/adjust tests for new behavior
6. Run focused test suite

# Business POV

## Why this matters
Customers asking for tariff/fee comparisons need complete and readable answers. Current behavior can return fragmented row slices and malformed markdown blocks, which harms trust and increases manual support load.

## Practical scenarios

### 1) Multi-currency tariff comparison (EGP vs USD)
- **Today:** Retrieval over-focuses on single rows; answer can contain chopped phrases and broken heading/list formatting.
- **After fix:** Search/read pipeline favors table context where appropriate, so the model sees coherent rows/columns and returns cleaner structured summaries.
- **Success signal:** Fewer malformed responses and lower repeat-question rate for the same topic.

### 2) Agent references many rows from the same table
- **Today:** Duplicate row-level refs increase token noise and reduce synthesis quality.
- **After fix:** Ref shaping promotes table context and reduces repetitive row-only evidence when table-level context is better.
- **Success signal:** Lower prompt clutter; more complete first-answer accuracy.

### 3) Streaming output with malformed markdown
- **Today:** A line like "## Heading 1. item" can persist as malformed heading blocks.
- **After fix:** Finalization path repairs/normalizes markdown before persistence and prevents malformed block structures from becoming canonical.
- **Success signal:** Portal message rendering remains stable and readable.

### 4) Inconsistent table schema (duplicate column names)
- **Today:** Duplicate columns leak into evidence payloads and confuse generation.
- **After fix:** Column normalization/deduping yields stable structured table payloads.
- **Success signal:** Cleaner evidence payloads and fewer contradictory list items.

## Risks / regressions to watch
- Reading larger table context can raise token usage; must keep bounded by existing budgets/cursors.
- Over-promotion to table context could reduce precision for pinpoint row questions; keep targeted-row capability for exact lookups.

## How we know it worked
- Automated tests cover:
  - table ref shaping behavior,
  - read behavior for grouped table evidence,
  - markdown repair persistence path.
- Manual sample: problematic query now produces valid heading + list structure, with fewer truncation artifacts.
