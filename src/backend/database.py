"""SQLite 存储层。所有持久化通过这里,AI 引擎与 API 不直接碰数据库。"""
from __future__ import annotations
import sqlite3
import os
import time
from typing import Optional

from .models import Goal, Phase, CheckIn, ChatMessage, plan_to_json, plan_from_json

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data.db")


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = os.path.abspath(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        c = self.conn.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            normalized TEXT,
            created_at REAL,
            status TEXT,
            profile_json TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS plans (
            goal_id INTEGER PRIMARY KEY,
            phases_json TEXT,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
        );
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            ts REAL,
            mood TEXT,
            note TEXT,
            done_task_ids TEXT
        );
        CREATE TABLE IF NOT EXISTS chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            role TEXT,
            content TEXT,
            ts REAL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            task_id TEXT,
            title TEXT,
            cron_hour INTEGER,
            cron_min INTEGER,
            weekday INTEGER DEFAULT -1,
            enabled INTEGER DEFAULT 1,
            last_fired REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS summaries (
            goal_id INTEGER,
            kind TEXT,
            ref_date TEXT,
            data_json TEXT,
            created_at REAL,
            UNIQUE(goal_id, kind, ref_date)
        );
        CREATE TABLE IF NOT EXISTS membership (
            goal_id INTEGER PRIMARY KEY,
            deposit INTEGER DEFAULT 0,
            threshold INTEGER DEFAULT 21,
            consecutive INTEGER DEFAULT 0,
            last_checkin_date TEXT DEFAULT '',
            refunded INTEGER DEFAULT 0,
            created_at REAL
        );
        """)
        # 兼容旧库:补 profile_json 列
        try:
            c.execute("ALTER TABLE goals ADD COLUMN profile_json TEXT")
        except Exception:
            pass
        self.conn.commit()

    # ---------- meta(全局键值,如 current_goal_id) ----------
    def get_meta(self, key: str, default=None):
        c = self.conn.cursor()
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, str(value)))
        self.conn.commit()

    # ---------- Goals ----------
    def add_goal(self, title: str, normalized: str = "", profile_json: str = "", status: str = "draft") -> Goal:
        c = self.conn.cursor()
        c.execute("INSERT INTO goals (title, normalized, created_at, status, profile_json) VALUES (?,?,?,?,?)",
                  (title, normalized, time.time(), status, profile_json))
        self.conn.commit()
        return Goal(id=c.lastrowid, title=title, normalized=normalized,
                    created_at=time.time(), status=status, profile=profile_json)

    def get_active_goal(self) -> Optional[Goal]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM goals WHERE status='active' ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        return self._row_to_goal(row)

    def get_latest_draft_goal(self) -> Optional[Goal]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM goals WHERE status='draft' ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        return self._row_to_goal(row)

    def get_goal(self, gid: int) -> Optional[Goal]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM goals WHERE id=?", (gid,))
        return self._row_to_goal(c.fetchone())

    def list_goals(self, include_draft: bool = True) -> list[Goal]:
        c = self.conn.cursor()
        if include_draft:
            c.execute("SELECT * FROM goals ORDER BY id DESC")
        else:
            c.execute("SELECT * FROM goals WHERE status='active' ORDER BY id DESC")
        return [self._row_to_goal(r) for r in c.fetchall()]

    def set_goal_status(self, gid: int, status: str):
        c = self.conn.cursor()
        c.execute("UPDATE goals SET status=? WHERE id=?", (status, gid))
        self.conn.commit()

    def update_goal_profile(self, gid: int, profile_json: str):
        c = self.conn.cursor()
        c.execute("UPDATE goals SET profile_json=? WHERE id=?", (profile_json, gid))
        self.conn.commit()

    @staticmethod
    def _row_to_goal(row) -> Optional[Goal]:
        if not row:
            return None
        return Goal(id=row["id"], title=row["title"], normalized=row["normalized"] or "",
                    created_at=row["created_at"], status=row["status"],
                    profile=row["profile_json"] or "")

    # ---------- Plans ----------
    def save_plan(self, goal_id: int, phases: list[Phase]):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO plans (goal_id, phases_json) VALUES (?,?)",
                  (goal_id, plan_to_json(phases)))
        self.conn.commit()

    def get_plan(self, goal_id: int) -> list[Phase]:
        c = self.conn.cursor()
        c.execute("SELECT phases_json FROM plans WHERE goal_id=?", (goal_id,))
        row = c.fetchone()
        if not row:
            return []
        return plan_from_json(row["phases_json"])

    # ---------- Check-ins ----------
    def add_checkin(self, ci: CheckIn):
        c = self.conn.cursor()
        c.execute("INSERT INTO checkins (goal_id, ts, mood, note, done_task_ids) VALUES (?,?,?,?,?)",
                  (ci.goal_id, ci.ts, ci.mood, ci.note, ci.done_task_ids))
        self.conn.commit()

    def recent_checkins(self, goal_id: int, limit: int = 10) -> list[CheckIn]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM checkins WHERE goal_id=? ORDER BY ts DESC LIMIT ?", (goal_id, limit))
        return [CheckIn(id=r["id"], goal_id=r["goal_id"], ts=r["ts"], mood=r["mood"],
                        note=r["note"], done_task_ids=r["done_task_ids"]) for r in c.fetchall()]

    # ---------- Chat ----------
    def add_message(self, goal_id: int, msg: ChatMessage):
        c = self.conn.cursor()
        c.execute("INSERT INTO chat (goal_id, role, content, ts) VALUES (?,?,?,?)",
                  (goal_id, msg.role, msg.content, msg.ts))
        self.conn.commit()

    def chat_history(self, goal_id: int, limit: int = 40) -> list[ChatMessage]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM chat WHERE goal_id=? ORDER BY ts ASC LIMIT ?", (goal_id, limit))
        return [ChatMessage(role=r["role"], content=r["content"], ts=r["ts"]) for r in c.fetchall()]

    # ---------- Reminders ----------
    def add_reminder(self, goal_id: int, task_id: str, title: str, hour: int, minute: int, weekday: int = -1):
        c = self.conn.cursor()
        c.execute("INSERT INTO reminders (goal_id, task_id, title, cron_hour, cron_min, weekday, enabled) VALUES (?,?,?,?,?,?,1)",
                  (goal_id, task_id, title, hour, minute, weekday))
        self.conn.commit()

    def list_reminders(self, goal_id: int) -> list[dict]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM reminders WHERE goal_id=?", (goal_id,))
        return [dict(r) for r in c.fetchall()]

    def mark_fired(self, rid: int):
        c = self.conn.cursor()
        c.execute("UPDATE reminders SET last_fired=? WHERE id=?", (time.time(), rid))
        self.conn.commit()

    # ---------- Summaries (长期记忆) ----------
    def add_summary(self, goal_id: int, kind: str, ref_date: str, data: dict):
        import json
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO summaries (goal_id, kind, ref_date, data_json, created_at) VALUES (?,?,?,?,?)",
                  (goal_id, kind, ref_date, json.dumps(data, ensure_ascii=False), time.time()))
        self.conn.commit()

    def upsert_summary(self, goal_id: int, kind: str, ref_date: str, data: dict):
        import json
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO summaries (goal_id, kind, ref_date, data_json, created_at) VALUES (?,?,?,?,?)",
                  (goal_id, kind, ref_date, json.dumps(data, ensure_ascii=False), time.time()))
        self.conn.commit()

    def get_summaries(self, goal_id: int, limit: int = 20) -> list:
        c = self.conn.cursor()
        c.execute("SELECT * FROM summaries WHERE goal_id=? ORDER BY ref_date DESC, kind DESC LIMIT ?",
                  (goal_id, limit))
        out = []
        for r in c.fetchall():
            try:
                data = json.loads(r["data_json"])
            except Exception:
                data = {}
            out.append({"kind": r["kind"], "ref_date": r["ref_date"], "data": data})
        return out

    def summary_exists(self, goal_id: int, kind: str, ref_date: str) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT 1 FROM summaries WHERE goal_id=? AND kind=? AND ref_date=?",
                  (goal_id, kind, ref_date))
        return c.fetchone() is not None

    def checkins_by_date(self, goal_id: int, date_str: str) -> list:
        c = self.conn.cursor()
        c.execute("SELECT * FROM checkins WHERE goal_id=? AND date(ts,'unixepoch','localtime')=? ORDER BY ts",
                  (goal_id, date_str))
        return [CheckIn(id=r["id"], goal_id=r["goal_id"], ts=r["ts"], mood=r["mood"],
                        note=r["note"], done_task_ids=r["done_task_ids"]) for r in c.fetchall()]

    def chat_by_date(self, goal_id: int, date_str: str) -> list:
        c = self.conn.cursor()
        c.execute("SELECT * FROM chat WHERE goal_id=? AND date(ts,'unixepoch','localtime')=? ORDER BY ts",
                  (goal_id, date_str))
        return [ChatMessage(role=r["role"], content=r["content"], ts=r["ts"]) for r in c.fetchall()]

    # ---------- Membership (连续签到 + 押金退款) ----------
    def get_membership(self, goal_id: int) -> Optional[dict]:
        c = self.conn.cursor()
        c.execute("SELECT * FROM membership WHERE goal_id=?", (goal_id,))
        r = c.fetchone()
        return dict(r) if r else None

    def ensure_membership(self, goal_id: int, deposit: int, threshold: int):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO membership (goal_id, deposit, threshold, consecutive, last_checkin_date, refunded, created_at) VALUES (?,?,?,0,'',0,?)",
                  (goal_id, deposit, threshold, time.time()))
        self.conn.commit()

    def record_checkin_streak(self, goal_id: int, today_str: str) -> dict:
        import datetime as _dt
        m = self.get_membership(goal_id)
        if not m:
            return {"consecutive": 0, "threshold": 0, "deposit": 0, "refunded": 0, "refunded_now": False}
        last = m["last_checkin_date"]
        if last == today_str:
            consecutive = m["consecutive"]
        elif last == (lambda d: (d - _dt.timedelta(days=1)).strftime("%Y-%m-%d"))(
                _dt.date.fromisoformat(today_str)):
            consecutive = m["consecutive"] + 1
        else:
            consecutive = 1
        refunded_now = False
        refunded = m["refunded"]
        if not refunded and consecutive >= m["threshold"]:
            refunded = 1
            refunded_now = True
        c = self.conn.cursor()
        c.execute("UPDATE membership SET consecutive=?, last_checkin_date=?, refunded=? WHERE goal_id=?",
                  (consecutive, today_str, refunded, goal_id))
        self.conn.commit()
        return {"consecutive": consecutive, "threshold": m["threshold"],
                "deposit": m["deposit"], "refunded": refunded, "refunded_now": refunded_now}

    def close(self):
        self.conn.close()
