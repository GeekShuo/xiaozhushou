/* 小助手前端逻辑。仅通过 window.pywebview.api 或 HTTP /api 与 Python 后端交互。 */

let state = { personas: [], persona_id: null, goal: null, plan: null, chat: [], reminders: [], goals: [], onboarding: false, profile: {} };
let checkinMood = null;
let calY = new Date().getFullYear();
let calM = new Date().getMonth() + 1;
let pendingImage = null;

/* ---------- 与后端通信 ---------- */
function whenReady() {
  if (!window.pywebview) return Promise.resolve();
  if (window.pywebview && window.pywebview.api) return Promise.resolve();
  if (window.pywebviewReady) return window.pywebviewReady;
  return new Promise((resolve) => window.addEventListener("pywebviewready", resolve));
}
async function call(method, ...args) {
  await whenReady();
  if (window.pywebview && window.pywebview.api) {
    return await window.pywebview.api[method](...args);
  }
  const resp = await fetch("/api", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: method, args }),
  });
  const data = await resp.json();
  if (data && data.error) throw new Error(data.error);
  return data.result;
}
async function refresh() {
  state = await call("get_state");
  renderAll();
}

/* ---------- 导航 ---------- */
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("view-" + btn.dataset.view).classList.add("active");
    if (btn.dataset.view === "chat") scrollChatBottom();
    if (btn.dataset.view === "calendar") loadCalendar();
  });
});

/* ---------- 渲染 ---------- */
function renderAll() {
  renderPersonaSidebar();
  renderPersonaChips();
  renderGoalList();
  renderPlan();
  renderChat();
  renderCheckin();
  renderReminders();
  renderProgress();
}

function renderPersonaSidebar() {
  const p = state.personas.find((x) => x.id === state.persona_id);
  document.getElementById("personaName").textContent = p ? `${p.emoji} ${p.name}` : "—";
  document.getElementById("personaTag").textContent = p ? p.tagline : "";
}

function renderPersonaChips() {
  const box = document.getElementById("personaChips");
  box.innerHTML = "";
  (state.personas || []).forEach((p) => {
    const c = document.createElement("div");
    c.className = "chip" + (p.id === state.persona_id ? " active" : "");
    c.textContent = `${p.emoji} ${p.name}`;
    c.onclick = async () => {
      await call("set_persona", p.id);
      await refresh();
      if (document.getElementById("view-chat").classList.contains("active")) renderChat();
    };
    box.appendChild(c);
  });
}

function renderGoalList() {
  const box = document.getElementById("goalList");
  box.innerHTML = "";
  (state.goals || []).forEach((g) => {
    const d = document.createElement("div");
    d.className = "goal-item" + (g.current ? " current" : "");
    const styleLabel = { workout_buddy: "🤝 运动搭子", idol: "⭐ 偶像", strict: "📏 严师", warm: "🫶 温柔" }[g.motivation_style] || "";
    d.innerHTML = `<div class="g-main"><div class="g-title">${escapeHtml(g.title)}</div>
      <div class="g-meta">${g.status === "draft" ? "共创中…" : "进行中"} ${styleLabel ? "· " + styleLabel : ""}</div></div>`;
    if (!g.current && (g.status === "active" || g.status === "draft")) {
      const sw = document.createElement("button");
      sw.className = "switch-btn";
      sw.textContent = "切换";
      sw.onclick = async () => { await call("switch_goal", g.id); await refresh(); };
      d.appendChild(sw);
    }
    if (g.current) {
      const tag = document.createElement("span");
      tag.className = "cur-tag";
      tag.textContent = "当前";
      d.appendChild(tag);
    }
    box.appendChild(d);
  });
}

function renderPlan() {
  const list = document.getElementById("planList");
  list.innerHTML = "";
  if (!state.plan || !state.plan.length) {
    list.innerHTML = `<p class="sub">还没有计划。去「目标」页立个 flag,我们会一起把它聊清楚。</p>`;
    return;
  }
  state.plan.forEach((phase) => {
    const div = document.createElement("div");
    div.className = "phase";
    let tasksHtml = (phase.tasks || []).map((t) => `
      <div class="task ${t.done ? "done" : ""}">
        <input type="checkbox" data-id="${t.id}" ${t.done ? "checked" : ""}/>
        <div>
          <div class="t-title">${escapeHtml(t.title)}</div>
          <div class="t-detail">${escapeHtml(t.detail || "")}</div>
          <div class="t-meta">${freqLabel(t.frequency)} · 约 ${t.duration_min} 分钟</div>
        </div>
      </div>`).join("");
    div.innerHTML = `<h3>${escapeHtml(phase.title)}</h3>
      <div class="weeks">${escapeHtml(phase.weeks || "")}</div>
      <div class="rationale">${escapeHtml(phase.rationale || "")}</div>${tasksHtml}`;
    list.appendChild(div);
  });
  list.querySelectorAll('input[type=checkbox]').forEach((cb) => {
    cb.onchange = async () => {
      const r = await call("toggle_task", cb.dataset.id);
      if (r.plan) { state.plan = r.plan; renderPlan(); renderCheckin(); }
    };
  });
}

