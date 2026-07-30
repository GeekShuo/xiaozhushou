"""AI 引擎层。

设计要点:
- ``AIEngine`` 是统一接口,任何实现都能插进来。
- ``MockAIEngine``:知识库 + 规则,作为离线兜底(无 API Key / 调用失败自动回退)。
- ``LLMAIEngine``:调用真实大模型(OpenAI / DeepSeek / 通义 / 本地 Ollama 等)。

切换由 ``build_engine(config)`` 根据配置决定,无需改前端与数据层。
"""
from __future__ import annotations

import json
from typing import Optional

from .persona import Persona, get_persona
from .models import Goal, Phase, Task, CheckIn
from .knowledge import normalize, lookup_plan

try:
    from .llm_client import LLMClient, LLMError, resolve_llm_config, strip_json
except Exception:  # 极端情况下也能跑桌面/测试
    LLMClient = None  # type: ignore
    LLMError = Exception  # type: ignore

    def resolve_llm_config(_=None):
        return "", "", "", 0.7, False

    def strip_json(_):
        return {}


FREQUENCIES = ("daily", "weekly", "once")


class AIEngine:
    """统一接口。切换实现时子类只需实现这些方法。"""

    # 目标共创:多轮澄清后产出量化计划
    # 返回 (ask: 给用户的话, profile: 收集到的画像 dict, plan: list[Phase]|None)
    def onboard_goal(self, text: str, history: list, persona: Persona, profile: dict | None = None) -> tuple:
        raise NotImplementedError

    def decompose_goal(self, text: str, profile: Optional[dict] = None) -> list[Phase]:
        raise NotImplementedError

    def chat(self, message: str, history: list, goal: Goal, plan: list[Phase],
             persona: Persona, checkins: list, image: Optional[str] = None) -> str:
        raise NotImplementedError

    def adapt_plan(self, feedback: str, goal: Goal, plan: list[Phase], persona: Persona) -> tuple:
        raise NotImplementedError

    # 打卡后的即时反馈(每日情绪价值峰值)
    def checkin_reply(self, mood: str, note: str, done_titles: list, total: int,
                      goal: Goal, persona: Persona, streak: dict | None = None) -> str:
        raise NotImplementedError


# 共创 Checklist:决定「信息是否收齐」的维度与展示顺序
ONBOARD_DIMS = [
    ("baseline", "当前基线"),
    ("target", "目标数字+时间"),
    ("habit", "习惯/痛点"),
    ("constraint", "约束条件"),
    ("motivation_style", "动力风格"),
]
ONBOARD_KEYS = ("baseline", "habits", "target", "constraint", "motivation_style", "idol")


ONBOARD_ORDER = ["baseline", "target", "habit", "constraint", "motivation_style"]


def _pval(p: dict, k: str) -> str:
    """取画像字段值。habit/habits 两种拼法(LLM schema 用 habits,checklist 用 habit)视为同一字段。"""
    v = (p.get(k) or "").strip()
    if not v and k == "habit":
        v = (p.get("habits") or "").strip()
    return v


def first_missing_dim(p: dict) -> str:
    """按提问顺序返回第一个未收集的维度 key;都齐了返回空串。"""
    p = p or {}
    return next((k for k in ONBOARD_ORDER if not _pval(p, k)), "")


def _onboard_done(p: dict) -> bool:
    p = p or {}
    return (bool(_pval(p, "baseline")) and bool(_pval(p, "target"))
            and (bool(_pval(p, "habit")) or bool(_pval(p, "constraint")))
            and bool(_pval(p, "motivation_style")))


def _next_question(p: dict) -> str:
    first = first_missing_dim(p) or None
    asks = {
        "baseline": "咱们先从「起点」聊起:你现在的状态是怎样的?(比如体重/身高/目前水平/每天大概怎么过)",
        "target": "想达成什么具体目标?最好带个数字和期限,比如「3 个月减 7 斤」「每天能读 20 页」。",
        "habit": "你现在有什么习惯或痛点?比如爱喝奶茶、久坐、晚上才有力气动。",
        "constraint": "你每天大概能抽出多少时间?预算有限吗?工作节奏怎么样?",
        "motivation_style": "你希望我以什么身份陪你?🤝运动搭子 / ⭐偶像 / 📏严师 / 🫶温柔陪伴 —— 选一个我最来劲。",
    }
    return asks.get(first, "信息差不多齐了,我帮你生成专属计划 ✅")


