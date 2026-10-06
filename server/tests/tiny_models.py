"""A small stand-in for the speech model, and a fake network. No test reaches the network or loads a model."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from desk_crit.models import Model, ModelFile

WEIGHTS = bytes(range(256)) * 40  # 10,240 bytes
CONFIG = b'{"tiny": true}\n'


def tiny_model(
    folder: Path, title: str = "Tiny model", files: dict[str, bytes] | None = None,
    host: str = "example.test", license: str = "MIT",
) -> Model:
    """A model of a few small files, pinned by their real size and sha256, living in ``folder``."""
    files = files if files is not None else {"weights.bin": WEIGHTS}
    return Model(
        title=title, license=license, source_host=host,
        files=tuple(
            ModelFile(name, f"https://{host}/{name}", len(data), hashlib.sha256(data).hexdigest())
            for name, data in files.items()
        ),
        where=lambda: folder,
    )


def tiny_speech(folder: Path) -> Model:
    """A speech model of two small files in ``folder``."""
    return tiny_model(
        folder, "Tiny speech model", {"config.json": CONFIG, "speech.bin": WEIGHTS},
        host="hub.example.test", license="CC-BY-4.0",
    )


def content_of(model: Model) -> dict[str, bytes]:
    """What a server would serve for each file of ``tiny_speech``, by url."""
    known = {WEIGHTS, CONFIG}
    return {
        f.url: next(data for data in known if hashlib.sha256(data).hexdigest() == f.sha256) for f in model.files
    }


class FakeNet:
    """A fake opener: serves bytes by url, answers a Range as a server does, and can fail or corrupt on demand.

    ``honor_range`` False answers 200 with the whole file whatever was asked.
    ``cut_after`` closes each connection with an error after that many bytes.
    ``requests`` lists every ``(url, start)`` it was asked for.
    """

    def __init__(self, served: dict[str, bytes], *, honor_range: bool = True, cut_after: int | None = None) -> None:
        self.served = dict(served)
        self.honor_range = honor_range
        self.cut_after = cut_after
        self.requests: list[tuple[str, int]] = []

    def __call__(self, url: str, start: int) -> tuple[int, Iterator[bytes]]:
        self.requests.append((url, start))
        data = self.served[url]
        ranged = bool(start) and self.honor_range
        return (206 if ranged else 200), self._chunks(data[start:] if ranged else data)

    def _chunks(self, data: bytes, size: int = 1000) -> Iterator[bytes]:
        sent = 0
        for at in range(0, len(data), size):
            piece = data[at:at + size]
            if self.cut_after is not None and sent + len(piece) > self.cut_after:
                yield piece[: self.cut_after - sent]
                raise ConnectionResetError("connection reset by peer")
            sent += len(piece)
            yield piece

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.requests]
