from __future__ import annotations

from apps.api.agents.endpoints import (
    agent_capabilities_view,
    agent_detail_view,
    agent_knowledge_access_view,
    agents_collection,
    agents_directory,
)
from apps.api.knowledge_documents.endpoints import (
    knowledge_document_detail,
    knowledge_document_download,
    knowledge_document_preview_csv,
    knowledge_document_scrape,
    knowledge_document_status,
    knowledge_documents_collection,
)
from apps.api.integrations.endpoints import (
    google_drive_oauth_callback,
    google_drive_resources,
    google_drive_save_resources,
    google_drive_sync_now,
    integration_sheets_collection,
    integrations_collection,
    start_google_drive_oauth,
)
from apps.api.registration.endpoints import (
    configure_agent,
    finalize_uploads,
    placeholder,
    start_registration,
    update_business_profile,
)
