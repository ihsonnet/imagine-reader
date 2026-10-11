from pathlib import Path

from imagine_reader.ingestion import ingest_pdf
from imagine_reader.narration import HEADING_PAUSE, PARAGRAPH_PAUSE, SENTENCE_PAUSE, clip_key, plan_section
from imagine_reader.providers import MockSpeechProvider, wav_duration

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pdfs" / "two-column.pdf"


def test_mock_narrator_paces_clips_by_word_count():
    provider = MockSpeechProvider()
    short = provider.synthesize("Then another.", provider.default_voice)
    long = provider.synthesize("One by one, the fishing boats turned toward the harbor.", provider.default_voice)
    assert short.media_type == "audio/wav"
    assert short.duration_seconds == wav_duration(short.audio)
    assert 0.7 < short.duration_seconds < 1.0  # two words at 165 per minute, plus a breath
    assert long.duration_seconds > 3 * short.duration_seconds
    assert provider.synthesize("Then another.", "placeholder").audio == short.audio  # deterministic


def test_section_plan_follows_sentences_with_structural_pauses():
    document = ingest_pdf(FIXTURE)
    items = plan_section(document, document.sections[0])
    assert [item.order for item in items] == list(range(len(document.sentences)))
    assert items[0].text == "The Lantern Keeper" and items[0].pause_after == HEADING_PAUSE
    assert items[1].pause_after == SENTENCE_PAUSE  # "Every evening ..." continues its paragraph
    assert items[4].text == "He had never once counted wrong." and items[4].pause_after == PARAGRAPH_PAUSE


def test_clip_keys_change_with_provider_voice_or_text():
    keys = {
        clip_key("mock-1", "placeholder", "Hello."),
        clip_key("mock-2", "placeholder", "Hello."),
        clip_key("mock-1", "other", "Hello."),
        clip_key("mock-1", "placeholder", "Hello!"),
    }
    assert len(keys) == 4
    assert clip_key("mock-1", "placeholder", "Hello.") == clip_key("mock-1", "placeholder", "Hello.")


def test_stories_are_told_in_the_story_style_with_longer_pauses():
    from imagine_reader.narration import PAUSES, narration_style, plan_sentences
    from imagine_reader.story import SentenceText

    story = ["“Oh dear! I shall be late!” said the Rabbit.", "Alice ran after it.", "She was curious."] * 10
    manual = ["Remove the four screws from the back panel.", "Lift the panel away.", "Disconnect the cable."] * 10
    assert narration_style(story) == "story"
    assert narration_style(manual) == "plain" and narration_style([]) == "plain"

    sentences = [
        SentenceText("s1", 0, "p1", "One."),
        SentenceText("s2", 1, "p1", "Two."),
        SentenceText("s3", 2, "p2", "Three."),
    ]
    told = plan_sentences(sentences, set(), "story")
    assert [item.pause_after for item in told] == [PAUSES["story"][0], PAUSES["story"][1], PAUSES["story"][1]]
    assert told[0].pause_after > plan_sentences(sentences, set())[0].pause_after


def test_a_narrator_that_reads_runs_gets_whole_paragraphs_or_several_and_no_pause_inside_a_run():
    from imagine_reader.narration import NarrationItem, read_in_runs

    pauses = [0.4, 0.4, 0.9, 0.4, 0.9, 1.3, 0.4, 0.9]  # paragraph ends at 2 and 4, a heading at 5
    items = [NarrationItem(sentence_id=f"s{n}", order=n, text="x" * 100, pause_after=p) for n, p in enumerate(pauses)]

    def shape(runs):
        return [[(item.order, item.pause_after) for item in run] for run in runs]

    assert shape(read_in_runs(items, "story", 6, 700)) == [
        [(0, 0.0), (1, 0.0), (2, 0.9)], [(3, 0.0), (4, 0.9)], [(5, 1.3)], [(6, 0.0), (7, 0.9)]
    ]  # fmt: skip
    assert shape(read_in_runs(items, "story", 40, 1500, across_paragraphs=True)) == [
        [(0, 0.0), (1, 0.0), (2, 0.0), (3, 0.0), (4, 0.0), (5, 1.3)], [(6, 0.0), (7, 0.9)]
    ]  # fmt: skip
    assert [len(run) for run in read_in_runs(items, "story", 40, 250, across_paragraphs=True)] == [2, 2, 2, 2]
