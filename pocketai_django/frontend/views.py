from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.utils import timezone

from apps.cases.models import Case, CaseStatus
from apps.customers.models import Customer
from apps.services.cases import list_cases
from apps.services.customers import list_customers

def _mobile_app_section() -> Dict[str, object]:
    return {
        "title": "Try the mobile app",
        "stores": [
            {
                "label": "Google Play",
                "href": "#play",
                "icon": """<svg width="30" height="30" viewBox="0 0 512 512" aria-hidden="true"><path fill="currentColor" d="M325.3 234.3 90.7 28.6C79 19 64 24.7 64 39.3v433.4c0 14.7 15 20.3 26.7 10.7l234.6-205.7c9.3-8.1 9.3-23.1 0-30.4z"/><linearGradient id="g2-app" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#34a853"/><stop offset="100%" stop-color="#4285f4"/></linearGradient><path fill="url(#g2-app)" d="M421.9 213.8 360.4 178 325.3 234.3c9.3 8.1 9.3 23.1 0 30.4l35.1 56.3 61.5-35.8c18.5-10.8 18.5-38.5 0-49.4z"/></svg>""",
            },
            {
                "label": "App Store",
                "href": "#store",
                "icon": """<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.365 1.43c0 1.14-.47 2.25-1.2 3.05-.76.83-2.01 1.47-3.12 1.39-.13-1.13.38-2.27 1.14-3.06.79-.85 2.12-1.46 3.18-1.38zm3.54 16.3c-.61 1.36-.9 1.95-1.68 3.15-1.09 1.67-2.63 3.75-4.53 3.75-1.7 0-2.14-1.1-4.46-1.1-2.34 0-2.83 1.1-4.53 1.1-1.92 0-3.36-1.8-4.45-3.46C-.02 17.9-.4 14.49 1.3 12.23c1.1-1.54 2.86-2.51 4.85-2.55 1.9-.04 3.69 1.28 4.46 1.28.77 0 2.54-1.58 4.3-1.35 1.47.17 2.85.76 3.88 1.73-3.52 1.93-2.95 6.97.1 7.49z"/></svg>""",
            },
        ],
    }


def _legal_section(
    section_id: str,
    title: str,
    paragraphs: List[str] | None = None,
    bullets: List[str] | None = None,
) -> Dict[str, object]:
    body: List[Dict[str, object]] = []
    for text in paragraphs or []:
        body.append({"type": "paragraph", "text": text})
    if bullets:
        body.append({"type": "list", "items": bullets})
    return {"id": section_id, "title": title, "body": body}


def _current_user_name(request: HttpRequest) -> str:
    if not request.user.is_authenticated:
        return ""
    user = request.user
    first = getattr(user, "first_name", "")
    if isinstance(first, str) and first.strip():
        return first.strip()
    if hasattr(user, "get_short_name"):
        short = (user.get_short_name() or "").strip()
        if short:
            return short
    if hasattr(user, "get_username"):
        return user.get_username()
    return str(user)


