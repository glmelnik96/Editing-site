from pathlib import Path

import pytest

from server.app.config import Settings
from server.media.render import (
    RenderInvalid,
    SourceInfo,
    build_render_command,
    escape_for_filter,
    output_size,
    total_duration,
)

S = Settings(_env_file=None)
SOURCES = {
    "ast_1": SourceInfo(path="/d/u/assets/ast_1/source.mp4", duration=120.0, has_audio=True),
    "ast_2": SourceInfo(path="/d/u/assets/ast_2/source.mp4", duration=60.0, has_audio=False),
    "ast_m": SourceInfo(path="/d/u/assets/ast_m/source.mp3", duration=200.0, has_audio=True),
    "ast_s": SourceInfo(path="/d/u/assets/ast_s/subs.vtt", duration=0.0, has_audio=False),
}


def clip(asset="ast_1", start=1.0, end=5.0, **over):
    return {"id": "c1", "asset_id": asset, "in": start, "out": end,
            "snap_to_pauses": False, "in_verified": False, "out_verified": False, **over}


def doc(**over):
    base = {"output": {"aspect": "16:9", "fit": "pad", "fps": 30},
            "clips": [clip()], "music": None, "subtitles": None}
    return {**base, **over}


def build(document=None, quality="draft", out="/d/out.mp4.part"):
    return build_render_command(document or doc(), sources=SOURCES, quality=quality,
                                settings=S, out_path=out)


def joined(args):
    return " ".join(args)


def filter_of(args):
    return args[args.index("-filter_complex") + 1]


class TestОбщее:
    def test_разрешение_из_пропорции_и_качества(self):
        assert output_size("16:9", 720) == (1280, 720)
        assert output_size("16:9", 1080) == (1920, 1080)
        assert output_size("9:16", 720) == (720, 1280)
        assert output_size("1:1", 1080) == (1080, 1080)

    def test_ширина_и_высота_всегда_чётные(self):
        # Нечётный размер кадра ломает yuv420p: ffmpeg откажется кодировать.
        for aspect in ("16:9", "9:16", "1:1"):
            for side in (721, 1081, 999):
                width, height = output_size(aspect, side)
                assert width % 2 == 0 and height % 2 == 0

    def test_длительность_ролика_это_сумма_клипов(self):
        two = doc(clips=[clip(start=1, end=5), clip(start=10, end=12.5)])
        assert total_duration(two) == 6.5

    def test_экранирование_пути(self):
        assert escape_for_filter("/d/subs.vtt") == "/d/subs.vtt"
        assert escape_for_filter(r"C:\d\subs.vtt") == r"C\:/d/subs.vtt"
        assert escape_for_filter("/d/it's.vtt") == r"/d/it\'s.vtt"


class TestВходы:
    def test_каждый_клип_отдельным_входом_с_подрезкой_до_декодирования(self):
        args = build(doc(clips=[clip(start=1, end=5), clip(asset="ast_1", start=10, end=12)]))
        # -ss перед -i: поиск по смещению без декодирования всего файла
        assert args.count("-i") == 2
        first = args.index("-i")
        assert args[first - 4:first] == ["-ss", "1.0", "-t", "4.0"]
        second = args.index("-i", first + 1)
        assert args[second - 4:second] == ["-ss", "10.0", "-t", "2.0"]

    def test_один_ассет_в_двух_клипах_даёт_два_входа(self):
        args = build(doc(clips=[clip(start=0, end=2), clip(start=5, end=7)]))
        assert args.count(SOURCES["ast_1"].path) == 2

    def test_клип_без_звука_получает_тишину_той_же_длины(self):
        args = build(doc(clips=[clip(asset="ast_2", start=0, end=3)]))
        assert "anullsrc=channel_layout=stereo:sample_rate=48000" in joined(args)
        # Длина тишины ровно как у клипа, иначе склейка разъедется
        lavfi = args.index("lavfi")
        assert args[lavfi + 1:lavfi + 3] == ["-t", "3.0"]

    def test_ассета_нет_в_словаре(self):
        with pytest.raises(RenderInvalid) as e:
            build(doc(clips=[clip(asset="ast_пропал")]))
        assert "ast_пропал" in str(e.value)

    def test_пустой_список_клипов(self):
        with pytest.raises(RenderInvalid):
            build(doc(clips=[]))


