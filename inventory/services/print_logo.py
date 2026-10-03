"""The shop's logo for the printed eBay sheet: data/print_logo.svg, .png or .jpg.

It lives in data/ (outside the code) and staff upload it from the browser, so no file has to be
copied onto the server by hand.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from PIL import Image, UnidentifiedImageError

MIME_FOR_SUFFIX = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg"}
MAX_BYTES = 2 * 1024 * 1024
_SVG_NS = "{http://www.w3.org/2000/svg}"


class LogoError(Exception):
    """The upload was refused; the message is safe to show."""


def _folder() -> Path:
    return Path(settings.PINKSHEET["PRINT_LOGO_DIR"])


def current() -> Path | None:
    """The logo file in use, or None when no logo has been added."""
    for suffix in MIME_FOR_SUFFIX:
        path = _folder() / f"print_logo{suffix}"
        if path.is_file():
            return path
    return None


def _check_svg(data: bytes) -> None:
    text = data.decode("utf-8", errors="replace")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise LogoError("That SVG has a DOCTYPE or entities, which logos don't need. Export it again as plain SVG.")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise LogoError("That file isn't a valid SVG.") from exc
    if root.tag not in ("svg", f"{_SVG_NS}svg"):
        raise LogoError("That file isn't a valid SVG.")
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1].lower()
        if name in ("script", "foreignobject", "iframe", "embed", "object"):
            raise LogoError("That SVG contains scripts or embedded pages. Export it again as a plain logo.")
        for attr, value in element.attrib.items():
            attr = attr.rsplit("}", 1)[-1].lower()
            if attr.startswith("on"):
                raise LogoError("That SVG contains scripts. Export it again as a plain logo.")
            if attr == "href" and not value.startswith(("#", "data:image/")):
                raise LogoError("That SVG links to outside files. Export it again with everything embedded.")


def _suffix_for(data: bytes, name: str) -> str:
    if name.lower().endswith(".svg") or data.lstrip()[:5] in (b"<?xml", b"<svg ", b"<svg>"):
        _check_svg(data)
        return ".svg"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            kind = image.format
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise LogoError("The logo must be an SVG, PNG or JPG file.") from exc
    if kind == "PNG":
        return ".png"
    if kind == "JPEG":
        return ".jpg"
    raise LogoError("The logo must be an SVG, PNG or JPG file.")


def save(upload) -> Path:
    """Check and store an uploaded logo, replacing any earlier one."""
    if upload is None:
        raise LogoError("Choose a logo file first.")
    if upload.size > MAX_BYTES:
        raise LogoError("That logo is over 2 MB. Use a smaller file.")
    data = upload.read()
    suffix = _suffix_for(data, upload.name or "")
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"print_logo{suffix}"
    temp = target.with_name(target.name + ".tmp")
    temp.write_bytes(data)
    temp.replace(target)  # written whole, so a print never sees half a file
    for other in MIME_FOR_SUFFIX:
        if other != suffix:
            (folder / f"print_logo{other}").unlink(missing_ok=True)
    return target


def remove() -> None:
    for suffix in MIME_FOR_SUFFIX:
        (_folder() / f"print_logo{suffix}").unlink(missing_ok=True)
