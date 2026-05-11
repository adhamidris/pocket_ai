import sys
from apps.conversations.rich_blocks import RichBlockStreamBuilder

builder = RichBlockStreamBuilder()
chunks = [
    "| Head 1 | Head 2 |\n",
    "|---|---|\n",
    "| Cell 1 | Cell 2 |\n",
    "| Cell 3 |"
]
for chunk in chunks:
    events = builder.process_text_chunk(chunk)
    print("Chunk:", repr(chunk))
    for event in events:
        print("  Event:", event)

