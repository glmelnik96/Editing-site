"""Кэш субтитров проекта: `subs/{version}.srt` и `.vtt` из расшифровки исходника."""
import json

import pytest

from server.app.config import Settings
from server.app.projects.store import (
    SubtitlesUnavailable,
    build_project_subtitles,
    create_project,
    save_project,
)
from server.app.storage import asset_dir, subs_dir, transcript_path
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate

USER = "usr_00000000000a"
ASSET = "ast_000000000001"
OTHER = "ast_000000000002"

# Фраза длиннее любой строки субтитра: только на такой видно, что ширина зависит от пропорции.
PHRASE = (
    "Мы поехали в большой старый дом на окраине города рано утром "
    "и долго стояли у ворот пока хозяин искал ключи от калитки"
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso())
    )
    for asset_id in (ASSET, OTHER):
        c.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
            "created_at, last_access_at) VALUES (?, ?, 'video', 'a', 'mp4', 1, 'ready', 120, ?, ?)",
            (asset_id, USER, now_iso(), now_iso()),
        )
        asset_dir(settings, USER, asset_id).mkdir(parents=True, exist_ok=True)
    yield c
    c.close()


def words(phrase=PHRASE, *, start=0.0, step=0.15):
    """Слова подряд по step секунд: вся фраза короче предела реплики в 4 секунды, поэтому
    реплики режет только ширина строки, а не время."""
    return [
        {"w": word, "s": round(start + i * step, 3), "e": round(start + (i + 1) * step, 3),
         "interpolated": True}
        for i, word in enumerate(phrase.split())
    ]


def add_transcript(settings, *, asset=ASSET, **over):
    path = transcript_path(settings, USER, asset)
    data = {
        "asset_id": asset, "duration": 120.0,
        "segments": [{"id": 1, "start": 0.0, "end": 3.3, "text": PHRASE, "words": words()}],
        **over,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def project(conn, settings, *, aspect="16:9", clips=None, subtitles="transcript", asset=ASSET):
    raw = {
        "output": {"aspect": aspect, "fit": "pad", "fps": 30},
        "clips": clips or [{"asset_id": ASSET, "in": 0.0, "out": 10.0}],
    }
    if subtitles:
        raw["subtitles"] = {"source": subtitles, "asset_id": asset, "mode": "burn",
                            "style": "default"}
    return create_project(conn, settings, USER, name="С субтитрами", raw_doc=raw)


def cue_lines(path):
    """Строки текста из SRT: без номеров, таймингов и пустых разделителей."""
    return [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line and "-->" not in line and not line.isdigit()
    ]


class TestКогдаСобиратьНечего:
    def test_проект_без_субтитров(self, conn, settings):
        assert build_project_subtitles(conn, settings, project(conn, settings, subtitles=None)) is None

    def test_субтитры_из_загруженного_файла_уже_лежат_на_диске(self, conn, settings):
        """source=file собирать не надо: файл-ассет ffmpeg возьмёт из каталога ассета."""
        subs = "ast_000000000003"
        conn.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
            "created_at, last_access_at) VALUES (?, ?, 'subtitle', 's.srt', 'srt', 1, 'ready', 0, ?, ?)",
            (subs, USER, now_iso(), now_iso()),
        )
        made = project(conn, settings, subtitles="file", asset=subs)
        assert build_project_subtitles(conn, settings, made) is None
        assert not subs_dir(settings, USER, made["id"]).exists()


