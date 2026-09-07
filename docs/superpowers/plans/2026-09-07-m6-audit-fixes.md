# M6. Починка по итогам аудита — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** перестать молча терять работу человека и закрыть жизненный цикл заданий. Новых возможностей веха не добавляет.

**Спека:** `docs/superpowers/specs/2026-09-07-audit-fixes-design.md`.

---

## Порядок

Сервер идёт первым: его задачи независимы друг от друга и от интерфейса. Интерфейс вторым, и внутри него сначала савер — от него зависят остальные панели. Документы и выкатка последними.

## Решения, принятые до начала

**Отмену анализа запрещаем, а не чиним.** Без анализа запись бесполезна: нет длительности, карт пауз и полоски кадров, в проект она не встанет. Кому нужно прервать — удаляет запись, и это уже отменяет задания правильно.

**Ответ сохранения не применяется поверх более новой правки.** Из него берётся только версия. Это чинит и мигающий устаревший документ, и половину потери текста в субтитрах; вторую половину чинит сравнение реплик перед перерисовкой.

**Документы догоняют код.** Вкладки, дверь и шаги кабинета в коде — выкаченные и одобренные решения; расходятся README и спека, править нужно их.

---

### Task 1: Удаление и завершение проекта отменяют его задания

**Files:** `server/app/projects/store.py`, `server/app/projects/routes.py`, `tests/test_project_store.py`, `tests/test_projects_api.py`

- [ ] **Step 1: Тесты**

```python
def test_delete_cancels_the_render_in_flight(conn, settings, project):
    """ffmpeg может кодировать ещё час: удалили проект — остановись."""
    job_id = enqueue_job(conn, user_id=USER, type_="render", target_id=project["id"])
    claim_job(conn, lane="cpu", pid=1)
    delete_project(conn, settings, USER, project["id"])
    assert job_status(conn, job_id) == "canceled"


def test_finish_cancels_the_render_in_flight(conn, settings, project):
    """Иначе у завершённого проекта появится ролик, который человек только что убрал."""
    job_id = enqueue_job(conn, user_id=USER, type_="render", target_id=project["id"])
    finish_project(conn, settings, USER, project["id"])
    assert job_status(conn, job_id) == "canceled"


def test_render_of_a_finished_project_is_refused(client, token, finished_project):
    r = client.post(f"/api/v1/projects/{finished_project}/render", json={"quality": "draft"},
                    headers=token)
    assert r.status_code == 422
    assert r.json()["error"] == "project_finished"
```

- [ ] **Step 2: Реализация**

- `delete_project` и `finish_project` вызывают `cancel_jobs_for_target(conn, project_id)` **внутри** той же транзакции, что меняет запись: отдельная транзакция оставила бы окно, в котором проекта уже нет, а задание ещё живо.
- `POST /projects/{id}/render` отказывает завершённому проекту: `422 project_finished`.

```bash
git commit -m "fix(projects): delete and finish stop the render in flight"
```

---

### Task 2: Отмену анализа запрещаем, зависшую запись подбирает janitor

**Files:** `server/app/renders/routes.py`, `server/janitor/rules.py`, `tests/test_renders_api.py`, `tests/test_janitor.py`

- [ ] **Step 1: Тесты**

```python
def test_analyze_cannot_be_canceled(client, token, analyze_job):
    """Без анализа запись бесполезна: отменять нечего, есть удаление записи."""
    r = client.post(f"/api/v1/jobs/{analyze_job}/cancel", headers=token)
    assert r.status_code == 422
    assert r.json()["error"] == "cannot_cancel"


def test_render_and_transcribe_are_still_cancelable(client, token, render_job, transcribe_job):
    for job_id in (render_job, transcribe_job):
        assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=token).status_code == 204


def test_janitor_frees_an_asset_stuck_in_analyzing(conn, settings):
    """Воркера убили на середине: живого задания нет, а запись висит и в проект не встанет."""
    asset_id = make_asset(conn, status="analyzing")
    collect(conn, settings)
    assert asset_status(conn, asset_id) == "failed"


def test_janitor_leaves_an_asset_whose_analyze_is_alive(conn, settings):
    asset_id = make_asset(conn, status="analyzing")
    enqueue_job(conn, user_id=USER, type_="analyze", target_id=asset_id)
    collect(conn, settings)
    assert asset_status(conn, asset_id) == "analyzing"
```

