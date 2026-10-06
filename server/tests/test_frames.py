"""The frames tool: the frame on screen at a time, size limits, region, files, and the errors."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from desk_crit import frames as screenshots
from desk_crit import ffmpeg, project as projects, server, tools
from desk_crit.errors import StudioError
from desk_crit.frames import Probe

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
NAMES = {RED: "red", GREEN: "green", BLUE: "blue", YELLOW: "yellow"}
# (start, colour) of each screen in the variable frame rate file, as a screen recording writes it.
SCREENS = [(0.0, RED), (4.0, GREEN), (10.0, BLUE), (13.0, YELLOW)]
VFR_LENGTH = 16.0


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True, stdin=subprocess.DEVNULL)


def _colour_name(rgb) -> str:
    """The palette colour nearest to ``rgb``: video colour conversion moves a channel by a few steps."""
    nearest = min(NAMES, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, rgb)))
    return NAMES[nearest]


def _centre_colour(path: str) -> str:
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        return _colour_name(rgb.getpixel((rgb.width // 2, rgb.height // 2)))


@pytest.fixture(scope="session")
def vfr_master(tmp_path_factory) -> Path:
    """Four solid screens held for 4, 6, 3 and 3 s: variable frame rate, one frame per change."""
    work = tmp_path_factory.mktemp("vfr")
    listing = []
    holds = [4, 6, 3, 3]
    for (_, colour), hold, n in zip(SCREENS, holds, "abcd"):
        Image.new("RGB", (320, 240), colour).save(work / f"{n}.png")
        listing += [f"file '{work / n}.png'", f"duration {hold}"]
    listing.append(f"file '{work / 'd'}.png'")
    (work / "list.txt").write_text("\n".join(listing) + "\n")
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
            "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(work / "screen.mp4"))
    return work / "screen.mp4"


@pytest.fixture(scope="session")
def offset_master(tmp_path_factory, vfr_master) -> Path:
    """The same recording with its clock starting at 5 s, as some .mov files have."""
    out = tmp_path_factory.mktemp("offset") / "screen.mov"
    _ffmpeg("-i", str(vfr_master), "-c", "copy", "-output_ts_offset", "5", str(out))
    return out


def _copy(master: Path, tmp_path: Path, name: str) -> Path:
    folder = tmp_path / "footage"
    folder.mkdir(exist_ok=True)
    path = folder / name
    shutil.copy(master, path)
    return path


@pytest.fixture
def vfr(tmp_path, vfr_master) -> Path:
    """A recording with no transcript and no saved edit beside it."""
    return _copy(vfr_master, tmp_path, "screen.mp4")


@pytest.fixture
def offset(tmp_path, offset_master) -> Path:
    return _copy(offset_master, tmp_path, "offset.mov")


@pytest.fixture(scope="session")
def retina_master(tmp_path_factory) -> Path:
    """A 2880x1800 recording, one quarter of the screen each colour: red, green over blue, yellow."""
    work = tmp_path_factory.mktemp("retina")
    im = Image.new("RGB", (2880, 1800))
    for (x, y), colour in {(0, 0): RED, (1440, 0): GREEN, (0, 900): BLUE, (1440, 900): YELLOW}.items():
        im.paste(Image.new("RGB", (1440, 900), colour), (x, y))
    im.save(work / "screen.png")
    _ffmpeg("-loop", "1", "-framerate", "5", "-i", str(work / "screen.png"), "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(work / "retina.mp4"))
    return work / "retina.mp4"


@pytest.fixture
def retina(tmp_path, retina_master) -> Path:
    return _copy(retina_master, tmp_path, "retina.mp4")


@pytest.fixture(scope="session")
def noisy_master(tmp_path_factory) -> Path:
    """Random pixels at 1280x720: a PNG of this is well over 1 MB."""
    work = tmp_path_factory.mktemp("noisy")
    pixels = np.random.default_rng(7).integers(0, 256, size=(720, 1280, 3), dtype=np.uint8)
    Image.fromarray(pixels).save(work / "noise.png")
    _ffmpeg("-loop", "1", "-framerate", "5", "-i", str(work / "noise.png"), "-t", "2",
            "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(work / "noisy.mp4"))
    return work / "noisy.mp4"


def _packet_times(video: Path) -> list[float]:
    """Every picture time in the file, read straight from ffprobe, relative to the start."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True).stdout
    start = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True).stdout)
    return sorted(float(line) - start for line in out.split())


