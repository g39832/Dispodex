"""Bookkeeping for the two-way Square sync.

Nothing here is required for the app to run. When Square credentials are not
configured these tables simply stay empty (queued jobs wait until they are).
"""
from django.db import models
from django.utils import timezone


class CatalogSync(models.Model):
    """Which Square catalog item/variation belongs to which SKU, and its sync state."""

    sku_normalized = models.CharField(max_length=64, unique=True)
    square_item_id = models.CharField(max_length=64, blank=True)
    square_item_version = models.BigIntegerField(null=True, blank=True)
    square_variation_id = models.CharField(max_length=64, blank=True, db_index=True)
    square_variation_version = models.BigIntegerField(null=True, blank=True)
    square_image_id = models.CharField(max_length=64, blank=True)
    square_image_photo_id = models.BigIntegerField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_sale_sync_at = models.DateTimeField(null=True, blank=True)
    last_inventory_sync_at = models.DateTimeField(null=True, blank=True)
    sync_enabled = models.BooleanField(default=True)
    last_origin = models.CharField(max_length=16, blank=True)
    last_correlation_id = models.CharField(max_length=64, blank=True)
    last_local_change_at = models.DateTimeField(null=True, blank=True)
    last_square_change_at = models.DateTimeField(null=True, blank=True)
    previous_status = models.CharField(max_length=32, blank=True)
    last_synced_quantity = models.IntegerField(null=True, blank=True)
    last_synced_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "catalog sync record"

    def __str__(self) -> str:
        return self.sku_normalized

    @property
    def is_mapped(self) -> bool:
        return bool(self.square_item_id and self.square_variation_id)


class SyncJob(models.Model):
    """One pending piece of Square work, retried with back-off until it succeeds."""

    class Operation(models.TextChoices):
        CATALOG_UPSERT = "catalog_upsert", "Catalog update"
        INVENTORY_SET = "inventory_set", "Inventory count"
        INVENTORY_PULL = "inventory_pull", "Inventory check"
        FULL_SYNC = "full_sync", "Full sync"

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        RETRYING = "retrying", "Retrying"
        DEAD_LETTER = "dead_letter", "Gave up"

    sku_normalized = models.CharField(max_length=64)
    operation = models.CharField(max_length=32, choices=Operation.choices)
    priority = models.IntegerField(default=0)
    status = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED, db_index=True)
    rerun = models.BooleanField(default=False, help_text="Changed again while it was being processed.")
    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=10)
    last_error = models.TextField(blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sku_normalized", "operation"], name="unique_job_per_sku_op")]
        indexes = [models.Index(fields=["status", "next_retry_at"])]
        ordering = ["-priority", "created_at"]

    def __str__(self) -> str:
        return f"{self.operation} {self.sku_normalized} ({self.status})"


class SyncAuditLog(models.Model):
    class Direction(models.TextChoices):
        PUSH = "push", "Dispodex → Square"
        PULL = "pull", "Square → Dispodex"

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        SKIPPED = "skipped", "Skipped"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    operation = models.CharField(max_length=64)
    sku_normalized = models.CharField(max_length=64, blank=True, db_index=True)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    status = models.CharField(max_length=8, choices=Result.choices)
    duration_ms = models.IntegerField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    object_type = models.CharField(max_length=32, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    queue_job_id = models.BigIntegerField(null=True, blank=True)
    webhook_id = models.CharField(max_length=128, blank=True)
    correlation_id = models.CharField(max_length=64, blank=True)
    request_body_hash = models.CharField(max_length=64, blank=True)
    response_summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.operation} {self.status}"


class WebhookEvent(models.Model):
    """Every Square webhook we accepted — also stops the same event being applied twice."""

    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64)
    merchant_id = models.CharField(max_length=64, blank=True)
    location_id = models.CharField(max_length=64, blank=True)
    body_hash = models.CharField(max_length=64)
    result_status = models.CharField(max_length=16, blank=True)
    result_message = models.TextField(blank=True)
    processed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self) -> str:
        return f"{self.event_type} {self.event_id}"


class Sale(models.Model):
    """A sale rung up on the Square POS for one of our SKUs."""

    sku = models.CharField(max_length=64)
    sku_normalized = models.CharField(max_length=64, db_index=True)
    square_order_id = models.CharField(max_length=64)
    square_payment_id = models.CharField(max_length=64, blank=True, db_index=True)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    line_item_quantity = models.PositiveIntegerField(default=1)
    sold_at = models.DateTimeField(db_index=True)
    location_id = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=32, default="square_pos")
    receipt_number = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sold_at"]
        constraints = [
            models.UniqueConstraint(fields=["square_order_id", "sku_normalized"], name="unique_sale_line")
        ]

    def __str__(self) -> str:
        return f"{self.sku_normalized} ${self.sale_price}"


class ReconciliationRun(models.Model):
    class Trigger(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"
        ONDEMAND = "ondemand", "On demand"

    class State(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    trigger_type = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(max_length=16, choices=State.choices, default=State.RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_items_checked = models.IntegerField(default=0)
    issues_detected = models.IntegerField(default=0)
    issues_repaired = models.IntegerField(default=0)
    manual_actions_required = models.IntegerField(default=0)
    api_requests_made = models.IntegerField(default=0)
    api_requests_failed = models.IntegerField(default=0)
    runtime_seconds = models.FloatField(default=0)
    error_message = models.TextField(blank=True)
    breakdown = models.JSONField(default=dict, blank=True)
    error_summary = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"Run #{self.pk} ({self.status})"


class ReconciliationIssue(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    class RepairStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        AUTO_REPAIRED = "auto_repaired", "Auto-repaired"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"
        MANUAL_REQUIRED = "manual_required", "Needs a person"

    run = models.ForeignKey(ReconciliationRun, on_delete=models.CASCADE, related_name="issues")
    sku_normalized = models.CharField(max_length=64, blank=True)
    issue_type = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.WARNING)
    description = models.TextField()
    pinksheet_value = models.TextField(blank=True)
    square_value = models.TextField(blank=True)
    auto_repairable = models.BooleanField(default=False)
    repair_action = models.CharField(max_length=32, blank=True)
    repair_status = models.CharField(max_length=16, choices=RepairStatus.choices, default=RepairStatus.PENDING)
    repair_result = models.TextField(blank=True)
    repaired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.issue_type} {self.sku_normalized}"


class ReconciliationAlert(models.Model):
    alert_type = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, choices=ReconciliationIssue.Severity.choices, default="warning")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    sku_normalized = models.CharField(max_length=64, blank=True)
    dismissed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title
