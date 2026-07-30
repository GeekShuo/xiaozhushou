/* 小助手 · 前端逻辑 v2
 * 覆盖:今天首页 / 多轮共创(进度+风格卡片+跳过) / 打卡AI反馈+庆祝 /
 *       任务按天重置即时刷新 / 押金挑战 / 提醒配置 / 日历 / 设置(测试连接)
 */
"use strict";

// ---------------- 桥接层:pywebview 或 HTTP ----------------
const isPywebview = () => !!(window.pywebview && window.pywebview.api);

async function call(name, ...args) {
  if (isPywebview()) {
    return await window.pywebview.api[name](...args);
  }
  const res = await fetch("/api", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, args }),
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.result;
}

// ---------------- 全局状态 ----------------
let S = null;              // 最近一次 get_state
let TODAY = null;          // 今日视图数据
let calY = null, calM = null;
let selMood = "";
let pendingImg = null;     // dataURL

const $ = (id) => document.getElementById(id);

// ---------------- Toast / 庆祝 ----------------
let toastTimer = null;
function toast(msg, type = "info") {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast " + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

function celebrate(html) {
  const wrap = $("celebrate"), card = $("celeCard");
  card.innerHTML = html;
  wrap.classList.remove("hidden");
  wrap.onclick = () => wrap.classList.add("hidden");
  setTimeout(() => wrap.classList.add("hidden"), 5200);
}

// 按钮 loading 态
function busy(btn, on, textWhenBusy) {
  if (!btn) return;
  if (on) {
    btn.dataset.txt = btn.textContent;
    btn.textContent = textWhenBusy || "请稍等…";
    btn.disabled = true;
    btn.classList.add("busy");
  } else {
    if (btn.dataset.txt) btn.textContent = btn.dataset.txt;
    btn.disabled = false;
    btn.classList.remove("busy");
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------- 导航 ----------------
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const v = btn.dataset.view;
    document.querySelectorAll(".view").forEach((s) => s.classList.remove("active"));
    $("view-" + v).classList.add("active");
    if (v === "calendar") loadCalendar();
    if (v === "today") refreshToday();
    if (v === "chat") renderChat();
  });
});

function gotoView(v) {
  document.querySelector(`.nav-item[data-view="${v}"]`)?.click();
}

// ---------------- 状态刷新 ----------------
async function refreshState() {
  try {
    S = await call("get_state");
    TODAY = S.today || TODAY;
    renderAll();
  } catch (e) {
    toast("加载失败:" + e.message, "error");
  }
}

async function refreshToday() {
  try {
    TODAY = await call("get_today");
    renderToday();
  } catch (e) { /* 静默 */ }
}

function renderAll() {
  renderToday();
  renderChat();
  renderPlan();
  renderGoals();
  renderProgress();
  renderPersonaBox();
}