class TestСборка:
    def test_рядом_ложатся_оба_формата_и_ни_одного_огрызка(self, conn, settings):
        add_transcript(settings)
        made = project(conn, settings)
        srt = build_project_subtitles(conn, settings, made)

        folder = subs_dir(settings, USER, made["id"])
        assert srt == folder / f"{made['version']}.srt"
        assert (folder / f"{made['version']}.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
        assert not list(folder.glob("*.part"))

    def test_реплики_идут_по_шкале_ролика_а_не_исходника(self, conn, settings):
        """Клип начинается с 2-й секунды исходника: субтитр обязан приехать к началу ролика."""
        add_transcript(settings)
        made = project(conn, settings, clips=[{"asset_id": ASSET, "in": 2.0, "out": 10.0}])
        text = build_project_subtitles(conn, settings, made).read_text(encoding="utf-8")
        # Седьмое слово звучит с 1.8 с исходника, то есть с нуля ролика.
        assert text.splitlines()[1].startswith("00:00:00,000")
        assert "Мы поехали" not in text  # первые слова вырезаны вместе с началом исходника

    def test_чужой_клип_сдвигает_субтитры(self, conn, settings):
        add_transcript(settings)
        made = project(conn, settings, clips=[
            {"asset_id": OTHER, "in": 0.0, "out": 5.0},
            {"asset_id": ASSET, "in": 0.0, "out": 5.0},
        ])
        text = build_project_subtitles(conn, settings, made).read_text(encoding="utf-8")
        assert text.splitlines()[1].startswith("00:00:05,000")

    def test_ширина_строки_зависит_от_пропорции(self, conn, settings):
        add_transcript(settings)
        wide = build_project_subtitles(conn, settings, project(conn, settings, aspect="16:9"))
        tall = build_project_subtitles(conn, settings, project(conn, settings, aspect="9:16"))
        square = build_project_subtitles(conn, settings, project(conn, settings, aspect="1:1"))

        longest = {name: max(len(line) for line in cue_lines(path))
                   for name, path in (("16:9", wide), ("9:16", tall), ("1:1", square))}
        assert longest["16:9"] <= 42 and longest["1:1"] <= 32 and longest["9:16"] <= 24
        # В вертикальном кадре строка обязана выйти короче: иначе текст уедет за край.
        assert longest["9:16"] < longest["1:1"] < longest["16:9"]
        # И та же фраза распадается на большее число реплик.
        assert len(cue_lines(tall)) > len(cue_lines(wide))

    def test_без_расшифровки_отказ_понятен_человеку(self, conn, settings):
        made = project(conn, settings)
        with pytest.raises(SubtitlesUnavailable) as e:
            build_project_subtitles(conn, settings, made)
        assert "расшифров" in str(e.value).lower()
        assert not subs_dir(settings, USER, made["id"]).exists()

    def test_испорченная_расшифровка_это_тот_же_отказ(self, conn, settings):
        """Обрезанный JSON — то же самое: расшифровки нет, и ffmpeg об этом знать не должен."""
        transcript_path(settings, USER, ASSET).write_text('{"segments": [', encoding="utf-8")
        with pytest.raises(SubtitlesUnavailable):
            build_project_subtitles(conn, settings, project(conn, settings))

    def test_расшифровка_без_слов_даёт_пустые_файлы_а_не_отказ(self, conn, settings):
        """Слов нет — показывать нечего, но ролик собрать можно: пустой файл ffmpeg переварит."""
        add_transcript(settings, segments=[{"id": 1, "start": 0.0, "end": 1.0, "text": "…"}])
        srt = build_project_subtitles(conn, settings, project(conn, settings))
        assert srt.read_text(encoding="utf-8") == ""


class TestКэш:
    def test_та_же_версия_переиспользует_файл(self, conn, settings):
        add_transcript(settings)
        made = project(conn, settings)
        srt = build_project_subtitles(conn, settings, made)
        # Якорь вместо содержимого: если файл пересобрали, он не переживёт второй вызов.
        srt.write_text("ЯКОРЬ", encoding="utf-8")
        assert build_project_subtitles(conn, settings, made) == srt
        assert srt.read_text(encoding="utf-8") == "ЯКОРЬ"

    def test_новая_версия_это_новое_имя(self, conn, settings):
        add_transcript(settings)
        made = project(conn, settings)
        first = build_project_subtitles(conn, settings, made)
        saved = save_project(
            conn, settings, USER, made["id"], name=made["name"],
            raw_doc={**made["doc"], "clips": [{"asset_id": ASSET, "in": 1.0, "out": 10.0}]},
            version=made["version"],
        )
        second = build_project_subtitles(conn, settings, saved)
        assert second != first and second.name == f"{saved['version']}.srt"
        # Старый файл остаётся: он уходит вместе с каталогом проекта, а не по одному.
        assert first.exists()
        assert second.read_text(encoding="utf-8") != first.read_text(encoding="utf-8")

    def test_половина_кэша_не_считается_кэшем(self, conn, settings):
        """Пропал .vtt — собираем оба заново: ручка субтитров просит любой из двух форматов."""
        add_transcript(settings)
        made = project(conn, settings)
        srt = build_project_subtitles(conn, settings, made)
        vtt = srt.with_suffix(".vtt")
        vtt.unlink()
        build_project_subtitles(conn, settings, made)
        assert vtt.exists()


EDITED_CUES = [{"start": 0.0, "end": 1.5, "text": "Правленый текст"},
               {"start": 1.5, "end": 3.0, "text": "и вторая\nреплика"}]


class TestРепликиИзДокумента:
    """source=cues: реплики уже вычитаны человеком и лежат в документе."""

    def with_cues(self, conn, settings, cues=None):
        raw = {
            "output": {"aspect": "16:9", "fit": "pad", "fps": 30},
            "clips": [{"asset_id": ASSET, "in": 0.0, "out": 10.0}],
            "subtitles": {"source": "cues", "mode": "burn", "style": "default",
                          "cues": cues or EDITED_CUES},
        }
        return create_project(conn, settings, USER, name="Вычитанные", raw_doc=raw)

    def test_ролик_собирается_из_вычитанных_реплик(self, conn, settings):
        made = self.with_cues(conn, settings)
        srt = build_project_subtitles(conn, settings, made)
        assert cue_lines(srt) == ["Правленый текст", "и вторая", "реплика"]
        assert srt.with_suffix(".vtt").read_text(encoding="utf-8").startswith("WEBVTT")

    def test_правка_текста_меняет_файл(self, conn, settings):
        """Человек правил реплику как раз потому, что расшифровка ошиблась: молча пересобрать
        её значит вернуть ошибку в кадр."""
        made = self.with_cues(conn, settings)
        build_project_subtitles(conn, settings, made)
        saved = save_project(
            conn, settings, USER, made["id"], name=made["name"], version=made["version"],
            raw_doc={**made["doc"], "subtitles": {**made["doc"]["subtitles"],
                                                  "cues": [{"start": 0.0, "end": 1.5,
                                                            "text": "Совсем другое"}]}},
        )
        again = build_project_subtitles(conn, settings, saved)
        assert cue_lines(again) == ["Совсем другое"]

    def test_расшифровка_не_нужна_вовсе(self, conn, settings):
        """Расшифровку можно удалить: реплики самодостаточны, и сборка ролика от неё не зависит."""
        add_transcript(settings)
        made = self.with_cues(conn, settings)
        transcript_path(settings, USER, ASSET).unlink()
        assert cue_lines(build_project_subtitles(conn, settings, made)) == [
            "Правленый текст", "и вторая", "реплика"
        ]

    def test_расшифровка_новее_кэша_ничего_не_меняет(self, conn, settings):
        """Версия следит за документом, а реплики в нём и лежат: пересобирать не с чего."""
        import os
        import time

        made = self.with_cues(conn, settings)
        srt = build_project_subtitles(conn, settings, made)
        path = add_transcript(settings)
        later = time.time() + 10
        os.utime(path, (later, later))

        srt.write_text("ЯКОРЬ", encoding="utf-8")  # якорь: пересобранный файл его не переживёт
        assert build_project_subtitles(conn, settings, made) == srt
        assert srt.read_text(encoding="utf-8") == "ЯКОРЬ"


class TestКэшИРасшифровка:
    def test_новая_расшифровка_пересобирает_субтитры(self, conn, settings):
        """Версия проекта следит за документом, но не за расшифровкой: её могли заказать заново
        при той же версии, и кэш отдал бы старый текст к новому звуку."""
        import os
        import time

        add_transcript(settings)
        made = project(conn, settings)
        srt = build_project_subtitles(conn, settings, made)
        assert "ключи" in " ".join(cue_lines(srt))

        path = add_transcript(settings, segments=[
            {"id": 1, "start": 0.0, "end": 1.0, "text": "Совсем другой текст",
             "words": words("Совсем другой текст")},
        ])
        # mtime на некоторых файловых системах идёт с крупным шагом: двигаем время явно,
        # иначе тест проверял бы удачу, а не правило.
        later = time.time() + 10
        os.utime(path, (later, later))

        again = build_project_subtitles(conn, settings, made)
        text = " ".join(cue_lines(again))
        assert "Совсем другой текст" in text and "ключи" not in text

    def test_та_же_расшифровка_кэш_не_трогает(self, conn, settings):
        add_transcript(settings)
        made = project(conn, settings)
        srt = build_project_subtitles(conn, settings, made)
        stamp = srt.stat().st_mtime_ns
        assert build_project_subtitles(conn, settings, made).stat().st_mtime_ns == stamp
