import pytest

from server.app.config import Settings
from server.app.projects.doc import AssetInfo, ProjectInvalid, clamp_fades, validate_doc

S = Settings(_env_file=None)
ASSETS = {
    "ast_000000000001": AssetInfo(kind="video", status="proxy_ready", duration=120.0),
    "ast_000000000002": AssetInfo(kind="video", status="ready", duration=60.0),
    "ast_000000000003": AssetInfo(kind="audio", status="proxy_ready", duration=200.0),
    "ast_000000000004": AssetInfo(kind="subtitle", status="ready", duration=None),
    "ast_000000000005": AssetInfo(kind="video", status="analyzing", duration=None),
    "ast_000000000006": AssetInfo(kind="image", status="proxy_ready", duration=None),
}


def clip(**over) -> dict:
    return {"asset_id": "ast_000000000001", "in": 1.0, "out": 5.0, **over}


def doc(**over) -> dict:
    return {"clips": [clip()], **over}


def errors_of(raw) -> list[str]:
    with pytest.raises(ProjectInvalid) as e:
        validate_doc(raw, assets=ASSETS, settings=S)
    return [item["field"] for item in e.value.errors]


def test_minimal_document_gets_defaults():
    out = validate_doc(doc(), assets=ASSETS, settings=S)
    assert out["output"] == {"aspect": "16:9", "fit": "pad", "fps": 30}
    assert out["music"] is None and out["subtitles"] is None
    c = out["clips"][0]
    assert c["id"] == "c1" and c["snap_to_pauses"] is False
    assert c["in_verified"] is False and c["out_verified"] is False
    assert c["in"] == 1.0 and c["out"] == 5.0
    assert c["volume"] == 1.0


def test_times_are_rounded_to_milliseconds():
    out = validate_doc(doc(clips=[clip(**{"in": 1.00049, "out": 5.6667})]), assets=ASSETS, settings=S)
    assert out["clips"][0]["in"] == 1.0 and out["clips"][0]["out"] == 5.667


def test_client_cannot_set_verification_flags():
    """Флаги подтверждения выставляет только сервер (раздел 4 спеки)."""
    raw = doc(clips=[clip(in_verified=True, out_verified=True)])
    out = validate_doc(raw, assets=ASSETS, settings=S)
    assert out["clips"][0]["in_verified"] is False and out["clips"][0]["out_verified"] is False


def test_ids_are_kept_and_generated():
    raw = doc(clips=[clip(id="left"), clip(**{"in": 10, "out": 12})])
    out = validate_doc(raw, assets=ASSETS, settings=S)
    assert [c["id"] for c in out["clips"]] == ["left", "c2"]


def test_duplicate_ids_are_rejected():
    assert errors_of(doc(clips=[clip(id="x"), clip(id="x", **{"in": 9, "out": 10})])) == ["clips[1].id"]


def test_clip_count_bounds():
    assert errors_of({"clips": []}) == ["clips"]
    many = [clip(**{"in": 0, "out": 0.5}) for _ in range(S.max_clips + 1)]
    assert errors_of({"clips": many}) == ["clips"]


def test_clip_time_rules():
    assert errors_of(doc(clips=[clip(**{"in": 5, "out": 5})])) == ["clips[0].out"]
    assert errors_of(doc(clips=[clip(**{"in": 6, "out": 5})])) == ["clips[0].out"]
    assert errors_of(doc(clips=[clip(**{"in": -1, "out": 5})])) == ["clips[0].in"]
    assert errors_of(doc(clips=[clip(**{"in": 1, "out": 500})])) == ["clips[0].out"]
    assert errors_of(doc(clips=[clip(**{"in": 1.0, "out": 1.05})])) == ["clips[0].out"]


def test_clip_asset_rules():
    assert errors_of(doc(clips=[clip(asset_id="ast_00000000dead")])) == ["clips[0].asset_id"]
    assert errors_of(doc(clips=[clip(asset_id="ast_000000000003")])) == ["clips[0].asset_id"]  # звук
    assert errors_of(doc(clips=[clip(asset_id="ast_000000000005")])) == ["clips[0].asset_id"]  # не готов


def test_total_duration_limit():
    small = Settings(_env_file=None, max_total_duration_sec=10)
    raw = doc(clips=[clip(**{"in": 0, "out": 6}), clip(**{"in": 0, "out": 6})])
    with pytest.raises(ProjectInvalid) as e:
        validate_doc(raw, assets=ASSETS, settings=small)
    assert e.value.errors[0]["field"] == "clips"