# What is on screen: the colour of the last screen that started at or before the time.
ON_SCREEN = [
    (0.0, "red"), (2.5, "red"), (3.9, "red"), (3.999, "red"),
    (4.0, "green"), (4.001, "green"), (7.0, "green"), (9.99, "green"),
    (10.0, "blue"), (11.0, "blue"), (12.999, "blue"),
    (13.0, "yellow"), (15.5, "yellow"), (16.0, "yellow"),
]


@pytest.mark.parametrize("at, colour", ON_SCREEN)
def test_each_time_shows_the_colour_that_is_on_screen_then(vfr, at, colour):
    result = tools.frames(str(vfr), [at])
    assert _centre_colour(result["frames"][0]["path"]) == colour


def test_a_still_screen_is_not_replaced_by_the_next_one(vfr):
    """The obvious ``-ss 7`` returns the 10 s screen. Seven seconds into a long hold must show the 4 s one."""
    result = tools.frames(str(vfr), [7.0])
    frame = result["frames"][0]
    assert frame["shown"] == pytest.approx(4.0)
    assert _centre_colour(frame["path"]) == "green"


def test_shown_is_never_after_at_and_is_the_packet_time_of_that_frame(vfr):
    packets = _packet_times(vfr)
    asked = [t for t, _ in ON_SCREEN]
    for batch in (asked[:6], asked[6:12], asked[12:]):
        for frame in tools.frames(str(vfr), batch)["frames"]:
            assert frame["shown"] <= frame["at"]
            assert frame["shown"] == pytest.approx(max(p for p in packets if p <= frame["at"] + 1e-6), abs=1e-3)


def test_a_file_with_a_start_time_gives_the_same_colours_and_relative_times(offset):
    probed = screenshots.ScreenFootage().probe(offset)
    assert probed.start_time == pytest.approx(5.0)
    assert probed.duration >= VFR_LENGTH  # ffprobe counts the 5 s offset in this muxer's length
    frames = tools.frames(str(offset), [t for t, _ in ON_SCREEN[:6]])["frames"]
    frames += tools.frames(str(offset), [t for t, _ in ON_SCREEN[6:12]])["frames"]
    for frame, (at, colour) in zip(frames, ON_SCREEN[:12]):
        assert _centre_colour(frame["path"]) == colour, at
        assert frame["shown"] <= frame["at"]
        assert frame["shown"] == pytest.approx(max(s for s, _ in SCREENS if s <= at), abs=1e-3)


def test_the_result_has_the_fields_the_contract_names(vfr):
    result = tools.frames(str(vfr), [1.5, 7.0])
    assert set(result) == {"frames", "source_size", "region"}
    assert result["source_size"] == [320, 240]
    assert result["region"] is None
    assert [f["at"] for f in result["frames"]] == [1.5, 7.0]
    first = result["frames"][1]
    assert set(first) == {"at", "shown", "clock", "path", "width", "height"}
    assert first["clock"] == "0:07"
    assert (first["width"], first["height"]) == (320, 240)
    json.dumps(result)


# ── Size, aspect and region ───────────────────────────────────────────────────


def test_a_retina_screen_comes_back_at_the_long_edge_limit_with_the_same_aspect(retina):
    frame = tools.frames(str(retina), [0.5])["frames"][0]
    assert frame["width"] == ffmpeg.MAX_LONG_EDGE == 1568
    assert frame["height"] == 980  # 1568 * 1800 / 2880
    with Image.open(frame["path"]) as im:
        assert im.size == (frame["width"], frame["height"])
    assert tools.frames(str(retina), [0.5])["source_size"] == [2880, 1800]


def test_a_region_returns_that_part_of_the_screen(retina):
    result = tools.frames(str(retina), [0.5], [0.5, 0.5, 1, 1])
    frame = result["frames"][0]
    assert result["region"] == [0.5, 0.5, 1.0, 1.0]
    assert (frame["width"], frame["height"]) == (1440, 900)
    with Image.open(frame["path"]) as im:
        rgb = im.convert("RGB")
        for point in [(5, 5), (720, 450), (1434, 894)]:
            assert _colour_name(rgb.getpixel(point)) == "yellow"
    top_left = tools.frames(str(retina), [0.5], [0, 0, 0.5, 0.5])["frames"][0]
    assert _centre_colour(top_left["path"]) == "red"


def test_a_wide_region_is_scaled_after_the_crop(retina):
    frame = tools.frames(str(retina), [0.5], [0, 0, 1, 0.5])["frames"][0]  # 2880 x 900 of source
    assert (frame["width"], frame["height"]) == (1568, 490)


