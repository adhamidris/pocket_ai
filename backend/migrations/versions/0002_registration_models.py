"""Create registration flow models and reference data."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import column, table

# revision identifiers, used by Alembic.
revision = "0002_registration_models"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


REGISTRATION_STEP_ENUM = sa.Enum(
    "business_profile",
    "agent_setup",
    "knowledge_uploads",
    "completed",
    name="registration_step_enum",
)

MEMBERSHIP_ROLE_ENUM = sa.Enum(
    "owner",
    "admin",
    "agent",
    name="membership_role_enum",
)

AGENT_ROLE_ENUM = sa.Enum(
    "sales",
    "support",
    "research",
    "success",
    "marketing",
    name="agent_role_enum",
)

AGENT_TONE_ENUM = sa.Enum(
    "friendly",
    "professional",
    "casual",
    "formal",
    "empathetic",
    "playful",
    name="agent_tone_enum",
)

AGENT_TRAIT_ENUM = sa.Enum(
    "concise",
    "detailed",
    "curious",
    "patient",
    "proactive",
    "direct",
    "creative",
    name="agent_trait_enum",
)

ESCALATION_RULE_ENUM = sa.Enum(
    "never",
    "on_fallback",
    "on_negative_sentiment",
    "on_high_value",
    "always",
    name="escalation_rule_enum",
)

KNOWLEDGE_SOURCE_ENUM = sa.Enum(
    "file",
    "url",
    "text",
    name="knowledge_source_enum",
)

KNOWLEDGE_STATUS_ENUM = sa.Enum(
    "pending",
    "processing",
    "ready",
    "failed",
    name="knowledge_status_enum",
)

INDUSTRY_ROWS = [
    {"code": "industry:e-commerce", "label": "E-commerce", "search_terms": ["e-commerce", "retail"]},
    {"code": "industry:saas", "label": "SaaS", "search_terms": ["saas", "software"]},
    {"code": "industry:finance", "label": "Finance", "search_terms": ["financial"]},
    {"code": "industry:healthcare", "label": "Healthcare", "search_terms": ["health"]},
    {"code": "industry:education", "label": "Education", "search_terms": ["education"]},
    {"code": "industry:hospitality", "label": "Hospitality", "search_terms": ["hospitality", "travel"]},
    {"code": "industry:manufacturing", "label": "Manufacturing", "search_terms": ["manufactur"]},
    {"code": "industry:logistics", "label": "Logistics", "search_terms": ["logistics", "transport"]},
    {"code": "industry:real-estate", "label": "Real Estate", "search_terms": ["real estate"]},
    {"code": "industry:media-and-entertainment", "label": "Media & Entertainment", "search_terms": ["media", "entertainment"]},
    {"code": "industry:telecommunications", "label": "Telecommunications", "search_terms": ["telecom"]},
    {"code": "industry:energy-and-utilities", "label": "Energy & Utilities", "search_terms": ["energy", "utilit"]},
    {"code": "industry:nonprofit-and-ngos", "label": "Nonprofit & NGOs", "search_terms": ["nonprofit", "ngo"]},
    {"code": "industry:professional-services", "label": "Professional Services", "search_terms": ["professional"]},
    {"code": "industry:consumer-services", "label": "Consumer Services", "search_terms": ["consumer"]},
    {"code": "industry:other", "label": "Other", "search_terms": []},
]

INDUSTRY_NICHES_ROWS = [
    {"code": "niche:e-commerce-apparel", "industry_code": "industry:e-commerce", "label": "Apparel", "search_terms": []},
    {"code": "niche:e-commerce-electronics", "industry_code": "industry:e-commerce", "label": "Electronics", "search_terms": []},
    {"code": "niche:e-commerce-beauty-and-personal-care", "industry_code": "industry:e-commerce", "label": "Beauty & Personal Care", "search_terms": []},
    {"code": "niche:e-commerce-home-and-kitchen", "industry_code": "industry:e-commerce", "label": "Home & Kitchen", "search_terms": []},
    {"code": "niche:e-commerce-sports-and-outdoors", "industry_code": "industry:e-commerce", "label": "Sports & Outdoors", "search_terms": []},
    {"code": "niche:e-commerce-groceries", "industry_code": "industry:e-commerce", "label": "Groceries", "search_terms": []},
    {"code": "niche:e-commerce-digital-goods", "industry_code": "industry:e-commerce", "label": "Digital Goods", "search_terms": []},
    {"code": "niche:e-commerce-handmade-and-crafts", "industry_code": "industry:e-commerce", "label": "Handmade & Crafts", "search_terms": []},
    {"code": "niche:e-commerce-automotive-accessories", "industry_code": "industry:e-commerce", "label": "Automotive Accessories", "search_terms": []},
    {"code": "niche:saas-crm", "industry_code": "industry:saas", "label": "CRM", "search_terms": []},
    {"code": "niche:saas-marketing-automation", "industry_code": "industry:saas", "label": "Marketing Automation", "search_terms": []},
    {"code": "niche:saas-analytics", "industry_code": "industry:saas", "label": "Analytics", "search_terms": []},
    {"code": "niche:saas-project-management", "industry_code": "industry:saas", "label": "Project Management", "search_terms": []},
    {"code": "niche:saas-customer-support", "industry_code": "industry:saas", "label": "Customer Support", "search_terms": []},
    {"code": "niche:saas-developer-tools", "industry_code": "industry:saas", "label": "Developer Tools", "search_terms": []},
    {"code": "niche:saas-productivity", "industry_code": "industry:saas", "label": "Productivity", "search_terms": []},
    {"code": "niche:saas-security", "industry_code": "industry:saas", "label": "Security", "search_terms": []},
    {"code": "niche:saas-billing-subscriptions", "industry_code": "industry:saas", "label": "Billing/Subscriptions", "search_terms": []},
    {"code": "niche:saas-auth-identity", "industry_code": "industry:saas", "label": "Auth/Identity", "search_terms": []},
    {"code": "niche:saas-observability", "industry_code": "industry:saas", "label": "Observability", "search_terms": []},
    {"code": "niche:saas-data-platform", "industry_code": "industry:saas", "label": "Data Platform", "search_terms": []},
    {"code": "niche:finance-banking", "industry_code": "industry:finance", "label": "Banking", "search_terms": []},
    {"code": "niche:finance-lending", "industry_code": "industry:finance", "label": "Lending", "search_terms": []},
    {"code": "niche:finance-payments", "industry_code": "industry:finance", "label": "Payments", "search_terms": []},
    {"code": "niche:finance-wealth-management", "industry_code": "industry:finance", "label": "Wealth Management", "search_terms": []},
    {"code": "niche:finance-insurance", "industry_code": "industry:finance", "label": "Insurance", "search_terms": []},
    {"code": "niche:finance-accounting", "industry_code": "industry:finance", "label": "Accounting", "search_terms": []},
    {"code": "niche:finance-crypto-blockchain", "industry_code": "industry:finance", "label": "Crypto/Blockchain", "search_terms": []},
    {"code": "niche:finance-trading-platforms", "industry_code": "industry:finance", "label": "Trading Platforms", "search_terms": []},
    {"code": "niche:healthcare-clinics", "industry_code": "industry:healthcare", "label": "Clinics", "search_terms": []},
    {"code": "niche:healthcare-telemedicine", "industry_code": "industry:healthcare", "label": "Telemedicine", "search_terms": []},
    {"code": "niche:healthcare-pharmacy", "industry_code": "industry:healthcare", "label": "Pharmacy", "search_terms": []},
    {"code": "niche:healthcare-diagnostics", "industry_code": "industry:healthcare", "label": "Diagnostics", "search_terms": []},
    {"code": "niche:healthcare-medical-devices", "industry_code": "industry:healthcare", "label": "Medical Devices", "search_terms": []},
    {"code": "niche:healthcare-wellness", "industry_code": "industry:healthcare", "label": "Wellness", "search_terms": []},
    {"code": "niche:healthcare-electronic-health-records", "industry_code": "industry:healthcare", "label": "Electronic Health Records", "search_terms": []},
    {"code": "niche:education-k-12", "industry_code": "industry:education", "label": "K-12", "search_terms": []},
    {"code": "niche:education-higher-education", "industry_code": "industry:education", "label": "Higher Education", "search_terms": []},
    {"code": "niche:education-edtech-platform", "industry_code": "industry:education", "label": "EdTech Platform", "search_terms": []},
    {"code": "niche:education-corporate-training", "industry_code": "industry:education", "label": "Corporate Training", "search_terms": []},
    {"code": "niche:education-test-prep", "industry_code": "industry:education", "label": "Test Prep", "search_terms": []},
    {"code": "niche:education-language-learning", "industry_code": "industry:education", "label": "Language Learning", "search_terms": []},
    {"code": "niche:education-tutoring-coaching", "industry_code": "industry:education", "label": "Tutoring & Coaching", "search_terms": []},
    {"code": "niche:hospitality-hotels", "industry_code": "industry:hospitality", "label": "Hotels", "search_terms": []},
    {"code": "niche:hospitality-restaurants", "industry_code": "industry:hospitality", "label": "Restaurants", "search_terms": []},
    {"code": "niche:hospitality-catering", "industry_code": "industry:hospitality", "label": "Catering", "search_terms": []},
    {"code": "niche:hospitality-travel-tours", "industry_code": "industry:hospitality", "label": "Travel & Tours", "search_terms": []},
    {"code": "niche:hospitality-venues-events", "industry_code": "industry:hospitality", "label": "Venues & Events", "search_terms": []},
    {"code": "niche:hospitality-short-term-rentals", "industry_code": "industry:hospitality", "label": "Short-Term Rentals", "search_terms": []},
    {"code": "niche:manufacturing-oem-production", "industry_code": "industry:manufacturing", "label": "OEM Production", "search_terms": []},
    {"code": "niche:manufacturing-contract-manufacturing", "industry_code": "industry:manufacturing", "label": "Contract Manufacturing", "search_terms": []},
    {"code": "niche:manufacturing-cnc-machining", "industry_code": "industry:manufacturing", "label": "CNC Machining", "search_terms": []},
    {"code": "niche:manufacturing-injection-molding", "industry_code": "industry:manufacturing", "label": "Injection Molding", "search_terms": []},
    {"code": "niche:manufacturing-3d-printing", "industry_code": "industry:manufacturing", "label": "3D Printing", "search_terms": []},
    {"code": "niche:manufacturing-pcb-assembly", "industry_code": "industry:manufacturing", "label": "PCB Assembly", "search_terms": []},
    {"code": "niche:manufacturing-quality-assurance", "industry_code": "industry:manufacturing", "label": "Quality Assurance", "search_terms": []},
    {"code": "niche:manufacturing-procurement-supply", "industry_code": "industry:manufacturing", "label": "Procurement & Supply", "search_terms": []},
    {"code": "niche:manufacturing-packaging", "industry_code": "industry:manufacturing", "label": "Packaging", "search_terms": []},
    {"code": "niche:manufacturing-maintenance-mro", "industry_code": "industry:manufacturing", "label": "Maintenance (MRO)", "search_terms": []},
    {"code": "niche:logistics-freight-forwarding", "industry_code": "industry:logistics", "label": "Freight Forwarding", "search_terms": []},
    {"code": "niche:logistics-last-mile-delivery", "industry_code": "industry:logistics", "label": "Last-Mile Delivery", "search_terms": []},
    {"code": "niche:logistics-warehousing-fulfillment", "industry_code": "industry:logistics", "label": "Warehousing & Fulfillment", "search_terms": []},
    {"code": "niche:logistics-cold-chain", "industry_code": "industry:logistics", "label": "Cold Chain", "search_terms": []},
    {"code": "niche:logistics-customs-brokerage", "industry_code": "industry:logistics", "label": "Customs Brokerage", "search_terms": []},
    {"code": "niche:logistics-fleet-management", "industry_code": "industry:logistics", "label": "Fleet Management", "search_terms": []},
    {"code": "niche:logistics-courier", "industry_code": "industry:logistics", "label": "Courier", "search_terms": []},
    {"code": "niche:logistics-ltl-ftl-trucking", "industry_code": "industry:logistics", "label": "LTL/FTL Trucking", "search_terms": []},
    {"code": "niche:logistics-air-cargo", "industry_code": "industry:logistics", "label": "Air Cargo", "search_terms": []},
    {"code": "niche:logistics-ocean-freight", "industry_code": "industry:logistics", "label": "Ocean Freight", "search_terms": []},
    {"code": "niche:real-estate-residential-sales", "industry_code": "industry:real-estate", "label": "Residential Sales", "search_terms": []},
    {"code": "niche:real-estate-commercial-leasing", "industry_code": "industry:real-estate", "label": "Commercial Leasing", "search_terms": []},
    {"code": "niche:real-estate-property-management", "industry_code": "industry:real-estate", "label": "Property Management", "search_terms": []},
    {"code": "niche:real-estate-valuation-appraisal", "industry_code": "industry:real-estate", "label": "Valuation & Appraisal", "search_terms": []},
    {"code": "niche:real-estate-real-estate-development", "industry_code": "industry:real-estate", "label": "Real Estate Development", "search_terms": []},
    {"code": "niche:real-estate-facility-management", "industry_code": "industry:real-estate", "label": "Facility Management", "search_terms": []},
    {"code": "niche:real-estate-co-working", "industry_code": "industry:real-estate", "label": "Co-working", "search_terms": []},
    {"code": "niche:real-estate-mortgage-brokerage", "industry_code": "industry:real-estate", "label": "Mortgage Brokerage", "search_terms": []},
    {"code": "niche:real-estate-title-escrow", "industry_code": "industry:real-estate", "label": "Title & Escrow", "search_terms": []},
    {"code": "niche:real-estate-short-term-rentals", "industry_code": "industry:real-estate", "label": "Short-Term Rentals", "search_terms": []},
    {"code": "niche:media-and-entertainment-streaming-subscriptions", "industry_code": "industry:media-and-entertainment", "label": "Streaming Subscriptions", "search_terms": []},
    {"code": "niche:media-and-entertainment-ott-platform", "industry_code": "industry:media-and-entertainment", "label": "OTT Platform", "search_terms": []},
    {"code": "niche:media-and-entertainment-content-production", "industry_code": "industry:media-and-entertainment", "label": "Content Production", "search_terms": []},
    {"code": "niche:media-and-entertainment-post-production", "industry_code": "industry:media-and-entertainment", "label": "Post-Production", "search_terms": []},
    {"code": "niche:media-and-entertainment-music-publishing", "industry_code": "industry:media-and-entertainment", "label": "Music Publishing", "search_terms": []},
    {"code": "niche:media-and-entertainment-game-development", "industry_code": "industry:media-and-entertainment", "label": "Game Development", "search_terms": []},
    {"code": "niche:media-and-entertainment-live-events", "industry_code": "industry:media-and-entertainment", "label": "Live Events", "search_terms": []},
    {"code": "niche:media-and-entertainment-digital-advertising", "industry_code": "industry:media-and-entertainment", "label": "Digital Advertising", "search_terms": []},
    {"code": "niche:media-and-entertainment-influencer-campaigns", "industry_code": "industry:media-and-entertainment", "label": "Influencer Campaigns", "search_terms": []},
    {"code": "niche:media-and-entertainment-licensing-syndication", "industry_code": "industry:media-and-entertainment", "label": "Licensing & Syndication", "search_terms": []},
    {"code": "niche:telecommunications-mobile-voice", "industry_code": "industry:telecommunications", "label": "Mobile Voice", "search_terms": []},
    {"code": "niche:telecommunications-fixed-broadband", "industry_code": "industry:telecommunications", "label": "Fixed Broadband", "search_terms": []},
    {"code": "niche:telecommunications-voip", "industry_code": "industry:telecommunications", "label": "VoIP", "search_terms": []},
    {"code": "niche:telecommunications-iot-connectivity", "industry_code": "industry:telecommunications", "label": "IoT Connectivity", "search_terms": []},
    {"code": "niche:telecommunications-cloud-pbx", "industry_code": "industry:telecommunications", "label": "Cloud PBX", "search_terms": []},
    {"code": "niche:telecommunications-sip-trunking", "industry_code": "industry:telecommunications", "label": "SIP Trunking", "search_terms": []},
    {"code": "niche:telecommunications-managed-networks", "industry_code": "industry:telecommunications", "label": "Managed Networks", "search_terms": []},
    {"code": "niche:telecommunications-5g-solutions", "industry_code": "industry:telecommunications", "label": "5G Solutions", "search_terms": []},
    {"code": "niche:telecommunications-fiber-to-the-home", "industry_code": "industry:telecommunications", "label": "Fiber to the Home", "search_terms": []},
    {"code": "niche:telecommunications-data-center-colocation", "industry_code": "industry:telecommunications", "label": "Data Center Colocation", "search_terms": []},
    {"code": "niche:energy-and-utilities-electricity-supply", "industry_code": "industry:energy-and-utilities", "label": "Electricity Supply", "search_terms": []},
    {"code": "niche:energy-and-utilities-natural-gas-supply", "industry_code": "industry:energy-and-utilities", "label": "Natural Gas Supply", "search_terms": []},
    {"code": "niche:energy-and-utilities-renewable-generation", "industry_code": "industry:energy-and-utilities", "label": "Renewable Generation", "search_terms": []},
    {"code": "niche:energy-and-utilities-solar-installation", "industry_code": "industry:energy-and-utilities", "label": "Solar Installation", "search_terms": []},
    {"code": "niche:energy-and-utilities-energy-storage", "industry_code": "industry:energy-and-utilities", "label": "Energy Storage", "search_terms": []},
    {"code": "niche:energy-and-utilities-smart-metering", "industry_code": "industry:energy-and-utilities", "label": "Smart Metering", "search_terms": []},
    {"code": "niche:energy-and-utilities-demand-response", "industry_code": "industry:energy-and-utilities", "label": "Demand Response", "search_terms": []},
    {"code": "niche:energy-and-utilities-energy-trading", "industry_code": "industry:energy-and-utilities", "label": "Energy Trading", "search_terms": []},
    {"code": "niche:energy-and-utilities-ev-charging", "industry_code": "industry:energy-and-utilities", "label": "EV Charging", "search_terms": []},
    {"code": "niche:energy-and-utilities-utility-billing", "industry_code": "industry:energy-and-utilities", "label": "Utility Billing", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-fundraising", "industry_code": "industry:nonprofit-and-ngos", "label": "Fundraising", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-grant-management", "industry_code": "industry:nonprofit-and-ngos", "label": "Grant Management", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-program-delivery", "industry_code": "industry:nonprofit-and-ngos", "label": "Program Delivery", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-volunteer-management", "industry_code": "industry:nonprofit-and-ngos", "label": "Volunteer Management", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-advocacy-and-outreach", "industry_code": "industry:nonprofit-and-ngos", "label": "Advocacy & Outreach", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-education-programs", "industry_code": "industry:nonprofit-and-ngos", "label": "Education Programs", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-healthcare-missions", "industry_code": "industry:nonprofit-and-ngos", "label": "Healthcare Missions", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-disaster-relief", "industry_code": "industry:nonprofit-and-ngos", "label": "Disaster Relief", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-community-development", "industry_code": "industry:nonprofit-and-ngos", "label": "Community Development", "search_terms": []},
    {"code": "niche:nonprofit-and-ngos-monitoring-and-evaluation", "industry_code": "industry:nonprofit-and-ngos", "label": "Monitoring & Evaluation", "search_terms": []},
    {"code": "niche:professional-services-consulting", "industry_code": "industry:professional-services", "label": "Consulting", "search_terms": []},
    {"code": "niche:professional-services-legal-advisory", "industry_code": "industry:professional-services", "label": "Legal Advisory", "search_terms": []},
    {"code": "niche:professional-services-tax-and-audit", "industry_code": "industry:professional-services", "label": "Tax & Audit", "search_terms": []},
    {"code": "niche:professional-services-accounting", "industry_code": "industry:professional-services", "label": "Accounting", "search_terms": []},
    {"code": "niche:professional-services-architecture", "industry_code": "industry:professional-services", "label": "Architecture", "search_terms": []},
    {"code": "niche:professional-services-engineering", "industry_code": "industry:professional-services", "label": "Engineering", "search_terms": []},
    {"code": "niche:professional-services-design-and-creative", "industry_code": "industry:professional-services", "label": "Design & Creative", "search_terms": []},
    {"code": "niche:professional-services-recruitment", "industry_code": "industry:professional-services", "label": "Recruitment", "search_terms": []},
    {"code": "niche:professional-services-it-consulting", "industry_code": "industry:professional-services", "label": "IT Consulting", "search_terms": []},
    {"code": "niche:professional-services-managed-it", "industry_code": "industry:professional-services", "label": "Managed IT", "search_terms": []},
    {"code": "niche:consumer-services-home-cleaning", "industry_code": "industry:consumer-services", "label": "Home Cleaning", "search_terms": []},
    {"code": "niche:consumer-services-appliance-repair", "industry_code": "industry:consumer-services", "label": "Appliance Repair", "search_terms": []},
    {"code": "niche:consumer-services-beauty-and-wellness", "industry_code": "industry:consumer-services", "label": "Beauty & Wellness", "search_terms": []},
    {"code": "niche:consumer-services-fitness-and-training", "industry_code": "industry:consumer-services", "label": "Fitness & Training", "search_terms": []},
    {"code": "niche:consumer-services-tutoring", "industry_code": "industry:consumer-services", "label": "Tutoring", "search_terms": []},
    {"code": "niche:consumer-services-pet-care", "industry_code": "industry:consumer-services", "label": "Pet Care", "search_terms": []},
    {"code": "niche:consumer-services-event-planning", "industry_code": "industry:consumer-services", "label": "Event Planning", "search_terms": []},
    {"code": "niche:consumer-services-photography", "industry_code": "industry:consumer-services", "label": "Photography", "search_terms": []},
    {"code": "niche:consumer-services-home-renovation", "industry_code": "industry:consumer-services", "label": "Home Renovation", "search_terms": []},
    {"code": "niche:consumer-services-moving-and-storage", "industry_code": "industry:consumer-services", "label": "Moving & Storage", "search_terms": []},
    {"code": "niche:other-consulting", "industry_code": "industry:other", "label": "Consulting", "search_terms": []},
    {"code": "niche:other-custom-development", "industry_code": "industry:other", "label": "Custom Development", "search_terms": []},
    {"code": "niche:other-training-and-enablement", "industry_code": "industry:other", "label": "Training & Enablement", "search_terms": []},
    {"code": "niche:other-support-and-success", "industry_code": "industry:other", "label": "Support & Success", "search_terms": []},
]


def upgrade() -> None:
    bind = op.get_bind()

    REGISTRATION_STEP_ENUM.create(bind, checkfirst=True)
    MEMBERSHIP_ROLE_ENUM.create(bind, checkfirst=True)
    AGENT_ROLE_ENUM.create(bind, checkfirst=True)
    AGENT_TONE_ENUM.create(bind, checkfirst=True)
    AGENT_TRAIT_ENUM.create(bind, checkfirst=True)
    ESCALATION_RULE_ENUM.create(bind, checkfirst=True)
    KNOWLEDGE_SOURCE_ENUM.create(bind, checkfirst=True)
    KNOWLEDGE_STATUS_ENUM.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("first_name", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("auth_provider", sa.String(length=32), nullable=False, server_default="password"),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("char_length(first_name) BETWEEN 1 AND 80", name="ck_users_first_name_length"),
        sa.CheckConstraint("auth_provider IN ('password','google')", name="ck_users_auth_provider_allowed"),
    )

    op.create_table(
        "industries",
        sa.Column("code", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("search_terms", postgresql.ARRAY(sa.String(length=64)), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("code ~ '^industry:[a-z0-9-]{2,50}$'", name="ck_industries_code_format"),
        sa.CheckConstraint("char_length(label) BETWEEN 2 AND 120", name="ck_industries_label_length"),
    )

    op.create_table(
        "industry_niches",
        sa.Column("code", sa.String(length=96), primary_key=True, nullable=False),
        sa.Column("industry_code", sa.String(length=64), sa.ForeignKey("industries.code", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("search_terms", postgresql.ARRAY(sa.String(length=64)), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("code ~ '^niche:[a-z0-9-]{2,100}$'", name="ck_industry_niches_code_format"),
        sa.CheckConstraint("char_length(label) BETWEEN 2 AND 120", name="ck_industry_niches_label_length"),
    )

    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("industry_code", sa.String(length=64), sa.ForeignKey("industries.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("char_length(name) BETWEEN 2 AND 120", name="ck_businesses_name_length"),
        sa.CheckConstraint("industry_code ~ '^industry:[a-z0-9-]{2,50}$'", name="ck_businesses_industry_code_format"),
    )

    op.create_table(
        "registration_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("current_step", REGISTRATION_STEP_ENUM, nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now() + interval '7 days'")),
        sa.Column("steps_completed", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_steps", sa.SmallInteger(), nullable=False, server_default=sa.text("4")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"), server_onupdate=sa.text("now()")),
        sa.CheckConstraint("expires_at > created_at", name="ck_registration_sessions_expiry_after_created"),
        sa.CheckConstraint("steps_completed BETWEEN 0 AND total_steps", name="ck_registration_sessions_steps_range"),
    )

    op.create_table(
        "business_niches",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("niche_code", sa.String(length=96), sa.ForeignKey("industry_niches.code", ondelete="RESTRICT"), primary_key=True, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("niche_code ~ '^niche:[a-z0-9-]{2,50}$'", name="ck_business_niches_code_format"),
    )

    op.create_table(
        "user_business_memberships",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("role", MEMBERSHIP_ROLE_ENUM, nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("role", AGENT_ROLE_ENUM, nullable=False),
        sa.Column("tone", AGENT_TONE_ENUM, nullable=False),
        sa.Column("escalation_rule", ESCALATION_RULE_ENUM, nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("char_length(name) BETWEEN 2 AND 80", name="ck_agents_name_length"),
    )

    op.create_table(
        "agent_traits",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("trait_code", AGENT_TRAIT_ENUM, primary_key=True, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "knowledge_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", KNOWLEDGE_SOURCE_ENUM, nullable=False),
        sa.Column("status", KNOWLEDGE_STATUS_ENUM, nullable=False, server_default="pending"),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("display_name IS NULL OR char_length(display_name) <= 120", name="ck_knowledge_items_display_name_length"),
    )

    op.create_table(
        "knowledge_item_files",
        sa.Column("knowledge_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=128), nullable=True),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 20971520", name="ck_knowledge_item_files_size_range"),
        sa.CheckConstraint(
            "content_type IS NULL OR lower(content_type) = ANY (ARRAY['application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document','application/vnd.ms-excel','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.google-apps.document','application/vnd.google-apps.spreadsheet'])",
            name="ck_knowledge_item_files_content_type_allowed",
        ),
    )

    op.create_table(
        "knowledge_item_urls",
        sa.Column("knowledge_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.CheckConstraint("url LIKE 'https://%'", name="ck_knowledge_item_urls_https_only"),
    )

    op.create_table(
        "knowledge_item_texts",
        sa.Column("knowledge_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.CheckConstraint("char_length(text_content) BETWEEN 1 AND 200000", name="ck_knowledge_item_texts_length"),
    )

    op.create_index("ix_industry_niches_industry_code", "industry_niches", ["industry_code"])
    op.create_index("ix_businesses_industry_code", "businesses", ["industry_code"])
    op.create_index("ix_business_niches_business_id", "business_niches", ["business_id"])
    op.create_index("ix_registration_sessions_user_id", "registration_sessions", ["user_id"])
    op.create_index(
        "ix_user_business_memberships_business_role",
        "user_business_memberships",
        ["business_id", "role"],
    )
    op.create_index("ix_agents_business_id", "agents", ["business_id"])
    op.create_index(
        "ix_knowledge_items_business_status",
        "knowledge_items",
        ["business_id", "status"],
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))"
    )

    industries_table = table(
        "industries",
        column("code", sa.String()),
        column("label", sa.String()),
        column("search_terms", postgresql.ARRAY(sa.String())),
    )
    op.bulk_insert(industries_table, INDUSTRY_ROWS)

    industry_niches_table = table(
        "industry_niches",
        column("code", sa.String()),
        column("industry_code", sa.String()),
        column("label", sa.String()),
        column("search_terms", postgresql.ARRAY(sa.String())),
    )
    op.bulk_insert(industry_niches_table, INDUSTRY_NICHES_ROWS)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")

    op.drop_index("ix_knowledge_items_business_status", table_name="knowledge_items")
    op.drop_index("ix_agents_business_id", table_name="agents")
    op.drop_index("ix_user_business_memberships_business_role", table_name="user_business_memberships")
    op.drop_index("ix_registration_sessions_user_id", table_name="registration_sessions")
    op.drop_index("ix_business_niches_business_id", table_name="business_niches")
    op.drop_index("ix_businesses_industry_code", table_name="businesses")
    op.drop_index("ix_industry_niches_industry_code", table_name="industry_niches")

    op.drop_table("knowledge_item_texts")
    op.drop_table("knowledge_item_urls")
    op.drop_table("knowledge_item_files")
    op.drop_table("knowledge_items")
    op.drop_table("agent_traits")
    op.drop_table("agents")
    op.drop_table("user_business_memberships")
    op.drop_table("business_niches")
    op.drop_table("registration_sessions")
    op.drop_table("businesses")
    op.drop_table("industry_niches")
    op.drop_table("industries")
    op.drop_table("users")

    KNOWLEDGE_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
    KNOWLEDGE_SOURCE_ENUM.drop(op.get_bind(), checkfirst=True)
    ESCALATION_RULE_ENUM.drop(op.get_bind(), checkfirst=True)
    AGENT_TRAIT_ENUM.drop(op.get_bind(), checkfirst=True)
    AGENT_TONE_ENUM.drop(op.get_bind(), checkfirst=True)
    AGENT_ROLE_ENUM.drop(op.get_bind(), checkfirst=True)
    MEMBERSHIP_ROLE_ENUM.drop(op.get_bind(), checkfirst=True)
    REGISTRATION_STEP_ENUM.drop(op.get_bind(), checkfirst=True)
