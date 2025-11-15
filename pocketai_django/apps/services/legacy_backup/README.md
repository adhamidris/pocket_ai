# Legacy Orchestrator Backup

This folder snapshots the original ledger-based orchestrator stack before the MCP migration.

## Files
- `ai_orchestrator_legacy.py`: Copy of `apps/services/ai_orchestrator.py` as of MCP phase 6.
- `ai_prompt_builder_legacy.py`: Copy of `apps/services/ai_prompt_builder.py` as of MCP phase 6.

## Restoring
1. Replace imports of `AiOrchestratorService` / `PromptBuilder` in `apps/api/chat_portal.py` and related modules with the versions in this folder.
2. Ensure feature flag `RAG_USE_MCP_ORCHESTRATOR` is set to `false` (or remove the MCP routing).
3. If additional files are removed later, copy their backups from this folder into their original locations.

Each legacy file includes a header comment pointing back to its source path and commit context. Keep this folder up to date if new components are unmapped.