def landing(request: HttpRequest) -> HttpResponse:
    """Render landing page with server-authored copy."""
    hero = {
        "title_prefix": "AI Powered",
        "title_highlight": "Customer Service",
        "subtitle": "Deliver instant, intelligent support 24/7 with our AI-powered platform. Reduce response times by 90% and delight your customers.",
        "primary_cta": {"label": "Start Free Trial", "href": "/register"},
        "secondary_cta": {"label": "Watch Demo", "href": "#demo"},
        "stats": [
            {
                "id": "faster-response",
                "label": "Faster Response",
                "target": 90,
                "suffix": "%",
                "format": "integer",
            },
            {
                "id": "ai-support",
                "label": "AI Support",
                "target": 24,
                "suffix": "/7",
                "format": "hours",
            },
            {
                "id": "happy-customers",
                "label": "Happy Customers",
                "target": 10_000,
                "suffix": "",
                "format": "thousands-plus",
            },
        ],
        "store_links": [
            {
                "id": "google-play",
                "label_top": "GET IT ON",
                "label_bottom": "Google Play",
                "href": "#play",
                "icon": """<svg width="30" height="30" viewBox="0 0 512 512" aria-hidden="true"><defs><linearGradient id="hero-google-play" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#34a853"/><stop offset="100%" stop-color="#4285f4"/></linearGradient></defs><path fill="currentColor" d="M325.3 234.3 90.7 28.6C79 19 64 24.7 64 39.3v433.4c0 14.7 15 20.3 26.7 10.7l234.6-205.7c9.3-8.1 9.3-23.1 0-30.4z"/><path fill="url(#hero-google-play)" d="M421.9 213.8 360.4 178 325.3 234.3c9.3 8.1 9.3 23.1 0 30.4l35.1 56.3 61.5-35.8c18.5-10.8 18.5-38.5 0-49.4z"/></svg>""",
            },
            {
                "id": "app-store",
                "label_top": "Download on the",
                "label_bottom": "App Store",
                "href": "#store",
                "icon": """<svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.365 1.43c0 1.14-.47 2.25-1.2 3.05-.76.83-2.01 1.47-3.12 1.39-.13-1.13.38-2.27 1.14-3.06.79-.85 2.12-1.46 3.18-1.38zm3.54 16.3c-.61 1.36-.9 1.95-1.68 3.15-1.09 1.67-2.63 3.75-4.53 3.75-1.7 0-2.14-1.1-4.46-1.1-2.34 0-2.83 1.1-4.53 1.1-1.92 0-3.36-1.8-4.45-3.46C-.02 17.9-.4 14.49 1.3 12.23c1.1-1.54 2.86-2.51 4.85-2.55 1.9-.04 3.69 1.28 4.46 1.28.77 0 2.54-1.58 4.3-1.35 1.47.17 2.85.76 3.88 1.73-3.52 1.93-2.95 6.97.1 7.49z"/></svg>""",
            },
        ],
        "demo": {
            "browser_bar": "chat.pocket.ai",
            "online_label": "Online",
            "input_placeholder": "This is a demo - try the real widget below! →",
            "scenarios": [
                {
                    "agent_name": "Nancy",
                    "job_title": "E-Commerce Support Agent",
                    "conversation": [
                        {"id": 1, "text": "Hi! I need help with my order #12345", "is_bot": False, "delay": 1000},
                        {"id": 2, "text": "Hello, I’m Nancy. I’d be glad to help. I’ll check that order now.", "is_bot": True, "delay": 1400},
                        {"id": 3, "text": "Thanks for waiting. Your order shipped yesterday and should arrive tomorrow by 3 PM. Tracking: TR123456789.", "is_bot": True, "delay": 1600},
                        {"id": 4, "text": "Perfect. Can I change the delivery address?", "is_bot": False, "delay": 1200},
                        {"id": 5, "text": "Certainly. What is the new address?", "is_bot": True, "delay": 1200},
                        {"id": 6, "text": "123 New Street, Los Angeles, CA 90210", "is_bot": False, "delay": 1300},
                        {"id": 7, "text": "All set. I’ve updated the address. Anything else I can assist with today?", "is_bot": True, "delay": 1400},
                        {"id": 8, "text": "No, that’s all. Thank you, Nancy.", "is_bot": False, "delay": 1100},
                        {"id": 9, "text": "You’re welcome. Happy to help.", "is_bot": True, "delay": 1400},
                    ],
                },
                {
                    "agent_name": "Jack",
                    "job_title": "Banking Assistance Agent",
                    "conversation": [
                        {"id": 1, "text": "Hi. I believe the interest on my credit card was calculated incorrectly.", "is_bot": False, "delay": 1100},
                        {"id": 2, "text": "Hello, this is Jack. I can clarify. Interest is calculated daily on the carried balance and summed for the billing cycle.", "is_bot": True, "delay": 1600},
                        {"id": 3, "text": "For example: 8 days at AED 5,000 and 22 days at AED 2,000 produce a blended amount based on each daily balance.", "is_bot": True, "delay": 1700},
                        {"id": 4, "text": "That helps. Could you send me the detailed breakdown?", "is_bot": False, "delay": 1200},
                        {"id": 5, "text": "Of course. I’ve sent a statement breakdown to your registered email. Would you like assistance setting up autopay?", "is_bot": True, "delay": 1400},
                        {"id": 6, "text": "No, that’s fine for now. Thanks, Jack.", "is_bot": False, "delay": 1200},
                        {"id": 7, "text": "Anytime. If anything else comes up, I’m here to help.", "is_bot": True, "delay": 1400},
                    ],
                },
                {
                    "agent_name": "Suzan",
                    "job_title": "Realtor Agent",
                    "conversation": [
                        {"id": 1, "text": "Hello Suzan. Are there any units available in Dubai Marina?", "is_bot": False, "delay": 1100},
                        {"id": 2, "text": "Hello, I'm happy to help. Yes, here’s what’s currently available:", "is_bot": True, "delay": 1400},
                        {"id": 3, "text": "2BR, 1,320 sqft, Marina view — AED 2.1M.\n3BR, 2,450 sqft, high floor — AED 4.5M.\n1BR, 820 sqft, furnished — AED 1.35M.", "is_bot": True, "delay": 1700},
                        {"id": 4, "text": "Would you like a sales specialist to contact you?", "is_bot": True, "delay": 1400},
                        {"id": 5, "text": "Not yet. Could I see some images?", "is_bot": False, "delay": 1300},
                        {
                            "id": 6,
                            "text": "Certainly. Sharing a few photos:",
                            "is_bot": True,
                            "delay": 1600,
                            "images": [
                                "https://images.unsplash.com/photo-1505691723518-36a5ac3b2b8f?w=600&q=80&auto=format&fit=crop",
                                "https://images.unsplash.com/photo-1523217582562-09d0def993a6?w=600&q=80&auto=format&fit=crop",
                                "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?w=600&q=80&auto=format&fit=crop",
                            ],
                        },
                        {"id": 7, "text": "Looks good. Please have someone contact me at +971 50 123 4567. Name: Ahmed.", "is_bot": False, "delay": 1500},
                        {"id": 8, "text": "Done. I’ve scheduled a call for today at 4:30 PM. Our specialist Sara will contact you shortly.", "is_bot": True, "delay": 1600},
                        {"id": 9, "text": "Great, thank you.", "is_bot": False, "delay": 1200},
                        {"id": 10, "text": "You’re welcome. I’m here if you need anything else.", "is_bot": True, "delay": 1400},
                    ],
                },
            ],
        },
    }

    features = {
        "section_title_prefix": "Everything You Need for",
        "section_title_highlight": "Perfect Support",
        "section_subtitle": "Our comprehensive platform combines cutting-edge AI with intuitive design to deliver exceptional customer service experiences.",
        "tabs": [
            {
                "key": "setup",
                "label": "Easy Setup",
                "icon": "settings",
                "title": "From sign-up to live in minutes",
                "promo": "Create your account, connect your business data, configure the agent, and share the chat link instantly—everywhere.",
                "bullets": [
                    {"icon": "check-circle-2", "label": "Guided, no-code onboarding"},
                    {"icon": "settings", "label": "Business profile & preferences"},
                    {"icon": "link", "label": "Instant chat portal link"},
                    {"icon": "globe", "label": "Omnichannel social & WhatsApp"},
                ],
            },
            {
                "key": "agents",
                "label": "Multiple Agents",
                "icon": "users",
                "title": "Scale with specialized, brand-aligned agents",
                "promo": "Spin up dedicated agents for sales, support, onboarding, and more. Each agent carries your tone and executes actions confidently.",
                "bullets": [
                    {"icon": "check-circle-2", "label": "Customizable personas and tone"},
                    {"icon": "zap", "label": "Actionable workflows and tools"},
                    {"icon": "clock", "label": "24/7 availability across timezones"},
                    {"icon": "shield", "label": "Modern, fine-tuned LLM models"},
                ],
            },
            {
                "key": "kb",
                "label": "Knowledge Base",
                "icon": "book-open",
                "title": "Instant business intelligence for your agents",
                "promo": "Upload SOPs and docs, sync help centers and sites. Retrieval-augmented generation gives agents precise, grounded answers from your materials.",
                "bullets": [
                    {"icon": "link", "label": "One-click uploads & syncs"},
                    {"icon": "check-circle-2", "label": "Automatic chunking & embeddings"},
                    {"icon": "shield", "label": "Citations & guardrails for trust"},
                    {"icon": "zap", "label": "Realtime refresh & invalidation"},
                ],
            },
            {
                "key": "crm",
                "label": "Flexible CRM",
                "icon": "layers",
                "title": "Build the CRM your workflows deserve",
                "promo": "Compose a flexible CRM—add or remove tabs, define data parameters to collect, track customer profiles, and manage insights your way.",
                "bullets": [
                    {"icon": "layers", "label": "Dynamic tabs & fields"},
                    {"icon": "users", "label": "Customer profiles & segments"},
                    {"icon": "bar-chart-3", "label": "Insight management & exports"},
                    {"icon": "link", "label": "Integrations: HubSpot, and more"},
                ],
            },
            {
                "key": "integrations",
                "label": "Integrations",
                "icon": "link",
                "title": "Connect your stack in minutes",
                "promo": "Plug into tools your team already uses — CRM, support, messaging, and automation platforms.",
                "bullets": [],
            },
            {
                "key": "dashboard",
                "label": "Operations",
                "icon": "layout-dashboard",
                "title": "Run operations with clarity and control",
                "promo": "Governance and operations: queue visibility, live sessions, agent routing, and scheduled reports—everything leaders need to steer performance.",
                "bullets": [
                    {"icon": "message-square", "label": "Live queue & session views"},
                    {"icon": "users", "label": "Routing & assignment rules"},
                    {"icon": "bar-chart-3", "label": "Scheduled reports & alerts"},
                    {"icon": "shield", "label": "Roles, audit logs, and SSO"},
                ],
            },
            {
                "key": "billing",
                "label": "Billing",
                "icon": "credit-card",
                "title": "Pay for outcomes—not idle seats",
                "promo": "Activate agents when you need them. Use wage-based billing, predictable packages, and smart notifications to stay on budget.",
                "bullets": [
                    {"icon": "credit-card", "label": "Wage-based activation"},
                    {"icon": "zap", "label": "Usage packages"},
                    {"icon": "clock", "label": "Credit depletion alerts"},
                    {"icon": "shield", "label": "Spend caps & schedules"},
                ],
            },
        ],
        "integrations": [
            {"name": "HubSpot", "slug": "hubspot"},
            {"name": "Slack", "slug": "slack"},
            {"name": "Zapier", "slug": "zapier"},
            {"name": "WhatsApp", "slug": "whatsapp"},
            {"name": "Gmail", "slug": "gmail"},
            {"name": "Shopify", "slug": "shopify"},
            {"name": "Zendesk", "slug": "zendesk"},
            {"name": "Intercom", "slug": "intercom"},
        ],
    }

    testimonials = {
        "title_prefix": "Customers who",
        "title_highlight": "build differently",
        "subtitle": "Real teams, real results. Built with speed, reliability, and brand in mind.",
        "items": [
            {
                "quote": "Pocket helped us cut first response time from hours to minutes. Our customers finally feel heard instantly.",
                "author": "Sofia Martinez",
                "role": "Director of Customer Experience, Luma",
            },
            {
                "quote": "The AI assistant handles 80% of inquiries, freeing our agents to focus on complex cases and high-value work.",
                "author": "Elliot Rhodes",
                "role": "Support Operations Lead, Northbeam",
            },
        ],
    }

    trusted_by = {
        "title": "Trusted by",
        "brands": [
            {"name": name, "slug": slug}
            for name, slug in [
                ("Google", "google"),
                ("Apple", "apple"),
                ("Stripe", "stripe"),
                ("Shopify", "shopify"),
                ("Netflix", "netflix"),
                ("Uber", "uber"),
                ("Airbnb", "airbnb"),
                ("Slack", "slack"),
                ("Spotify", "spotify"),
                ("Meta", "meta"),
                ("PayPal", "paypal"),
                ("Samsung", "samsung"),
                ("TikTok", "tiktok"),
            ]
        ]
        * 2,
    }

    mobile_app = _mobile_app_section()

    pricing = {
        "flexible": "Flexible pricing",
        "choose": "Choose what fits ",
        "motion": "your motion",
        "tabs": [
            {"key": "wage", "label": "Wage based"},
            {"key": "packages", "label": "Packages"},
            {"key": "self", "label": "One-time setup (self-hosted)"},
        ],
        "subheader": {
            "packages": "Simple plans for any stage. Switch billing to see savings with yearly.",
            "wage": "Prepay a minimum credit (wage) to activate agents and features, then scale usage.",
            "self": "Deploy on your own infrastructure with your database, custom integrations, and add-ons.",
        },
        "billing_cycle": {
            "monthly": "Monthly",
            "yearly": "Yearly",
            "suffix_monthly": "/mo",
            "suffix_yearly": "/mo · billed yearly",
            "trial": "7-day free trial",
            "get_started": "Get started",
            "add_credit": "Add credit",
            "flexible": "Flexible",
            "min_credit": "Minimum credit (wage)",
            "activates": "Activates agents and unlocks features. Credit is consumed by usage.",
            "users_choice_badge": "Users’ Choice",
        },
        "packages": [
            {
                "tier": "Plus",
                "description": "For frontliners who talk to customers daily — fits any industry.",
                "monthly": 29,
                "yearly": 24,
                "features": [
                    "1 agent",
                    "Branded chat portal",
                    "Core knowledge base",
                    "Inbox + basic analytics",
                    "Email transcripts",
                ],
            },
            {
                "tier": "Pro",
                "badge": "Users’ Choice",
                "description": "For SMBs — multiple agents and advanced workflows.",
                "monthly": 89,
                "yearly": 69,
                "features": [
                    "Up to 3 agents",
                    "Advanced knowledge base + citations",
                    "Workflows and tools (actions)",
                    "CRM profiles + segments",
                    "Reports & scheduled alerts",
                ],
            },
            {
                "tier": "Enterprise",
                "description": "For large teams — security, scale, and customization.",
                "monthly": 249,
                "yearly": 199,
                "features": [
                    "Unlimited agents",
                    "SSO, roles & audit logs",
                    "Custom routing & priority queues",
                    "Integrations (HubSpot, webhooks)",
                    "Premium support & SLA",
                ],
            },
        ],
        "wage_plans": [
            {
                "id": "starter",
                "label": "Starter",
                "min_credit": 50,
                "bullets": [
                    "1 agent active",
                    "Up to 3k assisted messages",
                    "All core features included",
                    "Community support",
                ],
            },
            {
                "id": "growth",
                "label": "Growth",
                "min_credit": 200,
                "bullets": [
                    "Up to 3 agents active",
                    "Up to 15k assisted messages",
                    "Advanced KB + citations",
                    "Priority email support",
                ],
            },
            {
                "id": "scale",
                "label": "Scale",
                "min_credit": 1000,
                "bullets": [
                    "Unlimited agents active",
                    "Up to 100k assisted messages",
                    "Full platform + integrations",
                    "Premium support & SLA",
                ],
            },
        ],
        "self": {
            "title": "Self-hosted deployment",
            "bullets": [
                "On-prem or private cloud",
                "Your database and VPC",
                "Custom integrations & add-ons",
                "SSO, roles & audit logs",
                "Implementation support",
            ],
            "cta_quote": "Get a quote",
            "cta_sales": "Talk to sales",
        },
    }

    faq = {
        "title_prefix": "Frequently asked",
        "title_highlight": "questions",
        "items": [
            {
                "question": "How do agents learn our business?",
                "answer": "Sync your docs and sites or upload files. Retrieval with citations keeps answers grounded and up to date.",
            },
            {
                "question": "What happens if the AI doesn’t know?",
                "answer": "It can request clarification, route to a human, or create a follow-up with full context—your choice.",
            },
            {
                "question": "Can I customize tone and behavior?",
                "answer": "Yes. Configure personas, guardrails, tools, and workflows per agent, then test in a live sandbox.",
            },
            {
                "question": "How does billing work?",
                "answer": "Choose packages or prepay credit (wage) to activate agents. Switch to yearly for savings.",
            },
            {
                "question": "Is my data secure?",
                "answer": "Bank-level encryption, roles and audit logs. Optional SSO and data residency controls.",
            },
        ],
    }

    get_token(request)

    context = {
        "page": {
            "hero": hero,
            "sections": {
                "trusted_by": trusted_by,
                "features": features,
                "testimonials": testimonials,
                "pricing": pricing,
                "faq": faq,
                "mobile_app": mobile_app,
            },
        },
    }
    return render(request, "frontend/index.html", context)


