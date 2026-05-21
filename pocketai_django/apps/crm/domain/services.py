from __future__ import annotations

from django.db import transaction

from apps.accounts.models import BusinessProfile, User
from apps.crm.domain.companies import *  # noqa: F401,F403
from apps.crm.domain.contacts import *  # noqa: F401,F403
from apps.crm.domain.duplicates import *  # noqa: F401,F403
from apps.crm.domain.fields import *  # noqa: F401,F403
from apps.crm.domain.links import *  # noqa: F401,F403
from apps.crm.domain.shared import *  # noqa: F401,F403
from apps.crm.domain.shared import _log_activity
from apps.crm.models import CrmActivityType, CrmCompany, CrmContact, CrmNote


@transaction.atomic
def add_note(
    *,
    business_profile: BusinessProfile,
    actor: User | None,
    body: str,
    contact: CrmContact | None = None,
    company: CrmCompany | None = None,
) -> CrmNote:
    note = CrmNote.objects.create(
        business_profile=business_profile,
        contact=contact,
        company=company,
        author=actor,
        body=body.strip(),
    )
    _log_activity(business_profile, actor, CrmActivityType.NOTE_ADDED, "Note added", contact=contact, company=company)
    return note