function renderChat() {
  const box = document.getElementById("chatBox");
  box.innerHTML = "";
  (state.chat || []).forEach((m) => {
    const d = document.createElement("div");
    d.className = "msg " + m.role;
    d.textContent = m.content;
    box.appendChild(d);
  });
  document.getElementById("onboardBanner").classList.toggle("hidden", !state.onboarding);
  scrollChatBottom();
}

function renderCheckin() {
  const box = document.getElementById("checkinTasks");
  box.innerHTML = "";
  if (!state.plan) return;
  state.plan.forEach((phase) => {
    (phase.tasks || []).forEach((t) => {
      const label = document.createElement("label");
      label.innerHTML = `<input type="checkbox" class="ck" value="${t.id}" ${t.done ? "checked" : ""}/> ${escapeHtml(t.title)}`;
      box.appendChild(label);
    });
  });
}

function renderReminders() {
  const list = document.getElementById("reminderList");
  list.innerHTML = "";
  if (!state.reminders || !state.reminders.length) {
    list.innerHTML = `<p class="sub">暂无提醒。</p>`;
    return;
  }
  state.reminders.forEach((r) => {
    const d = document.createElement("div");
    d.className = "reminder-item";
    d.innerHTML = `<span>${escapeHtml(r.title)}</span><span>${pad(r.cron_hour)}:${pad(r.cron_min)}</span>`;
    list.appendChild(d);
  });
}

/* ---------- 进度 / 粘性 ---------- */
function renderProgress() {
  const m = state.membership;
  const card = document.getElementById("memberCard");
  const subBox = document.getElementById("subBox");
  if (!m) {
    card.innerHTML = `<div class="label">尚未开通押金机制。开通后,连续签到达标全额退还——用沉没成本对抗拖延。</div>`;
    subBox.classList.remove("hidden");
  } else {
    subBox.classList.add("hidden");
    const pct = Math.min(100, Math.round((m.consecutive / m.threshold) * 100));
    let stateHtml, cls;
    if (m.refunded) { stateHtml = `已连续 ${m.consecutive} 天达标,押金 ${m.deposit} 币已全额退还!`; cls = "done"; }
    else if (m.checked_today) { stateHtml = `今天已打卡 ✓ 保持住,还差 ${m.threshold - m.consecutive} 天退款`; cls = "ok"; }
    else { stateHtml = `已押 ${m.deposit} 币 · 还差 ${m.threshold - m.consecutive} 天退款`; cls = ""; }
    card.innerHTML = `
      <div class="row"><div class="big">${m.consecutive}</div><div class="label">/ ${m.threshold} 天连续签到</div></div>
      <div class="streak-bar"><div class="streak-fill" style="width:${pct}%"></div></div>
      <div class="state ${cls}">${stateHtml}</div>`;
  }
  const list = document.getElementById("summaryList");
  list.innerHTML = "";
  if (!state.summaries || !state.summaries.length) {
    list.innerHTML = `<p class="sub">还没有摘要。打卡几天后,教练会在这里记录你的长期进展。</p>`;
    return;
  }
  state.summaries.forEach((s) => {
    const d = s.data || {};
    const blk = (d.blockers || []).map((b) => `<div class="s-block">⚠ ${escapeHtml(b)}</div>`).join("");
    const prefs = (d.prefs || []).map((p) => `<span class="tag">${escapeHtml(p)}</span>`).join("");
    const div = document.createElement("div");
    div.className = "summary-item";
    div.innerHTML = `
      <div class="s-head"><span class="s-date">${s.ref_date}</span><span class="s-kind">${s.kind === "week" ? "周报" : "日报"}</span></div>
      <div class="s-progress">${escapeHtml(d.progress || "")}</div>
      ${blk}
      ${prefs ? `<div class="tag-line">${prefs}</div>` : ""}`;
    list.appendChild(div);
  });
}

