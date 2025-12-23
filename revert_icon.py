import os

path = '/Users/adham/Desktop/pocket_ai-main 2/pocketai_django/frontend/templates/frontend/index.html'
with open(path, 'r') as f:
    content = f.read()

# 1. Remove Style Block
# Search for signature
style_sig = '@keyframes pulse-ring'
idx_sig = content.find(style_sig)

if idx_sig != -1:
    # Find start <style>
    start_tag = content.rfind('<style>', 0, idx_sig)
    # Find end </style>
    end_tag = content.find('</style>', idx_sig)
    
    if start_tag != -1 and end_tag != -1:
        end_pos = end_tag + 8 # len('</style>')
        # Remove, keeping surrounding lines clean
        # Look for newline before start_tag
        prev_newline = content.rfind('\n', 0, start_tag)
        if prev_newline != -1:
            start_pos = prev_newline
        else:
            start_pos = start_tag
            
        content = content[:start_pos] + content[end_pos:]

# 2. Replace Div Block
div_sig = 'bg-gradient-to-br from-cyan-500 to-blue-600'
idx_div = content.find(div_sig)

new_div = '''                <div class="h-8 w-8 rounded-lg bg-primary flex items-center justify-center">
                  <span class="text-white text-xs font-semibold" data-chat-agent-initials>AI</span>
                </div>'''

if idx_div != -1:
    # Find start of <div
    start_tag = content.rfind('<div', 0, idx_div)
    
    # Heuristic for end: look for the closing </div> after </svg>
    # The block ends with </svg> then </div>
    svg_end_sig = '</svg>'
    idx_svg = content.find(svg_end_sig, start_tag)
    
    if idx_svg != -1:
        end_div = content.find('</div>', idx_svg)
        if end_div != -1:
            end_pos = end_div + 6 # len('</div>')
            content = content[:start_tag] + new_div + content[end_pos:]

with open(path, 'w') as f:
    f.write(content)
