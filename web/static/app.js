// 兼容子路径挂载（如 /xiaonian/app/）：API 基址取当前路径前缀
const API = window.location.pathname.replace(/\/$/, "");
let currentConfirmId = null;

const $ = (id) => document.getElementById(id);

async function jget(path) { const r = await fetch(API + path); return r.json(); }
async function jpost(path, body) {
  const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  return r.json();
}

// ---------- 初始化 ----------
async function init() {
  await loadProviders();
  await refreshPet();
  await refreshLife();
  await refreshMemory();
  await refreshObs();
  pollProactive();
  setInterval(pollProactive, 15000);
  setInterval(refreshLife, 30000);
  const inp = $("input");
  inp.addEventListener("input", () => { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 160) + "px"; });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  greet();
}

function greet() {
  addMessage("assistant", "你好呀，我是小念～ 我住在你电脑里，会记住你、主动关心你，也能帮你在电脑上把活干完。试试跟我说：『帮我看看桌面有哪些文件』或者『记住我喜欢喝美式』。");
  if (new URLSearchParams(location.search).get("preview") === "1") renderPreview();
}

// 演示预览模式：渲染一段代表性会话，便于展示/截图（不调用模型）
function renderPreview() {
  addMessage("user", "记住我喜欢喝美式，不加糖");
  addToolPill("remember", "好，我记住了：preference.咖啡 = 美式不加糖");
  addMessage("assistant", "记住啦～ 以后帮你点单/推荐都按这个来。");
  addMessage("user", "帮我看看桌面有哪些文件");
  addToolPill("computer_action", "list_dir → 20260630_小米黑客松 / Cursor / 飞书 / WPS Office …");
  addMessage("assistant", "桌面上主要有这些：小米黑客松项目文件夹、几个调研目录，以及 Cursor / 飞书 / WPS 等快捷方式。需要我帮你整理成分类文件夹吗？");
  addMessage("user", "中午想吃点辣的，帮我点外卖");
  addToolPill("use_skill", "已载入技能《order-food》步骤");
  addMessage("assistant", "结合你『爱吃辣、不加糖』的口味，给你挑了 3 家：① 川小馆·水煮牛肉 ¥38；② 湘味码头·剁椒鱼 ¥42；③ 麻辣香锅·自选 ¥35。选哪个？确认后我帮你下单（下单需你二次确认）。");
  const el = document.createElement("div");
  el.className = "msg proactive";
  el.innerHTML = `<div class="who">小念 · 主动关心</div><div class="body">忙了一天辛苦啦，今天有点累的样子，早点休息呀。明天上午 10 点的会我帮你记着了。</div>`;
  document.getElementById("messages").appendChild(el);
  applyPet({ face: "(っ◕‿◕)っ", message: "今天事情有点多，我帮你盯着，别太拼。", energy: 62, color: "#FF9AA2" });
  scrollDown();
  if (new URLSearchParams(location.search).get("preview") === "2") {
    showConfirm("demo", "写入文件 ~/Desktop/本周周报_20260630.md（约 820 字符）");
  }
}

// ---------- 模型 ----------
async function loadProviders() {
  const data = await jget("/api/providers");
  const sel = $("providerSel");
  sel.innerHTML = "";
  data.providers.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = `${p.name} · ${p.model}` + (p.available ? "" : "（未配置）");
    if (p.name === data.default) o.selected = true;
    sel.appendChild(o);
  });
  $("provBadge").textContent = "模型 · " + data.default;
  sel.onchange = async () => { await jpost("/api/provider", { provider: sel.value }); $("provBadge").textContent = "模型 · " + sel.value; };
  const h = await jget("/api/health");
  $("memBadge").textContent = "记忆 · " + h.memory_engine;
  $("backendInfo").textContent = `本地 · 执行后端 ${h.computer_backend} · 数据不出本机`;
}

// ---------- 对话（SSE 流式）----------
async function send() {
  const inp = $("input");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = ""; inp.style.height = "auto";
  $("sendBtn").disabled = true;
  addMessage("user", text);

  const provider = $("providerSel").value;
  const assistantEl = addMessage("assistant", "");
  try {
    const resp = await fetch(API + "/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, provider }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) handleSSE(part, assistantEl);
    }
  } catch (e) {
    assistantEl.querySelector(".body").textContent = "（连接失败：" + e + "）";
  }
  $("sendBtn").disabled = false;
  refreshMemory(); refreshObs(); refreshTools();
}

function handleSSE(part, assistantEl) {
  const lines = part.split("\n");
  let ev = "message", data = "";
  for (const l of lines) {
    if (l.startsWith("event:")) ev = l.slice(6).trim();
    else if (l.startsWith("data:")) data += l.slice(5).trim();
  }
  if (!data) return;
  let obj; try { obj = JSON.parse(data); } catch { return; }

  if (ev === "delta") {
    assistantEl.querySelector(".body").textContent += obj.text;
    scrollDown();
  } else if (ev === "tool") {
    addToolPill(obj.name, obj.result);
  } else if (ev === "confirm") {
    showConfirm(obj.confirm_id, obj.summary);
  } else if (ev === "pet") {
    applyPet(obj);
  }
}

// ---------- 消息渲染 ----------
function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  const who = role === "user" ? "你" : "小念";
  el.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
  el.querySelector(".body").textContent = text;
  $("messages").appendChild(el);
  scrollDown();
  return el;
}
function addToolPill(name, result) {
  const el = document.createElement("div");
  el.className = "tool-pill";
  el.textContent = `⚙ 调用 ${name}` + (result ? ` → ${String(result).slice(0, 60)}` : "");
  $("messages").appendChild(el);
  scrollDown();
}
function scrollDown() { const m = $("messages"); m.scrollTop = m.scrollHeight; }

