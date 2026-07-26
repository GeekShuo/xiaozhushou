"""临时冒烟测试 #1+#2:连续签到/押金退款 + 长期记忆摘要。脚本结束后自清理。"""
import sys, os, time, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from backend.api import API
from backend.database import Database, CheckIn, ChatMessage
from backend import summarizer as sm

HERE = os.path.dirname(os.path.abspath(__file__))
default_db = os.path.join(HERE, "src", "data.db")
tmp = os.path.join(HERE, f".smoke2_{int(time.time()*1000)}.db")
if os.path.exists(tmp): os.remove(tmp)

api = API()
api.db = Database(tmp)  # 改用临时库,避免污染项目

# 目标
s = api.set_goal("我想减肥")
gid = s["goal"]["id"]
print("✓ 目标:", gid)

# 会员 + 押金
m = api.subscribe(21)
print("✓ 开通押金:", m)
assert m["deposit"] == 21 and m["threshold"] == 21

# 今日打卡 -> 连续 1
r = api.checkin("great", "今天跑了步", [])
print("✓ 打卡后会员:", r["membership"])
assert r["membership"]["consecutive"] == 1

# 模拟目标创建于 4 天前(否则 run_summaries 不会回溯到创建之前)
api.db.conn.execute("UPDATE goals SET created_at=? WHERE id=?", (time.time() - 4 * 86400, gid))
api.db.conn.commit()

# 模拟过去某天有打卡+对话(含"减少"偏好),验证日报提取
past = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
pts = datetime.datetime.fromisoformat(past).timestamp() + 3600
api.db.add_checkin(CheckIn(None, gid, pts, "ok", "有点累但坚持了", ""))
api.db.add_message(gid, ChatMessage("user", "今天太难了想减少点", pts))
api.db.add_message(gid, ChatMessage("assistant", "好,调轻", pts))

# 手动复现 run_summaries 的循环,观察每个 ds 的 summarize_day 结果
created = datetime.date.fromtimestamp(api.db.get_goal(gid).created_at)
today = datetime.date.today()
d = created
while d < today:
    ds = d.strftime("%Y-%m-%d")
    print("PROBE", ds, "->", sm.summarize_day(api.db, gid, ds))
    d += datetime.timedelta(days=1)

sm.run_summaries(api.db, api.db.get_goal(gid))
sums = api.db.get_summaries(gid, 50)
print("DEBUG all summaries:", [(x["kind"], x["ref_date"], x["data"]) for x in sums])
c = api.db.conn.cursor()
c.execute("SELECT ref_date, typeof(data_json), data_json FROM summaries WHERE ref_date=?", ("2026-07-23",))
print("RAW 07-23:", c.fetchall())
api.db.add_summary(gid, "day", "X1", sm.summarize_day(api.db, gid, past))
c.execute("SELECT data_json FROM summaries WHERE ref_date=?", ("X1",))
print("RAW X1 data_json:", repr(c.fetchone()[0]))
import json as _json
print("JSON.dumps of full:", _json.dumps(sm.summarize_day(api.db, gid, past), ensure_ascii=False)[:120])
day = [x for x in sums if x["kind"] == "day" and x["ref_date"] == past]
print("✓ 日报:", day[0]["data"] if day else "MISSING")
assert day, "日报缺失"
assert any("减少" in p for p in day[0]["data"]["prefs"]), "偏好未捕获"

# get_state 携带 membership + summaries
st = api.get_state()
print("✓ state.membership:", bool(st["membership"]), "| summaries:", len(st["summaries"]))

api.db.close()

# 自清理
for f in (tmp, default_db):
    try:
        if os.path.exists(f): os.remove(f)
    except Exception:
        pass
print("\nSMOKE2_OK")
