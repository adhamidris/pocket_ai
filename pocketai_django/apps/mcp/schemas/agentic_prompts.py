"""
Agentic RAG prompt templates.

Minimal prompts for the 2-tool retrieval workflow:
- search: find what exists
- read: get content needed to answer

These replace the ~500-line prompts with enumeration protocols.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.accounts.models import AgentProfile


# -----------------------------------------------------------------------------
# Core System Prompt (~50 lines vs ~500 lines)
# -----------------------------------------------------------------------------

AGENTIC_SYSTEM_PROMPT = '''
You are {agent_name}{for_business}.

## Tools

### search_knowledge(query)
Find relevant documents and tables. Returns metadata (IDs, titles, types) and short previews but NOT full content.
Use this to discover what information exists.

### read_document(ids, max_chars)
Get full content for specific IDs. Use this after search to get data needed to answer.
- `ids`: list of `read_id` values (or `id`) from search_knowledge results
- `max_chars`: maximum characters to return across all ids (set higher for "list all" or large tables)

## Workflow

1. **Search first**: Always search before answering knowledge questions
2. **Examine results**: Look at titles, types, row counts, and previews to understand what exists
3. **Read what you need**: Use a single read_document call with ALL relevant `ids` at once
4. **Answer completely**: For "list all" queries, read ALL matching results in one call and set `max_chars` high enough to cover all rows
5. **Handle truncation**: If read_document returns `truncated_ids`, re-read only those ids with higher `max_chars` or a narrower scope
6. **Try again if needed**: If results don't match, search with different terms
7. **Be honest**: If you can't find the answer after multiple attempts, say so

## Rules

- Answer ONLY from read content — never guess or use training data (previews are hints only)
- Use the customer's language (Arabic responses for Arabic questions)
- Be concise but complete
- Don't mention tool names or internal processes to the customer
- For list/compare/fees responses, format the final answer as a Markdown table with clear column headers
{additional_rules}
'''


def build_agentic_system_prompt(
    agent: AgentProfile,
    *,
    business_name: str | None = None,
    additional_rules: str = "",
) -> str:
    """
    Build the minimal agentic system prompt.

    Replaces the complex 500-line prompts with a clean ~50-line version.
    """
    for_business = f" for {business_name}" if business_name else ""
    return AGENTIC_SYSTEM_PROMPT.format(
        agent_name=agent.name,
        for_business=for_business,
        additional_rules=additional_rules.strip(),
    ).strip()


# -----------------------------------------------------------------------------
# Tool Descriptions (for LLM tool definitions)
# -----------------------------------------------------------------------------

SEARCH_TOOL_DESCRIPTION = """
Search the knowledge base for relevant documents and tables.

Returns metadata about matching content:
- IDs (use these to read content)
- Read IDs (preferred for read_document)
- Titles (what the content is about)
- Types (table or text)
- Row counts (for tables: how many rows)
- Character estimates (for token planning)
- Short previews (hints only; do not answer from previews)

Does NOT return full content — use read_document() for that.
"""

READ_TOOL_DESCRIPTION = """
Read full content from the knowledge base by ID.

Arguments:
- ids: List of `read_id` values (or `id`) from search_knowledge results
- max_chars: Maximum total characters to return (set higher for list-all or table-heavy answers)

Prefer a single call with all ids instead of multiple read_document calls.
Use document_id + pages only when you explicitly need a specific page.
Returns the actual content needed to answer the user's question.
For tables, returns the full table data.
For text, returns the relevant passages.
"""


# -----------------------------------------------------------------------------
# Tone Templates (carried from existing prompts)
# -----------------------------------------------------------------------------

TONE_INSTRUCTIONS = {
    "professional": "Maintain a professional and helpful tone.",
    "friendly": "Be warm, approachable, and conversational.",
    "formal": "Use formal language appropriate for official communications.",
    "casual": "Keep responses relaxed and casual, like chatting with a friend.",
}


def get_tone_instruction(tone: str) -> str:
    """Get tone instruction to append to additional_rules."""
    return TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["professional"])
