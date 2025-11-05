#!/bin/bash

# Find the absolute line number of the uploads subtitle
echo "# --- Locating uploads subtitle line ---"

SUBTITLE_LINE_NUM=$(grep -n '"subtitle": "Add links to your key docs so your agent gets smart fast"' pocketai_django/frontend/views.py | cut -d: -f1)

if [ -z "$SUBTITLE_LINE_NUM" ]; then
  echo "Error: Could not find the uploads subtitle line"
  exit 1
fi

echo "Found subtitle at line: $SUBTITLE_LINE_NUM"
sed -n "$((SUBTITLE_LINE_NUM - 5)),$((SUBTITLE_LINE_NUM + 5))p" pocketai_django/frontend/views.py
echo "# --- Ready to append after line $SUBTITLE_LINE_NUM ---"