/* ---------- 新建目标 / 多轮共创 ---------- */
document.getElementById("goalBtn").onclick = async () => {
  const v = document.getElementById("goalInput").value.trim();
  if (!v) return;
  const r = await call("start_goal", v);
  if (r.error) { alert(r.error); return; }
  state = r;
  renderAll();
  document.getElementById("goalInput").value = "";
  document.querySelector('.nav-item[data-view="chat"]').click();
};

/* ---------- 对话(共创 / 普通) ---------- */
document.getElementById("chatSend").onclick = sendChat;
document.getElementById("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendChat();
});

document.getElementById("chatImg").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    pendingImage = reader.result;
    const prev = document.getElementById("imgPrev");
    prev.src = reader.result;
    prev.classList.remove("hidden");
  };
  reader.readAsDataURL(file);
});

async function sendChat() {
  const inp = document.getElementById("chatInput");
  const v = inp.value.trim();
  if (!v && !pendingImage) return;

  if (state.onboarding) {
    // 共创阶段:走 goal_chat
    state.chat.push({ role: "user", content: v || "（图片）" });
    renderChat();
    inp.value = "";
    const img = pendingImage; pendingImage = null; document.getElementById("imgPrev").classList.add("hidden");
    const r = await call("goal_chat", v);
    if (r.error) { alert(r.error); return; }
    if (r.onboarding_complete) {
      state = await call("get_state");
      renderAll();
      document.querySelector('.nav-item[data-view="plan"]').click();
      alert("🎉 计划已生成!去看看你的专属计划吧。");
    } else {
      state.chat.push({ role: "assistant", content: r.reply });
      state.onboarding = true;
      renderChat();
    }
    return;
  }

  // 普通对话
  state.chat.push({ role: "user", content: v || "（图片）" });
  renderChat();
  inp.value = "";
  const img = pendingImage; pendingImage = null; document.getElementById("imgPrev").classList.add("hidden");
  const r = await call("chat", v, img || null);
  if (r.reply) { state.chat.push({ role: "assistant", content: r.reply }); renderChat(); }
}

/* ---------- 打卡 ---------- */
document.querySelectorAll(".mood").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".mood").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    checkinMood = b.dataset.mood;
  };
});
document.getElementById("checkinBtn").onclick = async () => {
  if (!checkinMood) { alert("先选个状态~"); return; }
  const ids = [...document.querySelectorAll(".ck:checked")].map((c) => c.value);
  const note = document.getElementById("checkinNote").value;
  const r = await call("checkin", checkinMood, note, ids);
  if (r.plan) state.plan = r.plan;
  if (r.membership) state.membership = r.membership;
  if (r.reply) { state.chat.push({ role: "assistant", content: r.reply }); }
  checkinMood = null;
  document.querySelectorAll(".mood").forEach((x) => x.classList.remove("active"));
  document.getElementById("checkinNote").value = "";
  renderCheckin();
  renderProgress();
  if (r.refunded_now) alert("🎉 连续签到达标!押金已全额退还。教练说:" + r.reply);
  else alert("打卡成功!教练说:" + r.reply);
};

/* ---------- 计划调整 ---------- */
document.getElementById("adaptBtn").onclick = async () => {
  const v = document.getElementById("adaptInput").value.trim();
  if (!v) return;
  const r = await call("adapt_plan", v);
  if (r.plan) state.plan = r.plan;
  if (r.reply) state.chat.push({ role: "assistant", content: r.reply });
  document.getElementById("adaptInput").value = "";
  renderPlan();
  alert(r.reply || (r.changed ? "已更新计划" : "计划未变"));
};

