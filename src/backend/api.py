"""对前端(桌面 PyWebView / 浏览器 HTTP)暴露的 API 层。

所有方法都可被前端直接调用:
- 桌面模式:window.pywebview.api.<method>(...args)
- 浏览器模式:POST /api  {name, args}
"""
from __future__ import annotations

import os
import re
import json
import time
import datetime as _dt
import threading
import logging

from .database import Database
from .models import Goal, Phase, Task, CheckIn, ChatMessage
from .persona import PERSONAS, get_persona
from . import reminders as reminders_mod
from .ai_engine import build_engine, AIEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lifecoach-api")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")


class API:
    def __init__(self):
        self.db = Database()
        self.config = self._load_config()
        self.engine: AIEngine = build_engine(self.config)
        self.persona_id = self.config.get("persona_id", "warm_mentor")
        self.reminders = reminders_mod.ReminderScheduler(self.db, self._on_reminder)
        try:
            self.reminders.start()
        except Exception as e:
            logger.warning("提醒线程启动失败:%s", e)

    # ---------------- 配置 ----------------
    @staticmethod
    def _load_config() -> dict:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_config(self, data: dict):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_config(self) -> dict:
        cfg = self.config
        llm = cfg.get("llm", {}) if isinstance(cfg.get("llm"), dict) else {}
        api_key_set = bool(
            llm.get("api_key")
            or os.environ.get("LIFECOACH_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        active_engine = type(self.engine).__name__
        return {
            "persona_id": self.persona_id,
            "engine": cfg.get("engine", "auto"),
            "llm": {
                "base_url": llm.get("base_url", "https://api.deepseek.com/v1"),
                "model": llm.get("model", "deepseek-chat"),
                "api_key_set": api_key_set,
                "temperature": llm.get("temperature", 0.7),
                "vision": llm.get("vision", False),
            },
            "personas": [{"id": p.id, "name": p.name, "emoji": p.emoji} for p in PERSONAS.values()],
            "active_engine": active_engine,
            "using_llm": active_engine == "LLMAIEngine",
        }

    def save_config(self, payload: dict) -> dict:
        cfg = self._load_config()
        if isinstance(payload, dict):
            if "engine" in payload:
                cfg["engine"] = payload["engine"]
            if "persona_id" in payload:
                cfg["persona_id"] = payload["persona_id"]
                self.persona_id = payload["persona_id"]
            if isinstance(payload.get("llm"), dict):
                llm = dict(cfg.get("llm", {})) if isinstance(cfg.get("llm"), dict) else {}
                for k in ("base_url", "model", "temperature", "vision"):
                    if k in payload["llm"] and payload["llm"][k] is not None:
                        llm[k] = payload["llm"][k]
                ak = payload["llm"].get("api_key")
                if ak:
                    llm["api_key"] = ak
                cfg["llm"] = llm
        self._save_config(cfg)
        self.config = cfg
        self.engine = build_engine(cfg)
        return {"ok": True, "active_engine": type(self.engine).__name__}

    # ---------------- 目标 / 多目标 ----------------
    def get_current_goal(self) -> Goal | None:
        cur = self.db.get_meta("current_goal_id")
        if cur:
            g = self.db.get_goal(int(cur))
            if g:
                return g
        g = self.db.get_active_goal()
        if g:
            return g
        return self.db.get_latest_draft_goal()

    def get_active_goal(self) -> Goal | None:
        # 兼容提醒线程:返回当前目标(优先 active)
        return self.get_current_goal()

    def list_goals(self) -> list:
        goals = self.db.list_goals(include_draft=True)
        cur = self.get_current_goal()
        out = []
        for g in goals:
            prof = g.profile_dict()
            out.append({
                "id": g.id,
                "title": g.title,
                "status": g.status,
                "created_at": g.created_at,
                "motivation_style": prof.get("motivation_style", ""),
                "current": (cur is not None and cur.id == g.id),
            })
        return out

    def switch_goal(self, gid: int) -> dict:
        g = self.db.get_goal(gid)
        if not g or g.status not in ("active", "draft"):
            return {"error": "目标不存在或已归档"}
        self.db.set_meta("current_goal_id", gid)
        return self.get_state()

    def start_goal(self, text: str) -> dict:
        """新建目标并进入「多轮共创」对话(先和用户确认,再生成量化计划)。"""
        text = (text or "").strip()
        if not text:
            return {"error": "目标不能为空"}
        goal = self.db.add_goal(text, status="draft")
        self.db.set_meta("current_goal_id", goal.id)  # 新草稿立即成为当前目标,进入共创
        persona = get_persona(self.persona_id)
        try:
            ask, profile, _ = self.engine.onboard_goal(goal.title, [], persona)
        except Exception as e:
            logger.warning("onboard 失败,回退模板:%s", e)
            ask, profile, _ = build_engine({"engine": "mock"}).onboard_goal(goal.title, [], persona)
        self.db.update_goal_profile(goal.id, json.dumps(profile, ensure_ascii=False))
        self.db.add_message(goal.id, ChatMessage(role="assistant", content=ask, ts=time.time()))
        return self.get_state()

    def goal_chat(self, message: str) -> dict:
        """共创过程中的多轮对话。攒够信息后由 AI 产出计划并激活目标。"""
        goal = self.get_current_goal()
        if not goal or goal.status != "draft":
            return {"error": "当前没有正在共创的目标"}
        message = (message or "").strip()
        if not message:
            return {"error": "说点什么吧"}
        self.db.add_message(goal.id, ChatMessage(role="user", content=message, ts=time.time()))
        persona = get_persona(self.persona_id)
        history = [{"role": m.role, "content": m.content} for m in self.db.chat_history(goal.id)]
        profile = goal.profile_dict()
        try:
            ask, profile, done = self.engine.onboard_goal(goal.title, history, persona, profile)
        except Exception as e:
            logger.warning("onboard 失败,回退模板:%s", e)
            ask, profile, done = build_engine({"engine": "mock"}).onboard_goal(goal.title, history, persona, profile)
        self.db.update_goal_profile(goal.id, json.dumps(profile, ensure_ascii=False))
        # 安全上限:对话轮次过多也强制生成,避免永远问下去
        force = len(history) >= 8
        if done or force:
            try:
                plan = self.engine.decompose_goal(goal.title, profile)
            except Exception as e:
                logger.warning("decompose 失败,回退模板:%s", e)
                plan = build_engine({"engine": "mock"}).decompose_goal(goal.title, profile)
            if plan:
                self.db.save_plan(goal.id, plan)
                self.db.set_goal_status(goal.id, "active")
                self.db.set_meta("current_goal_id", goal.id)
                self._auto_reminders(goal, plan)
                summary = "计划已生成 ✅ 我们一步步来,每天打卡,我陪你。"
                self.db.add_message(goal.id, ChatMessage(role="assistant", content=summary, ts=time.time()))
                return {**self.get_state(), "onboarding_complete": True, "plan": [p.to_dict() for p in plan]}
        self.db.add_message(goal.id, ChatMessage(role="assistant", content=ask, ts=time.time()))
        return {"reply": ask, "onboarding": True, "profile": profile}

    def _auto_reminders(self, goal: Goal, plan: list[Phase]):
        try:
            self.db.set_meta(f"remind_{goal.id}", "1")
        except Exception:
            pass
        for pi, p in enumerate(plan, 1):
            for t in p.tasks:
                if t.frequency == "daily":
                    self.db.add_reminder(goal.id, t.id, t.title, 21, 0, -1)
                elif t.frequency == "weekly":
                    wd = self._weekday_from_id(t.id)
                    self.db.add_reminder(goal.id, t.id, t.title, 10, 0, wd)
                else:
                    self.db.add_reminder(goal.id, t.id, t.title, 10, 0, -1)
        # 每日打卡提醒
        self.db.add_reminder(goal.id, "__checkin__", "每日打卡", 21, 30, -1)

    @staticmethod
    def _weekday_from_id(tid: str) -> int:
        return sum(ord(c) for c in str(tid)) % 7

    # ---------------- 对话 ----------------
    def chat(self, message: str, image: str | None = None) -> dict:
        goal = self.get_current_goal()
        if not goal or goal.status != "active":
            return {"reply": "先告诉我你的目标吧~ 在「目标」里新建一个,我们会一起把它聊清楚。", "no_goal": True}
        message = (message or "").strip()
        if not message and not image:
            return {"reply": "我在呢,说说看?"}
        persona = get_persona(self.persona_id)
        plan = self.db.get_plan(goal.id)
        checkins = self.db.recent_checkins(goal.id, 5)
        history = [{"role": m.role, "content": m.content} for m in self.db.chat_history(goal.id)]
        try:
            reply = self.engine.chat(message, history, goal, plan, persona, checkins, image=image)
        except Exception as e:
            logger.warning("chat 失败,回退模板:%s", e)
            reply = build_engine({"engine": "mock"}).chat(message, history, goal, plan, persona, checkins, image=image)
        self.db.add_message(goal.id, ChatMessage(role="user", content=message or "（图片）", ts=time.time()))
        self.db.add_message(goal.id, ChatMessage(role="assistant", content=reply, ts=time.time()))
        return {"reply": reply}

    def adapt_plan(self, feedback: str) -> dict:
        goal = self.get_current_goal()
        if not goal or goal.status != "active":
            return {"error": "没有可调整的目标"}
        plan = self.db.get_plan(goal.id)
        if not plan:
            return {"error": "计划为空"}
        persona = get_persona(self.persona_id)
        try:
            changed, new_plan = self.engine.adapt_plan(feedback, goal, plan, persona)
        except Exception as e:
            logger.warning("adapt 失败:%s", e)
            changed, new_plan = False, plan
        if changed:
            self.db.save_plan(goal.id, new_plan)
            self._auto_reminders(goal, new_plan)
        return {"changed": changed, "plan": [p.to_dict() for p in new_plan]}

    # ---------------- 打卡 ----------------
    def checkin(self, mood: str = "", note: str = "", done_task_ids: list | None = None) -> dict:
        goal = self.get_current_goal()
        if not goal or goal.status != "active":
            return {"error": "没有进行中的目标"}
        ci = CheckIn(goal_id=goal.id, ts=time.time(), mood=mood, note=note,
                     done_task_ids=",".join(done_task_ids or []))
        self.db.add_checkin(ci)
        today = _dt.date.today().strftime("%Y-%m-%d")
        streak = self.db.record_checkin_streak(goal.id, today)
        return {**self.get_state(), "streak": streak}

    def toggle_task(self, task_id: str) -> dict:
        goal = self.get_current_goal()
        if not goal:
            return {"error": "no goal"}
        plan = self.db.get_plan(goal.id)
        found = None
        for p in plan:
            for t in p.tasks:
                if t.id == task_id:
                    t.done = not t.done
                    found = t
        if found is None:
            return {"error": "task not found"}
        self.db.save_plan(goal.id, plan)
        return {"ok": True, "task": found.to_dict()}

    # ---------------- 日历(聚合所有目标) ----------------
    def get_calendar(self, year: int | None = None, month: int | None = None) -> dict:
        today = _dt.date.today()
        year = int(year or today.year)
        month = int(month or today.month)
        goals = self.db.list_goals(include_draft=False)
        days: dict[str, list] = {}
        for g in goals:
            plan = self.db.get_plan(g.id)
            base = _dt.datetime.fromtimestamp(g.created_at)
            for p in plan:
                start_w, end_w = self._parse_weeks(p.weeks)
                pstart = (base + _dt.timedelta(weeks=start_w)).date()
                pend = (base + _dt.timedelta(weeks=end_w)).date()
                for t in p.tasks:
                    occ = self._occurrences(t, pstart, pend, year, month)
                    for d in occ:
                        days.setdefault(d, []).append({
                            "goal_id": g.id,
                            "goal_title": g.title,
                            "task_id": t.id,
                            "task_title": t.title,
                            "detail": t.detail,
                            "frequency": t.frequency,
                            "duration_min": t.duration_min,
                        })
        return {
            "year": year, "month": month,
            "today": today.isoformat(),
            "days": days,
            "goals": [{"id": g.id, "title": g.title} for g in goals],
        }

    @staticmethod
    def _parse_weeks(s: str):
        s = str(s)
        nums = [int(x) for x in re.findall(r"\d+", s)]
        if not nums:
            return 0, 4
        if "+" in s and nums:
            return max(0, nums[0] - 1), nums[0] + 10
        if len(nums) >= 2:
            return max(0, nums[0] - 1), nums[1]
        return max(0, nums[0] - 1), nums[0]

    @staticmethod
    def _occurrences(t: Task, pstart, pend, year, month) -> list:
        out = []
        if t.frequency == "daily":
            d = pstart
            while d <= pend:
                if d.year == year and d.month == month:
                    out.append(d.isoformat())
                d += _dt.timedelta(days=1)
        elif t.frequency == "weekly":
            wd = sum(ord(c) for c in str(t.id)) % 7
            d = pstart
            while d.weekday() != wd:
                d += _dt.timedelta(days=1)
            while d <= pend:
                if d.year == year and d.month == month:
                    out.append(d.isoformat())
                d += _dt.timedelta(weeks=1)
        else:  # once
            d = pstart
            if d.year == year and d.month == month:
                out.append(d.isoformat())
        return out

    # ---------------- 状态 ----------------
    def get_state(self) -> dict:
        goal = self.get_current_goal()
        onboarding = bool(goal and goal.status == "draft")
        plan = self.db.get_plan(goal.id) if goal else []
        checkins = self.db.recent_checkins(goal.id, 10) if goal else []
        history = [m.to_dict() for m in self.db.chat_history(goal.id)] if goal else []
        reminders = self.db.list_reminders(goal.id) if goal else []
        try:
            membership = self.db.get_membership(goal.id) if goal else None
        except Exception:
            membership = None
        try:
            summaries = self.db.get_summaries(goal.id) if goal else []
        except Exception:
            summaries = []
        return {
            "goal": goal.to_dict() if goal else None,
            "onboarding": onboarding,
            "plan": [p.to_dict() for p in plan],
            "checkins": [c.to_dict() for c in checkins],
            "history": history,
            "chat": history,  # 前端使用 state.chat 渲染对话
            "goals": self.list_goals(),
            "profile": goal.profile_dict() if goal else {},
            "personas": [{"id": p.id, "name": p.name, "emoji": p.emoji,
                          "tagline": getattr(p, "tagline", "")} for p in PERSONAS.values()],
            "persona_id": self.persona_id,
            "reminders": reminders,
            "membership": membership,
            "summaries": summaries,
        }

    # ---------------- 人设 ----------------
    def set_persona(self, pid: str):
        if pid in PERSONAS:
            self.persona_id = pid
            cfg = self._load_config()
            cfg["persona_id"] = pid
            self._save_config(cfg)
            self.config = cfg
            goal = self.get_active_goal()
            if goal:
                self.db.add_message(goal.id, ChatMessage(
                    role="assistant",
                    content=f"我是你的新教练:{PERSONAS[pid].name} {PERSONAS[pid].emoji} 接下来由我陪你。",
                    ts=time.time()))
            return self.get_state()
        return {"error": "未知人设"}

    # ---------------- 工具 ----------------
    def get_suggestions(self) -> list:
        return []

    def _on_reminder(self, title: str):
        logger.info("提醒触发:%s", title)
        # 桌面模式下可在此向 pywebview 推送横幅;浏览器/小程序模式下由各自客户端拉取

    def stop(self):
        try:
            self.reminders.stop()
        except Exception:
            pass
        self.db.close()


# 暴露给 HTTP 层:方法名 -> 是否接收可变位置参数
if __name__ == "__main__":
    pass
