"""MCP wiring only: each tool parses its inputs and calls the plain function in tools.py.

Derived from Lumr Studio's server.py, slimmed to five tools.

Speaks stdio. The SDK's ``stdio_server`` points fd 1 at stderr while it serves,
so stray prints from the speech model or ffmpeg can never corrupt the protocol
stream; logging goes to stderr too.

Results: every tool returns a ``CallToolResult`` built by mcp_results, the one
place that decides how a tool answers and fails: compact JSON (or, for
read_transcript and find_words, the packed text) with the full dict as
``structured_content``. ``frames`` also returns each picture as an image block,
so the model sees it.
"""

import base64
import logging
import sys
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import Field

from desk_crit import tools
from desk_crit.mcp_results import tool_annotations as _annotations
from desk_crit.mcp_results import json_text, result as _result, run as _run

INSTRUCTIONS = (
    "Desk Crit turns a screen recording with spoken feedback into notes Claude can act on. "
    "Every tool except job_status takes the absolute path of the recording. Times are seconds "
    "in the recording. transcribe returns a job_id; call job_status with wait=50 until it is "
    "done. The plugin needs one model on this Mac, downloaded once: Parakeet (2.47 GB from "
    "Hugging Face) writes the transcript. When transcribe answers needs_models, ask the creator "
    "before you pass download_models; nothing downloads without a yes. read_transcript reads the "
    "words in pages. find_words lists every place a word is said, with its time, so you can find "
    "the moments the creator pointed at something. frames shows what was on screen at any times "
    "you pass, as images. No tool deletes or overwrites a file."
)

mcp = MCPServer("desk-crit", instructions=INSTRUCTIONS)

VideoPath = Annotated[str, Field(description="Absolute path to the recording.")]


def _call(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> CallToolResult:
    return _result(_run(fn, *args, **kwargs))


@mcp.tool(title="Transcribe", annotations=_annotations("Transcribe", read_only=False))
def transcribe(
    video_path: VideoPath,
    force: Annotated[bool, Field(description="Re-transcribe even if a transcript exists.")] = False,
    download_models: Annotated[
        bool,
        Field(description="Download the model transcribe said was missing. Pass true only after the creator said yes to what needs_models listed."),
    ] = False,
) -> CallToolResult:
    """Transcribe the recording. Returns {job_id}; or {status: "exists", words_path, word_count, duration} when a transcript is already there. When the speech model is not on this Mac it downloads nothing and returns {status: "needs_models", models: [{name, size_mb, source_host, license}], total_mb, note}: tell the creator what would download, how big, from where and under which license, and ask. Only when they say yes call transcribe again with download_models true: it returns {status: "downloading", job_id}, and when that job is done call transcribe again."""
    return _call(tools.transcribe, video_path, force, download_models)


@mcp.tool(title="Read transcript", annotations=_annotations("Read transcript", read_only=True))
def read_transcript(
    video_path: VideoPath,
    start: Annotated[float | None, Field(description="Window start, seconds in the recording.")] = None,
    end: Annotated[float | None, Field(description="Window end, seconds in the recording.")] = None,
) -> CallToolResult:
    """Packed transcript as plain text, one line per sentence or two, split at pauses and sentence ends. Each line starts with its start and end in seconds. The text ends with `NEXT <time>` (pass it as start to continue) or `END`."""
    data = _run(tools.read_transcript, video_path, start, end)
    return _result(data, text=data["text"])


@mcp.tool(title="Find words", annotations=_annotations("Find words", read_only=True))
def find_words(
    video_path: VideoPath,
    words: Annotated[list[str], Field(description='One to five words or short phrases to find, such as ["this", "right here"].')],
    start: Annotated[float | None, Field(description="Go on from here, seconds in the recording: the time after NEXT.")] = None,
) -> CallToolResult:
    """Every place a word is said, as plain text, one place a line: an id, the clock time, and six words before and after. A creator who points says words like this, here and that as the cursor lands on the thing, so use it to find those moments, then pass the times to frames. The id holds the word's start and end in seconds. The text ends with `NEXT <time>` (pass it as start to continue) or `END`."""
    data = _run(tools.find_words, video_path, words, start)
    return _result(data, text=data["text"])


@mcp.tool(title="Screenshots from the recording", annotations=_annotations("Screenshots from the recording", read_only=False))
def frames(
    video_path: VideoPath,
    times: Annotated[
        list[float],
        Field(description="Seconds in the recording, 1 to 6 of them, each from 0 to the recording's length."),
    ],
    region: Annotated[
        list[float] | None,
        Field(
            description=(
                "[left, top, right, bottom] as fractions of the frame, 0 to 1, to zoom in on part of the screen, "
                "such as [0.5, 0.5, 1, 1] for the bottom right quarter. Applies to every time. Leave out for the whole screen."
            )
        ),
    ] = None,
) -> CallToolResult:
    """Screenshots of the recording, as images you can see, one per time, saved in the project's frames folder. Each is the frame that was on screen at that moment, not the next one: a screen recording only writes a frame when the picture changes. At most 6 a call. Pass region to zoom in on small text or a button. Each image comes with a line giving its time as m:ss; the last block is JSON with at, shown (the time of the frame actually on screen), clock, path, width and height for each frame, and source_size. Works with no transcript."""
    data = _run(tools.frames, video_path, times, region)
    content: list[TextContent | ImageContent] = []
    total = len(data["frames"])
    for i, frame in enumerate(data["frames"], start=1):
        content.append(TextContent(type="text", text=f"Frame {i} of {total} at {frame['clock']} ({frame['at']:.2f} s)"))
        mime = "image/png" if frame["path"].endswith(".png") else "image/jpeg"
        with open(frame["path"], "rb") as fh:
            picture = base64.b64encode(fh.read()).decode("ascii")
        content.append(ImageContent(type="image", data=picture, mime_type=mime))
    content.append(json_text(data))
    return CallToolResult(content=content, structured_content=data)


@mcp.tool(title="Job status", annotations=_annotations("Job status", read_only=True))
def job_status(
    job_id: Annotated[str, Field(description="Id returned by transcribe.")],
    wait: Annotated[
        float, Field(description="Seconds to wait for the job to finish before answering, at most 50.")
    ] = 0.0,
) -> CallToolResult:
    """Status (running, done, failed), progress 0 to 1 when known, and the result or error. Pass wait=50 to hold the call until the job finishes."""
    return _call(tools.job_status, job_id, wait)


def main() -> None:
    """Console entry point: serve over stdio."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
