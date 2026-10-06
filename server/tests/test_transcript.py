import json
from pathlib import Path

import pytest

from desk_crit.errors import StudioError
from conftest import spoken_run, spread_evenly
from desk_crit.transcript import (
    load_transcript,
    pack_transcript,
    timing_quality,
    timing_report,
    validate_words,
)

# A copy of the ClipForge 3 minute take's transcript: the plugin's tests can't reach into the repo.
# It's real speech, so the public-tree build withholds it (studio/tools/assemble-public.sh).
# The tests that read it skip when it's missing.
FIXTURE = Path(__file__).parent / "fixtures" / "test_3min.words.json"
needs_fixture = pytest.mark.skipif(not FIXTURE.exists(), reason="the real-speech fixture isn't shipped")


# ── Validation ────────────────────────────────────────────────────────────────


@needs_fixture
def test_real_fixture_loads():
    words = load_transcript(FIXTURE)
    assert len(words) > 100
    assert any(w.get("type") == "event" for w in words)


def test_good_entries_pass_with_extra_keys_and_events(words):
    assert validate_words(words) == words  # energy_rms extras and an event entry


@pytest.mark.parametrize("key", ["word", "start", "end"])
def test_missing_key_names_the_index(words, key):
    del words[3][key]
    with pytest.raises(StudioError, match=f"entry 3 is missing required key '{key}'"):
        validate_words(words)


@pytest.mark.parametrize("bad", ["1.0", None, True, float("nan")])
def test_wrong_type_for_time_names_the_index(words, bad):
    words[5]["start"] = bad
    with pytest.raises(StudioError, match="entry 5: 'start' must be a finite number"):
        validate_words(words)


def test_word_must_be_string(words):
    words[0]["word"] = 7
    with pytest.raises(StudioError, match="entry 0: 'word' must be a string"):
        validate_words(words)


def test_end_before_start_rejected(words):
    words[2]["end"] = 0.1
    with pytest.raises(StudioError, match="entry 2: end"):
        validate_words(words)


def test_not_a_list_rejected():
    with pytest.raises(StudioError, match="JSON list"):
        validate_words({"words": []})


def test_missing_and_corrupt_files(tmp_path):
    with pytest.raises(StudioError, match="Run the transcribe tool"):
        load_transcript(tmp_path / "none.words.json")
    bad = tmp_path / "bad.words.json"
    bad.write_text("[{")
    with pytest.raises(StudioError, match="not valid JSON"):
        load_transcript(bad)


def test_load_sorts_by_start(tmp_path, words):
    path = tmp_path / "x.words.json"
    path.write_text(json.dumps(list(reversed(words))))
    assert [w["start"] for w in load_transcript(path)] == sorted(w["start"] for w in words)


# ── Packing ───────────────────────────────────────────────────────────────────


def _lines(result):
    return result["text"].splitlines()


def test_phrases_split_at_pauses_with_events_on_their_own_line(words):
    lines = _lines(pack_transcript(words))
    assert lines[0] == "[0.50-2.40] Hello everyone and welcome."
    assert lines[1] == "  (pause 1.0)"
    assert lines[2] == "[3.40-6.00] um today we talk about editing"
    assert lines[3] == "[6.00-6.60] [vocalization]"
    assert lines[4] == "  (pause 1.4)"
    assert lines[-1] == "END"


def test_window_limits_and_next_marker(words):
    result = pack_transcript(words, start=3.0, end=11.0)
    lines = _lines(result)
    assert lines[0].startswith("[3.40-")
    assert all("this is the part" not in line for line in lines)
    assert lines[-1] == "NEXT 11.0"
    assert result["next_start"] == 11.0


def test_window_reaching_last_word_ends_with_end(words):
    result = pack_transcript(words, start=12.0, end=100.0)
    assert _lines(result)[-1] == "END"
    assert result["next_start"] is None


def test_bad_window_rejected(words):
    with pytest.raises(StudioError, match="must be after start"):
        pack_transcript(words, start=5, end=5)


def test_size_budget_gives_contiguous_windows(words):
    seen: list[str] = []
    start = None
    for _ in range(20):
        result = pack_transcript(words, start=start, budget_chars=60)
        body = [ln for ln in _lines(result)[:-1] if ln.startswith("[")]
        assert body, "every window makes progress"
        seen.extend(body)
        if _lines(result)[-1] == "END":
            break
        assert _lines(result)[-1] == f"NEXT {result['next_start']!r}"
        start = result["next_start"]
    else:
        pytest.fail("never reached END")
    assert result["next_start"] is None  # final window
    spoken_text = " ".join(ln.split("] ", 1)[1] for ln in seen)
    assert spoken_text == " ".join(w["word"] for w in words)  # no overlap, nothing skipped


def test_show_cuts_marks_and_splits_phrases(words):
    removed = [(3.35, 3.75), (12.0, 12.52)]  # "um", and "this is"
    lines = _lines(pack_transcript(words, removed=removed))
    assert "CUT [3.40-3.70] um" in lines
    assert "[3.90-6.00] today we talk about editing" in lines
    assert "CUT [12.00-12.50] this is" in lines
    assert "[12.55-14.20] the part that matters." in lines


