"""长期记忆:把每天/每周的打卡与对话压缩成结构化摘要存起来。

mock 阶段用规则提取(进度/卡点/偏好);接真 LLM 后,这里改成"把当日原始记录喂给模型→
产出同样结构的摘要",上层(对话回灌、周报)完全不用改。
"""
from __future__ import annotations
import datetime
from typing import Optional

NEG = ["放弃", "不想", "累", "没动力", "算了", "摆烂", "焦虑", "太难", "太多",
       "没时间", "做不到", "压力", "忙", "拉胯", "难受"]
PREF_EASY = ["减少", "太难", "太多", "没时间", "做不到", "轻松", "太松"]
PREF_HARD = ["加量", "太简单", "加强", "更狠", "加点量"]


def date_str(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def today_str() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def monday_of(d: str) -> str:
    dt = datetime.date.fromisoformat(d)
    return (dt - datetime.timedelta(days=dt.weekday())).strftime("%Y-%m-%d")


def _detect(text: str, words) -> bool:
    return any(w in text for w in words)


def summarize_day(db, goal_id: int, ds: str) -> dict:
    checkins = db.checkins_by_date(goal_id, ds)
    chats = db.chat_by_date(goal_id, ds)
    plan = db.get_plan(goal_id)

    done_titles = []
    for p in plan:
        for t in p.tasks:
            if t.last_done and date_str(t.last_done) == ds:
                done_titles.append(t.title)

    mood = checkins[-1].mood if checkins else ""
    note = checkins[-1].note if checkins else ""

    blockers = []
    for m in chats:
        if m.role == "user" and _detect(m.content, NEG):
            snippet = m.content if len(m.content) <= 40 else m.content[:40] + "…"
            if snippet not in blockers:
                blockers.append(snippet)
    if mood == "bad":
        blockers.append("当天自评状态差")

    prefs = []
    for m in chats:
        if m.role == "user":
            if _detect(m.content, PREF_EASY):
                prefs.append("偏好更轻松的节奏")
            if _detect(m.content, PREF_HARD):
                prefs.append("偏好更高强度")

    progress = f"完成 {len(done_titles)} 个任务"
    if mood:
        progress += f",状态:{ {'great':'不错','ok':'还行','bad':'拉胯'}.get(mood,'') }"

    return {
        "progress": progress,
        "done_tasks": done_titles,
        "mood": mood,
        "blockers": blockers,
        "prefs": list(dict.fromkeys(prefs)),
        "note": note,
    }


def summarize_week(db, goal_id: int, week_start: str) -> dict:
    all_sum = db.get_summaries(goal_id, limit=500)
    days = [s for s in all_sum if s["kind"] == "day"
            and week_start <= s["ref_date"] <= (datetime.date.fromisoformat(week_start)
                                                + datetime.timedelta(days=6)).strftime("%Y-%m-%d")]
    if not days:
        return {"progress": "本周无记录", "blockers": [], "prefs": [], "done_count": 0}
    total_done = sum(len(d["data"].get("done_tasks", [])) for d in days)
    blockers = []
    prefs = []
    for d in days:
        blockers += d["data"].get("blockers", [])
        prefs += d["data"].get("prefs", [])
    return {
        "progress": f"本周完成 {total_done} 个任务,打卡 {len(days)} 天",
        "blockers": list(dict.fromkeys(blockers)),
        "prefs": list(dict.fromkeys(prefs)),
        "done_count": total_done,
    }


def run_summaries(db, goal) -> None:
    """补齐历史日/周摘要(在 App 启动时调用)。"""
    if not goal:
        return
    created = datetime.date.fromtimestamp(goal.created_at)
    today = datetime.date.today()
    # 日摘要:从创建日补到昨天
    d = created
    while d < today:
        ds = d.strftime("%Y-%m-%d")
        if not db.summary_exists(goal.id, "day", ds):
            db.add_summary(goal.id, "day", ds, summarize_day(db, goal.id, ds))
        d += datetime.timedelta(days=1)
    # 周摘要:补齐已结束的整周
    ms = datetime.date.fromisoformat(monday_of(created.strftime("%Y-%m-%d")))
    last_week_monday = today - datetime.timedelta(days=today.weekday() + 7)
    while ms <= last_week_monday:
        ws = ms.strftime("%Y-%m-%d")
        if not db.summary_exists(goal.id, "week", ws):
            db.add_summary(goal.id, "week", ws, summarize_week(db, goal.id, ws))
        ms += datetime.timedelta(days=7)
