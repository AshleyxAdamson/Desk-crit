"""find_words: where a word is said, its id and clock, the words around it, pagination, and what it refuses."""

import pytest
from conftest import make_words

from desk_crit import tools, word_finder
from desk_crit.errors import StudioError
from desk_crit.word_finder import check_phrases, find_places, pack_places


def places_of(phrases, words=None):
    parsed = check_phrases(phrases)
    return find_places(words if words is not None else make_words(), parsed), parsed


def test_a_place_has_an_id_built_from_the_words_own_times():
    places, _ = places_of(["we"])
    assert [p.id for p in places] == ["w4.350-4.500", "w8.450-8.600"]
    assert (places[0].start, places[0].end) == (4.35, 4.5)


def test_a_phrase_takes_the_start_of_its_first_word_and_the_end_of_its_last():
    (place,), _ = places_of(["talk about"], make_words()[:11])
    assert place.id == "w4.550-5.300" and place.said == "talk about"


def test_the_clock_is_minutes_and_whole_seconds():
    places, parsed = places_of(["we"])
    lines = pack_places(places, parsed)["text"].splitlines()
    assert lines[2].startswith("w4.350-4.500 0:04 ")
    assert lines[3].startswith("w8.450-8.600 0:08 ")
    words = [{"word": "here", "start": 125.4, "end": 125.6}]
    assert pack_places(*places_of(["here"], words))["text"].splitlines()[2].startswith("w125.400-125.600 2:05 ")


def test_context_is_what_there_is_at_the_ends_of_the_recording():
    places, parsed = places_of(["watching"])
    line = pack_places(places, parsed)["text"].splitlines()[2]
    assert line == "w16.250-17.000 0:16 the part that matters. thanks for [watching.]"
    first = places_of(["hello"])[0][0]
    assert first.before == "" and first.after.split() == ["everyone", "and", "welcome.", "um", "today", "we"]


def test_context_is_exactly_six_words_when_there_are_that_many():
    words = [{"word": f"w{i}", "start": i, "end": i + 0.5} for i in range(20)]
    (place,), _ = places_of(["w10"], words)
    assert place.before == "w4 w5 w6 w7 w8 w9" and place.after == "w11 w12 w13 w14 w15 w16"


def test_a_sound_is_shown_as_a_sound():
    places, _ = places_of(["talk"])
    assert places[0].before == "everyone and welcome. um today we"
    assert places[0].after == "about editing (sound) today we talk"
    (second,) = [p for p in places_of(["today"])[0] if p.start == 8.0]
    assert second.before.endswith("about editing (sound)")


def test_matching_ignores_case_and_the_punctuation_around_a_word():
    places, _ = places_of(["Welcome"])
    assert [p.said for p in places] == ["welcome."]


def test_the_summary_line_counts_each_phrase_and_the_text_ends_in_end():
    places, parsed = places_of(["we", "talk about", "absent"])
    packed = pack_places(places, parsed)
    lines = packed["text"].splitlines()
    assert lines[:3] == ["we: said 2 times.", "talk about: said 2 times.", "absent: said 0 times."]
    assert lines[3] == word_finder.HOW_TO_READ
    assert lines[-1] == "END" and packed["next_start"] is None and packed["listed"] == 4
    assert packed["words"] == [{"word": "we", "said": 2}, {"word": "talk about", "said": 2}, {"word": "absent", "said": 0}]


def test_the_lines_carry_no_edit_columns():
    places, parsed = places_of(["we"])
    text = pack_places(places, parsed)["text"]
    for word in ("OUT", "NOT CLEAN", "quiet", "clean", "cut"):
        assert word not in text, word


def test_places_come_in_time_order_across_phrases():
    places, _ = places_of(["we", "today"])
    assert [p.start for p in places] == sorted(p.start for p in places)


def test_pagination_stops_at_the_budget_and_next_goes_on_without_overlap():
    words = [{"word": "this", "start": float(i), "end": i + 0.4} for i in range(60)]
    places, parsed = places_of(["this"], words)
    first = pack_places(places, parsed, budget_chars=700)
    assert 0 < first["listed"] < 60 and first["next_start"] is not None
    assert first["text"].splitlines()[-1] == f"NEXT {first['next_start']!r}"
    seen = first["listed"]
    nxt, rounds = first["next_start"], 0
    while nxt is not None:
        page = pack_places(places, parsed, start=nxt, budget_chars=700)
        lines = page["text"].splitlines()
        ids = [ln.split()[0] for ln in lines if ln.startswith("w")]
        assert ids[0] == f"w{nxt:.3f}-{nxt + 0.4:.3f}"
        seen += page["listed"]
        nxt, rounds = page["next_start"], rounds + 1
        assert rounds < 100
    assert seen == 60


def test_one_place_is_always_listed_even_when_it_is_over_the_budget():
    places, parsed = places_of(["we"])
    packed = pack_places(places, parsed, budget_chars=1)
    assert packed["listed"] == 1 and packed["next_start"] == places[1].start


def test_the_default_budget_is_24000_characters():
    assert word_finder.PACK_BUDGET_CHARS == 24000


@pytest.mark.parametrize("bad", [[], "this", ["this", 3], [""], ["   "], ["..."]])
def test_a_list_that_is_not_one_to_five_phrases_is_refused(bad):
    with pytest.raises(StudioError, match="words|holds no word"):
        check_phrases(bad)


def test_zero_phrases_and_six_phrases_are_rejected_through_the_tool(video):
    with pytest.raises(StudioError, match="one to 5"):
        tools.find_words(str(video), [])
    with pytest.raises(StudioError, match="at most 5"):
        tools.find_words(str(video), ["a", "b", "c", "d", "e", "f"])
    assert tools.find_words(str(video), ["a", "b", "c", "d", "e"])["listed"] == 0


def test_a_phrase_longer_than_four_words_is_refused():
    with pytest.raises(StudioError, match="at most 4 words"):
        check_phrases(["one two three four five"])
    assert check_phrases(["one two three four"]) == [["one", "two", "three", "four"]]


def test_the_same_phrase_twice_is_looked_up_once():
    assert check_phrases(["This", "this,"]) == [["this"]]


def test_the_tool_answers_text_counts_and_the_next_start(video):
    answer = tools.find_words(str(video), ["we"])
    assert answer["text"].startswith("we: said 2 times.") and answer["words"] == [{"word": "we", "said": 2}]
    assert answer["listed"] == 2 and answer["next_start"] is None
    assert "word_times" not in answer


def test_start_outside_the_recording_is_refused(video):
    with pytest.raises(StudioError, match="outside the recording"):
        tools.find_words(str(video), ["we"], start=99.0)
    with pytest.raises(StudioError, match="outside the recording"):
        tools.find_words(str(video), ["we"], start=-1.0)


def test_with_no_transcript_it_says_to_run_transcribe(video):
    video.with_suffix(".words.json").unlink()
    with pytest.raises(StudioError, match="transcribe"):
        tools.find_words(str(video), ["we"])
    with pytest.raises(StudioError, match="transcribe"):
        tools.read_transcript(str(video))