# 动力风格 → 语气指令。用户在共创里亲口选的风格,优先级高于人格设定。
STYLE_DIRECTIVES = {
    "workout_buddy": (
        "【语气规则·最高优先级|运动搭子】你是和用户一起练的搭子,多说「咱们」「一起」,"
        "有并肩作战和击掌的感觉;可以喊「兄弟/姐妹」。不要端着当老师。"
    ),
    "idol": (
        "【语气规则·最高优先级|偶像】用用户偶像的口吻和气质说话{idol},"
        "像榜样亲自看见了他的努力,带一点距离感的珍视,不要油腻。"
    ),
    "strict": (
        "【语气规则·最高优先级|严师】直接、不绕弯:先肯定做到的,再明确点出没做到的,"
        "并给出明天的硬性要求。不要安慰性套话。"
    ),
    "warm": (
        "【语气规则·最高优先级|温柔陪伴】先共情他的感受,再肯定努力,语气温和克制。"
        "禁止使用「兄弟」「姐妹」「咱们练起来」「干就完了」「别废话」这类硬汉/搭子口吻,"
        "也不要用命令句和感叹号连打;称呼用「你」。"
    ),
}


def _plan_signature(phases) -> str:
    """计划的内容签名:只看阶段与任务的实质内容,忽略 done/last_done 等运行时状态。"""
    return json.dumps([
        {"title": p.title, "weeks": p.weeks,
         "tasks": [{"title": t.title, "detail": t.detail,
                    "frequency": t.frequency, "duration_min": t.duration_min}
                   for t in p.tasks]}
        for p in phases], ensure_ascii=False, sort_keys=True)


def style_directive(profile: dict | None) -> str:
    """根据目标画像里的动力风格,生成压过人格设定的语气指令。"""
    prof = profile or {}
    style = (prof.get("motivation_style") or "").strip()
    tpl = STYLE_DIRECTIVES.get(style)
    if not tpl:
        return ""
    idol = (prof.get("idol") or "").strip()
    text = tpl.format(idol=f"(偶像:{idol})" if idol else "") if "{idol}" in tpl else tpl
    return text + "\n若本规则与上面的人格设定冲突,一律以本规则为准。"


