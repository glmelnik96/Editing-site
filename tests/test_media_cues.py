"""Типографика субтитров (спека §10.9, правила Т1–Т8): чистые функции, ни диска, ни ffmpeg."""
from server.media.cues import build_cues, is_bad_break, is_glue_word, polish_edges, wrap_lines


def w(text: str, start: float, end: float) -> dict:
    return {"w": text, "s": start, "e": end}


def test_glue_words_are_recognized_with_punctuation():
    assert is_glue_word("и") and is_glue_word("В") and is_glue_word("что,")
    assert not is_glue_word("дом")


def test_word_ending_in_punctuation_is_a_natural_break():
    """«что,» в конце строки читается нормально: знак уже сказал, что фраза кончилась."""
    assert is_bad_break("что") is True
    assert is_bad_break("что,") is False


def test_number_does_not_part_from_its_word():
    assert is_bad_break("5") is True


def test_no_break_after_an_opening_quote():
    assert is_bad_break("«") is True


def test_no_break_after_an_opening_bracket_or_a_dangling_dash():
    """Т6: скобка и тире висят на конце строки так же плохо, как кавычка."""
    assert is_bad_break("(") is True
    assert is_bad_break("—") is True
    assert is_bad_break("сказал(") is True


def test_a_plain_word_is_a_fine_place_to_break():
    assert is_bad_break("дом") is False
    assert is_bad_break("") is False


def test_edges_lose_a_comma_but_keep_a_question_mark():
    assert polish_edges(["Привет,"]) == ["Привет"]
    assert polish_edges(["Привет?"]) == ["Привет?"]
    assert polish_edges([",", "дальше"]) == ["", "дальше"]


def test_edges_keep_an_ellipsis_typed_as_three_dots():
    """Т1: многоточие меняет интонацию и остаётся; расшифровка пишет его точками, а не «…»."""
    assert polish_edges(["Ну…"]) == ["Ну…"]
    assert polish_edges(["Ну..."]) == ["Ну..."]


def test_edges_lose_a_dash_at_the_end_but_keep_one_at_the_start():
    """Т2: тире в начале — реплика диалога, оно остаётся; на конце оно повисает ни на чём."""
    assert polish_edges(["—", "Привет"]) == ["—", "Привет"]
    assert polish_edges(["Привет", "—"]) == ["Привет", ""]


def test_edges_strip_punctuation_behind_a_closing_quote():
    assert polish_edges(["сказал.»"]) == ["сказал»"]


def test_edges_of_an_empty_cue():
    assert polish_edges([]) == []


def test_line_break_avoids_a_hanging_preposition():
    words = ["Мы", "поехали", "в", "большой", "старый", "дом"]
    assert wrap_lines(words, max_chars=20, max_lines=2) == "Мы поехали\nв большой старый дом"


def test_line_break_does_not_part_a_number_from_its_word():
    """Т5: разрыв после «5» балансирует строки лучше всех, и всё равно проигрывает."""
    words = ["весит", "5", "кг", "ровно"]
    assert wrap_lines(words, max_chars=10, max_lines=2) == "весит\n5 кг ровно"


def test_lines_are_balanced_and_the_top_one_is_shorter():
    words = ["раз", "два", "три", "четыре"]
    top, bottom = wrap_lines(words, max_chars=14, max_lines=2).split("\n")
    assert len(top) <= len(bottom)


def test_at_equal_balance_the_top_line_is_the_shorter_one():
    """Т8: обе половины расходятся на 4 знака, «пирамида» решает ничью."""
    assert wrap_lines(["раз", "два", "три"], max_chars=8, max_lines=2) == "раз\nдва три"


def test_fewer_lines_win_when_everything_fits():
    assert wrap_lines(["раз", "два"], max_chars=20, max_lines=2) == "раз два"


def test_single_word_needs_no_break():
    assert wrap_lines(["слово"], max_chars=20, max_lines=2) == "слово"


def test_one_line_stays_one_line_even_when_nothing_fits():
    """Ширину нарушить можно, число строк — нет: под однострочный субтитр свёрстан кадр."""
    assert wrap_lines(["раз", "два"], max_chars=4, max_lines=1) == "раз два"


def test_overlong_words_are_never_cut_by_the_wrap():
    words = ["длинноеслово", "другоедлинное"]
    assert wrap_lines(words, max_chars=5, max_lines=2) == "длинноеслово\nдругоедлинное"


def test_a_wordy_cue_does_not_stall_the_search():
    """Триста слов в две строки не влезают никак: включается аварийное разбиение, а не перебор."""
    words = [f"с{i}" for i in range(300)]
    out = wrap_lines(words, max_chars=40, max_lines=2)
    assert len(out.split("\n")) == 2


def test_a_many_line_search_stays_within_its_budget():
    """Число строк приходит из настроек, и на пяти полный перебор считался бы секундами."""
    lines = wrap_lines(["и"] * 80, max_chars=100, max_lines=5).split("\n")
    assert len(lines) <= 5
    assert all(len(line) <= 100 for line in lines)


def test_cue_does_not_end_on_a_conjunction():
    """Одинокое «и» на экране читатель прочитает дважды: слово уезжает в следующую реплику."""
    words = [w("Мы", 0.0, 0.4), w("пошли", 0.4, 1.0), w("и", 1.0, 1.2),
             w("увидели", 1.2, 2.0), w("дом", 2.0, 2.6)]
    cues = build_cues(words, max_chars=10, max_lines=1, max_dur=4.0)
    assert cues[0]["text"].split()[-1] != "и"
    assert cues[1]["text"] == "и увидели"


