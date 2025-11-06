from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from django.urls import reverse


class NavLink(TypedDict):
    label: str
    href: str
    external: bool


class CTALink(TypedDict, total=False):
    label: str
    href: str
    style: str
    method: str
    opens_modal: bool
    next: str
    display_name: str


def _user_display_name(request) -> str:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    first = (getattr(user, "first_name", "") or "").strip()
    if first:
        return first
    if hasattr(user, "get_short_name"):
        short = (user.get_short_name() or "").strip()
        if short:
            return short
    if hasattr(user, "get_username"):
        return user.get_username()
    return str(user)


def site_globals(request) -> Dict[str, Any]:
    """Expose global navigation, footer, and CTA copy."""

    nav_links: List[NavLink] = [
        {"label": "Features", "href": "#features", "external": False},
        {"label": "Pricing", "href": "#pricing", "external": False},
        {"label": "About", "href": "#about", "external": False},
        {"label": "Contact", "href": "#contact", "external": False},
    ]

    user_display_name = _user_display_name(request)
    is_authenticated = bool(user_display_name)

    if is_authenticated:
        nav_links = [
            {"label": "Dashboard", "href": reverse("frontend:dashboard"), "external": False},
            *nav_links,
        ]
        auth_links: List[CTALink] = [
            {"label": f"Hi, {user_display_name}", "href": reverse("frontend:dashboard"), "style": "ghost"},
            {
                "label": "Log out",
                "href": reverse("accounts:logout"),
                "method": "post",
                "style": "ghost",
            },
        ]
    else:
        auth_links = [
            {
                "label": "Sign In",
                "href": reverse("accounts:login"),
                "style": "ghost",
                "opens_modal": True,
            },
            {"label": "Register", "href": reverse("frontend:register"), "style": "ghost"},
            {"label": "Get Started", "href": reverse("frontend:register"), "style": "primary"},
        ]

    return {
        "site": {
            "brand": {"name": "Pocket", "tagline": "AI Customer Service Platform", "href": "/"},
            "nav_links": nav_links,
            "auth_links": auth_links,
            "footer": {
                "brand": "AI Support",
                "blurb": "Transforming customer service with intelligent AI solutions. Deliver exceptional support experiences that delight your customers and grow your business.",
                "categories": {
                    "Product": [
                        {"label": "Features", "href": "#features"},
                        {"label": "Pricing", "href": "#pricing"},
                        {"label": "API Documentation", "href": "/api-docs"},
                        {"label": "Integrations", "href": "/integrations"},
                        {"label": "Security", "href": "/security"},
                    ],
                    "Company": [
                        {"label": "About Us", "href": "/about"},
                        {"label": "Careers", "href": "/careers"},
                        {"label": "Press", "href": "/press"},
                        {"label": "Blog", "href": "/blog"},
                        {"label": "Contact", "href": "/contact"},
                    ],
                    "Resources": [
                        {"label": "Help Center", "href": "/help"},
                        {"label": "Community", "href": "/community"},
                        {"label": "Guides", "href": "/guides"},
                        {"label": "Status", "href": "/status"},
                        {"label": "Changelog", "href": "/changelog"},
                    ],
                    "Legal": [
                        {"label": "Privacy Policy", "href": "/privacy"},
                        {"label": "Terms of Service", "href": "/terms"},
                        {"label": "GDPR", "href": "/gdpr"},
                        {"label": "Compliance", "href": "/compliance"},
                        {"label": "Cookies", "href": "/cookies"},
                    ],
                },
                "social": [
                    {"label": "Twitter", "href": "#", "icon": "twitter"},
                    {"label": "LinkedIn", "href": "#", "icon": "linkedin"},
                    {"label": "GitHub", "href": "#", "icon": "github"},
                    {"label": "Email", "href": "#", "icon": "mail"},
                ],
                "bottom": {
                    "copyright": "© 2024 AI Support. All rights reserved.",
                    "privacy": {"label": "Privacy Policy", "href": "/privacy"},
                    "terms": {"label": "Terms of Service", "href": "/terms"},
                    "cookie": {"label": "Cookie Policy", "href": "/cookies"},
                },
            },
            "language_toggle": {
                "label": "Language",
                "options": [
                    {"code": "en", "label": "English"},
                    {"code": "ar", "label": "العربية"},
                ],
            },
            "theme_toggle": {
                "light_label": "Light mode",
                "dark_label": "Dark mode",
            },
        }
    }
