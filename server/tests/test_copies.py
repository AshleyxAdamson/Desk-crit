"""The shared files are copies. These tests hold the copy honest.

``tools/sync-from-lumr.sh`` is the only writer of the copied files. Its
``--check`` runs here when the Lumr Studio folder sits beside this repo
(or ``LUMR_STUDIO_DIR`` names it); a copy of the plugin alone has no sources,
so that test skips there. The rest run anywhere.
"""

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
PACKAGE = SERVER_DIR / "desk_crit"
REPO = SERVER_DIR.parent
SYNC = REPO / "tools" / "sync-from-lumr.sh"
LUMR = Path(os.environ.get("LUMR_STUDIO_DIR") or REPO.parent / "lumr-studio")
HEADER = "# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/"
HEADER_END = ". Do not edit: change it in Lumr Studio and run the script."

# The files the script writes, relative to the package. Every other file is hand-written.
COPIED = [
    "errors.py", "jobs.py", "mcp_results.py", "transcript.py", "frames.py",
    "speech/__init__.py", "speech/__main__.py", "speech/transcribe.py", "speech/ui.py",
]
# A copied file may import a module from inside a function nothing here calls,
# or one the plugin doesn't ship. This names each one; a new one fails the test.
# These are the speech model packages speech/transcribe.py chooses between: the
# Parakeet one is shipped and the rest are never reached (the settings in
# speech/__main__.py name parakeet-mlx).
DEAD_IMPORTS = {
    ("speech/transcribe.py", "parakeet_mlx"),
    ("speech/transcribe.py", "numpy"),
    ("speech/transcribe.py", "librosa"),
    ("speech/transcribe.py", "mlx_whisper"),
    ("speech/transcribe.py", "faster_whisper"),
    ("speech/transcribe.py", "nemo"),
    ("speech/transcribe.py", "torch"),
}

needs_lumr = pytest.mark.skipif(not (LUMR / "server" / "lumr_studio").is_dir(), reason="no Lumr Studio folder beside this repo")


def copied_files() -> list[Path]:
    return [PACKAGE / name for name in COPIED]


def test_the_copies_are_the_files_the_script_writes():
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*.py")}
    script = SYNC.read_text()
    listed = next(line for line in script.splitlines() if line.startswith("FILES=")).split('"')[1].split()
    assert listed == COPIED
    assert set(COPIED) <= on_disk
    # Everything else in the package is hand-written, and says so or says where it comes from.
    hand_written = on_disk - set(COPIED)
    assert hand_written == {
        "__init__.py", "ffmpeg.py", "paths.py", "project.py", "models.py", "transcription.py",
        "word_finder.py", "tools.py", "server.py",
    }


@pytest.mark.parametrize("name", COPIED)
def test_every_copy_says_it_is_copied_and_from_where(name):
    first = (PACKAGE / name).read_text().splitlines()[0]
    assert first == f"{HEADER}{name}{HEADER_END}", first
    if (LUMR / "server" / "lumr_studio").is_dir():
        assert (LUMR / "server" / "lumr_studio" / name).is_file(), f"{name} names a source that isn't there"


@pytest.mark.parametrize("name", [
    "__init__.py", "ffmpeg.py", "paths.py", "project.py", "models.py", "transcription.py",
    "word_finder.py", "tools.py", "server.py",
])
def test_every_hand_written_module_says_what_it_comes_from_or_that_it_is_slim(name):
    text = (PACKAGE / name).read_text()
    assert not text.startswith(HEADER)
    if name != "__init__.py":
        assert "Lumr Studio" in text.split('"""')[1], f"{name}'s docstring should say it is derived from Lumr Studio"


@needs_lumr
def test_no_copy_has_drifted_from_its_source():
    done = subprocess.run([str(SYNC), "--check"], capture_output=True, text=True, env={**os.environ, "LUMR_STUDIO_DIR": str(LUMR)})
    assert done.returncode == 0, done.stdout + done.stderr


@needs_lumr
def test_the_check_notices_a_changed_copy_and_a_missing_one(tmp_path):
    # Run the script against a throwaway copy of this repo's package so the real files stay as they are.
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / SYNC.name).write_text(SYNC.read_text())
    (repo / "tools" / SYNC.name).chmod(0o755)
    env = {**os.environ, "LUMR_STUDIO_DIR": str(LUMR)}
    script = str(repo / "tools" / SYNC.name)
    assert subprocess.run([script, "--check"], capture_output=True, text=True, env=env).returncode == 1  # nothing written yet
    assert subprocess.run([script], capture_output=True, text=True, env=env).returncode == 0
    assert subprocess.run([script, "--check"], capture_output=True, text=True, env=env).returncode == 0
    edited = repo / "server" / "desk_crit" / "jobs.py"
    edited.write_text(edited.read_text() + "# a hand edit\n")
    done = subprocess.run([script, "--check"], capture_output=True, text=True, env=env)
    assert done.returncode == 1 and "differs: server/desk_crit/jobs.py" in done.stdout
    (repo / "server" / "desk_crit" / "speech" / "ui.py").unlink()
    done = subprocess.run([script, "--check"], capture_output=True, text=True, env=env)
    assert "missing: server/desk_crit/speech/ui.py" in done.stdout


