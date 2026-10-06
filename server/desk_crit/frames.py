# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/frames.py. Do not edit: change it in Lumr Studio and run the script.
"""Screenshots of the source video at the times Claude asks for.

A screen recording is variable frame rate: QuickTime and Cmd-Shift-5 write a
frame only when the picture changes, so a still screen can go many seconds
without one. The obvious ``ffmpeg -ss T -i video -frames:v 1`` returns the
next frame after T, which can be a screen the person had already left. So the
frame on screen at T is found first, from packet times, without decoding:

1. List the packet times just before T (``ffprobe -read_intervals``).
2. Take the largest at or before T. When there is none, look further back
   (10 s, then 60 s, then from the start).
3. Decode exactly that frame, with ``-ss`` set a hair before its time.

ffprobe's times are absolute and ffmpeg's ``-ss`` is relative to the file's
start time (some ``.mov`` files start at 5 s, say), so the start time is read
once and taken off. ``shown`` is relative, like ``at`` and the transcript.

Everything that is not ffmpeg is a plain function here: checking the times and
the region, choosing the frame, the crop box, the scale, the file encoding.
``ScreenFootage`` is the part that runs ffmpeg and ffprobe, and tests pass a
fake with the same three methods. ffmpeg's stderr goes to a log file in a temp
folder that is removed afterwards, as in look.py.
"""

from __future__ import annotations

import io
import json
import math
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from desk_crit.ffmpeg import clock
from desk_crit.errors import StudioError
from desk_crit.ffmpeg import MAX_FILE_BYTES, MAX_LONG_EDGE, _run

# ── Limits ────────────────────────────────────────────────────────────────────

MAX_TIMES = 6
# A crop smaller than this on a side is too small to read anything in.
MIN_CROP_PIXELS = 64
JPEG_QUALITY = 90
# How far back from the time asked for to list packets, in order. Each step is
# tried only when the one before found no frame at or before the time. The last
# step is "from the start of the file".
LOOK_BACK_SECONDS = (2.0, 10.0, 60.0, None)
# How far past the time asked for ffprobe keeps listing packets. It stops at
# the first packet at or after its end, and an encoder with B-frames writes some
# packets after a later one: in the test recording the 13 s frame comes after the
# 16 s one. Ending exactly at the time would lose such a frame, so the list runs
# on and ``latest_at_or_before`` throws the extra away. Listing packets is cheap.
END_PAD_SECONDS = 10.0
# ffmpeg is told to start this far before the frame, so rounding in the
# container's clock can't land it on the frame before.
SEEK_MARGIN = 0.0005
# Two times closer than this are the same time. ffprobe prints six decimals.
TIME_EPS = 1e-6
# Frames decoded at once. A decode of one frame is quick; a few at a time is enough.
MAX_WORKERS = 4


# ── The footage (the only part that touches ffmpeg) ───────────────────────────


@dataclass(frozen=True)
class Probe:
    """What the checks need to know about the video, as the picture is shown."""

    duration: float
    width: int
    height: int
    start_time: float


