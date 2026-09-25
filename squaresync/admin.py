from django.contrib import admin

from squaresync import models


@admin.register(models.CatalogSync)
class CatalogSyncAdmin(admin.ModelAdmin):
    list_display = ("sku_normalized", "square_item_id", "last_synced_at", "last_error")
    search_fields = ("sku_normalized", "square_item_id", "square_variation_id")


@admin.register(models.SyncJob)
class SyncJobAdmin(admin.ModelAdmin):
    list_display = ("sku_normalized", "operation", "status", "retry_count", "next_retry_at", "updated_at")
    list_filter = ("status", "operation")
    search_fields = ("sku_normalized",)


@admin.register(models.SyncAuditLog)
class SyncAuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "operation", "sku_normalized", "direction", "status")
    list_filter = ("status", "direction")
    search_fields = ("sku_normalized", "operation", "webhook_id")


@admin.register(models.Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("sku_normalized", "sale_price", "sold_at", "square_order_id")
    search_fields = ("sku_normalized", "square_order_id", "square_payment_id")


admin.site.register(models.WebhookEvent)
admin.site.register(models.ReconciliationRun)
admin.site.register(models.ReconciliationIssue)
admin.site.register(models.ReconciliationAlert)