# --------------------------------------------------------------------------
# 真实大模型实现
# --------------------------------------------------------------------------
class LLMAIEngine(AIEngine):
    def __init__(self, config: dict | None = None):
        _, _, _, _, vision = resolve_llm_config(config)
        self.vision = vision
        self.config = config or {}
        self._client = None  # 懒加载,避免无网络时构造失败

    @property
    def client(self):
        if self._client is None:
            base_url, model, api_key, temperature, _ = resolve_llm_config(self.config)
            if not api_key:
                raise LLMError("缺少 API Key")
            self._client = LLMClient(base_url, api_key, model, temperature)
        return self._client

    # ---- 多轮共创:只负责澄清收集,产出计划交给 decompose_goal ----
    def onboard_goal(self, text: str, history: list, persona: Persona, profile: dict | None = None):
        """返回 (ask, profile, done)。

        设计要点:
        - 主调用让模型自然提问并顺带抽取画像(JSON);
        - 若模型开小差(只聊不输出 JSON / 没抽到画像),用确定性下一问 + 独立抽取兜底,
          保证共创始终向前推进,绝不卡在「继续,再多告诉我一点?」。
        """
        profile = dict(profile or {})
        profile_hint = ""
        if profile:
            profile_hint = "\n已收集到的用户画像(据此判断还缺什么,凑齐即可 done=true):\n" + json.dumps(profile, ensure_ascii=False)
        system = (
            f"{persona.system_prompt}\n\n"
            "你是「目标共创教练」。用户会先抛出一个模糊的目标(如「减肥」)。\n"
            "请通过多轮对话把目标澄清并个性化,像一个真人教练一样自然、有温度。\n\n"
            "澄清时一次只问一个问题,建议顺序:\n"
            "1) 当前基线(体重/身高/现状/起点)\n"
            "2) 目标的具体数字与时间(想减多少、几周/月达成)\n"
            "3) 已有习惯与痛点(运动、饮食、时间安排)\n"
            "4) 约束(工作强度/久坐/可运动时长/预算)\n"
            "5) 动力风格:你希望我以什么身份陪你?可选——\n"
            "   【运动搭子】像朋友一起练、互相打卡;\n"
            "   【偶像】用你崇拜的榜样/偶像口吻激励你(可顺带问一句你偶像是谁);\n"
            "   【严师】严格督促;\n"
            "   【温柔陪伴】温和鼓励。\n\n"
            "当 基线 / 目标数字+时间 / 至少一个习惯或约束 / 动力风格 大体收集齐,把 done 设为 true,\n"
            "ask 写一句收尾(如「好,我帮你生成专属计划」)。**不要在这里输出计划**,计划会由另一段逻辑生成。\n\n"
            "【重要】profile 里**只填写用户本轮或之前明确说出的内容,不要臆测、不要替用户做决定**;"
            "没提到的字段一律留空字符串。例如用户只说了现状,就不要去猜目标数字或习惯。\n\n"
            "每一轮**只**输出如下 JSON(不要多余文字,也不要解释):\n"
            '{"ask":"下一个问题或收尾语","profile":{"baseline":"","habits":"","target":"","constraint":"","motivation_style":"workout_buddy|idol|strict|warm","idol":""},"done":false}'
            + profile_hint
        )
        msgs = [{"role": "system", "content": system}]
        msgs.append({"role": "user", "content": f"我的目标:{text}"})
        for m in history:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "user")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            msgs.append({"role": role, "content": str(content)})
        try:
            raw = self.client.chat(msgs, json_mode=True, max_tokens=600)
            data = strip_json(raw)
            ask = (data.get("ask") or "").strip() if isinstance(data, dict) else ""
            new_profile = (data.get("profile") or {}) if isinstance(data, dict) else {}
            done = bool(data.get("done")) if isinstance(data, dict) else False
        except Exception:
            ask, new_profile, done = "", {}, False

        merged = {**profile, **{k: v for k, v in new_profile.items() if v}}

        # 兜底:模型没给问题 或 完全没抽到画像 → 独立抽取 + 确定性下一问
        got_any = any((merged.get(k) or "").strip() for k in ONBOARD_KEYS)
        if not ask or not got_any:
            extracted = self._extract_profile(text, history, persona, merged)
            if extracted:
                merged = {**merged, **extracted}
            ask = ask or _next_question(merged)
            done = _onboard_done(merged)

        # 信息收齐即以 checklist 收尾(即使模型没显式 done)
        if _onboard_done(merged):
            done = True
            if not ask:
                ask = "信息齐了,我这就帮你生成专属计划 ✅"
        return ask, merged, done

    def _extract_profile(self, text: str, history: list, persona: Persona, base: dict) -> dict:
        """把整段对话可靠地抽取成结构化画像(JSON)。仅在主调用失败时兜底使用。"""
        sys_p = (
            f"{persona.system_prompt}\n你是信息抽取器。从下面的对话中抽取用户的「目标画像」,"
            "只输出 JSON,不要多余文字:\n"
            '{"baseline":"","habits":"","target":"","constraint":"","motivation_style":"workout_buddy|idol|strict|warm","idol":""}'
            "\n没提到的字段留空字符串。"
        )
        msgs = [{"role": "system", "content": sys_p},
                {"role": "user", "content": f"目标:{text}"}]
        for m in history:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "user")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            msgs.append({"role": role, "content": str(content)})
        try:
            raw = self.client.chat(msgs, json_mode=True, max_tokens=600)
            d = strip_json(raw)
            if isinstance(d, dict):
                return {k: (d.get(k) or "") for k in ONBOARD_KEYS}
        except Exception:
            pass
        return {}

    # ---- 一次性拆解(兜底 / 一键生成) ----
    def decompose_goal(self, text: str, profile: Optional[dict] = None) -> list[Phase]:
        profile = profile or {}
        profile_hint = ""
        if profile:
            profile_hint = "用户画像:" + json.dumps(profile, ensure_ascii=False) + "\n"
        system = (
            f"{get_persona('warm_mentor').system_prompt}\n\n"
            "你是目标拆解专家。把用户的宏大目标拆成 2~4 个阶段,每阶段 3~5 个可量化任务。\n"
            f"{profile_hint}"
            "只输出 JSON:\n"
            '{"phases":[{"title":"","weeks":"第1-2周","rationale":"","tasks":[{"title":"","detail":"含量化指标","frequency":"daily|weekly|once","duration_min":30}]}]}'
        )
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": f"目标:{text}"}]
        raw = self.client.chat(msgs, json_mode=True, max_tokens=1400)
        data = strip_json(raw)
        phases = self._build_phases(data.get("phases") if isinstance(data, dict) else None)
        if phases:
            return phases
        raise LLMError("计划解析失败")

    # ---- 对话 ----
    def chat(self, message, history, goal, plan, persona, checkins, image=None) -> str:
        system = self._build_system(persona, goal, plan, checkins)
        msgs = [{"role": "system", "content": system}]
        for m in history[-16:]:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "user")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            msgs.append({"role": role, "content": str(content)})
        if image and self.vision:
            msgs.append({"role": "user", "content": [
                {"type": "text", "text": message or "（用户发来一张图片）"},
                {"type": "image_url", "image_url": {"url": image}},
            ]})
        else:
            suffix = ""
            if image and not self.vision:
                suffix = "\n（注:当前模型不支持看图,用户发来一张图片但你无法识别,请基于文字继续。）"
            msgs.append({"role": "user", "content": (message or "") + suffix})
        return self.client.chat(msgs, max_tokens=900)

    # ---- 计划调整 ----
    def adapt_plan(self, feedback, goal, plan, persona) -> tuple:
        sys_p = (
            f"{persona.system_prompt}\n"
            "你是计划调整助手。用户对当前计划提出了修改要求,你必须据此把计划真正改出来。\n"
            "规则:\n"
            "1) 严格执行用户要求(增删任务、调频率/时长/难度、改阶段划分等),输出修改后的**完整**计划;\n"
            "2) 用户没提到的部分尽量保持原样;\n"
            "3) 仅当反馈与计划完全无关(纯闲聊)时,才允许原样返回;\n"
            "4) 只输出 JSON,不要任何解释:\n"
            '{"phases":[{"title":"","weeks":"第1-2周","rationale":"为什么这样调",'
            '"tasks":[{"title":"","detail":"含量化指标","frequency":"daily|weekly|once","duration_min":30}]}]}'
        )
        plan_json = json.dumps([p.to_dict() for p in plan], ensure_ascii=False)
        msgs = [{"role": "system", "content": sys_p},
                {"role": "user", "content": f"目标:{goal.title}\n当前计划:\n{plan_json}\n\n我的修改要求:{feedback}"}]
        raw = self.client.chat(msgs, json_mode=True, max_tokens=2400)
        data = strip_json(raw)
        if isinstance(data, dict) and data.get("phases"):
            new_phases = self._build_phases(data["phases"])
            if new_phases:
                # changed 不信模型自述,实际内容 diff 才算数(忽略打卡状态等运行时字段)
                changed = _plan_signature(new_phases) != _plan_signature(plan)
                return changed, new_phases
        raise LLMError("调整结果解析失败,请再试一次或把要求说得更具体")

    # ---- 打卡即时反馈 ----
    def checkin_reply(self, mood, note, done_titles, total, goal, persona, streak=None) -> str:
        streak = streak or {}
        prof = goal.profile_dict() if goal else {}
        mood_txt = {"great": "状态不错", "ok": "状态一般", "bad": "状态很差/很累"}.get(mood, "未知")
        sys_p = (
            f"{persona.system_prompt}\n"
            "用户刚完成今日打卡。请给一段 2~4 句的走心反馈:\n"
            "1) 针对他完成/没完成的任务给具体回应(不要泛泛地说'加油');\n"
            "2) 如果他备注了内容,必须回应备注;\n"
            "3) 结合连续打卡天数制造成就感或紧迫感;\n"
            "4) 最后给明天一个最小行动建议。\n"
            "直接输出文本,不要 JSON、不要列表符号。\n"
            + style_directive(prof)
        )
        user_p = (
            f"目标:{goal.title if goal else ''}\n"
            f"今日心情:{mood_txt}\n"
            f"完成任务({len(done_titles)}/{total}):{'、'.join(done_titles) if done_titles else '一项都没完成'}\n"
            f"备注:{note or '无'}\n"
            f"连续打卡:{streak.get('consecutive', 0)} 天"
            + (f"(距押金退还还差 {max(0, streak.get('threshold', 0) - streak.get('consecutive', 0))} 天)" if streak.get("deposit") else "")
        )
        return self.client.chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
            max_tokens=400)

    # ---- 内部工具 ----
    @staticmethod
    def _build_phases(phases_raw) -> list[Phase]:
        if not isinstance(phases_raw, list):
            return []
        phases = []
        for i, p in enumerate(phases_raw, 1):
            if not isinstance(p, dict):
                continue
            tasks = []
            for j, t in enumerate(p.get("tasks", []), 1):
                if not isinstance(t, dict):
                    continue
                freq = t.get("frequency", "daily")
                if freq not in FREQUENCIES:
                    freq = "daily"
                tasks.append(Task(
                    id=f"p{i}t{j}",
                    title=str(t.get("title", "任务"))[:60],
                    detail=str(t.get("detail", ""))[:500],
                    frequency=freq,
                    duration_min=int(t.get("duration_min", 0) or 0),
                ))
            if not tasks:
                continue
            phases.append(Phase(
                title=str(p.get("title", f"阶段{i}"))[:60],
                weeks=str(p.get("weeks", f"第{i}阶段"))[:40],
                rationale=str(p.get("rationale", ""))[:300],
                tasks=tasks,
            ))
        return phases

    @staticmethod
    def _build_system(persona: Persona, goal, plan, checkins) -> str:
        persona = persona or get_persona("warm_mentor")
        lines = [persona.system_prompt]
        prof = goal.profile_dict() if goal else {}
        if prof:
            lines.append("用户画像:" + json.dumps(prof, ensure_ascii=False))
        if goal:
            lines.append(f"当前目标:{goal.title}")
        if plan:
            plan_txt = "\n".join(
                f"· {p.title}({p.weeks}): " + " / ".join(t.title for t in p.tasks)
                for p in plan
            )
            lines.append("当前计划:\n" + plan_txt)
        if checkins:
            last = checkins[0]
            lines.append(f"用户最近一次打卡:心情={last.mood or '无'} 备注={last.note or '无'}")
        lines.append("用简体中文、像真人教练一样自然地回复,必要时给出具体可执行的建议。")
        sd = style_directive(prof)
        if sd:
            lines.append(sd)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# 离线兜底实现(无 Key / 调用失败时使用)
