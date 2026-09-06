# M4b. Субтитры из транскрипта и панель транскрипта — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** проект с `subtitles.source: "transcript"` собирается с настоящими субтитрами, а в браузере рядом с исходником появляется панель транскрипта, по которой можно монтировать текстом.

**Архитектура:** слова транскрипта пересчитываются через клипы проекта в шкалу готового ролика, режутся на реплики по типографским правилам спеки §10.9 и кэшируются как `subs/{version}.srt` и `.vtt`. Рендер подставляет этот файл туда же, куда сейчас подставляет загруженный.

**Технологии:** чистые функции в `server/media/`, TypeScript без фреймворков в `web/src/`, pytest и vitest.

---

## Что уже готово

| | |
|---|---|
| Транскрипт | M4a: `transcript.json` рядом с ассетом, сегменты со словами (`{w, s, e, interpolated}`) и флагами подтверждения |
| SRT и VTT | `server/media/subs.py` — `to_srt`, `to_vtt` из транскрипта, без разбивки на строки |
| Рендер с субтитрами | `server/media/render.py` умеет `burn` и `soft` из файла-ассета; для `source: "transcript"` там сейчас `RenderInvalid("субтитры из транскрипта появятся в M4")` (строка 144) |
| Проверка документа | `server/app/projects/doc.py`, `_validate_subtitles` — для `source=transcript` требует видеоассет |
| Панель исходника | `web/src/source.ts` — плеер прокси, полоса файла, отметка куска, кнопка «Добавить в шкалу» |

## Правила разбиения

Спека §10.9, таблица Т1–Т8. Коротко: реплика не кончается точкой или запятой, но кончается вопросительным знаком; не начинается с брошенной запятой; не кончается предлогом или союзом, если дальше есть слова; строка внутри реплики тоже старается ими не кончаться; число не отрывается от слова; после открывающей кавычки не рвём; строки уравновешены; при равном балансе верхняя короче.

Ширина строки зависит от кадра: **42 знака для 16:9, 24 для 9:16, 32 для 1:1**. Не больше двух строк, не дольше 4 секунд.

Алгоритм перенесён из `C:\Users\Глеб\Documents\Extensions-LLM-Chat\lib\pure\subtitles.js` (`buildCues`, `wrapCueLines`, `_scoreSplit`), где обкатан на часовых подкастах. Оценка разбиения: висящий предлог на конце строки — штраф 100, разброс длин строк — штраф в знаках, «пирамида» — половина очка, чтобы решать только ничьи.

---

## Структура файлов

| Файл | За что отвечает |
|---|---|
| `server/media/cues.py` (создать) | Чистое: склеивание слов в реплики по правилам Т1–Т8 |
| `server/media/timeline.py` (создать) | Чистое: пересчёт слов транскрипта через клипы проекта в шкалу ролика |
| `server/app/projects/store.py` | Кэш `subs/{version}.srt|.vtt`: сборка, чтение, сброс при смене версии |
| `server/worker/handlers.py` | Рендер собирает файл субтитров перед вызовом ffmpeg |
| `server/media/render.py` | Ветка `source: "transcript"` перестаёт быть заглушкой |
| `server/app/projects/routes.py` | `GET /projects/{id}/subtitles?format=srt|vtt` |
| `web/src/transcript.ts` (создать) | Панель транскрипта: слова, клик, выделение |
| `web/src/editor.ts`, `web/src/style.css` | Место под панель и её монтирование |

---

### Task 1: Типографика реплик

**Files:** создать `server/media/cues.py`, `tests/test_media_cues.py`

- [ ] **Step 1: Тесты**

