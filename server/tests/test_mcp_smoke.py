"""Start the real server over stdio, the way a plugin host would, and talk to it."""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

SERVER_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = SERVER_DIR.parent

# The tools the plugin offers, each with whether it is read only.
TOOLS = {
    "transcribe": False, "read_transcript": True, "find_words": True, "frames": False, "job_status": True,
}
TITLES = {
    "transcribe": "Transcribe", "read_transcript": "Read transcript", "find_words": "Find words",
    "frames": "Screenshots from the recording", "job_status": "Job status",
}


@pytest.fixture
def plugin_data(tmp_path):
    """A stand-in for ${CLAUDE_PLUGIN_DATA} whose ``venv`` is the server's own ``.venv``.

    The plugin keeps its environment in ${CLAUDE_PLUGIN_DATA}/venv. Here that name
    is a link to ``.venv``, the environment this suite already runs in, so the
    launcher sets VIRTUAL_ENV exactly as it ships and uv still finds the packages
    in ``.venv``. Nothing is built in a second place.
    """
    data = tmp_path / "plugin-data"
    data.mkdir()
    (data / "venv").symlink_to(SERVER_DIR / ".venv", target_is_directory=True)
    return data


def server_params(data_dir: Path) -> StdioServerParameters:
    """The launch ``.mcp.json`` declares, with the two plugin folders filled in the way Claude Code fills them.

    The test reads the shipped command and runs the shipped launcher script, so
    it can't drift from either. The plugin folder is this checkout, and
    ``data_dir`` stands in for ${CLAUDE_PLUGIN_DATA} (see ``plugin_data``).
    """
    declared = json.loads((PLUGIN_DIR / ".mcp.json").read_text())["mcpServers"]["desk-crit"]
    fill = {"${CLAUDE_PLUGIN_ROOT}": str(PLUGIN_DIR), "${CLAUDE_PLUGIN_DATA}": str(data_dir)}

    def resolved(arg: str) -> str:
        for name, value in fill.items():
            arg = arg.replace(name, value)
        return arg

    return StdioServerParameters(
        command=declared["command"],
        args=[resolved(arg) for arg in declared["args"]],
        env={
            "DESK_CRIT_PROJECTS_DIR": os.environ["DESK_CRIT_PROJECTS_DIR"],
            "LUMR_HOME": os.environ["LUMR_HOME"],
        },
        cwd=SERVER_DIR,
    )


@pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")
def test_server_lists_its_five_tools_and_answers_them(video, plugin_data):
    async def talk():
        async with Client(server_params(plugin_data), read_timeout_seconds=120) as client:
            listed = (await client.list_tools()).tools
            text = await client.call_tool("read_transcript", {"video_path": str(video)})
            found = await client.call_tool("find_words", {"video_path": str(video), "words": ["we"]})
            shots = await client.call_tool("frames", {"video_path": str(video), "times": [5.0]})
            bad = await client.call_tool("frames", {"video_path": "relative.mp4", "times": [1.0]})
            return listed, client.instructions, text, found, shots, bad

    listed, instructions, text, found, shots, bad = asyncio.run(talk())

    by_name = {t.name: t for t in listed}
    assert len(listed) == len(by_name) == 5, sorted(by_name)
    assert set(by_name) == set(TOOLS)
    for name, read_only in TOOLS.items():
        tool = by_name[name]
        assert tool.description and tool.description.strip(), f"{name} has no description"
        ann = tool.annotations
        assert ann is not None and ann.title == TITLES[name], name
        assert ann.read_only_hint is read_only, name
        assert ann.destructive_hint is False, name

    assert "needs_models" in instructions and "frames" in instructions
    assert "download_models" in instructions and "nothing downloads without a yes" in instructions
    for gone in ("edit", "cut", "pace", "align", "filler", "render"):
        assert gone not in instructions.lower().replace("deletes or overwrites", ""), gone
    for tool in listed:
        said = (tool.description or "").lower()
        for gone in ("word_times", "aligning", "filler", "set_edit", "render"):
            assert gone not in said, (tool.name, gone)

    body = text.content[0].text
    assert not text.is_error
    assert body.startswith("[0.50-2.40] Hello")  # plain text, not a JSON object
    assert body.endswith("\nEND")
    assert text.structured_content["next_start"] is None

    places = found.content[0].text.splitlines()  # plain text, packed like the transcript
    assert not found.is_error and places[0] == "we: said 2 times."
    assert places[2].startswith("w4.350-4.500 0:04 ") and "[we]" in places[2] and places[-1] == "END"
    assert found.structured_content["words"][0]["said"] == 2

    assert not shots.is_error
    assert [block.type for block in shots.content] == ["text", "image", "text"]
    assert shots.content[0].text == "Frame 1 of 1 at 0:05 (5.00 s). Find the cursor: what it's on is what the creator means."
    assert shots.content[1].mime_type == "image/png" and shots.content[1].data
    assert json.loads(shots.content[2].text) == shots.structured_content
    assert shots.structured_content["source_size"] == [320, 240]
    assert shots.structured_content["frames"][0]["shown"] <= 5.0

    assert bad.is_error
    message = bad.content[0].text
    assert "relative" in message and "Traceback" not in message
