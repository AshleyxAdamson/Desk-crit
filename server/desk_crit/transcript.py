# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/transcript.py. Do not edit: change it in Lumr Studio and run the script.
"""Load, validate, and pack the speech model's ``<video>.words.json`` transcript.

The file is a JSON list. Each entry needs ``word`` (str), ``start`` and ``end``
(seconds). ``type == "event"`` marks a vocalization; a missing ``type`` means a
spoken word. Unknown extra keys (``energy_rms``, ``pitch_mean``…) are allowed.

Packing turns the word list into short text for the model: one line per
sentence or two, broken at pauses and sentence ends, so a long transcript reads
in a small budget and every line boundary is a sensible cut point.

The timing check spots transcripts whose word times were estimated (spread
evenly across a sentence) instead of measured.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desk_crit.errors import StudioError

EVENT_TYPE = "event"

# A gap at least this long between words ends a phrase and prints a pause line.
PHRASE_PAUSE_SECONDS = 0.5
# Whole sentences are joined into lines of roughly this many seconds.
LINE_MIN_SECONDS = 6.0
LINE_MAX_SECONDS = 12.0
# Only a sentence longer than this is broken, at a comma or the longest gap.
MAX_SENTENCE_SECONDS = 20.0
SENTENCE_END = (".", "?", "!")
CLAUSE_END = (",", ";", ":")
# The packed text the tool returns stays under this many characters.
PACK_BUDGET_CHARS = 8000


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_words(data: Any) -> list[dict[str, Any]]:
    """Check a parsed transcript and return its entries.

    Raises StudioError naming the index of the first bad entry.
    """
    if not isinstance(data, list):
        raise StudioError(
            f"Transcript must be a JSON list of word entries, got {type(data).__name__}. "
            "Re-run transcribe with force=true to rebuild it."
        )
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise StudioError(f"Transcript entry {i} is {type(entry).__name__}, expected an object.")
        for key in ("word", "start", "end"):
            if key not in entry:
                raise StudioError(f"Transcript entry {i} is missing required key {key!r}.")
        if not isinstance(entry["word"], str):
            raise StudioError(f"Transcript entry {i}: 'word' must be a string.")
        for key in ("start", "end"):
            if not _is_number(entry[key]):
                raise StudioError(
                    f"Transcript entry {i}: {key!r} must be a finite number of seconds, "
                    f"got {entry[key]!r}."
                )
        if entry["end"] < entry["start"]:
            raise StudioError(
                f"Transcript entry {i}: end {entry['end']} is before start {entry['start']}."
            )
        if "type" in entry and not isinstance(entry["type"], str):
            raise StudioError(f"Transcript entry {i}: 'type' must be a string when present.")
    return data


def load_transcript(words_path: Path) -> list[dict[str, Any]]:
    """Read and validate a words.json file. Raises StudioError when absent or invalid."""
    if not words_path.exists():
        raise StudioError(
            f"No transcript at {words_path}. Run the transcribe tool on this video first."
        )
    try:
        data = json.loads(words_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StudioError(
            f"Transcript {words_path} is not valid JSON ({exc.msg} at line {exc.lineno}). "
            "Re-run transcribe with force=true to rebuild it."
        ) from None
    return sorted(validate_words(data), key=lambda w: float(w["start"]))


# Punctuation a transcript word carries around it.
AROUND_A_WORD = ".,!?;:\"'()[]\u201c\u201d\u2018\u2019"


def plain_text(text: str) -> str:
    """A word as said: lower case, without the punctuation around it. "Like," reads ``like``."""
    return text.strip().lower().strip(AROUND_A_WORD)


def is_event(entry: dict[str, Any]) -> bool:
    return entry.get("type") == EVENT_TYPE


def spoken_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries that are speech, not vocalization events."""
    return [w for w in words if not is_event(w)]


# ── Timing quality ────────────────────────────────────────────────────────────

# Two neighbouring words whose durations match to within this are "identical".
IDENTICAL_DURATION_TOLERANCE = 0.002
# A word starting more than this before its predecessor ends is an overlap.
OVERLAP_TOLERANCE = 0.05
# Above this share of identical neighbouring durations the timings are estimates.
# Measured speech sits far below it (the ClipForge fixture: 0.038). Timings
# spread evenly across each sentence sit far above it: every pair inside a
# sentence matches, so the share is about (n - 1) / n for n-word sentences.
APPROXIMATE_SHARE_THRESHOLD = 0.5
APPROXIMATE_WARNING = (
    "The word timings in this transcript are estimates, not measurements, so cuts "
    "placed from them will be imprecise. Re-run transcribe with force=true to get "
    "measured timings."
)


