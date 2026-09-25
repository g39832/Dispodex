"""Small template helpers: {% icon %}, money and file-size formatting."""
from decimal import Decimal

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name: str, extra_class: str = ""):
    """Inline SVG icon from the sprite in templates/partials/icons.html."""
    return format_html('<svg class="icon {}" aria-hidden="true"><use href="#i-{}"></use></svg>', extra_class, name)


@register.filter
def money(value):
    if value in (None, ""):
        return ""
    try:
        return f"${Decimal(str(value)):,.2f}"
    except ArithmeticError:
        return str(value)


@register.filter
def filesize(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def ago(value):
    """Short relative time: "just now", "5 min ago", "3 hours ago", "2 days ago"."""
    if not value:
        return ""
    from django.utils import timezone
    from django.utils.timesince import timesince

    if (timezone.now() - value).total_seconds() < 60:
        return "just now"
    first = timesince(value).split(",")[0].replace("\xa0", " ")
    return first.replace("minutes", "min").replace("minute", "min") + " ago"