```python
import pytest

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


def test_edges_lose_a_comma_but_keep_a_question_mark():
    assert polish_edges(["Привет,"]) == ["Привет"]
    assert polish_edges(["Привет?"]) == ["Привет?"]
    assert polish_edges([",", "дальше"]) == ["", "дальше"]


def test_line_break_avoids_a_hanging_preposition():
    words = ["Мы", "поехали", "в", "большой", "старый", "дом"]
    assert wrap_lines(words, max_chars=20, max_lines=2) == "Мы поехали\nв большой старый дом"


def test_lines_are_balanced_and_the_top_one_is_shorter():
    words = ["раз", "два", "три", "четыре"]
    top, bottom = wrap_lines(words, max_chars=14, max_lines=2).split("\n")
    assert len(top) <= len(bottom)


def test_single_word_needs_no_break():
    assert wrap_lines(["слово"], max_chars=20, max_lines=2) == "слово"


def test_cue_does_not_end_on_a_conjunction():
    """Одинокое «и» на экране читатель прочитает дважды: слово уезжает в следующую реплику."""
    words = [w("Мы", 0.0, 0.4), w("пошли", 0.4, 1.0), w("и", 1.0, 1.2),
             w("увидели", 1.2, 2.0), w("дом", 2.0, 2.6)]
    cues = build_cues(words, max_chars=10, max_lines=1, max_dur=4.0)
    assert not cues[0]["text"].endswith("и")


def test_cue_is_split_by_duration():
    words = [w(f"с{i}", float(i), float(i) + 1.0) for i in range(8)]
    cues = build_cues(words, max_chars=100, max_lines=2, max_dur=3.0)
    assert len(cues) > 1
    assert all(cue["end"] - cue["start"] <= 3.0 + 1e-6 for cue in cues)


def test_cue_times_come_from_the_words():
    words = [w("раз", 1.0, 1.5), w("два", 1.5, 2.25)]
    cues = build_cues(words, max_chars=40, max_lines=2, max_dur=4.0)
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 2.25


def test_overlong_word_becomes_its_own_cue():
    """Резать слово переносом хуже, чем нарушить ширину."""
    words = [w("короткое", 0.0, 0.5), w("невероятноразмашистоедлинное", 0.5, 1.5)]
    cues = build_cues(words, max_chars=10, max_lines=2, max_dur=4.0)
    assert any(cue["text"] == "невероятноразмашистоедлинное" for cue in cues)


def test_empty_input_gives_no_cues():
    assert build_cues([], max_chars=42, max_lines=2, max_dur=4.0) == []


def test_words_survive_in_the_cue():
    """Слова остаются при реплике: по ним панель подсвечивает текущее и строится караоке."""
    words = [w("раз", 0.0, 0.5), w("два", 0.5, 1.0)]
    cue = build_cues(words, max_chars=40, max_lines=2, max_dur=4.0)[0]
    assert [x["w"] for x in cue["words"]] == ["раз", "два"]
```

- [ ] **Step 2: Прогон — падает**

Run: `uv run python -m pytest tests/test_media_cues.py -q`
Expected: FAIL, `ModuleNotFoundError: server.media.cues`

- [ ] **Step 3: Реализация**

Модуль чистый, без ввода-вывода. Публичное: `is_glue_word`, `is_bad_break`, `polish_edges`, `wrap_lines`, `build_cues`.

- Служебные слова (Т3, Т4): русские предлоги, союзы и частицы плюс английский минимум — список в `subtitles.js`, перенести целиком.
- `is_bad_break(word)`: слово, кончающееся знаком препинания, — естественный разрыв (`False`); голое число и слово, кончающееся открывающей кавычкой, скобкой или тире, — плохой разрыв (`True`); иначе — служебное ли оно.
- `polish_edges(words)`: с последнего слова снимаются `. , ; : —` (вопросительный, восклицательный и многоточие остаются), с первого — `. , ; :`. Возвращается список той же длины: слово, ставшее пустым, вызывающий выбрасывает вместе с его временем.
- `wrap_lines(words, max_chars, max_lines)`: перебрать все разбиения на 1..`max_lines` строк, отбросить те, где строка длиннее `max_chars`, оценить штрафом (плохой разрыв +100 за каждый, разброс длин +разница, «пирамида» +0.5 когда верхняя длиннее нижней), выбрать наименьший; при равенстве — меньше строк, затем разрыв ближе к середине. Если ни одно разбиение не влезло — жадный перенос, а если и он не смог, пополам.
- `build_cues(words, *, max_chars, max_lines, max_dur)`: жадно набирать слова, пока текст влезает в `max_chars × max_lines` и длительность не превысила `max_dur`; затем правило Т3 — пока последнее слово служебное и дальше есть слова, вернуть его назад (не больше двух раз); затем `polish_edges` и выброс опустевших слов вместе с их временами. Реплика: `{"start", "end", "text", "words"}`, времена из первого и последнего слова.

