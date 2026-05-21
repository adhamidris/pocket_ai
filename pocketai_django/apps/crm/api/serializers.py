from __future__ import annotations

from apps.crm.models import (
    CrmCompany,
    CrmContact,
    CrmContactCompanyLink,
    CrmDuplicateSuggestion,
    CrmFieldDefinition,
)


def _serialize_owner(owner) -> dict[str, object] | None:
    if owner is None:
        return None
    return {
        "id": str(owner.id),
        "email": owner.email,
        "name": (f"{owner.first_name} {owner.last_name}".strip() or owner.email),
    }


def _serialize_note(note) -> dict[str, object]:
    return {
        "id": str(note.id),
        "body": note.body,
        "author": _serialize_owner(note.author),
        "createdAt": note.created_at.isoformat(),
        "updatedAt": note.updated_at.isoformat(),
    }


def _serialize_activity(activity) -> dict[str, object]:
    return {
        "id": str(activity.id),
        "type": activity.activity_type,
        "actorType": activity.actor_type,
        "actor": _serialize_owner(activity.actor_user),
        "summary": activity.summary,
        "detail": activity.detail,
        "occurredAt": activity.occurred_at.isoformat(),
    }


def _serialize_external_identity(identity) -> dict[str, object]:
    return {
        "id": str(identity.id),
        "recordType": identity.record_type,
        "sourceSystem": identity.source_system,
        "sourceAccountRef": identity.source_account_ref,
        "externalObjectType": identity.external_object_type,
        "externalId": identity.external_id,
        "externalLabel": identity.external_label,
        "lastSeenAt": identity.last_seen_at.isoformat(),
    }


def _serialize_custom_field_value(field_value) -> dict[str, object]:
    definition = field_value.field_definition
    return {
        "definitionId": str(definition.id),
        "key": definition.key,
        "label": definition.label,
        "fieldType": definition.field_type,
        "searchable": definition.searchable,
        "filterable": definition.filterable,
        "pii": definition.pii,
        "value": field_value.value_json,
    }


def _serialize_contact_company_link(link: CrmContactCompanyLink) -> dict[str, object]:
    return {
        "contactId": str(link.contact_id),
        "companyId": str(link.company_id),
        "companyName": link.company.name,
        "relationshipTitle": link.relationship_title,
        "isPrimary": link.is_primary,
        "startedAt": link.started_at.isoformat() if link.started_at else None,
        "endedAt": link.ended_at.isoformat() if link.ended_at else None,
        "metadata": link.metadata,
        "createdAt": link.created_at.isoformat(),
        "updatedAt": link.updated_at.isoformat(),
    }


def _serialize_company_contact_link(link: CrmContactCompanyLink) -> dict[str, object]:
    return {
        "contactId": str(link.contact_id),
        "contactName": link.contact.display_name,
        "relationshipTitle": link.relationship_title,
        "isPrimary": link.is_primary,
        "startedAt": link.started_at.isoformat() if link.started_at else None,
        "endedAt": link.ended_at.isoformat() if link.ended_at else None,
        "metadata": link.metadata,
        "createdAt": link.created_at.isoformat(),
        "updatedAt": link.updated_at.isoformat(),
    }


def _serialize_field_definition(definition: CrmFieldDefinition) -> dict[str, object]:
    return {
        "id": str(definition.id),
        "targetObject": definition.target_object,
        "key": definition.key,
        "label": definition.label,
        "fieldType": definition.field_type,
        "required": definition.required,
        "searchable": definition.searchable,
        "filterable": definition.filterable,
        "pii": definition.pii,
        "archived": definition.archived,
        "options": definition.options,
        "schema": definition.schema,
        "createdAt": definition.created_at.isoformat(),
        "updatedAt": definition.updated_at.isoformat(),
    }


def _serialize_duplicate_suggestion(item: CrmDuplicateSuggestion) -> dict[str, object]:
    return {
        "id": str(item.id),
        "recordType": item.record_type,
        "recordId": str(item.record_id) if item.record_id else None,
        "candidateRecordId": str(item.candidate_record_id),
        "sourceRowNumber": item.source_row_number,
        "incomingSnapshot": item.incoming_snapshot,
        "matchReasons": item.match_reasons,
        "status": item.status,
        "resolutionNote": item.resolution_note,
        "createdAt": item.created_at.isoformat(),
        "resolvedAt": item.resolved_at.isoformat() if item.resolved_at else None,
    }


def _serialize_contact_summary(contact: CrmContact) -> dict[str, object]:
    return {
        "id": str(contact.id),
        "publicId": str(contact.public_id),
        "displayName": contact.display_name,
        "firstName": contact.first_name,
        "lastName": contact.last_name,
        "primaryEmail": contact.primary_email,
        "primaryPhone": contact.primary_phone,
        "title": contact.title,
        "source": contact.source,
        "owner": _serialize_owner(contact.owner),
        "status": contact.status,
        "tags": list(contact.tags or []),
        "companyLinks": [{"companyId": str(link.company_id), "companyName": link.company.name, "isPrimary": link.is_primary} for link in getattr(contact, "company_links", []).all()] if hasattr(getattr(contact, "company_links", None), "all") else [],
        "createdAt": contact.created_at.isoformat(),
        "updatedAt": contact.updated_at.isoformat(),
        "archivedAt": contact.archived_at.isoformat() if contact.archived_at else None,
    }


def _serialize_contact_detail(contact: CrmContact) -> dict[str, object]:
    payload = _serialize_contact_summary(contact)
    payload.update(
        {
            "customFields": [_serialize_custom_field_value(item) for item in contact.field_values.all()],
            "externalIdentities": [_serialize_external_identity(item) for item in contact.external_identities.all()],
            "notes": [_serialize_note(item) for item in contact.notes.all()],
            "activities": [_serialize_activity(item) for item in contact.activities.all()],
            "metadata": contact.metadata,
        }
    )
    return payload


def _serialize_company_summary(company: CrmCompany) -> dict[str, object]:
    return {
        "id": str(company.id),
        "publicId": str(company.public_id),
        "name": company.name,
        "website": company.website,
        "websiteDomain": company.website_domain_normalized,
        "primaryPhone": company.primary_phone,
        "source": company.source,
        "owner": _serialize_owner(company.owner),
        "status": company.status,
        "tags": list(company.tags or []),
        "createdAt": company.created_at.isoformat(),
        "updatedAt": company.updated_at.isoformat(),
        "archivedAt": company.archived_at.isoformat() if company.archived_at else None,
    }


def _serialize_company_detail(company: CrmCompany) -> dict[str, object]:
    payload = _serialize_company_summary(company)
    payload.update(
        {
            "contactLinks": [_serialize_company_contact_link(item) for item in company.contact_links.all()],
            "customFields": [_serialize_custom_field_value(item) for item in company.field_values.all()],
            "externalIdentities": [_serialize_external_identity(item) for item in company.external_identities.all()],
            "notes": [_serialize_note(item) for item in company.notes.all()],
            "activities": [_serialize_activity(item) for item in company.activities.all()],
            "metadata": company.metadata,
        }
    )
    return payload
