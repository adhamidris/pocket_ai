from __future__ import annotations

from django.conf import settings

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import BusinessProfile


def crm_v1_enabled(business_profile: BusinessProfile | None) -> bool:
    """
    Coarse rollout gate for the new standalone CRM runtime.

    - Global setting wins when explicitly set.
    - Otherwise defer to the per-business feature flag payload.
    """

    override = getattr(settings, "CRM_V1_GLOBAL_OVERRIDE", None)
    if isinstance(override, bool):
        return override
    state = FeatureFlagService.snapshot(business_profile)
    return bool(getattr(state, "crm_v1", False))