def test_the_script_says_so_when_there_is_no_lumr_studio_folder(tmp_path):
    done = subprocess.run([str(SYNC), "--check"], capture_output=True, text=True,
                          env={**os.environ, "LUMR_STUDIO_DIR": str(tmp_path / "nowhere")})
    assert done.returncode == 2 and "LUMR_STUDIO_DIR" in done.stderr


def test_the_rewrite_table_was_applied():
    for path in copied_files():
        body = "\n".join(path.read_text().splitlines()[1:])
        assert "lumr_studio" not in body, path.name
    frames = (PACKAGE / "frames.py").read_text()
    assert "from desk_crit.ffmpeg import clock" in frames
    assert "from desk_crit.ffmpeg import MAX_FILE_BYTES, MAX_LONG_EDGE, _run" in frames
    assert "Job ids come from transcribe;" in (PACKAGE / "jobs.py").read_text()
    assert "python -m desk_crit.speech" in (PACKAGE / "speech" / "__main__.py").read_text()


def _is_module(name: str) -> bool:
    """Whether ``desk_crit.x.y`` is a file the package ships."""
    base = PACKAGE.joinpath(*name.split(".")[1:])
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _desk_crit_modules(path: Path) -> set[str]:
    """Every ``desk_crit`` module ``path`` imports, anywhere in the file: at the top or inside a function.

    ``from desk_crit import models`` names the module ``desk_crit.models``.
    ``from desk_crit.models import SPEECH`` names ``desk_crit.models``.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == "desk_crit"}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level and node.module.split(".")[0] == "desk_crit":
            if node.module == "desk_crit":
                found |= {f"desk_crit.{a.name}" for a in node.names}
            else:
                found.add(node.module)
    return found


def test_no_copy_imports_a_desk_crit_module_that_is_not_shipped():
    lacking = sorted(
        (path.relative_to(PACKAGE).as_posix(), module)
        for path in copied_files()
        for module in _desk_crit_modules(path)
        if not _is_module(module)
    )
    assert lacking == []


def test_the_copies_reach_the_hand_written_modules_the_rewrite_points_them_at():
    reached = {module for path in copied_files() for module in _desk_crit_modules(path)}
    assert {"desk_crit.ffmpeg", "desk_crit.project", "desk_crit.models"} <= reached
    assert not {m for m in reached if m.startswith(("desk_crit.edit", "desk_crit.look", "desk_crit.engine"))}


def _third_party_imports_inside_functions(path: Path) -> set[str]:
    """The top-level package names ``path`` imports from inside a function, other than the standard library and desk_crit."""
    found: set[str] = set()
    for function in ast.walk(ast.parse(path.read_text())):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                found |= {n.split(".")[0] for n in names}
    return {n for n in found if n not in sys.stdlib_module_names and n != "desk_crit"}


def test_a_copy_reaches_only_the_listed_dead_paths_from_inside_its_functions():
    found = {
        (path.relative_to(PACKAGE).as_posix(), module)
        for path in copied_files()
        for module in _third_party_imports_inside_functions(path)
    }
    assert found == DEAD_IMPORTS


def test_the_dead_paths_name_only_packages_the_environment_lacks_or_ships_for_the_speech_model():
    missing = {name for name in {m for _, m in DEAD_IMPORTS} if importlib.util.find_spec(name) is None}
    assert missing == {"mlx_whisper", "faster_whisper", "nemo", "torch"}


def test_every_module_loads_without_torch_and_without_the_other_packages_nobody_calls():
    modules = ["desk_crit.server"] + [
        "desk_crit." + name.removesuffix(".py").replace("/", ".").removesuffix(".__init__") for name in COPIED
    ]
    code = (
        "import importlib, sys\n"
        "for blocked in ('torch', 'torchaudio', 'librosa', 'mlx_whisper', 'faster_whisper', 'nemo'):\n"
        "    sys.modules[blocked] = None\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        "heavy = {m.split('.')[0] for m in sys.modules if sys.modules[m] is not None} & {'torch', 'torchaudio', 'parakeet_mlx'}\n"
        "assert not heavy, f'loaded at import: {heavy}'\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_torch_is_not_in_the_environment():
    assert importlib.util.find_spec("torch") is None and importlib.util.find_spec("torchaudio") is None
    lock = (SERVER_DIR / "uv.lock").read_text()
    assert 'name = "torch"' not in lock and 'name = "torchaudio"' not in lock
    pyproject = (SERVER_DIR / "pyproject.toml").read_text()
    dependencies = pyproject.split("dependencies = [")[1].split("]")[0]
    assert "torch" not in dependencies
