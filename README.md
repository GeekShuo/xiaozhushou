# 小助手 (LifeCoach Desktop)

一个**有性格的 AI 人生教练**桌面应用。把你"很空"的长期目标(减肥 / 赚钱 / 学 AI 编程 / 英语口语 / 表达力……)拆成专业可执行的落地清单,通过 AI 形象陪你定计划、监督提醒、根据反馈调整,并提供情绪价值。

## 当前阶段: 已接入真实 LLM(默认兜底模板)

AI 大脑封装在 `src/backend/ai_engine.py` 的 `AIEngine` 接口中:
- `LLMAIEngine`:调用真实大模型(OpenAI / DeepSeek / 通义 / 本地 Ollama 等任意兼容 `/chat/completions` 的服务)。
- `MockAIEngine`:知识库 + 规则实现,作为离线兜底——无 API Key 或调用失败时自动回退,保证产品始终可用。

切换引擎、配置 API Key 都可在前端的 **⚙️ 设置** 面板完成,无需改代码。

## 接入真实大模型(LLM)

在 `⚙️ 设置` 面板填写:
- **引擎模式**:`auto`(有 Key 用真模型,否则用模板) / `mock`(仅模板) / `llm`(强制真模型)
- **API Base URL**:如 `https://api.deepseek.com/v1`、`https://api.openai.com/v1`、`http://localhost:11434/v1`(Ollama)
- **模型名**:如 `deepseek-chat`、`gpt-4o-mini`
- **API Key**:留空则不修改已保存的 Key(密钥只存于本地 `config.json`,不会回传到前端)

也可用环境变量(优先级高于 `config.json`,适合不落盘注入密钥):
```bash
set LIFECOACH_LLM_API_KEY=sk-xxxx        # 或 OPENAI_API_KEY
set LIFECOACH_LLM_BASE_URL=https://api.deepseek.com/v1
set LIFECOACH_LLM_MODEL=deepseek-chat
```

## 技术栈
- 部署形态: **纯 Web 服务**(Python 标准库 http.server + 静态前端),无桌面依赖,可配合 Cloudflare Tunnel 对外提供
- 存储: **SQLite**(目标 / 计划 / 任务 / 打卡 / 反馈)
- 提醒: 后台线程轮询 + 系统通知(plyer),应用内也有提醒横幅
- 前端: 原生 HTML/CSS/JS,无构建步骤

## 运行(Web 模式)
```bash
pip install -r requirements.txt   # 现在仅需标准库,此步通常可省略
python src/main.py --port 8787     # 默认监听 127.0.0.1:8787(8000 在部分 Windows 上被系统保留,被占用换其它端口即可)
```
浏览器打开 http://127.0.0.1:8000 即可使用。

### 让同学 / 外网访问(Cloudflare Tunnel)
本机无需公网 IP,安装 `cloudflared` 后一行命令把本地端口暴露到公网:
```bash
cloudflared tunnel --url http://localhost:8000
```
终端会给出一个 `https://xxxx.trycloudflare.com` 域名,发给同学即可访问(电脑需保持运行)。

## 目录结构
```
src/
  main.py              入口,创建 PyWebView 窗口
  backend/
    api.py             PyWebView 暴露给前端的接口
    database.py        SQLite 存储
    models.py          数据模型
    ai_engine.py       AI 引擎接口 + Mock 实现 + 真实 LLM 实现(LLMAIEngine)
    llm_client.py       OpenAI 兼容 LLM 客户端(纯标准库,无第三方依赖)
    knowledge.py       目标拆解知识库(减肥/赚钱/技能...)
    persona.py         人设定义(毒舌教练/温柔陪伴/严厉导师)
    reminders.py       提醒调度器
  frontend/
    index.html / css / js   UI
```

## 核心能力

### 1. 多轮「目标共创」(重点)
不再一句话套模板。新建目标后,AI 会先和你**多轮对话**,问清:
- 当前基线(体重/身高/现状)、已有习惯与痛点、目标的具体数字与时间、约束(工作强度/可运动时长)、
- **动力风格**:运动搭子(一起练、互相打卡) / 偶像(用你崇拜的偶像口吻激励) / 严师 / 温柔陪伴。

凑齐关键信息后,AI 生成一份**可量化的专属计划**(如「每日快走 30 分钟,心率 120–130」「晚餐换成鸡胸+蔬菜」),
并从对话中提取画像存进目标。无 Key / 调用失败时自动回退内置模板,**永远能生成计划**。

### 2. 多目标 + 切换
支持同时推进多个目标(减肥 / 学 AI 编程 / 副业赚钱…),目标列表可一键切换当前目标,
对话、计划、打卡、提醒都跟着当前目标走。

### 3. 日历聚合
「📅 日历」页把**所有目标**的每日任务(每天/每周/一次)汇总到月视图,点某天看当天要做什么。

### 4. 吃饭拍照(视觉钩子)
对话框可发图片(如饭图)。开启 `vision` 并换成支持视觉的模型(如 deepseek-vl)后,
AI 能"看"图给饮食建议;文本模型下会优雅提示无法看图。

## 运行

Web 模式(纯浏览器,已移除桌面 GUI):
```bash
python src/main.py --port 8787
# 自动选空闲端口: python src/main.py
```

## 微信小程序(未来载体)
`miniprogram/` 目录是一套小程序前端脚手架(目标 / 对话 / 日历三个页面),
通过 `utils/request.js` 调同一个后端 `/api`,**无需重写 AI 逻辑**。详见 `miniprogram/README.md`
(含「微信好友式每日提醒」的合规实现路径:订阅消息 / 服务号模板消息 + openid)。

## 路线图
- [x] MVP: 目标拆解 + 人设对话 + 打卡 + 提醒
- [x] 接入真实 LLM(OpenAI / DeepSeek / 通义 / 本地 Ollama),并在无 Key 时自动回退模板
- [x] 多轮目标共创(先确认再生成量化计划)+ 动力风格(运动搭子/偶像)
- [x] 多目标切换
- [x] 日历聚合所有目标的每日计划
- [x] 微信小程序脚手架(未来:小程序 + 微信好友式每日提醒)
- [ ] 长期记忆与个性化沉淀(摘要已落库,待接入对话)
- [ ] 打包分发(exe / dmg)
- [ ] 手机推送(微信订阅消息 / 服务号)
