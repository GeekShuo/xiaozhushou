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
from .ai_engine import (build_engine, AIEngine, ONBOARD_DIMS,
                        first_missing_dim, _next_question, _onboard_done)

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
        return {**self.get_state(), "reply": ask}

    def goal_chat(self, message: str, image: str | None = None) -> dict:
        """共创过程中的多轮对话。攒够信息后由 AI 产出计划并激活目标。"""
        goal = self.get_current_goal()
        if not goal or goal.status != "draft":
            return {"error": "当前没有正在共创的目标"}
        message = (message or "").strip()
        if image and not message:
            message = "(我发了一张图片,请结合它继续了解我)"
        if not message:
            return {"error": "说点什么吧"}
        self.db.add_message(goal.id, ChatMessage(role="user", content=message, ts=time.time()))
        persona = get_persona(self.persona_id)
        history = [{"role": m.role, "content": m.content} for m in self.db.chat_history(goal.id)]
        profile = goal.profile_dict()
        # 动力风格由用户「点选卡片」或明确措辞决定,不可靠交给自由文本抽取。
        # 若全对话都没提过风格关键词,则清掉 LLM 可能误猜的 style,逼用户点卡片。
        user_texts = [message] + self._user_texts(history)
        det_style = self._detect_style(*user_texts)
        if det_style:
            profile["motivation_style"] = det_style
        else:
            profile["motivation_style"] = ""
        try:
            ask, profile, done = self.engine.onboard_goal(goal.title, history, persona, profile)
        except Exception as e:
            logger.warning("onboard 失败,回退模板:%s", e)
            ask, profile, done = build_engine({"engine": "mock"}).onboard_goal(goal.title, history, persona, profile)
        # 确定性风格锁定:只要对话里出现过风格关键词,LLM 不得覆盖(避免「运动搭子」被误判成「严师」)
        det2 = self._detect_style(*user_texts)
        if det2:
            profile["motivation_style"] = det2
        else:
            profile["motivation_style"] = ""
        # 防复读:AI 又问了和上一轮一模一样的问题 = 没接住用户的回答。
        # 把用户原话直接计入当前缺失维度(动力风格除外,那个必须关键词确认),强制推进到下一问。
        prev_ask = next((m.get("content", "") for m in reversed(history[:-1])
                         if m.get("role") == "assistant"), "")
        if not done and ask and ask.strip() == prev_ask.strip():
            missing = first_missing_dim(profile)
            if missing and missing != "motivation_style":
                profile["habits" if missing == "habit" else missing] = message
            ask = _next_question(profile)
            done = _onboard_done(profile)
        self.db.update_goal_profile(goal.id, json.dumps(profile, ensure_ascii=False))
        # 安全上限:对话轮次过多也强制生成,避免永远问下去。
        # history 含双方消息,共创有 5 个维度,至少要给 6 轮问答(≈12 条)的空间。
        force = len(history) >= 12
        # 动力风格决定后续所有陪伴语气,没问到就不许收尾(除非已到轮次上限)
        if done and not profile.get("motivation_style") and not force:
            done = False
            if "陪着你" in prev_ask or "身份陪你" in prev_ask:
                # 上一轮已经问过风格,换个说法追问,别复读
                ask = ("收到!就差最后一步了:直接回我一个词——「搭子」「偶像」「严师」或「温柔」,"
                       "我马上生成你的专属计划 ✅")
            else:
                ask = ("最后一个问题:你希望我用哪种方式陪着你?\n"
                       "🤝 运动搭子(一起干、互相打卡) / ⭐ 偶像(用你偶像的口吻激励你) / "
                       "📏 严师(直接指出问题、push 你) / 🫶 温柔陪伴(先共情再鼓励)")
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
        prog = [{"key": k, "label": lb, "done": bool(str(profile.get(k) or (profile.get("habits") if k == "habit" else "")).strip())}
                for k, lb in ONBOARD_DIMS]
        return {"reply": ask, "onboarding": True, "profile": profile, "onboarding_progress": prog,
                "history": [m.to_dict() for m in self.db.chat_history(goal.id)]}

    def delete_goal(self, gid: int) -> dict:
        gid = int(gid)
        g = self.db.get_goal(gid)
        if not g:
            return {"error": "目标不存在"}
        self.db.delete_goal(gid)
        # 若删的是当前目标,自动切到最近一个可用目标
        cur = self.db.get_meta("current_goal_id")
        if cur and int(cur) == gid:
            rest = [x for x in self.db.list_goals(include_draft=True) if x.status in ("active", "draft")]
            self.db.set_meta("current_goal_id", rest[0].id if rest else "")
        return self.get_state()

    def archive_goal(self, gid: int) -> dict:
        gid = int(gid)
        g = self.db.get_goal(gid)
        if not g:
            return {"error": "目标不存在"}
        self.db.set_goal_status(gid, "archived")
        cur = self.db.get_meta("current_goal_id")
        if cur and int(cur) == gid:
            rest = [x for x in self.db.list_goals(include_draft=True) if x.status in ("active", "draft")]
            self.db.set_meta("current_goal_id", rest[0].id if rest else "")
        return self.get_state()

    def finish_onboarding(self) -> dict:
        """用户主动跳过共创:用已收集的画像直接生成计划。"""
        goal = self.get_current_goal()
        if not goal or goal.status != "draft":
            return {"error": "当前没有正在共创的目标"}
        profile = goal.profile_dict()
        # 跳过时若用户点过风格卡片,从对话历史里确定性锁定风格
        hist = self.db.chat_history(goal.id)
        det = self._detect_style(*self._user_texts(hist))
        if det:
            profile["motivation_style"] = det
        try:
            plan = self.engine.decompose_goal(goal.title, profile)
        except Exception as e:
            logger.warning("decompose 失败,回退模板:%s", e)
            plan = build_engine({"engine": "mock"}).decompose_goal(goal.title, profile)
        if not plan:
            return {"error": "计划生成失败,请再试一次"}
        self.db.save_plan(goal.id, plan)
        self.db.set_goal_status(goal.id, "active")
        self.db.set_meta("current_goal_id", goal.id)
        self._auto_reminders(goal, plan)
        self.db.add_message(goal.id, ChatMessage(
            role="assistant", content="好,按你目前告诉我的信息,计划已生成 ✅ 之后随时可以在计划页让我调整。",
            ts=time.time()))
        return {**self.get_state(), "onboarding_complete": True}

    def _auto_reminders(self, goal: Goal, plan: list[Phase]):
        """根据计划生成默认提醒。先清空旧提醒,避免重复叠加;时间用户可在进度页修改。"""
        self.db.clear_reminders(goal.id)
        for p in plan:
            for t in p.tasks:
                if t.frequency == "daily":
                    self.db.add_reminder(goal.id, t.id, t.title, 20, 0, -1)
                elif t.frequency == "weekly":
                    self.db.add_reminder(goal.id, t.id, t.title, 10, 0, self._weekly_weekday(t.id))
        # 每日打卡提醒(单独一条,时间可改)
        self.db.add_reminder(goal.id, "__checkin__", "每日打卡", 21, 30, -1)

    @staticmethod
    def _weekly_weekday(tid: str) -> int:
        """weekly 任务默认排周六(5);同目标多个 weekly 任务错开到周六/周日/周三。"""
        import re as _re
        nums = _re.findall(r"\d+", str(tid))
        idx = int(nums[-1]) if nums else 0
        return [5, 6, 2][idx % 3]

    STYLE_RULES = [
        ("workout_buddy", ["运动搭子", "搭子", "一起练", "互相打卡", "陪练", "workout_buddy"]),
        ("idol", ["偶像", "榜样", "爱豆", "明星", "崇拜", "idol"]),
        ("strict", ["严师", "严格", "督促", "push", "狠一点", "严厉", "strict"]),
        ("warm", ["温柔", "陪伴", "鼓励", "宽松", "warm"]),
    ]

    @classmethod
    def _detect_style(cls, *texts: str) -> str:
        """确定性识别动力风格。

        注意:只能传「用户说的话」,且按「最近的在前」传入。
        助手的提问里会列出全部四种风格(如「运动搭子/偶像/严师/温柔陪伴」),
        一旦把助手文本混进来必然误判。
        同一句里命中多个关键词时,取出现位置最靠前的那个(更接近用户的表述主体)。
        """
        for t in texts:
            t = (t or "").lower()
            if not t:
                continue
            best, best_pos = "", len(t) + 1
            for style, kws in cls.STYLE_RULES:
                for kw in kws:
                    pos = t.find(kw)
                    if 0 <= pos < best_pos:
                        best, best_pos = style, pos
            if best:
                return best
        return ""

    @staticmethod
    def _user_texts(history: list, newest_first: bool = True) -> list:
        """从对话历史里取出用户说过的话(dict 或 ChatMessage 均可)。"""
        out = []
        for m in history or []:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            if role == "user":
                out.append(content or "")
        return list(reversed(out)) if newest_first else out

    # ---------------- 提醒配置 ----------------
    def update_reminder(self, rid: int, hour=None, minute=None, enabled=None) -> dict:
        ok = self.db.update_reminder(int(rid),
                                     None if hour is None else int(hour),
                                     None if minute is None else int(minute),
                                     None if enabled is None else (1 if enabled else 0))
        goal = self.get_current_goal()
        return {"ok": ok, "reminders": self.db.list_reminders(goal.id) if goal else []}

    def delete_reminder(self, rid: int) -> dict:
        ok = self.db.delete_reminder(int(rid))
        goal = self.get_current_goal()
        return {"ok": ok, "reminders": self.db.list_reminders(goal.id) if goal else []}

    # ---------------- 对话 ----------------
    ADJUST_STRONG = [
        "调整计划", "改计划", "改一下计划", "改下计划", "重新安排", "重新排",
        "简化计划", "把", "降到", "减到", "加到", "改成", "删掉", "去掉",
        "加量", "减量", "减少量", "降低难度", "增加难度", "难度降低", "难度增加",
        "减少任务", "增加任务", "计划太", "任务太",
    ]
    ADJUST_WEAK = ["太难", "太松", "太简单", "太累", "太满", "太重", "太死板"]

    @classmethod
    def _is_adjust_intent(cls, text: str) -> bool:
        """判断用户是不是在对话里要求修改已设定好的计划。"""
        t = (text or "").lower()
        if not t:
            return False
        if any(k in t for k in cls.ADJUST_STRONG):
            return True
        # 弱信号必须同时提到「计划/任务」,避免把打卡吐槽(如「今天太难了」)误判
        if any(k in t for k in cls.ADJUST_WEAK) and ("计划" in t or "任务" in t):
            return True
        return False

    def chat(self, message: str, image: str | None = None) -> dict:
        goal = self.get_current_goal()
        if not goal or goal.status != "active":
            return {"reply": "先告诉我你的目标吧~ 在「目标」里新建一个,我们会一起把它聊清楚。", "no_goal": True}
        message = (message or "").strip()
        if not message and not image:
            return {"reply": "我在呢,说说看?"}
        # 在对话里直接改计划:意图命中且目标已生成计划,就走调整流程
        if not image and self._is_adjust_intent(message) and self.db.get_plan(goal.id):
            self.db.add_message(goal.id, ChatMessage(role="user", content=message, ts=time.time()))
            r = self.adapt_plan(message)
            if r.get("error"):
                return {"reply": r["error"]}
            reply = (r.get("reply")
                     or ("已按你的要求调整了计划,去计划页看看新安排 👀" if r.get("changed")
                         else "我看了下,当前计划已经覆盖了这个点,暂时没改。说得更具体些我就动手,比如「把每日阅读从30分钟减到15分钟」。"))
            r["reply"] = reply
            return r
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
            return {"error": f"调整失败:{e}"}
        if changed:
            # 按任务标题继承原打卡状态,调整计划不清空已完成的勾
            old_state = {t.title: (t.done, t.last_done) for p in plan for t in p.tasks}
            for p in new_plan:
                for t in p.tasks:
                    if t.title in old_state:
                        t.done, t.last_done = old_state[t.title]
            self.db.save_plan(goal.id, new_plan)
            self._auto_reminders(goal, new_plan)
            msg = f"已按你的反馈「{feedback}」调整了计划,去计划页看看新的安排。"
            self.db.add_message(goal.id, ChatMessage(role="assistant", content=msg, ts=time.time()))
        else:
            msg = (f"我看了你的反馈「{feedback}」,感觉当前计划已经覆盖了,暂时没改。"
                   "如果你确实想改,说得再具体点,比如「把每日阅读从30分钟减到15分钟」「第2阶段太难,降低强度」。")
            self.db.add_message(goal.id, ChatMessage(role="assistant", content=msg, ts=time.time()))
        return {**self.get_state(), "changed": changed, "reply": msg}

    # ---------------- 打卡 ----------------
    def checkin(self, mood: str = "", note: str = "", done_task_ids: list | None = None) -> dict:
        goal = self.get_current_goal()
        if not goal or goal.status != "active":
            return {"error": "没有进行中的目标"}
        done_ids = list(done_task_ids or [])
        ci = CheckIn(id=None, goal_id=goal.id, ts=time.time(), mood=mood, note=note,
                     done_task_ids=",".join(done_ids))
        self.db.add_checkin(ci)
        # 打卡即勾选任务(与计划页状态打通)
        plan = self._normalized_plan(goal.id)
        now = time.time()
        # 分母只算「今天该做的任务」,不能把未来阶段的任务也算进来
        today_ids = {i["task_id"] for i in self._today_items(goal, _dt.date.today(), plan)}
        titles_done, total = [], len(today_ids)
        changed = False
        for p in plan:
            for t in p.tasks:
                if t.id in done_ids:
                    titles_done.append(t.title)
                    if t.id not in today_ids:
                        total += 1  # 用户额外完成了非今日任务,计入分母
                    if not t.done:
                        t.done, t.last_done, changed = True, now, True
        if changed:
            self.db.save_plan(goal.id, plan)
        # streak 无押金也累计(deposit=0 行)
        self.db.ensure_membership(goal.id, 0, 0)
        today = _dt.date.today().strftime("%Y-%m-%d")
        streak = self.db.record_checkin_streak(goal.id, today)
        # AI 教练即时反馈(每日情绪价值峰值)
        persona = get_persona(self.persona_id)
        try:
            reply = self.engine.checkin_reply(mood, note, titles_done, total, goal, persona, streak)
        except Exception as e:
            logger.warning("checkin_reply 失败,回退模板:%s", e)
            reply = build_engine({"engine": "mock"}).checkin_reply(
                mood, note, titles_done, total, goal, persona, streak)
        self.db.add_message(goal.id, ChatMessage(
            role="user", content=f"【打卡】{ {'great':'😄 状态不错','ok':'😐 一般','bad':'😫 很累'}.get(mood,'') } 完成 {len(titles_done)}/{total}" + (f",备注:{note}" if note else ""),
            ts=time.time()))
        self.db.add_message(goal.id, ChatMessage(role="assistant", content=reply, ts=time.time()))
        # 每 3 次打卡自动生成教练观察
        try:
            self._maybe_observe(goal, streak)
        except Exception as e:
            logger.warning("观察摘要生成失败:%s", e)
        return {**self.get_state(), "streak": streak, "reply": reply}

    def _maybe_observe(self, goal: Goal, streak: dict):
        checkins = self.db.recent_checkins(goal.id, 12)
        if not checkins or len(checkins) % 3 != 0:
            return
        today = _dt.date.today().isoformat()
        if self.db.summary_exists(goal.id, "observation", today):
            return
        moods = [c.mood for c in checkins[:6] if c.mood]
        notes = [c.note for c in checkins[:6] if c.note]
        text = ""
        try:
            if hasattr(self.engine, "client"):
                persona = get_persona(self.persona_id)
                sys_p = (f"{persona.system_prompt}\n你是长期教练。根据用户最近的打卡记录,"
                         "写 2~3 句「长期观察」:指出趋势(状态/习惯变化)、值得表扬的点、一个改进建议。"
                         "口吻真诚,像认识他很久。直接输出文本。")
                user_p = (f"目标:{goal.title}\n最近心情序列:{','.join(moods) or '无'}\n"
                          f"最近备注:{' | '.join(notes) or '无'}\n"
                          f"连续打卡:{streak.get('consecutive', 0)} 天,累计 {len(checkins)} 次")
                text = self.engine.client.chat(
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                    max_tokens=300)
        except Exception as e:
            logger.warning("LLM 观察失败:%s", e)
        if not text:
            good = sum(1 for m in moods if m == "great")
            text = (f"已陪你打卡 {len(checkins)} 次,连续 {streak.get('consecutive', 0)} 天。"
                    + ("最近状态不错,保持这个节奏。" if good >= len(moods) / 2 else
                       "最近状态起伏有点大,记得把任务拆小一点,先保完成率。"))
        self.db.upsert_summary(goal.id, "observation", today, {"text": text})

    def _normalized_plan(self, goal_id: int) -> list:
        """读取计划并做「每日任务跨天重置」:daily 任务若非今天完成,自动取消勾选。"""
        plan = self.db.get_plan(goal_id)
        today = _dt.date.today()
        changed = False
        for p in plan:
            for t in p.tasks:
                if t.done and t.frequency == "daily":
                    done_day = _dt.date.fromtimestamp(t.last_done) if t.last_done else None
                    if done_day != today:
                        t.done, changed = False, True
                elif t.done and t.frequency == "weekly" and t.last_done:
                    # 每周任务:完成超过 7 天自动重置
                    if (time.time() - t.last_done) > 7 * 86400:
                        t.done, changed = False, True
        if changed:
            self.db.save_plan(goal_id, plan)
        return plan

    def toggle_task(self, task_id: str) -> dict:
        goal = self.get_current_goal()
        if not goal:
            return {"error": "no goal"}
        plan = self._normalized_plan(goal.id)
        found = None
        for p in plan:
            for t in p.tasks:
                if t.id == task_id:
                    t.done = not t.done
                    t.last_done = time.time() if t.done else 0.0
                    found = t
        if found is None:
            return {"error": "task not found"}
        self.db.save_plan(goal.id, plan)
        return {**self.get_state(), "ok": True, "task": found.to_dict()}

    # ---------------- 押金挑战(模拟账本) ----------------
    def open_deposit(self, deposit: int = 99, threshold: int = 21) -> dict:
        goal = self.get_current_goal()
        if not goal or goal.status != "active":
            return {"error": "先激活一个目标再开通挑战"}
        try:
            deposit = max(1, int(deposit)); threshold = max(3, int(threshold))
        except (TypeError, ValueError):
            return {"error": "金额/天数不合法"}
        m = self.db.open_deposit(goal.id, deposit, threshold)
        self.db.add_message(goal.id, ChatMessage(
            role="assistant",
            content=f"🔥 挑战开启!{deposit} 元押金已托管(模拟)。连续打卡 {threshold} 天全额退还——从今天开始,我盯着你。",
            ts=time.time()))
        return {**self.get_state(), "membership": m}

    # ---------------- 今日视图(跨目标聚合) ----------------
    def _today_items(self, goal: Goal, today: _dt.date, plan: list | None = None) -> list:
        """单个目标在 today 这一天应该出现的任务(按阶段区间 + 频率过滤)。"""
        plan = plan if plan is not None else self._normalized_plan(goal.id)
        base = _dt.datetime.fromtimestamp(goal.created_at)
        items = []
        for p in plan:
            start_w, end_w = self._parse_weeks(p.weeks)
            pstart = (base + _dt.timedelta(weeks=start_w)).date()
            pend = (base + _dt.timedelta(weeks=end_w)).date()
            if not (pstart <= today <= pend):
                continue
            for t in p.tasks:
                if t.frequency == "daily":
                    show = True
                elif t.frequency == "weekly":
                    show = today.weekday() == self._weekly_weekday(t.id)
                else:  # once:未完成就一直显示
                    show = not t.done
                if show:
                    items.append({
                        "goal_id": goal.id, "goal_title": goal.title,
                        "task_id": t.id, "title": t.title, "detail": t.detail,
                        "frequency": t.frequency, "duration_min": t.duration_min,
                        "done": t.done, "phase": p.title,
                    })
        return items

    def get_today(self) -> dict:
        today = _dt.date.today()
        today_str = today.isoformat()
        items = []
        for g in self.db.list_goals(include_draft=False):
            items.extend(self._today_items(g, today))
        cur = self.get_current_goal()
        return {
            "date": today_str,
            "weekday": "一二三四五六日"[today.weekday()],
            "items": items,
            "current_goal_id": cur.id if cur else None,
        }

    def toggle_today_task(self, goal_id: int, task_id: str) -> dict:
        """今日视图直接勾选(可能不是当前目标)。"""
        plan = self._normalized_plan(int(goal_id))
        found = None
        for p in plan:
            for t in p.tasks:
                if t.id == task_id:
                    t.done = not t.done
                    t.last_done = time.time() if t.done else 0.0
                    found = t
        if found is None:
            return {"error": "task not found"}
        self.db.save_plan(int(goal_id), plan)
        return {"ok": True, "today": self.get_today()}

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
            wd = API._weekly_weekday(t.id)
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
        plan = self._normalized_plan(goal.id) if goal else []
        checkins = self.db.recent_checkins(goal.id, 10) if goal else []
        history = [m.to_dict() for m in self.db.chat_history(goal.id)] if goal else []
        reminders = self.db.list_reminders(goal.id) if goal else []
        try:
            membership = self.db.get_membership(goal.id) if goal else None
            if membership:
                membership = dict(membership)
                membership["checked_today"] = (
                    membership.get("last_checkin_date") == _dt.date.today().isoformat())
        except Exception:
            membership = None
        try:
            summaries = self.db.get_summaries(goal.id) if goal else []
        except Exception:
            summaries = []
        # 共创进度( Checklist):哪些维度已收集,驱动前端进度条
        onboarding_progress = []
        if goal and onboarding:
            prof = goal.profile_dict()
            for key, label in ONBOARD_DIMS:
                v = prof.get(key) or (prof.get("habits") if key == "habit" else "")
                onboarding_progress.append({"key": key, "label": label, "done": bool(str(v).strip())})
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
            "today": self.get_today(),
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
    def test_llm(self) -> dict:
        """设置页「测试连接」:直接调一次模型,不写入任何历史。"""
        if not hasattr(self.engine, "client"):
            return {"ok": False, "engine": type(self.engine).__name__,
                    "message": "当前是内置模板模式(未配置 Key 或引擎选了 mock)"}
        try:
            t0 = time.time()
            out = self.engine.client.chat(
                [{"role": "user", "content": "回复两个字:在线"}], max_tokens=10)
            return {"ok": True, "engine": "LLM", "latency_ms": int((time.time() - t0) * 1000),
                    "message": (out or "").strip()[:40] or "(空响应)"}
        except Exception as e:
            return {"ok": False, "engine": "LLM", "message": f"连接失败:{e}"}

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
