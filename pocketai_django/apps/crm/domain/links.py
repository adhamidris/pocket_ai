from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import BusinessProfile, User
from apps.crm.domain.shared import (
    _apply_contact_company_link_payload,
    _log_activity,
    _unset_other_primary_links,
    _validate_link_records,
)
from apps.crm.models import CrmActivityType, CrmCompany, CrmContact, CrmContactCompanyLink


@transaction.atomic
def link_contact_to_company(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    contact: CrmContact,
    company: CrmCompany,
    payload: dict[str, Any] | None = None,
) -> CrmContactCompanyLink:
    _validate_link_records(business_profile=business_profile, contact=contact, company=company)
    payload = payload or {}
    link, created = CrmContactCompanyLink.objects.get_or_create(
        business_profile=business_profile,
        contact=contact,
        company=company,
    )
    _apply_contact_company_link_payload(link=link, payload=payload, default_primary=created)
    link.full_clean()
    link.save()
    if link.is_primary:
        _unset_other_primary_links(link)
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.UPDATED if not created else CrmActivityType.CREATED,
        "Contact linked to company",
        contact=contact,
        company=company,
        detail=f"link={link.id}",
    )
    return link


@transaction.atomic
def update_contact_company_link(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    link: CrmContactCompanyLink,
    payload: dict[str, Any],
) -> CrmContactCompanyLink:
    if link.business_profile_id != business_profile.id:
        raise ValidationError("Link belongs to a different business.")
    _apply_contact_company_link_payload(link=link, payload=payload, default_primary=False)
    link.full_clean()
    link.save()
    if link.is_primary:
        _unset_other_primary_links(link)
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.UPDATED,
        "Contact-company link updated",
        contact=link.contact,
        company=link.company,
        detail=f"link={link.id}",
    )
    return link


@transaction.atomic
def unlink_contact_from_company(*, business_profile: BusinessProfile, actor: User | None, link: CrmContactCompanyLink) -> None:
    if link.business_profile_id != business_profile.id:
        raise ValidationError("Link belongs to a different business.")
    contact = link.contact
    company = link.company
    was_primary = link.is_primary
    link.delete()
    if was_primary:
        replacement = (
            CrmContactCompanyLink.objects.filter(business_profile=business_profile, contact=contact)
            .order_by("created_at")
            .first()
        )
        if replacement:
            replacement.is_primary = True
            replacement.save(update_fields=["is_primary", "updated_at"])
    _log_activity(
        business_profile,
        actor,
        CrmActivityType.OTHER,
        "Contact unlinked from company",
        contact=contact,
        company=company,
    )

