"""Home dashboard: today's numbers, recent activity, backups and Square health."""
from django.conf import settings
from django.shortcuts import render
from django.utils import timezone

from inventory.models import Item, Status
from inventory.services.photos import thumbnail_map
from operations import backups


def dashboard(request):
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    items = Item.objects.all()
    counts = {
        "total": items.count(),
        "in_progress": items.exclude(status=Status.SOLD).count(),
        "today": items.filter(created_at__gte=today_start).count(),
        "sold": items.filter(status=Status.SOLD).count(),
    }
    lanes = {value: 0 for value in Status.values}
    for status in items.values_list("status", flat=True):
        lanes[status] = lanes.get(status, 0) + 1

    recent = list(items.exclude(sku_normalized="").order_by("-updated_at", "-id")[:10])
    thumbs = thumbnail_map(i.sku_normalized for i in recent)
    for entry in recent:
        entry.thumb_id = thumbs.get(entry.sku_normalized)

    backup = backups.backup_status()
    alerts = []
    stale_hours = settings.PINKSHEET["BACKUP_STALE_HOURS"]
    if backup["latest"] is None:
        alerts.append("No backups yet. Run one now, or leave the server running overnight for the automatic backup.")
    elif backup["age_hours"] is not None and backup["age_hours"] > stale_hours:
        alerts.append(f"The latest backup is older than {stale_hours} hours.")
    if backup["free_percent"] is not None and backup["free_percent"] < 10:
        alerts.append("The backup drive is low on space (under 10% free).")

    return render(
        request,
        "inventory/dashboard.html",
        {
            "page": "dashboard",
            "counts": counts,
            "lanes": [
                (value, label, lanes.get(value, 0), token)
                for (value, label), token in zip(Status.choices, ["intake", "draft", "review", "listed", "store", "sold"])
            ],
            "recent": recent,
            "backup": backup,
            "alerts": alerts,
        },
    )