// ---------- 确认 ----------
function showConfirm(id, summary) {
  currentConfirmId = id;
  $("confirmText").textContent = summary || "确认执行该写操作？";
  $("confirmModal").classList.remove("hidden");
}
async function resolveConfirm(approve) {
  $("confirmModal").classList.add("hidden");
  if (!currentConfirmId) return;
  const r = await jpost("/api/confirm", { confirm_id: currentConfirmId, approve });
  addMessage("assistant", approve ? `已执行：${r.result || ""}` : "好的，已取消这个操作。");
  currentConfirmId = null;
  refreshObs();
}

// ---------- 宠物 ----------
async function refreshPet() { applyPet(await jget("/api/pet")); }
function applyPet(p) {
  if (!p) return;
  $("petFace").textContent = p.face;
  $("petMsg").textContent = p.message;
  $("energyVal").textContent = p.energy;
  $("energyFill").style.width = p.energy + "%";
  $("petCard").style.boxShadow = `0 0 30px ${p.color}33`;
  $("petFace").style.color = p.color;
}

// ---------- 生活状态 ----------
async function refreshLife() {
  const l = await jget("/api/life");
  $("lifeBox").innerHTML = `
    <div class="row"><span>忙碌度</span><b>${(l.workload*100).toFixed(0)}%</b></div>
    <div class="row"><span>疲惫度</span><b>${(l.fatigue*100).toFixed(0)}%</b></div>
    <div class="row"><span>情绪</span><b>${l.mood}</b></div>
    <div class="row"><span>近1小时互动</span><b>${l.interactions_last_hour}</b></div>`;
  refreshPet();
}

// ---------- 记忆可视化 ----------
async function refreshMemory() {
  const data = await jget("/api/memory/profile");
  const box = $("tab-memory");
  const prof = data.profile || {};
  const cats = Object.keys(prof);
  if (!cats.length) { box.innerHTML = `<div class="empty">还没有关于你的记忆。<br>跟我多聊聊，或说「记住我…」，我会越来越懂你。</div>`; return; }
  box.innerHTML = cats.map((c) => {
    const kvs = Object.entries(prof[c]).map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
    return `<div class="card"><div class="cat">${c}</div>${kvs}</div>`;
  }).join("");
}

// ---------- 工具调用 ----------
let toolHistory = [];
function refreshTools() { renderTools(); }
function renderTools() {
  const box = $("tab-tools");
  if (!toolHistory.length) { box.innerHTML = `<div class="empty">还没有工具调用。<br>让我帮你干点活试试，比如整理文件。</div>`; return; }
  box.innerHTML = toolHistory.slice(-30).reverse().map((t) =>
    `<div class="tool-log"><b>${t.name}</b><br>${String(t.result).slice(0,140)}</div>`).join("");
}

// ---------- 可观测 ----------
async function refreshObs() {
  const data = await jget("/api/observability");
  const s = data.summary;
  $("tab-obs").innerHTML = `
    <div class="obs-stat"><span>模型调用次数</span><b>${s.calls}</b></div>
    <div class="obs-stat"><span>Prompt tokens</span><b>${s.prompt_tokens}</b></div>
    <div class="obs-stat"><span>Completion tokens</span><b>${s.completion_tokens}</b></div>
    <div class="obs-stat"><span>总 tokens</span><b>${s.total_tokens}</b></div>
    <div class="obs-stat"><span>预估成本</span><b>¥${s.cost_cny}</b></div>`;
  // 把最近工具事件并入
  (data.recent || []).filter(r => r.kind === "tool" || r.kind === "redact").forEach(() => {});
}

// ---------- 主动关心轮询 ----------
async function pollProactive() {
  try {
    const data = await jget("/api/proactive/pending");
    (data.messages || []).forEach((m) => {
      const el = document.createElement("div");
      el.className = "msg proactive";
      el.innerHTML = `<div class="who">小念 · 主动关心</div><div class="body"></div>`;
      el.querySelector(".body").textContent = m.text;
      $("messages").appendChild(el);
      scrollDown();
      applyPet({ face: faceFor(m.emotion), message: m.text, energy: 70, color: "#FF9AA2" });
    });
  } catch {}
}
function faceFor(e) {
  const m = { happy: "(◕ᴗ◕)", caring: "(っ◕‿◕)っ", worried: "(•́ω•̀)", tired: "(￣ヘ￣)", calm: "(｡•‿•｡)" };
  return m[e] || m.calm;
}
async function demoProactive(kind) {
  const r = await jpost("/api/proactive/demo", { kind });
  if (r.message) {
    const el = document.createElement("div");
    el.className = "msg proactive";
    el.innerHTML = `<div class="who">小念 · 主动关心</div><div class="body"></div>`;
    el.querySelector(".body").textContent = r.message.text;
    $("messages").appendChild(el);
    scrollDown();
  }
  if (r.pet) applyPet(r.pet);
}

// ---------- Tabs ----------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  ["memory", "tools", "obs"].forEach((n) => $("tab-" + n).classList.toggle("hidden", n !== name));
  if (name === "obs") refreshObs();
  if (name === "memory") refreshMemory();
  if (name === "tools") renderTools();
}

// 拦截 SSE 工具事件存入历史
const _handleSSE = handleSSE;
handleSSE = function (part, el) {
  if (part.includes("event: tool")) {
    const dataLine = part.split("\n").find((l) => l.startsWith("data:"));
    if (dataLine) { try { toolHistory.push(JSON.parse(dataLine.slice(5).trim())); } catch {} }
  }
  return _handleSSE(part, el);
};

init();