// ================= ☀️ 今天 =================
function renderToday() {
  const t = TODAY;
  if (!t) return;
  $("todayTitle").textContent = `今天 · ${t.date.slice(5).replace("-", "/")} 周${t.weekday}`;

  const items = t.items || [];
  const done = items.filter((i) => i.done).length;
  $("todaySub").textContent = items.length
    ? `${done}/${items.length} 项完成 —— ${done === items.length ? "今天全清,漂亮!" : "把最小的那件先做掉。"}`
    : "今天没有安排任务。去「目标」页立一个目标吧。";
  $("todayFill").style.width = items.length ? (done / items.length) * 100 + "%" : "0%";

  // streak 徽章
  const m = S && S.membership;
  const badge = $("streakBadge");
  if (m && m.consecutive > 0) {
    badge.innerHTML = `🔥 连续 <b>${m.consecutive}</b> 天`;
    badge.classList.remove("hidden");
  } else {
    badge.innerHTML = "";
  }

  // 任务列表(按目标分组)
  const box = $("todayList");
  box.innerHTML = "";
  if (!items.length) {
    const noGoals = !(S && (S.goals || []).some((g) => g.status === "active" || g.status === "draft"));
    box.innerHTML = noGoals
      ? `<div class="empty-tip">🌱 还没有目标。立一个,AI 教练会陪你从「共创」走到「每天打卡」。<br/><button class="primary" id="goCreate">＋ 创建第一个目标</button></div>`
      : `<div class="empty-tip">🌱 今天没有安排任务。多目标同时推进时,这里会汇总所有目标今天要做的事。</div>`;
    const gc = $("goCreate");
    if (gc) gc.addEventListener("click", () => gotoView("goal"));
  }
  const groups = {};
  items.forEach((i) => (groups[i.goal_title] = groups[i.goal_title] || []).push(i));
  Object.entries(groups).forEach(([gt, list]) => {
    const g = document.createElement("div");
    g.className = "today-group";
    g.innerHTML = `<div class="tg-title">🎯 ${esc(gt)}</div>`;
    list.forEach((i) => {
      const row = document.createElement("div");
      row.className = "today-item" + (i.done ? " done" : "");
      row.innerHTML = `
        <label class="ti-check"><input type="checkbox" ${i.done ? "checked" : ""}/></label>
        <div class="ti-main">
          <div class="ti-title">${esc(i.title)} <span class="ti-freq">${i.frequency === "daily" ? "每日" : i.frequency === "weekly" ? "每周" : "一次"}${i.duration_min ? " · " + i.duration_min + "min" : ""}</span></div>
          <div class="ti-detail">${esc(i.detail)}</div>
        </div>`;
      row.querySelector("input").addEventListener("change", async (ev) => {
        ev.target.disabled = true;
        try {
          const r = await call("toggle_today_task", i.goal_id, i.task_id);
          TODAY = r.today;
          renderToday();
          renderPlan && refreshStateSilent();
        } catch (e) {
          toast("操作失败:" + e.message, "error");
          ev.target.disabled = false;
        }
      });
      g.appendChild(row);
    });
    box.appendChild(g);
  });

  // 无 active 目标时隐藏打卡卡片;已打卡则收起
  const ck = $("todayCheckin");
  const hasActive = (S && (S.goals || []).some((g) => g.status === "active"));
  if (!hasActive) {
    ck.classList.add("hidden");
  } else {
    ck.classList.remove("hidden");
    if (m && m.checked_today) {
      ck.querySelector("h3").textContent = "✅ 今日已打卡";
      ck.querySelectorAll(".moods,textarea,#checkinBtn").forEach((e) => e.classList.add("hidden"));
    } else {
      ck.querySelector("h3").textContent = "今日打卡";
      ck.querySelectorAll(".moods,textarea,#checkinBtn").forEach((e) => e.classList.remove("hidden"));
    }
  }
}

async function refreshStateSilent() {
  try { S = await call("get_state"); renderPlan(); renderProgress(); } catch (e) {}
}

// 心情选择
document.querySelectorAll(".mood").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".mood").forEach((x) => x.classList.remove("sel"));
    b.classList.add("sel");
    selMood = b.dataset.mood;
  });
});

// 提交打卡
$("checkinBtn").addEventListener("click", async () => {
  if (!selMood) return toast("先选一下今天的状态 😄😐😩", "warn");
  const btn = $("checkinBtn");
  busy(btn, true, "教练正在看你的打卡…");
  const doneIds = (TODAY?.items || []).filter((i) => i.done).map((i) => i.task_id);
  try {
    const r = await call("checkin", selMood, $("checkinNote").value.trim(), doneIds);
    S = r; TODAY = r.today;
    const reply = r.reply || "打卡成功!";
    const box = $("coachReply");
    box.innerHTML = `<div class="cr-tag">🎙️ 教练说</div><div class="cr-text">${esc(reply)}</div>`;
    box.classList.remove("hidden");
    $("checkinNote").value = "";
    document.querySelectorAll(".mood").forEach((x) => x.classList.remove("sel"));
    selMood = "";
    const st = r.streak || {};
    if (st.refunded_now) {
      celebrate(`<div class="cele-emoji">🎉💰</div><h2>押金全额退还!</h2><p>连续打卡 ${st.consecutive} 天,你赢回了自己的钱,更赢回了习惯。</p>`);
    } else if ([3, 7, 21, 50, 100].includes(st.consecutive)) {
      celebrate(`<div class="cele-emoji">🔥</div><h2>连续 ${st.consecutive} 天!</h2><p>${st.consecutive >= 21 ? "习惯已经长在你身上了。" : "势头起来了,别断!"}</p>`);
    } else {
      toast("打卡成功 ✅", "ok");
    }
    renderAll();
  } catch (e) {
    toast("打卡失败:" + e.message, "error");
  } finally {
    busy(btn, false);
  }
});

