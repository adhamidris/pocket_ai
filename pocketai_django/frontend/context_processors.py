from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _


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
        {"label": _("Features"), "href": "#features", "external": False},
        {"label": _("Pricing"), "href": "#pricing", "external": False},
        {"label": _("Testimonials"), "href": "#testimonials", "external": False},
        {"label": _("FAQ"), "href": "#faq", "external": False},
    ]

    user_display_name = _user_display_name(request)
    is_authenticated = bool(user_display_name)

    if is_authenticated:
        nav_links = [
            {"label": _("Dashboard"), "href": reverse("frontend:dashboard"), "external": False},
            *nav_links,
        ]
        auth_links: List[CTALink] = [
            {
                "label": _("Hi, %(name)s") % {"name": user_display_name},
                "href": reverse("frontend:dashboard"),
                "style": "ghost",
            },
            {
                "label": _("Log out"),
                "href": reverse("accounts:logout"),
                "method": "post",
                "style": "ghost",
            },
        ]
    else:
        auth_links = [
            {
                "label": _("Sign In"),
                "href": reverse("accounts:login"),
                "style": "ghost",
                "opens_modal": True,
            },
            {"label": _("Register"), "href": reverse("frontend:register"), "style": "ghost"},
            {"label": _("Get Started"), "href": reverse("frontend:register"), "style": "primary"},
        ]

    language_options = [
        {"code": str(code), "label": label}
        for code, label in getattr(settings, "LANGUAGES", ())
        if str(code or "").strip()
    ]
    if not language_options:
        language_options = [{"code": "en", "label": _("English")}]

    return {
        "site": {
            "brand": {"name": "Pocket", "tagline": _("AI Customer Service Platform"), "href": "/"},
            "nav_links": nav_links,
            "auth_links": auth_links,
            "footer": {
                "brand": _("AI Support"),
                "blurb": _(
                    "Transforming customer service with intelligent AI solutions. "
                    "Deliver exceptional support experiences that delight your customers and grow your business."
                ),
                "categories": {
                    _("Product"): [
                        {"label": _("Features"), "href": "#features"},
                        {"label": _("Pricing"), "href": "#pricing"},
                        {"label": _("API Documentation"), "href": "/api-docs"},
                        {"label": _("Integrations"), "href": "/integrations"},
                        {"label": _("Security"), "href": "/security"},
                    ],
                    _("Company"): [
                        {"label": _("About Us"), "href": "/about"},
                        {"label": _("Careers"), "href": "/careers"},
                        {"label": _("Press"), "href": "/press"},
                        {"label": _("Blog"), "href": "/blog"},
                        {"label": _("Contact"), "href": "/contact"},
                    ],
                    _("Resources"): [
                        {"label": _("Help Center"), "href": "/help"},
                        {"label": _("Community"), "href": "/community"},
                        {"label": _("Guides"), "href": "/guides"},
                        {"label": _("Status"), "href": "/status"},
                        {"label": _("Changelog"), "href": "/changelog"},
                    ],
                    _("Legal"): [
                        {"label": _("Privacy Policy"), "href": "/privacy"},
                        {"label": _("Terms of Service"), "href": "/terms"},
                        {"label": _("GDPR"), "href": "/gdpr"},
                        {"label": _("Compliance"), "href": "/compliance"},
                        {"label": _("Cookies"), "href": "/cookies"},
                    ],
                },
                "social": [
                    {"label": _("Twitter"), "href": "#", "icon": "twitter"},
                    {"label": _("LinkedIn"), "href": "#", "icon": "linkedin"},
                    {"label": _("GitHub"), "href": "#", "icon": "github"},
                    {"label": _("Email"), "href": "#", "icon": "mail"},
                ],
                "bottom": {
                    "copyright": _("© 2024 AI Support. All rights reserved."),
                    "privacy": {"label": _("Privacy Policy"), "href": "/privacy"},
                    "terms": {"label": _("Terms of Service"), "href": "/terms"},
                    "cookie": {"label": _("Cookie Policy"), "href": "/cookies"},
                },
            },
            "language_toggle": {
                "label": _("Language"),
                "options": language_options,
            },
            "theme_toggle": {
                "light_label": _("Light mode"),
                "dark_label": _("Dark mode"),
            },
        }
    }
