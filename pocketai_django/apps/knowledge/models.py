from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from pgvector.django import VectorField

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeAuditAction,
    KnowledgeBlockType,
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeIssueSeverity,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeVisibility,
    User,
)


class KnowledgeUpload(models.Model):
    """
    Central knowledge artifact powering the AI agent experience.

    Supports uploads, URLs, manual snippets, and integration-sourced content while
    tracking ingestion state and access metadata. When the
    source type is ``integration`` the ``source_uid`` tracks the integration
    resource id (e.g., a Drive file + sheet gid) so sync jobs can upsert rows
    deterministically.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_uploads",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(User, related_name="knowledge_uploads", on_delete=models.CASCADE)
    created_by_agent = models.ForeignKey(
        AgentProfile,
        related_name="knowledge_contributions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    integration = models.ForeignKey(
        "integrations.KnowledgeIntegration",
        related_name="uploads",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    display_name = models.CharField(max_length=255, blank=True, default="")
    slug = models.SlugField(max_length=160, blank=True, db_index=True, default="")
    description = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")
    source_name = models.CharField(max_length=255, blank=True, default="")
    source_uid = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Identifier for deduping integration resources (e.g., drive_file:sheet_gid).",
    )
    external_reference = models.CharField(max_length=255, blank=True, default="")
    legacy_url = models.URLField(blank=True, default="")
    source_type = models.CharField(
        max_length=32,
        choices=KnowledgeSourceType.choices,
        default=KnowledgeSourceType.FILE,
        db_column="resource_type",
    )
    status = models.CharField(max_length=32, choices=KnowledgeStatus.choices, default=KnowledgeStatus.PENDING)
    visibility = models.CharField(max_length=32, choices=KnowledgeVisibility.choices, default=KnowledgeVisibility.PRIVATE)
    language = models.CharField(max_length=32, blank=True, default="")
    category = models.CharField(max_length=64, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ingestion_metadata = models.JSONField(default=dict, blank=True)
    retention_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional rules for expiry or redaction.",
    )
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    token_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    is_sensitive = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    last_ingested_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    ingestion_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="upload_business_status_idx"),
            models.Index(fields=["business_profile", "source_type"], name="upload_business_source_idx"),
            models.Index(fields=["business_profile", "slug"], name="upload_business_slug_idx"),
            models.Index(fields=["integration", "status"], name="upload_integration_status_idx"),
            GinIndex(
                fields=["display_name"],
                name="upload_display_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "slug"],
                condition=~models.Q(slug=""),
                name="knowledge_unique_business_slug",
            )
        ]

    def __str__(self) -> str:
        label = self.display_name or self.source_name or self.external_reference or str(self.id)
        return f"{label} ({self.get_source_type_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.display_name:
            self.display_name = self.source_name or self.external_reference or self.legacy_url or "Knowledge Item"

        if not self.slug:
            base_slug = slugify(self.display_name) or "knowledge"
            candidate = base_slug
            suffix = 1
            while KnowledgeUpload.objects.filter(
                business_profile=self.business_profile,
                slug=candidate,
            ).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base_slug}-{suffix}"
            self.slug = candidate

        super().save(*args, **kwargs)


class KnowledgeUploadFile(models.Model):
    """
    File metadata for a document-type knowledge upload.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(
        KnowledgeUpload,
        related_name="file_detail",
        on_delete=models.CASCADE,
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True, default="")
    storage_path = models.CharField(max_length=512)
    size_bytes = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_file"

    def __str__(self) -> str:
        return f"{self.filename} ({self.content_type or 'unknown'})"


class KnowledgeUploadUrl(models.Model):
    """
    Captures external URL references saved into the knowledge base.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(
        KnowledgeUpload,
        related_name="url_detail",
        on_delete=models.CASCADE,
    )
    url = models.URLField()
    normalized_host = models.CharField(max_length=120, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_url"

    def __str__(self) -> str:
        return self.url


class KnowledgeUploadText(models.Model):
    """
    Stores manual snippets or playbooks entered directly via the dashboard.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(
        KnowledgeUpload,
        related_name="text_detail",
        on_delete=models.CASCADE,
    )
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_text"

    def __str__(self) -> str:
        return f"Text snippet for {self.upload}"