class ScreenFootage:
    """Reads facts and single frames from a video with ffprobe and ffmpeg.

    Tests pass any object with the same three methods, so no test needs a real
    decode unless it wants one.
    """

    def probe(self, video: Path) -> Probe:
        """Length, picture size and container start time of ``video``."""
        out = _run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=start_time,duration:stream=width,height:stream_side_data=rotation",
             "-of", "json", str(video)],
            what="ffprobe could not read",
            video=video,
            capture_stdout=True,
        )
        try:
            info = json.loads(out)
            stream = info["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            for side in stream.get("side_data_list", []):
                if abs(float(side.get("rotation", 0))) % 180 == 90:
                    width, height = height, width
            fmt = info["format"]
            duration = float(fmt["duration"])
            start = float(fmt.get("start_time", 0.0))
        except (KeyError, IndexError, ValueError, TypeError):
            raise StudioError(
                f"{video.name} has no readable picture or length. Check that the file is a video with a picture."
            ) from None
        if not duration > 0:
            raise StudioError(f"ffprobe could not read a length from {video.name}. Check that the file is a playable video.")
        return Probe(duration, width, height, start)

    def packet_times(self, video: Path, start: float | None, end: float) -> list[float]:
        """Absolute picture packet times between ``start`` and ``end`` (None: from the file's start).

        ffprobe seeks to the keyframe before ``start``, so the list can begin
        earlier. Nothing is decoded.
        """
        window = f"{max(0.0, start):.4f}%{end:.4f}" if start is not None else f"%{end:.4f}"
        out = _run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", window,
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(video)],
            what="ffprobe could not list the frames of",
            video=video,
            capture_stdout=True,
        )
        times = []
        for line in out.splitlines():
            try:
                times.append(float(line.strip().rstrip(",")))
            except ValueError:
                continue  # a packet with no time prints N/A
        return times

    def decode(self, video: Path, shown: float) -> Image.Image:
        """The frame at ``shown`` seconds from the start of the file, in full size and RGB."""
        with tempfile.TemporaryDirectory(prefix="lumr-frames-") as tmp:
            folder = Path(tmp)
            still = folder / "frame.png"
            _run(
                ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, shown - SEEK_MARGIN):.4f}",
                 "-i", str(video), "-map", "0:v:0", "-frames:v", "1", "-c:v", "png", "-f", "image2", str(still)],
                what="ffmpeg could not read a frame from",
                video=video,
                log=folder / "ffmpeg.log",
            )
            if not still.exists():
                raise StudioError(
                    f"ffmpeg found no frame at {shown:.3f} s in {video.name}. Pass a time inside the video."
                )
            with Image.open(still) as im:
                return im.convert("RGB")


# ── Checking what was asked for ───────────────────────────────────────────────


def check_times(times: object) -> list[float]:
    """The times as floats, or StudioError when the count or a value is wrong.

    This is the part that needs no probe. ``check_range`` does the rest.
    """
    if not isinstance(times, (list, tuple)) or not times:
        raise StudioError(f"times is empty. Pass 1 to {MAX_TIMES} source times in seconds, such as [83.4].")
    if len(times) > MAX_TIMES:
        raise StudioError(
            f"times has {len(times)} entries and a call takes at most {MAX_TIMES}. "
            f"Split them into calls of {MAX_TIMES} or fewer."
        )
    out = []
    for t in times:
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            raise StudioError(f"time {t!r} is not a number. Pass source times in seconds, such as [83.4].")
        out.append(float(t))
    return out


def check_range(times: Sequence[float], duration: float) -> None:
    """StudioError for the first time below 0 or past the end of the video."""
    for t in times:
        if t < 0:
            raise StudioError(f"time {t:g} is below 0. Pass source seconds from 0 to {duration:.2f}.")
        if t > duration + TIME_EPS:
            raise StudioError(
                f"time {t:g} is past the end of the video, which is {duration:.2f} s long. "
                f"Pass source seconds from 0 to {duration:.2f}."
            )


def check_region(region: object) -> tuple[float, float, float, float] | None:
    """The region as four floats, None when there is none, or StudioError when it is not a box inside the frame."""
    if region is None:
        return None
    ok = (
        isinstance(region, (list, tuple)) and len(region) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in region)
    )
    if ok:
        left, top, right, bottom = (float(v) for v in region)
        ok = 0 <= left < right <= 1 and 0 <= top < bottom <= 1
    if not ok:
        raise StudioError(
            f"region {region!r} is not a box inside the frame. Pass [left, top, right, bottom] as fractions "
            "from 0 to 1 with left below right and top below bottom, such as [0.5, 0.5, 1, 1] for the "
            "bottom right quarter. Leave region out for the whole screen."
        )
    return left, top, right, bottom


