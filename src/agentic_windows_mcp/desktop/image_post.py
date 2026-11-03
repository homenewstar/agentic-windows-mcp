"""Post-processing for captured screenshots (issue #9).

A full-screen PNG can exceed the result-size limit of some MCP clients,
which then truncate the base64 payload mid-stream and leave the agent
with no usable image at all. The Screenshot tool therefore offers
explicit size control: downscale to fit a width/height budget, re-encode
as JPEG, or save the capture to a file on the host and return only
metadata. Everything here is pure Pillow so it can be unit-tested
without a desktop.
"""

import io
import os

from PIL import Image as PILImage

SUPPORTED_FORMATS = ("png", "jpeg")

MIN_DIMENSION = 16
MAX_DIMENSION = 7680


def _validate_dimension(name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not (MIN_DIMENSION <= value <= MAX_DIMENSION):
        raise ValueError(f"{name} must be between {MIN_DIMENSION} and {MAX_DIMENSION}")


def normalize_format(image_format: str) -> str:
    """Normalize a caller-supplied format name to ``png`` or ``jpeg``."""
    fmt = str(image_format or "png").strip().lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"image_format must be one of {SUPPORTED_FORMATS}, got {image_format!r}")
    return fmt


def validate_screenshot_options(
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    image_format: str = "png",
    quality: int = 85,
) -> str:
    """Validate Screenshot post-processing options; returns the format.

    Split out so the tool layer can fail fast on bad arguments before
    paying for a capture it would only throw away.
    """
    fmt = normalize_format(image_format)
    _validate_dimension("max_width", max_width)
    _validate_dimension("max_height", max_height)
    if isinstance(quality, bool) or not isinstance(quality, int) or not (1 <= quality <= 95):
        raise ValueError("quality must be an integer between 1 and 95")
    return fmt


def process_screenshot(
    data: bytes,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    image_format: str = "png",
    quality: int = 85,
    save_path: str | None = None,
) -> dict:
    """Resize and/or re-encode a captured PNG screenshot.

    The image is only ever downscaled (never enlarged) and always keeps
    its aspect ratio. With ``save_path`` the processed bytes are written
    to that file and ``data`` in the result is ``None`` — the caller
    returns metadata only, so nothing large crosses the MCP transport.

    Returns a dict with: ``data`` (bytes | None), ``format``, ``width``,
    ``height``, ``original_width``, ``original_height``, ``byte_size``
    and ``saved_path`` (str | None).
    """
    fmt = validate_screenshot_options(
        max_width=max_width, max_height=max_height, image_format=image_format, quality=quality
    )

    with PILImage.open(io.BytesIO(data)) as image:
        original_width, original_height = image.size
        scale = 1.0
        if max_width is not None:
            scale = min(scale, max_width / original_width)
        if max_height is not None:
            scale = min(scale, max_height / original_height)
        if scale < 1.0:
            new_size = (
                max(1, round(original_width * scale)),
                max(1, round(original_height * scale)),
            )
            image = image.resize(new_size, PILImage.LANCZOS)
        if fmt == "jpeg" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        buffered = io.BytesIO()
        if fmt == "jpeg":
            image.save(buffered, format="JPEG", quality=quality)
        else:
            image.save(buffered, format="PNG")
        processed = buffered.getvalue()
        width, height = image.size

    saved_path = None
    if save_path:
        saved_path = os.path.abspath(os.path.expanduser(str(save_path)))
        with open(saved_path, "wb") as fh:
            fh.write(processed)

    return {
        "data": None if saved_path else processed,
        "format": fmt,
        "width": width,
        "height": height,
        "original_width": original_width,
        "original_height": original_height,
        "byte_size": len(processed),
        "saved_path": saved_path,
    }
