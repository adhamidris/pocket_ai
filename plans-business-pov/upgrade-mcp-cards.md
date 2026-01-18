# Upgrade MCP Tool Cards Design

## Business POV
### Goal Description
Enhance the visual design of the MCP tool calling cards in the chat portal to match the "Premium" and "Dynamic" aesthetic requirements. The current cards use basic utility styling; we will move to a bespoke, glassmorphic design that feels "alive" and builds user trust in the AI's actions.

### Scenarios
1.  **User observes a long-running tool**:
    *   *Current*: Static card says "Running".
    *   *New*: Card pulses subtly; a "working" indicator animates. User feels the system is active and processing.
2.  **User reviews past actions**:
    *   *Current*: Cluttered list of gray boxes.
    *   *New*: Clean, structured history with clear hierarchy, distinct input/output sections, and refined typography.

### Success Metrics
*   **Visual Consistency**: Tool cards match the high-end feel of the rest of the application.
*   **Code Maintainability**: Moving inline Tailwind strings to a scoped CSS file (`mcp-cards.css`) makes `chat-portal.js` easier to read and maintain.

## User Review Required
> [!NOTE]
> This change introduces a new CSS file `frontend/static/css/mcp-cards.css` which must be included in the portal template.

## Proposed Changes

### Frontend
#### [NEW] [mcp-cards.css](file:///Users/adham/Desktop/pocket_ai-main%202/pocketai_django/frontend/static/css/mcp-cards.css)
*   Define `.mcp-card` component with glassmorphism (background blur, subtle border).
*   Define `.mcp-card-header`, `.mcp-card-body`, `.mcp-chip` classes.
*   Add animations for `details[open]` transitions and status pulsing.

#### [MODIFY] [portal.html](file:///Users/adham/Desktop/pocket_ai-main%202/pocketai_django/frontend/templates/frontend/chat/portal.html)
*   Include `mcp-cards.css` in the `extra_head` block.

#### [MODIFY] [chat-portal.js](file:///Users/adham/Desktop/pocket_ai-main%202/pocketai_django/frontend/static/js/chat-portal.js)
*   Update `buildToolEventCard` to use the new CSS classes instead of long Tailwind utility strings.
*   Refactor `updateToolEventCard` to toggle class names for status changes rather than replacing text/classes manually where possible.

## Verification Plan

### Manual Verification
1.  **Trigger a Tool Call**:
    *   Use the "simulator" or interact with an agent that calls a tool (e.g., "Check status of ticket #123").
2.  **Observe Rendering**:
    *   Verify the card appears with the new glassmorphic style.
    *   Check that the "Running" state has a pulsing animation.
    *   Click to expand/collapse and verify the smooth animation.
    *   Check Input/Output tabs switch correctly and look styled.
3.  **Mobile Check**:
    *   Resize browser to mobile width and ensure the card fits and remains readable.
