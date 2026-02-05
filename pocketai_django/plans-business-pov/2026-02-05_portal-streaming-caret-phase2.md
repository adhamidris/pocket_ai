# Portal Streaming Caret Stability (Phase 2)

## Plan
1. Make the streaming caret follow the **block currently being client-paced/drained**, not the newest incoming block event.
2. Restrict caret rendering to **leaf text blocks** (paragraph/heading/list_item/text + code/reasoning code), never structural containers (list/quote).
3. Keep the change isolated to portal UI behavior: no changes to content semantics or block rendering order.

## Business POV

### What this improves
- The purple blinking caret feels “anchored” to where the visitor sees text actually typing.
- Prevents “cursor teleporting” when the backend emits deltas faster than the UI pacing can render them.
- Removes caret appearing after container blocks (lists/quotes), which reads like a formatting glitch.

### Scenarios to validate (2–5)
1. **Long paragraphs**  
   The caret stays at the end of the currently typing paragraph, even if later blocks have already arrived from the server.
2. **Lists streaming in**  
   The caret never appears on the list container; it appears on the active `list_item` as each item is typed.
3. **Quote blocks**  
   The caret never appears on the quote container; it appears on the leaf paragraph(s) inside it.
4. **Code blocks + reasoning**  
   The caret renders at the end of the code text (inside `<code>`) and in reasoning code, not on the outer containers.

### Possible regressions
- The caret may “lag” behind server events while pacing backlog drains; this is intentional and matches visible typing.
- If a block DOM node is missing/unmounted unexpectedly, the caret may temporarily disappear rather than jump to a container.

### How success is measured
- Manual QA shows no caret on list/quote containers and no mid-stream caret jumps.
- Reduced user reports of “flashing cursor” / “caret in random places” during streaming.