- [ ] **Step 2: Реализация**

- Маршрут отмены смотрит тип задания: `analyze` → `422 cannot_cancel`. Остальные как раньше.
- Правило janitor расширяется: запись в `analyzing`, у которой нет задания `analyze` в статусе `queued` или `running`, переводится в `failed` с причиной «анализ не завершился». Существующее правило про протухшее `running` остаётся — оно ловит другой случай и срабатывает раньше.

```bash
git commit -m "fix(jobs): analysis is not cancelable, janitor frees assets stuck in it"
```

---

### Task 3: Здоровье без пульса — degraded

**Files:** `server/app/health.py`, `tests/test_health.py`

- [ ] **Step 1: Тесты**

```python
def test_health_without_any_heartbeat_is_degraded(client, conn):
    """Воркер ни разу не стартовал: deploy ждёт status=ok и не должен его получить."""
    conn.execute("DELETE FROM heartbeats")
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["worker_seen_sec_ago"] is None


def test_health_disk_threshold_comes_from_settings(client, settings, monkeypatch):
    """Порог был зашитой десяткой и расходился с настройкой отказа в загрузке."""
```

- [ ] **Step 2: Реализация**

- Отсутствие записи пульса делает здоровье `degraded`: `worker_seen_sec_ago` остаётся `null`, признак «воркер жив» становится ложью.
- Порог свободного места берётся из `settings.disk_low_pct`, а не из константы в модуле.
- Проверить, что `deploy.sh` ждёт здоровья достаточно долго: воркер пишет пульс при старте, но между рестартом юнита и первой записью есть секунды.

```bash
git commit -m "fix(health): no worker heartbeat is degraded, not ok"
```

---

### Task 4: Удаление расшифровки не спорит с воркером

**Files:** `server/app/assets/routes.py`, `tests/test_transcript_api.py`

- [ ] **Step 1: Тест**

```python
def test_delete_is_refused_while_transcribing(client, token, asset_with_running_transcribe):
    """Для PUT это уже чинили: доехавший воркер молча вернёт удалённый файл."""
    r = client.delete(f"/api/v1/assets/{asset_with_running_transcribe}/transcript", headers=token)
    assert r.status_code == 409
    assert r.json()["error"] == "already_queued"
```

- [ ] **Step 2: Реализация**

`delete_transcript` вызывает `_refuse_while_transcribing`, как `transcribe` и `put_transcript`.

```bash
git commit -m "fix(transcript): delete waits for the running job like put does"
```

---

### Task 5: Тесты, которые ничего не доказывали

**Files:** `tests/test_worker_handlers.py`, `tests/test_worker_render.py`, `tests/test_worker_transcribe.py`

- [ ] Тест `test_analyze_of_a_missing_asset_is_not_an_error` получает утверждения: задание закончилось без ошибки, запись не появилась, задание `proxy` не поставлено.
- [ ] Тест отмены рендера проверяет **поведение**, а не наличие аргумента: после отмены переданный `should_stop()` возвращает истину.
- [ ] Новый тест: нехватка места при расшифровке даёт `disk_low`, по образцу `test_worker_render.py:212`.

```bash
git commit -m "test(worker): assertions where there were none"
```

---

### Task 6: Ответ сохранения не перетирает более новую правку

**Files:** `web/src/project.ts`, `web/src/editor.ts`, `web/src/project.test.ts`

- [ ] **Step 1: Тесты**

```ts
it('не отдаёт наверх ответ, пока в очереди лежит более новая правка', async () => {
  // Иначе на экране мигнёт документ, который человек уже успел изменить.
})

it('после отказа проверки состояние — failed, а не idle', async () => {
  // Зелёное «сохранено» на отклонённом документе — враньё: на сервере прежняя версия.
})
```

- [ ] **Step 2: Реализация**

- `run` зовёт `onSaved` только когда `queued` пуст. Иначе наверх уходит одна версия — очередь и так подставляет её в следующую отправку.
- `422` ставит `failed`, как сеть и `500`. Причина отказа показывается рядом со статусом и не гаснет по таймеру: сообщение об отклонённом документе живёт, пока документ не примут или человек не уйдёт с экрана.

