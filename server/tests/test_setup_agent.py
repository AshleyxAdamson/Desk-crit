"""tools/setup-agent.sh: sets Desk Crit up for OpenCode and Codex, and prints a snippet for the rest.

Every test copies the script and the skill folder into a fake repo root in a temp
folder, and runs the copy under ``/bin/sh`` with ``HOME`` pointed at another temp
folder. Nothing touches the real home folder or the real checkout.

``uv`` is a stub (``DESK_CRIT_UV``). It writes each call to a file. ``uv sync``
does nothing, because the fake root has no environment to build. ``uv run ...
python`` hands the script to this test's own Python, so the JSON edit really
runs. A fake root, not the real checkout, because the real one has a built
``server/.venv`` that would make the script skip the sync, and the sync step is
one of the things worth testing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "setup-agent.sh"

UV_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$1" in
  sync) exit 0 ;;
  run)
    shift
    while [ $# -gt 0 ] && [ "$1" != python ]; do shift; done
    shift
    exec "{python}" "$@" ;;
esac
exit 0
"""


class Setup:
    def __init__(self, tmp: Path):
        self.root = (tmp / "repo").resolve()
        (self.root / "tools").mkdir(parents=True)
        shutil.copy(SCRIPT, self.root / "tools" / "setup-agent.sh")
        shutil.copytree(REPO / "skills" / "desk-crit", self.root / "skills" / "desk-crit")
        (self.root / "server").mkdir()
        self.home = (tmp / "home").resolve()
        self.home.mkdir()
        self.calls = tmp / "uv-calls.txt"
        self.uv = tmp / "uv"
        self.uv.write_text(UV_STUB.format(calls=self.calls, python=sys.executable))
        self.uv.chmod(0o755)
        self.skill = self.root / "skills" / "desk-crit"
        self.opencode_config = self.home / ".config" / "opencode" / "opencode.json"
        self.opencode_backup = self.opencode_config.with_name("opencode.json.bak-desk-crit")
        self.opencode_link = self.home / ".config" / "opencode" / "skills" / "desk-crit"
        self.codex_config = self.home / ".codex" / "config.toml"
        self.codex_backup = self.codex_config.with_name("config.toml.bak-desk-crit")
        self.codex_link = self.home / ".agents" / "skills" / "desk-crit"

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "DESK_CRIT_UV": str(self.uv)}
        return subprocess.run(
            ["/bin/sh", str(self.root / "tools" / "setup-agent.sh"), *args],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def uv_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def want_entry(self) -> dict:
        return {
            "type": "local",
            "command": [str(self.uv), "run", "--locked", "--project", str(self.root / "server"), "desk-crit-server"],
            "enabled": True,
            "environment": {"HF_HUB_DISABLE_TELEMETRY": "1"},
            "timeout": 60000,
        }


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def lines_are_sentences(result: subprocess.CompletedProcess):
    """Every line printed to stdout is a plain sentence: it ends with a full stop."""
    for line in result.stdout.splitlines():
        assert line.endswith("."), line


# ---- OpenCode ---------------------------------------------------------------

def test_opencode_creates_the_config_and_the_link_when_there_are_none(setup):
    r = setup.run("opencode")
    assert r.returncode == 0, r.stderr
    data = json.loads(setup.opencode_config.read_text())
    assert data == {"$schema": "https://opencode.ai/config.json", "mcp": {"desk-crit": setup.want_entry()}}
    assert not setup.opencode_backup.exists(), "there was no file to back up"
    assert setup.opencode_link.is_symlink()
    assert os.readlink(setup.opencode_link) == str(setup.skill)
    assert (setup.opencode_link / "SKILL.md").is_file()
    lines_are_sentences(r)


def test_opencode_builds_the_environment_first(setup):
    setup.run("opencode")
    calls = setup.uv_calls()
    assert calls[0] == f"sync --locked --project {setup.root / 'server'}"
    assert all(c.startswith("run --locked --project") for c in calls[1:])


def test_opencode_skips_the_sync_when_the_environment_is_built(setup):
    launcher = setup.root / "server" / ".venv" / "bin" / "desk-crit-server"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("")
    r = setup.run("opencode")
    assert r.returncode == 0, r.stderr
    assert not any(c.startswith("sync") for c in setup.uv_calls())
    assert "skipped the sync" in r.stdout


def test_opencode_patches_an_existing_config_and_keeps_every_other_key(setup):
    original = {
        "$schema": "https://opencode.ai/config.json",
        "theme": "tokyonight",
        "model": "anthropic/claude-sonnet",
        "mcp": {"other": {"type": "remote", "url": "https://example.com/mcp", "enabled": False}},
        "keybinds": {"leader": "ctrl+x"},
    }
    setup.opencode_config.parent.mkdir(parents=True)
    setup.opencode_config.write_text(json.dumps(original))
    r = setup.run("opencode")
    assert r.returncode == 0, r.stderr
    data = json.loads(setup.opencode_config.read_text())
    assert data["mcp"]["desk-crit"] == setup.want_entry()
    assert data["mcp"]["other"] == original["mcp"]["other"]
    for key in ("$schema", "theme", "model", "keybinds"):
        assert data[key] == original[key]
    assert set(data) == set(original)
    assert json.loads(setup.opencode_backup.read_text()) == original


def test_opencode_adds_an_mcp_key_when_the_config_has_none(setup):
    setup.opencode_config.parent.mkdir(parents=True)
    setup.opencode_config.write_text('{"theme": "x"}')
    assert setup.run("opencode").returncode == 0
    assert json.loads(setup.opencode_config.read_text()) == {"theme": "x", "mcp": {"desk-crit": setup.want_entry()}}


def test_opencode_replaces_an_old_desk_crit_entry(setup):
    setup.opencode_config.parent.mkdir(parents=True)
    setup.opencode_config.write_text(json.dumps({"mcp": {"desk-crit": {"type": "local", "command": ["old"]}}}))
    r = setup.run("opencode")
    assert r.returncode == 0, r.stderr
    assert json.loads(setup.opencode_config.read_text())["mcp"]["desk-crit"] == setup.want_entry()
    assert "Updated" in r.stdout


def test_opencode_writes_the_backup_once(setup):
    setup.opencode_config.parent.mkdir(parents=True)
    setup.opencode_config.write_text('{"theme": "first"}')
    setup.run("opencode")
    assert json.loads(setup.opencode_backup.read_text()) == {"theme": "first"}
    # The user changes the config and the entry, then runs it again: the backup stays the first one.
    data = json.loads(setup.opencode_config.read_text())
    data["theme"] = "second"
    data["mcp"]["desk-crit"]["timeout"] = 1
    setup.opencode_config.write_text(json.dumps(data))
    setup.run("opencode")
    assert json.loads(setup.opencode_backup.read_text()) == {"theme": "first"}
    assert json.loads(setup.opencode_config.read_text())["theme"] == "second"


def test_opencode_is_idempotent(setup):
    setup.opencode_config.parent.mkdir(parents=True)
    setup.opencode_config.write_text('{"theme": "x"}')
    assert setup.run("opencode").returncode == 0
    config = setup.opencode_config.read_text()
    backup = setup.opencode_backup.read_text()
    setup.calls.unlink()
    r = setup.run("opencode")
    assert r.returncode == 0, r.stderr
    assert setup.opencode_config.read_text() == config
    assert setup.opencode_backup.read_text() == backup
    assert os.readlink(setup.opencode_link) == str(setup.skill)
    assert "already up to date" in r.stdout
    assert "already points" in r.stdout
    assert "Added" not in r.stdout and "Saved a backup" not in r.stdout
    lines_are_sentences(r)


def test_opencode_leaves_a_config_it_cant_read_alone(setup):
    setup.opencode_config.parent.mkdir(parents=True)
    text = '{\n  // a comment\n  "theme": "x"\n}\n'
    setup.opencode_config.write_text(text)
    r = setup.run("opencode")
    assert r.returncode == 1
    assert "left it alone" in r.stderr
    assert setup.opencode_config.read_text() == text
    assert not setup.opencode_backup.exists()
    assert not setup.opencode_link.exists() and not setup.opencode_link.is_symlink()


def test_opencode_remove_deletes_only_its_entry_and_its_link(setup):
    setup.opencode_config.parent.mkdir(parents=True)
    original = {"theme": "x", "mcp": {"other": {"type": "local", "command": ["x"]}}}
    setup.opencode_config.write_text(json.dumps(original))
    setup.run("opencode")
    r = setup.run("opencode", "--remove")
    assert r.returncode == 0, r.stderr
    assert json.loads(setup.opencode_config.read_text()) == original
    assert not setup.opencode_link.is_symlink() and not setup.opencode_link.exists()
    assert setup.skill.is_dir(), "the skill itself stays"
    assert setup.opencode_backup.exists(), "a backup is never deleted"
    assert "Removed the desk-crit entry" in r.stdout and "Removed the skill link" in r.stdout
    lines_are_sentences(r)
    again = setup.run("opencode", "--remove")
    assert again.returncode == 0
    assert "nothing to remove" in again.stdout
    assert json.loads(setup.opencode_config.read_text()) == original


def test_opencode_remove_with_nothing_installed_changes_nothing(setup):
    r = setup.run("opencode", "--remove")
    assert r.returncode == 0, r.stderr
    assert not setup.opencode_config.exists()
    assert setup.uv_calls() == []
    assert "nothing to remove" in r.stdout


def test_opencode_remove_leaves_a_link_that_points_elsewhere(setup, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    setup.opencode_link.parent.mkdir(parents=True)
    setup.opencode_link.symlink_to(elsewhere)
    r = setup.run("opencode", "--remove")
    assert r.returncode == 0
    assert setup.opencode_link.is_symlink() and os.readlink(setup.opencode_link) == str(elsewhere)
    assert "points somewhere else" in r.stdout


def test_opencode_replaces_a_link_that_points_elsewhere(setup, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    setup.opencode_link.parent.mkdir(parents=True)
    setup.opencode_link.symlink_to(elsewhere)
    r = setup.run("opencode")
    assert r.returncode == 0, r.stderr
    assert os.readlink(setup.opencode_link) == str(setup.skill)


# ---- Codex ------------------------------------------------------------------

def codex_block(setup: Setup) -> str:
    return (
        "[mcp_servers.desk-crit]\n"
        f'command = "{setup.uv}"\n'
        f'args = ["run", "--locked", "--project", "{setup.root / "server"}", "desk-crit-server"]\n'
        'env = { HF_HUB_DISABLE_TELEMETRY = "1" }\n'
        "startup_timeout_sec = 60.0\n"
    )


def test_codex_creates_the_config_and_the_link_when_there_are_none(setup):
    r = setup.run("codex")
    assert r.returncode == 0, r.stderr
    assert setup.codex_config.read_text() == codex_block(setup)
    assert not setup.codex_backup.exists()
    assert os.readlink(setup.codex_link) == str(setup.skill)
    assert setup.uv_calls() == [f"sync --locked --project {setup.root / 'server'}"]
    lines_are_sentences(r)


def test_codex_appends_once_and_keeps_the_rest(setup):
    original = 'model = "gpt-5"\n\n[tui]\nnotifications = true\n\n[mcp_servers.other]\ncommand = "x"\n'
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text(original)
    r = setup.run("codex")
    assert r.returncode == 0, r.stderr
    assert setup.codex_config.read_text() == original + "\n" + codex_block(setup)
    assert setup.codex_backup.read_text() == original


def test_codex_adds_a_newline_when_the_file_has_none_at_the_end(setup):
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text('model = "x"')
    assert setup.run("codex").returncode == 0
    assert setup.codex_config.read_text() == 'model = "x"\n\n' + codex_block(setup)


def test_codex_is_idempotent(setup):
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text('model = "x"\n')
    setup.run("codex")
    after_first = setup.codex_config.read_text()
    r = setup.run("codex")
    assert r.returncode == 0, r.stderr
    assert setup.codex_config.read_text() == after_first
    assert after_first.count("[mcp_servers.desk-crit]") == 1
    assert setup.codex_backup.read_text() == 'model = "x"\n'
    assert "already there" in r.stdout and "already points" in r.stdout
    lines_are_sentences(r)


def test_codex_remove_deletes_the_block_and_leaves_other_tables(setup):
    original = 'model = "gpt-5"\n\n[tui]\nnotifications = true\n\n[mcp_servers.other]\ncommand = "x"\n'
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text(original)
    setup.run("codex")
    r = setup.run("codex", "--remove")
    assert r.returncode == 0, r.stderr
    assert setup.codex_config.read_text() == original
    assert not setup.codex_link.is_symlink()
    assert setup.codex_backup.exists(), "a backup is never deleted"
    assert "Removed the [mcp_servers.desk-crit] block" in r.stdout
    lines_are_sentences(r)
    again = setup.run("codex", "--remove")
    assert again.returncode == 0 and "nothing to remove" in again.stdout
    assert setup.codex_config.read_text() == original


def test_codex_remove_stops_at_the_next_header(setup):
    # The block sits in the middle of the file, with a sub-table of its own.
    text = (
        "[tui]\nnotifications = true\n\n"
        + codex_block(setup)
        + "\n[mcp_servers.desk-crit.env]\nEXTRA = \"1\"\n\n"
        + "[mcp_servers.other]\ncommand = \"x\"\n"
    )
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text(text)
    r = setup.run("codex", "--remove")
    assert r.returncode == 0, r.stderr
    assert setup.codex_config.read_text() == '[tui]\nnotifications = true\n\n[mcp_servers.other]\ncommand = "x"\n'


def test_codex_remove_of_a_block_in_the_middle_keeps_the_gap_before_the_next_table(setup):
    text = "[a]\nx = 1\n\n" + codex_block(setup) + "\n[b]\ny = 2\n"
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text(text)
    assert setup.run("codex", "--remove").returncode == 0
    assert setup.codex_config.read_text() == "[a]\nx = 1\n\n[b]\ny = 2\n"


def test_codex_ignores_a_commented_out_header(setup):
    setup.codex_config.parent.mkdir(parents=True)
    setup.codex_config.write_text("# [mcp_servers.desk-crit]\n")
    assert setup.run("codex").returncode == 0
    assert setup.codex_config.read_text().count("[mcp_servers.desk-crit]") == 2


# ---- print ------------------------------------------------------------------

def test_print_shows_the_command_the_folder_and_the_snippet_and_runs_nothing(setup):
    r = setup.run("print")
    assert r.returncode == 0, r.stderr
    assert setup.uv_calls() == [], "print must not call uv"
    assert not (setup.home / ".config").exists() and not (setup.home / ".codex").exists()
    assert not (setup.home / ".agents").exists()
    assert f"{setup.uv} run --locked --project {setup.root / 'server'} desk-crit-server" in r.stdout
    assert f"{setup.uv} sync --locked --project {setup.root / 'server'}" in r.stdout
    assert str(setup.skill) in r.stdout
    start = r.stdout.index("{")
    snippet = json.loads(r.stdout[start:])
    assert snippet == {"mcpServers": {"desk-crit": {
        "command": str(setup.uv),
        "args": ["run", "--locked", "--project", str(setup.root / "server"), "desk-crit-server"],
        "env": {"HF_HUB_DISABLE_TELEMETRY": "1"},
    }}}


# ---- refusals and bad usage -------------------------------------------------

@pytest.mark.parametrize("mode", ["opencode", "codex"])
def test_a_real_folder_at_the_skill_path_is_refused_before_anything_changes(setup, mode):
    link = setup.opencode_link if mode == "opencode" else setup.codex_link
    config = setup.opencode_config if mode == "opencode" else setup.codex_config
    link.mkdir(parents=True)
    (link / "mine.txt").write_text("keep me")
    r = setup.run(mode)
    assert r.returncode == 1
    assert "real folder" in r.stderr and str(link) in r.stderr
    assert (link / "mine.txt").read_text() == "keep me"
    assert not link.is_symlink()
    assert not config.exists(), "the config isn't touched when the link is refused"
    assert setup.uv_calls() == []


@pytest.mark.parametrize("mode", ["opencode", "codex"])
def test_remove_never_deletes_a_real_folder(setup, mode):
    link = setup.opencode_link if mode == "opencode" else setup.codex_link
    link.mkdir(parents=True)
    (link / "mine.txt").write_text("keep me")
    r = setup.run(mode, "--remove")
    assert r.returncode == 0
    assert (link / "mine.txt").read_text() == "keep me"
    assert "real folder" in r.stdout


@pytest.mark.parametrize("args", [[], ["nope"], ["opencode", "codex"], ["opencode", "--force"], ["print", "--remove"], ["--remove"]])
def test_bad_usage_exits_2_and_changes_nothing(setup, args):
    r = setup.run(*args)
    assert r.returncode == 2
    assert "usage:" in r.stderr
    assert setup.uv_calls() == []
    assert not (setup.home / ".config").exists() and not (setup.home / ".codex").exists()


def test_help_exits_0(setup):
    r = setup.run("--help")
    assert r.returncode == 0
    assert "usage:" in r.stderr
