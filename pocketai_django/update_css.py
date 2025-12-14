
import os
import re

file_path = '../src/index.css'

with open(file_path, 'r') as f:
    content = f.read()

# Define the target block to replace (using regex for flexibility with whitespace)
pattern = r'(\.text-gradient-hero\s*\{[^}]*)(\})'
replacement = r'\1  background-size: 200% auto;\n    animation: gradientShift 6s ease infinite;\n  \2'

# Check if already applied
if 'animation: gradientShift' in content and '.text-gradient-hero' in content:
    # Check if it's inside the class block involves more complex parsing or just trusting the user won't run it twice blindly.
    # But let's check specifically if the class block has it.
    match = re.search(r'\.text-gradient-hero\s*\{[^}]*animation: gradientShift', content, re.DOTALL)
    if match:
        print("Animation already present.")
        exit(0)

new_content = re.sub(pattern, replacement, content, count=1)

if new_content == content:
    print("Could not find .text-gradient-hero block to update.")
    exit(1)

with open(file_path, 'w') as f:
    f.write(new_content)

print(f"Successfully updated {file_path}")