def test_a_small_crop_is_not_scaled_up(retina):
    frame = tools.frames(str(retina), [0.5], [0, 0, 0.05, 0.05])["frames"][0]
    assert (frame["width"], frame["height"]) == (144, 90)


def test_a_small_video_is_not_scaled_up(vfr):
    frame = tools.frames(str(vfr), [1.0])["frames"][0]
    assert (frame["width"], frame["height"]) == (320, 240)


def test_the_size_rules_without_a_video():
    assert screenshots.scaled_size(2880, 1800) == (1568, 980)
    assert screenshots.scaled_size(1800, 2880) == (980, 1568)
    assert screenshots.scaled_size(1568, 100) == (1568, 100)
    assert screenshots.scaled_size(640, 480) == (640, 480)
    assert screenshots.crop_box(2880, 1800, None) == (0, 0, 2880, 1800)
    assert screenshots.crop_box(2880, 1800, (0.5, 0.5, 1, 1)) == (1440, 900, 2880, 1800)
    assert screenshots.crop_box(1000, 1000, (0, 0, 0.064, 1)) == (0, 0, 64, 1000)  # exactly the minimum
    assert screenshots.latest_at_or_before([13.0, 0.0, 4.0, 10.0], 9.99) == 4.0
    assert screenshots.latest_at_or_before([4.0, 10.0], 3.0) is None


# ── File type and size ────────────────────────────────────────────────────────


def test_a_screenshot_of_flat_colour_is_a_png_under_a_megabyte(retina):
    frame = tools.frames(str(retina), [0.5])["frames"][0]
    path = Path(frame["path"])
    assert path.suffix == ".png"
    assert path.stat().st_size <= ffmpeg.MAX_FILE_BYTES
    with Image.open(path) as im:
        assert im.format == "PNG"


def test_a_noisy_frame_falls_back_to_a_jpeg(tmp_path, noisy_master):
    video = _copy(noisy_master, tmp_path, "noisy.mp4")
    frame = tools.frames(str(video), [0.5])["frames"][0]
    path = Path(frame["path"])
    assert path.suffix == ".jpg"
    with Image.open(path) as im:
        assert im.format == "JPEG"
        assert im.size == (1280, 720)


# ── Files ─────────────────────────────────────────────────────────────────────


def test_files_land_in_the_frames_folder_and_never_beside_the_video(vfr):
    before = sorted(p.name for p in vfr.parent.iterdir())
    result = tools.frames(str(vfr), [1.0, 83.0 / 10])
    project = projects.open_project(str(vfr))
    for frame in result["frames"]:
        path = Path(frame["path"])
        assert path.parent == project.frames_dir
        assert path.name.startswith("frame-") and path.stat().st_size > 0
    assert sorted(p.name for p in vfr.parent.iterdir()) == before


def test_a_file_is_named_by_its_time_and_the_moment_it_was_made(vfr):
    names = [Path(f["path"]).name for f in tools.frames(str(vfr), [1.0, 9.5])["frames"]]
    assert names[0].startswith("frame-0m01.00s-") and names[1].startswith("frame-0m09.50s-")
    assert all(n.endswith(".png") and len(n.split("-")[-2]) == 8 for n in names)


def test_a_time_past_a_minute_is_named_in_minutes(retina_master, tmp_path):
    named = _name_for(retina_master, tmp_path, 83.4)
    assert named == "frame-1m23.40s-"
    assert _name_for(retina_master, tmp_path, 59.999) == "frame-1m00.00s-"


def _name_for(video: Path, tmp_path: Path, at: float) -> str:
    """The file name's start for a time, using a footage fake so no video that long is needed."""
    fake = FakeFootage(duration=200.0)
    copy = _copy(video, tmp_path, f"fake-{at}.mp4")
    path = Path(tools.frames(str(copy), [at], footage=fake)["frames"][0]["path"])
    return path.name.rsplit("-", 2)[0] + "-"


def test_two_calls_at_the_same_time_make_two_files(vfr):
    first = tools.frames(str(vfr), [2.0])["frames"][0]["path"]
    second = tools.frames(str(vfr), [2.0])["frames"][0]["path"]
    assert first != second
    assert Path(first).exists() and Path(second).exists()
    assert len(list(projects.open_project(str(vfr)).frames_dir.glob("frame-*.png"))) == 2


def test_one_call_with_the_same_time_twice_makes_two_files(vfr):
    frames = tools.frames(str(vfr), [2.0, 2.0])["frames"]
    assert frames[0]["path"] != frames[1]["path"]