def crop_box(width: int, height: int, region: tuple[float, float, float, float] | None) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` in source pixels for ``region``, the whole frame when it is None.

    Raises StudioError when the crop is under ``MIN_CROP_PIXELS`` on a side.
    """
    if region is None:
        return 0, 0, width, height
    left, top = round(region[0] * width), round(region[1] * height)
    right, bottom = round(region[2] * width), round(region[3] * height)
    if right - left < MIN_CROP_PIXELS or bottom - top < MIN_CROP_PIXELS:
        need_w = math.ceil(MIN_CROP_PIXELS / width * 100) / 100
        need_h = math.ceil(MIN_CROP_PIXELS / height * 100) / 100
        raise StudioError(
            f"region is only {right - left} x {bottom - top} px of the {width} x {height} source, and a crop "
            f"needs at least {MIN_CROP_PIXELS} px on each side. Pass a larger box: at least {need_w:g} of the "
            f"width and {need_h:g} of the height."
        )
    return left, top, right, bottom


def scaled_size(width: int, height: int) -> tuple[int, int]:
    """``(width, height)`` with the long edge at most ``MAX_LONG_EDGE``, same aspect, never larger than the input."""
    long_edge = max(width, height)
    if long_edge <= MAX_LONG_EDGE:
        return width, height
    scale = MAX_LONG_EDGE / long_edge
    return max(1, round(width * scale)), max(1, round(height * scale))


def encode(image: Image.Image) -> tuple[bytes, str]:
    """``(bytes, suffix)``: a PNG when it is at most ``MAX_FILE_BYTES``, else a JPEG at ``JPEG_QUALITY``."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    if buf.tell() <= MAX_FILE_BYTES:
        return buf.getvalue(), ".png"
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue(), ".jpg"


# ── The frame on screen at a time ─────────────────────────────────────────────


def latest_at_or_before(packet_times: Sequence[float], at: float) -> float | None:
    """The largest packet time at or before ``at``, or None. Packets are not sorted: B-frames reorder them."""
    found = [t for t in packet_times if t <= at + TIME_EPS]
    return max(found) if found else None


def shown_time(video: Path, at: float, start_time: float, footage: ScreenFootage) -> float:
    """The time, relative to the start like ``at``, of the frame on screen at ``at``. Never after ``at``.

    Raises StudioError when the video has no picture yet at ``at`` (its first
    frame comes later).
    """
    absolute = at + start_time
    for back in LOOK_BACK_SECONDS:
        lo = None if back is None else absolute - back
        found = latest_at_or_before(footage.packet_times(video, lo, absolute + END_PAD_SECONDS), absolute)
        if found is not None:
            return min(max(0.0, round(found - start_time, 3)), at)
        if lo is not None and lo <= start_time:
            break  # the window already reached the start of the file: looking further back finds nothing new
    first = min(footage.packet_times(video, None, absolute + END_PAD_SECONDS), default=None)
    if first is not None:
        raise StudioError(
            f"No picture is on screen yet at {at:g} s: the first frame of {video.name} is at "
            f"{max(0.0, first - start_time):.3f} s. Pass a time of {max(0.0, first - start_time):.3f} or later."
        )
    raise StudioError(f"ffprobe found no frames in {video.name} near {at:g} s. Check that the file is a playable video.")


# ── The call ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Shot:
    """One screenshot, ready to write: the encoded file and what it shows."""

    at: float
    shown: float
    data: bytes
    suffix: str  # ".png" or ".jpg"
    width: int
    height: int

    def describe(self, path: Path) -> dict[str, Any]:
        """The entry for this frame in the tool's result."""
        return {
            "at": self.at,
            "shown": self.shown,
            "clock": clock(self.at),
            "path": str(path),
            "width": self.width,
            "height": self.height,
        }


def _shoot(video: Path, at: float, start_time: float, region: tuple[float, float, float, float] | None,
           footage: ScreenFootage) -> Shot:
    shown = shown_time(video, at, start_time, footage)
    image = footage.decode(video, shown)
    left, top, right, bottom = crop_box(image.width, image.height, region)
    if (left, top, right, bottom) != (0, 0, image.width, image.height):
        image = image.crop((left, top, right, bottom))
    size = scaled_size(image.width, image.height)
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    data, suffix = encode(image)
    return Shot(at, shown, data, suffix, image.width, image.height)


def capture(
    video: Path,
    times: object,
    region: object = None,
    *,
    footage: ScreenFootage | None = None,
) -> tuple[list[Shot], Probe, tuple[float, float, float, float] | None]:
    """Check the call, then one ``Shot`` per time, in the order given, plus what the video is like.

    Nothing is written. Raises StudioError for bad times or region, or when
    ffmpeg cannot read the video.
    """
    wanted = check_times(times)
    box = check_region(region)
    footage = footage or ScreenFootage()
    info = footage.probe(video)
    check_range(wanted, info.duration)
    crop_box(info.width, info.height, box)  # fail on a crop that is too small before decoding anything
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(wanted))) as pool:
        shots = list(pool.map(lambda t: _shoot(video, t, info.start_time, box, footage), wanted))
    return shots, info, box
