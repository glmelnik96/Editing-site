import pytest

from server.app.config import Settings
from server.app.projects.doc import AssetInfo, ProjectInvalid, validate_doc

S = Settings(_env_file=None)
ASSETS = {
    "ast_000000000001": AssetInfo(kind="video", status="proxy_ready", duration=120.0),
    "ast_000000000002": AssetInfo(kind="video", status="ready", duration=60.0),
    "ast_000000000003": AssetInfo(kind="audio", status="proxy_ready", duration=200.0),
    "ast_000000000004": AssetInfo(kind="subtitle", status="ready", duration=None),
    "ast_000000000005": AssetInfo(kind="video", status="analyzing", duration=None),
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
    }
    assert errors_of(doc(music={"asset_id": "ast_000000000004"})) == ["music.asset_id"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "volume": 2})) == ["music.volume"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "fade_in": -1})) == ["music.fade_in"]


def test_subtitles_rules():
    out = validate_doc(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "soft"}),
        assets=ASSETS, settings=S,
    )
    assert out["subtitles"] == {
        "source": "file", "asset_id": "ast_000000000004", "mode": "soft", "style": "default",
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
    assert set(out) == {"output", "clips", "music", "subtitles"}


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