def test_output_rules():
    out = validate_doc(doc(output={"aspect": "9:16", "fit": "crop", "fps": 50}), assets=ASSETS, settings=S)
    assert out["output"] == {"aspect": "9:16", "fit": "crop", "fps": 50}
    assert errors_of(doc(output={"aspect": "4:3"})) == ["output.aspect"]
    assert errors_of(doc(output={"fit": "stretch"})) == ["output.fit"]
    assert errors_of(doc(output={"fps": 24})) == ["output.fps"]


def test_music_rules():
    out = validate_doc(
        doc(music={"asset_id": "ast_000000000003", "volume": 0.25, "fade_in": 1, "fade_out": 2}),
        assets=ASSETS, settings=S,
    )
    assert out["music"] == {
        "asset_id": "ast_000000000003", "volume": 0.25, "fade_in": 1.0, "fade_out": 2.0, "loop": True,
        "duck": False, "speech_volume": 1.0,
    }
    assert errors_of(doc(music={"asset_id": "ast_000000000004"})) == ["music.asset_id"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "volume": 2})) == ["music.volume"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "fade_in": -1})) == ["music.fade_in"]


def test_music_duck_and_speech_volume_defaults():
    """Нет ключей — старый проект звучит как сейчас: без дакинга, речь на 1."""
    out = validate_doc(
        doc(music={"asset_id": "ast_000000000003"}),
        assets=ASSETS, settings=S,
    )
    assert out["music"]["duck"] is False
    assert out["music"]["speech_volume"] == 1.0


def test_music_duck_and_speech_volume_are_kept():
    out = validate_doc(
        doc(music={"asset_id": "ast_000000000003", "duck": True, "speech_volume": 0.4}),
        assets=ASSETS, settings=S,
    )
    assert out["music"]["duck"] is True
    assert out["music"]["speech_volume"] == 0.4


def test_music_speech_volume_is_rounded_and_bounded():
    out = validate_doc(
        doc(music={"asset_id": "ast_000000000003", "speech_volume": 0.1234}),
        assets=ASSETS, settings=S,
    )
    assert out["music"]["speech_volume"] == 0.123
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "speech_volume": 1.1})) == [
        "music.speech_volume"
    ]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "speech_volume": -0.01})) == [
        "music.speech_volume"
    ]


def test_music_duck_must_be_bool():
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "duck": "да"})) == ["music.duck"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "duck": 1})) == ["music.duck"]


def test_clip_volume_defaults_to_one_and_always_present():
    out = validate_doc(doc(clips=[clip(), clip(**{"in": 10, "out": 12})]), assets=ASSETS, settings=S)
    assert [c["volume"] for c in out["clips"]] == [1.0, 1.0]


def test_clip_volume_is_rounded_and_bounded():
    out = validate_doc(doc(clips=[clip(volume=1.2344)]), assets=ASSETS, settings=S)
    assert out["clips"][0]["volume"] == 1.234
    loud = validate_doc(doc(clips=[clip(volume=2)]), assets=ASSETS, settings=S)
    assert loud["clips"][0]["volume"] == 2.0
    silent = validate_doc(doc(clips=[clip(volume=0)]), assets=ASSETS, settings=S)
    assert silent["clips"][0]["volume"] == 0.0
    assert errors_of(doc(clips=[clip(volume=2.001)])) == ["clips[0].volume"]
    assert errors_of(doc(clips=[clip(volume=-0.1)])) == ["clips[0].volume"]
    assert errors_of(doc(clips=[clip(volume="громко")])) == ["clips[0].volume"]


def test_clip_volume_garbage_is_not_kept():
    out = validate_doc(doc(clips=[clip(volume=0.5, gain=9)]), assets=ASSETS, settings=S)
    assert out["clips"][0]["volume"] == 0.5
    assert "gain" not in out["clips"][0]


def test_music_unknown_keys_are_dropped():
    out = validate_doc(
        doc(music={"asset_id": "ast_000000000003", "duck": True, "sidechain": 0.05}),
        assets=ASSETS, settings=S,
    )
    assert "sidechain" not in out["music"]
    assert out["music"]["duck"] is True