class TestФильтры:
    def test_режим_pad_вписывает_кадр_с_полями(self):
        chain = filter_of(build(doc(output={"aspect": "16:9", "fit": "pad", "fps": 30})))
        assert "scale=1280:720:force_original_aspect_ratio=decrease" in chain
        assert "pad=1280:720:(ow-iw)/2:(oh-ih)/2" in chain

    def test_режим_crop_обрезает_по_центру(self):
        chain = filter_of(build(doc(output={"aspect": "16:9", "fit": "crop", "fps": 30})))
        assert "scale=1280:720:force_original_aspect_ratio=increase" in chain
        assert "crop=1280:720" in chain

    def test_каждый_сегмент_приводится_к_одной_частоте_кадров_и_пикселям(self):
        chain = filter_of(build(doc(output={"aspect": "16:9", "fit": "pad", "fps": 50})))
        assert "fps=50" in chain
        assert "setsar=1" in chain and "format=yuv420p" in chain

    def test_звук_каждого_сегмента_приводится_к_общему_виду(self):
        chain = filter_of(build())
        assert "aresample=48000" in chain
        assert "aformat=sample_fmts=fltp:channel_layouts=stereo" in chain

    def test_сегменты_сшиваются_одной_склейкой(self):
        chain = filter_of(build(doc(clips=[clip(), clip(start=10, end=12), clip(start=20, end=21)])))
        assert "concat=n=3:v=1:a=1" in chain


class TestМузыка:
    def music_doc(self, **over):
        return doc(clips=[clip(start=0, end=10)],
                   music={"asset_id": "ast_m", "volume": 0.25, "fade_in": 1.0,
                          "fade_out": 2.0, "loop": True, **over})

    def test_музыка_подмешивается_и_не_удлиняет_ролик(self):
        args = build(self.music_doc())
        chain = filter_of(args)
        assert "amix=inputs=2:duration=first:normalize=0" in chain
        assert "atrim=0:10.0" in chain

    def test_петля_включается_только_при_loop(self):
        assert "-stream_loop" in build(self.music_doc())
        assert "-stream_loop" not in build(self.music_doc(loop=False))

    def test_затухания_считаются_от_длины_ролика(self):
        chain = filter_of(build(self.music_doc()))
        assert "afade=t=in:st=0:d=1.0" in chain
        assert "afade=t=out:st=8.0:d=2.0" in chain  # 10 − 2

    def test_нулевые_затухания_не_добавляют_фильтров(self):
        chain = filter_of(build(self.music_doc(fade_in=0, fade_out=0)))
        assert "afade" not in chain

    def test_затухание_длиннее_ролика_обрезается(self):
        chain = filter_of(build(self.music_doc(fade_in=30, fade_out=30)))
        # Ролик 10 с: затухания не могут перекрыть друг друга и уйти в минус
        assert "afade=t=out:st=" in chain
        assert "st=-" not in chain

    def test_громкость_попадает_в_цепочку(self):
        assert "volume=0.25" in filter_of(build(self.music_doc()))


class TestСубтитры:
    def subs_doc(self, **over):
        return doc(subtitles={"source": "file", "asset_id": "ast_s", "mode": "burn",
                              "style": "default", **over})

    def test_вжигание_идёт_после_склейки(self):
        chain = filter_of(build(self.subs_doc()))
        assert "subtitles=" in chain
        assert chain.index("concat=") < chain.index("subtitles=")

    def test_мягкая_дорожка_отдельным_входом(self):
        args = build(self.subs_doc(mode="soft"))
        assert "mov_text" in args
        assert SOURCES["ast_s"].path in args
        assert "subtitles=" not in filter_of(args)

    def test_путь_субтитров_экранируется(self):
        sources = dict(SOURCES)
        sources["ast_s"] = SourceInfo(path="/d/it's:weird/subs.vtt", duration=0.0, has_audio=False)
        args = build_render_command(self.subs_doc(), sources=sources, quality="draft",
                                    settings=S, out_path="/d/o.mp4.part")
        chain = filter_of(args)
        assert r"\:" in chain and r"\'" in chain


