import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from config import DATABASE_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE NOT NULL,
                username    TEXT,
                password    TEXT NOT NULL,
                is_active   INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now')),
                last_seen   TEXT
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id              INTEGER PRIMARY KEY,
                fixture_id      INTEGER NOT NULL,
                league_id       INTEGER NOT NULL,
                league_name     TEXT,
                home_team       TEXT NOT NULL,
                away_team       TEXT NOT NULL,
                match_datetime  TEXT NOT NULL,
                market          TEXT NOT NULL,
                prediction      TEXT NOT NULL,
                confidence      REAL NOT NULL,
                estimated_odds  REAL NOT NULL,
                stats_snapshot  TEXT,
                llm_analysis    TEXT,
                sent_at         TEXT,
                result          TEXT,
                outcome         TEXT,
                resolved_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS team_stats (
                id              INTEGER PRIMARY KEY,
                team_id         INTEGER NOT NULL,
                league_id       INTEGER NOT NULL,
                season          INTEGER NOT NULL,
                team_name       TEXT DEFAULT '',
                avg_corners_for     REAL,
                avg_corners_against REAL,
                avg_fh_corners_for  REAL,
                avg_fh_corners_against REAL,
                home_avg_corners    REAL,
                away_avg_corners    REAL,
                matches_played      INTEGER DEFAULT 0,
                extra_json      TEXT DEFAULT '{}',
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(team_id, league_id, season)
            );

            CREATE TABLE IF NOT EXISTS model_weights (
                id          INTEGER PRIMARY KEY,
                feature     TEXT UNIQUE NOT NULL,
                weight      REAL NOT NULL DEFAULT 1.0,
                correct     INTEGER DEFAULT 0,
                total       INTEGER DEFAULT 0,
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS accuracy_log (
                id          INTEGER PRIMARY KEY,
                date        TEXT NOT NULL,
                league_name TEXT,
                total       INTEGER DEFAULT 0,
                correct     INTEGER DEFAULT 0,
                accuracy    REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS live_watch (
                fixture_id          INTEGER PRIMARY KEY,
                league_id           INTEGER,
                league_name         TEXT,
                home_team           TEXT,
                away_team           TEXT,
                match_datetime      TEXT,
                pre_match_expected  REAL,
                last_alert_minute   INTEGER,
                alerts_sent         INTEGER DEFAULT 0,
                is_finished         INTEGER DEFAULT 0,
                added_at            TEXT DEFAULT (datetime('now'))
            );

        """)
        # Migrations: add columns that may not exist in older DBs
        for col, defn in [
            ("team_name", "TEXT DEFAULT ''"),
            ("extra_json", "TEXT DEFAULT '{}'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE team_stats ADD COLUMN {col} {defn}")
            except Exception:
                pass  # column already exists
        conn.executescript("""
            INSERT OR IGNORE INTO model_weights (feature, weight) VALUES
                ('avg_corners_for',    1.0),
                ('avg_corners_against',1.0),
                ('h2h_corners',        0.9),
                ('referee_corners',    0.7),
                ('form_last5',         1.0),
                ('live_corners',       1.5),
                ('live_shots',         1.2),
                ('live_crosses',       1.1),
                ('live_attacks',       1.0);
        """)


# ── Users ──────────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username: str, password: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users (telegram_id, username, password)
            VALUES (?, ?, ?)
        """, (telegram_id, username, password))


def activate_user(telegram_id: int) -> bool:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_active=1, last_seen=datetime('now') WHERE telegram_id=?",
            (telegram_id,)
        )
        row = conn.execute(
            "SELECT is_active FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        return bool(row and row["is_active"])


def get_user(telegram_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()


def get_active_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT telegram_id FROM users WHERE is_active=1"
        ).fetchall()


def touch_user(telegram_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_seen=datetime('now') WHERE telegram_id=?",
            (telegram_id,)
        )


# ── Predictions ────────────────────────────────────────────────────────────

def save_prediction(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT OR REPLACE INTO predictions
            (fixture_id, league_id, league_name, home_team, away_team,
             match_datetime, market, prediction, confidence, estimated_odds,
             stats_snapshot, llm_analysis, sent_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        """, (
            data["fixture_id"], data["league_id"], data["league_name"],
            data["home_team"], data["away_team"], data["match_datetime"],
            data["market"], data["prediction"],
            data["confidence"], data["estimated_odds"],
            json.dumps(data.get("stats_snapshot", {})),
            data.get("llm_analysis", ""),
        ))
        return cur.lastrowid


def get_pending_predictions():
    """Predictions sent but not yet resolved."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM predictions
            WHERE outcome IS NULL AND sent_at IS NOT NULL
        """).fetchall()


def resolve_prediction(prediction_id: int, result: str, outcome: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE predictions
            SET result=?, outcome=?, resolved_at=datetime('now')
            WHERE id=?
        """, (result, outcome, prediction_id))


def prediction_exists(fixture_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM predictions WHERE fixture_id=?", (fixture_id,)
        ).fetchone()
        return row is not None


# ── Team Stats ─────────────────────────────────────────────────────────────

def upsert_team_stats(data: dict):
    extra = {k: data[k] for k in data if k not in {
        "team_id", "league_id", "season", "team_name",
        "avg_corners_for", "avg_corners_against",
        "avg_fh_corners_for", "avg_fh_corners_against",
        "home_avg_corners", "away_avg_corners", "matches_played",
    }}
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO team_stats
            (team_id, league_id, season, team_name, avg_corners_for, avg_corners_against,
             avg_fh_corners_for, avg_fh_corners_against,
             home_avg_corners, away_avg_corners, matches_played, extra_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(team_id, league_id, season) DO UPDATE SET
                team_name=excluded.team_name,
                avg_corners_for=excluded.avg_corners_for,
                avg_corners_against=excluded.avg_corners_against,
                avg_fh_corners_for=excluded.avg_fh_corners_for,
                avg_fh_corners_against=excluded.avg_fh_corners_against,
                home_avg_corners=excluded.home_avg_corners,
                away_avg_corners=excluded.away_avg_corners,
                matches_played=excluded.matches_played,
                extra_json=excluded.extra_json,
                updated_at=datetime('now')
        """, (
            data["team_id"], data["league_id"], data["season"],
            data.get("team_name", ""),
            data.get("avg_corners_for", 0), data.get("avg_corners_against", 0),
            data.get("avg_fh_corners_for", 0), data.get("avg_fh_corners_against", 0),
            data.get("home_avg_corners", 0), data.get("away_avg_corners", 0),
            data.get("matches_played", 0),
            json.dumps(extra),
        ))


def get_team_stats(team_id: int, league_id: int, season: int):
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM team_stats
            WHERE team_id=? AND league_id=? AND season=?
        """, (team_id, league_id, season)).fetchone()


def get_cached_team_profile(team_id: int, league_id: int, season: int, max_age_hours: int = 24):
    """Return cached team stats if they exist and are fresh enough."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT *, (julianday('now') - julianday(updated_at)) * 24 as age_hours
            FROM team_stats
            WHERE team_id=? AND league_id=? AND season=? AND matches_played > 0
        """, (team_id, league_id, season)).fetchone()
        if row and row["age_hours"] <= max_age_hours:
            return row
        return None


# ── Model weights ──────────────────────────────────────────────────────────

def get_weights() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT feature, weight FROM model_weights").fetchall()
        return {r["feature"]: r["weight"] for r in rows}


def update_weight(feature: str, correct: bool):
    """Nudge weight up on correct prediction, down on wrong."""
    with get_conn() as conn:
        delta = 0.05 if correct else -0.03
        conn.execute("""
            UPDATE model_weights
            SET weight = MAX(0.1, MIN(3.0, weight + ?)),
                correct = correct + ?,
                total = total + 1,
                updated_at = datetime('now')
            WHERE feature = ?
        """, (delta, 1 if correct else 0, feature))


# ── Accuracy log ───────────────────────────────────────────────────────────

def log_accuracy(league_name: str, total: int, correct: int):
    accuracy = correct / total if total > 0 else 0.0
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO accuracy_log (date, league_name, total, correct, accuracy)
            VALUES (date('now'), ?, ?, ?, ?)
        """, (league_name, total, correct, accuracy))


# ── Live watch list ────────────────────────────────────────────────────────

def add_to_watch_list(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO live_watch
            (fixture_id, league_id, league_name, home_team, away_team,
             match_datetime, pre_match_expected)
            VALUES (?,?,?,?,?,?,?)
        """, (
            data["fixture_id"], data["league_id"], data["league_name"],
            data["home_team"], data["away_team"],
            data["match_datetime"], data.get("pre_match_expected", 0),
        ))


def get_watch_list() -> list:
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM live_watch WHERE is_finished=0
        """).fetchall()


def update_watch_alert(fixture_id: int, minute: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE live_watch
            SET last_alert_minute=?, alerts_sent=alerts_sent+1
            WHERE fixture_id=?
        """, (minute, fixture_id))


def mark_watch_finished(fixture_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE live_watch SET is_finished=1 WHERE fixture_id=?",
            (fixture_id,)
        )


def get_overall_stats():
    with get_conn() as conn:
        return conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome='won' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome='lost' THEN 1 ELSE 0 END) as losses,
                AVG(CASE WHEN outcome IS NOT NULL
                    THEN CASE WHEN outcome='won' THEN 1.0 ELSE 0.0 END
                    END) as win_rate
            FROM predictions
        """).fetchone()
