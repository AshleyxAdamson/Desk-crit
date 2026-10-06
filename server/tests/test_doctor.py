"""The first-run check (hooks/doctor.sh): silent when all is well, one line per problem otherwise.

Every scenario runs the real script under ``/bin/sh`` with a PATH that holds only
stub programs, so the result doesn't depend on this machine's ffmpeg, uv or CPU.
``uv`` is a stub too: it records how it was called and never builds anything.
"""

import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from shutil import which

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[2]
DOCTOR = PLUGIN_DIR / "hooks" / "doctor.sh"

# What the hook says while the environment builds. Spelled out in full on
# purpose: this is the text a person ends up reading, so a change to it should
# show up here as a change.
BUILDING_LINE = (
    "Desk Crit is building its Python environment in the background "
    "(about 530 MB, a few minutes). Tell the user the desk-crit server shows "
    "as failed until it's done, and that's expected. When it's done, they run "
    "/mcp and choose Reconnect on desk-crit, or start a new session.\n"
)


def failed_before(data_dir: Path) -> str:
    """What the hook adds to its line when the last build failed."""
    return (f" The last build failed; if this retry fails too, tell the user to look at "
            f"{data_dir / 'build.log'} to see why.")


# Desk Crit only decodes frames, so the check looks for the programs and asks
# nothing of them: this stub answers every question the same way, with a
# version and an encoder list that an old or minimal ffmpeg would give.
FFMPEG_STUB = """#!/bin/sh
case "$1" in
  -version) echo "ffmpeg version {version} Copyright (c) the FFmpeg developers" ;;
  *) echo " V....D {encoder}  an encoder" ;;
esac
"""

# `uv sync` writes one line to the calls file and its own pid to a second one,
# waits for the release file when there is one, then leaves the launcher behind,
# as a finished build does.
UV_TEMPLATE = """#!/bin/sh
printf '%s|%s|%s\\n' "$*" "$VIRTUAL_ENV" "$HF_HUB_DISABLE_TELEMETRY" >> "@CALLS@"
echo "$$" > "@CALLS@.pid"
echo "stub uv: building"
if [ -n "@HOLD@" ]; then
  n=0
  while [ ! -e "@HOLD@" ] && [ "$n" -lt 400 ]; do /bin/sleep 0.05; n=$((n + 1)); done
fi
if [ @STATUS@ -eq 0 ]; then
  mkdir -p "$VIRTUAL_ENV/bin"
  : > "$VIRTUAL_ENV/bin/desk-crit-server"
fi
exit @STATUS@
"""

# What the script needs from the real system besides the stubs.
REAL_TOOLS = ("sh", "mkdir", "rm", "nohup", "ps", "find")


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body if body.startswith("#!") else f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


LAUNCHER = "venv/bin/desk-crit-server"


def build_env(data_dir: Path) -> None:
    """What a finished first start leaves in the plugin data folder."""
    launcher = data_dir / LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)