def register(request: HttpRequest) -> HttpResponse:
    """Registration page – initial step mirrored from the React experience."""

    industries = [
        "E-commerce & Retail",
        "SaaS & Software",
        "Financial Services",
        "Healthcare & Life Sciences",
        "Education",
        "Hospitality & Travel",
        "Manufacturing",
        "Logistics & Transportation",
        "Real Estate",
        "Media & Entertainment",
        "Telecommunications",
        "Energy & Utilities",
        "Nonprofit & NGOs",
        "Professional Services",
        "Consumer Services",
    ]

    industry_to_lob_key = {
        "E-commerce & Retail": "E-commerce",
        "SaaS & Software": "SaaS",
        "Financial Services": "Finance",
        "Healthcare & Life Sciences": "Healthcare",
        "Education": "Education",
        "Hospitality & Travel": "Hospitality",
        "Manufacturing": "Manufacturing",
        "Logistics & Transportation": "Logistics",
        "Real Estate": "Real Estate",
        "Media & Entertainment": "Media & Entertainment",
        "Telecommunications": "Telecommunications",
        "Energy & Utilities": "Energy & Utilities",
        "Nonprofit & NGOs": "Nonprofit & NGOs",
        "Professional Services": "Professional Services",
        "Consumer Services": "Consumer Services",
        "Other": "Other",
    }

    line_of_business_map = {
        "E-commerce": [
            "Apparel",
            "Electronics",
            "Beauty & Personal Care",
            "Home & Kitchen",
            "Sports & Outdoors",
            "Groceries",
            "Digital Goods",
            "Handmade & Crafts",
            "Automotive Accessories",
        ],
        "SaaS": [
            "CRM",
            "Marketing Automation",
            "Analytics",
            "Project Management",
            "Customer Support",
            "Developer Tools",
            "Productivity",
            "Security",
            "Billing/Subscriptions",
            "Auth/Identity",
            "Observability",
            "Data Platform",
        ],
        "Finance": [
            "Banking",
            "Lending",
            "Payments",
            "Wealth Management",
            "Insurance",
            "Accounting",
            "Crypto/Blockchain",
            "Trading Platforms",
        ],
        "Healthcare": [
            "Clinics",
            "Telemedicine",
            "Pharmacy",
            "Diagnostics",
            "Medical Devices",
            "Wellness",
            "Electronic Health Records",
        ],
        "Education": [
            "K-12",
            "Higher Education",
            "EdTech Platform",
            "Corporate Training",
            "Test Prep",
            "Language Learning",
            "Tutoring & Coaching",
        ],
        "Hospitality": [
            "Hotels",
            "Restaurants",
            "Catering",
            "Travel & Tours",
            "Venues & Events",
            "Short-Term Rentals",
        ],
        "Manufacturing": [
            "OEM Production",
            "Contract Manufacturing",
            "CNC Machining",
            "Injection Molding",
            "3D Printing",
            "PCB Assembly",
            "Quality Assurance",
            "Procurement & Supply",
            "Packaging",
            "Maintenance (MRO)",
        ],
        "Logistics": [
            "Freight Forwarding",
            "Last-Mile Delivery",
            "Warehousing & Fulfillment",
            "Cold Chain",
            "Customs Brokerage",
            "Fleet Management",
            "Courier",
            "LTL/FTL Trucking",
            "Air Cargo",
            "Ocean Freight",
        ],
        "Real Estate": [
            "Residential Sales",
            "Commercial Leasing",
            "Property Management",
            "Valuation & Appraisal",
            "Real Estate Development",
            "Facility Management",
            "Co-working",
            "Mortgage Brokerage",
            "Title & Escrow",
            "Short-Term Rentals",
        ],
        "Media & Entertainment": [
            "Streaming Subscriptions",
            "OTT Platform",
            "Content Production",
            "Post-Production",
            "Music Publishing",
            "Game Development",
            "Live Events",
            "Digital Advertising",
            "Influencer Campaigns",
            "Licensing & Syndication",
        ],
        "Telecommunications": [
            "Mobile Voice",
            "Fixed Broadband",
            "VoIP",
            "IoT Connectivity",
            "Cloud PBX",
            "SIP Trunking",
            "Managed Networks",
            "5G Solutions",
            "Fiber to the Home",
            "Data Center Colocation",
        ],
        "Energy & Utilities": [
            "Electricity Supply",
            "Natural Gas Supply",
            "Renewable Generation",
            "Solar Installation",
            "Energy Storage",
            "Smart Metering",
            "Demand Response",
            "Energy Trading",
            "EV Charging",
            "Utility Billing",
        ],
        "Nonprofit & NGOs": [
            "Fundraising",
            "Grant Management",
            "Program Delivery",
            "Volunteer Management",
            "Advocacy & Outreach",
            "Education Programs",
            "Healthcare Missions",
            "Disaster Relief",
            "Community Development",
            "Monitoring & Evaluation",
        ],
        "Professional Services": [
            "Consulting",
            "Legal Advisory",
            "Tax & Audit",
            "Accounting",
            "Architecture",
            "Engineering",
            "Design & Creative",
            "Recruitment",
            "IT Consulting",
            "Managed IT",
        ],
        "Consumer Services": [
            "Home Cleaning",
            "Appliance Repair",
            "Beauty & Wellness",
            "Fitness & Training",
            "Tutoring",
            "Pet Care",
            "Event Planning",
            "Photography",
            "Home Renovation",
            "Moving & Storage",
        ],
        "Other": [
            "Consulting",
            "Custom Development",
            "Training & Enablement",
            "Support & Success",
        ],
    }

    countries = [
        {"value": "United States", "flag": "🇺🇸"},
        {"value": "United Kingdom", "flag": "🇬🇧"},
        {"value": "United Arab Emirates", "flag": "🇦🇪"},
        {"value": "Saudi Arabia", "flag": "🇸🇦"},
        {"value": "Egypt", "flag": "🇪🇬"},
        {"value": "France", "flag": "🇫🇷"},
        {"value": "Germany", "flag": "🇩🇪"},
        {"value": "Spain", "flag": "🇪🇸"},
        {"value": "Italy", "flag": "🇮🇹"},
        {"value": "Other", "flag": "🌐"},
    ]

    agent_roles = [
        "Customer Support Agent",
        "Support Specialist",
        "Customer Success Representative",
        "Sales Support Agent",
        "Front Desk Representative",
        "Account Manager",
        "Helpdesk Agent",
    ]

    agent_tones = [
        "Friendly",
        "Professional",
        "Casual",
        "Formal",
        "Empathetic",
        "Playful",
    ]

    agent_traits = [
        "Concise",
        "Detailed",
        "Curious",
        "Patient",
        "Proactive",
        "Direct",
        "Creative",
    ]

    agent_escalations = [
        "Never",
        "On fallback",
        "On negative sentiment",
        "On high value",
        "Always",
    ]

    catalog_entity = "Products & Services"
    uploads_materials = [
        {
            "id": "vision",
            "label": "Vision",
            "field": "uploadsVision",
            "pending_field": "uploadsVisionUrl",
            "placeholder": "https://your-site.com/vision",
        },
        {
            "id": "mission",
            "label": "Mission",
            "field": "uploadsMission",
            "pending_field": "uploadsMissionUrl",
            "placeholder": "https://your-site.com/mission",
        },
        {
            "id": "catalog",
            "label": f"{catalog_entity} Catalog",
            "field": "uploadsCatalog",
            "pending_field": "uploadsCatalogUrl",
            "placeholder": "https://your-site.com/catalog",
        },
        {
            "id": "faqs",
            "label": "FAQs",
            "field": "uploadsFaqs",
            "pending_field": "uploadsFaqsUrl",
            "placeholder": "https://your-site.com/faqs",
        },
        {
            "id": "kb",
            "label": "Knowledge Base",
            "field": "uploadsKb",
            "pending_field": "uploadsKbUrl",
            "placeholder": "https://help.your-site.com",
        },
        {
            "id": "sops",
            "label": "SOPs",
            "field": "uploadsSops",
            "pending_field": "uploadsSopsUrl",
            "placeholder": "https://drive.google.com/...",
        },
        {
            "id": "tc",
            "label": "T&C",
            "field": "uploadsTc",
            "pending_field": "uploadsTcUrl",
            "placeholder": "https://your-site.com/terms",
        },
    ]

    paperclip_icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05 12.12 20.37a5 5 0 1 1-7.07-7.07L14.37 4.93a3.5 3.5 0 1 1 4.95 4.95L10.12 19.08"/></svg>"""

    context = {
        "page": {
            "title_prefix": "Create your",
            "title_highlight": "Account",
            "typed_subtitle": "Your clients, served better.",
            "step_indicator": "form",
            "divider_label": "OR",
            "google_label": "Continue with Google",
            "cta_label": "Create Account",
            "cta_loading": "Creating...",
            "google_icon": """<svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36 16.8 36 11 30.2 11 23S16.8 10 24 10c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 12 2 2 12 2 24s10 22 22 22 22-10 22-22c0-1.3-.1-2.5-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.4 16.3 18.8 14 24 14c3.8 0 7.2 1.4 9.8 3.7l5.7-5.7C35.5 4.1 30 2 24 2 15.3 2 7.8 7.1 4.2 14.1l2.1.6z"/><path fill="#4CAF50" d="M24 46c6 0 11.5-2.2 15.6-5.8l-7.2-5.9C30.7 35.7 27.6 37 24 37c-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-1.3 3.8-4.8 6.5-9.3 6.5-5.2 0-9.7-3.3-11.3-7.9l-6.6 5.1C9.7 40.9 16.3 46 24 46c12 0 22-10 22-22 0-1.3-.1-2.5-.4-3.5z"/></svg>""",
            "fields": {
                "first_name": {
                    "label": "First name",
                    "placeholder": "John",
                    "error_required": "First name is required",
                },
                "email": {
                    "label": "Email",
                    "placeholder": "you@company.com",
                    "error_invalid": "Please enter a valid email",
                },
                "password": {
                    "label": "Password",
                    "placeholder": "••••••••",
                    "error_min": "Password must be at least 8 characters",
                },
                "confirm_password": {
                    "label": "Confirm password",
                    "placeholder": "••••••••",
                    "error_match": "Passwords do not match",
                },
        },
            "have_account_label": "Already have an account?",
            "login_label": "Log in",
            "business": {
                "title_prefix": "Business Profile",
                "title_highlight": "Setup",
                "badge": "Step 2 · Business profile",
                "subtitle": "Help us understand your business",
                "fields": {
                    "business_name": {
                        "label": "Business name",
                        "placeholder": "Acme Inc.",
                        "error_required": "Business name is required",
                    },
                    "industry": {
                        "label": "Industry",
                        "placeholder": "Search industry",
                        "search_placeholder": "Search industry...",
                        "empty": "Search industry",
                        "no_results": "No industry found.",
                    },
                    "line_of_business": {
                        "label": "Industry niches",
                        "placeholder": "Select niches",
                        "empty": "Select industry first",
                        "search_placeholder": "Search niches",
                        "custom_placeholder": "Add custom",
                        "no_results": "No niches found.",
                    },
                    "country": {
                        "label": "Country",
                        "placeholder": "Select country",
                    },
                    "website": {
                        "label": "Website",
                        "optional": "(optional)",
                        "placeholder": "https://example.com",
                    },
                },
                "buttons": {
                    "back": "Back",
                    "next": "Next",
                    "loading": "Saving...",
                    "add": "Add",
                    "done": "Done",
                },
            },
            "agent": {
                "title_prefix": "Agent",
                "title_highlight": "Setup",
                "subtitle": "Set up your agent basics",
                "fields": {
                    "name": {
                        "label": "Agent name",
                        "placeholder": "e.g., Nancy",
                        "error_required": "Please enter an agent name",
                    },
                    "role": {
                        "label": "Role",
                        "placeholder": "Select a role",
                        "error_required": "Please select a role",
                        "options": agent_roles,
                    },
                    "tone": {
                        "label": "Tone",
                        "placeholder": "Select tone",
                        "error_required": "Please choose a tone",
                        "options": agent_tones,
                    },
                    "traits": {
                        "label": "Traits",
                        "hint": "Pick as many as you like",
                        "options": agent_traits,
                    },
                    "escalation": {
                        "label": "Escalation rule",
                        "placeholder": "Choose escalation rule",
                        "error_required": "Please choose when to escalate",
                        "options": agent_escalations,
                    },
                },
                "buttons": {
                    "back": "Back",
                    "next": "Next",
                    "loading": "Saving...",
                },
            },
            "uploads": {
                "title_prefix": "Knowledge",
                "title_highlight": "Uploads",
                "subtitle": "Add links to your key docs so your agent gets smart fast. You can skip this and do it later.",
                "materials_card": {
                    "title": "Which materials do you want to attach?",
                    "description": "Select the knowledge sources you want to add now. You can always come back later.",
                },
                "materials": uploads_materials,
                "empty_state": "Select at least one material above to start adding links.",
                "attach_label": "Attach",
                "attach_icon": paperclip_icon,
                "count_suffix": "added",
                "errors": {
                    "invalid_url": "Enter a valid URL (https://example.com)",
                    "duplicate_url": "This link is already attached",
                    "materials": "Select at least one material to continue",
                    "links": "Add at least one link for each selected source",
                    "general": "We couldn't save your uploads. Try again.",
                    "dependencies": "Complete the earlier steps before finishing registration.",
                },
                "buttons": {
                    "back": "Back",
                    "skip": "Upload later",
                    "finish": "Finish",
                    "loading": "Finishing…",
                },
                # where to go after uploads resolves (default in JS if no custom handler prevents)
                "redirect_after": "/dashboard/",
            },
        },
        "business_data": {
            "industries": industries,
            "industry_map": industry_to_lob_key,
            "line_of_business_map": line_of_business_map,
            "countries": countries,
            "max_badges": 2,
        },
        "mobile_app": _mobile_app_section(),
    }
    return render(request, "frontend/register.html", context)