class KnowledgeUploadChunk(models.Model):
    """
    Normalized chunk of extracted knowledge text for search + embedding retrieval.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="chunks",
        on_delete=models.CASCADE,
    )
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_chunks",
        on_delete=models.CASCADE,
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    embedding = VectorField(dimensions=settings.EMBED_DIM, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_chunk"
        ordering = ("upload_id", "chunk_index")
        indexes = [
            models.Index(fields=["upload", "chunk_index"], name="knowledge_chunk_window_idx"),
            models.Index(fields=["business_profile", "chunk_index"], name="kn_chunk_biz_idx"),
            models.Index(fields=["business_profile", "upload"], name="kn_chunk_biz_upload_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "upload", "chunk_index"],
                name="knowledge_chunk_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} for {self.upload_id}"

    def save(self, *args, **kwargs):
        if self.upload_id and not self.business_profile_id and getattr(self, "upload", None):
            self.business_profile = self.upload.business_profile
        super().save(*args, **kwargs)


class KnowledgeUploadShadowChunk(models.Model):
    """
    Shadow copy of knowledge chunks for evaluation or alternate retrieval paths.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="shadow_chunks",
        on_delete=models.CASCADE,
    )
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_shadow_chunks",
        on_delete=models.CASCADE,
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    embedding = VectorField(dimensions=settings.EMBED_DIM, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_shadow_chunk"
        ordering = ("upload_id", "chunk_index")
        indexes = [
            models.Index(fields=["upload", "chunk_index"], name="kn_shadow_chunk_window_idx"),
            models.Index(fields=["business_profile", "chunk_index"], name="kn_shadow_biz_idx"),
            models.Index(fields=["business_profile", "upload"], name="kn_shadow_biz_upload_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business_profile", "upload", "chunk_index"],
                name="knowledge_shadow_chunk_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Shadow chunk {self.chunk_index} for {self.upload_id}"

    def save(self, *args, **kwargs):
        if self.upload_id and not self.business_profile_id and getattr(self, "upload", None):
            self.business_profile = self.upload.business_profile
        super().save(*args, **kwargs)


class KnowledgeEntity(models.Model):
    """
    Structured entity detected during ingestion (primarily from JSON sources).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_entities",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="entities",
        on_delete=models.CASCADE,
    )
    chunk_id = models.UUIDField(null=True, blank=True, db_index=True)
    entity_type = models.CharField(max_length=120, blank=True, default="")
    entity_name = models.CharField(max_length=255, blank=True, default="")
    primary_label = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_entity"
        indexes = [
            models.Index(fields=["business_profile", "entity_type"], name="knowledge_entity_type_idx"),
            models.Index(fields=["upload"], name="knowledge_entity_upload_idx"),
        ]

    def __str__(self) -> str:
        label = self.entity_name or self.primary_label or str(self.id)
        return f"{label} ({self.entity_type or 'entity'})"


class KnowledgeAlias(models.Model):
    """
    Normalized alias/identifier tied to a structured entity for deterministic lookups.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_aliases",
        on_delete=models.CASCADE,
    )
    entity = models.ForeignKey(
        KnowledgeEntity,
        related_name="aliases",
        on_delete=models.CASCADE,
    )
    alias_raw = models.CharField(max_length=255)
    alias_normalized = models.CharField(max_length=255, db_index=True)
    alias_search_vector = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=60, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_alias"
        indexes = [
            models.Index(fields=["business_profile", "alias_normalized"], name="kn_alias_biz_norm_idx"),
            models.Index(
                fields=["alias_normalized"],
                name="kn_alias_norm_len_idx",
                condition=models.Q(alias_normalized__regex=r".{5,}"),
            ),
            GinIndex(
                fields=["alias_normalized"],
                name="kn_alias_norm_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["alias_search_vector"],
                name="knowledge_alias_search_gin",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "alias_normalized"],
                name="knowledge_alias_unique_entity_alias",
            )
        ]

    def __str__(self) -> str:
        return self.alias_raw



