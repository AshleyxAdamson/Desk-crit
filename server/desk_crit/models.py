"""The model Desk Crit needs, and the one place that downloads it.

Derived from Lumr Studio's models.py, slimmed to one model. The model is not
shipped with the plugin. Parakeet writes the transcript. ``transcribe`` says
when it is missing (``needs_models``) and the creator's yes starts
``download``. That function is the only code in the plugin that reaches the
network for a model. Everything else looks at files on disk.

A model is a few files, each pinned by exact size and sha256. ``download``
writes ``<file>.part``, checks size and sha256, then renames, so a file at its
real name is whole and was checked. A ``.part`` that a stopped download left is
picked up where it ended. Files land in the cache other tools share (Hugging
Face's), so an uninstall keeps them and a second tool finds them. Lumr Studio
pins the same revision, so a Mac that has one plugin's model has the other's.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import threading
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from desk_crit.errors import StudioError

PART_SUFFIX = ".part"
CHUNK_BYTES = 1 << 20
# A stalled connection gives up after this long without a byte, and the
# ``.part`` file is kept for the next try.
NETWORK_TIMEOUT_SECONDS = 30.0
USER_AGENT = "desk-crit-plugin"


@dataclass(frozen=True)
class ModelFile:
    """One file of a model. ``size`` and ``sha256`` are what the file must be."""

    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Model:
    """A model: where it comes from, its license, and the files that make it up.

    ``where`` answers the folder the files live in, when asked: the cache
    follows the environment (``HF_HOME``), and tests point it at a temp folder.
    """

    title: str
    license: str
    source_host: str
    files: tuple[ModelFile, ...]
    where: Callable[[], Path]

    @property
    def folder(self) -> Path:
        return self.where()

    def path_of(self, name: str) -> Path:
        return self.folder / name

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def present(self) -> bool:
        """Whether every file is there at its exact size. Never opens the file, so a check costs nothing."""
        return all(_whole(self.path_of(f.name), f) for f in self.files)

    def describe(self) -> dict[str, object]:
        """What the creator is told before anything downloads."""
        return {
            "name": self.title, "size_mb": round(self.size_bytes / 1_000_000),
            "source_host": self.source_host, "license": self.license, "folder": str(self.folder),
        }


def _whole(path: Path, file: ModelFile) -> bool:
    try:
        return path.stat().st_size == file.size
    except OSError:
        return False


# ── Where the model lives ─────────────────────────────────────────────────────


def hf_hub_cache() -> Path:
    """Hugging Face's model cache, resolved the way ``huggingface_hub`` does, from the environment as it is now.

    ``huggingface_hub`` reads the environment once, at import. This reads it
    when asked, so a test can move the cache. A test holds the two together.
    """
    def expand(value: str) -> Path:
        return Path(os.path.expandvars(os.path.expanduser(value)))

    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(name):
            return expand(os.environ[name])
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return expand(os.environ.get("HF_HOME") or os.path.join(cache, "huggingface")) / "hub"


# ── The model, pinned ─────────────────────────────────────────────────────────

SPEECH_REPO = "mlx-community/parakeet-tdt-0.6b-v2"
# The full commit of the snapshot the plugin was tested on. Pinned so the file
# a stranger gets is the file we ran, whatever the repo's main branch does later.
SPEECH_REVISION = "8ae155301e23d820d82aa60d24817c900e69e487"
_SPEECH_URL = f"https://huggingface.co/{SPEECH_REPO}/resolve/{SPEECH_REVISION}"


def _speech_folder() -> Path:
    return hf_hub_cache() / f"models--{SPEECH_REPO.replace('/', '--')}" / "snapshots" / SPEECH_REVISION


SPEECH = Model(
    title="NVIDIA Parakeet TDT 0.6B v2 (speech recognition)",
    license="CC-BY-4.0",
    source_host="huggingface.co",
    files=(
        ModelFile(
            "config.json", f"{_SPEECH_URL}/config.json", 36_176,
            "9bd323e60afe2615c983a5d9fc3a2c0470df2a03edf90c0f861bd59509d07264",
        ),
        ModelFile(
            "model.safetensors", f"{_SPEECH_URL}/model.safetensors", 2_471_559_904,
            "b958c37a6baa6874a279108755c8f2818e27bf647d72d54800a234a421341dfe",
        ),
    ),
    where=_speech_folder,
)


# ── Downloading ───────────────────────────────────────────────────────────────

# Opens ``url`` from byte ``start`` and answers ``(status, chunks)``: 206 when
# the bytes go on from ``start``, 200 when the server sends the file from its
# first byte. Tests pass a fake, so no test reaches the network.
Opener = Callable[[str, int], tuple[int, Iterable[bytes]]]


def open_url(url: str, start: int) -> tuple[int, Iterator[bytes]]:
    """The production opener. Redirects are followed, and the range asked for goes along."""
    headers = {"User-Agent": USER_AGENT}
    if start:
        headers["Range"] = f"bytes={start}-"
    response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=NETWORK_TIMEOUT_SECONDS)
    return response.status, _read_all(response)


def _read_all(response: http.client.HTTPResponse) -> Iterator[bytes]:
    with response:
        while chunk := response.read(CHUNK_BYTES):
            yield chunk


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _matches(path: Path, file: ModelFile) -> bool:
    return _whole(path, file) and sha256_of(path) == file.sha256


class _Progress:
    """How much of the downloads' bytes are on disk, as a fraction, reported as it moves."""

    def __init__(self, total: int, report: Callable[[float], None] | None) -> None:
        self.total = total
        self.done = 0
        self._report = report

    def add(self, count: int) -> None:
        self.done = max(0, self.done + count)
        if self._report is not None:
            self._report(min(1.0, self.done / self.total))


