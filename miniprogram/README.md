# 小助手 · 微信小程序(未来载体)

> 当前 Web/桌面版的所有能力(多轮目标共创、量化计划、多目标切换、日历、打卡、提醒)
> 都由 `python src/main.py --web` 启动的 **HTTP 后端** 提供。小程序只是一个更轻的壳,
> 通过 `utils/request.js` 调同一个 `/api` 接口,**无需重写任何 AI 逻辑**。

## 怎么跑起来
1. 先启动后端:`python src/main.py --web`(会打印 `http://127.0.0.1:<端口>`)。
2. 在微信开发者工具里「导入项目」,选择本 `miniprogram/` 目录。
3. 把 `app.js` 里的 `baseUrl` 改成后端地址:
   - 本地调试:电脑的局域网 IP,如 `http://192.168.1.x:8080`
   - 真机预览:需后端有公网 HTTPS 域名(`wx.request` 只认 https 域名)
4. 用 `touristappid` 可直接预览;正式发布需替换为你的小程序 AppID。

## 页面
- `pages/goals` — 目标列表 + 新建(进入多轮共创)+ 切换
- `pages/chat` — 与教练对话(共创阶段自动走 `goal_chat`)
- `pages/calendar` — 所有目标的每日任务日历

## 每日提醒 / 「微信好友」交互(下一步)
用户想要的是"像加了个微信好友一样,每天被提醒、能聊天"。正确且合规的路径:

1. **提醒推送**:用**微信小程序订阅消息**(`wx.requestSubscribeMessage` + 后端调
   `subscribeMessage.send`),或**服务号模板消息**。后端 `reminders` 已按 cron 产出提醒,
   只需再加一个「微信推送适配器」:到点时把提醒文案推到对应用户 `openid`。
   - 拿到 `openid`:小程序 `wx.login` → 后端用 `code` 换 `openid`(需 `appid+secret`)。
2. **好友式对话**:用户在小程序里和教练聊天 = 走 `/api` 的 `chat`(已支持图文)。
   若想"在微信聊天列表里直接收消息",需接入**服务号客服消息**或**微信对话开放平台**,
   把后端 `chat` 作为应答源——本质是把 `chat` 包一层微信消息收发。
3. **吃饭拍照**:`chat` 已支持 `image` 参数(需开启 `vision` 并换成支持视觉的模型,如 deepseek-vl)。

> 不推荐个人号机器人(wechaty/itchat),违反微信平台规则,有封号风险。

## 后端接口速查(小程序侧统一 POST `/api` `{name, args}`)
- `get_state()` → 当前目标/计划/对话/目标列表
- `start_goal(text)` → 新建目标,进入多轮共创
- `goal_chat(text)` → 共创对话(攒够信息后返回 `onboarding_complete`)
- `chat(text, image)` → 普通对话
- `switch_goal(id)` → 切换当前目标
- `get_calendar(year, month)` → 日历聚合
- `checkin(mood, note, done_task_ids)` → 打卡
- `save_config({...})` → 配置引擎/Key(生产环境建议 Key 放服务器环境变量,不要下发前端)