def privacy_policy(request: HttpRequest) -> HttpResponse:
    today = date.today().strftime("%B %d, %Y")
    sections = [
        _legal_section(
            "overview",
            "1. Overview",
            paragraphs=[
                'Pocket AI Support ("we", "us", "our") provides AI-powered customer service tools including multi-agent chat, retrieval-based knowledge, and CRM capabilities. This Privacy Policy explains how we collect, use, share, and protect your information when you use our website, products, and services (the "Services").',
                "By using the Services, you agree to this Policy. If you do not agree, please discontinue use.",
            ],
        ),
        _legal_section(
            "collection",
            "2. Information We Collect",
            bullets=[
                "Account and Profile: name, email, role, preferences, and authentication identifiers.",
                "Business Data: uploaded SOPs, documents, websites, and help-center sources you connect for retrieval.",
                "Communications: chat transcripts, feedback, and logs from agent and user interactions.",
                "Usage and Device: product usage metrics, approximate location, device and browser details, cookies.",
                "Integrations: third-party identifiers and metadata when you connect services such as HubSpot, Slack, or WhatsApp.",
            ],
        ),
        _legal_section(
            "use",
            "3. How We Use Information",
            bullets=[
                "Provide, operate, and improve the Services and features.",
                "Configure and personalise AI agents to match your brand.",
                "Power retrieval-augmented answers with citations from your data.",
                "Monitor quality, performance, security, and abuse prevention.",
                "Support, troubleshoot, and communicate about the Services.",
                "Comply with legal obligations and enforce terms.",
            ],
        ),
        _legal_section(
            "ai",
            "4. AI Processing and Training",
            paragraphs=[
                "We use large language models and tooling to enable agent capabilities. Unless you opt in to data sharing for model improvement, we do not use your private business content to train foundation models. We may use aggregated, de-identified analytics to improve reliability and safety.",
                "Retrieval sources and citations are stored to provide grounded responses and auditability. You can refresh or remove sources at any time from your account.",
            ],
        ),
        _legal_section(
            "sharing",
            "5. How We Share Information",
            bullets=[
                "Vendors and sub-processors under contractual safeguards.",
                "Integrations you enable, limited to the data required for that service.",
                "Legal and safety requests where disclosure is required to comply with law or protect rights.",
                "Business transfers as part of a merger, acquisition, or asset sale with notice where required.",
            ],
        ),
        _legal_section(
            "intl",
            "6. International Transfers",
            paragraphs=[
                "Your information may be processed in jurisdictions other than your own. We apply safeguards such as standard contractual clauses to protect cross-border transfers in line with applicable law.",
            ],
        ),
        _legal_section(
            "retention",
            "7. Data Retention",
            paragraphs=[
                "We retain information as long as needed to deliver the Services, meet legal obligations, resolve disputes, and enforce agreements. You may request deletion of certain data from within your account or by contacting us.",
            ],
        ),
        _legal_section(
            "rights",
            "8. Your Rights and Choices",
            bullets=[
                "Access, correct, or delete certain personal information.",
                "Export data where applicable.",
                "Opt out of marketing communications.",
                "Control cookies via browser settings.",
                "Disable or remove integrations at any time.",
            ],
            paragraphs=[
                "Regional rights (for example GDPR or CCPA) may apply depending on your location and role. We honour requests in accordance with applicable laws.",
            ],
        ),
        _legal_section(
            "security",
            "9. Security",
            paragraphs=[
                "We implement industry-standard security measures, including encryption in transit and at rest, role-based access controls, and audit logs for enterprise plans. No method of transmission or storage is 100% secure; please use strong credentials and enable SSO where available.",
            ],
        ),
        _legal_section(
            "children",
            "10. Children's Privacy",
            paragraphs=[
                "Our Services are not directed to children under 13 (or the age of digital consent in your region). We do not knowingly collect data from children. If you believe a child has provided personal information, contact us to request deletion.",
            ],
        ),
        _legal_section(
            "changes",
            "11. Changes to this Policy",
            paragraphs=[
                "We may update this Policy from time to time. Material changes will be announced via the website or email. Your continued use of the Services after an update constitutes acceptance of the revised Policy.",
            ],
        ),
        _legal_section(
            "contact",
            "12. Contact Us",
            paragraphs=[
                "For privacy inquiries, requests, or complaints, contact our team at privacy@pocket.ai.",
            ],
        ),
    ]

    context = {
        "page": {
            "title": "Privacy Policy",
            "last_updated": today,
            "sections": sections,
            "include_mobile_promo": True,
            "sections_mobile": _mobile_app_section(),
        },
    }
    return render(request, "frontend/legal/page.html", context)


