ALTER TABLE users ADD COLUMN yandex_id TEXT;
CREATE UNIQUE INDEX users_yandex_id_idx ON users(yandex_id);
