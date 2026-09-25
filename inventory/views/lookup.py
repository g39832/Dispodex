"""SKU Lookup: search and filter the whole inventory."""
from decimal import Decimal
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.shortcuts import render

from inventory.models import Condition, Functional, ScriptCache, Status
from inventory.services.photos import thumbnail_map
from inventory.services.search import ItemFilters, distinct_values, filter_items, photo_counts

PAGE_SIZE = 25


def lookup(request):
    filters = ItemFilters.from_query(request.GET)
    queryset = filter_items(filters)
    totals = queryset.aggregate(
        value=Sum(ExpressionWrapper(F("price") * F("quantity"), output_field=DecimalField(max_digits=14, decimal_places=2)))
    )
    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    rows = list(page.object_list)
    skus = [row.sku_normalized for row in rows]
    thumbs = thumbnail_map(skus)
    counts = photo_counts(skus)
    scripts = {s.sku_normalized: s.state for s in ScriptCache.objects.filter(sku_normalized__in=skus)}
    for row in rows:
        row.thumb_id = thumbs.get(row.sku_normalized)
        row.photo_count = counts.get(row.sku_normalized, 0)
        row.script_state = scripts.get(row.sku_normalized, "none")

    query = filters.as_query()
    page_query = urlencode(query)
    return render(
        request,
        "inventory/lookup.html",
        {
            "page": "lookup",
            "filters": filters,
            "rows": rows,
            "page_obj": page,
            "paginator": paginator,
            "page_range": paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
            "total_value": totals["value"] or Decimal("0"),
            "statuses": Status.choices,
            "condition_choices": Condition.values,
            "functional_choices": Functional.values,
            "what_options": distinct_values("what_is_it"),
            "brand_options": distinct_values("brand_model"),
            "cpu_options": distinct_values("cpu", 100),
            "ram_options": distinct_values("ram", 50),
            "storage_options": distinct_values("ssd_gb", 50),
            "location_options": distinct_values("where_it_goes", 100),
            "source_options": distinct_values("source", 100),
            "filter_chips": filters.active_chips(),
            "query": query,
            "page_query": page_query,
        },
    )