def terms_of_service(request: HttpRequest) -> HttpResponse:
    today = date.today().strftime("%B %d, %Y")
    sections = [
        _legal_section(
            "intro",
            "1. Introduction",
            paragraphs=[
                'These Terms of Service ("Terms") govern your access to and use of Pocket AI Support\'s website, products, and services (the "Services"). By accessing or using the Services, you agree to be bound by these Terms. If you do not agree, do not use the Services.',
            ],
        ),
        _legal_section(
            "eligibility",
            "2. Eligibility",
            paragraphs=[
                "You must be at least the age of majority in your jurisdiction and have the authority to bind your organisation to these Terms. You represent and warrant that you will use the Services only for lawful purposes.",
            ],
        ),
        _legal_section(
            "account",
            "3. Account Registration and Security",
            bullets=[
                "Provide accurate and complete information when creating an account.",
                "Maintain the security of your credentials and notify us of any breach.",
                "You are responsible for all activities under your account.",
            ],
        ),
        _legal_section(
            "acceptable-use",
            "4. Acceptable Use",
            bullets=[
                "Do not misuse the Services, attempt unauthorised access, or disrupt operations.",
                "Do not upload unlawful, infringing, or harmful content.",
                "Respect usage limits, fair use, and applicable third-party terms.",
                "Do not use outputs to violate rights, privacy, or applicable law.",
            ],
        ),
        _legal_section(
            "customer-data",
            "5. Customer Data and Privacy",
            paragraphs=[
                '"Customer Data" means content you submit to the Services (for example SOPs, documents, websites, chat transcripts, and configuration). You retain ownership of Customer Data. We process Customer Data to provide and improve the Services in line with our Privacy Policy. You are responsible for obtaining all rights and consents required to submit Customer Data.',
            ],
        ),
        _legal_section(
            "ai",
            "6. AI Outputs and Limitations",
            paragraphs=[
                "AI-generated outputs may be probabilistic and may contain errors. Use human oversight where material. Outputs are provided \"as is\" without warranties. Do not rely on outputs for legal, medical, financial, or other professional advice without validation.",
            ],
        ),
        _legal_section(
            "ip",
            "7. Intellectual Property",
            bullets=[
                "We and our licensors retain all rights, title, and interest in the Services, including software, models, and design elements.",
                "You are granted a limited, non-exclusive, non-transferable licence to use the Services in accordance with these Terms.",
                "Feedback you provide may be used by us without obligation.",
            ],
        ),
        _legal_section(
            "billing",
            "8. Billing and Plans",
            paragraphs=[
                "Pricing is described on our website (packages, wage-based credits, and self-hosted options). Fees are non-refundable unless required by law. We may change prices with prior notice. Usage limits and overage policies may apply.",
            ],
        ),
        _legal_section(
            "integrations",
            "9. Integrations and Third-Party Services",
            paragraphs=[
                "When you connect third-party tools (such as HubSpot, Slack, or WhatsApp), you authorise us to exchange necessary data with those services. Third-party terms govern your use of their products. We are not responsible for third-party services.",
            ],
        ),
        _legal_section(
            "security",
            "10. Security",
            paragraphs=[
                "We implement industry-standard measures to protect the Services. No system is completely secure. You are responsible for securing your accounts, endpoints, and integration credentials.",
            ],
        ),
        _legal_section(
            "term",
            "11. Term and Termination",
            bullets=[
                "We may suspend or terminate access for violations of these Terms.",
                "You may stop using the Services at any time.",
                "Upon termination, your right to access the Services ends; certain provisions survive.",
            ],
        ),
        _legal_section(
            "warranty",
            "12. Disclaimers",
            paragraphs=[
                "THE SERVICES ARE PROVIDED \"AS IS\" AND \"AS AVAILABLE\" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE SERVICES WILL BE ERROR-FREE OR UNINTERRUPTED.",
            ],
        ),
        _legal_section(
            "liability",
            "13. Limitation of Liability",
            paragraphs=[
                "TO THE MAXIMUM EXTENT PERMITTED BY LAW, NEITHER WE NOR OUR LICENSORS SHALL BE LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS, REVENUE, DATA, OR USE. OUR AGGREGATE LIABILITY WILL NOT EXCEED THE FEES PAID BY YOU FOR THE SERVICES IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM.",
            ],
        ),
        _legal_section(
            "indemnity",
            "14. Indemnification",
            paragraphs=[
                "You will defend, indemnify, and hold harmless Pocket AI Support and its affiliates from and against claims arising out of your use of the Services or violation of these Terms, including Customer Data you provide and your use of AI outputs.",
            ],
        ),
        _legal_section(
            "governing-law",
            "15. Governing Law and Dispute Resolution",
            paragraphs=[
                "These Terms are governed by the laws of the jurisdiction where Pocket AI Support is organised, without regard to conflict of law principles. Disputes will be resolved through good-faith negotiations; if unresolved, they shall be brought in competent courts of that jurisdiction.",
            ],
        ),
        _legal_section(
            "changes",
            "16. Changes to these Terms",
            paragraphs=[
                "We may modify these Terms from time to time. Material changes will be communicated via the website or email. Your continued use of the Services after changes become effective constitutes acceptance.",
            ],
        ),
        _legal_section(
            "contact",
            "17. Contact",
            paragraphs=[
                "Questions about these Terms? Contact our team at legal@pocket.ai.",
            ],
        ),
    ]

    context = {
        "page": {
            "title": "Terms of Service",
            "last_updated": today,
            "sections": sections,
            "include_mobile_promo": True,
            "sections_mobile": _mobile_app_section(),
        },
    }
    return render(request, "frontend/legal/page.html", context)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    dashboard_metrics: List[Dict[str, object]] = []
    conversation_groups: List[Dict[str, object]] = []
    ai_pulse_items: List[Dict[str, object]] = []
    agent_snapshot_items: List[Dict[str, object]] = []
    total_all = sum(len(group.get("entries", [])) for group in conversation_groups)
    total_ai = sum(
        1
        for group in conversation_groups
        for item in group.get("entries", [])
        if item.get("handled_by") == "ai"
    )
    total_human = sum(
        1
        for group in conversation_groups
        for item in group.get("entries", [])
        if item.get("handled_by") == "human"
    )
    context = {
        "user_name": user_name,
        "dashboard_loading": False,
        "dashboard_metrics": dashboard_metrics,
        "dashboard_metrics_empty_message": "Metrics will appear once you start receiving conversations.",
        "dashboard_alert_message": "No escalations yet — monitoring continuously.",
        "dashboard_conversation_groups": conversation_groups,
        "dashboard_conversation_counts": {
            "all": total_all,
            "ai": total_ai,
            "human": total_human,
        },
        "dashboard_conversations_empty_message": "Connect your support channels to start streaming conversations here.",
        "dashboard_ai_pulse": ai_pulse_items,
        "dashboard_ai_pulse_empty_message": "Metrics will appear once cases begin flowing in.",
        "dashboard_agent_snapshot": agent_snapshot_items,
        "dashboard_agent_snapshot_empty_message": "Invite your team to see performance insights here.",
    }
    return render(request, "frontend/dashboard.html", context)