def test_a_receipt_names_the_times_and_paths(vfr):
    result = tools.frames(str(vfr), [1.0, 5.0])
    lines = [json.loads(line) for line in projects.open_project(str(vfr)).receipts_path.read_text().splitlines()]
    assert lines[-1]["step"] == "frames"
    assert lines[-1]["times"] == [1.0, 5.0]
    assert lines[-1]["paths"] == [f["path"] for f in result["frames"]]


def test_works_with_no_transcript(vfr):
    assert not list(vfr.parent.glob("*.words.json"))
    assert len(tools.frames(str(vfr), [3.0])["frames"]) == 1
    assert not list(vfr.parent.glob("*.words.json"))  # nothing appears beside the recording


# ── Errors: each says what was wrong and how to fix the call ─────────────────


def _fails(video, times, region=None, *, match: str, fix: str) -> None:
    with pytest.raises(StudioError, match=match) as raised:
        tools.frames(str(video), times, region)
    message = str(raised.value)
    assert fix in message
    assert "Traceback" not in message


def test_no_times_is_an_error_that_says_to_pass_some(vfr):
    _fails(vfr, [], match="times is empty", fix="Pass 1 to 6 source times")


def test_seven_times_is_an_error_that_says_to_split_the_call(vfr):
    _fails(vfr, [1, 2, 3, 4, 5, 6, 7], match="7 entries", fix="Split them into calls of 6 or fewer")


def test_a_negative_time_is_an_error_that_names_the_length(vfr):
    _fails(vfr, [-1.0], match="below 0", fix="from 0 to 16.04")


def test_a_time_past_the_end_is_an_error_that_names_the_length(vfr):
    _fails(vfr, [1.0, 99.0], match="past the end", fix="16.04 s long")


@pytest.mark.parametrize("region", [
    [0.5, 0.5, 0.5, 1],  # no width
    [0.6, 0.0, 0.5, 1],  # backwards
    [-0.1, 0, 1, 1],
    [0, 0, 1.2, 1],
    [0, 0, 1],  # three numbers
    [0, 0, 1, 1, 1],
    ["a", 0, 1, 1],
    [0, 0, float("nan"), 1],
])
def test_a_bad_region_is_an_error_that_says_what_to_pass(vfr, region):
    _fails(vfr, [1.0], region, match="not a box inside the frame", fix="[0.5, 0.5, 1, 1]")


def test_a_region_under_64_pixels_is_an_error_that_says_how_big(retina):
    _fails(retina, [0.5], [0, 0, 0.01, 0.01], match="at least 64 px", fix="at least 0.03 of the width")


def test_a_relative_path_is_an_error_that_says_to_pass_an_absolute_one():
    with pytest.raises(StudioError, match="relative") as raised:
        tools.frames("screen.mp4", [1.0])
    assert "absolute path" in str(raised.value)


def test_a_time_before_the_first_picture_says_when_the_first_one_comes(vfr):
    fake = FakeFootage(packets=[0.5, 4.0], duration=20.0)
    with pytest.raises(StudioError, match="first frame of screen.mp4 is at 0.500 s") as raised:
        tools.frames(str(vfr), [0.1], footage=fake)
    assert "Pass a time of 0.500 or later" in str(raised.value)


def test_nothing_is_written_when_one_frame_cannot_be_read(vfr):
    class Failing(FakeFootage):
        def decode(self, video, shown):
            if shown >= 4.0:
                raise StudioError("ffmpeg could not read a frame from screen.mp4: broken. Check that the file is a playable video.")
            return super().decode(video, shown)

    with pytest.raises(StudioError, match="broken"):
        tools.frames(str(vfr), [1.0, 5.0], footage=Failing())
    assert not projects.open_project(str(vfr)).frames_dir.exists() or not any(
        projects.open_project(str(vfr)).frames_dir.iterdir())


# ── Choosing the frame, with a fake footage ───────────────────────────────────


class FakeFootage:
    """Stands in for ``ScreenFootage``: packet times and solid frames, and a log of the windows asked for."""

    def __init__(self, packets=(0.0, 4.0, 10.0, 13.0), duration=20.0, start=0.0, size=(320, 240)):
        self.packets = list(packets)
        self.duration = duration
        self.start = start
        self.size = size
        self.windows: list[tuple[float | None, float]] = []
        self.decoded: list[float] = []

    def probe(self, video):
        return Probe(self.duration, *self.size, self.start)

    def packet_times(self, video, start, end):
        self.windows.append((start, end))
        lo = -1e9 if start is None else start
        return [p + self.start for p in self.packets if lo <= p + self.start <= end]

    def decode(self, video, shown):
        self.decoded.append(shown)
        return Image.new("RGB", self.size, (10, 20, 30))


