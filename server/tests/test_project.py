"""The project folder: where it lives, the sidecar beside the video, and the writes that never overwrite."""

import json
import os
from pathlib import Path

import pytest

from desk_crit import project as projects
from desk_crit.errors import StudioError
from desk_crit.project import (
    append_receipt, open_project, project_dir_name, projects_root, reserve_unique_path, words_path_for,
    write_json_atomic, write_text_atomic,
)


def test_the_root_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_CRIT_PROJECTS_DIR", str(tmp_path / "somewhere"))
    assert projects_root() == tmp_path / "somewhere"


def test_the_root_follows_the_environment_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_CRIT_PROJECTS_DIR", str(tmp_path / "one"))
    first = projects_root()
    monkeypatch.setenv("DESK_CRIT_PROJECTS_DIR", str(tmp_path / "two"))
    assert (first, projects_root()) == (tmp_path / "one", tmp_path / "two")


def test_without_an_override_the_root_sits_under_the_lumr_home(tmp_path, monkeypatch):
    monkeypatch.delenv("DESK_CRIT_PROJECTS_DIR")
    assert projects_root() == tmp_path / "lumr-home" / "desk-crit" / "projects"


def test_the_lumr_home_falls_back_to_the_saved_folder_then_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("DESK_CRIT_PROJECTS_DIR")
    monkeypatch.delenv("LUMR_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    assert projects_root() == tmp_path / "me" / "Lumr" / "desk-crit" / "projects"
    saved = tmp_path / "me" / ".config" / "lumr" / "home.txt"
    saved.parent.mkdir(parents=True)
    saved.write_text(f"{tmp_path / 'elsewhere'}\n")
    assert projects_root() == tmp_path / "elsewhere" / "desk-crit" / "projects"


def test_the_transcript_sidecar_sits_beside_the_video_with_the_same_stem(video):
    assert words_path_for(video) == video.with_name("talk.words.json")
    assert open_project(str(video)).words_path == video.resolve().with_name("talk.words.json")


def test_the_sidecar_name_is_the_one_lumr_studio_uses():
    assert words_path_for(Path("/x/My Recording.mov")) == Path("/x/My Recording.words.json")


def test_open_project_makes_only_the_root(video):
    root = projects_root()
    assert not root.exists()
    project = open_project(str(video))
    assert project.root.parent == root and project.root.is_dir()
    assert list(project.root.iterdir()) == []
    assert [p.name for p in root.iterdir()] == [project.root.name]
    assert not project.frames_dir.exists()


def test_the_project_folder_is_named_from_the_stem_and_the_resolved_path(video, tmp_path):
    project = open_project(str(video))
    name = project_dir_name(video.resolve())
    assert project.root.name == name and name.startswith("talk-") and len(name) == len("talk-") + 12
    elsewhere = tmp_path / "other" / "talk.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    assert open_project(str(elsewhere)).root != project.root


def test_opening_twice_is_the_same_project(video):
    assert open_project(str(video)) == open_project(str(video))


def test_paths_that_are_not_a_file_are_refused_in_plain_words(tmp_path):
    with pytest.raises(StudioError, match="empty"):
        open_project("  ")
    with pytest.raises(StudioError, match="relative"):
        open_project("talk.mp4")
    with pytest.raises(StudioError, match="No file at"):
        open_project(str(tmp_path / "nope.mp4"))
    with pytest.raises(StudioError, match="directory"):
        open_project(str(tmp_path))


def test_the_duration_is_probed(video):
    assert open_project(str(video)).duration() == pytest.approx(20.0, abs=0.1)


def test_a_file_that_is_not_a_video_has_no_duration(tmp_path):
    junk = tmp_path / "notes.mp4"
    junk.write_text("not a video")
    with pytest.raises(StudioError, match="could not read a duration"):
        open_project(str(junk)).duration()


def test_reserve_unique_path_never_overwrites(tmp_path):
    folder = tmp_path / "frames"
    first = reserve_unique_path(folder, "frame", ".png")
    first.write_bytes(b"one")
    second = reserve_unique_path(folder, "frame", ".png")
    third = reserve_unique_path(folder, "frame", ".png")
    assert [p.name for p in (first, second, third)] == ["frame.png", "frame-2.png", "frame-3.png"]
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"" and second.exists()  # claimed, so a second caller can't take it


def test_write_text_atomic_replaces_the_file_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out" / "note.txt"
    write_text_atomic(target, "one")
    write_text_atomic(target, "two")
    assert target.read_text() == "two" and [p.name for p in target.parent.iterdir()] == ["note.txt"]


def test_write_json_atomic_writes_indented_utf8_with_a_final_newline(tmp_path):
    target = tmp_path / "data.json"
    write_json_atomic(target, {"word": "café"})
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n") and "café" in text and json.loads(text) == {"word": "café"}


def test_receipts_append_one_line_each(video):
    project = open_project(str(video))
    append_receipt(project, "frames", times=[1.0])
    append_receipt(project, "transcribe", status="done")
    lines = [json.loads(line) for line in project.receipts_path.read_text().splitlines()]
    assert [line["step"] for line in lines] == ["frames", "transcribe"] and lines[0]["times"] == [1.0]
    assert all(line["at"].endswith("+00:00") for line in lines)


def test_the_projects_module_does_not_read_the_lumr_studio_variable(video, tmp_path, monkeypatch):
    monkeypatch.setenv("LUMR_STUDIO_PROJECTS_DIR", str(tmp_path / "lumr-studio-projects"))
    assert open_project(str(video)).root.parent == tmp_path / "projects"
    assert not os.path.exists(tmp_path / "lumr-studio-projects")
    assert projects.PROJECTS_DIR_ENV == "DESK_CRIT_PROJECTS_DIR"
