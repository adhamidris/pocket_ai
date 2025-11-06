from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.db import models
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile, User


class CustomerRecordOrigin(models.TextChoices):
    """Source channel describing how the customer was created."""

    AI_EXTRACTED = "ai_extracted", "AI Extracted"
    MANUAL = "manual", "Manual Entry"
    IMPORT_CSV = "import_csv", "CSV Import"
    IMPORT_CONTACTS = "import_contacts", "Contacts Import"
    SYSTEM = "system", "System"
    UNKNOWN = "unknown", "Unknown"


class CustomerRecordState(models.TextChoices):
    """High level lifecycle state of the customer record."""

    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"
    PLACEHOLDER = "placeholder", "Placeholder"


class CustomerManager(models.Manager["Customer"]):
    def create_placeholder(
        self,
        *,
        business_profile: BusinessProfile,
        agent_profile: AgentProfile | None = None,
        label: str = "Unidentified Customer",
        metadata: dict[str, Any] | None = None,
        placeholder_key: str = "default",
    ) -> Customer:
        """
        Return a placeholder customer record that cases can attach to when the AI
        cannot resolve an identity.

        Ensures a single placeholder per `(business_profile, placeholder_key)` tuple.
        """

        defaults = {
            "display_name": label,
            "record_origin": CustomerRecordOrigin.SYSTEM,
            "record_state": CustomerRecordState.PLACEHOLDER,
            "agent_profile": agent_profile,
            "metadata": metadata or {},
            "is_placeholder": True,
        }
        customer, _created = self.get_or_create(
            business_profile=business_profile,
            placeholder_key=placeholder_key,
            defaults=defaults,
        )
        return customer


class Customer(models.Model):
    """
    Stores customer identity and primary contact information for a business.

    Records can originate from AI extraction, manual creation, or CSV/contact imports.
    Placeholder entries allow cases to exist without confirmed customer attribution.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="customers",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="customers",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_by = models.ForeignKey(
        User,
        related_name="customers_created",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    display_name = models.CharField(max_length=255)
    given_name = models.CharField(max_length=120, blank=True)
    family_name = models.CharField(max_length=120, blank=True)
    primary_email = models.EmailField(blank=True)
    primary_phone = models.CharField(max_length=40, blank=True)
    primary_address = models.JSONField(default=dict, blank=True)
    record_origin = models.CharField(max_length=32, choices=CustomerRecordOrigin.choices, default=CustomerRecordOrigin.UNKNOWN)
    record_state = models.CharField(
        max_length=32,
        choices=CustomerRecordState.choices,
        default=CustomerRecordState.ACTIVE,
    )
    is_placeholder = models.BooleanField(default=False)
    placeholder_key = models.CharField(
        max_length=64,
        blank=True,
        help_text="Identifier for grouping placeholder customers per business (e.g., 'default').",
    )
    first_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_interaction_at = models.DateTimeField(null=True, blank=True, db_index=True)
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CustomerManager()

    class Meta:
        db_table = "customers_customer"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "record_state"], name="customer_business_state_idx"),
            models.Index(fields=["business_profile", "display_name"], name="customer_business_name_idx"),
            models.Index(fields=["public_id"], name="customer_public_id_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "placeholder_key"],
                condition=~models.Q(placeholder_key=""),
                name="customer_unique_placeholder",
            )
        ]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.business_profile.name})"

    def mark_interaction(self, *, timestamp: datetime | None = None) -> None:
        """Update the last interaction timestamp for analytics surfaces."""

        ts = timestamp or timezone.now()
        self.last_interaction_at = ts
        self.save(update_fields=["last_interaction_at", "updated_at"])


class CustomerContactType(models.TextChoices):
    """Supported contact channels."""

    EMAIL = "email", "Email"
    PHONE = "phone", "Phone"
    ADDRESS = "address", "Address"
    SOCIAL = "social", "Social"
    MESSAGING = "messaging", "Messaging"
    OTHER = "other", "Other"


class CustomerContactPoint(models.Model):
    """
    Represents a single contact method (email, phone, address, etc.) for a customer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="contact_points", on_delete=models.CASCADE)
    contact_type = models.CharField(max_length=24, choices=CustomerContactType.choices)
    label = models.CharField(max_length=64, blank=True)
    value = models.CharField(max_length=255, blank=True)
    normalized_value = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customers_contact_point"
        ordering = ("contact_type", "label")
        indexes = [
            models.Index(fields=["customer", "contact_type"], name="contact_customer_type_idx"),
            models.Index(fields=["normalized_value"], name="contact_normalized_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.customer.display_name}:{self.contact_type}:{self.value}"


class CustomerActivityType(models.TextChoices):
    """Enumeration of CRM activity kinds."""

    MESSAGE = "message", "Message"
    TASK = "task", "Task"
    NOTE = "note", "Note"
    STATUS_CHANGE = "status_change", "Status Change"
    WORKFLOW = "workflow", "Workflow"
    CASE_UPDATE = "case_update", "Case Update"
    OTHER = "other", "Other"


class CustomerActivityActor(models.TextChoices):
    """Identifies who performed the activity."""

    CUSTOMER = "customer", "Customer"
    AI_AGENT = "ai_agent", "AI Agent"
    BUSINESS = "business", "Business Owner"
    SYSTEM = "system", "System"


class CustomerActivity(models.Model):
    """
    Auditable CRM item tracking anything done by, or on behalf of, the customer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="activities", on_delete=models.CASCADE)
    case = models.ForeignKey(
        "cases.Case",
        related_name="customer_activities",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="customer_activities",
        on_delete=models.CASCADE,
    )
    agent_profile = models.ForeignKey(
        AgentProfile,
        related_name="customer_activities",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_type = models.CharField(max_length=32, choices=CustomerActivityActor.choices)
    activity_type = models.CharField(max_length=32, choices=CustomerActivityType.choices, default=CustomerActivityType.OTHER)
    session_reference = models.CharField(max_length=64, blank=True)
    subject = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customers_activity"
        ordering = ("-occurred_at", "-created_at")
        indexes = [
            models.Index(fields=["customer", "occurred_at"], name="activity_customer_time_idx"),
            models.Index(fields=["case", "occurred_at"], name="activity_case_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.customer.display_name} - {self.subject}"


class CustomerNoteAuthor(models.TextChoices):
    """Restrict note authorship to customer-facing entities."""

    CUSTOMER = "customer", "Customer"
    AI_AGENT = "ai_agent", "AI Agent"


class CustomerNote(models.Model):
    """
    Free-form note associated with a customer, authored by the customer or the AI agent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, related_name="notes", on_delete=models.CASCADE)
    case = models.ForeignKey(
        "cases.Case",
        related_name="customer_notes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    author_type = models.CharField(max_length=32, choices=CustomerNoteAuthor.choices)
    content = models.TextField()
    is_pinned = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customers_note"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["customer", "created_at"], name="note_customer_time_idx"),
            models.Index(fields=["case", "created_at"], name="note_case_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.customer.display_name} note"