def test_the_window_widens_until_a_frame_is_found(tmp_path):
    fake = FakeFootage(packets=[0.0, 100.0], duration=200.0)
    shown = screenshots.shown_time(tmp_path / "v.mp4", 99.0, 0.0, fake)
    assert shown == 0.0
    # 2 s back, 10 s, 60 s, then from the start.
    assert [round(99.0 - w[0], 3) if w[0] is not None else None for w in fake.windows] == [2.0, 10.0, 60.0, None]


def test_the_window_stops_widening_at_the_start_of_the_file(tmp_path):
    fake = FakeFootage(packets=[3.0], duration=20.0)
    with pytest.raises(StudioError, match="No picture is on screen yet"):
        screenshots.shown_time(tmp_path / "v.mp4", 1.0, 0.0, fake)
    assert len(fake.windows) == 2  # the 2 s window reached 0, so only the first-frame lookup follows


def test_the_first_window_is_enough_when_a_frame_is_close(tmp_path):
    fake = FakeFootage(packets=[0.0, 4.0], duration=20.0)
    assert screenshots.shown_time(tmp_path / "v.mp4", 4.5, 0.0, fake) == 4.0
    assert len(fake.windows) == 1
    assert fake.windows[0][0] == pytest.approx(2.5)
    assert fake.windows[0][1] == pytest.approx(4.5 + screenshots.END_PAD_SECONDS)


def test_a_start_time_is_added_for_ffprobe_and_taken_off_the_answer(tmp_path):
    fake = FakeFootage(packets=[0.0, 4.0, 10.0], start=5.0)
    assert screenshots.shown_time(tmp_path / "v.mov", 7.0, 5.0, fake) == 4.0
    assert fake.windows[0][1] == pytest.approx(12.0 + screenshots.END_PAD_SECONDS)  # 7 s in is 12 s on the file's clock


def test_shown_is_the_time_to_the_millisecond_and_never_above_at(tmp_path):
    fake = FakeFootage(packets=[83.3996], duration=100.0)
    assert screenshots.shown_time(tmp_path / "v.mp4", 83.3996, 0.0, fake) <= 83.3996


def test_decode_starts_a_hair_before_the_frame(vfr):
    fake = FakeFootage(packets=[0.0, 4.0, 10.0])
    tools.frames(str(vfr), [7.0], footage=fake)
    assert fake.decoded == [4.0]  # ScreenFootage.decode subtracts the margin itself


def test_frames_run_for_every_time_in_order(vfr):
    fake = FakeFootage()
    result = tools.frames(str(vfr), [11.0, 1.0, 14.0], footage=fake)
    assert [f["at"] for f in result["frames"]] == [11.0, 1.0, 14.0]
    assert [f["shown"] for f in result["frames"]] == [10.0, 0.0, 13.0]


# ── What the MCP tool returns ─────────────────────────────────────────────────


def test_the_mcp_result_is_label_image_pairs_then_the_json(vfr):
    result = server.frames(str(vfr), [1.0, 7.0])
    kinds = [block.type for block in result.content]
    assert kinds == ["text", "image", "text", "image", "text"]
    assert result.content[0].text == "Frame 1 of 2 at 0:01 (1.00 s)"
    assert result.content[2].text == "Frame 2 of 2 at 0:07 (7.00 s)"
    assert all(block.mime_type == "image/png" for block in result.content if block.type == "image")
    data = json.loads(result.content[-1].text)
    assert data == result.structured_content
    assert "\n" not in result.content[-1].text
    first = result.content[1]
    assert base64.b64decode(first.data) == Path(data["frames"][0]["path"]).read_bytes()


def test_the_mcp_result_marks_a_jpeg_as_one(tmp_path, noisy_master):
    video = _copy(noisy_master, tmp_path, "noisy.mp4")
    result = server.frames(str(video), [0.5])
    assert result.content[1].mime_type == "image/jpeg"


def test_the_mcp_tool_turns_a_bad_call_into_a_plain_tool_error(vfr):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as raised:
        server.frames(str(vfr), [1, 2, 3, 4, 5, 6, 7])
    assert "Split them into calls of 6 or fewer" in str(raised.value)
    assert "Traceback" not in str(raised.value)