- [ ] **Step 4: Прогон и коммит**

```bash
git add server/media/cues.py tests/test_media_cues.py
git commit -m "feat(subs): cue typography — hanging words, balance, pyramid"
```

---

### Task 2: Пересчёт транскрипта через клипы

**Files:** создать `server/media/timeline.py`, `tests/test_media_timeline.py`

- [ ] **Step 1: Тесты**

```python
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
```

- [ ] **Step 2: Реализация**

```python
def words_through_clips(transcript: dict, clips: list[dict], *, asset_id: str) -> list[dict]:
    """Слова исходника в шкале готового ролика (спека §10.9).

    Слово с временем t внутри клипа k получает offset_k + (t − in_k). Клипы других ассетов слова
    не дают, но занимают место: смещение считается по всем клипам подряд, иначе субтитры уедут.
    """
```

Слова на стыках обрезаются по границам клипа; слово, не пересёкшееся ни с одним клипом, выбрасывается. Времена округляются до миллисекунд.

- [ ] **Step 3: Прогон и коммит**

```bash
git add server/media/timeline.py tests/test_media_timeline.py
git commit -m "feat(subs): recompute transcript words through the project clips"
```

---

### Task 3: Кэш субтитров и рендер

**Files:** `server/app/projects/store.py`, `server/worker/handlers.py`, `server/media/render.py`, `server/app/storage.py`, тесты

- [ ] **Step 1: Сборка и кэш**

`build_project_subtitles(conn, settings, project) -> Path | None`:

1. `doc["subtitles"]` с `source: "transcript"` — иначе `None`;
2. прочитать `transcript.json` ассета из `subtitles.asset_id`; нет — `MediaError` с внятным текстом («у файла нет расшифровки, закажите её»);
3. `words_through_clips` по клипам проекта;
4. ширина строки по `doc["output"]["aspect"]`: 42 для `16:9`, 24 для `9:16`, 32 для `1:1`;
5. `build_cues`;
6. записать `subs/{version}.srt` и `.vtt` в каталог проекта (`storage.subs_dir`), атомарно;
7. вернуть путь к `.srt`.

Кэш: файл с именем версии; при новой версии собирается заново, старые чистит janitor вместе с проектом. Повторный рендер той же версии файл переиспользует.

- [ ] **Step 2: Рендер**

В `server/media/render.py` убрать `RenderInvalid("субтитры из транскрипта появятся в M4")`. Сборщик команды получает путь к файлу субтитров отдельным аргументом (`subtitles_path`), а не догадывается о нём: чистая функция не должна ходить на диск. Ветки `burn` и `soft` работают одинаково для обоих источников.

Обработчик рендера зовёт `build_project_subtitles` до сборки команды и передаёт путь.

- [ ] **Step 3: Тесты**

- рендер проекта с `source: "transcript"` больше не отказывает; в команде виден файл субтитров;
- у ассета нет транскрипта → задание падает с внятной ошибкой, а не с непонятной ошибкой ffmpeg;
- смена версии проекта пересобирает файл (имя другое);
- интеграционный: настоящий ffmpeg, короткий проект, `burn` — ролик собрался; `soft` — в контейнере есть дорожка субтитров (`ffprobe`).

- [ ] **Step 4: Коммит**

```bash
git commit -m "feat(render): burn and soft subtitles from a transcript"
```

---

### Task 4: Ручка субтитров проекта

**Files:** `server/app/projects/routes.py`, `tests/test_projects_api.py`

- [ ] `GET /api/v1/projects/{id}/subtitles?format=srt|vtt` — собрать (или взять из кэша) и отдать `text/plain; charset=utf-8`.
- `422 no_transcript_subtitles`, если у проекта не задан `subtitles.source: "transcript"`; `422 no_transcript`, если у ассета нет расшифровки; чужой проект — `404`.
- Тесты: оба формата, оба отказа, чужой проект, агентский токен работает.