/* ---------- 日历 ---------- */
function loadCalendar() {
  document.getElementById("calTitle").textContent = `${calY} 年 ${calM} 月`;
  call("get_calendar", calY, calM).then((data) => {
    const grid = document.getElementById("calGrid");
    grid.innerHTML = "";
    const weekNames = ["一", "二", "三", "四", "五", "六", "日"];
    weekNames.forEach((w) => {
      const h = document.createElement("div");
      h.className = "cal-wd";
      h.textContent = w;
      grid.appendChild(h);
    });
    const first = new Date(calY, calM - 1, 1);
    let lead = (first.getDay() + 6) % 7; // 周一为起点
    for (let i = 0; i < lead; i++) {
      const e = document.createElement("div");
      e.className = "cal-cell empty";
      grid.appendChild(e);
    }
    const days = new Date(calY, calM, 0).getDate();
    for (let d = 1; d <= days; d++) {
      const cell = document.createElement("div");
      const iso = `${calY}-${pad(calM)}-${pad(d)}`;
      const items = (data.days && data.days[iso]) || [];
      cell.className = "cal-cell" + (items.length ? " has" : "") + (iso === data.today ? " today" : "");
      cell.innerHTML = `<div class="cal-d">${d}</div>` + (items.length ? `<div class="cal-dot">${items.length}</div>` : "");
      cell.onclick = () => showDay(data.days, iso);
      grid.appendChild(cell);
    }
  });
}
function showDay(daysMap, iso) {
  const box = document.getElementById("calInfo");
  const items = daysMap[iso] || [];
  if (!items.length) { box.innerHTML = `<p class="sub">${iso} 当天没有安排。</p>`; return; }
  box.innerHTML = `<h3>${iso}</h3>` + items.map((it) =>
    `<div class="cal-task"><span class="ct-g">${escapeHtml(it.goal_title)}</span> · ${escapeHtml(it.task_title)}
     <div class="ct-d">${escapeHtml(it.detail || "")} · ${freqLabel(it.frequency)} · ${it.duration_min} 分钟</div></div>`).join("");
}
document.getElementById("calPrev").onclick = () => { calM--; if (calM < 1) { calM = 12; calY--; } loadCalendar(); };
document.getElementById("calNext").onclick = () => { calM++; if (calM > 12) { calM = 1; calY++; } loadCalendar(); };

/* ---------- 设置 ---------- */
async function loadConfig() {
  const cfg = await call("get_config");
  if (!cfg) return;
  document.getElementById("cfgEngine").value = cfg.engine || "auto";
  document.getElementById("cfgBase").value = cfg.llm?.base_url || "https://api.deepseek.com/v1";
  document.getElementById("cfgModel").value = cfg.llm?.model || "deepseek-chat";
  document.getElementById("cfgTemp").value = cfg.llm?.temperature ?? 0.7;
  document.getElementById("cfgVision").value = cfg.llm?.vision ? "true" : "false";
  const sel = document.getElementById("cfgPersona");
  sel.innerHTML = "";
  (cfg.personas || []).forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.emoji} ${p.name}`;
    if (p.id === cfg.persona_id) o.selected = true;
    sel.appendChild(o);
  });
  showCfgStatus(cfg);
}
function showCfgStatus(cfg) {
  const el = document.getElementById("cfgStatus");
  if (!el) return;
  if (cfg.using_llm) {
    el.className = "cfg-status ok";
    el.textContent = `✅ 当前已接入真实大模型（${cfg.active_engine}）`;
  } else {
    el.className = "cfg-status warn";
    el.textContent = "🧩 当前使用内置模板引擎（未配置有效 API Key 或已设为仅模板）";
  }
}
document.getElementById("cfgSave").onclick = async () => {
  const payload = {
    engine: document.getElementById("cfgEngine").value,
    persona_id: document.getElementById("cfgPersona").value,
    llm: {
      base_url: document.getElementById("cfgBase").value.trim(),
      model: document.getElementById("cfgModel").value.trim(),
      api_key: document.getElementById("cfgKey").value,
      temperature: parseFloat(document.getElementById("cfgTemp").value || "0.7"),
      vision: document.getElementById("cfgVision").value === "true",
    },
  };
  const r = await call("save_config", payload);
  if (r && r.ok) {
    const cfg = await call("get_config");
    showCfgStatus(cfg);
    alert("已保存，引擎已重载：" + r.active_engine + "\n（下次对话即生效）");
    await refresh();
  } else {
    alert("保存失败");
  }
};

/* ---------- 工具 ---------- */
function freqLabel(f) { return { daily: "每天", weekly: "每周", once: "一次" }[f] || f; }
function pad(n) { return String(n).padStart(2, "0"); }
function scrollChatBottom() { const b = document.getElementById("chatBox"); b.scrollTop = b.scrollHeight; }
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* 后端提醒触发前端横幅 */
window.__toast = function (title) {
  const t = document.getElementById("toast");
  t.textContent = "⏰ " + title;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 8000);
};

/* ---------- 启动 ---------- */
(async function init() {
  await refresh();
  loadConfig();
})();