def test_subtitles_rules():
    out = validate_doc(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "soft"}),
        assets=ASSETS, settings=S,
    )
    assert out["subtitles"] == {
        "source": "file", "asset_id": "ast_000000000004", "mode": "soft",
        "style": "default", "enabled": True,
    }
    assert errors_of(doc(subtitles={"source": "file", "asset_id": "ast_000000000001"})) == [
        "subtitles.asset_id"
    ]
    assert errors_of(doc(subtitles={"source": "transcript", "asset_id": "ast_000000000004"})) == [
        "subtitles.asset_id"
    ]
    assert errors_of(doc(subtitles={"source": "guess", "asset_id": "ast_000000000004"})) == [
        "subtitles.source"
    ]
    assert errors_of(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "glow"})
    ) == ["subtitles.mode"]


def test_missing_enabled_means_on():
    """Старый проект без ключа не должен молча потерять субтитры в ролике."""
    out = validate_doc(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "soft"}),
        assets=ASSETS, settings=S,
    )
    assert out["subtitles"]["enabled"] is True


def test_enabled_false_is_kept():
    out = validate_doc(subs_doc(enabled=False), assets=ASSETS, settings=S)
    assert out["subtitles"]["enabled"] is False
    assert out["subtitles"]["cues"][0]["text"] == "Привет"


def test_enabled_must_be_bool():
    assert errors_of(subs_doc(enabled="false")) == ["subtitles.enabled"]
    assert errors_of(subs_doc(enabled=1)) == ["subtitles.enabled"]


def subs_doc(**over) -> dict:
    """Документ с репликами в субтитрах: источник cues ассета не просит."""
    return doc(subtitles={
        "source": "cues", "cues": [{"start": 0.0, "end": 2.0, "text": "Привет"}], **over,
    })


def test_cues_source_needs_no_asset():
    """Реплики самодостаточны: расшифровка нужна была, чтобы их собрать, а не чтобы показать."""
    out = validate_doc(subs_doc(), assets=ASSETS, settings=S)
    assert out["subtitles"]["source"] == "cues"
    assert out["subtitles"]["cues"] == [{"start": 0.0, "end": 2.0, "text": "Привет"}]
    assert out["subtitles"]["asset_id"] is None
    assert out["subtitles"]["mode"] == "burn" and out["subtitles"]["style"] == "default"
    assert out["subtitles"]["enabled"] is True


def test_cues_are_sorted_by_start():
    raw = subs_doc(cues=[{"start": 5.0, "end": 6.0, "text": "два"},
                         {"start": 0.0, "end": 1.0, "text": "раз"}])
    out = validate_doc(raw, assets=ASSETS, settings=S)
    assert [c["text"] for c in out["subtitles"]["cues"]] == ["раз", "два"]


def test_overlapping_cues_are_refused():
    """Наложение — это два субтитра в кадре одновременно."""
    raw = subs_doc(cues=[{"start": 0.0, "end": 3.0, "text": "раз"},
                         {"start": 2.0, "end": 4.0, "text": "два"}])
    assert errors_of(raw) == ["subtitles.cues"]
    # Встык — не наложение: одна реплика сменяет другую.
    touching = subs_doc(cues=[{"start": 0.0, "end": 3.0, "text": "раз"},
                              {"start": 3.0, "end": 4.0, "text": "два"}])
    assert len(validate_doc(touching, assets=ASSETS, settings=S)["subtitles"]["cues"]) == 2


def test_cue_needs_text_and_positive_length():
    assert errors_of(subs_doc(cues=[{"start": 1.0, "end": 1.0, "text": "нет длины"}])) == [
        "subtitles.cues[0].end"
    ]
    assert errors_of(subs_doc(cues=[{"start": -1.0, "end": 1.0, "text": "до начала"}])) == [
        "subtitles.cues[0].start"
    ]
    for bad_text in ("   ", "я" * 201, "раз\nдва\nтри", 5, None):
        assert errors_of(subs_doc(cues=[{"start": 0.0, "end": 1.0, "text": bad_text}])) == [
            "subtitles.cues[0].text"
        ]


def test_cue_of_half_a_millisecond_is_refused():
    """Времена округляются до миллисекунд: реплика короче не покажется в кадре вовсе."""
    assert errors_of(subs_doc(cues=[{"start": 1.0, "end": 1.0004, "text": "мигом"}])) == [
        "subtitles.cues[0].end"
    ]


