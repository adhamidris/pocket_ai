from __future__ import annotations

import re
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import BusinessProfile, User


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _normalize_phone(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _normalize_domain(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    return text.split("/", 1)[0].strip()


class CrmContactStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"
    MERGED = "merged", "Merged"


class CrmCompanyStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"
    MERGED = "merged", "Merged"


class CrmActivityType(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    IMPORTED = "imported", "Imported"
    MERGED = "merged", "Merged"
    NOTE_ADDED = "note_added", "Note Added"
    ARCHIVED = "archived", "Archived"
    RESTORED = "restored", "Restored"
    OTHER = "other", "Other"


class CrmActivityActor(models.TextChoices):
    USER = "user", "User"
    IMPORT = "import", "Import"
    SYSTEM = "system", "System"


class CrmContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_contacts", on_delete=models.CASCADE)
    owner = models.ForeignKey(User, related_name="owned_crm_contacts", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=24, choices=CrmContactStatus.choices, default=CrmContactStatus.ACTIVE)
    source = models.CharField(max_length=64, blank=True, default="manual")
    display_name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    primary_email = models.EmailField(blank=True)
    primary_email_normalized = models.CharField(max_length=254, blank=True, db_index=True)
    primary_phone = models.CharField(max_length=40, blank=True)
    primary_phone_normalized = models.CharField(max_length=40, blank=True, db_index=True)
    title = models.CharField(max_length=120, blank=True)
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "crm_contact"
        ordering = ("display_name", "created_at")
        indexes = [
            models.Index(fields=["business_profile", "status"], name="crm_contact_biz_status_idx"),
            models.Index(fields=["business_profile", "display_name"], name="crm_contact_biz_name_idx"),
            models.Index(fields=["business_profile", "primary_email_normalized"], name="crm_contact_biz_email_idx"),
            models.Index(fields=["business_profile", "primary_phone_normalized"], name="crm_contact_biz_phone_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "primary_email_normalized"],
                condition=~Q(primary_email_normalized=""),
                name="crm_contact_unique_email_per_biz",
            ),
        ]

    def save(self, *args, **kwargs):
        self.primary_email_normalized = _normalize_email(self.primary_email)
        self.primary_phone_normalized = _normalize_phone(self.primary_phone)
        if self.status == CrmContactStatus.ARCHIVED and not self.archived_at:
            self.archived_at = timezone.now()
        elif self.status != CrmContactStatus.ARCHIVED:
            self.archived_at = None
        super().save(*args, **kwargs)


class CrmCompany(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_companies", on_delete=models.CASCADE)
    owner = models.ForeignKey(User, related_name="owned_crm_companies", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=24, choices=CrmCompanyStatus.choices, default=CrmCompanyStatus.ACTIVE)
    source = models.CharField(max_length=64, blank=True, default="manual")
    name = models.CharField(max_length=255)
    website = models.URLField(blank=True)
    website_domain_normalized = models.CharField(max_length=255, blank=True, db_index=True)
    primary_phone = models.CharField(max_length=40, blank=True)
    primary_phone_normalized = models.CharField(max_length=40, blank=True, db_index=True)
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "crm_company"
        ordering = ("name", "created_at")
        indexes = [
            models.Index(fields=["business_profile", "status"], name="crm_company_biz_status_idx"),
            models.Index(fields=["business_profile", "name"], name="crm_company_biz_name_idx"),
            models.Index(fields=["business_profile", "website_domain_normalized"], name="crm_company_biz_domain_idx"),
        ]

    def save(self, *args, **kwargs):
        self.website_domain_normalized = _normalize_domain(self.website)
        self.primary_phone_normalized = _normalize_phone(self.primary_phone)
        if self.status == CrmCompanyStatus.ARCHIVED and not self.archived_at:
            self.archived_at = timezone.now()
        elif self.status != CrmCompanyStatus.ARCHIVED:
            self.archived_at = None
        super().save(*args, **kwargs)


class CrmContactCompanyLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_contact_company_links", on_delete=models.CASCADE)
    contact = models.ForeignKey(CrmContact, related_name="company_links", on_delete=models.CASCADE)
    company = models.ForeignKey(CrmCompany, related_name="contact_links", on_delete=models.CASCADE)
    relationship_title = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    started_at = models.DateField(null=True, blank=True)
    ended_at = models.DateField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_contact_company_link"
        ordering = ("-is_primary", "created_at")
        constraints = [
            models.UniqueConstraint(fields=["contact", "company"], name="crm_contact_company_unique"),
        ]
        indexes = [
            models.Index(fields=["business_profile", "is_primary"], name="crm_ctco_biz_primary_idx"),
        ]


class CrmExternalIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_external_identities", on_delete=models.CASCADE)
    record_type = models.CharField(max_length=16, choices=(("contact", "Contact"), ("company", "Company")))
    contact = models.ForeignKey(CrmContact, related_name="external_identities", null=True, blank=True, on_delete=models.CASCADE)
    company = models.ForeignKey(CrmCompany, related_name="external_identities", null=True, blank=True, on_delete=models.CASCADE)
    source_system = models.CharField(max_length=64)
    source_account_ref = models.CharField(max_length=128, blank=True)
    external_object_type = models.CharField(max_length=64, blank=True, default="record")
    external_id = models.CharField(max_length=255)
    external_label = models.CharField(max_length=255, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_external_identity"
        ordering = ("source_system", "external_object_type", "external_id")
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "source_system", "source_account_ref", "external_object_type", "external_id"],
                name="crm_external_identity_unique_per_source",
            ),
        ]
        indexes = [
            models.Index(fields=["business_profile", "record_type"], name="crm_extid_biz_type_idx"),
            models.Index(fields=["contact", "source_system"], name="crm_extid_contact_idx"),
            models.Index(fields=["company", "source_system"], name="crm_extid_company_idx"),
        ]

    def clean(self):
        if self.record_type == "contact" and not self.contact_id:
            raise ValidationError("Contact identity requires contact_id")
        if self.record_type == "company" and not self.company_id:
            raise ValidationError("Company identity requires company_id")


class CrmNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_notes", on_delete=models.CASCADE)
    contact = models.ForeignKey(CrmContact, related_name="notes", null=True, blank=True, on_delete=models.CASCADE)
    company = models.ForeignKey(CrmCompany, related_name="notes", null=True, blank=True, on_delete=models.CASCADE)
    author = models.ForeignKey(User, related_name="crm_notes_authored", null=True, blank=True, on_delete=models.SET_NULL)
    body = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_note"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "created_at"], name="crm_note_biz_created_idx"),
            models.Index(fields=["contact", "created_at"], name="crm_note_contact_created_idx"),
            models.Index(fields=["company", "created_at"], name="crm_note_company_created_idx"),
        ]


class CrmActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(BusinessProfile, related_name="crm_activities", on_delete=models.CASCADE)
    contact = models.ForeignKey(CrmContact, related_name="activities", null=True, blank=True, on_delete=models.CASCADE)
    company = models.ForeignKey(CrmCompany, related_name="activities", null=True, blank=True, on_delete=models.CASCADE)
    activity_type = models.CharField(max_length=32, choices=CrmActivityType.choices, default=CrmActivityType.OTHER)
    actor_type = models.CharField(max_length=32, choices=CrmActivityActor.choices, default=CrmActivityActor.SYSTEM)
    actor_user = models.ForeignKey(User, related_name="crm_activities", null=True, blank=True, on_delete=models.SET_NULL)
    summary = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_activity"
        ordering = ("-occurred_at", "-created_at")
        indexes = [
            models.Index(fields=["business_profile", "occurred_at"], name="crm_activity_biz_occ_idx"),
            models.Index(fields=["contact", "occurred_at"], name="crm_activity_contact_occ_idx"),
            models.Index(fields=["company", "occurred_at"], name="crm_activity_company_occ_idx"),
        ]
