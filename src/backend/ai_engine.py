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
        profile = profile or {}
        profile_hint = ""
        if profile:
            profile_hint = "\n已收集到的用户画像(据此判断还缺什么,凑齐即可 done=true):\n" + json.dumps(profile, ensure_ascii=False)
        system = (
            f"{persona.system_prompt}\n\n"
            "你是「目标共创教练」。用户会先抛出一个模糊的目标(如「减肥」)。\n"
            "请通过多轮对话把目标澄清并个性化。\n\n"
            "澄清时自然、一次只问一个问题,建议顺序:\n"
            "1) 当前基线(体重/身高/现状/起点)\n"
            "2) 已有习惯与痛点(运动、饮食、时间安排)\n"
            "3) 目标的具体数字与时间(想减多少、几周/月达成)\n"
            "4) 约束(工作强度/久坐/可运动时长/预算)\n"
            "5) 动力风格:你希望我以什么身份陪你?可选——\n"
            "   【运动搭子】像朋友一起练、互相打卡;\n"
            "   【偶像】用你崇拜的榜样/偶像口吻激励你(可顺带问一句你偶像是谁);\n"
            "   【严师】严格督促;\n"
            "   【温柔陪伴】温和鼓励。\n\n"
            "当 基线 / 目标数字+时间 / 至少一个习惯或约束 / 动力风格 大体收集齐,把 done 设为 true,\n"
            "ask 写一句收尾(如「好,我帮你生成专属计划」)。**不要在这里输出计划**,计划会由另一段逻辑生成。\n\n"
            "每一轮**只**输出如下 JSON(不要多余文字):\n"
            '{"ask":"下一个问题或收尾语","profile":{"baseline":"","habits":"","target":"","constraint":"","motivation_style":"workout_buddy|idol|strict|warm","idol":""},"done":false}'
            + profile_hint
        )
        msgs = [{"role": "system", "content": system}]
        msgs.append({"role": "user", "content": f"我的目标:{text}"})
        for m in history:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "user")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            msgs.append({"role": role, "content": str(content)})
        raw = self.client.chat(msgs, json_mode=True, max_tokens=600)
        data = strip_json(raw)
        if not isinstance(data, dict):
            data = {}
        merged = {**profile, **(data.get("profile") or {})}
        done = bool(data.get("done"))
        ask = data.get("ask") or ("好,我帮你生成专属计划 ✅" if done else "继续,再多告诉我一点?")
        return ask, merged, done

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
            f"{persona.system_prompt}\n你是计划调整助手。根据用户反馈修改计划。\n"
            "只输出 JSON:{\"changed\":true/false,\"phases\":[与原来相同结构]}。\n"
            "若无需修改 changed=false 且 phases 为原样。"
        )
        plan_json = json.dumps([p.to_dict() for p in plan], ensure_ascii=False)
        msgs = [{"role": "system", "content": sys_p},
                {"role": "user", "content": f"当前计划:\n{plan_json}\n\n用户反馈:{feedback}"}]
        raw = self.client.chat(msgs, json_mode=True, max_tokens=1400)
        data = strip_json(raw)
        if isinstance(data, dict) and data.get("phases"):
            new_phases = self._build_phases(data["phases"])
            if new_phases:
                return bool(data.get("changed", True)), new_phases
        return False, plan

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
        style = ""
        if goal and goal.profile_dict():
            prof = goal.profile_dict()
            style = prof.get("motivation_style", "")
            idol = prof.get("idol", "")
            if style == "workout_buddy":
                lines.append("【动力风格:运动搭子】像用户的训练搭子,一起练、互相打卡、说'咱们今天也动了'这种话,语气亲切有伙伴感。")
            elif style == "idol":
                idol_txt = f"(用户崇拜的偶像:{idol})" if idol else ""
                lines.append(f"【动力风格:偶像】用用户偶像的口吻与气质激励他{idol_txt},让他感觉被榜样亲自鼓励。")
            elif style == "strict":
                lines.append("【动力风格:严师】严格、直接、不绕弯,指出松懈并push行动。")
            else:
                lines.append("【动力风格:温柔陪伴】温和、共情、鼓励为主。")
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
        return "\n".join(lines)


# --------------------------------------------------------------------------
# 离线兜底实现(无 Key / 调用失败时使用)
# --------------------------------------------------------------------------
class MockAIEngine(AIEngine):
    def onboard_goal(self, text, history, persona, profile=None):
        profile = profile or {"baseline": "", "habits": "", "target": "",
                              "constraint": "", "motivation_style": "workout_buddy", "idol": ""}
        steps = [
            "先从哪开始?告诉我你现在的起点(比如体重/身高/现状)。",
            "你平时有什么运动或饮食习惯?一天大概能抽出多少时间?",
            "这次想达成什么具体数字、多久达成?希望我当你的运动搭子还是偶像来激励你?",
        ]
        if len(history) < len(steps):
            return steps[len(history)], profile, False
        # 收齐后用内置模板产出(离线兜底,非 AI 生成)
        return "（离线模板模式)已根据你说的整理了一份基础计划 👇", profile, True

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
