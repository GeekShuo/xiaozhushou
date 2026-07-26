"""数据模型定义。Plan / Task 以 JSON 形式存于数据库,这里给出 Python 结构便于前端与 AI 引擎协作。"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import time


@dataclass
class Goal:
    id: Optional[int]
    title: str                 # 用户的原始宏大目标,如 "减肥"
    normalized: str = ""       # 归一化后的关键词,用于匹配知识库
    created_at: float = field(default_factory=time.time)
    status: str = "draft"      # draft(共创中) / active / archived
    profile: str = ""          # 共创收集到的用户画像 JSON 字符串

    def to_dict(self):
        return asdict(self)

    def profile_dict(self) -> dict:
        if not self.profile:
            return {}
        try:
            return json.loads(self.profile)
        except Exception:
            return {}


@dataclass
class Phase:
    """计划的一个阶段,包含若干任务。"""
    title: str
    weeks: str = ""            # 如 "第1-2周"
    rationale: str = ""        # 为什么有这个阶段
    tasks: list = field(default_factory=list)  # List[Task]

    def to_dict(self):
        return {
            "title": self.title,
            "weeks": self.weeks,
            "rationale": self.rationale,
            "tasks": [t.to_dict() for t in self.tasks],
        }


@dataclass
class Task:
    id: str                    # 稳定 id,如 "p1t3"
    title: str
    detail: str = ""           # 具体怎么做
    frequency: str = "daily"   # daily / weekly / once
    duration_min: int = 30     # 单次预计耗时(分钟)
    phase_id: str = ""
    done: bool = False
    last_done: float = 0.0

    def to_dict(self):
        return asdict(self)


def plan_to_json(phases: list[Phase]) -> str:
    return json.dumps([asdict(p) for p in phases], ensure_ascii=False)


def plan_from_json(data: str) -> list[Phase]:
    if not data:
        return []
    raw = json.loads(data)
    phases = []
    for p in raw:
        tasks = [Task(**t) for t in p.get("tasks", [])]
        phases.append(Phase(
            title=p["title"], weeks=p.get("weeks", ""),
            rationale=p.get("rationale", ""), tasks=tasks))
    return phases


@dataclass
class CheckIn:
    id: Optional[int]
    goal_id: int
    ts: float = field(default_factory=time.time)
    mood: str = ""             # great/ok/bad
    note: str = ""
    done_task_ids: str = ""    # 逗号分隔

    def to_dict(self):
        return asdict(self)


@dataclass
class ChatMessage:
    role: str                  # user / assistant
    content: str
    ts: float = field(default_factory=time.time)

    def to_dict(self):
        return asdict(self)
