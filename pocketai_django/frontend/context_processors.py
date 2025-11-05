from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class NavLink(TypedDict):
    label: str
    href: str
    external: bool


class CTALink(TypedDict, total=False):
    label: str
    href: str
    style: str


def site_globals(_request) -> Dict[str, Any]:
    """Expose global navigation, footer, and CTA copy."""

    nav_links: List[NavLink] = [
        {"label": "Features", "href": "#features", "external": False},
        {"label": "Pricing", "href": "#pricing", "external": False},
        {"label": "About", "href": "#about", "external": False},
        {"label": "Contact", "href": "#contact", "external": False},
    ]

    auth_links: List[CTALink] = [
        {"label": "Sign In", "href": "/login", "style": "ghost"},
        {"label": "Register", "href": "/register", "style": "ghost"},
        {"label": "Get Started", "href": "/get-started", "style": "primary"},
    ]

    footer_columns = [
        {
            "title": "Product",
            "links": [
                {"label": "Features", "href": "#features"},
                {"label": "Integrations", "href": "/integrations"},
                {"label": "Roadmap", "href": "/roadmap"},
            ],
        },
        {
            "title": "Company",
            "links": [
                {"label": "About", "href": "#about"},
                {"label": "Customers", "href": "/customers"},
                {"label": "Careers", "href": "/careers"},
            ],
        },
        {
            "title": "Resources",
            "links": [
                {"label": "Documentation", "href": "/docs"},
                {"label": "Support", "href": "/support"},
                {"label": "Status", "href": "/status"},
            ],
        },
    ]

    legal_links = [
        {"label": "Privacy Policy", "href": "/privacy"},
        {"label": "Terms of Service", "href": "/terms"},
        {"label": "Cookies", "href": "/cookies"},
    ]

    return {
        "site": {
            "brand": {"name": "Pocket", "tagline": "AI Customer Service Platform", "href": "/"},
            "nav_links": nav_links,
            "auth_links": auth_links,
            "footer": {
                "columns": footer_columns,
                "legal": legal_links,
                "copyright": "© Pocket AI Customer Service Platform",
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
