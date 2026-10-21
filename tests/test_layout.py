from career_copilot.layout import is_multi_column, merge_wrapped_lines
from career_copilot.layout import VisualLine


def _word(x0: float, x1: float, top: float, text: str = "word") -> dict[str, object]:
    return {"x0": x0, "x1": x1, "top": top, "text": text}


def test_right_aligned_dates_are_not_a_second_column():
    words = []
    for line in range(10):
        top = 100 + line * 14
        x = 72
        for _index in range(6):
            words.append(_word(x, x + 40, top, "alpha"))
            x += 48
        if line < 2:
            words.append(_word(460, 540, top, "2020"))
    assert is_multi_column(words, 600) is False


def test_contents_page_numbers_are_not_a_second_column():
    words = []
    for line in range(14):
        top = 80 + line * 16
        words.append(_word(70, 220, top, "Chapter"))
        words.append(_word(228, 280, top, str(line + 1)))
        words.append(_word(500, 524, top, "iii"))
    assert is_multi_column(words, 595) is False


def test_a_real_gutter_is_multi_column():
    words = []
    for line in range(10):
        top = 80 + line * 14
        for index, text in enumerate(("alpha", "beta", "gamma")):
            words.append(_word(50 + index * 40, 80 + index * 40, top, text))
            words.append(_word(360 + index * 40, 390 + index * 40, top, text))
    assert is_multi_column(words, 600) is True


def test_wrapped_sentence_joins_and_a_new_role_does_not():
    lines = [
        VisualLine("Knowledge engineer specialized in data modeling and graphs to inject rich", 72, 540, 100),
        VisualLine("contextual logic into models.", 72, 240, 114),
        VisualLine("Engineer | Acme 2020 - 2022", 72, 400, 140),
        VisualLine("Led the migration.", 72, 200, 154),
    ]
    blocks = merge_wrapped_lines(lines, content_right=540)
    assert [block.text for block in blocks] == [
        "Knowledge engineer specialized in data modeling and graphs to inject rich contextual logic into models.",
        "Engineer | Acme 2020 - 2022",
        "Led the migration.",
    ]
    assert blocks[2].kind.value == "paragraph"


def test_a_full_line_ending_in_a_period_still_wraps():
    lines = [
        VisualLine('Paper title ending at the margin.". ', 72, 540, 100),
        VisualLine("Journal Name 30(2) (2022): 153-169. Published. (DOI:", 72, 400, 114),
        VisualLine("10.1000/example.1)", 72, 180, 128),
        VisualLine("Languages", 72, 120, 150),
    ]
    # "Languages" is not a known heading here, so the stopper is the short sentence end.
    blocks = merge_wrapped_lines(lines[:3], content_right=540)
    assert len(blocks) == 1
    assert "10.1000/example.1" in blocks[0].text

    stopped = merge_wrapped_lines(
        [
            VisualLine("A short finished sentence.", 72, 200, 100),
            VisualLine("This is the next paragraph.", 72, 280, 114),
        ],
        content_right=540,
    )
    assert len(stopped) == 2