class KnowledgeUploadPage(models.Model):
    """
    Captures per-page layout, measurements, and extraction metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="pages",
        on_delete=models.CASCADE,
    )
    page_number = models.PositiveIntegerField()
    width = models.FloatField(default=0.0)
    height = models.FloatField(default=0.0)
    rotation = models.IntegerField(default=0)
    text_density = models.FloatField(default=0.0)
    has_ocr_content = models.BooleanField(default=False)
    content_type = models.CharField(max_length=100, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_page"
        ordering = ("upload_id", "page_number")
        indexes = [
            models.Index(fields=["upload", "page_number"], name="knowledge_page_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "page_number"],
                name="knowledge_page_unique_number",
            )
        ]

    def __str__(self) -> str:
        return f"Page {self.page_number} ({self.upload_id})"


class KnowledgeUploadPageBlock(models.Model):
    """
    Stores layout-aware text/image/table blocks with bounding box provenance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="page_blocks",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        KnowledgeUploadPage,
        related_name="blocks",
        on_delete=models.CASCADE,
    )
    block_type = models.CharField(
        max_length=32,
        choices=KnowledgeBlockType.choices,
        default=KnowledgeBlockType.PARAGRAPH,
    )
    order_index = models.PositiveIntegerField(default=0)
    text = models.TextField(blank=True, default="")
    bbox = models.JSONField(default=dict, blank=True)
    section_heading = models.CharField(max_length=255, blank=True, default="")
    heading_path = models.JSONField(default=list, blank=True)
    detected_language = models.CharField(max_length=32, blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_page_block"
        ordering = ("page_id", "order_index")
        indexes = [
            models.Index(fields=["upload", "block_type"], name="knowledge_block_type_idx"),
            models.Index(fields=["page", "block_type"], name="knowledge_block_page_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "order_index"],
                name="knowledge_block_unique_order",
            )
        ]

    def __str__(self) -> str:
        return f"Block {self.order_index} ({self.block_type}) on page {self.page_id}"


class KnowledgeUploadTable(models.Model):
    """
    Normalized representation of detected tables with provenance metadata.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="tables",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        KnowledgeUploadPage,
        related_name="tables",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    source_block = models.ForeignKey(
        KnowledgeUploadPageBlock,
        related_name="tables",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255, blank=True, default="")
    section_heading = models.CharField(max_length=255, blank=True, default="")
    order_index = models.PositiveIntegerField(default=0)
    bbox = models.JSONField(default=dict, blank=True)
    column_schema = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered schema describing each detected column.",
    )
    data_dictionary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional metadata describing column semantics/normalization.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_table"
        ordering = ("upload_id", "order_index")
        indexes = [
            models.Index(fields=["upload", "order_index"], name="knowledge_table_upload_idx"),
            models.Index(fields=["page", "order_index"], name="knowledge_table_page_idx"),
        ]

    def __str__(self) -> str:
        return f"Table {self.order_index} for {self.upload_id}"


class KnowledgeTableColumn(models.Model):
    """
    Indexed representation of table column headers for semantic search.
    
    Enables column-header search queries like "what columns are available"
    or semantic matching of column names to query tokens (e.g. "annual fee"
    matching column "Annual Fee (EGP)").
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="columns",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="table_columns",
        on_delete=models.CASCADE,
    )
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="table_columns",
        on_delete=models.CASCADE,
    )
    column_index = models.PositiveIntegerField()
    column_name = models.CharField(max_length=255)
    column_normalized = models.CharField(max_length=255, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_table_column"
        ordering = ("table_id", "column_index")
        indexes = [
            models.Index(
                fields=["business_profile", "column_normalized"],
                name="knowledge_table_col_biz_idx",
            ),
            GinIndex(
                fields=["column_normalized"],
                name="knowledge_table_col_norm_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["column_name"],
                name="knowledge_table_col_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["table", "column_index"],
                name="knowledge_table_column_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Column {self.column_index}: {self.column_name} (table {self.table_id})"


class KnowledgeUploadTableRow(models.Model):
    """
    Row-level representation to retain positional accuracy and provenance.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="rows",
        on_delete=models.CASCADE,
    )
    row_index = models.PositiveIntegerField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    bbox = models.JSONField(default=dict, blank=True)
    raw_text = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_table_row"
        ordering = ("table_id", "row_index")
        constraints = [
            models.UniqueConstraint(
                fields=["table", "row_index"],
                name="knowledge_table_row_unique_index",
            )
        ]

    def __str__(self) -> str:
        return f"Row {self.row_index} for table {self.table_id}"


class KnowledgeUploadTableCell(models.Model):
    """
    Cell-level storage for both raw and normalized values plus coordinates.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="cells",
        on_delete=models.CASCADE,
    )
    row = models.ForeignKey(
        KnowledgeUploadTableRow,
        related_name="cells",
        on_delete=models.CASCADE,
    )
    column_index = models.PositiveIntegerField()
    column_key = models.CharField(max_length=160, blank=True, default="")
    raw_text = models.TextField(blank=True, default="")
    normalized_value = models.JSONField(
        default=dict,
        blank=True,
        help_text="Parsed/typed representation (e.g., amount, currency).",
    )
    bbox = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_table_cell"
        ordering = ("table_id", "row_id", "column_index")
        indexes = [
            models.Index(fields=["table", "column_index"], name="knowledge_cell_column_idx"),
            models.Index(fields=["column_key"], name="knowledge_cell_column_key_idx"),
            GinIndex(
                fields=["raw_text"],
                name="knowledge_cell_raw_text_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["row", "column_index"],
                name="knowledge_cell_unique_row_column",
            )
        ]

    def __str__(self) -> str:
        return f"Cell r{self.row_id}-c{self.column_index}"


class KnowledgeUploadIssue(models.Model):
    """
    Structured issues log connected back to uploads, pages, and table artifacts.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="issues",
        on_delete=models.CASCADE,
    )
    page = models.ForeignKey(
        KnowledgeUploadPage,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    table = models.ForeignKey(
        KnowledgeUploadTable,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    table_row = models.ForeignKey(
        KnowledgeUploadTableRow,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    table_cell = models.ForeignKey(
        KnowledgeUploadTableCell,
        related_name="issues",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    issue_code = models.CharField(max_length=120)
    severity = models.CharField(
        max_length=16,
        choices=KnowledgeIssueSeverity.choices,
        default=KnowledgeIssueSeverity.INFO,
    )
    description = models.TextField(blank=True, default="")
    detected_by = models.CharField(max_length=64, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_upload_issue"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["upload", "severity"], name="knowledge_issue_severity_idx"),
            models.Index(fields=["table", "issue_code"], name="knowledge_issue_table_idx"),
        ]

    def __str__(self) -> str:
        return f"Issue {self.issue_code} ({self.severity}) for upload {self.upload_id}"


class KnowledgeIngestionJob(models.Model):
    """
    Tracks asynchronous ingestion, syncing, and re-index tasks for knowledge uploads.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_jobs",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="ingestion_jobs",
        on_delete=models.CASCADE,
    )
    job_type = models.CharField(max_length=24, choices=KnowledgeIngestionJobType.choices)
    status = models.CharField(max_length=24, choices=KnowledgeIngestionJobStatus.choices, default=KnowledgeIngestionJobStatus.QUEUED)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    run_after = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_ingestion_job"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "status"], name="knowledge_job_status_idx"),
            models.Index(fields=["upload", "job_type"], name="knowledge_job_type_idx"),
            models.Index(fields=["status", "run_after"], name="knowledge_job_run_after_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="knowledge_job_lease_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_job_type_display()} for {self.upload}"


class KnowledgeDriftSample(models.Model):
    class SampleKind(models.TextChoices):
        INGESTION = "ingestion", "Ingestion"
        RETRIEVAL = "retrieval", "Retrieval"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="drift_samples",
        on_delete=models.CASCADE,
    )
    sample_kind = models.CharField(max_length=24, choices=SampleKind.choices)
    metrics = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_knowledge_drift_sample"
        ordering = ("-observed_at",)
        indexes = [
            models.Index(fields=["business_profile", "sample_kind", "observed_at"], name="knowledge_drift_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.business_profile_id}:{self.sample_kind} at {self.observed_at:%Y-%m-%d %H:%M}"


class RAGEvaluationRun(models.Model):
    class RunStatus(models.TextChoices):
        PASSED = "pass", "Pass"
        FAILED = "fail", "Fail"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="evaluation_runs",
        on_delete=models.CASCADE,
    )
    slug = models.CharField(max_length=64)
    status = models.CharField(max_length=8, choices=RunStatus.choices, default=RunStatus.PASSED)
    metrics = models.JSONField(default=dict, blank=True)
    latencies = models.JSONField(default=dict, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)
    violations = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_rag_evaluation_run"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "slug"], name="rag_eval_business_slug_idx"),
            models.Index(fields=["status", "created_at"], name="rag_eval_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.slug} ({self.get_status_display()})"


class KnowledgeFeedbackCase(models.Model):
    class BehaviorChoices(models.TextChoices):
        ALIAS = "alias_exact", "Alias Path"
        HYBRID = "hybrid", "Hybrid Search"
        NOT_FOUND = "not_found", "Not Found"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="feedback_cases",
        on_delete=models.CASCADE,
    )
    conversation_feedback = models.OneToOneField(
        "conversations.ConversationFeedback",
        related_name="knowledge_case",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    query_text = models.TextField()
    expected_behavior = models.CharField(max_length=24, choices=BehaviorChoices.choices, default=BehaviorChoices.ALIAS)
    expected_entities = models.JSONField(default=list, blank=True)
    expected_aliases = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=32, default="feedback")
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_knowledge_feedback_case"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["business_profile", "is_active"], name="knowledge_feedback_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.business_profile_id}:{self.query_text[:40]}"


