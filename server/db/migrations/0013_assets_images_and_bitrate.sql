-- foreign_keys: off
-- Картинка — новый вид записи, а расширить CHECK в SQLite нельзя: таблицу пересоздаём.
--
-- На assets ссылаются transcripts и conversions с ON DELETE CASCADE, и DROP TABLE при включённых
-- внешних ключах сделал бы неявный DELETE FROM, уничтожив расшифровки и готовые конвертации.
-- Пометка в первой строке велит бегунку миграций выключить ключи на время скрипта; после COMMIT
-- он же проверяет, что висячих ссылок не осталось (server/db/migrate.py).
--
-- Заодно появляется bit_rate: он нужен рендеру, чтобы «высокое качество» означало «не хуже
-- исходника», и подписи «у ваших записей было столько-то». У старых записей он NULL — там
-- битрейт оценивается как размер, делённый на длительность, пока запись не переанализируют.
CREATE TABLE assets_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'audio', 'image', 'subtitle')),
    original_name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('uploaded', 'analyzing', 'ready', 'proxy_ready', 'failed')),
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    has_audio INTEGER CHECK (has_audio IN (0, 1)),
    video_codec TEXT,
    audio_codec TEXT,
    bit_rate INTEGER,
    error TEXT,
    created_at TEXT NOT NULL,
    last_access_at TEXT NOT NULL
);
INSERT INTO assets_new (
    id, user_id, kind, original_name, ext, size, status, duration, width, height, fps,
    has_audio, video_codec, audio_codec, bit_rate, error, created_at, last_access_at
)
SELECT
    id, user_id, kind, original_name, ext, size, status, duration, width, height, fps,
    has_audio, video_codec, audio_codec, NULL, error, created_at, last_access_at
FROM assets;
DROP TABLE assets;
ALTER TABLE assets_new RENAME TO assets;
CREATE INDEX assets_user_idx ON assets(user_id, created_at);
CREATE INDEX assets_last_access_idx ON assets(last_access_at);

-- Тот же CHECK стоит на незавершённых загрузках: без него картинку нельзя даже начать грузить.
-- У uploads тоже есть дети с каскадом (upload_chunks), и спасает та же пометка в первой строке:
-- недокачанные файлы переживают миграцию и докачиваются с места разрыва.
CREATE TABLE uploads_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'audio', 'image', 'subtitle')),
    chunk_size INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
INSERT INTO uploads_new (id, user_id, filename, size, kind, chunk_size, path, created_at, expires_at)
SELECT id, user_id, filename, size, kind, chunk_size, path, created_at, expires_at FROM uploads;
DROP TABLE uploads;
ALTER TABLE uploads_new RENAME TO uploads;
CREATE INDEX uploads_user_idx ON uploads(user_id);
CREATE INDEX uploads_expires_idx ON uploads(expires_at);
