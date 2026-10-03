"""The read-only Archive: search legacy history and export it."""
import csv
import io
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from archive.models import ArchiveItem
from core.skus import normalize_sku
from inventory.services.exports import spreadsheet_safe

PAGE_SIZE = 50


@dataclass
class ArchiveFilters:
    q: str = ""
    status: str = ""
    source: str = ""
    legacy_source: str = ""
    sold_from: date | None = None
    sold_to: date | None = None

    @classmethod
    def from_query(cls, params):
        return cls(
            q=(params.get("q") or "").strip()[:100],
            status=(params.get("status") or "").strip(),
            source=(params.get("source") or "").strip(),
            legacy_source=(params.get("legacy_source") or "").strip(),
            sold_from=parse_date(params.get("sold_from") or "") if params.get("sold_from") else None,
            sold_to=parse_date(params.get("sold_to") or "") if params.get("sold_to") else None,
        )

    def as_query(self) -> dict:
        data = {
            "q": self.q, "status": self.status, "source": self.source, "legacy_source": self.legacy_source,
            "sold_from": self.sold_from.isoformat() if self.sold_from else "",
            "sold_to": self.sold_to.isoformat() if self.sold_to else "",
        }
        return {k: v for k, v in data.items() if v}


def filter_archive(filters: ArchiveFilters):
    qs = ArchiveItem.objects.all()
    if filters.q:
        term = filters.q
        qs = qs.filter(
            Q(sku_normalized__startswith=normalize_sku(term))
            | Q(sku__icontains=term)
            | Q(title__icontains=term)
            | Q(status__istartswith=term)
            | Q(source__icontains=term)
            | Q(buyer__icontains=term)
            | Q(legacy_source__icontains=term)
            | Q(legacy_table__icontains=term)
            | Q(legacy_id__iexact=term)
            | Q(notes__icontains=term)
            | Q(legacy_payload__icontains=term)
        )
    if filters.status:
        qs = qs.filter(status__iexact=filters.status)
    if filters.source:
        qs = qs.filter(source__iexact=filters.source)
    if filters.legacy_source:
        qs = qs.filter(legacy_source__iexact=filters.legacy_source)
    if filters.sold_from or filters.sold_to:
        qs = qs.annotate(effective_date=Coalesce("sold_at", TruncDate("created_at")))
        if filters.sold_from:
            qs = qs.filter(effective_date__gte=filters.sold_from)
        if filters.sold_to:
            qs = qs.filter(effective_date__lte=filters.sold_to)
    return qs


def _distinct(field: str) -> list[str]:
    return list(ArchiveItem.objects.exclude(**{field: ""}).values_list(field, flat=True).distinct().order_by(field))


def archive_list(request):
    filters = ArchiveFilters.from_query(request.GET)
    queryset = filter_archive(filters)
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    query = filters.as_query()
    return render(
        request,
        "archive/archive.html",
        {
            "page": "archive",
            "filters": filters,
            "page_obj": page,
            "paginator": paginator,
            "page_range": paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
            "statuses": _distinct("status"),
            "sources": _distinct("source"),
            "legacy_sources": _distinct("legacy_source"),
            "query": query,
            "page_query": urlencode(query),
            "total": ArchiveItem.objects.count(),
        },
    )


EXPORT_HEADERS = [
    "SKU", "Title", "Status", "Sold / Date", "Sale Price", "Purchase Price", "Source", "Buyer",
    "Legacy Source", "Legacy Table", "Legacy ID", "Notes", "Created", "Updated",
]


def archive_csv(request):
    rows = filter_archive(ArchiveFilters.from_query(request.GET))
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_HEADERS)

    def stamp(value):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S") if value else ""

    for row in rows:
        writer.writerow([spreadsheet_safe(value) for value in [
            row.sku, row.title, row.status, row.sold_at.isoformat() if row.sold_at else "",
            "" if row.sold_price is None else f"{row.sold_price:.2f}",
            "" if row.purchase_price is None else f"{row.purchase_price:.2f}",
            row.source, row.buyer, row.legacy_source, row.legacy_table, row.legacy_id, row.notes,
            stamp(row.created_at), stamp(row.updated_at),
        ]])
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="archive_{timezone.localdate():%Y-%m-%d}.csv"'
    return response