@login_required
def dashboard_customers(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {"label": "New customers", "value": None, "helper": "No data to measure yet"},
        {"label": "Active customers", "value": None, "helper": "No customers yet"},
        {"label": "Open cases", "value": None, "helper": "Create cases to populate data"},
    ]
    customers: list[dict[str, object]] = []
    total_customers = 0
    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    business = None
    if request.user.is_authenticated:
        business = request.user.business_profiles.order_by("-created_at").first()

    if business:
        try:
            result = list_customers(business_profile=business, limit=50)
            total_customers = result.total_count
            for item in result.items:
                name = item.display_name or "Customer"
                tokens = [token for token in name.split() if token]
                if not tokens:
                    initials = "CU"
                elif len(tokens) == 1:
                    initials = tokens[0][:2].upper()
                else:
                    initials = (tokens[0][0] + tokens[-1][0]).upper()
                state_label = item.state.replace("_", " ").title() if item.state else "—"
                last_contact = item.last_interaction_at
                customers.append(
                    {
                        "uuid": str(item.id),
                        "name": name,
                        "initials": initials,
                        "email": item.email or "—",
                        "state": item.state or "",
                        "state_label": state_label,
                        "cases_open": item.open_cases,
                        "cases_total": item.total_cases,
                        "last_contact": last_contact.strftime("%b %d, %Y %H:%M") if last_contact else "No activity yet",
                        "last_contact_iso": last_contact.isoformat() if last_contact else "",
                    }
                )
        except Exception:
            pass

        new_customers = Customer.objects.filter(
            business_profile=business,
            created_at__gte=thirty_days_ago,
        ).count()
        active_customers = Customer.objects.filter(
            business_profile=business,
            record_state="active",
        ).count()
        open_cases = Case.objects.filter(business_profile=business, status=CaseStatus.OPEN).count()
        stats = [
            {"label": "New customers", "value": new_customers or 0, "helper": "Last 30 days"},
            {"label": "Active customers", "value": active_customers or 0, "helper": "Currently engaged"},
            {"label": "Open cases", "value": open_cases or 0, "helper": "Customer cases awaiting action"},
        ]

    context = {
        "user_name": user_name,
        "customers_stats": stats,
        "customers_filters": {
            "search_placeholder": "Search name, email, text…",
            "lifecycle_label": "All lifecycle stages",
            "date_label": "Any time",
            "limit": 25,
        },
        "customers_backend_notice": None,
        "customers_auth_notice": None,
        "customers_loading": False,
        "skeleton_rows": range(6),
        "customers": customers,
        "customers_empty_message": "No customers added yet. Import customers or add one.",
        "customers_showing_count": len(customers),
        "customers_total": total_customers if total_customers else len(customers),
        "customers_has_prev": False,
        "customers_has_next": False,
        "customers_detail_empty_title": "No customer selected",
        "customers_detail_empty_message": "Choose a customer from the table to inspect profiles, activity, and notes.",
    }
    return render(request, "frontend/customers.html", context)


