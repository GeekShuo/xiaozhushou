"""临时冒烟测试:验证后端流程(不含 GUI)。"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from backend.api import API
from backend.database import Database

api = API()
api.start()

# 1) 设定目标
s = api.set_goal("我想三个月瘦下来,顺便学点 AI 编程")
assert s["goal"], "goal 未创建"
print("✓ 目标已建:", s["goal"]["title"])
plan = s["plan"]
assert plan and plan[0]["tasks"], "计划为空"
print("✓ 计划阶段数:", len(plan), "首阶段任务:", [t["title"] for t in plan[0]["tasks"]])

# 2) 切换人设
api.set_persona("coach_strict")
print("✓ 人设:", api.persona_id)

# 3) 对话
r = api.chat("今天太累了不想动")
print("✓ 教练回应:", r["reply"][:40], "...")

# 4) 勾选任务
tid = plan[0]["tasks"][0]["id"]
api.toggle_task(tid)
print("✓ 切换任务:", tid)

# 5) 反馈调整
r = api.adapt_plan("太难了,减少点")
print("✓ 调整计划 changed:", r["changed"])

# 6) 打卡
r = api.checkin("great", "今天不错", [])
print("✓ 打卡回应:", r["reply"][:30], "...")

# 7) 提醒列表
print("✓ 提醒数:", len(api.db.list_reminders(s["goal"]["id"])))

api.db.close()
print("\nSMOKE_OK")
