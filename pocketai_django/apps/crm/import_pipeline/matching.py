from __future__ import annotations

from typing import Any

from apps.accounts.models import BusinessProfile
from apps.crm.models import CrmCompany, CrmContact, CrmExternalIdentity, CrmRecordType


def _match_contact(
    *,
    business_profile: BusinessProfile,
    payload: dict[str, Any],
    external_identity: dict[str, str] | None = None,
) -> tuple[CrmContact | None, CrmContact | None]:
    if external_identity:
        identity_match = (
            CrmExternalIdentity.objects.filter(
                business_profile=business_profile,
                record_type=CrmRecordType.CONTACT,
                source_system=external_identity["source_system"],
                source_account_ref=external_identity["source_account_ref"],
                external_object_type=external_identity["external_object_type"],
                external_id=external_identity["external_id"],
                contact__isnull=False,
            )
            .select_related("contact")
            .first()
        )
        if identity_match and identity_match.contact:
            return identity_match.contact, None
    email = (payload.get("primary_email") or "").strip().lower()
    phone = "".join(ch for ch in str(payload.get("primary_phone") or "") if ch.isdigit())
    if email:
        match = CrmContact.objects.filter(business_profile=business_profile, primary_email_normalized=email).first()
        if match:
            return None, match
    if phone:
        match = CrmContact.objects.filter(business_profile=business_profile, primary_phone_normalized=phone).first()
        if match:
            return None, match
    return None, None


def _match_company(*, business_profile: BusinessProfile, payload: dict[str, Any]) -> CrmCompany | None:
    name = (payload.get("name") or "").strip()
    website = (payload.get("website") or "").strip().lower()
    if website:
        match = CrmCompany.objects.filter(business_profile=business_profile, website_domain_normalized__icontains=website.split("//")[-1].split("/")[0]).first()
        if match:
            return match
    if name:
        return CrmCompany.objects.filter(business_profile=business_profile, name__iexact=name).first()
    return None


def _attach_company(*, contact: CrmContact, company: CrmCompany) -> None:
    from apps.crm.models import CrmContactCompanyLink

    CrmContactCompanyLink.objects.get_or_create(
        business_profile=contact.business_profile,
        contact=contact,
        company=company,
        defaults={"is_primary": True},
    )


def _external_identity_payload(mapping: dict[str, Any], row: dict[str, Any]) -> dict[str, str] | None:
    identity = mapping.get("external_identity")
    if not isinstance(identity, dict):
        return None
    source_column = identity.get("source_column")
    if not source_column:
        return None
    external_id = str(row.get(source_column) or "").strip()
    if not external_id:
        return None
    return {
        "source_system": str(identity.get("source_system") or "import").strip() or "import",
        "source_account_ref": str(identity.get("source_account_ref") or "").strip(),
        "external_object_type": str(identity.get("external_object_type") or "contact").strip() or "contact",
        "external_id": external_id,
        "external_label": str(identity.get("external_label") or source_column).strip(),
    }