// ================= 💬 对话 =================
function profileProgress(p) {
  // 优先用后端算好的可靠进度,否则前端兜底
  if (S && S.onboarding_progress && S.onboarding_progress.length) {
    return S.onboarding_progress.map((s) => ({ label: s.label, done: !!s.done }));
  }
  const keys = ["baseline", "habits", "target", "constraint", "motivation_style"];
  const labels = ["基线", "习惯", "目标数字", "约束", "陪伴风格"];
  return keys.map((k, i) => ({ label: labels[i], done: !!(p && p[k]) }));
}

function renderChat() {
  if (!S) return;
  const box = $("chatBox");
  box.innerHTML = "";
  (S.chat || []).forEach((m) => appendMsg(m.role, m.content, false));
  box.scrollTop = box.scrollHeight;

  // 共创横幅 + 进度
  const banner = $("onboardBanner");
  const cards = $("styleCards");
  if (S.onboarding) {
    banner.classList.remove("hidden");
    const steps = profileProgress(S.profile);
    const doneN = steps.filter((s) => s.done).length;
    $("obText").textContent = `🤝 目标共创中(${doneN}/${steps.length} 项信息已收集)`;
    $("obSteps").innerHTML = steps
      .map((s) => `<span class="ob-step ${s.done ? "done" : ""}">${s.done ? "✓ " : ""}${s.label}</span>`)
      .join("");
    // 风格未定时展示可点选卡片
    if (!(S.profile && S.profile.motivation_style)) cards.classList.remove("hidden");
    else cards.classList.add("hidden");
  } else {
    banner.classList.add("hidden");
    cards.classList.add("hidden");
  }
}

function appendMsg(role, content, scroll = true) {
  const box = $("chatBox");
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "me" : "ai");
  div.innerHTML = `<div class="bubble">${esc(content)}</div>`;
  box.appendChild(div);
  if (scroll) box.scrollTop = box.scrollHeight;
  return div;
}

