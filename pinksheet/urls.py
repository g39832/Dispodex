"""Every URL in Dispodex, grouped by area."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, re_path
from django.views.generic import RedirectView

from archive import views as archive_views
from inventory.views import api, dashboard, exports, intake, lookup, pages, photos
from operations import views as ops_views
from squaresync import views as square_views

urlpatterns = [
    # ── Pages ──────────────────────────────────────────────────────────────
    path("", dashboard.dashboard, name="dashboard"),
    path("intake/", intake.intake, name="intake"),
    path("lookup/", lookup.lookup, name="lookup"),
    path("board/", pages.board, name="board"),
    path("archive/", archive_views.archive_list, name="archive"),
    path("scripts/", pages.script_builder, name="scripts"),
    path("listing-images/", pages.listing_images, name="listing_images"),
    path("system/", ops_views.system, name="system"),
    path("card/<str:sku>/", pages.card_redirect, name="card"),
    path("m/<str:sku>/", pages.mobile_card, name="mobile_card"),
    path("print/<str:sku>/", pages.print_card, name="print_card"),
    # ── Files ──────────────────────────────────────────────────────────────
    path("photos/<int:photo_id>/", photos.serve_photo, name="photo"),
    path("listing-images/files/<str:sku>/<str:filename>", photos.serve_listing_image, name="listing_image_file"),
    path("exports/inventory.csv", exports.inventory_csv, name="export_csv"),
    path("exports/inventory.zip", exports.inventory_zip, name="export_zip"),
    path("exports/inventory.xlsx", exports.inventory_xlsx, name="export_xlsx"),
    path("exports/partner.csv", exports.partner_csv, name="export_partner_csv"),
    path("exports/partner.zip", exports.partner_zip, name="export_partner_zip"),
    path("exports/partner.xlsx", exports.partner_xlsx, name="export_partner_xlsx"),
    path("exports/partner-sortable.xlsx", exports.partner_sortable_xlsx, name="export_partner_sortable_xlsx"),
    path("exports/archive.csv", archive_views.archive_csv, name="export_archive"),
    # ── JSON API used by the pages ─────────────────────────────────────────
    path("api/items/undo-delete/", api.item_undo_delete, name="api_undo_delete"),
    path("api/items/bulk-update/", api.item_bulk_update, name="api_item_bulk_update"),
    path("api/items/<int:item_id>/delete/", api.item_delete, name="api_item_delete"),
    path("api/items/<str:sku>/", api.item_copy, name="api_item_copy"),
    path("api/items/<str:sku>/update/", api.item_update, name="api_item_update"),
    path("api/drafts/<str:sku>/", api.draft, name="api_draft"),
    path("api/suggestions/", api.suggestions, name="api_suggestions"),
    path("api/palette/", api.palette, name="api_palette"),
    path("api/board/cards/", api.board_cards, name="api_board_cards"),
    path("api/photos/", api.photo_list, name="api_photo_list"),
    path("api/photos/upload/", api.photo_upload, name="api_photo_upload"),
    path("api/photos/reorder/", api.photo_reorder, name="api_photo_reorder"),
    path("api/photos/<int:photo_id>/delete/", api.photo_delete, name="api_photo_delete"),
    path("api/photos/<int:photo_id>/thumbnail/", api.photo_set_thumbnail, name="api_photo_thumbnail"),
    path("api/scripts/<str:sku>/", api.script, name="api_script"),
    path("api/labels/zpl/", api.label_zpl, name="api_label_zpl"),
    path("api/ebay-categories/", api.ebay_category_list, name="api_ebay_categories"),
    path("api/listing-images/upload/", api.listing_image_upload, name="api_listing_upload"),
    path("api/listing-images/<str:sku>/layout/", api.listing_image_layout, name="api_listing_layout"),
    path("api/health/", ops_views.health_json, name="api_health"),
    path("api/ops/backup/", ops_views.backup_now, name="api_backup"),
    path("api/ops/verify/", ops_views.verify_backup, name="api_verify"),
    path("api/ops/import-database/", ops_views.import_database, name="api_import_database"),
    path("api/square/status/", square_views.status, name="api_square_status"),
    path("api/square/sync-all/", square_views.sync_all, name="api_square_sync_all"),
    path("api/square/queue-all/", square_views.queue_everything, name="api_square_queue_all"),
    path("api/square/test/", square_views.test_connection, name="api_square_test"),
    path("api/square/retry/", square_views.retry_dead_letters, name="api_square_retry"),
    path("api/reconciliation/", square_views.reconciliation_status, name="api_recon_status"),
    path("api/reconciliation/run/", square_views.reconciliation_run, name="api_recon_run"),
    # ── Square webhook (public) + label printer bridge ─────────────────────
    path("webhooks/square/", square_views.webhook, name="square_webhook"),
    path("webhooks/square.php", square_views.webhook),
    path("square_webhook.php", square_views.webhook),
    path("qz/certificate/", ops_views.qz_certificate, name="qz_certificate"),
    path("qz/sign/", ops_views.qz_sign, name="qz_sign"),
    path("favicon.ico", RedirectView.as_view(url="/static/img/favicon.svg", permanent=True)),
    # ── Accounts & admin ───────────────────────────────────────────────────
    path("login/", ops_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    # ── Old PHP addresses keep working (printed QR codes, bookmarks) ───────
    re_path(r"^(?P<page>[a-z_]+\.php)$", ops_views.legacy_redirect, name="legacy_redirect"),
]
