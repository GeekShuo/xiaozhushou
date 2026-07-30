# -*- coding: utf-8 -*-
import urllib.request, urllib.error, json
URL = 'http://127.0.0.1:61373/api'

def api(name, *args):
    req = urllib.request.Request(URL, data=json.dumps({'name': name, 'args': list(args)}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=300)).get('result')
    except urllib.error.HTTPError as e:
        return {'ERR': e.read().decode('utf-8', 'replace')[:300]}

print('--- 挑剔用户全流程 ---')
r = api('start_goal', '每天读书30分钟,一年读20本')
print('1. 首问:', (r.get('reply') or '')[:50])

answers = [
    '我现在一年读不到2本,通勤地铁上能看',
    '希望一年读20本,每本平均250页',
    '晚上睡前最容易读,周末时间多',
    '要温柔陪伴风格,别给我压力',
]
for a in answers:
    r = api('goal_chat', a)
    if r.get('onboarding_complete'):
        break
print('2. 风格:', r.get('profile', {}).get('motivation_style'))
print('3. 计划阶段数:', len(r.get('plan', [])))
if r.get('plan'):
    t0 = r['plan'][0]['tasks'][0]
    print('   样例任务:', t0['title'], '|', t0['detail'][:40])

# 今天视图勾选 + 打卡(AI 反馈)
t = api('get_today')
ids = [i['task_id'] for i in t['items'][:2]]
print('4. 今日任务数:', len(t['items']))
r = api('checkin', 'great', '今天在地铁上读了15分钟,很顺', ids)
print('5. 教练反馈:', (r.get('reply') or '')[:70])
print('   连续天数:', r['streak']['consecutive'])

# 连续打卡触发观察
for i in range(2):
    api('checkin', ['ok', 'great'][i], f'第{i+2}次', [])
s = api('get_state')
print('6. 观察摘要数:', len(s.get('summaries', [])))
for sm in s.get('summaries', []):
    d = sm.get('data') or {}
    print('   观察:', (d.get('text') or 'EMPTY')[:70])

# 押金 + 提醒
api('open_deposit', 99, 21)
s = api('get_state')
print('7. 押金:', s['membership']['deposit'], '阈值:', s['membership']['threshold'])
print('8. 提醒数:', len(s.get('reminders', [])))

gid = s['goal']['id']
api('delete_goal', gid)
print('9. 已清理测试目标')
