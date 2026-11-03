"""Issue #9 — Screenshot size control: downscale, JPEG re-encode and
save-to-path so a capture can fit MCP clients that cap result payloads."""

import io
import random

import pytest
from PIL import Image as PILImage

from agentic_windows_mcp.desktop.image_post import (
    process_screenshot,
    validate_screenshot_options,
)


def _png_bytes(width: int, height: int, *, noisy: bool = False) -> bytes:
    if noisy:
        rng = random.Random(42)
        image = PILImage.new("RGB", (width, height))
        image.putdata(
            [
                (rng.randrange(256), rng.randrange(256), rng.randrange(256))
                for _ in range(width * height)
            ]
        )
    else:
        image = PILImage.new("RGBA", (width, height), (30, 60, 90, 255))
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    return buffered.getvalue()


class TestProcessScreenshot:
    def test_defaults_return_png_unchanged_dimensions(self):
        info = process_screenshot(_png_bytes(800, 600))
        assert info["format"] == "png"
        assert (info["width"], info["height"]) == (800, 600)
        assert info["saved_path"] is None
        assert info["byte_size"] == len(info["data"])

    def test_max_width_downscales_keeping_aspect(self):
        info = process_screenshot(_png_bytes(1920, 1200), max_width=1280)
        assert (info["width"], info["height"]) == (1280, 800)
        assert (info["original_width"], info["original_height"]) == (1920, 1200)

    def test_max_height_downscales_keeping_aspect(self):
        info = process_screenshot(_png_bytes(1920, 1200), max_height=600)
        assert (info["width"], info["height"]) == (960, 600)

    def test_never_upscales(self):
        info = process_screenshot(_png_bytes(800, 600), max_width=1920, max_height=1200)
        assert (info["width"], info["height"]) == (800, 600)

    def test_jpeg_is_much_smaller_than_png_for_noisy_image(self):
        data = _png_bytes(640, 400, noisy=True)
        png = process_screenshot(data)
        jpeg = process_screenshot(data, image_format="jpeg", quality=80)
        assert jpeg["format"] == "jpeg"
        assert jpeg["byte_size"] < png["byte_size"] // 3
        with PILImage.open(io.BytesIO(jpeg["data"])) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.mode == "RGB"  # RGBA source converted for JPEG

    def test_jpg_alias_is_accepted(self):
        info = process_screenshot(_png_bytes(64, 64), image_format="JPG")
        assert info["format"] == "jpeg"

    def test_save_path_writes_file_and_returns_no_inline_data(self, tmp_path):
        target = tmp_path / "capture.jpg"
        info = process_screenshot(
            _png_bytes(320, 200), image_format="jpeg", save_path=str(target)
        )
        assert info["data"] is None
        assert info["saved_path"] == str(target)
        assert target.exists()
        assert target.stat().st_size == info["byte_size"]

    def test_invalid_options_raise(self):
        data = _png_bytes(64, 64)
        with pytest.raises(ValueError):
            process_screenshot(data, image_format="bmp")
        with pytest.raises(ValueError):
            process_screenshot(data, quality=0)
        with pytest.raises(ValueError):
            process_screenshot(data, quality=100)
        with pytest.raises(ValueError):
            process_screenshot(data, max_width=4)
        with pytest.raises(ValueError):
            validate_screenshot_options(max_height=True)


def _capture_result(screenshot_bytes, image_info=None):
    class _State:
        cursor_position = (0, 0)
        screenshot_original_size = None
        available_displays = []
        screenshot_displays = None
        screenshot_region = None
        screenshot_backend = None

    result = {
        "desktop_state": _State(),
        "interactive_elements": "",
        "scrollable_elements": "",
        "semantic_tree": "",
        "windows": "",
        "active_window": "",
        "active_desktop": "",
        "all_desktops": "",
        "screenshot_bytes": screenshot_bytes,
        "frame": None,
        "interactive_page": None,
        "refs_generation": None,
    }
    if image_info is not None:
        result["image_info"] = image_info
    return result


class TestResponseBuilderImageInfo:
    def test_saved_screenshot_returns_metadata_only(self, tmp_path):
        from agentic_windows_mcp.tools._snapshot_helpers import build_snapshot_response

        info = process_screenshot(
            _png_bytes(320, 200), save_path=str(tmp_path / "shot.png")
        )
        response = build_snapshot_response(
            _capture_result(info["data"], info), include_ui_details=False
        )
        assert len(response) == 1  # text only, no inline image
        assert "Saved Screenshot:" in response[0]
        assert "Image Format: png" in response[0]

    def test_jpeg_info_sets_image_mime_type(self):
        from agentic_windows_mcp.tools._snapshot_helpers import build_snapshot_response

        info = process_screenshot(_png_bytes(320, 200, noisy=True), image_format="jpeg")
        response = build_snapshot_response(
            _capture_result(info["data"], info), include_ui_details=False
        )
        assert len(response) == 2
        content = response[1].to_image_content()
        mime = getattr(content, "mime_type", None) or getattr(content, "mimeType", None)
        assert mime == "image/jpeg"
        assert "Image Format: jpeg" in response[0]

    def test_resize_reports_coordinate_ratio(self):
        from agentic_windows_mcp.tools._snapshot_helpers import build_snapshot_response

        info = process_screenshot(_png_bytes(1920, 1200), max_width=960)
        response = build_snapshot_response(
            _capture_result(info["data"], info), include_ui_details=False
        )
        assert "Image Processing: resized 1920x1200 -> 960x600" in response[0]
