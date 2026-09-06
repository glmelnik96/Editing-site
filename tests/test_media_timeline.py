from server.media.timeline import words_through_clips


def word(text, s, e):
    return {"w": text, "s": s, "e": e, "interpolated": True}


TRANSCRIPT = {
    "segments": [
        {"start": 0.0, "end": 10.0, "text": "…",
         "words": [word("раз", 1.0, 2.0), word("два", 4.0, 5.0), word("три", 8.0, 9.0)]}
    ]
}


def test_word_moves_into_the_timeline():
    """Слово внутри клипа сдвигается на смещение клипа минус его точка входа."""
    clips = [{"asset_id": "ast_1", "in": 3.0, "out": 6.0}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert [x["w"] for x in out] == ["два"]
    assert out[0]["s"] == 1.0 and out[0]["e"] == 2.0


def test_second_clip_continues_the_timeline():
    clips = [{"asset_id": "ast_1", "in": 0.0, "out": 3.0}, {"asset_id": "ast_1", "in": 7.0, "out": 10.0}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert [x["w"] for x in out] == ["раз", "три"]
    assert out[1]["s"] == 3.0 + (8.0 - 7.0)


def test_word_on_the_edge_is_trimmed():
    """Слово, наполовину вырезанное клипом, обрезается по краю, а не пропадает и не вылезает."""
    clips = [{"asset_id": "ast_1", "in": 0.0, "out": 1.5}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert out[0]["w"] == "раз" and out[0]["e"] == 1.5


def test_word_fully_outside_is_dropped():
    clips = [{"asset_id": "ast_1", "in": 6.0, "out": 7.0}]
    assert words_through_clips(TRANSCRIPT, clips, asset_id="ast_1") == []


def test_clips_of_other_assets_are_skipped_but_shift_the_timeline():
    """Чужой клип занимает место в ролике: слова после него обязаны сдвинуться на его длину."""
    clips = [{"asset_id": "ast_2", "in": 0.0, "out": 5.0}, {"asset_id": "ast_1", "in": 1.0, "out": 3.0}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert out[0]["w"] == "раз" and out[0]["s"] == 5.0 + (1.0 - 1.0)


def test_a_transcript_without_words_gives_nothing():
    assert words_through_clips({"segments": [{"start": 0.0, "end": 1.0, "text": "…"}]},
                               [{"asset_id": "ast_1", "in": 0.0, "out": 1.0}], asset_id="ast_1") == []


def test_the_same_source_used_twice_appears_twice():
    """Один и тот же кусок исходника, поставленный дважды, и озвучен дважды — субтитр тоже."""
    clips = [{"asset_id": "ast_1", "in": 0.0, "out": 3.0}, {"asset_id": "ast_1", "in": 0.0, "out": 3.0}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert [x["w"] for x in out] == ["раз", "раз"]
    assert out[0]["s"] == 1.0 and out[1]["s"] == 4.0


def test_words_keep_their_own_fields():
    clips = [{"asset_id": "ast_1", "in": 0.0, "out": 3.0}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert out[0]["interpolated"] is True, "пометка обязана дожить: по нашим словам резать нельзя"


def test_order_follows_the_timeline_not_the_source():
    """Куски переставлены местами — субтитры идут в порядке ролика, а не исходника."""
    clips = [{"asset_id": "ast_1", "in": 7.0, "out": 10.0}, {"asset_id": "ast_1", "in": 0.0, "out": 3.0}]
    out = words_through_clips(TRANSCRIPT, clips, asset_id="ast_1")
    assert [x["w"] for x in out] == ["три", "раз"]
    assert out[0]["s"] < out[1]["s"]
