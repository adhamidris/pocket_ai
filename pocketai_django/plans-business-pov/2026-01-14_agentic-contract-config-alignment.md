# Agentic Contract + Config Alignment — Business POV

## Plan
1. Make Docker Compose pass the full repo-root `.env` into the `web` + `worker` containers (while preserving explicit overrides like `POSTGRES_HOST=postgres`).
2. Update `.env.example` to document the currently supported LLM behavior knobs (notably `LLM_TEMPERATURE`) and clarify the few “system” env vars that may appear in local `.env` files.
3. Make agentic tool outputs schema-faithful (no legacy `snippets` alongside `results/contents`) while preserving backward compatibility for legacy modes.
4. Teach the MCP prompt compactor to understand agentic payloads (`results` / `contents`) so long chats stay deterministic and don’t lose critical evidence during budget trimming.
5. Update MCP-facing docs to reflect the current tool contract and add regression tests so drift is caught early.

## Business POV
### Scenario 1: Tenant enables Azure DI + VLM repair in Docker
- Before: The tenant sets DI/VLM env vars in `.env`, but ingestion inside Docker silently runs with defaults because only a subset of env vars were forwarded.
- After: Docker reliably receives the full `.env`, so the tenant’s extraction/repair settings are honored without “works on host, fails in container” debugging.
- Success signals: Fewer ingestion “why didn’t DI run?” incidents; reduced setup time; predictable behavior across dev/staging/prod-like environments.

### Scenario 2: End-user long chat with multiple tool calls
- Before: Budget trimming can drop the model-visible `results/contents` for agentic tools (because compaction only understands legacy `snippets`), causing loops, repeats, or vague answers.
- After: Compaction keeps the important fields for agentic payloads (compact `results` and clipped `contents`), so the model can continue deterministically without re-searching.
- Success signals: Fewer redundant tool calls; lower latency/cost per turn; fewer “please repeat” / “I couldn’t find it” responses in longer sessions.

### Scenario 3: Developer onboarding / teammate changes knobs
- Before: `.env.example` claims to be exhaustive, but misses key knobs used by runtime (e.g., temperature), and docs mention legacy tool names, creating confusion and misconfiguration.
- After: `.env.example` and docs match the actual runtime contract; new contributors can configure the system correctly on the first try.
- Success signals: Fewer Slack/DM questions about “which env var works?”; fewer PRs that accidentally re-introduce legacy tool assumptions.

### Scenario 4: Ops / release confidence
- Before: Small contract drift (“snippets vs contents”, docs vs code) shows up late as quality regressions rather than as fast test failures.
- After: Regression tests enforce the agentic tool envelope and compaction behavior.
- Success signals: Faster iteration on retrieval without breaking the tool loop; fewer production hotfixes due to contract mismatches.