@login_required
def dashboard_agents(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {"label": "Active agents", "value": None, "helper": "No agents deployed yet"},
        {"label": "Avg. satisfaction", "value": None, "helper": "Scores will populate once conversations start"},
        {"label": "Automation coverage", "value": None, "helper": "Connect channels to calculate coverage"},
    ]
    context = {
        "user_name": user_name,
        "agents_stats": stats,
        "agents_filters": {
            "search_placeholder": "Search name, ID, role…",
            "status_label": "All statuses",
            "limit": 25,
        },
        "agents_backend_notice": None,
        "agents_auth_notice": None,
        "agents_loading": False,
        "agents_error_message": None,
        "skeleton_rows": range(6),
        "agents": [],
        "agents_empty_message": "No agents created yet. Launch your first AI teammate to get started.",
        "agents_showing_count": 0,
        "agents_total": 0,
        "agents_has_prev": False,
        "agents_has_next": False,
        "agents_panel_empty_title": "No agent selected",
        "agents_panel_empty_message": "Choose an agent from the table to preview configuration and analytics.",
        "agents_modal_roles": [
            "Support Agent",
            "Sales Associate",
            "Technical Specialist",
            "Customer Success",
        ],
    }
    return render(request, "frontend/agents.html", context)


@login_required
def dashboard_leads(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {"label": "Open leads", "value": None, "delta": "Up 0% vs last week"},
        {"label": "Hot leads", "value": None, "delta": "Ready for outreach"},
        {"label": "Avg. response SLA", "value": None, "delta": "< 3h target"},
    ]
    heat_options = [
        {"label": "All", "value": "all"},
        {"label": "Hot", "value": "hot"},
        {"label": "Warm", "value": "warm"},
        {"label": "Cold", "value": "cold"},
    ]
    pipeline_breakdown = [
        {"label": "New", "helper": "0 leads"},
        {"label": "Qualified", "helper": "0 leads"},
        {"label": "Engaged", "helper": "0 leads"},
        {"label": "Negotiation", "helper": "0 leads"},
        {"label": "Closed Won", "helper": "0 leads"},
    ]
    context = {
        "user_name": user_name,
        "leads_stats": stats,
        "leads_filters": {
            "search_placeholder": "Search lead, company, or tag",
            "stage_label": "All stages",
            "owner_label": "All owners",
            "range_label": "Last 14 days",
        },
        "leads_heat_options": heat_options,
        "leads_heat_active": "all",
        "leads_backend_notice": None,
        "leads_auth_notice": None,
        "leads_loading": False,
        "leads_error_message": None,
        "skeleton_rows": range(6),
        "leads": [],
        "leads_empty_message": "No leads match this view yet.",
        "leads_showing_count": 0,
        "leads_total": 0,
        "leads_has_prev": False,
        "leads_has_next": False,
        "leads_detail_empty_title": "Select a lead",
        "leads_detail_empty_message": "Choose a lead to review stage, owner activity, and history.",
        "leads_pipeline_breakdown": pipeline_breakdown,
    }
    return render(request, "frontend/leads.html", context)