def wait_until(condition, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def build_over(data_dir: Path, timeout=10.0) -> bool:
    """True once the background build has ended and let go of its lock."""
    return wait_until(lambda: not (data_dir / "build.lock").exists(), timeout)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def uv_calls(tmp_path: Path) -> list[str]:
    """The `uv sync` calls the stub saw, one entry each."""
    calls = tmp_path / "uv-calls"
    return calls.read_text().splitlines() if calls.exists() else []


def hook_env(bin_dir: Path, plugin_data: Path | None) -> dict:
    env = {"PATH": str(bin_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_DIR)}
    if plugin_data is not None:
        env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    return env


def make_bin(tmp_path: Path, *, system="Darwin", machine="arm64", rosetta=False,
             uv=True, ffmpeg="7.1", ffprobe=True, encoder="libx264", brew=True,
             hold: Path | None = None, uv_status=0, calls: Path | None = None) -> Path:
    """A PATH folder of stubs. ``ffmpeg=None`` leaves ffmpeg out.

    ``hold``: the stub build waits until that file exists. ``uv_status``: what
    the stub build exits with (nonzero builds no launcher).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for tool in REAL_TOOLS:
        (bin_dir / tool).symlink_to(which(tool))
    stub(bin_dir, "uname", f'case "$1" in -s) echo {system};; -m) echo {machine};; esac')
    stub(bin_dir, "sysctl", f"echo {1 if rosetta else 0}")
    if uv:
        calls = calls or tmp_path / "uv-calls"
        body = (UV_TEMPLATE.replace("@CALLS@", str(calls))
                .replace("@HOLD@", str(hold) if hold else "")
                .replace("@STATUS@", str(uv_status)))
        stub(bin_dir, "uv", body)
    if brew:
        stub(bin_dir, "brew", "echo brew")
    if ffmpeg:
        stub(bin_dir, "ffmpeg", FFMPEG_STUB.format(version=ffmpeg, encoder=encoder))
    if ffprobe:
        stub(bin_dir, "ffprobe", "echo ffprobe")
    return bin_dir


def run_doctor(tmp_path: Path, *, plugin_data: Path | None = None, settle=True, **stubs):
    """Run the script with a PATH of stubs and return what it printed.

    ``plugin_data`` is what Claude Code exports as CLAUDE_PLUGIN_DATA. Left as
    None, the variable isn't set at all, as on a Claude Code without data folders.
    With a data folder the script starts a background build. ``settle`` waits for
    that build to end first, so a test reads what it left behind. Pass False
    when the test looks at the build while it runs.
    """
    bin_dir = make_bin(tmp_path, **stubs)
    done = subprocess.run(["/bin/sh", str(DOCTOR)], env=hook_env(bin_dir, plugin_data),
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    if settle and plugin_data is not None:
        assert build_over(plugin_data), "the background build never ended"
    return done.stdout


def problem_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("- ")]


def test_silent_when_all_is_well(tmp_path):
    assert run_doctor(tmp_path) == ""


# --- The first start: build the environment in the background ---------------


def test_first_start_starts_the_build_and_says_so_in_exactly_these_words(tmp_path):
    data = tmp_path / "data"
    out = run_doctor(tmp_path / "run", plugin_data=data)
    assert out == BUILDING_LINE
    # Not a problem to fix: no "can't run yet" header and no list line.
    assert problem_lines(out) == []
    # The build ran once, from the plugin's own server folder, and cleaned up.
    assert len(uv_calls(tmp_path / "run")) == 1
    assert (data / LAUNCHER).exists() and not (data / "build.lock").exists()
    # Its output went to a log a person can open, not to the hook's pipes.
    assert "stub uv: building" in (data / "build.log").read_text()


def test_the_build_is_the_servers_launch_line_minus_the_server(tmp_path):
    # hooks/start-server.sh says how the server starts and .mcp.json hands it the
    # two folders. The build must run the same project with the same locked file
    # and environment, or the server would rebuild.
    launcher = (PLUGIN_DIR / "hooks" / "start-server.sh").read_text()
    server = json.loads((PLUGIN_DIR / ".mcp.json").read_text())["mcpServers"]["desk-crit"]
    data = tmp_path / "data"
    run_doctor(tmp_path / "run", plugin_data=data)

    def resolved(text: str) -> str:
        return text.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_DIR)).replace(
            "${CLAUDE_PLUGIN_DATA}", str(data))

    # The launcher runs `sh start-server.sh <root> <data>`: the folders are $1 and $2.
    assert server["command"] == "sh" and "env" not in server
    script, root, folder = (resolved(a) for a in server["args"])
    assert script == str(PLUGIN_DIR / "hooks" / "start-server.sh")
    assert (root, folder) == (str(PLUGIN_DIR), str(data))

    def fill(word: str) -> str:
        return word.replace("$1", root).replace("$2", folder)

    (exec_line,) = [ln for ln in launcher.splitlines() if ln.startswith("exec ")]
    launch = [fill(word) for word in shlex.split(exec_line)[1:]]
    assert launch[0] == "uv" and launch[1] == "run" and launch[-1] == "desk-crit-server"
    (virtual_env,) = [ln for ln in launcher.splitlines() if ln.startswith("VIRTUAL_ENV=")]
    (telemetry_line,) = [ln for ln in launcher.splitlines() if ln.startswith("HF_HUB_DISABLE_TELEMETRY=")]

    (call,) = uv_calls(tmp_path / "run")
    args, project_env, telemetry = call.split("|")
    assert args.split() == ["sync", *launch[2:-1]]
    assert "--locked" in args.split() and "--active" in args.split()
    # No dev group: no flag asks for one, and the server's own launch doesn't either.
    assert "group" not in args and "dev" not in args
    assert project_env == fill(shlex.split(virtual_env)[0].split("=", 1)[1])
    assert project_env == f"{data}/venv"
    assert telemetry == telemetry_line.split("=", 1)[1]


def test_the_launch_has_no_env_block_and_every_path_is_under_the_plugin():
    # The validator flags any variable in the env block that steers uv, so the
    # launcher script sets them instead. Every path the launch names is under
    # the plugin's own folders, written the way Claude Code fills in.
    server = json.loads((PLUGIN_DIR / ".mcp.json").read_text())["mcpServers"]["desk-crit"]
    assert "env" not in server
    assert server["command"] == "sh"
    for arg in server["args"]:
        assert arg == "${CLAUDE_PLUGIN_DATA}" or arg.startswith("${CLAUDE_PLUGIN_ROOT}"), arg
    script = server["args"][0]
    assert script.startswith("${CLAUDE_PLUGIN_ROOT}/") and ".." not in script
    assert (PLUGIN_DIR / script.removeprefix("${CLAUDE_PLUGIN_ROOT}/")).is_file()


def test_no_build_and_no_words_once_the_launcher_exists(tmp_path):
    data = tmp_path / "data"
    build_env(data)
    assert run_doctor(tmp_path / "run", plugin_data=data) == ""
    assert uv_calls(tmp_path / "run") == []
    assert sorted(p.name for p in data.iterdir()) == ["venv"]


def test_the_build_starts_while_the_venv_folder_has_no_launcher(tmp_path):
    # uv makes the folder in its first milliseconds, long before the packages
    # arrive. Only the launcher script says the build finished.
    data = tmp_path / "data"
    (data / "venv" / "bin").mkdir(parents=True)
    assert run_doctor(tmp_path / "run", plugin_data=data) == BUILDING_LINE
    assert len(uv_calls(tmp_path / "run")) == 1


def test_no_build_when_no_data_folder_is_given(tmp_path):
    # An older Claude Code exports nothing. There's no folder to build in, and
    # the server builds the environment on its own.
    assert run_doctor(tmp_path, plugin_data=None) == ""
    assert uv_calls(tmp_path) == []


def test_no_build_without_uv_or_on_the_wrong_platform(tmp_path):
    data = tmp_path / "data"
    out = run_doctor(tmp_path / "run", uv=False, plugin_data=data)
    assert len(problem_lines(out)) == 1 and "building" not in out
    assert not (data / "build.lock").exists() and not (data / "build.log").exists()
    out = run_doctor(tmp_path / "wrong", machine="x86_64", plugin_data=data)
    assert len(problem_lines(out)) == 1 and "building" not in out
    assert uv_calls(tmp_path / "wrong") == []


def test_a_missing_ffmpeg_doesnt_hold_the_build_back(tmp_path):
    # The environment needs uv only. The user still hears about ffmpeg, and that
    # the server fails until the build is done.
    out = run_doctor(tmp_path / "run", ffmpeg=None, ffprobe=False, plugin_data=tmp_path / "data")
    assert out == (
        "Desk Crit can't run yet. Tell the user what's missing, and let them run the fixes:\n"
        "- ffmpeg and ffprobe aren't installed. Run: brew install ffmpeg\n"
    ) + BUILDING_LINE
    assert len(uv_calls(tmp_path / "run")) == 1


def test_the_hook_returns_at_once_and_the_build_keeps_going(tmp_path):
    data = tmp_path / "data"
    hold = tmp_path / "release"
    bin_dir = make_bin(tmp_path / "run", hold=hold)
    began = time.monotonic()
    # capture_output waits for the pipes to close, as Claude Code does, so a
    # build that held them open would make this wait for the build too.
    done = subprocess.run(["/bin/sh", str(DOCTOR)], env=hook_env(bin_dir, data),
                          capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - began
    try:
        assert done.returncode == 0 and done.stderr == ""
        assert done.stdout == BUILDING_LINE
        assert elapsed < 2.0
        # The hook is gone and the build is still running: the lock names it.
        assert wait_until(lambda: len(uv_calls(tmp_path / "run")) == 1)
        assert not (data / LAUNCHER).exists()
        builder = int((data / "build.lock" / "pid").read_text())
        os.kill(builder, 0)
    finally:
        hold.touch()
    assert build_over(data) and (data / LAUNCHER).exists()


def test_the_build_is_out_of_the_hooks_process_group_and_survives_its_kill(tmp_path):
    data = tmp_path / "data"
    hold = tmp_path / "release"
    bin_dir = make_bin(tmp_path / "run", hold=hold)
    hook = subprocess.Popen(["/bin/sh", str(DOCTOR)], env=hook_env(bin_dir, data),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)  # the hook leads its own group
    try:
        hook.communicate(timeout=30)
        assert wait_until(lambda: (data / "build.lock" / "pid").exists())
        builder = int((data / "build.lock" / "pid").read_text())
        assert os.getpgid(builder) != hook.pid
        # Kill everything still in the hook's group. The group may be empty
        # already, and then there's nothing to kill.
        try:
            os.killpg(hook.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.2)
        os.kill(builder, 0)  # raises if the build died with the group
    finally:
        hold.touch()
    assert build_over(data) and (data / LAUNCHER).exists()


def test_a_build_that_fails_leaves_no_lock_and_the_next_session_tries_again(tmp_path):
    data = tmp_path / "data"
    calls = tmp_path / "uv-calls"
    first = run_doctor(tmp_path / "first", plugin_data=data, uv_status=1, calls=calls)
    assert first == BUILDING_LINE  # nothing has failed yet, so nothing to add
    second = run_doctor(tmp_path / "second", plugin_data=data, uv_status=1, calls=calls)
    assert second == BUILDING_LINE.removesuffix("\n") + failed_before(data) + "\n"
    assert len(calls.read_text().splitlines()) == 2
    assert not (data / LAUNCHER).exists()
    assert "uv sync finished with status 1" in (data / "build.log").read_text()


@pytest.mark.parametrize("log, failed", [
    ("uv sync finished with status 1\n", True),
    ("uv sync finished with status 130\n", True),
    ("uv sync finished with status 0\n", False),
    ("Building the Desk Crit environment in /x/venv\n", False),  # killed hard: no last line
    ("", False),
])
def test_the_line_says_the_last_build_failed_only_when_its_log_says_so(tmp_path, log, failed):
    data = tmp_path / "data"
    data.mkdir()
    (data / "build.log").write_text(log)
    out = run_doctor(tmp_path / "run", plugin_data=data)
    assert out == BUILDING_LINE.removesuffix("\n") + (failed_before(data) if failed else "") + "\n"
    assert len(uv_calls(tmp_path / "run")) == 1


def test_a_build_still_running_doesnt_repeat_the_failed_sentence(tmp_path):
    # The running build has already replaced the log, and the sentence would be
    # about a build that is no longer the last one.
    data = tmp_path / "data"
    other = fake_build(tmp_path)
    try:
        (data / "build.lock").mkdir(parents=True)
        (data / "build.lock" / "pid").write_text(f"{other.pid}\n")
        (data / "build.log").write_text("uv sync finished with status 1\n")
        out = run_doctor(tmp_path / "run", plugin_data=data, settle=False)
        assert out == BUILDING_LINE
    finally:
        other.kill()
        other.wait()


# --- A signal ends the build at once ------------------------------------------


@pytest.mark.parametrize("sig, status", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signal_stops_the_build_at_once_and_the_log_says_it_failed(tmp_path, sig, status):
    # uv stays held, as it does through a long download. A shell runs its trap
    # only when the foreground command returns, so a build that ran uv in the
    # foreground would sit here until uv finished.
    data = tmp_path / "data"
    hold = tmp_path / "release"
    calls = tmp_path / "uv-calls"
    run_doctor(tmp_path / "run", plugin_data=data, settle=False, hold=hold, calls=calls)
    try:
        assert wait_until(lambda: (data / "build.lock" / "pid").exists()
                          and Path(f"{calls}.pid").exists())
        builder = int((data / "build.lock" / "pid").read_text())
        uv = int(Path(f"{calls}.pid").read_text())
        os.kill(builder, sig)
        assert build_over(data, timeout=5.0), "the build ignored the signal until uv returned"
        # It took uv down with it, and said so in the log, with a nonzero status.
        assert wait_until(lambda: not pid_alive(uv), timeout=5.0)
        assert f"uv sync finished with status {status}" in (data / "build.log").read_text()
        assert not (data / LAUNCHER).exists()
    finally:
        hold.touch()


# --- One build at a time ------------------------------------------------------


def test_a_running_build_blocks_a_second_one_and_is_still_announced(tmp_path):
    data = tmp_path / "data"
    hold = tmp_path / "release"
    calls = tmp_path / "uv-calls"
    try:
        first = run_doctor(tmp_path / "one", plugin_data=data, settle=False, hold=hold, calls=calls)
        assert wait_until(lambda: len(calls.read_text().splitlines()) == 1 if calls.exists() else False)
        second = run_doctor(tmp_path / "two", plugin_data=data, settle=False, hold=hold, calls=calls)
        # Both sessions tell the user. Only one build runs.
        assert first == second == BUILDING_LINE
        time.sleep(0.3)
        assert len(calls.read_text().splitlines()) == 1
    finally:
        hold.touch()
    assert build_over(data)
    assert len(calls.read_text().splitlines()) == 1


def fake_build(tmp_path: Path) -> subprocess.Popen:
    """A live process whose command line is ours: `sh /…/build-env.sh`, waiting."""
    script = tmp_path / "fake" / "hooks" / "build-env.sh"
    script.parent.mkdir(parents=True)
    script.write_text("while :; do /bin/sleep 0.1; done\n")
    return subprocess.Popen(["/bin/sh", str(script), str(tmp_path / "fake" / "build.lock")])


def age(path: Path, seconds: float) -> None:
    """Make a file or folder look ``seconds`` old."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_live_lock_from_another_process_blocks_the_build(tmp_path):
    data = tmp_path / "data"
    (data / "build.lock").mkdir(parents=True)
    other = fake_build(tmp_path)
    try:
        (data / "build.lock" / "pid").write_text(f"{other.pid}\n")
        out = run_doctor(tmp_path / "run", plugin_data=data, settle=False)
        assert out == BUILDING_LINE
        assert uv_calls(tmp_path / "run") == []
        # The lock is still theirs.
        assert (data / "build.lock" / "pid").read_text().strip() == str(other.pid)
    finally:
        other.kill()
        other.wait()


def test_a_live_pid_that_isnt_our_build_is_a_stale_lock(tmp_path):
    # After a reboot or a kill -9 the build is gone and its pid can go to any
    # process. Trusting the number alone would say "building" for good.
    data = tmp_path / "data"
    lock = data / "build.lock"
    lock.mkdir(parents=True)
    stranger = subprocess.Popen(["/bin/sleep", "30"])
    try:
        (lock / "pid").write_text(f"{stranger.pid}\n")
        out = run_doctor(tmp_path / "run", plugin_data=data)
        assert out == BUILDING_LINE
        assert len(uv_calls(tmp_path / "run")) == 1
        assert (data / LAUNCHER).exists() and not lock.exists()
        assert stranger.poll() is None  # the hook takes over the lock, not the process
    finally:
        stranger.kill()
        stranger.wait()


def dead_pid() -> int:
    gone = subprocess.Popen(["/usr/bin/true"])
    gone.wait()
    return gone.pid


def make_lock(data: Path, pid_file: str | None, old: bool) -> Path:
    contents = {"dead": f"{dead_pid()}\n", "junk": "not a pid\n", "empty": ""}
    lock = data / "build.lock"
    lock.mkdir(parents=True)
    if pid_file is not None:
        (lock / "pid").write_text(contents[pid_file])
    if old:
        age(lock, 600)
    return lock


@pytest.mark.parametrize("pid_file", ["dead", "junk", "empty", None])
def test_a_stale_lock_is_taken_over(tmp_path, pid_file):
    # A dead pid is stale at any age. No usable pid is stale once it's old.
    data = tmp_path / "data"
    lock = make_lock(data, pid_file, old=pid_file != "dead")
    out = run_doctor(tmp_path / "run", plugin_data=data)
    assert out == BUILDING_LINE
    assert len(uv_calls(tmp_path / "run")) == 1
    assert (data / LAUNCHER).exists() and not lock.exists()


@pytest.mark.parametrize("pid_file", ["junk", "empty", None])
def test_a_young_lock_with_no_pid_yet_is_a_build_being_started(tmp_path, pid_file):
    # Another hook is between making the folder and writing the build's pid. Taking
    # the lock over now would start a second build and delete the first one's folder.
    data = tmp_path / "data"
    lock = make_lock(data, pid_file, old=False)
    before = (lock / "pid").read_text() if (lock / "pid").exists() else None
    out = run_doctor(tmp_path / "run", plugin_data=data, settle=False)
    assert out == BUILDING_LINE
    time.sleep(0.3)  # long enough for a build to start, if the hook wrongly began one
    assert uv_calls(tmp_path / "run") == []
    # The folder is untouched: no new build wrote its pid into it.
    assert lock.is_dir()
    assert ((lock / "pid").read_text() if (lock / "pid").exists() else None) == before


@pytest.mark.parametrize("version, encoder", [("4.4.2", "libx264"), ("8.0.1", "libx265"), ("N-113456-gabcdef", "none")])
def test_any_ffmpeg_will_do_so_no_version_or_encoder_is_asked_for(tmp_path, version, encoder):
    # Desk Crit decodes frames and reads durations. It never encodes video.
    assert run_doctor(tmp_path, ffmpeg=version, encoder=encoder) == ""


def test_the_hook_never_asks_ffmpeg_for_its_version_or_encoders():
    script = DOCTOR.read_text()
    for gone in ("-version", "-encoders", "libx264"):
        assert gone not in script, gone


def test_uv_off_the_path_says_so_not_that_it_is_missing(tmp_path):
    # Claude Code opened from the Dock has no Homebrew on its PATH, whatever is installed.
    assert problem_lines(run_doctor(tmp_path, uv=False)) == [
        "- uv isn't on the PATH Claude Code sees. Run: brew install uv. "
        "If it's already installed, start Claude Code from a terminal, which has your PATH."
    ]


def test_missing_ffmpeg_and_ffprobe_is_one_line(tmp_path):
    lines = problem_lines(run_doctor(tmp_path, ffmpeg=None, ffprobe=False))
    assert lines == ["- ffmpeg and ffprobe aren't installed. Run: brew install ffmpeg"]


def test_missing_ffprobe_alone(tmp_path):
    lines = problem_lines(run_doctor(tmp_path, ffprobe=False))
    assert len(lines) == 1 and "ffprobe" in lines[0] and "brew install ffmpeg" in lines[0]


def test_each_problem_gets_its_own_line(tmp_path):
    out = run_doctor(tmp_path, uv=False, ffprobe=False)
    assert len(problem_lines(out)) == 2
    assert out.splitlines()[0].startswith("Desk Crit can't run yet.")


def test_no_homebrew_adds_one_line_only_when_a_fix_is_needed(tmp_path):
    fixes = problem_lines(run_doctor(tmp_path / "a", uv=False, brew=False))
    assert len(fixes) == 2 and "https://brew.sh" in fixes[1]
    assert run_doctor(tmp_path / "b", brew=False) == ""


@pytest.mark.parametrize("kwargs, words", [
    ({"system": "Linux", "machine": "x86_64"}, "isn't a Mac"),
    ({"machine": "x86_64"}, "Intel Macs aren't supported"),
    ({"machine": "x86_64", "rosetta": True}, "Rosetta"),
])
def test_wrong_platform_is_the_only_line(tmp_path, kwargs, words):
    # uv and ffmpeg are fine here on purpose: the platform is the one problem said.
    lines = problem_lines(run_doctor(tmp_path, **kwargs))
    assert len(lines) == 1 and words in lines[0]


def test_hook_config_runs_the_shipped_script():
    config = json.loads((PLUGIN_DIR / "hooks" / "hooks.json").read_text())
    handlers = [h for group in config["hooks"]["SessionStart"] for h in group["hooks"]]
    assert len(handlers) == 1 and handlers[0]["type"] == "command"
    # The variable sits inside double quotes, so a path with spaces stays one word.
    assert handlers[0]["command"] == 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/doctor.sh"'
    assert DOCTOR.is_file()
