from __future__ import annotations

from typing import Iterable, Mapping

from django import template

register = template.Library()


@register.inclusion_tag("frontend/components/buttons/primary.html")
def primary_button(label: str, href: str = "#", icon: str | None = None, size: str = "md"):
    return {"label": label, "href": href, "icon": icon, "size": size}


@register.inclusion_tag("frontend/components/buttons/ghost.html")
def ghost_button(label: str, href: str = "#", icon: str | None = None, size: str = "md"):
    return {"label": label, "href": href, "icon": icon, "size": size}


@register.inclusion_tag("frontend/components/cards/stat.html")
def stat_card(title: str, value: str, description: str | None = None):
    return {"title": title, "value": value, "description": description}


@register.inclusion_tag("frontend/components/cards/feature.html")
def feature_card(feature: Mapping[str, str]):
    return {"feature": feature}


@register.inclusion_tag("frontend/components/chat/message.html")
def chat_message(message: Mapping[str, str]):
    return {"message": message}


@register.simple_tag
def join_classes(*classes: Iterable[str | None]) -> str:
    parts: list[str] = []
    for item in classes:
        if not item:
            continue
        if isinstance(item, str):
            parts.append(item)
        else:
            parts.extend(filter(None, item))
    return " ".join(parts)
