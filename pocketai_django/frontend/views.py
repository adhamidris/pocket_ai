from __future__ import annotations

from datetime import date
from typing import Dict, List

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


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
