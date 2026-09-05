import json

from server.app.config import Settings
from server.app.projects.snap import load_silences, snap_clips, snap_in, snap_out
from server.app.storage import asset_dir

S = Settings(_env_file=None)
# Речь до 10.0, пауза 10.0–11.0, речь 11.0–20.0, пауза 20.0–20.4 (короткая), речь дальше.
PAUSES = [{"start": 10.0, "end": 11.0}, {"start": 20.0, "end": 20.4}]


def test_snap_in_moves_to_the_start_of_speech_with_a_buffer():
    """in подтягивается к концу паузы и отступает буфером внутрь паузы (раздел 10.6)."""
    assert snap_in(10.9, PAUSES, window=0.35, buffer=0.3) == 10.7
    assert snap_in(11.2, PAUSES, window=0.35, buffer=0.3) == 10.7


def test_snap_in_never_goes_past_the_middle_of_a_short_pause():
    assert snap_out(20.2, PAUSES, window=0.35, buffer=0.3) == 20.2  # середина паузы 20.0–20.4
    assert snap_in(20.3, PAUSES, window=0.35, buffer=0.3) == 20.2


def test_snap_out_moves_to_the_end_of_speech_with_a_buffer():
    assert snap_out(9.9, PAUSES, window=0.35, buffer=0.3) == 10.3
    assert snap_out(10.2, PAUSES, window=0.35, buffer=0.3) == 10.3


def test_no_pause_in_the_window_leaves_the_value_alone():
    assert snap_in(5.0, PAUSES, window=0.35, buffer=0.3) is None
    assert snap_out(15.0, PAUSES, window=0.35, buffer=0.3) is None
    assert snap_in(1.0, [], window=0.35, buffer=0.3) is None


def test_the_nearest_edge_wins():
    pauses = [{"start": 4.0, "end": 5.0}, {"start": 5.2, "end": 6.0}]
    assert snap_in(5.15, pauses, window=0.35, buffer=0.3) == 4.7  # конец 5.0 ближе, чем 6.0


def test_snap_clips_sets_flags_and_leaves_others_alone(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    folder = asset_dir(settings, "usr_00000000000a", "ast_000000000001")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text(
        json.dumps({"silences": [], "silences_dense": PAUSES}), encoding="utf-8"
    )
    clips = [
        {"id": "c1", "asset_id": "ast_000000000001", "in": 10.9, "out": 20.2,
         "snap_to_pauses": True, "in_verified": False, "out_verified": False},
        {"id": "c2", "asset_id": "ast_000000000001", "in": 10.9, "out": 20.2,
         "snap_to_pauses": False, "in_verified": False, "out_verified": False},
    ]
    snap_clips(clips, settings=settings, user_id="usr_00000000000a")
    assert clips[0]["in"] == 10.7 and clips[0]["in_verified"] is True
    assert clips[0]["out"] == 20.2 and clips[0]["out_verified"] is True
    assert clips[1]["in"] == 10.9 and clips[1]["in_verified"] is False


def test_snap_is_rolled_back_when_it_would_break_the_clip():
    """Подтяжка не имеет права сделать клип нулевым или перевёрнутым.

    Пауза короче удвоенного буфера (0.2 с < 0.6 с): и in, и out упираются в её середину
    10.1 и совпадают — без отката клип схлопнулся бы в ноль.
    """
    pauses = [{"start": 10.0, "end": 10.2}]
    clips = [{"id": "c1", "asset_id": "a", "in": 9.9, "out": 10.1,
              "snap_to_pauses": True, "in_verified": False, "out_verified": False}]
    snap_clips(clips, silences_by_asset={"a": pauses}, settings=S)
    assert clips[0]["in"] == 9.9 and clips[0]["out"] == 10.1
    assert clips[0]["in_verified"] is False and clips[0]["out_verified"] is False


def test_missing_analysis_file_is_not_an_error(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    clips = [{"id": "c1", "asset_id": "ast_000000000009", "in": 1.0, "out": 2.0,
              "snap_to_pauses": True, "in_verified": False, "out_verified": False}]
    snap_clips(clips, settings=settings, user_id="usr_00000000000a")
    assert clips[0]["in"] == 1.0 and clips[0]["in_verified"] is False


def test_broken_analysis_file_is_not_an_error(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    folder = asset_dir(settings, "usr_00000000000a", "ast_000000000001")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text("{не json", encoding="utf-8")
    assert load_silences(settings, "usr_00000000000a", "ast_000000000001") == []


def test_load_silences_prefers_the_dense_map(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    folder = asset_dir(settings, "usr_00000000000a", "ast_000000000001")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text(
        json.dumps({"silences": [{"start": 1, "end": 2}], "silences_dense": [{"start": 3, "end": 4}]}),
        encoding="utf-8",
    )
    assert load_silences(settings, "usr_00000000000a", "ast_000000000001") == [{"start": 3.0, "end": 4.0}]


def test_zero_window_disables_snapping():
    assert snap_in(11.0, PAUSES, window=0.0, buffer=0.3) == 10.7
    assert snap_in(11.01, PAUSES, window=0.0, buffer=0.3) is None
