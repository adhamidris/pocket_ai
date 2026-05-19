from __future__ import annotations

from typing import Any, Mapping

from apps.accounts.models import BusinessProfile
from apps.integrations.models import EmailAccount, IntegrationAccount

_MCP_SETUP_FIELDS_MAX_KEYS = 25
_MCP_SETUP_FIELD_MAX_CHARS = 4096
_MCP_SETUP_FIELDS_TOTAL_MAX_CHARS = 16384
_MCP_NATIVE_OAUTH_CONNECTION_TYPES = frozenset({"email_oauth", "integration_oauth"})
_MCP_SURFACE_INTEGRATIONS = "integrations"

def _marketplace_entry(marketplace_key: str) -> dict[str, Any] | None:
    key = str(marketplace_key or "").strip()
    if not key:
        return None
    for item in _mcp_marketplace_catalog():
        if str(item.get("key") or "").strip() == key:
            return item
    return None


def _is_native_oauth_marketplace_item(item: Mapping[str, Any]) -> bool:
    connection_type = str(item.get("connectionType") or "").strip().lower()
    return connection_type in _MCP_NATIVE_OAUTH_CONNECTION_TYPES


def _extract_setup_fields(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """
    Extract optional setup fields from request payload.

    Supports either top-level "setupFields" or legacy "metadata.setupFields".
    """
    direct = payload.get("setupFields")
    if isinstance(direct, dict):
        return direct
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        nested = metadata.get("setupFields")
        if isinstance(nested, dict):
            return nested
    return None


def _validate_setup_fields(
    setup_fields: Mapping[str, Any],
    *,
    marketplace_key: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    """
    Validate + normalize marketplace setup fields.

    Stored values are treated as sensitive and persisted encrypted (inside McpConnection.credentials).
    """
    entry = _marketplace_entry(marketplace_key or "")
    if entry is None:
        if marketplace_key:
            return None, "marketplaceKey is invalid."
        return None, "setupFields require a marketplaceKey."
    allowed = entry.get("setupFields") if isinstance(entry, Mapping) else None
    allowed_keys = [str(key).strip() for key in allowed if str(key).strip()] if isinstance(allowed, list) else []
    allowed_set = set(allowed_keys)

    if not allowed_set:
        return None, "This MCP does not accept setup fields."

    cleaned: dict[str, str] = {}
    total_chars = 0
    for raw_key, raw_value in setup_fields.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if key not in allowed_set:
            return None, f"Unknown setup field: {key}."

        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            return None, f"Setup field {key} must be a string."
        value = raw_value.strip()
        if not value:
            continue
        if len(value) > _MCP_SETUP_FIELD_MAX_CHARS:
            return None, f"Setup field {key} is too long."
        cleaned[key] = value
        total_chars += len(value)
        if total_chars > _MCP_SETUP_FIELDS_TOTAL_MAX_CHARS:
            return None, "Setup fields payload is too large."
        if len(cleaned) > _MCP_SETUP_FIELDS_MAX_KEYS:
            return None, "Too many setup fields."

    return cleaned, None


def _mcp_marketplace_catalog() -> list[dict[str, Any]]:
    """
    Curated MCP marketplace (templates).

    Note: Entries are templates—customers still supply their own server URL unless
    an entry includes a hosted URL.

    Categories:
    - communication: Email, chat, messaging tools
    - storage: File storage, cloud drives
    - productivity: Project management, notes, databases
    - crm: Customer relationship management
    - analytics: Data analytics, reporting
    - development: Code, repos, CI/CD
    - marketing: Ads, email marketing, social
    - ecommerce: Shopping, payments, inventory
    - finance: Accounting, invoicing, banking
    - utilities: Search, web scraping, general tools

    Industries (matches BusinessProfile.industry_key):
    - marketing, ecommerce, healthcare, legal, real_estate, saas_tech, finance, consulting, general
    """

    return [
        # ═══════════════════════════════════════════════════════════════════════
        # COMMUNICATION - EMAIL (Native first-party connectors)
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "gmail",
            "name": "Gmail",
            "description": "Search, read, and send emails via Gmail. Native integration with draft approval for safe sending.",
            "category": "communication",
            "industries": ["marketing", "ecommerce", "legal", "real_estate", "consulting", "general"],
            "connectionType": "email_oauth",  # Special type for native email connectors
            "oauthProvider": "google_email",  # Uses /api/email/oauth/start/
            "serverUrl": "__builtin__",  # Native - no external MCP server
            "docsUrl": "https://developers.google.com/gmail/api",
            "badge": "Popular",
            "tier": 1,  # First-party = tier 1
            "setupFields": [],
        },
        {
            "key": "outlook",
            "name": "Outlook / Microsoft 365",
            "description": "Search, read, and send emails via Microsoft Graph. Native integration with draft approval for safe sending.",
            "category": "communication",
            "industries": ["consulting", "finance", "legal", "real_estate", "general"],
            "connectionType": "email_oauth",  # Special type for native email connectors
            "oauthProvider": "microsoft_email",  # Uses /api/email/oauth/start/
            "serverUrl": "__builtin__",  # Native - no external MCP server
            "docsUrl": "https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview",
            "badge": "Popular",
            "tier": 1,  # First-party = tier 1
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # COMMUNICATION - MESSAGING
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "slack",
            "name": "Slack",
            "description": "Send messages, read channels, and search across your Slack workspace. Native integration with encrypted credentials.",
            "category": "communication",
            "industries": ["marketing", "saas_tech", "consulting", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "slack_native",
            "serverUrl": "__builtin__",
            "docsUrl": "https://api.slack.com/",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        {
            "key": "microsoft_teams",
            "name": "Microsoft Teams",
            "description": "Send messages and manage team channels via Teams API.",
            "category": "communication",
            "industries": ["consulting", "finance", "legal", "general"],
            "connectionType": "oauth",
            "oauthProvider": "microsoft",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/microsoft-teams",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        {
            "key": "discord",
            "name": "Discord",
            "description": "Bot integration for Discord servers and channels.",
            "category": "communication",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/discord",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],  # Just needs bot token
            "setupLabels": {"token": "Discord Bot Token"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # STORAGE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "google_drive",
            "name": "Google Drive",
            "description": "Search, list, and read files in Google Drive. Native integration with encrypted credentials.",
            "category": "storage",
            "industries": ["marketing", "ecommerce", "legal", "consulting", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "google_drive",
            "serverUrl": "__builtin__",
            "docsUrl": "https://developers.google.com/drive/api",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        {
            "key": "dropbox",
            "name": "Dropbox",
            "description": "File storage and sharing via Dropbox API.",
            "category": "storage",
            "industries": ["consulting", "legal", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/dropbox",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        {
            "key": "onedrive",
            "name": "OneDrive",
            "description": "Search, list, and read files in OneDrive. Native integration with encrypted credentials.",
            "category": "storage",
            "industries": ["consulting", "finance", "legal", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "microsoft_drive",
            "serverUrl": "__builtin__",
            "docsUrl": "https://learn.microsoft.com/en-us/graph/api/resources/onedrive",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # PRODUCTIVITY
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "notion",
            "name": "Notion",
            "description": "Access pages, databases, and workspace content in Notion.",
            "category": "productivity",
            "industries": ["marketing", "saas_tech", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/notion",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Notion Integration Token"},
            "setupHelp": {"token": "Create an integration at notion.so/my-integrations"},
        },
        {
            "key": "airtable",
            "name": "Airtable",
            "description": "Database and spreadsheet hybrid for structured data management.",
            "category": "productivity",
            "industries": ["marketing", "ecommerce", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/airtable",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Airtable API Key"},
            "setupHelp": {"token": "Find at airtable.com/account"},
        },
        {
            "key": "trello",
            "name": "Trello",
            "description": "Kanban boards and task management via Trello API.",
            "category": "productivity",
            "industries": ["marketing", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/trello",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Trello API Key"},
        },
        {
            "key": "asana",
            "name": "Asana",
            "description": "Project and task management via Asana API.",
            "category": "productivity",
            "industries": ["marketing", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/asana",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        {
            "key": "google_calendar",
            "name": "Google Calendar",
            "description": "List, create, and manage calendar events. Native integration with encrypted credentials.",
            "category": "productivity",
            "industries": ["real_estate", "consulting", "legal", "healthcare", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "google_calendar",
            "serverUrl": "__builtin__",
            "docsUrl": "https://developers.google.com/calendar/api",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # CRM
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "salesforce",
            "name": "Salesforce",
            "description": "Access leads, opportunities, accounts, and CRM data.",
            "category": "crm",
            "industries": ["marketing", "ecommerce", "consulting", "general"],
            "connectionType": "oauth",
            "oauthProvider": "salesforce",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/salesforce",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Premium",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "hubspot",
            "name": "HubSpot",
            "description": "Search contacts, manage deals, and access CRM data. Native integration with encrypted credentials.",
            "category": "crm",
            "industries": ["marketing", "saas_tech", "consulting", "general"],
            "connectionType": "integration_oauth",
            "oauthProvider": "hubspot",
            "serverUrl": "__builtin__",
            "docsUrl": "https://developers.hubspot.com/docs/api/overview",
            "badge": "Native",
            "tier": 1,
            "setupFields": [],
        },
        {
            "key": "pipedrive",
            "name": "Pipedrive",
            "description": "Sales pipeline and deal management CRM.",
            "category": "crm",
            "industries": ["real_estate", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/pipedrive",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Pipedrive API Token"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # ANALYTICS
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "google_analytics",
            "name": "Google Analytics",
            "description": "Website traffic and user behavior analytics.",
            "category": "analytics",
            "industries": ["marketing", "ecommerce", "saas_tech", "general"],
            "connectionType": "oauth",
            "oauthProvider": "google",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/googleanalytics",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "mixpanel",
            "name": "Mixpanel",
            "description": "Product analytics and user event tracking.",
            "category": "analytics",
            "industries": ["saas_tech", "ecommerce", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/mixpanel",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Mixpanel API Secret"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # DEVELOPMENT
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "github",
            "name": "GitHub",
            "description": "Official GitHub MCP for repos, issues, pull requests, and code.",
            "category": "development",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://api.githubcopilot.com/mcp/",
            "docsUrl": "https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/set-up-the-github-mcp-server",
            "badge": "Official",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "GitHub Personal Access Token"},
            "setupHelp": {"token": "Create at github.com/settings/tokens"},
        },
        {
            "key": "gitlab",
            "name": "GitLab",
            "description": "Repository management, issues, and CI/CD pipelines.",
            "category": "development",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/gitlab",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "GitLab Personal Access Token"},
        },
        {
            "key": "jira",
            "name": "Jira",
            "description": "Issue tracking and agile project management.",
            "category": "development",
            "industries": ["saas_tech", "consulting", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/jira",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Jira API Token"},
            "setupHelp": {"token": "Create at id.atlassian.com/manage-profile/security/api-tokens"},
        },
        {
            "key": "linear",
            "name": "Linear",
            "description": "Modern issue tracking for software teams.",
            "category": "development",
            "industries": ["saas_tech"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/linear",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Linear API Key"},
        },
        {
            "key": "context7",
            "name": "Context7 Docs",
            "description": "Up-to-date library documentation tools via MCP.",
            "category": "development",
            "industries": ["saas_tech", "general"],
            "connectionType": "none",
            "recommendedAuth": "none",
            "serverUrl": "https://mcp.context7.com/mcp",
            "docsUrl": "https://context7.com/",
            "badge": "Popular",
            "tier": 1,
            "setupFields": [],  # 1-click - no setup needed
        },
        # ═══════════════════════════════════════════════════════════════════════
        # MARKETING
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "google_ads",
            "name": "Google Ads",
            "description": "Campaign management and advertising analytics.",
            "category": "marketing",
            "industries": ["marketing", "ecommerce", "general"],
            "connectionType": "oauth",
            "oauthProvider": "google",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/googleads",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "mailchimp",
            "name": "Mailchimp",
            "description": "Email marketing campaigns and audience management.",
            "category": "marketing",
            "industries": ["marketing", "ecommerce", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/mailchimp",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "Mailchimp API Key"},
            "setupHelp": {"token": "Find at mailchimp.com/account/api"},
        },
        {
            "key": "sendgrid",
            "name": "SendGrid",
            "description": "Transactional and marketing email delivery.",
            "category": "marketing",
            "industries": ["saas_tech", "ecommerce", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/sendgrid",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["token"],
            "setupLabels": {"token": "SendGrid API Key"},
        },
        {
            "key": "linkedin",
            "name": "LinkedIn",
            "description": "Professional network data and posting (read-only for most).",
            "category": "marketing",
            "industries": ["marketing", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/linkedin",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # ECOMMERCE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "shopify",
            "name": "Shopify",
            "description": "E-commerce store management, orders, and products.",
            "category": "ecommerce",
            "industries": ["ecommerce"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/shopify",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": ["token", "store_url"],
            "setupLabels": {"token": "Shopify Access Token", "store_url": "Store URL"},
            "setupHelp": {"store_url": "e.g., mystore.myshopify.com"},
        },
        {
            "key": "stripe",
            "name": "Stripe",
            "description": "Payment processing, subscriptions, and invoices.",
            "category": "ecommerce",
            "industries": ["ecommerce", "saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/stripe",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Stripe Secret Key"},
            "setupHelp": {"token": "Find at dashboard.stripe.com/apikeys"},
        },
        {
            "key": "woocommerce",
            "name": "WooCommerce",
            "description": "WordPress e-commerce store management.",
            "category": "ecommerce",
            "industries": ["ecommerce"],
            "connectionType": "api_key",
            "recommendedAuth": "header",
            "serverUrl": "https://mcp.composio.dev/woocommerce",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["consumer_key", "consumer_secret", "store_url"],
            "setupLabels": {"consumer_key": "Consumer Key", "consumer_secret": "Consumer Secret", "store_url": "Store URL"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # FINANCE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "quickbooks",
            "name": "QuickBooks",
            "description": "Accounting, invoicing, and financial reporting.",
            "category": "finance",
            "industries": ["finance", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/quickbooks",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "xero",
            "name": "Xero",
            "description": "Cloud accounting and bookkeeping.",
            "category": "finance",
            "industries": ["finance", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/xero",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # LEGAL
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "docusign",
            "name": "DocuSign",
            "description": "Electronic signatures and document workflows.",
            "category": "legal",
            "industries": ["legal", "real_estate", "consulting", "general"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/docusign",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 3,
            "setupFields": [],
        },
        {
            "key": "clio",
            "name": "Clio",
            "description": "Legal practice management and case tracking.",
            "category": "legal",
            "industries": ["legal"],
            "connectionType": "oauth",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/clio",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": [],
        },
        # ═══════════════════════════════════════════════════════════════════════
        # REAL ESTATE
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "zillow",
            "name": "Zillow",
            "description": "Property listings and real estate data.",
            "category": "real_estate",
            "industries": ["real_estate"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/zillow",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 3,
            "setupFields": ["token"],
            "setupLabels": {"token": "Zillow API Key"},
        },
        # ═══════════════════════════════════════════════════════════════════════
        # UTILITIES (Universal tools available to all)
        # ═══════════════════════════════════════════════════════════════════════
        {
            "key": "brave_search",
            "name": "Brave Search",
            "description": "Web search with privacy-focused results.",
            "category": "utilities",
            "industries": ["general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/bravesearch",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Popular",
            "tier": 1,
            "setupFields": ["token"],
            "setupLabels": {"token": "Brave Search API Key"},
            "setupHelp": {"token": "Get at brave.com/search/api"},
        },
        {
            "key": "postgres",
            "name": "PostgreSQL",
            "description": "Query and analytics workflows via a Postgres MCP server.",
            "category": "utilities",
            "industries": ["saas_tech", "general"],
            "connectionType": "api_key",
            "recommendedAuth": "bearer",
            "serverUrl": "https://mcp.composio.dev/postgresql",
            "docsUrl": "https://mcp.composio.dev/",
            "badge": "Template",
            "tier": 2,
            "setupFields": ["connection_string"],
            "setupLabels": {"connection_string": "Connection String"},
            "setupHelp": {"connection_string": "postgresql://user:pass@host:5432/db"},
        },
        {
            "key": "excel",
            "name": "Excel / Sheets",
            "description": "Spreadsheet creation and manipulation. Built into your AI.",
            "category": "utilities",
            "industries": ["finance", "consulting", "marketing", "general"],
            "connectionType": "none",
            "recommendedAuth": "none",
            "serverUrl": "__builtin__",
            "docsUrl": "",
            "badge": "Built-in",
            "tier": 1,
            "setupFields": [],  # Built-in, no setup
            "isBuiltIn": True,
        },
        {
            "key": "pdf_tools",
            "name": "PDF Tools",
            "description": "Create, read, and manipulate PDF documents. Built into your AI.",
            "category": "utilities",
            "industries": ["legal", "consulting", "general"],
            "connectionType": "none",
            "recommendedAuth": "none",
            "serverUrl": "__builtin__",
            "docsUrl": "",
            "badge": "Built-in",
            "tier": 1,
            "setupFields": [],  # Built-in, no setup
            "isBuiltIn": True,
        },
    ]


def _get_marketplace_categories() -> list[dict[str, str]]:
    """Return available marketplace categories for filtering."""
    return [
        {"key": "communication", "label": "Communication"},
        {"key": "storage", "label": "Storage"},
        {"key": "productivity", "label": "Productivity"},
        {"key": "crm", "label": "CRM"},
        {"key": "analytics", "label": "Analytics"},
        {"key": "development", "label": "Development"},
        {"key": "marketing", "label": "Marketing"},
        {"key": "ecommerce", "label": "E-commerce"},
        {"key": "finance", "label": "Finance"},
        {"key": "legal", "label": "Legal"},
        {"key": "real_estate", "label": "Real Estate"},
        {"key": "utilities", "label": "Utilities"},
    ]


def _get_industry_display_names() -> dict[str, str]:
    """Map industry keys to display names."""
    return {
        "marketing": "Marketing",
        "ecommerce": "E-commerce",
        "healthcare": "Healthcare",
        "legal": "Legal",
        "real_estate": "Real Estate",
        "saas_tech": "SaaS & Tech",
        "finance": "Finance",
        "consulting": "Consulting",
        "general": "General",
    }


def _filter_marketplace_by_industry(catalog: list[dict[str, Any]], industry_key: str) -> list[dict[str, Any]]:
    """Filter marketplace items that match a given industry."""
    if not industry_key:
        return []

    industry_lower = industry_key.lower().replace(" ", "_").replace("-", "_")

    matched = []
    for item in catalog:
        industries = item.get("industries") or []
        industries_lower = [i.lower() for i in industries]
        if industry_lower in industries_lower or "general" in industries_lower:
            matched.append(item)

    return matched


def _get_common_tools(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tools that are common across all industries (tier 1 or general)."""
    common = []
    for item in catalog:
        tier = item.get("tier", 2)
        industries = item.get("industries") or []
        if tier == 1 or "general" in industries:
            common.append(item)
    return common


def _get_email_accounts_payload(business: BusinessProfile) -> list[dict[str, Any]]:
    """
    Return serialized email accounts for the business.

    These are native first-party email connectors (Gmail/Outlook) that create
    EmailAccount records rather than McpConnection records.
    """
    accounts = EmailAccount.objects.filter(business_profile=business).order_by("email_address")
    from apps.mcp import tools as mcp_tools
    result = []
    for account in accounts:
        # Map provider to marketplace key
        provider = str(account.provider or "").strip().lower()
        if provider == "google":
            marketplace_key = "gmail"
            display_name = "Gmail"
        elif provider == "microsoft":
            marketplace_key = "outlook"
            display_name = "Outlook"
        else:
            marketplace_key = provider
            display_name = provider.title()

        catalog = mcp_tools.get_email_integration_tools_for_provider(provider)
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        enabled_map = mcp_tools.get_email_tool_enabled_map_for_account(account, tool_names=tool_names) if tool_names else {}
        total_tool_count = len(tool_names)
        enabled_tool_count = sum(1 for name in tool_names if bool(enabled_map.get(name, True)))

        result.append({
            "id": str(account.id),
            "type": "email_account",  # Distinguish from MCP connections
            "marketplaceKey": marketplace_key,
            "name": f"{display_name} ({account.email_address})",
            "provider": provider,
            "emailAddress": account.email_address,
            "status": account.status,
            "sendMode": account.send_mode,
            "totalToolCount": total_tool_count,
            "enabledToolCount": enabled_tool_count,
            "lastError": account.last_error or "",
            "lastHealthCheckedAt": account.last_health_checked_at.isoformat() if account.last_health_checked_at else None,
            "createdAt": account.created_at.isoformat() if account.created_at else None,
            "updatedAt": account.updated_at.isoformat() if account.updated_at else None,
        })
    return result


def _get_integration_accounts_payload(business: BusinessProfile) -> list[dict[str, Any]]:
    """
    Return serialized native integration accounts for the business.

    These are first-party integrations (Calendar, Drive, OneDrive, Slack, HubSpot)
    that create IntegrationAccount records.
    """
    accounts = IntegrationAccount.objects.filter(business_profile=business).order_by("integration_type")
    result = []
    from apps.mcp import tools as mcp_tools
    for account in accounts:
        catalog = mcp_tools.get_native_integration_tools_for_type(str(account.integration_type or ""))
        tool_names = [str(row.get("toolName") or "").strip() for row in catalog if str(row.get("toolName") or "").strip()]
        enabled_map = mcp_tools.get_native_tool_enabled_map_for_account(account, tool_names=tool_names) if tool_names else {}
        total_tool_count = len(tool_names)
        enabled_tool_count = sum(1 for name in tool_names if bool(enabled_map.get(name, True)))
        result.append({
            "id": str(account.id),
            "type": "integration_account",
            "integration_type": account.integration_type,
            "provider": account.provider,
            "account_identifier": account.account_identifier,
            "status": account.status,
            "totalToolCount": total_tool_count,
            "enabledToolCount": enabled_tool_count,
            "lastError": account.last_error or "",
            "lastHealthCheckedAt": account.last_health_checked_at.isoformat() if account.last_health_checked_at else None,
            "createdAt": account.created_at.isoformat() if account.created_at else None,
            "updatedAt": account.updated_at.isoformat() if account.updated_at else None,
        })
    return result