class TestСубтитрыИзТранскрипта:
    """Файл собирает вызывающий: чистая функция на диск не ходит и написать его не может."""

    CACHE = Path("/d/u/projects/prj_1/subs/3.srt")

    def subs_doc(self, **over):
        return doc(subtitles={"source": "transcript", "asset_id": "ast_1", "mode": "burn",
                              "style": "default", **over})

    def build(self, document, path=CACHE):
        return build_render_command(document, sources=SOURCES, quality="draft", settings=S,
                                    out_path="/d/o.mp4.part", subtitles_path=path)

    def test_вжигается_переданный_файл(self):
        chain = filter_of(self.build(self.subs_doc()))
        assert "subs/3.srt" in chain
        assert chain.index("concat=") < chain.index("subtitles=")

    def test_мягкая_дорожка_из_того_же_файла(self):
        args = self.build(self.subs_doc(mode="soft"))
        assert "mov_text" in args and str(self.CACHE) in args
        assert "subtitles=" not in filter_of(args)

    def test_без_пути_это_ошибка_вызывающего(self):
        # Не «неподдержанный случай», а недоделка вызывающего — и сказать надо именно это.
        with pytest.raises(RenderInvalid) as e:
            self.build(self.subs_doc(), path=None)
        assert "subtitles_path" in str(e.value)

    def test_путь_кэша_экранируется_как_и_чужой(self):
        chain = filter_of(self.build(self.subs_doc(), path=Path(r"C:\d\it's\subs\3.srt")))
        assert r"\:" in chain and r"\'" in chain

    def test_вычитанные_реплики_идут_тем_же_путём(self):
        """У source=cues ассета нет вовсе: файл собран из документа и пришёл путём."""
        chain = filter_of(self.build(doc(subtitles={"source": "cues", "asset_id": None,
                                                    "mode": "burn", "style": "default",
                                                    "cues": [{"start": 0, "end": 1, "text": "х"}]})))
        assert "subs/3.srt" in chain

    def test_путь_не_подменяет_загруженный_файл(self):
        """У source=file субтитры лежат рядом с ассетом: путь кэша тут ни при чём."""
        args = self.build(doc(subtitles={"source": "file", "asset_id": "ast_s", "mode": "soft",
                                         "style": "default"}))
        assert SOURCES["ast_s"].path in args and str(self.CACHE) not in args


class TestКодирование:
    def test_черновик_и_финал_отличаются_пресетом_и_качеством(self):
        draft = build(quality="draft")
        final = build(quality="final")
        assert draft[draft.index("-preset") + 1] == "ultrafast"
        assert draft[draft.index("-crf") + 1] == "26"
        assert draft[draft.index("-b:a") + 1] == "128k"
        assert final[final.index("-preset") + 1] == "veryfast"
        assert final[final.index("-crf") + 1] == "20"
        assert final[final.index("-b:a") + 1] == "160k"
        assert "1280:720" in filter_of(draft)
        assert "1920:1080" in filter_of(final)

    def test_неизвестное_качество(self):
        with pytest.raises(RenderInvalid):
            build(quality="ultra")

    def test_контейнер_задан_явно(self):
        # Временный файл называется .part, по такому имени ffmpeg контейнер не угадывает
        args = build(out="/d/rnd_1.mp4.part")
        assert args[args.index("-f") + 1] == "mp4"
        assert args[-1] == "/d/rnd_1.mp4.part"

    def test_прогресс_и_перезапись_включены(self):
        args = build()
        assert "-progress" in args and args[args.index("-progress") + 1] == "pipe:1"
        assert "-nostats" in args and "-y" in args
        assert "+faststart" in args

    def test_команда_начинается_с_ffmpeg_из_настроек(self):
        args = build()
        assert args[0] == S.ffmpeg_path