def test_cue_hands_back_no_more_than_two_words_in_a_row():
    """Т3 отступает не глубже двух слов: иначе служебная цепочка растащит половину фразы."""
    words = [w("дом", 0.0, 0.4), w("и", 0.4, 0.6), w("в", 0.6, 0.8),
             w("то", 0.8, 1.0), w("место", 1.0, 1.6)]
    cues = build_cues(words, max_chars=10, max_lines=1, max_dur=4.0)
    assert cues[0]["text"] == "дом и"


def test_cue_keeps_a_glue_word_that_ends_a_phrase():
    """«что?» знаком препинания уже закрыто — уезжать в следующую реплику ему незачем."""
    words = [w("Он", 0.0, 0.4), w("сказал", 0.4, 1.0), w("что?", 1.0, 1.4),
             w("наверное", 1.4, 2.0)]
    cues = build_cues(words, max_chars=17, max_lines=1, max_dur=4.0)
    assert cues[0]["text"] == "Он сказал что?"


def test_cue_is_split_by_duration():
    words = [w(f"с{i}", float(i), float(i) + 1.0) for i in range(8)]
    cues = build_cues(words, max_chars=100, max_lines=2, max_dur=3.0)
    assert len(cues) > 1
    assert all(cue["end"] - cue["start"] <= 3.0 + 1e-6 for cue in cues)


def test_a_single_word_longer_than_the_limit_still_becomes_a_cue():
    """Резать слово по времени нечем: показываем как есть, иначе реплика пропадёт вовсе."""
    cues = build_cues([w("тяяяянется", 0.0, 9.0)], max_chars=40, max_lines=2, max_dur=4.0)
    assert len(cues) == 1
    assert cues[0]["end"] - cues[0]["start"] == 9.0


def test_cue_times_come_from_the_words():
    words = [w("раз", 1.0, 1.5), w("два", 1.5, 2.25)]
    cues = build_cues(words, max_chars=40, max_lines=2, max_dur=4.0)
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 2.25


def test_cue_does_not_end_with_a_comma():
    """Т1: запятая в конце реплики смысла не несёт, а место занимает."""
    words = [w("Привет,", 0.0, 0.5), w("мир", 0.5, 1.0)]
    cues = build_cues(words, max_chars=7, max_lines=1, max_dur=4.0)
    assert cues[0]["text"] == "Привет"
    assert cues[0]["words"][0]["w"] == "Привет"


def test_a_punctuation_only_word_leaves_with_its_time():
    """Т2: осиротевшая запятая выбрасывается вместе со своим временем, иначе времена разъедутся."""
    words = [w(",", 0.0, 0.2), w("дальше", 0.2, 1.0)]
    cues = build_cues(words, max_chars=20, max_lines=2, max_dur=4.0)
    assert cues[0]["text"] == "дальше"
    assert cues[0]["start"] == 0.2
    assert [x["w"] for x in cues[0]["words"]] == ["дальше"]


def test_overlong_word_becomes_its_own_cue():
    """Резать слово переносом хуже, чем нарушить ширину."""
    words = [w("короткое", 0.0, 0.5), w("невероятноразмашистоедлинное", 0.5, 1.5)]
    cues = build_cues(words, max_chars=10, max_lines=2, max_dur=4.0)
    assert any(cue["text"] == "невероятноразмашистоедлинное" for cue in cues)


def test_empty_input_gives_no_cues():
    assert build_cues([], max_chars=42, max_lines=2, max_dur=4.0) == []


def test_words_survive_in_the_cue():
    """Слова остаются при реплике: по ним панель подсвечивает текущее слово."""
    words = [w("раз", 0.0, 0.5), w("два", 0.5, 1.0)]
    cue = build_cues(words, max_chars=40, max_lines=2, max_dur=4.0)[0]
    assert [x["w"] for x in cue["words"]] == ["раз", "два"]


def test_word_flags_survive_in_the_cue():
    """Флаг interpolated нужен панели, чтобы подсветить неподтверждённую границу слова."""
    words = [{"w": "раз", "s": 0.0, "e": 0.5, "interpolated": True}]
    cue = build_cues(words, max_chars=40, max_lines=2, max_dur=4.0)[0]
    assert cue["words"][0]["interpolated"] is True


def test_a_word_without_times_is_skipped():
    """Расшифровку правит человек: слово без времени показать нечем, а реплику оно порвёт."""
    words = [w("раз", 0.0, 0.5), {"w": "два"}, w("три", 0.5, 1.0)]
    cues = build_cues(words, max_chars=40, max_lines=2, max_dur=4.0)
    assert cues[0]["text"] == "раз три"


def test_every_line_of_a_cue_fits_the_width():
    spoken = ["мы", "поехали", "в", "большой", "старый", "дом", "на", "реке", "и", "там", "остались"]
    words = [w(text, i / 4, (i + 1) / 4) for i, text in enumerate(spoken)]
    cues = build_cues(words, max_chars=20, max_lines=2, max_dur=4.0)
    assert cues
    assert all(len(line) <= 20 for cue in cues for line in cue["text"].split("\n"))
