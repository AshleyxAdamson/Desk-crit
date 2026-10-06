"""Find every place a word or a short phrase is said, so Claude can find what the creator pointed at.

Derived from Lumr Studio's word_finder.py, slimmed: no quiet column, no clean
check, no OUT or cut notes. Desk Crit cuts nothing. A creator who points says
"this", "here" or "that" as the cursor lands on the thing. ``find_words`` gives
Claude each place with its time and the words around it, so it can take a
screenshot at that moment.

The answer is packed text, one place a line, the way ``read_transcript``
packs the transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desk_crit.errors import StudioError
from desk_crit.ffmpeg import clock
from desk_crit.transcript import is_event, plain_text

# Claude asks for a few words at a time, so one answer stays readable.
MAX_PHRASES = 5
# A pointing word or two: "this", "right here", "this button".
MAX_PHRASE_WORDS = 4
# Words shown either side of each place. Six is enough to tell which thing was meant.
CONTEXT_WORDS = 6
# The packed text stays under this many characters.
PACK_BUDGET_CHARS = 24000
# A place's id starts with this, then its start and end seconds.
PLACE_ID_PREFIX = "w"


@dataclass(frozen=True)
class Place:
    """One place a phrase is said."""

    phrase: str
    id: str
    start: float
    end: float
    before: str
    said: str
    after: str


def place_id(start: float, end: float) -> str:
    """The id of one place a word is said: ``w312.395-312.541``, from the word's own times."""
    return f"{PLACE_ID_PREFIX}{start:.3f}-{end:.3f}"


def check_phrases(phrases: Any) -> list[list[str]]:
    """The phrases as lists of plain words. Raises StudioError for a list Claude should fix."""
    example = '["this"] or ["right here", "that"]'
    if not isinstance(phrases, list) or not phrases or not all(isinstance(p, str) for p in phrases):
        raise StudioError(f"words must be a list of one to {MAX_PHRASES} words or short phrases, like {example}.")
    if len(phrases) > MAX_PHRASES:
        raise StudioError(f"words holds {len(phrases)} entries. Ask for at most {MAX_PHRASES} at a time, like {example}.")
    out = []
    for phrase in phrases:
        parts = [plain_text(t) for t in phrase.split()]
        if not parts or not all(parts):
            raise StudioError(f"{phrase!r} holds no word to look for. Pass words or short phrases, like {example}.")
        if len(parts) > MAX_PHRASE_WORDS:
            raise StudioError(f"{phrase!r} is {len(parts)} words long. A phrase is at most {MAX_PHRASE_WORDS} words.")
        if parts not in out:
            out.append(parts)
    return out


def _mid(entry: dict[str, Any]) -> float:
    return (float(entry["start"]) + float(entry["end"])) / 2


def _quote(tokens: list[dict[str, Any]]) -> str:
    return " ".join("(sound)" if is_event(w) else str(w["word"]).strip() for w in tokens)


def find_places(words: list[dict[str, Any]], phrases: list[list[str]]) -> list[Place]:
    """Every place each phrase is said, in time order."""
    tokens = sorted(words, key=_mid)
    plain = [None if is_event(w) else plain_text(str(w["word"])) for w in tokens]
    places = []
    for parts in phrases:
        n = len(parts)
        for i in range(len(plain) - n + 1):
            if plain[i:i + n] != parts:
                continue
            chosen = tokens[i:i + n]
            start = round(min(float(w["start"]) for w in chosen), 3)
            end = round(max(float(w["end"]) for w in chosen), 3)
            places.append(Place(
                phrase=" ".join(parts), id=place_id(start, end), start=start, end=end,
                before=_quote(tokens[max(0, i - CONTEXT_WORDS):i]), said=_quote(chosen),
                after=_quote(tokens[i + n:i + n + CONTEXT_WORDS]),
            ))
    return sorted(places, key=lambda p: (p.start, p.end))


def _line(place: Place) -> str:
    return f"{place.id} {clock(place.start)} {place.before} [{place.said}] {place.after}".rstrip()


HOW_TO_READ = (
    "Each line: id, clock, the words before, [the word], the words after. "
    "The id holds the word's start and end in seconds. A creator who points says the word "
    "as the cursor lands, so take a screenshot at its start time with frames."
)


def pack_places(places: list[Place], phrases: list[list[str]], *, start: float | None = None,
                budget_chars: int = PACK_BUDGET_CHARS) -> dict[str, Any]:
    """The places as packed text, with a count for each phrase.

    Returns ``{text, words: [{word, said}], listed, next_start}``. The counts
    are for the whole recording. The lines start at ``start`` and stop when the
    budget is used up; the text then ends with ``NEXT <time>``, the ``start``
    to pass to go on, else with ``END``.
    """
    counts = []
    for parts in phrases:
        phrase = " ".join(parts)
        counts.append({"word": phrase, "said": sum(p.phrase == phrase for p in places)})
    lines = [f"{c['word']}: said {c['said']} times." for c in counts] + [HOW_TO_READ]
    used = sum(len(line) + 1 for line in lines)
    listed, next_start = 0, None
    for place in places:
        if start is not None and place.start < start:
            continue
        line = _line(place)
        if listed and used + len(line) + 1 > budget_chars:
            next_start = place.start
            break
        lines.append(line)
        used += len(line) + 1
        listed += 1
    lines.append(f"NEXT {next_start!r}" if next_start is not None else "END")
    return {"text": "\n".join(lines), "words": counts, "listed": listed, "next_start": next_start}