def test_cue_text_and_times_are_normalized():
    raw = subs_doc(cues=[{"start": 0.00049, "end": 2.66666, "text": " раз\r\nдва "}])
    out = validate_doc(raw, assets=ASSETS, settings=S)
    assert out["subtitles"]["cues"][0] == {"start": 0.0, "end": 2.667, "text": "раз\nдва"}


def test_cue_list_bounds():
    assert errors_of(subs_doc(cues=[])) == ["subtitles.cues"]
    assert errors_of(subs_doc(cues="раз")) == ["subtitles.cues"]
    assert errors_of(subs_doc(cues=["раз"])) == ["subtitles.cues[0]"]
    many = [{"start": i * 2.0, "end": i * 2.0 + 1.0, "text": "а"} for i in range(S.max_cues + 1)]
    assert errors_of(subs_doc(cues=many)) == ["subtitles.cues"]


def test_every_bad_cue_is_reported_not_just_the_first():
    """Карточки правит человек: он должен увидеть все испорченные разом, а не по одной."""
    raw = subs_doc(cues=[{"start": 0.0, "end": 1.0, "text": ""},
                         {"start": 2.0, "end": 1.0, "text": "назад"}])
    assert errors_of(raw) == ["subtitles.cues[0].text", "subtitles.cues[1].end"]


def test_cues_of_other_sources_are_not_kept():
    """У file и transcript реплик нет: лишнее поле не должно доехать до рендера."""
    for source, asset in (("transcript", "ast_000000000001"), ("file", "ast_000000000004")):
        raw = doc(subtitles={"source": source, "asset_id": asset,
                             "cues": [{"start": 0, "end": 1, "text": "х"}]})
        out = validate_doc(raw, assets=ASSETS, settings=S)
        assert "cues" not in out["subtitles"] and out["subtitles"]["asset_id"] == asset


def test_cues_source_ignores_a_sent_asset():
    """Ассет источнику cues не нужен: присланный не должен удержать файл от уборки."""
    out = validate_doc(subs_doc(asset_id="ast_000000000001"), assets=ASSETS, settings=S)
    assert out["subtitles"]["asset_id"] is None


def test_wrong_shapes_do_not_crash():
    assert errors_of([]) == ["doc"]
    assert errors_of({"clips": "нет"}) == ["clips"]
    assert errors_of({"clips": ["строка"]}) == ["clips[0]"]
    assert errors_of(doc(output="широкий")) == ["output"]
    assert errors_of(doc(music=5)) == ["music"]
    assert errors_of(doc(clips=[clip(**{"in": "рано"})])) == ["clips[0].in"]


def test_all_errors_are_collected_not_just_the_first():
    raw = {
        "clips": [clip(**{"in": -1, "out": 500}), clip(asset_id="ast_00000000dead")],
        "output": {"fps": 24},
    }
    fields = errors_of(raw)
    assert "clips[0].in" in fields and "clips[0].out" in fields
    assert "clips[1].asset_id" in fields and "output.fps" in fields


def test_unknown_keys_are_dropped_not_echoed():
    out = validate_doc(doc(clips=[clip(evil="<script>")], extra=1), assets=ASSETS, settings=S)
    assert "extra" not in out and "evil" not in out["clips"][0]
    assert set(out) == {"output", "clips", "sounds", "music", "subtitles"}


