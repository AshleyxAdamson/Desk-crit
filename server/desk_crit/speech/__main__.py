# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/speech/__main__.py. Do not edit: change it in Lumr Studio and run the script.
"""``python -m desk_crit.speech <video>``: write ``<video>.words.json`` beside the video.

The server runs this as a subprocess (see ``transcription.py``), so the speech
model's memory goes back to the machine when it exits and a crash in the speech
stack can't take the server down.
"""

from __future__ import annotations

import sys
from pathlib import Path

from desk_crit import models
from desk_crit.speech.transcribe import transcribe_audio

NO_MODEL = (
    "The speech model isn't on this machine. Run transcribe again, and when it asks to download "
    "the models, say yes."
)


def parakeet_settings() -> dict[str, str]:
    """The settings ``hammy setup`` would have written, fixed: this entry has no config file.

    The model is the local folder the plugin downloaded (``models.SPEECH``),
    pinned to one revision, so nothing is looked up online.
    """
    return {
        "platform": "mac_silicon",
        "transcription_package": "parakeet-mlx",
        "transcription_model": str(models.SPEECH.folder),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m desk_crit.speech <video>", file=sys.stderr)
        return 2
    if not models.SPEECH.present():
        print(NO_MODEL, file=sys.stderr)
        return 3
    transcribe_audio(Path(argv[0]), parakeet_settings(), word_timestamps=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
