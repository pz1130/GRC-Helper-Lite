from app.indexing.chunking import MAX_CHARS, OVERLAP_CHARS, split, with_context


def test_short_text_stays_one_chunk():
    chunks = split("Normal changes follow the CAB cycle.")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].text == "Normal changes follow the CAB cycle."


def test_empty_text_yields_nothing():
    assert split("") == []
    assert split("   \n  ") == []


def test_long_text_is_split():
    text = "Sentence about privileged access. " * 100
    chunks = split(text)
    assert len(chunks) > 1
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_no_chunk_exceeds_the_limit():
    text = "Sentence about privileged access management controls. " * 100
    assert all(len(c.text) <= MAX_CHARS for c in split(text))


def test_never_splits_inside_a_word():
    text = "supercalifragilistic " * 200
    for chunk in split(text):
        assert not chunk.text.startswith(" ")
        assert chunk.text.split()[0] in text
        assert chunk.text.split()[-1] in text


def test_prefers_paragraph_boundaries():
    first = "A" * 600
    second = "B" * 600
    chunks = split(f"{first}\n\n{second}", max_chars=700, overlap=0)
    assert chunks[0].text == first
    assert chunks[1].text == second


def test_falls_back_to_sentence_boundaries():
    body = ". ".join(f"Sentence number {i} about controls" for i in range(60)) + "."
    chunks = split(body, max_chars=400, overlap=0)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert chunk.text.rstrip().endswith(".")


def test_overlap_repeats_the_tail_of_the_previous_chunk():
    text = "word " * 600
    chunks = split(text, max_chars=500, overlap=100)
    assert len(chunks) > 1
    tail = chunks[0].text[-80:]
    assert tail.strip() in chunks[1].text


def test_with_context_prefixes_the_heading_path():
    prefixed = with_context("Introduction › Periodic Review", "Reviewed annually.")
    assert prefixed.startswith("Introduction › Periodic Review")
    assert "Reviewed annually." in prefixed


def test_with_context_handles_an_empty_body():
    assert with_context("A › B", "") == "A › B"


def test_defaults_are_explicit():
    assert MAX_CHARS == 1200
    assert OVERLAP_CHARS == 150