def test_not_a_number_times_are_rejected():
    """NaN и бесконечность прошли бы все сравнения границ и испортили бы хранимый JSON."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert errors_of(doc(clips=[clip(**{"in": bad})])) == ["clips[0].in"]
        assert errors_of(doc(clips=[clip(out=bad)])) == ["clips[0].out"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "volume": float("nan")})) == ["music.volume"]


def test_clip_id_length_is_capped():
    assert errors_of(doc(clips=[clip(id="и" * 65)])) == ["clips[0].id"]
    out = validate_doc(doc(clips=[clip(id="и" * 64)]), assets=ASSETS, settings=S)
    assert out["clips"][0]["id"] == "и" * 64


def test_transition_into_second_clip_is_kept():
    out = validate_doc(
        doc(clips=[
            clip(id="a"),
            clip(id="b", **{"in": 10, "out": 12, "transition": {"kind": "fade", "duration": 0.5}}),
        ]),
        assets=ASSETS, settings=S,
    )
    assert "transition" not in out["clips"][0]
    assert out["clips"][1]["transition"] == {"kind": "fade", "duration": 0.5}
    assert out["clips"][0]["volume"] == 1.0 and out["clips"][1]["volume"] == 1.0


def test_zero_transition_is_a_cut():
    out = validate_doc(
        doc(clips=[
            clip(id="a"),
            clip(id="b", **{"in": 10, "out": 12, "transition": {"kind": "fade", "duration": 0}}),
        ]),
        assets=ASSETS, settings=S,
    )
    assert "transition" not in out["clips"][1]


def test_first_clip_transition_is_stripped():
    """Переход «в» первый клип не из чего делать — как присланные флаги подтверждения."""
    out = validate_doc(
        doc(clips=[clip(transition={"kind": "fade", "duration": 0.5})]),
        assets=ASSETS, settings=S,
    )
    assert "transition" not in out["clips"][0]


def test_transition_must_be_shorter_than_both_clips():
    assert errors_of(doc(clips=[
        clip(id="a", **{"in": 0, "out": 1}),
        clip(id="b", **{"in": 10, "out": 12, "transition": {"kind": "fade", "duration": 1}}),
    ])) == ["clips[1].transition.duration"]
    assert errors_of(doc(clips=[
        clip(id="a"),
        clip(id="b", **{"in": 10, "out": 12, "transition": {"kind": "wipe", "duration": 0.3}}),
    ])) == ["clips[1].transition.kind"]
    assert errors_of(doc(clips=[
        clip(id="a"),
        clip(id="b", **{"in": 10, "out": 12, "transition": {"kind": "fade", "duration": -1}}),
    ])) == ["clips[1].transition.duration"]


def test_transition_does_not_drop_music_fields_from_a():
    out = validate_doc(
        doc(
            clips=[
                clip(id="a", volume=0.8),
                clip(id="b", **{"in": 10, "out": 12, "volume": 1.2,
                                "transition": {"kind": "fade", "duration": 0.4}}),
            ],
            music={"asset_id": "ast_000000000003", "duck": True, "speech_volume": 0.6, "volume": 0.2},
        ),
        assets=ASSETS, settings=S,
    )
    assert out["music"]["duck"] is True
    assert out["music"]["speech_volume"] == 0.6
    assert out["music"]["volume"] == 0.2
    assert out["clips"][0]["volume"] == 0.8
    assert out["clips"][1]["volume"] == 1.2
    assert out["clips"][1]["transition"]["duration"] == 0.4


def _clip(cid: str, start: float, end: float, fade: float | None = None) -> dict:
    clip = {"id": cid, "asset_id": "ast_000000000001", "in": start, "out": end}
    if fade is not None:
        clip["transition"] = {"kind": "fade", "duration": fade}
    return clip


def test_clamp_fades_shrinks_a_transition_that_stopped_fitting():
    """Подтяжка к паузам укорачивает клип уже после проверки — переход обязан ужаться следом.

    Иначе xfade получает отрицательный offset. ffmpeg на это не ругается: он молча выбрасывает
    клип из ролика, и человек забирает файл без начала. Сам документ при этом перестаёт проходить
    проверку сервера, то есть следующее сохранение падало бы в 422.
    """
    clips = [_clip("c1", 0.0, 0.34), _clip("c2", 0.0, 2.0, 0.9)]
    clamp_fades(clips)
    assert clips[1]["transition"]["duration"] == 0.29


def test_clamp_fades_drops_a_transition_that_cannot_fit_at_all():
    clips = [_clip("c1", 0.0, 0.04), _clip("c2", 0.0, 2.0, 0.9)]
    clamp_fades(clips)
    assert "transition" not in clips[1]


def test_clamp_fades_leaves_a_transition_that_fits():
    clips = [_clip("c1", 0.0, 5.0), _clip("c2", 0.0, 5.0, 0.5)]
    clamp_fades(clips)
    assert clips[1]["transition"]["duration"] == 0.5


def test_clamp_fades_keeps_two_transitions_off_one_another():
    """Клип между двумя переходами: второй не должен начинаться, пока идёт первый, иначе
    собственных кадров у клипа в ролике не остаётся вовсе."""
    clips = [_clip("c1", 0.0, 3.0), _clip("c2", 0.0, 1.0, 0.9), _clip("c3", 0.0, 3.0, 0.9)]
    clamp_fades(clips)
    assert clips[1]["transition"]["duration"] == 0.9
    assert clips[2]["transition"]["duration"] == 0.05


def test_clamped_document_passes_validation_again():
    """Сохранённый документ обязан проходить собственную проверку сервера: иначе клиент,
    загрузив проект как есть, не может его сохранить обратно."""
    clips = [_clip("c1", 0.0, 0.34), _clip("c2", 0.0, 2.0, 0.9)]
    clamp_fades(clips)
    doc = {"output": {"aspect": "16:9", "fit": "pad", "fps": 30}, "clips": clips}
    validate_doc(doc, assets=ASSETS, settings=S)



PIC = "ast_000000000006"


def test_картинка_ложится_в_клип_несмотря_на_отсутствие_длительности():
    """У картинки нет своей длительности, и это не повод её отвергать: сколько она висит в
    кадре, решают при добавлении. Раньше проверка требовала длительность от любого ассета."""
    out = validate_doc(
        doc(clips=[clip(asset_id=PIC, **{"in": 0.0, "out": 5.0})]), assets=ASSETS, settings=S
    )
    assert out["clips"][0]["out"] == 5.0


def test_картинку_нельзя_растянуть_дольше_предела():
    """Без предела один кадр занял бы весь допустимый ролик, и кодировался бы он часами."""
    long = S.max_still_sec + 1
    assert errors_of(doc(clips=[clip(asset_id=PIC, **{"in": 0.0, "out": long})])) == ["clips[0].out"]
    ok = validate_doc(
        doc(clips=[clip(asset_id=PIC, **{"in": 0.0, "out": float(S.max_still_sec)})]),
        assets=ASSETS, settings=S,
    )
    assert ok["clips"][0]["out"] == float(S.max_still_sec)


def test_звук_и_субтитры_в_клип_по_прежнему_не_идут():
    assert errors_of(doc(clips=[clip(asset_id="ast_000000000003")])) == ["clips[0].asset_id"]
    assert errors_of(doc(clips=[clip(asset_id="ast_000000000004")])) == ["clips[0].asset_id"]


SND = "ast_000000000003"


def sound(**over) -> dict:
    return {"asset_id": SND, "at": 2.0, "in": 0.0, "out": 3.0, **over}


def test_звук_ложится_на_свою_дорожку_со_своим_временем():
    """У звука своё место на шкале — at: он идёт поверх речи клипов, а не в их очередь."""
    out = validate_doc(doc(sounds=[sound()]), assets=ASSETS, settings=S)
    assert out["sounds"] == [
        {"id": "s1", "asset_id": SND, "at": 2.0, "in": 0.0, "out": 3.0, "volume": 1.0}
    ]


def test_без_звуков_дорожка_пустая_а_не_пропавшая():
    # Старые документы приходят без ключа вовсе: читать их надо так же, как пустую дорожку.
    assert validate_doc(doc(), assets=ASSETS, settings=S)["sounds"] == []


def test_звук_проверяется_по_границам_виду_и_громкости():
    assert errors_of(doc(sounds=[sound(at=-1)])) == ["sounds[0].at"]
    assert errors_of(doc(sounds=[sound(out=500.0)])) == ["sounds[0].out"]  # запись длиной 200 с
    assert errors_of(doc(sounds=[sound(volume=3)])) == ["sounds[0].volume"]
    assert errors_of(doc(sounds=[sound(asset_id="ast_000000000004")])) == ["sounds[0].asset_id"]
    assert errors_of(doc(sounds=[sound(asset_id=PIC)])) == ["sounds[0].asset_id"]
    assert errors_of(doc(sounds="звук")) == ["sounds"]


def test_id_звука_не_совпадает_ни_с_клипом_ни_с_другим_звуком():
    """Выделение на шкале одно на клипы и звуки: общий id сделал бы его неоднозначным."""
    assert errors_of(doc(sounds=[sound(id="c1")])) == ["sounds[0].id"]
    assert errors_of(doc(sounds=[sound(id="x"), sound(id="x")])) == ["sounds[1].id"]


def test_звук_может_свисать_за_конец_ролика():
    """Ролик тут четыре секунды, звук лежит с сотой. Документ годен — лишнее обрежет сборка,
    иначе укоротить видео под уже положенной озвучкой было бы нельзя без её удаления."""
    out = validate_doc(
        doc(sounds=[sound(at=100.0, **{"in": 0.0, "out": 150.0})]), assets=ASSETS, settings=S
    )
    assert out["sounds"][0]["at"] == 100.0