function appendTyping() {
  const box = $("chatBox");
  const div = document.createElement("div");
  div.className = "msg ai typing";
  div.innerHTML = `<div class="bubble"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

// 风格卡片点选 → 直接作为一条消息发出去
document.querySelectorAll(".sc").forEach((c) => {
  c.addEventListener("click", () => {
    const label = { workout_buddy: "我想要「运动搭子」风格,像朋友一起练、互相打卡",
                    idol: "我想要「偶像」风格,用我偶像的口吻激励我",
                    strict: "我想要「严师」风格,严格督促我",
                    warm: "我想要「温柔陪伴」风格" }[c.dataset.style];
    $("chatInput").value = label;
    sendChat();
  });
});

// 跳过共创
$("obSkip").addEventListener("click", async () => {
  const btn = $("obSkip");
  busy(btn, true, "生成中…");
  const typing = appendTyping();
  try {
    const r = await call("finish_onboarding");
    S = r; TODAY = r.today;
    typing.remove();
    renderAll();
    toast("计划已生成 ✅", "ok");
    gotoView("plan");
  } catch (e) {
    typing.remove();
    toast(e.message, "error");
  } finally {
    busy(btn, false);
  }
});

// 图片选择
$("chatImg").addEventListener("change", (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    pendingImg = rd.result;
    $("imgPrev").src = pendingImg;
    $("imgPrev").classList.remove("hidden");
  };
  rd.readAsDataURL(f);
});

let sending = false;
async function sendChat() {
  if (sending) return;
  const v = $("chatInput").value.trim();
  const img = pendingImg;
  if (!v && !img) return;
  sending = true;
  const btn = $("chatSend");
  busy(btn, true, "…");
  $("chatInput").value = "";
  pendingImg = null;
  $("imgPrev").classList.add("hidden");
  $("chatImg").value = "";
  appendMsg("user", v + (img ? " 📷[图片]" : ""));
  const typing = appendTyping();
  try {
    let r;
    if (S && S.onboarding) {
      r = await call("goal_chat", v, img);
    } else {
      r = await call("chat", v, img);
    }
    typing.remove();
    if (r.onboarding_complete) {
      S = r; TODAY = r.today;
      renderAll();
      celebrate(`<div class="cele-emoji">📋✨</div><h2>专属计划已生成!</h2><p>去「计划」页看看,每天只需要跟今天那一件小事较劲。</p>`);
      gotoView("plan");
    } else if (r.reply !== undefined) {
      // 共创中:用后端返回的最新历史完整重绘,避免切换视图后丢消息
      if (r.onboarding) {
        S.chat = r.history || S.chat;
        S.profile = r.profile || S.profile;
        renderChat();
        renderChatBannerOnly();
      } else if (r.plan) {
        // 对话里直接改了计划:刷新计划/今日,并展示 AI 回复
        S = r; if (r.today) TODAY = r.today;
        renderAll();
      } else {
        appendMsg("assistant", r.reply);
      }
    } else {
      S = r; renderChat();
    }
  } catch (e) {
    typing.remove();
    appendMsg("assistant", "😥 出了点问题:" + e.message + "(内容已保留,可重发)");
    $("chatInput").value = v;
  } finally {
    sending = false;
    busy(btn, false);
  }
}

function renderChatBannerOnly() {
  const steps = profileProgress(S.profile);
  const doneN = steps.filter((s) => s.done).length;
  $("obText").textContent = `🤝 目标共创中(${doneN}/${steps.length} 项信息已收集)`;
  $("obSteps").innerHTML = steps
    .map((s) => `<span class="ob-step ${s.done ? "done" : ""}">${s.done ? "✓ " : ""}${s.label}</span>`)
    .join("");
  if (S.profile && S.profile.motivation_style) $("styleCards").classList.add("hidden");
}

$("chatSend").addEventListener("click", sendChat);
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing) sendChat();
});

// 动力风格卡片:点选即确定(比打字可靠),并推动共创前进
document.querySelectorAll("#styleCards .sc").forEach((card) => {
  card.addEventListener("click", () => pickStyle(card));
});

async function pickStyle(card) {
  if (sending) return;
  const style = card.dataset.style;
  const name = card.querySelector("b").textContent;
  sending = true;
  appendMsg("user", `我选了${name}风格 🎯`);
  const typing = appendTyping();
  try {
    // 发一条含风格关键词的消息,后端 _detect_style 会确定性设定
    const r = await call("goal_chat", `我选了${name}（${style}）风格`);
    typing.remove();
    if (r.onboarding_complete) {
      S = r; TODAY = r.today;
      renderAll();
      celebrate(`<div class="cele-emoji">📋✨</div><h2>专属计划已生成!</h2><p>去「计划」页看看,每天只需要跟今天那一件小事较劲。</p>`);
      gotoView("plan");
    } else if (r.reply !== undefined) {
      S.chat = r.history || S.chat;
      S.profile = Object.assign({}, S.profile, r.profile || {});
      S.profile.motivation_style = style;   // 直接锁定,避免 LLM 误判
      renderChat();
      renderChatBannerOnly();
    } else {
      S = r; renderChat();
    }
  } catch (e) {
    typing.remove();
    appendMsg("assistant", "😥 出错了:" + e.message);
  } finally {
    sending = false;
  }
}
$("adaptInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing) $("adaptBtn").click();
});

// ================= 🗂️ 计划 =================
function renderPlan() {
  if (!S) return;
  const box = $("planList");
  box.innerHTML = "";
  const plan = S.plan || [];
  if (!plan.length) {
    box.innerHTML = `<div class="empty-tip">还没有计划。去「目标」页开始共创,AI 会先了解你,再生成量化计划。</div>`;
    return;
  }
  plan.forEach((p) => {
    const ph = document.createElement("div");
    ph.className = "phase card";
    const doneN = p.tasks.filter((t) => t.done).length;
    ph.innerHTML = `
      <div class="ph-head">
        <div><b>${esc(p.title)}</b> <span class="ph-week">${esc(p.weeks)}</span></div>
        <span class="ph-count">${doneN}/${p.tasks.length}</span>
      </div>
      <div class="ph-rationale">${esc(p.rationale)}</div>`;
    p.tasks.forEach((t) => {
      const row = document.createElement("div");
      row.className = "task" + (t.done ? " done" : "");
      row.innerHTML = `
        <label><input type="checkbox" ${t.done ? "checked" : ""}/></label>
        <div class="t-main">
          <div class="t-title">${esc(t.title)} <span class="ti-freq">${t.frequency === "daily" ? "每日" : t.frequency === "weekly" ? "每周" : "一次"}${t.duration_min ? " · " + t.duration_min + "min" : ""}</span></div>
          <div class="t-detail">${esc(t.detail)}</div>
        </div>`;
      row.querySelector("input").addEventListener("change", async (ev) => {
        ev.target.disabled = true;
        try {
          const r = await call("toggle_task", t.id);
          S = r; TODAY = r.today;
          renderPlan(); renderToday();
        } catch (e) {
          toast("操作失败:" + e.message, "error");
          ev.target.disabled = false;
        }
      });
      ph.appendChild(row);
    });
    box.appendChild(ph);
  });
}

$("adaptBtn").addEventListener("click", async () => {
  const v = $("adaptInput").value.trim();
  if (!v) return toast("先说说计划哪里不合适", "warn");
  const btn = $("adaptBtn");
  busy(btn, true, "AI 调整中…");
  try {
    const r = await call("adapt_plan", v);
    S = r.state || r;
    if (r.today) TODAY = r.today;
    $("adaptInput").value = "";
    renderAll();
    toast(r.changed === false ? "AI 认为当前计划合适,未调整" : "计划已调整 ✅", "ok");
  } catch (e) {
    toast("调整失败:" + e.message, "error");
  } finally {
    busy(btn, false);
  }
});

// ================= 📅 日历 =================
async function loadCalendar() {
  try {
    const now = new Date();
    if (calY === null) { calY = now.getFullYear(); calM = now.getMonth() + 1; }
    const data = await call("get_calendar", calY, calM);
    renderCalendar(data);
  } catch (e) {
    toast("日历加载失败:" + e.message, "error");
  }
}

$("calPrev").addEventListener("click", () => { calM--; if (calM < 1) { calM = 12; calY--; } loadCalendar(); });
$("calNext").addEventListener("click", () => { calM++; if (calM > 12) { calM = 1; calY++; } loadCalendar(); });

const GOAL_COLORS = ["#4ade80", "#38bdf8", "#f472b6", "#facc15", "#a78bfa", "#fb923c"];

function renderCalendar(data) {
  $("calTitle").textContent = `${data.year} 年 ${data.month} 月`;
  const grid = $("calGrid");
  grid.innerHTML = "";
  "一二三四五六日".split("").forEach((w) => {
    const el = document.createElement("div");
    el.className = "cal-wd";
    el.textContent = "周" + w;
    grid.appendChild(el);
  });
  const colorOf = {};
  (data.goals || []).forEach((g, i) => (colorOf[g.id] = GOAL_COLORS[i % GOAL_COLORS.length]));

  const first = new Date(data.year, data.month - 1, 1);
  const daysIn = new Date(data.year, data.month, 0).getDate();
  let lead = first.getDay() - 1; if (lead < 0) lead = 6;
  for (let i = 0; i < lead; i++) {
    const c = document.createElement("div");
    c.className = "cal-cell empty";
    grid.appendChild(c);
  }
  for (let d = 1; d <= daysIn; d++) {
    const key = `${data.year}-${String(data.month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const tasks = data.days[key] || [];
    const cell = document.createElement("div");
    cell.className = "cal-cell" + (tasks.length ? " has" : "") + (key === data.today ? " today" : "");
    const gids = [...new Set(tasks.map((t) => t.goal_id))];
    cell.innerHTML = `<div class="cal-d">${d}</div>` +
      (tasks.length
        ? `<div class="cal-dots">${gids.map((g) => `<i style="background:${colorOf[g] || "#4ade80"}"></i>`).join("")}<span>${tasks.length}</span></div>`
        : "");
    cell.addEventListener("click", () => renderCalInfo(key, tasks, colorOf));
    grid.appendChild(cell);
  }
  // 默认展示今天
  if (data.today.startsWith(`${data.year}-${String(data.month).padStart(2, "0")}`)) {
    renderCalInfo(data.today, data.days[data.today] || [], colorOf);
  } else {
    $("calInfo").innerHTML = "";
  }
}

function renderCalInfo(dateKey, tasks, colorOf) {
  const box = $("calInfo");
  if (!tasks.length) {
    box.innerHTML = `<div class="empty-tip">📅 ${dateKey} 没有安排任务,休息也是计划的一部分。</div>`;
    return;
  }
  box.innerHTML = `<h3>${dateKey} · ${tasks.length} 项</h3>` + tasks.map((t) => `
    <div class="cal-task" style="border-left:3px solid ${colorOf[t.goal_id] || "#4ade80"}">
      <div class="ct-g">🎯 ${esc(t.goal_title)}</div>
      <b>${esc(t.task_title)}</b> <span class="ti-freq">${t.frequency === "daily" ? "每日" : t.frequency === "weekly" ? "每周" : "一次"}${t.duration_min ? " · " + t.duration_min + "min" : ""}</span>
      <div class="ct-d">${esc(t.detail)}</div>
    </div>`).join("");
}

// ================= 🎯 目标 =================
function renderGoals() {
  if (!S) return;
  const box = $("goalList");
  box.innerHTML = "";
  const styleEmoji = { workout_buddy: "🤝", idol: "⭐", strict: "📏", warm: "🫶" };
  (S.goals || []).forEach((g) => {
    const div = document.createElement("div");
    div.className = "goal-item" + (g.current ? " current" : "");
    const st = g.status === "draft" ? "共创中" : g.status === "active" ? "进行中" : "已归档";
    div.innerHTML = `
      <div>
        <div class="g-title">${styleEmoji[g.motivation_style] || "🎯"} ${esc(g.title)}</div>
        <div class="g-meta">${st} · ${new Date(g.created_at * 1000).toLocaleDateString()}</div>
      </div>
      <div class="g-btns">
        ${g.current ? '<span class="cur-tag">当前</span>'
          : (g.status === "active" || g.status === "draft")
            ? '<button class="switch-btn">切换</button>' : ""}
        <button class="del-btn" title="删除目标">🗑</button>
      </div>`;
    const sw = div.querySelector(".switch-btn");
    if (sw) sw.addEventListener("click", async () => {
      busy(sw, true, "…");
      try {
        S = await call("switch_goal", g.id);
        TODAY = S.today;
        renderAll();
        toast(`已切换到「${g.title}」`, "ok");
        if (S.onboarding) gotoView("chat");
      } catch (e) { toast(e.message, "error"); busy(sw, false); }
    });
    const del = div.querySelector(".del-btn");
    del.addEventListener("click", async () => {
      if (g.status === "active" &&
          !confirm(`删除「${g.title}」?\n它的计划、打卡记录、对话都会一起删除,不可恢复。`)) return;
      busy(del, true, "…");
      try {
        S = await call("delete_goal", g.id);
        TODAY = S.today;
        renderAll();
        toast("已删除", "ok");
      } catch (e) { toast(e.message, "error"); busy(del, false); }
    });
    box.appendChild(div);
  });
}

$("goalBtn").addEventListener("click", async () => {
  const v = $("goalInput").value.trim();
  if (!v) return toast("先写下你的目标,越随意越好", "warn");
  const btn = $("goalBtn");
  busy(btn, true, "教练正在准备第一个问题…");
  try {
    const r = await call("start_goal", v);
    S = r; TODAY = r.today;
    $("goalInput").value = "";
    renderAll();
    gotoView("chat");
    toast("共创开始!回答教练的问题吧", "ok");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    busy(btn, false);
  }
});

function renderPersonaBox() {
  if (!S) return;
  const ps = S.personas || [];
  const cur = ps.find((p) => p.id === S.persona_id);
  if (cur) {
    $("personaName").textContent = `${cur.emoji} ${cur.name}`;
    $("personaTag").textContent = cur.tagline || "";
  }
  // 目标页 chips
  const chips = $("personaChips");
  chips.innerHTML = "";
  ps.forEach((p) => {
    const c = document.createElement("button");
    c.className = "chip" + (p.id === S.persona_id ? " sel" : "");
    c.textContent = `${p.emoji} ${p.name}`;
    c.addEventListener("click", async () => {
      try {
        await call("set_persona", p.id);
        S.persona_id = p.id;
        renderPersonaBox();
        toast(`教练已切换为 ${p.name}`, "ok");
      } catch (e) { toast(e.message, "error"); }
    });
    chips.appendChild(c);
  });
  // 设置页下拉
  const sel = $("cfgPersona");
  if (sel && !sel.options.length) {
    ps.forEach((p) => {
      const o = document.createElement("option");
      o.value = p.id; o.textContent = `${p.emoji} ${p.name}`;
      sel.appendChild(o);
    });
  }
  if (sel) sel.value = S.persona_id;
}

// ================= 📈 进度 =================
function renderProgress() {
  if (!S) return;
  const m = S.membership;
  const card = $("memberCard");
  const subBox = $("subBox");
  if (m && m.deposit > 0) {
    subBox.classList.add("hidden");
    const pct = Math.min(100, Math.round((m.consecutive / m.threshold) * 100));
    card.innerHTML = m.refunded
      ? `<div class="mc-big">🏆 挑战成功!</div>
         <p>连续打卡 ${m.consecutive} 天,${m.deposit} 元押金已全额退还(模拟)。要不要再来一轮更狠的?</p>
         <button id="againBtn" class="primary">再来一轮</button>`
      : `<div class="mc-big">🔥 押金挑战进行中</div>
         <div class="mc-row"><span>连续 <b>${m.consecutive}</b>/${m.threshold} 天</span><span>${m.deposit} 元托管中</span></div>
         <div class="today-progress"><div class="tp-fill" style="width:${pct}%"></div></div>
         <p class="sub">${m.checked_today ? "今天已打卡 ✅ 稳住。" : "⚠️ 今天还没打卡——断一天,从头再来。"}</p>`;
    const again = $("againBtn");
    if (again) again.addEventListener("click", () => {
      subBox.classList.remove("hidden");
      card.innerHTML = `<div class="mc-big">设定新一轮挑战</div>`;
    });
  } else {
    card.innerHTML = `<div class="mc-big">💪 坚持数据</div>
      <div class="mc-row"><span>当前连续打卡</span><b>${m ? m.consecutive : 0} 天</b></div>
      <p class="sub">想要更强的约束?开通下面的押金挑战。</p>`;
    subBox.classList.remove("hidden");
  }

  // 提醒列表
  const rl = $("reminderList");
  rl.innerHTML = "";
  const rs = S.reminders || [];
  if (!rs.length) rl.innerHTML = `<div class="empty-tip">暂无提醒。生成计划后会自动创建。</div>`;
  const wdName = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  rs.forEach((r) => {
    const row = document.createElement("div");
    row.className = "reminder-row" + (r.enabled ? "" : " off");
    const t = `${String(r.cron_hour).padStart(2, "0")}:${String(r.cron_min).padStart(2, "0")}`;
    row.innerHTML = `
      <div class="rm-main">
        <b>${esc(r.title)}</b>
        <span class="rm-when">${r.weekday >= 0 ? wdName[r.weekday] + " " : "每天 "}${t}</span>
      </div>
      <input type="time" value="${t}" />
      <label class="rm-toggle"><input type="checkbox" ${r.enabled ? "checked" : ""}/><span></span></label>`;
    row.querySelector('input[type="time"]').addEventListener("change", async (ev) => {
      const [h, mi] = ev.target.value.split(":").map(Number);
      try {
        const res = await call("update_reminder", r.id, h, mi, null);
        S.reminders = res.reminders;
        renderProgress();
        toast("提醒时间已更新", "ok");
      } catch (e) { toast(e.message, "error"); }
    });
    row.querySelector('input[type="checkbox"]').addEventListener("change", async (ev) => {
      try {
        const res = await call("update_reminder", r.id, null, null, ev.target.checked);
        S.reminders = res.reminders;
        renderProgress();
      } catch (e) { toast(e.message, "error"); }
    });
    rl.appendChild(row);
  });

  // 长期观察
  const sl = $("summaryList");
  sl.innerHTML = "";
  const sums = S.summaries || [];
  if (!sums.length) {
    sl.innerHTML = `<div class="empty-tip">🧠 还没有观察记录。坚持打卡 3 次,教练会写下第一条对你的观察。</div>`;
  }
  sums.forEach((s) => {
    let text = "";
    try {
      const d = typeof s.data === "string" ? JSON.parse(s.data) : (s.data || {});
      text = d.text || "";
    } catch (e) { text = ""; }
    if (!text) return;
    const div = document.createElement("div");
    div.className = "summary-item card";
    div.innerHTML = `<div class="sm-date">${esc(s.ref_date)}</div><div>${esc(text)}</div>`;
    sl.appendChild(div);
  });
}

// 开通押金
$("subBtn").addEventListener("click", async () => {
  const dep = parseInt($("depInput").value, 10);
  const thr = parseInt($("thrInput").value, 10);
  if (!dep || dep < 1) return toast("押金至少 1 元", "warn");
  if (!thr || thr < 3) return toast("挑战至少 3 天", "warn");
  const btn = $("subBtn");
  busy(btn, true, "开通中…");
  try {
    const r = await call("open_deposit", dep, thr);
    S = r; TODAY = r.today;
    renderAll();
    celebrate(`<div class="cele-emoji">🔥</div><h2>挑战开启!</h2><p>${dep} 元已托管(模拟)。连续 ${thr} 天打卡,全额退还。<br/>从今天开始,教练盯着你。</p>`);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    busy(btn, false);
  }
});

// ================= ⚙️ 设置 =================
async function loadConfig() {
  try {
    const cfg = await call("get_config");
    $("cfgEngine").value = cfg.engine || "auto";
    $("cfgBase").value = cfg.llm.base_url || "";
    $("cfgModel").value = cfg.llm.model || "";
    $("cfgTemp").value = cfg.llm.temperature ?? 0.7;
    $("cfgVision").value = String(!!cfg.llm.vision);
    const st = $("cfgStatus");
    if (cfg.using_llm) {
      st.textContent = `✅ 正在使用真实大模型(${cfg.llm.model})`;
      st.className = "cfg-status ok";
    } else {
      st.textContent = cfg.llm.api_key_set
        ? "⚠️ 已存 Key 但当前用的是内置模板(检查引擎模式)"
        : "ℹ️ 未配置 Key,当前为内置模板模式(离线可用)";
      st.className = "cfg-status warn";
    }
  } catch (e) { /* 静默 */ }
}

$("cfgSave").addEventListener("click", async () => {
  const btn = $("cfgSave");
  busy(btn, true, "保存中…");
  try {
    const payload = {
      engine: $("cfgEngine").value,
      persona_id: $("cfgPersona").value || undefined,
      llm: {
        base_url: $("cfgBase").value.trim(),
        model: $("cfgModel").value.trim(),
        temperature: parseFloat($("cfgTemp").value) || 0.7,
        vision: $("cfgVision").value === "true",
      },
    };
    const key = $("cfgKey").value.trim();
    if (key) payload.llm.api_key = key;
    const r = await call("save_config", payload);
    $("cfgKey").value = "";
    toast("已保存,引擎:" + r.active_engine, "ok");
    await loadConfig();
    await refreshState();
  } catch (e) {
    toast("保存失败:" + e.message, "error");
  } finally {
    busy(btn, false);
  }
});

$("cfgTest").addEventListener("click", async () => {
  const btn = $("cfgTest");
  busy(btn, true, "测试中…");
  const st = $("cfgStatus");
  try {
    const r = await call("test_llm");
    if (r.ok) {
      st.textContent = `✅ 连接正常(${r.latency_ms}ms):${r.message}`;
      st.className = "cfg-status ok";
    } else {
      st.textContent = `❌ ${r.message}`;
      st.className = "cfg-status warn";
    }
  } catch (e) {
    st.textContent = "❌ 测试失败:" + e.message;
    st.className = "cfg-status warn";
  } finally {
    busy(btn, false);
  }
});

// ================= 启动 =================
async function boot() {
  // pywebview 桥可能晚就绪
  for (let i = 0; i < 20; i++) {
    try { await call("get_state"); break; } catch (e) { await new Promise((r) => setTimeout(r, 250)); }
  }
  await refreshState();
  await loadConfig();
  // 有共创中的目标 → 直接进对话;没有任何目标 → 进目标页
  if (S) {
    if (S.onboarding) gotoView("chat");
    else if (!(S.goals || []).length) gotoView("goal");
  }
}

window.addEventListener("pywebviewready", boot);
document.addEventListener("DOMContentLoaded", () => { if (!isPywebview()) boot(); });
