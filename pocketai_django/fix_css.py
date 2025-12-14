
import os
import re

file_path = '../src/index.css'

with open(file_path, 'r') as f:
    content = f.read()

# 1. Remove the incorrectly added lines. 
# We look for the specific block where we added it previously.
# The previous script replaced `(\.text-gradient-hero\s*\{[^}]*)(\})` with `\1  background-size: 200% auto;\n    animation: gradientShift 6s ease infinite;\n  \2`
# This likely hit: `.light .register-header .text-gradient-hero`

# Regex to find the bad block and clean it. 
# We want to remove the specific lines *if* they are not in the main block. 
# But let's just do a targeted removal of the exact string sequence we added, from the *first* occurrence, or globally if we are sure the main one doesn't have it yet.
# Actually, the main one *doesn't* have it yet.
# So we can remove the lines `background-size: 200% auto;` and `animation: gradientShift 6s ease infinite;` if they look like what we added.

incorrect_pattern = r'(background-size: 200% auto;\s+animation: gradientShift 6s ease infinite;)'
# We will remove this wherever found, then re-add to correct place.
# Be careful not to remove valid ones. But wait, did I add it with that indentation?
# Previous script replacement: `\1  background-size: 200% auto;\n    animation: gradientShift 6s ease infinite;\n  \2`
# So indentation was `  ` then `    `.

# Let's clean up broadly.
clean_content = content.replace("  background-size: 200% auto;\n    animation: gradientShift 6s ease infinite;\n", "")

# 2. Add to the correct block.
# The correct block matches exactly:
# .text-gradient-hero {
#    background: var(--gradient-hero);
#    -webkit-background-clip: text;
#    -webkit-text-fill-color: transparent;
#    background-clip: text;
#  }

correct_block_regex = r'(\.text-gradient-hero\s*\{\s*background: var\(--gradient-hero\);[^}]*)(background-clip: text;)(\s*\})'
# We append before the closing brace.

# Note: The grep output showed:
#   .text-gradient-hero {
#     background: var(--gradient-hero);
#     -webkit-background-clip: text;
#     -webkit-text-fill-color: transparent;
#     background-clip: text;
#   }
# (Indentation seems to be 2 spaces based on previous grep output).

target_replacement = r'\1\2\n    background-size: 200% auto;\n    animation: gradientShift 6s ease infinite;\3'

new_content = re.sub(correct_block_regex, target_replacement, clean_content, count=1)

if new_content == content:
    print("No changes made. Check if regex matches.")
    # Debug: print the last part of file to see structure
    print("Last 500 chars:", content[-500:])
else:
    with open(file_path, 'w') as f:
        f.write(new_content)
    print("Fixed CSS file.")