```bash
git commit -m "feat(api): project subtitles endpoint"
```

---

### Task 5: Панель транскрипта

**Files:** создать `web/src/transcript.ts`, изменить `web/src/editor.ts`, `web/src/style.css`

- [ ] Панель под панелью исходника, появляется, когда у выбранного файла есть расшифровка (`files.transcript` в карточке ассета).
- Показывает сегменты текстом; подозрительные (`suspect`) и неподтверждённые границы помечены — но ненавязчиво, подсказкой при наведении, а не пестротой.
- Клик по слову перематывает плеер исходника на его время.
- Выделение мышью от слова до слова показывает кнопку «Взять кусок»: она кладёт на шкалу клип с `snap_to_pauses: true` по границам крайних выделенных слов. Это монтаж по тексту, зеркальный тому, что делает агент через API.
- Кнопка «Расшифровать», если транскрипта ещё нет: ставит задание и показывает ход по `GET /api/v1/jobs/{id}`, как панель сборки; при `503` честно говорит, что расшифровка не настроена.
- Опрос прекращается на неустранимой ошибке и при уходе с экрана — как в `web/src/render.ts`, оттуда же взять `isRetryable`.

```bash
git commit -m "feat(web): transcript panel with text-driven editing"
```

---

### Task 6: Документация, выкатка, живая проверка

- [ ] README: раздел про субтитры из транскрипта и панель; в разделе M4a дописать, что теперь есть.
- [ ] Смоук `tools/agent_smoke.py`: после расшифровки собрать проект с `subtitles.source: "transcript"` и проверить, что рендер прошёл и субтитры в ролике есть.
- [ ] Слияние и выкатка (координатор).
- [ ] Живая проверка (координатор):
  1. Проект с субтитрами из транскрипта собирается, текст в кадре читается, реплики не длиннее двух строк.
  2. `soft`-режим даёт дорожку субтитров, которую видно в плеере.
  3. Панель: клик по слову перематывает, выделение кладёт клип, границы подтянуты к паузам.
  4. Смена версии проекта пересобирает субтитры, а не отдаёт старые.
  5. Замер влияния на эфир соседа во время сборки с вжиганием.

---

## Поправки по ходу выполнения

- **Спека дополнена до начала работы:** §10.9 получила таблицу Т1–Т8 и ширину строки, зависящую от кадра (42 / 24 / 32). Раньше там был один абзац, и «около 42 знаков» реализовали бы буквально во всех пропорциях.
- **Задача 1.** В переносимом коде плагина нашлись две ошибки: многоточие из трёх точек снималось как обычные точки (защищён был только символ `…`), а аварийное разбиение «пополам» могло вернуть две строки при `max_lines=1`. Обе починены. Предел перебора разбиений — 20 000 вариантов: стоимость растёт как (слов)^(строк−1), и опасен именно `max_lines`.
- **Задача 1, после ревью.** Условие конца реплики приведено к условию разрыва строки: голое число и открывающая кавычка на границе так же нехороши, как висящий предлог.
- **Задача 3.** Кэш субтитров пересобирается не только при новой версии документа, но и когда `transcript.json` новее кэша: версия следит за документом, а расшифровку могли заказать заново при той же версии.
- **Живая проверка на боевом 2026-09-06.** Сквозной сценарий с субтитрами из расшифровки прошёл целиком. Кадр из готового ролика снят и просмотрен: белый текст с подложкой внизу кадра, кириллица рисуется верно. Реплики собраны из пересчитанных через клипы слов — в ролик попали только слова выбранных кусков.
- **Известное свойство.** Реплика может оказаться длиннее предела в 4 секунды, если одно слово длится дольше: разрезать слово нечем. На синтетическом материале это видно (одно слово на 17 с), на речи слова короткие.
- **Замечено в сторону:** каталог проекта на диске не удаляет никто — ни `delete_project`, ни janitor. С M4b там копятся ещё и файлы субтитров по одному на версию. Заведена отдельная задача.

## Что остаётся за рамками

Караоке-подсветка слова в кадре, стили субтитров кроме `default`, фирменные шрифты, редактирование текста транскрипта.
