# Working Agreement for AI Coding Agents

This repo is a multi-tenant B2B SaaS. Your job is to ship production-ready changes that keep the platform safe, maintainable, and scalable.

## Read first
- `docs/product/saas_brief.md`
- `docs/product/technical.md`
- `README.md`

## Planning preferences (owner guidance)
- Prefer **long-term, correct refactors** over quick workarounds.
- Avoid “guardrails spaghetti” (try → fail → fallback loops). If you add a guardrail for safety, pair it with (or clearly propose) the **root-cause fix**.
- Don’t rush implementations. If requirements are unclear, **interview/clarify first**, then propose a solid plan that covers edge cases.
- Think “production-first”: avoid designs that only work because of dev quirks (e.g., `LocMemCache`, single-process assumptions).
- **Multi-agent work**: Other agents may be working in this repo concurrently. If you notice unrelated diffs, do **not** revert/restore them as “cleanup” — keep your changes tightly scoped to your task and leave unrelated edits alone.
- When initiating a plan, also write a **business POV** for that plan:
  - Include 2–5 realistic scenarios and the expected user experience (what improves, what might regress, how success is measured).
  - Save it under `plans-business-pov/` and include the **plan** followed by the **business POV**.

## Core invariants (do not break)
- **Tenant isolation**: no cross-tenant reads/writes, including caches and background jobs.
- **Privacy by default**: never expose sensitive data unless verified lookup/tenant policy allows it.
- **Auditability**: keep access/audit trails, but avoid storing raw document content/PII in logs so deletions remain meaningful.
- **Deterministic retrieval**: retrieval should be bounded, observable, and avoid unbounded fanout/thrashing.

## Documentation hygiene
- Keep `docs/product/saas_brief.md` business-only and `docs/product/technical.md` technical-only.
- Avoid duplicate “platform overview” docs that drift and confuse contributors.
- If you add/rename docs, update `docs/README.md` and `README.md`.

## Frontend & UI Strategy
> **CRITICAL**: The project relies on a pre-compiled `main.css` file. Attempting to run a Tailwind build (`npx tailwindcss`) or modifying `package.json` to generate CSS will **BREAK** the application's global styling.

1.  **Treat `main.css` as Read-Only**: Never overwrite, regenerate, or minimize this file.
2.  **No New Tailwind Utilities**: Do not assume you can add new Tailwind classes (e.g., `backdrop-blur-xl`, `bg-white/5`) and have them work. If they aren't in `main.css` already, they won't exist.
3.  **Use Scoped Pure CSS**: When building complex new UI components (especially glassmorphism or high-end designs):
    *   Create a **namespaced Component** (e.g., `.mcp-card`) in a `<style>` block or dedicated pre-loaded CSS file.
    *   Use `all: unset` or high-specificity selectors to isolate your component from global legacy styles.
    *   Manually define all properties (padding, colors, borders) to ensure pixel-perfect rendering without external dependencies.