# --------------------------------------------------------------------------
class MockAIEngine(AIEngine):
    def onboard_goal(self, text, history, persona, profile=None):
        profile = dict(profile or {"baseline": "", "habits": "", "target": "",
                                   "constraint": "", "motivation_style": "workout_buddy", "idol": ""})
        # 离线模式下,把用户说的话尽量填进画像(关键词启发),让「跳过共创」也能生成像样计划
        if history:
            last_user = ""
            for m in reversed(history):
                c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                if (m.get("role") if isinstance(m, dict) else getattr(m, "role", "")) == "user":
                    last_user = c
                    break
            if last_user:
                if not profile["baseline"] and any(k in last_user for k in ("身高", "体重", "kg", "cm", "目前的", "现在")):
                    profile["baseline"] = last_user[:60]
                if not profile["target"] and any(k in last_user for k in ("减", "瘦", "读", "跑", "学会", "达成", "天", "周", "月")):
                    profile["target"] = last_user[:60]
                if not profile["habit"] and any(k in last_user for k in ("习惯", "久坐", "奶茶", "运动", "每天", "时间")):
                    profile["habit"] = last_user[:60]
                if not profile["constraint"] and any(k in last_user for k in ("预算", "加班", "工作", "没时间", "钱")):
                    profile["constraint"] = last_user[:60]
                if not profile["motivation_style"]:
                    if "偶像" in last_user:
                        profile["motivation_style"] = "idol"
                    elif "严" in last_user or "督促" in last_user:
                        profile["motivation_style"] = "strict"
                    elif "温柔" in last_user:
                        profile["motivation_style"] = "warm"
                    else:
                        profile["motivation_style"] = "workout_buddy"
        ask = _next_question(profile)
        done = _onboard_done(profile)
        if done:
            ask = "信息齐了,我这就帮你生成专属计划 ✅"
        return ask, profile, done

    def decompose_goal(self, text, profile=None):
        return lookup_plan(text)

    def chat(self, message, history, goal, plan, persona, checkins, image=None):
        persona = persona or get_persona("warm_mentor")
        msg = (message or "").strip()
        if not msg:
            return "我在的,说说看?"
        if any(k in msg for k in ("没动力", "想放弃", "好累", "坚持不下去")):
            return f"抱抱你💛 今天状态不好很正常。{persona.name}陪你,咱们把目标拆小一点,先做一件 5 分钟就能完成的事,好吗?"
        if any(k in msg for k in ("怎么", "怎么办", "建议")):
            return f"针对「{goal.title if goal else '这个目标'}」,建议先看今天计划里最容易的一项,马上做起来。需要我把它再拆细吗?"
        return f"收到~ 关于「{goal.title if goal else '你的目标'}」,我会陪你一步步来。要不要先完成今天的第一个小任务?"

    def adapt_plan(self, feedback, goal, plan, persona):
        return False, plan

    def checkin_reply(self, mood, note, done_titles, total, goal, persona, streak=None):
        streak = streak or {}
        n = len(done_titles)
        consec = streak.get("consecutive", 0)
        if n == 0:
            base = "今天一项没完成也来打卡了,这份诚实很难得。明天先从最小的一件事开始,5 分钟就好。"
        elif n == total:
            base = f"全部 {total} 项完成!干净利落。"
        else:
            base = f"完成了 {n}/{total} 项,不错的进度。差的那几项,明天优先做。"
        if consec >= 2:
            base += f" 已经连续打卡 {consec} 天了,别断!"
        if mood == "bad":
            base = "看到你今天状态不太好。" + base + " 状态差的日子,完成比完美重要。"
        return base


# --------------------------------------------------------------------------
# 引擎构建(按配置自动选择 + 降级)
# --------------------------------------------------------------------------
def build_engine(config: dict | None = None):
    """根据配置返回可用引擎。

    - engine='llm':强制真实模型(无 Key 直接报错,便于发现配置问题)
    - engine='mock':仅内置模板
    - engine='auto'(默认):有 Key 用真实模型,否则用模板兜底
    """
    config = config or {}
    mode = config.get("engine", "auto")
    _, _, api_key, _, _ = resolve_llm_config(config)
    has_key = bool(api_key)

    if mode == "mock":
        return MockAIEngine()
    if mode == "llm":
        if not has_key:
            raise RuntimeError("已设置 engine=llm 但缺少 API Key,请到 ⚙️ 设置 填写。")
        return LLMAIEngine(config)
    # auto
    if has_key:
        try:
            return LLMAIEngine(config)
        except Exception:
            return MockAIEngine()
    return MockAIEngine()
