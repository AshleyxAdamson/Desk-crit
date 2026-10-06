"""The skill names only tools the server has."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "desk-crit" / "SKILL.md"
TOOLS = {"transcribe", "read_transcript", "find_words", "frames", "job_status"}
# Backticked names in the skill that are not tools: the tools' own words and fields.
NOT_TOOLS = {
    "needs_models", "download_models", "wait", "start", "end", "times", "region", "words", "job_id",
    "status", "models", "source_host", "clock", "force", "exists", "downloading",
}


def test_the_skill_is_there():
    assert SKILL.is_file(), f"{SKILL} is not written yet"


@pytest.mark.skipif(not SKILL.is_file(), reason="the skill is not written yet")
def test_every_tool_the_skill_names_is_one_of_the_five():
    names = set(re.findall(r"`([a-z]+(?:_[a-z]+)*)`", SKILL.read_text()))
    named_as_tools = {n for n in names if n in TOOLS or (n not in NOT_TOOLS and "_" in n)}
    assert named_as_tools <= TOOLS, f"names a tool the server doesn't have: {sorted(named_as_tools - TOOLS)}"
    assert {"transcribe", "read_transcript", "find_words", "frames", "job_status"} <= names


@pytest.mark.skipif(not SKILL.is_file(), reason="the skill is not written yet")
def test_the_skill_names_no_tool_from_lumr_studio():
    text = SKILL.read_text()
    for gone in ("set_edit", "get_edit", "analyze_take", "preview", "render", "look", "review"):
        assert f"`{gone}`" not in text, gone