# One download at a time on this machine's caches: two jobs writing the same
# ``.part`` file would corrupt it.
_ONE_AT_A_TIME = threading.Lock()


def download(
    models: Iterable[Model], opener: Opener = open_url, on_progress: Callable[[float], None] | None = None,
) -> list[Model]:
    """Download what is missing of ``models``, and answer the models it fetched.

    Only the files that are not whole are fetched. Progress is the share of
    all their bytes on disk, so it moves the same way through a resumed
    download. Raises StudioError, with what to do next, when there isn't the
    disk room, when a download stops (what arrived is kept and the next call
    resumes it), or when a file fails its checksum (nothing is installed).
    """
    with _ONE_AT_A_TIME:
        todo = [m for m in models if not m.present()]
        if not todo:
            return []
        _check_room(todo)
        progress = _Progress(sum(m.size_bytes for m in todo), on_progress)
        for model in todo:
            for file in model.files:
                target = model.path_of(file.name)
                if _whole(target, file):
                    progress.add(file.size)  # the other file of a model that was half there
                else:
                    _fetch(model, file, target, opener, progress)
        return todo


def _part_of(target: Path) -> Path:
    return target.with_name(target.name + PART_SUFFIX)


def _bytes_left(model: Model) -> int:
    """The bytes still to fetch for ``model``: what its files need, less the part of each a stopped download kept."""
    left = 0
    for file in model.files:
        target = model.path_of(file.name)
        if not _whole(target, file):
            part = _part_of(target)
            kept = part.stat().st_size if part.exists() else 0
            left += file.size - (kept if kept <= file.size else 0)
    return left


def _check_room(todo: list[Model]) -> None:
    """Raises StudioError when the disks that will hold ``todo`` lack the room for what is left to fetch."""
    disks: dict[int, tuple[Path, int]] = {}  # device -> (a folder on it, bytes wanted there)
    for model in todo:
        model.folder.mkdir(parents=True, exist_ok=True)
        disk = model.folder.stat().st_dev
        folder, wanted = disks.get(disk, (model.folder, 0))
        disks[disk] = (folder, wanted + _bytes_left(model))
    for folder, wanted in disks.values():
        free = shutil.disk_usage(folder).free
        if free < wanted:
            raise StudioError(
                f"The model needs {wanted / 1e9:.1f} GB of free disk in {folder} and there is {free / 1e9:.1f} GB. "
                "Free some space, then run transcribe with download_models=true again."
            )


def _fetch(model: Model, file: ModelFile, target: Path, opener: Opener, progress: _Progress) -> None:
    """Get ``file`` into ``target``: to ``.part``, checked, then renamed."""
    part = _part_of(target)
    have = part.stat().st_size if part.exists() else 0
    if have > file.size or (have == file.size and not _matches(part, file)):
        part.unlink()  # not this file, or a whole one that fails its check: start over
        have = 0
    progress.add(have)
    if have < file.size:
        have = _stream(model, file, part, have, opener, progress)
        if have < file.size:
            raise StudioError(_stopped(model, "the connection closed early"))
    if not _matches(part, file):
        part.unlink()
        raise StudioError(
            f"The {model.title} file from {model.source_host} didn't match its checksum, so it was not "
            "installed and the partial file was deleted. Nothing else changed. Run transcribe with "
            "download_models=true to try again. If it fails again, the file at the source may have "
            "changed, and the plugin needs an update."
        )
    os.replace(part, target)


def _stream(model: Model, file: ModelFile, part: Path, start: int, opener: Opener, progress: _Progress) -> int:
    """Write the rest of ``file`` to ``part``, from byte ``start`` when the server allows it. Answers the bytes in ``part`` now."""
    try:
        status, chunks = opener(file.url, start)
        if status not in (200, 206):
            raise OSError(f"the server answered {status}")
        if start and status != 206:
            progress.add(-start)  # it sent the whole file again
            start = 0
        with part.open("ab" if start else "wb") as out:
            for chunk in chunks:
                out.write(chunk)
                start += len(chunk)
                progress.add(len(chunk))
                if start > file.size:
                    break  # more than the file holds: the check will refuse it
    except (OSError, http.client.HTTPException) as why:
        raise StudioError(_stopped(model, str(why)[:200])) from why
    return start


def _stopped(model: Model, why: str) -> str:
    return (
        f"Downloading {model.title} from {model.source_host} stopped: {why}. What arrived is kept. "
        "Run transcribe with download_models=true again and it picks up where it left off."
    )
