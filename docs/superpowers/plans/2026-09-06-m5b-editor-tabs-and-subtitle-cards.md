# M5b. Редактор с вкладками и субтитры карточками — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** субтитры перестают появляться в ролике сами — человек нажимает «Собрать субтитры», видит реплики карточками, правит их и только потом накладывает на видео. Заодно редактор перестаёт показывать шесть панелей разом.

**Спека:** `docs/superpowers/specs/2026-09-06-ux-redesign-and-subtitle-review-design.md`, разделы 4.5 и 5.

---

## Что уже готово

| | |
|---|---|
| Реплики из расшифровки | `server/media/cues.py` (`build_cues`) и `server/media/timeline.py` (`words_through_clips`) |
| Кэш и рендер | `build_project_subtitles` в `server/app/projects/store.py`, ветки `burn` и `soft` в `server/media/render.py` |
| Экспорт | `server/media/subs.py` — `cues_to_srt`, `cues_to_vtt` |
| Панели редактора | `source.ts`, `transcript.ts`, `versions.ts`, `render.ts` — переезжают во вкладки как есть |
| Визуальная система | Токены, шрифты, классы — M5a |

## Решения

**Реплики живут в документе проекта.** `subtitles.source: "cues"` и список `cues`. Правки попадают под ту же версионность, что и монтаж: работают откат, точки сохранения и защита от одновременной правки. Сборка ролика перестаёт зависеть от того, жива ли ещё расшифровка.

**Прежние источники остаются.** `file` — загруженный SRT, `transcript` — автоматическая сборка без разбора: это путь агента, ему карточки показывать некому.

**Вкладка — не мастер.** Порядок вкладок подсказывает путь (материал → текст → субтитры → точки → сборка), но человек ходит по ним свободно: монтаж — не анкета.

---

### Task 1: Реплики в документе проекта

**Files:** `server/app/projects/doc.py`, `tests/test_project_doc.py`

- [ ] **Step 1: Тесты**

```python
def base_subs(**over):
    return {"source": "cues", "cues": [{"start": 0.0, "end": 2.0, "text": "Привет"}], **over}


def test_cues_source_needs_no_asset(assets, settings):
    """Реплики самодостаточны: расшифровка нужна была, чтобы их собрать, а не чтобы показать."""
    doc = validate_doc(with_subs(base_subs()), assets=assets, settings=settings)
    assert doc["subtitles"]["source"] == "cues"
    assert doc["subtitles"]["cues"][0]["text"] == "Привет"
    assert "asset_id" not in doc["subtitles"] or doc["subtitles"]["asset_id"] is None


def test_cues_are_sorted_and_do_not_overlap(assets, settings):
    """Наложение — это два субтитра в кадре одновременно."""
    bad = base_subs(cues=[{"start": 0.0, "end": 3.0, "text": "раз"},
                          {"start": 2.0, "end": 4.0, "text": "два"}])
    with pytest.raises(ProjectInvalid) as exc:
        validate_doc(with_subs(bad), assets=assets, settings=settings)
    assert any("subtitles.cues" in field for field, _ in exc.value.errors)


def test_cue_needs_text_and_positive_length(assets, settings):
    for cues in ([{"start": 1.0, "end": 1.0, "text": "нет длины"}],
                 [{"start": 0.0, "end": 1.0, "text": "   "}],
                 [{"start": 0.0, "end": 1.0, "text": "я" * 201}],
                 [{"start": 0.0, "end": 1.0, "text": "раз\nдва\nтри"}]):
        with pytest.raises(ProjectInvalid):
            validate_doc(with_subs(base_subs(cues=cues)), assets=assets, settings=settings)


def test_empty_cue_list_is_refused(assets, settings):
    with pytest.raises(ProjectInvalid):
        validate_doc(with_subs(base_subs(cues=[])), assets=assets, settings=settings)


def test_too_many_cues(assets, settings):
    many = [{"start": i * 2.0, "end": i * 2.0 + 1.0, "text": "а"} for i in range(2001)]
    with pytest.raises(ProjectInvalid):
        validate_doc(with_subs(base_subs(cues=many)), assets=assets, settings=settings)


def test_cues_of_other_sources_are_not_kept(assets, settings):
    """У file и transcript реплик нет: лишнее поле не должно доехать до рендера."""
    subs = {"source": "transcript", "asset_id": VIDEO, "cues": [{"start": 0, "end": 1, "text": "х"}]}
    doc = validate_doc(with_subs(subs), assets=assets, settings=settings)
    assert "cues" not in doc["subtitles"]
```

Вспомогательные `with_subs` и фикстуры — по образцу тех, что уже есть в файле.

- [ ] **Step 2: Реализация**

- `SUB_SOURCES` пополняется значением `"cues"`.
- Для `source == "cues"` ассет не требуется, а `cues` обязателен: список от 1 до `max_cues` (новая настройка, по умолчанию 2000).
- Реплика: `start` и `end` — числа как у клипов (конечные, неотрицательные, округление до миллисекунд), `end > start`; `text` — непустая строка после обрезки, не длиннее 200 знаков, не больше двух строк (`\n` не больше одного).
- Список сортируется по началу; наложение соседей — ошибка `subtitles.cues`.
- Для `file` и `transcript` поле `cues` отбрасывается, как и любые неизвестные поля.

