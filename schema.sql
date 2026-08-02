-- Follower-Stats Discord-Bot: Tabellen fuer die Follower-Historie.
--
-- Muss NICHT manuell ausgefuehrt werden - der Bot legt diese Tabellen beim
-- ersten Verbindungsaufbau selbst an (CREATE TABLE IF NOT EXISTS in db.py).
-- Dieses Skript ist nur ein Vorab-Setup/Verifikations-Hilfsmittel, z. B. um
-- unabhaengig vom Bot zu pruefen, dass der DB-User Schreib-/Create-Rechte hat.
--
-- Ausfuehren auf dem Server:
--   mysql -h 127.0.0.1 -P 3306 -u viennastaterpfollower -p followerDB < schema.sql

CREATE TABLE IF NOT EXISTS instagram_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    count BIGINT NOT NULL,
    recorded_at BIGINT NOT NULL,
    KEY idx_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tiktok_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    count BIGINT NOT NULL,
    recorded_at BIGINT NOT NULL,
    KEY idx_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS youtube_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    count BIGINT NOT NULL,
    recorded_at BIGINT NOT NULL,
    KEY idx_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS twitch_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    count BIGINT NOT NULL,
    recorded_at BIGINT NOT NULL,
    KEY idx_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