class KnowledgeAuditEvent(models.Model):
    """
    Immutable log of key knowledge events for compliance and debugging.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_profile = models.ForeignKey(
        BusinessProfile,
        related_name="knowledge_audit_events",
        on_delete=models.CASCADE,
    )
    upload = models.ForeignKey(
        KnowledgeUpload,
        related_name="audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    upload_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Snapshot of the upload UUID for retention when the upload is deleted.",
    )
    actor_user = models.ForeignKey(
        User,
        related_name="knowledge_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_agent = models.ForeignKey(
        AgentProfile,
        related_name="knowledge_audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=32, choices=KnowledgeAuditAction.choices)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_knowledge_audit_event"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["upload", "action"], name="knowledge_audit_action_idx"),
            models.Index(fields=["upload_id_snapshot", "action"], name="kn_audit_upid_action_idx"),
        ]

    def __str__(self) -> str:
        upload_ref = self.upload or self.upload_id_snapshot or "unknown-upload"
        return f"{upload_ref} - {self.get_action_display()}"


class AgentKnowledgeAccess(models.Model):
    """
    Through model that tracks explicit knowledge resources granted to an agent.

    Enables fine-grained permissions and auditing for document usage.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_profile = models.ForeignKey(
        "accounts.AgentProfile",
        related_name="knowledge_access_rules",
        on_delete=models.CASCADE,
    )
    knowledge_upload = models.ForeignKey(
        "KnowledgeUpload",
        related_name="agent_access_rules",
        on_delete=models.CASCADE,
    )
    granted_by = models.ForeignKey(
        User,
        related_name="agent_knowledge_grants",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional context about why access was granted.",
    )

    class Meta:
        db_table = "accounts_agent_knowledge_grant"
        ordering = ("-granted_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["agent_profile", "knowledge_upload"],
                name="agent_knowledge_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.agent_profile} -> {self.knowledge_upload}"