```bash
git commit -m "feat(projects): reviewed subtitle cues live in the document"
```

---

### Task 2: Сборка реплик в документ

**Files:** `server/app/projects/routes.py`, `server/app/projects/store.py`, `tests/test_project_subtitles_api.py`

- [ ] **`POST /api/v1/projects/{id}/subtitles/generate`** `{asset_id}` → проект целиком (как `PUT`).

Что делает: берёт расшифровку указанного ассета, гонит слова через клипы (`words_through_clips`), режет `build_cues` (ширина по пропорции проекта), кладёт результат в документ как `subtitles: {source: "cues", cues: [...], mode, style}` и сохраняет проект **как обычную правку** — с ростом версии, чтобы работали откат и точки сохранения. `mode` берётся из тела (`burn` по умолчанию), `style` — `default`.

Отказы: `404` — чужой проект; `422 no_transcript` — у ассета нет расшифровки; `422 empty_project` — в проекте нет клипов; `422 no_cues` — расшифровка есть, но в выбранные куски не попало ни одного слова (честнее, чем положить пустой список и упасть позже); `409 version_conflict` — как у обычного сохранения.

- [ ] Тесты: успешная сборка кладёт реплики и растит версию; повторная сборка заменяет реплики; отсутствие расшифровки и пустой результат дают свои коды; чужой проект — 404; агентский токен работает.

```bash
git commit -m "feat(api): generate subtitle cues into the project document"
```

---

### Task 3: Рендер из реплик

**Files:** `server/app/projects/store.py`, `tests/test_project_subtitles.py`

- [ ] `build_project_subtitles` учится в `source == "cues"`: реплики берутся прямо из документа, расшифровка не читается вовсе. Кэш по версии остаётся (версия растёт с каждой правкой реплик, значит файл всегда свежий); проверка «расшифровка новее кэша» для этого источника не нужна.
- [ ] Тесты: ролик собирается из отредактированных реплик; правка текста реплики меняет файл; расшифровку можно удалить — сборка всё равно проходит.

```bash
git commit -m "feat(render): build subtitles from the reviewed cues"
```

---

### Task 4: Вкладки в редакторе

**Files:** `web/src/editor.ts`, `web/src/style.css`

- [ ] Левая колонка получает переключатель вкладок: `Исходник` · `Текст` · `Субтитры` · `Точки` · `Сборка`. Открыта одна, по умолчанию «Исходник». Панели монтируются лениво — при первом открытии вкладки — и не размонтируются при переключении: заново загружать транскрипт при каждом клике незачем.
- [ ] Вкладка помечается точкой, когда в ней появилась новость: готова расшифровка, собрался ролик.
- [ ] Собственная шапка редактора уезжает: название проекта, состояние сохранения и кнопка «Собрать» встают в один ряд над сценой. Шапка оболочки остаётся одна на всё приложение.
- [ ] Сцена и шкала на месте всегда, клавиши и перетаскивание не меняются.

```bash
git commit -m "feat(web): editor tabs instead of six panels at once"
```

---

### Task 5: Панель субтитров

**Files:** создать `web/src/subtitles.ts`; изменить `web/src/project.ts`, `web/src/editor.ts`, `web/src/style.css`

- [ ] **Нет расшифровки:** одна кнопка «Расшифровать» и строка о том, что это займёт несколько минут. Ход задания — как в панели сборки.
- [ ] **Расшифровка есть, реплик нет:** кнопка «Собрать субтитры».
- [ ] **Реплики есть:** карточки. В карточке — время (два поля таймкода), текст (поле в две строки, как ляжет в кадр), кнопки «Разрезать» и «Убрать». Правка уходит обычным сохранением проекта, то есть попадает под откат и точки сохранения.
- [ ] Сверху: сколько реплик, кнопка «Наложить на видео» с выбором «вжечь» или «отдельной дорожкой», кнопка «Собрать заново» (предупреждает, что правки пропадут).
- [ ] Клик по карточке перематывает плеер; карточка текущей реплики подсвечена.
- [ ] Реплика, у которой время выходит за пределы ролика или пересекает соседнюю, помечена `--warn` — это подсказка, а не запрет: сохранить такое всё равно не даст сервер, и его ошибка покажется рядом.

```bash
git commit -m "feat(web): subtitle cards — review before burning"
```

---

### Task 6: Документация, выкатка, живая проверка

- [ ] README: раздел про разбор субтитров человеком и про новые экраны.
- [ ] Прогон всего: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`.
- [ ] Живая проверка на стенде: путь целиком от двери до собранного ролика с субтитрами, которые человек поправил руками.
- [ ] Слияние M5a и M5b одним заходом, выкатка, проверка на боевом.

---

## Поправки по ходу выполнения

(заполняется по ходу)

## Вне рамок

Караоке-подсветка слова в кадре, стили субтитров кроме `default`, редактирование текста расшифровки (правится реплика), мобильная вёрстка редактора.