@login_required
def dashboard_knowledge(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    stats = [
        {
            "label": "Documents indexed",
            "value": None,
            "helper": "Upload your first files to populate the knowledge base.",
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3-.75c0 .414.336.75.75.75h.75A2.25 2.25 0 0 0 19.5 15V6A2.25 2.25 0 0 0 17.25 3H6.75A2.25 2.25 0 0 0 4.5 5.25V18A2.25 2.25 0 0 0 6.75 20.25H18"/></svg>',
        },
        {
            "label": "Integrations",
            "value": None,
            "helper": "Connect Google Drive, Zendesk, or custom APIs.",
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12a2.25 2.25 0 0 1 2.25-2.25h10.5A2.25 2.25 0 0 1 19.5 12m-15 0a2.25 2.25 0 0 0 2.25 2.25h10.5A2.25 2.25 0 0 0 19.5 12m-15 0V7.5m15 4.5V16.5m0-9A2.25 2.25 0 0 0 17.25 5.25H6.75A2.25 2.25 0 0 0 4.5 7.5M19.5 16.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 16.5"/></svg>',
        },
        {
            "label": "Coverage",
            "value": None,
            "helper": "Coverage metrics appear once agents use knowledge.",
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v3m0 12v3m9-9h-3M6 12H3m15.364-6.364-2.121 2.121M8.757 15.243l-2.121 2.121m0-12.727 2.121 2.121m6.486 6.486 2.121 2.121"/></svg>',
        },
        {
            "label": "Sync health",
            "value": None,
            "helper": "Status updates will display after first sync.",
            "icon_svg": '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12a7.5 7.5 0 0 1 13.35-4.35L21 8.25M19.5 3v5.25M19.5 12a7.5 7.5 0 0 1-13.35 4.35L3 15.75M4.5 21v-5.25"/></svg>',
        },
    ]
    context = {
        "user_name": user_name,
        "knowledge_stats": stats,
        "knowledge_filters": {
            "search_placeholder": "Search documents, tags, sources…",
            "collection_label": "All collections",
        },
        "knowledge_backend_notice": None,
        "knowledge_auth_notice": None,
        "knowledge_loading": False,
        "knowledge_error_message": None,
        "skeleton_rows": range(5),
        "knowledge_documents": [],
        "knowledge_documents_empty_message": "No knowledge documents yet. Upload files or connect an integration to populate content.",
        "knowledge_documents_showing": 0,
        "knowledge_documents_total": 0,
        "knowledge_documents_has_prev": False,
        "knowledge_documents_has_next": False,
        "knowledge_integrations": [],
        "knowledge_integrations_empty_message": "Connect a source to sync articles, FAQs, or product specs automatically.",
        "knowledge_collections": [],
        "knowledge_collections_empty_message": "Group documents into collections to control agent access.",
        "knowledge_panel_empty_title": "Select a document",
        "knowledge_panel_empty_message": "Choose a document to preview summary, classification, and sync details here.",
    }
    return render(request, "frontend/knowledge.html", context)


@login_required
def dashboard_cases(request: HttpRequest) -> HttpResponse:
    user_name = _current_user_name(request)
    cases: List[Dict[str, object]] = []
    metrics = {"open": None, "urgent": None, "urgent_delta": None, "avg_open": None}
    metrics_message = "Connect your customer channels to start measuring performance."
    total = 0

    def _priority_class(priority: str) -> str:
        mapping = {
            "critical": "border-transparent bg-red-500/10 text-red-600",
            "high": "border-transparent bg-orange-500/10 text-orange-600",
            "medium": "border-transparent bg-amber-500/10 text-amber-600",
            "low": "border-transparent bg-emerald-500/10 text-emerald-600",
        }
        return mapping.get(priority.lower(), "border-transparent bg-muted/60 text-muted-foreground")

    def _status_class(status: str) -> str:
        mapping = {
            "open": "border-transparent bg-emerald-500/10 text-emerald-600",
            "closed": "border-transparent bg-muted/60 text-muted-foreground",
        }
        return mapping.get(status.lower(), "border-transparent bg-muted/60 text-muted-foreground")

    business = None
    if request.user.is_authenticated:
        business = request.user.business_profiles.order_by("-created_at").first()

    if business:
        try:
            result = list_cases(business_profile=business, limit=25)
            total = result.total_count
            metrics = {
                "open": result.metrics.open_total,
                "urgent": result.metrics.urgent_open,
                "urgent_delta": result.metrics.urgent_delta_hint,
                "avg_open": result.metrics.average_open_hours,
            }
            metrics_message = (
                f"You have {result.metrics.open_total} open cases."
                if result.metrics.open_total
                else "All cases resolved. Great job!"
            )
            for item in result.items:
                cases.append(
                    {
                        "id": item.case_number,
                        "uuid": str(item.id),
                        "priority": item.priority,
                        "priority_class": _priority_class(item.priority),
                        "type": "inquiry",
                        "title": item.title,
                        "description": item.description,
                        "status": item.status,
                        "status_class": _status_class(item.status),
                        "customer": {
                            "name": item.customer_name,
                            "email": item.customer_email or "—",
                            "initials": item.customer_initials,
                        },
                        "channel": (item.channel or "chat").replace("_", " ").title(),
                        "started": item.started_at.strftime("%b %d, %Y %H:%M"),
                        "started_iso": item.started_at.isoformat(),
                    }
                )
        except Exception:
            metrics_message = "Unable to load cases right now. Please try again shortly."

    context = {
        "user_name": user_name,
        "cases_total": total if total else len(cases),
        "cases_metrics": metrics,
        "cases_metrics_message": metrics_message,
        "skeleton_rows": range(6),
        "cases_loading": False,
        "cases": cases,
        "cases_empty_message": "No cases yet. Connect Pocket AI to your support channels to see live traffic.",
    }
    return render(request, "frontend/cases.html", context)


def chat_portal(request: HttpRequest, business_slug: str, agent_slug: str) -> HttpResponse:
    session_token = f"session-{business_slug}-{agent_slug}"
    messages = [
        {
            "author": "Pocket AI",
            "initials": "AI",
            "body": "Welcome! Ask me anything about your orders or account.",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    context = {
        "portal": {
            "business": {"name": business_slug.replace("-", " ").title()},
            "agent": {
                "name": agent_slug.replace("-", " ").title(),
                "role": "AI Customer Specialist",
                "bio": "Trained on your knowledge base and policies to provide personalised support.",
                "initials": agent_slug[:2].upper(),
            },
            "session_token": session_token,
            "conversation_status": "ACTIVE",
            "messages": messages,
            "csat_scores": [(i, i) for i in range(1, 6)],
            "endpoints": {
                "messages": "/api/chat/messages/",
                "stream_send": "/api/chat/stream/send/",
                "events": "/api/chat/events/",
                "csat": "/api/chat/csat/",
            },
        }
    }
    return render(request, "frontend/chat/portal.html", context)