def timing_quality(words: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the spoken words' timings look measured or synthesized.

    Returns ``{quality, identical_duration_share, overlapping_pairs}`` where
    ``quality`` is ``measured`` or ``approximate``.
    """
    spoken = spoken_words(words)
    pairs = list(zip(spoken, spoken[1:]))
    identical = sum(
        abs((float(a["end"]) - float(a["start"])) - (float(b["end"]) - float(b["start"])))
        <= IDENTICAL_DURATION_TOLERANCE
        for a, b in pairs
    )
    overlapping = sum(float(b["start"]) < float(a["end"]) - OVERLAP_TOLERANCE for a, b in pairs)
    share = identical / len(pairs) if pairs else 0.0
    return {
        "quality": "approximate" if share > APPROXIMATE_SHARE_THRESHOLD else "measured",
        "identical_duration_share": round(share, 3),
        "overlapping_pairs": overlapping,
    }


def timing_report(words: list[dict[str, Any]]) -> dict[str, Any]:
    """``{timing_quality}``, plus ``warning`` when the timings are estimates."""
    quality = timing_quality(words)
    report: dict[str, Any] = {"timing_quality": quality}
    if quality["quality"] == "approximate":
        report["warning"] = APPROXIMATE_WARNING
    return report


# ── Packing ───────────────────────────────────────────────────────────────────


@dataclass
class Phrase:
    """One packed line: a run of words, or a single event."""

    start: float
    end: float
    text: str
    cut: bool = False


def _inside_any(t: float, ranges: list[tuple[float, float]]) -> bool:
    return any(a <= t < b for a, b in ranges)


def word_is_cut(entry: dict[str, Any], removed: list[tuple[float, float]]) -> bool:
    """A word counts as removed when its midpoint falls inside a removed range."""
    mid = (float(entry["start"]) + float(entry["end"])) / 2
    return _inside_any(mid, removed)


Run = list[dict[str, Any]]


def _is_sentence_end(entry: dict[str, Any]) -> bool:
    return entry["word"].strip().rstrip("\"')\u201d\u2019").endswith(SENTENCE_END)


def _is_clause_end(entry: dict[str, Any]) -> bool:
    return entry["word"].strip().rstrip("\"')\u201d\u2019").endswith(CLAUSE_END)


def _span(run: Run) -> float:
    return float(run[-1]["end"]) - float(run[0]["start"])


def _runs(words: list[dict[str, Any]], removed: list[tuple[float, float]], pause: float) -> list[tuple[Run, bool]]:
    """Split entries at the hard boundaries: a pause, an event, a flip in cut state.

    Returns ``(entries, cut)`` pairs; an event is a run of its own.
    """
    runs: list[tuple[Run, bool]] = []
    current: Run = []
    current_cut = False
    for entry in words:
        cut = word_is_cut(entry, removed)
        if is_event(entry):
            if current:
                runs.append((current, current_cut))
            runs.append(([entry], cut))
            current = []
            continue
        if current and (float(entry["start"]) - float(current[-1]["end"]) >= pause or cut != current_cut):
            runs.append((current, current_cut))
            current = []
        if not current:
            current_cut = cut
        current.append(entry)
    if current:
        runs.append((current, current_cut))
    return runs


def _sentences(run: Run) -> list[Run]:
    """Split a run after every word that ends a sentence."""
    out: list[Run] = [[]]
    for entry in run:
        out[-1].append(entry)
        if _is_sentence_end(entry):
            out.append([])
    return [s for s in out if s]


def _split_long(sentence: Run, max_seconds: float) -> list[Run]:
    """Break a sentence longer than ``max_seconds`` into pieces that fit.

    Breaks after the comma nearest the middle, else at the longest gap between
    words, and repeats on each half until every piece fits.
    """
    if _span(sentence) <= max_seconds or len(sentence) < 2:
        return [sentence]
    mid = (float(sentence[0]["start"]) + float(sentence[-1]["end"])) / 2
    after = range(len(sentence) - 1)  # break after word i
    commas = [i for i in after if _is_clause_end(sentence[i])]
    if commas:
        i = min(commas, key=lambda k: abs(float(sentence[k]["end"]) - mid))
    else:
        i = max(after, key=lambda k: (
            float(sentence[k + 1]["start"]) - float(sentence[k]["end"]),
            -abs(float(sentence[k]["end"]) - mid),
        ))
    return _split_long(sentence[:i + 1], max_seconds) + _split_long(sentence[i + 1:], max_seconds)


def _pack_line_groups(pieces: list[Run], target_min: float, target_max: float) -> list[Run]:
    """Join whole sentences into lines of roughly ``target_min`` to ``target_max`` seconds.

    A line closes once it reaches ``target_min``, or when the next sentence
    would push it past ``target_max``.
    """
    lines: list[Run] = []
    for piece in pieces:
        if lines and _span(lines[-1]) < target_min and _span(lines[-1] + piece) <= target_max:
            lines[-1] = lines[-1] + piece
        else:
            lines.append(list(piece))
    return lines


# Sound labels (see ``sounds.py``) are matched to events by start time, to
# within this many seconds (transcript times carry 3 decimals).
LABEL_MATCH_SECONDS = 0.0015


def label_text(label: dict[str, Any]) -> str:
    """How a sound label reads in the packed text: ``(laugh 3.5s)``, ``(laugh? 1.6s)`` or ``(sound 0.2s)``."""
    seconds = float(label["seconds"])
    if label.get("kind") == "laugh":
        mark = "laugh" if label.get("confidence") == "likely" else "laugh?"
        return f"({mark} {seconds:.1f}s)"
    return f"(sound {seconds:.1f}s)"


def _entry_text(entry: dict[str, Any], labels: list[dict[str, Any]] | None) -> str:
    """The entry's word, or its label's text for an event that has a label."""
    if labels and is_event(entry):
        start = float(entry["start"])
        for label in labels:
            if abs(float(label["start"]) - start) <= LABEL_MATCH_SECONDS:
                return label_text(label)
    return entry["word"].strip()


def build_phrases(
    words: list[dict[str, Any]],
    removed: list[tuple[float, float]] | None = None,
    pause: float = PHRASE_PAUSE_SECONDS,
    labels: list[dict[str, Any]] | None = None,
) -> list[Phrase]:
    """Group sorted entries into lines that end where the speaker does.

    Hard breaks: a gap of at least ``pause`` seconds, an event (its own line),
    and a flip in cut state (so a line is wholly kept or wholly cut). Between
    those, whole sentences are joined into lines of about 6 to 12 seconds. A
    sentence is broken only when it runs past ``MAX_SENTENCE_SECONDS``.

    ``labels`` (from ``sounds.classify_events``) replaces an event's
    ``[vocalization]`` with what it likely is. Events without a matching label,
    and every event when ``labels`` is None, keep their word.
    """
    phrases: list[Phrase] = []
    for run, cut in _runs(words, removed or [], pause):
        pieces = [p for s in _sentences(run) for p in _split_long(s, MAX_SENTENCE_SECONDS)]
        for line in _pack_line_groups(pieces, LINE_MIN_SECONDS, LINE_MAX_SECONDS):
            phrases.append(Phrase(
                start=float(line[0]["start"]),
                end=float(line[-1]["end"]),
                text=" ".join(_entry_text(w, labels) for w in line),
                cut=cut,
            ))
    return phrases


def _phrase_lines(phrases: list[Phrase], pause: float) -> list[tuple[float, str]]:
    """``(phrase start, text)`` for every output line, pause lines included."""
    lines: list[tuple[float, str]] = []
    prev_end: float | None = None
    for p in phrases:
        if prev_end is not None and p.start - prev_end >= pause:
            lines.append((p.start, f"  (pause {p.start - prev_end:.1f})"))
        prefix = "CUT " if p.cut else ""
        lines.append((p.start, f"{prefix}[{p.start:.2f}-{p.end:.2f}] {p.text}"))
        prev_end = max(prev_end or 0.0, p.end)
    return lines


def pack_transcript(
    words: list[dict[str, Any]],
    *,
    start: float | None = None,
    end: float | None = None,
    removed: list[tuple[float, float]] | None = None,
    budget_chars: int = PACK_BUDGET_CHARS,
    pause: float = PHRASE_PAUSE_SECONDS,
    labels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pack the entries whose start falls in ``[start, end)`` into text lines.

    ``removed`` (source-time ranges) marks cut phrases with ``CUT``; pass None to
    leave cuts unmarked. ``labels`` (sound labels) prints each labelled event as
    ``(laugh 3.5s)``, ``(laugh? 1.6s)`` or ``(sound 0.2s)``; pass None to print
    ``[vocalization]``. When the lines would exceed ``budget_chars``, only the
    first part is packed.

    The text always ends with one of two lines:

    * ``NEXT <time>`` when more transcript remains. Passing ``<time>`` as the
      next call's ``start`` continues exactly where this window stopped, so
      windows are contiguous and never overlap.
    * ``END`` when the window reached the last entry.

    Returns ``{text, phrase_count, next_start}`` (``next_start`` None at END).
    """
    if start is not None and end is not None and end <= start:
        raise StudioError(f"end ({end}) must be after start ({start}). Pass a later end.")
    lo = start if start is not None else float("-inf")
    hi = end if end is not None else float("inf")
    window = [w for w in words if lo <= float(w["start"]) < hi]
    phrases = build_phrases(window, removed, pause=pause, labels=labels)
    lines = _phrase_lines(phrases, pause)

    kept: list[str] = []
    used = 0
    next_start: float | None = None
    for line_start, line in lines:
        cost = len(line) + 1
        if kept and used + cost > budget_chars:
            next_start = line_start
            break
        kept.append(line)
        used += cost

    # A trailing pause line before the cut-off point says nothing on its own.
    if next_start is not None and kept and kept[-1].startswith("  (pause"):
        kept.pop()

    if next_start is None and end is not None and any(float(w["start"]) >= end for w in words):
        next_start = end

    # repr() round-trips the float exactly, so NEXT's value used as ``start``
    # re-includes the first phrase left out, and nothing before it.
    kept.append(f"NEXT {next_start!r}" if next_start is not None else "END")
    return {
        "text": "\n".join(kept),
        "phrase_count": sum(1 for p in kept if p.startswith(("[", "CUT ["))),
        "next_start": next_start,
    }
