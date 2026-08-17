from __future__ import annotations

import csv
import hashlib
import base64
from pathlib import Path

import numpy as np
from PIL import Image
from moviepy import ColorClip, ImageClip, VideoFileClip

from scripts.patch_moviepy_pillow_metadata import patch_moviepy_metadata


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return f"sha256={digest.decode('ascii').rstrip('=')}"


def test_moviepy_metadata_patch_is_exact_and_updates_record(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    package = site / "moviepy"
    dist_info = site / "moviepy-2.2.1.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir()
    metadata = dist_info / "METADATA"
    metadata.write_text(
        "\n".join(
            [
                "Metadata-Version: 2.4",
                "Name: moviepy",
                "Version: 2.2.1",
                "Requires-Dist: pillow<12.0,>=9.2.0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    record = dist_info / "RECORD"
    record.write_text(
        "moviepy-2.2.1.dist-info/METADATA,old-hash,1\n"
        "moviepy-2.2.1.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    result = patch_moviepy_metadata(site)

    content = metadata.read_bytes()
    assert result["status"] == "patched"
    assert b"Requires-Dist: pillow<13.0,>=9.2.0" in content
    assert b"pillow<12.0" not in content
    rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
    metadata_row = next(row for row in rows if row[0].endswith("/METADATA"))
    assert metadata_row[1] == _record_hash(content)
    assert metadata_row[2] == str(len(content))


def test_moviepy_metadata_patch_is_idempotent(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    dist_info = site / "moviepy-2.2.1.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "\n".join(
            [
                "Metadata-Version: 2.4",
                "Name: moviepy",
                "Version: 2.2.1",
                "Requires-Dist: pillow<12.0,>=9.2.0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text(
        "moviepy-2.2.1.dist-info/METADATA,,\n"
        "moviepy-2.2.1.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    assert patch_moviepy_metadata(site)["status"] == "patched"
    assert patch_moviepy_metadata(site)["status"] == "already_patched"


def test_moviepy_pillow12_image_and_video_round_trip(tmp_path: Path) -> None:
    assert tuple(int(part) for part in Image.__version__.split(".")[:2]) >= (12, 3)
    pixels = np.zeros((16, 16, 3), dtype=np.uint8)
    pixels[:, :, 1] = 127
    resized = ImageClip(pixels, duration=0.2).resized((8, 8))
    assert resized.get_frame(0).shape == (8, 8, 3)

    output = tmp_path / "moviepy-pillow12-smoke.mp4"
    clip = ColorClip((16, 16), color=(0, 0, 0), duration=0.2)
    clip.write_videofile(
        str(output),
        fps=5,
        codec="libx264",
        audio=False,
        logger=None,
    )
    with VideoFileClip(str(output)) as loaded:
        assert loaded.get_frame(0).shape == (16, 16, 3)
