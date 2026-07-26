"""提醒调度器:后台线程轮询,到点触发系统通知 + 应用内横幅。"""
from __future__ import annotations
import threading
import time
import datetime
from typing import Callable, Optional

try:
    from plyer import notification as plyer_notify
except Exception:  # plyer 不可用时降级
    plyer_notify = None


class ReminderScheduler:
    def __init__(self, db, on_fire: Callable[[str], None]):
        self.db = db
        self.on_fire = on_fire
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            now = datetime.datetime.now()
            for goal in self._active_goals():
                for r in self.db.list_reminders(goal["id"]):
                    if not r["enabled"]:
                        continue
                    if r["cron_hour"] == now.hour and r["cron_min"] == now.minute:
                        last = r["last_fired"]
                        # 今天是否已触发过
                        if last and datetime.datetime.fromtimestamp(last).date() == now.date():
                            continue
                        self._fire(r)
            time.sleep(30)

    def _active_goals(self):
        # 简化:取所有有提醒的 goal。实际只活动目标也够用。
        c = self.db.conn.cursor()
        c.execute("SELECT DISTINCT goal_id as id FROM reminders")
        return [{"id": row["id"]} for row in c.fetchall()]

    def _fire(self, r: dict):
        title = f"⏰ 小助手提醒:{r['title']}"
        if plyer_notify:
            try:
                plyer_notify.notify(title=title, message="到点了,去做这件重要的事吧。", timeout=10)
            except Exception:
                pass
        self.db.mark_fired(r["id"])
        try:
            self.on_fire(r["title"])
        except Exception:
            pass
