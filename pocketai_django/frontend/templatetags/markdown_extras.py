from django import template
from django.template.defaultfilters import stringfilter
import markdown
import bleach

register = template.Library()

@register.filter
@stringfilter
def render_markdown(value):
    # Convert markdown to HTML
    html = markdown.markdown(value, extensions=['tables', 'fenced_code', 'nl2br'])
    
    # Sanitize HTML
    allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + ['p', 'div', 'span', 'br', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'code', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
    allowed_attributes = {
        **bleach.sanitizer.ALLOWED_ATTRIBUTES,
        '*': ['class', 'style'],
    }
    cleaned_html = bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes, strip=True)
    
    # Post-process to add Tailwind classes to tables to match client-side rendering
    cleaned_html = cleaned_html.replace('<table>', '<div class="mt-3 overflow-hidden rounded-xl border border-border/60 bg-background/80 shadow-sm"><table class="w-full border-collapse text-sm">')
    cleaned_html = cleaned_html.replace('</table>', '</table></div>')
    cleaned_html = cleaned_html.replace('<thead>', '<thead class="bg-muted/40 text-muted-foreground">')
    cleaned_html = cleaned_html.replace('<th>', '<th class="px-3 py-2 text-left font-medium">')
    cleaned_html = cleaned_html.replace('<td>', '<td class="px-3 py-2 border-t border-border/50">')
    
    # Match client-side vertical spacing by replacing breaks with paragraph splits
    cleaned_html = cleaned_html.replace('<br>', '</p><p>').replace('<br/>', '</p><p>').replace('<br />', '</p><p>')
    
    return cleaned_html
