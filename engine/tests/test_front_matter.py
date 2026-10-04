from imagine_reader.ingestion import story_start

STORY = "Alice was beginning to get very tired of sitting by her sister on the bank. " * 20  # 300 words


def test_the_story_starts_after_the_title_page_and_publishing_notes():
    sections = [
        ("ALICE'S ADVENTURES IN WONDERLAND · LEWIS CARROLL", "This book has been downloaded from www.example.com."),
        ("CHAPTER 1 DOWN THE RABBIT HOLE", STORY),
        ("CHAPTER 2 THE POOL OF TEARS", STORY),
    ]
    assert story_start(sections) == 1


def test_contents_and_prefaces_are_skipped():
    contents = " ".join(f"Chapter {n} A title of some length {n * 7}" for n in range(1, 30))  # 200 words
    sections = [
        ("Great Expectations", "by Charles Dickens"),
        ("Untitled", contents),
        ("Preface", STORY),
        ("Chapter I", STORY),
        *[(f"Chapter {n}", STORY) for n in range(2, 12)],
    ]
    assert story_start(sections) == 3


def test_a_book_without_front_matter_starts_at_the_beginning():
    assert story_start([("The Lantern Keeper", STORY)]) == 0
    assert story_start([("Chapter 1", STORY), ("Chapter 2", STORY)]) == 0
    assert story_start([]) == 0


def test_a_long_opening_section_with_a_link_is_still_the_story():
    long_story = STORY * 6 + " He wrote the address, www.example.com, on the card."
    assert story_start([("Chapter 1", long_story), ("Chapter 2", STORY)]) == 0


def test_short_poems_are_not_mistaken_for_title_pages():
    poems = [(f"Poem {n}", "The rain falls soft upon the hill, and all the town is still.") for n in range(12)]
    assert story_start(poems) == 0


def test_never_skips_most_of_the_book():
    sections = [("Introduction", STORY), ("Introduction, continued", STORY), ("Chapter 1", STORY)]
    assert story_start(sections) == 0  # two thirds of the words would be skipped