def test_without_show_cuts_nothing_is_marked(words):
    assert "CUT" not in pack_transcript(words)["text"]


# ── Timing quality ────────────────────────────────────────────────────────────


@needs_fixture
def test_measured_fixture_is_measured():
    q = timing_quality(load_transcript(FIXTURE))
    assert q["quality"] == "measured"
    assert q["identical_duration_share"] < 0.1
    assert q["overlapping_pairs"] == 0


def test_hand_written_words_are_measured(words):
    assert timing_quality(words)["quality"] == "measured"
    assert "warning" not in timing_report(words)


def test_evenly_spread_timings_are_approximate():
    words = spread_evenly([
        ("So the first thing I want to say is this.", 0.0, 4.2),
        ("We'll do three sections today.", 3.9, 6.4),  # overlaps the sentence before
        ("The first part covers the basics of editing.", 6.4, 10.0),
    ])
    q = timing_quality(words)
    assert q["quality"] == "approximate"
    assert q["identical_duration_share"] > 0.8
    assert q["overlapping_pairs"] >= 1
    assert "estimates" in timing_report(words)["warning"]


# ── Sentence-aware packing ────────────────────────────────────────────────────


def test_lines_end_at_sentences_and_join_short_ones():
    # 4 short sentences, no pause long enough to split on (gaps of 0.05 s).
    words = []
    t = 0.0
    for text in ["I made a thing.", "It took a while.", "Now it works fine.",
                 "Here is how I did it.", "First the setup, then the rest."]:
        run = spoken_run(text, t)
        words += run
        t = run[-1]["end"] + 0.05
    lines = [ln for ln in pack_transcript(words)["text"].splitlines() if ln.startswith("[")]
    assert len(lines) < 5  # short sentences were joined
    for line in lines:
        assert line.endswith(".")  # every line ends where a sentence does
        a, b = (float(x) for x in line[1:line.index("]")].split("-"))
        assert b - a <= 12.0


def test_long_sentence_breaks_at_a_comma():
    words = spoken_run(" ".join(["word"] * 30) + ", " + " ".join(["more"] * 30) + " end.", 0.0)
    assert words[-1]["end"] > 20
    lines = [ln for ln in pack_transcript(words)["text"].splitlines() if ln.startswith("[")]
    assert len(lines) == 2
    assert lines[0].endswith("word,")


def test_long_sentence_without_comma_breaks_at_the_longest_gap():
    first = spoken_run(" ".join(["alpha"] * 30), 0.0)
    second = spoken_run(" ".join(["beta"] * 30) + " done.", first[-1]["end"] + 0.3)
    lines = [ln for ln in pack_transcript(first + second)["text"].splitlines() if ln.startswith("[")]
    assert len(lines) == 2
    assert lines[1].split("] ")[1].startswith("beta")


@needs_fixture
def test_fixture_lines_mostly_end_at_sentences_or_pauses():
    lines = pack_transcript(load_transcript(FIXTURE), budget_chars=10**9)["text"].splitlines()
    speech = [ln for ln in lines if ln.startswith("[") and "[vocalization]" not in ln]
    for line in speech:
        a, b = (float(x) for x in line[1:line.index("]")].split("-"))
        assert b - a <= 20.0


# ── Sound labels in packing ───────────────────────────────────────────────────


def _label(start, end, kind, confidence="possible"):
    return {"start": start, "end": end, "seconds": round(end - start, 2), "kind": kind,
            "confidence": confidence, "punchline": None}


def test_labels_replace_the_event_word(words):
    lines = _lines(pack_transcript(words, labels=[_label(6.0, 6.6, "sound")]))
    assert lines[3] == "[6.00-6.60] (sound 0.6s)"


@pytest.mark.parametrize("confidence, text", [("likely", "(laugh 3.5s)"), ("possible", "(laugh? 3.5s)")])
def test_laugh_labels_show_confidence(confidence, text):
    words = spoken_run("That is the joke.", 0.0) + [
        {"word": "[vocalization]", "start": 2.0, "end": 5.52, "type": "event"},
    ]
    lines = _lines(pack_transcript(words, labels=[_label(2.0, 5.52, "laugh", confidence)]))
    assert f"[2.00-5.52] {text}" in lines


def test_no_labels_packs_exactly_as_before(words):
    before = pack_transcript(words)
    assert pack_transcript(words, labels=None) == before
    assert pack_transcript(words, labels=[]) == before
    if FIXTURE.exists():
        fixture = load_transcript(FIXTURE)
        assert pack_transcript(fixture, labels=None) == pack_transcript(fixture)


def test_event_without_a_matching_label_keeps_its_word(words):
    lines = _lines(pack_transcript(words, labels=[_label(99.0, 99.5, "laugh", "likely")]))
    assert "[6.00-6.60] [vocalization]" in lines


def test_labels_and_cut_marks_combine(words):
    lines = _lines(pack_transcript(words, removed=[(5.9, 6.7)], labels=[_label(6.0, 6.6, "laugh", "likely")]))
    assert "CUT [6.00-6.60] (laugh 0.6s)" in lines
    assert pack_transcript(words, labels=[_label(6.0, 6.6, "sound")])["phrase_count"] == pack_transcript(words)["phrase_count"]
