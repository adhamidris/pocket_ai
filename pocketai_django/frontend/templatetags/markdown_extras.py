from django import template
from django.template.defaultfilters import stringfilter
import markdown
import bleach

register = template.Library()

@register.filter
@stringfilter
def render_markdown(value):
    html = markdown.markdown(value, extensions=["tables", "fenced_code", "nl2br"])

    allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
        "p", "br",
        "table", "thead", "tbody", "tr", "th", "td",
        "code", "pre",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li",
        "blockquote",
    ]
    allowed_attributes = {
        **bleach.sanitizer.ALLOWED_ATTRIBUTES,
        "a": ["href", "title", "target", "rel", "class"],
        "*": ["class"],
    }
    return bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes, strip=True)