```bash
git commit -m "fix(web): a stale save response no longer overwrites newer work"
```

---

### Task 7: Карточки реплик не пересобираются без причины

**Files:** `web/src/subtitles.ts`, `web/src/subtitles.test.ts`

- [ ] **Step 1: Тесты**

```ts
it('не перерисовывает карточки, когда изменились только клипы', () => {
  // Текст живёт в textarea до потери фокуса: перерисовка стирает набранное.
})

it('перерисовывает, когда реплики действительно изменились', () => {})
```

- [ ] **Step 2: Реализация**

`setProject` сравнивает реплики и режим с нарисованными и при совпадении не трогает DOM. Сравнение по значению: список короткий, а глубокое равенство здесь честнее ссылочного — документ приходит с сервера новым объектом каждый раз.

```bash
git commit -m "fix(web): typing in a cue card survives a save"
```

---

### Task 8: Действия в обход савера начинаются со сброса очереди

**Files:** `web/src/subtitles.ts`, `web/src/versions.ts`

- [ ] Сборка реплик, возврат к точке и снятие точки формой в панели вызывают `flush()` перед своим запросом — как это делают кнопка «Собрать» и снятие точки из шапки.
- [ ] Проверить руками: несохранённый монтаж не превращается в `409` «проект изменился в другом месте».

```bash
git commit -m "fix(web): server actions flush the pending edit first"
```

---

### Task 9: Записи в редакторе обновляются сами

**Files:** `web/src/editor.ts`, `web/src/subtitles.ts`, `web/src/transcript.ts`

- [ ] Редактор опрашивает `/assets`, пока среди них есть незавершённые по статусу обработки или по расшифровке, и раздаёт свежую карточку панелям. Опрос гаснет в `stop()`, как остальные.
- [ ] Панель субтитров обрабатывает `transcript_exists` так же, как панель транскрипта: это не ошибка, а «уже готово».
- [ ] Проверить: запись, отданную в монтаж сразу после загрузки, можно доиграть без перезахода в редактор.

```bash
git commit -m "fix(web): the editor notices when a record finishes processing"
```

---

### Task 10: Точка новостей на вкладке

**Files:** `web/src/editor.ts`

- [ ] Точка ставится, когда во вкладке появилась новость и вкладка закрыта: готовая расшифровка для «Транскрибации» и «Субтитров», собранный ролик для «Рендера». Снятие уже написано.

```bash
git commit -m "feat(web): a tab with news wears a dot, as the spec promised"
```

---

### Task 11: Документы и гигиена

**Files:** `README.md`, `docs/superpowers/specs/2026-09-06-ux-redesign-and-subtitle-review-design.md`, `.gitignore`

- [ ] README и спека переработки UX догоняют код: вкладки «Транскрибация» и «Рендер», текст и вёрстка двери, шаги кабинета вместо двух равных карточек.
- [ ] README получает строки про новое поведение: отмена заданий при удалении и завершении проекта, запрет отмены анализа, здоровье без пульса.
- [ ] `.gitignore` закрывает все виды `.env`, кроме примера.
- [ ] Устаревшая копия `.env.bak-20260906-121744` удаляется с ВМ.

```bash
git commit -m "docs: bring README and the UX spec back in line with the code"
```

---

### Task 12: Прогон, живая проверка, выкатка

- [ ] Прогон всего: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npx tsc --noEmit && npm run build`.
- [ ] Живая проверка на стенде:
  - набор текста в карточке реплики переживает сохранение соседней;
  - отклонённый документ виден как «не сохранено» с причиной;
  - удаление проекта со сборкой останавливает ffmpeg;
  - запись, отданная в монтаж сразу после загрузки, доигрывается без перезахода;
  - вкладка с готовой расшифровкой помечена точкой.
- [ ] Слияние в `main`, выкатка, проверка на боевом: `/healthz` отвечает `ok`, бандл содержит правки.

---

## Поправки по ходу выполнения

_Заполняется по ходу: что в плане оказалось неверным, что нашлось сверх него._

## Вне рамок

Мобильная вёрстка редактора, правка текста расшифровки, стили субтитров кроме `default`, караоке-подсветка. Обновление `vite` и `vitest` до текущих мажоров. Возможности из разбора ffmpeg — следующая веха.
