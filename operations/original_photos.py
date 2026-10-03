"""Swap the low-res previews from the old app's Excel export for the old app's original photos.

The "Excel with photos" export only carried ~320px previews, so items imported from it show
blurry photos marked ``low_res``. The old app's own ``data/sku_photos`` folder still has the
full-size files. For every SKU that has low-res photos here and originals there, this adds the
originals (same order, same thumbnail) and retires the previews. Nothing else is touched: photos
staff took in Dispodex stay, items are not changed, and the old app's files are only read.

The preview files are left on disk, so restoring an older database backup still finds them.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.db.models import Max
from PIL import Image

from core.skus import normalize_sku, sku_directory
from inventory.models import Item, ItemEvent, Photo
from inventory.services import photos as photo_service


@dataclass
class Original:
    path: Path
    name: str
    is_thumb: bool
    size: tuple[int, int]


@dataclass
class SkuPlan:
    sku: str
    previews: list[Photo]
    originals: list[Original]


@dataclass
class Plan:
    skus: list[SkuPlan] = field(default_factory=list)
    low_res_skus: int = 0
    no_originals: list[str] = field(default_factory=list)
    not_sharper: list[str] = field(default_factory=list)
    unreadable: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def originals(self) -> int:
        return sum(len(s.originals) for s in self.skus)

    @property
    def previews(self) -> int:
        return sum(len(s.previews) for s in self.skus)

    def lines(self) -> list[str]:
        lines = [
            f"Items with low-res photos: {self.low_res_skus}",
            f"Items that will get their original photos: {len(self.skus)} "
            f"({self.previews} low-res photo(s) replaced by {self.originals} original(s))",
        ]
        if self.no_originals:
            lines.append(f"No originals in the old app for {len(self.no_originals)} item(s), left as they are: "
                         + _sample(self.no_originals))
        if self.not_sharper:
            lines.append(f"Originals no bigger than the previews for {len(self.not_sharper)} item(s), left as they are: "
                         + _sample(self.not_sharper))
        if self.failed:
            lines.append(f"Could not replace, left as they are: {_sample(self.failed)}")
        if self.unreadable:
            lines.append(f"Old photo files that could not be read (skipped): {self.unreadable}")
        return lines


def _sample(skus: list[str], limit: int = 10) -> str:
    return ", ".join(skus[:limit]) + (f" and {len(skus) - limit} more" if len(skus) > limit else "")


def _old_photo_rows(data_dir: Path) -> dict[str, list[dict]]:
    db = data_dir / "intake.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"Could not find the old database at {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # read-only: the old app is never changed
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM sku_photos ORDER BY sort_order, id")]
    finally:
        con.close()
    by_sku: dict[str, list[dict]] = {}
    for row in rows:
        sku = normalize_sku(row.get("sku_normalized"))
        if sku and row.get("stored_name"):
            by_sku.setdefault(sku, []).append(row)
    return by_sku


def _preview_size(photo: Photo) -> tuple[int, int]:
    try:
        with Image.open(photo_service.photo_path(photo)) as image:
            return image.size
    except (OSError, ValueError, SyntaxError):
        return (0, 0)


def plan(old_app: Path) -> Plan:
    """What would change, without changing anything."""
    data_dir = Path(old_app) / "data"
    old_rows = _old_photo_rows(data_dir)
    result = Plan()
    previews_by_sku: dict[str, list[Photo]] = {}
    for photo in Photo.objects.filter(low_res=True).order_by("sort_order", "id"):
        previews_by_sku.setdefault(photo.sku_normalized, []).append(photo)
    result.low_res_skus = len(previews_by_sku)

    for sku, previews in sorted(previews_by_sku.items()):
        originals = []
        for row in old_rows.get(sku, []):
            path = data_dir / "sku_photos" / sku_directory(sku) / Path(str(row["stored_name"])).name
            if not path.is_file():
                continue
            try:
                with Image.open(path) as image:
                    size = image.size
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                result.unreadable += 1
                continue
            originals.append(Original(path=path, name=str(row.get("original_name") or path.name)[:255],
                                      is_thumb=bool(row.get("is_thumb")), size=size))
        if not originals:
            result.no_originals.append(sku)
            continue
        biggest_preview = max(max(_preview_size(p)) for p in previews)
        if max(max(o.size) for o in originals) <= biggest_preview:
            result.not_sharper.append(sku)
            continue
        result.skus.append(SkuPlan(sku=sku, previews=previews, originals=originals))
    return result


def run(old_app: Path, *, actor: str = "System", progress=None) -> Plan:
    """Swap in the originals. Each item is its own transaction, so a stop part-way keeps what's done."""
    result = plan(old_app)
    for done, entry in enumerate(result.skus, start=1):
        try:
            _replace(entry, actor)
        except (photo_service.PhotoError, OSError) as exc:
            result.failed.append(f"{entry.sku} ({exc})")
        if progress and done % 100 == 0:
            progress(done, len(result.skus))
    return result


def _replace(entry: SkuPlan, actor: str) -> None:
    dest = photo_service.photo_root() / sku_directory(entry.sku)
    written = []
    try:
        stored = []
        for original in entry.originals:
            with original.path.open("rb") as handle:
                # Same pipeline as an upload: rotated upright, capped, saved as the app's format.
                stored.append(photo_service.store_image(handle, dest, widen_to=photo_service.min_width()))
            written.append(dest / stored[-1].stored_name)
        with transaction.atomic():
            previews = list(Photo.objects.select_for_update().filter(pk__in=[p.pk for p in entry.previews]))
            if not previews:
                raise _AlreadyDone
            slots = sorted(p.sort_order for p in previews)
            after = Photo.objects.filter(sku_normalized=entry.sku).aggregate(m=Max("sort_order"))["m"] or 0
            was_thumb = any(p.is_thumb for p in previews)
            thumb_index = next((i for i, o in enumerate(entry.originals) if o.is_thumb), 0)
            Photo.objects.filter(pk__in=[p.pk for p in previews]).delete()
            Photo.objects.bulk_create([
                Photo(
                    sku_normalized=entry.sku,
                    original_name=original.name,
                    stored_name=s.stored_name,
                    mime_type=s.mime_type,
                    file_size=s.file_size,
                    # Keep the previews' places in the photo order; any extra originals go last.
                    sort_order=slots[i] if i < len(slots) else after + i - len(slots) + 1,
                    is_thumb=was_thumb and i == thumb_index,
                    low_res=False,
                )
                for i, (original, s) in enumerate(zip(entry.originals, stored))
            ])
            item = Item.objects.filter(sku_normalized=entry.sku).first()
            if item is not None:
                ItemEvent.objects.create(
                    item=item, sku_normalized=entry.sku, action=ItemEvent.Action.PHOTO_ADDED, actor=actor[:64],
                    note=f"Replaced {len(previews)} low-res photo(s) with {len(stored)} original(s) from the old app"[:255],
                )
    except Exception as exc:
        for path in written:  # the database is unchanged; don't leave stray files
            path.unlink(missing_ok=True)
        if isinstance(exc, _AlreadyDone):
            return
        raise


class _AlreadyDone(Exception):
    """Someone else replaced these previews first."""
