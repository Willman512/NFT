/* The office, client side. Plain DOM, no framework, no build step —
   it has to keep working on a machine with no network. */

const POLL_MS = 3000;

const el = (id) => document.getElementById(id);
const state = { staff: {}, openPersona: null, lastFeedId: 0, sending: false };

/* ------------------------------------------------------------ helpers */
function money(cents) {
  if (cents === null || cents === undefined) return "—";
  const sign = cents < 0 ? "-" : "";
  const v = Math.abs(cents) / 100;
  return `${sign}$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function signClass(cents) {
  if (!cents) return "";
  return cents > 0 ? "pos" : "neg";
}

function ago(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/* Everything from the server is rendered as text, never as markup. */
function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

/* -------------------------------------------------------------- floor */
function renderFloor(data) {
  const floor = el("floor");
  floor.replaceChildren();
  // The manager takes the top of the floor; everyone else lines up beneath.
  const order = [...data.staff].sort((a, b) =>
    (a.key === "manager" ? -1 : 0) - (b.key === "manager" ? -1 : 0));
  for (const person of order) {
    state.staff[person.key] = person;
    const latest = data.latest_by_persona[person.key];

    const desk = node("button", person.key === "manager" ? "desk boss" : "desk");
    desk.style.setProperty("--seat", person.colour);
    desk.setAttribute("aria-label", `Talk to ${person.name}`);

    const top = node("div", "desk-top");
    top.append(node("span", "avatar", person.avatar));
    const who = node("div");
    who.append(node("div", "desk-name", person.name), node("div", "desk-title", person.title));
    top.append(who);

    const bubble = node("div", latest ? "bubble" : "bubble quiet",
      latest ? latest.text : "Nothing to say yet.");

    const foot = node("div", "desk-foot");
    foot.append(node("span", null, latest ? ago(latest.ts) : "idle"), node("span", null, "talk →"));

    desk.append(top, bubble);
    const job = (data.desks || {})[person.key];
    const doing = node("div", "desk-doing");
    if (job) {
      doing.append(node("b", null, job.status === "blocked" ? "waiting on you: " : "on now: "),
                   node("span", null, job.title));
    } else {
      doing.append(node("span", null, "nothing on their desk"));
    }
    desk.append(doing, foot);
    desk.addEventListener("click", () => openDrawer(person.key));
    floor.append(desk);
  }
}

/* -------------------------------------------------------------- ideas */
const MOOD = { for: "#22c55e", against: "#ef4444", unsure: "#f59e0b" };

function renderIdeas(data) {
  const wrap = el("ideas");
  wrap.replaceChildren();
  if (!data.ideas.length) {
    wrap.append(node("div", "empty",
      "Nothing yet. Write an idea above and they will pick it apart for you."));
    return;
  }

  for (const idea of data.ideas) {
    const card = node("button", "idea");
    const tally = idea.tally || {};
    const lead = (tally.against > tally.for) ? "against"
      : (tally.for > 0 ? "for" : "unsure");
    card.style.setProperty("--mood", MOOD[lead] || "var(--line)");

    const top = node("div", "idea-top");
    top.append(node("span", "idea-title", idea.title));
    top.append(node("span", `badge ${idea.status}`, idea.status.replace("_", " ")));
    card.append(top);

    const meta = node("div", "idea-meta");
    if (idea.reviews && idea.reviews.length) {
      const pips = node("span", "stances");
      for (const r of idea.reviews) pips.append(node("span", `pip ${r.stance}`));
      meta.append(pips);
      meta.append(node("span", null,
        `${tally.for || 0} for · ${tally.against || 0} against · ${tally.unsure || 0} unsure`));
    } else {
      meta.append(node("span", null, "not read yet"));
    }

    const p = idea.progress || {};
    const total = (p.todo || 0) + (p.doing || 0) + (p.blocked || 0) + (p.done || 0);
    if (total) {
      meta.append(node("span", null, `${p.done || 0}/${total} done`
        + ((p.blocked || 0) ? ` · ${p.blocked} waiting on you` : "")));
    }
    meta.append(node("span", null, ago(idea.ts)));
    card.append(meta);

    if (idea.decision) card.append(node("div", "idea-why", idea.decision));

    card.addEventListener("click", () => openIdea(idea.id));
    wrap.append(card);
  }
}

async function openIdea(ideaId) {
  const body = el("ideabody");
  body.replaceChildren(node("div", "empty", "Loading…"));
  el("ideadrawer").classList.add("open");
  el("ideadrawer").setAttribute("aria-hidden", "false");

  let idea;
  try {
    idea = await api(`/api/ideas/${ideaId}`);
  } catch (err) {
    body.replaceChildren(node("div", "empty", err.message));
    return;
  }

  el("idea-title").textContent = idea.title;
  el("idea-status").textContent = idea.status.replace("_", " ");
  body.replaceChildren();

  const quote = node("section");
  quote.append(node("div", "blockhead", "As you put it"));
  quote.append(node("p", "ideaquote", idea.body));
  body.append(quote);

  if (idea.decision) {
    const call = node("section");
    call.append(node("div", "blockhead", "What they decided"));
    call.append(node("p", "ideaquote", idea.decision));
    body.append(call);
  }

  if (idea.reviews.length) {
    const block = node("section");
    block.append(node("div", "blockhead", "What each of them said"));
    for (const r of idea.reviews) {
      const person = state.staff[r.persona] || { name: r.persona, avatar: "•", colour: "#888" };
      const card = node("div", "review");
      card.style.setProperty("--seat", person.colour);
      const top = node("div", "review-top");
      top.append(node("span", "avatar", person.avatar),
                 node("span", "review-who", person.name),
                 node("span", `chip ${r.stance}`, r.stance));
      card.append(top, node("div", "review-head", r.headline));
      if (r.points.length) {
        const ul = node("ul");
        for (const point of r.points) ul.append(node("li", null, point));
        card.append(ul);
      }
      if (r.questions.length) {
        card.append(node("div", "review-qs", "Asking: " + r.questions.join(" · ")));
      }
      block.append(card);
    }
    body.append(block);
  }

  if (idea.assignments.length) {
    const block = node("section");
    block.append(node("div", "blockhead", "Who has what"));
    for (const a of idea.assignments) {
      const person = state.staff[a.owner] || { name: a.owner, avatar: "•" };
      const job = node("div", `job ${a.status}`);
      const top = node("div", "job-top");
      top.append(node("span", "avatar", person.avatar),
                 node("span", "job-title", a.title),
                 node("span", `chip ${a.status === "done" ? "for" : (a.status === "blocked" ? "unsure" : "")}`,
                      a.status));
      top.append(node("span", "job-owner", person.name));
      job.append(top);
      for (const note of a.notes) job.append(node("div", "job-note", note));

      if (a.status === "blocked" && a.blocked_on === "you") {
        const form = node("form", "answerbox");
        const input = node("input");
        input.placeholder = "Answer them…";
        input.setAttribute("aria-label", "Your answer");
        const send = node("button", "btn btn-sm btn-primary", "Reply");
        send.type = "submit";
        form.append(input, send);
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          if (!input.value.trim()) return;
          send.disabled = true;
          try {
            await api(`/api/assignments/${a.id}/answer`,
              { method: "POST", body: JSON.stringify({ text: input.value.trim() }) });
            await openIdea(ideaId);
            refresh();
          } catch (err) { banner(err.message); send.disabled = false; }
        });
        job.append(form);
      }
      block.append(job);
    }
    body.append(block);
  }
}

function closeIdea() {
  el("ideadrawer").classList.remove("open");
  el("ideadrawer").setAttribute("aria-hidden", "true");
}

/* ----------------------------------------------------------- ventures */
function renderVentures(data) {
  const wrap = el("ventures");
  wrap.replaceChildren();
  // Most offices have none; an empty heading is just noise.
  el("ventures-head").classList.toggle("hidden", !data.ventures.length);
  if (!data.ventures.length) {
    el("income-venture").replaceChildren();
    return;
  }
  for (const v of data.ventures) {
    const card = node("div", v.survival_state === "eliminated" ? "venture dead" : "venture");

    const top = node("div", "venture-top");
    top.append(node("span", "venture-name", v.name),
               node("span", `badge ${v.survival_state}`, v.survival_state));
    if (v.in_grace && v.survival_state !== "eliminated") {
      top.append(node("span", "badge", "grace"));
    }
    card.append(top);

    // How far it has got toward paying for itself.
    const ratio = v.expense_cents > 0
      ? Math.min(v.income_cents / v.expense_cents, 1)
      : (v.income_cents > 0 ? 1 : 0);
    const meter = node("div", "meter");
    const bar = node("span");
    bar.style.width = `${Math.round(ratio * 100)}%`;
    if (ratio >= 1) bar.className = "";
    else if (ratio > 0) bar.className = "short";
    else bar.className = "none";
    meter.append(bar);
    card.append(meter);

    const nums = node("div", "venture-nums");
    nums.append(node("span", null, `in ${money(v.income_cents)}`),
                node("span", null, `out ${money(v.expense_cents)}`),
                node("span", null, `float ${money(v.float_cents)}`));
    card.append(nums);

    if (v.survival_reason) card.append(node("div", "venture-why", v.survival_reason));

    if (v.survival_state === "eliminated") {
      const revive = node("button", "btn btn-sm", "Give it another go");
      revive.addEventListener("click", async () => {
        revive.disabled = true;
        try { await api(`/api/venture/${encodeURIComponent(v.name)}/revive`, { method: "POST" }); refresh(); }
        catch (err) { banner(err.message); revive.disabled = false; }
      });
      const actions = node("div", "task-actions");
      actions.append(revive);
      card.append(actions);
    }
    wrap.append(card);
  }

  const select = el("income-venture");
  const chosen = select.value;
  select.replaceChildren();
  for (const v of data.ventures) select.append(new Option(v.name, v.name));
  if (chosen) select.value = chosen;
}

/* -------------------------------------------------------------- tasks */
function renderTasks(data) {
  const wrap = el("tasks");
  wrap.replaceChildren();
  if (!data.tasks.length) {
    wrap.append(node("div", "empty", "Nothing is waiting on you."));
    return;
  }
  for (const t of data.tasks) {
    const card = node("div", "task");
    card.append(node("div", "task-title", t.title), node("div", "task-body", t.body));
    if (t.artifact) card.append(node("code", "task-path", t.artifact));

    const actions = node("div", "task-actions");
    if (t.url) {
      const link = node("a", "btn btn-sm", "Open KDP ↗");
      link.href = t.url; link.target = "_blank"; link.rel = "noreferrer noopener";
      actions.append(link);
    }
    for (const [label, status] of [["Done", "done"], ["Skip", "skipped"]]) {
      const b = node("button", `btn btn-sm${status === "done" ? " btn-primary" : ""}`, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await api(`/api/tasks/${t.id}`, { method: "POST", body: JSON.stringify({ status }) }); refresh(); }
        catch (err) { banner(err.message); b.disabled = false; }
      });
      actions.append(b);
    }
    if (t.payoff_cents) actions.append(node("span", "hint", `worth about ${money(t.payoff_cents)}`));
    card.append(actions);
    wrap.append(card);
  }
}

/* --------------------------------------------------------------- feed */
function renderFeed(data) {
  const feed = el("feed");
  feed.replaceChildren();
  if (!data.feed.length) {
    feed.append(node("div", "empty", "Quiet so far. Run a standup to get them going."));
    return;
  }
  for (const r of data.feed) {
    const person = state.staff[r.persona] || { avatar: "•", name: r.persona, colour: "#888" };
    const row = node("div", `remark kind-${r.kind}`);
    row.style.setProperty("--seat", person.colour);
    row.append(node("span", "avatar", person.avatar));
    const body = node("div", "remark-body");
    body.append(node("div", "remark-who", `${person.name} · ${ago(r.ts)}${r.cycle ? ` · standup ${r.cycle}` : ""}`),
                node("div", "remark-text", r.text));
    row.append(body);
    feed.append(row);
  }
}

/* ------------------------------------------------------------- wallet */
function renderWallet(w) {
  el("w-mode").textContent = w.mode || "none";
  el("w-addr").textContent = w.address || "not configured — set office.wallet_address in forge.yaml";
  // Never show a confident 0.00 for a balance we could not actually read.
  if (!w.address) el("w-bal").textContent = "—";
  else if (!w.online && !w.fetched_ok) el("w-bal").textContent = "offline — balance unknown";
  else el("w-bal").textContent =
    `${(w.sol ?? 0).toFixed(4)} SOL${w.usd != null ? ` · $${w.usd.toFixed(2)}` : ""}`
    + (w.online ? "" : " (last known)");
  const dot = el("w-dot");
  dot.className = `wallet-dot ${w.address ? (w.online ? "online" : "offline") : ""}`;
  dot.title = w.error || (w.online ? "connected" : "offline");
  const link = el("w-link");
  if (w.explorer_url) { link.href = w.explorer_url; link.classList.remove("hidden"); }
  else link.classList.add("hidden");
  el("w-copy").classList.toggle("hidden", !w.address);
}

/* ------------------------------------------------------------- drawer */
async function openDrawer(personaKey) {
  const person = state.staff[personaKey];
  if (!person) return;
  state.openPersona = personaKey;
  el("drawer-avatar").textContent = person.avatar;
  el("drawer-name").textContent = person.name;
  el("drawer-title").textContent = person.title;
  el("drawer-remit").textContent = person.remit;
  el("drawer").classList.add("open");
  el("drawer").setAttribute("aria-hidden", "false");
  el("composer-input").focus();
  await loadThread(personaKey);
}

function closeDrawer() {
  state.openPersona = null;
  el("drawer").classList.remove("open");
  el("drawer").setAttribute("aria-hidden", "true");
}

async function loadThread(personaKey) {
  const thread = el("thread");
  try {
    const data = await api(`/api/chat/${encodeURIComponent(personaKey)}`);
    thread.replaceChildren();
    if (!data.messages.length) {
      const person = state.staff[personaKey];
      thread.append(node("div", "msg staff", person.greeting || "Ask me anything."));
    }
    for (const m of data.messages) {
      thread.append(node("div", `msg ${m.speaker === "user" ? "user" : "staff"}`, m.text));
    }
    thread.scrollTop = thread.scrollHeight;
  } catch (err) {
    banner(err.message);
  }
}

async function send(event) {
  event.preventDefault();
  const input = el("composer-input");
  const text = input.value.trim();
  if (!text || state.sending || !state.openPersona) return;
  const persona = state.openPersona;
  const thread = el("thread");

  state.sending = true;
  input.value = "";
  thread.append(node("div", "msg user", text));
  const pending = node("div", "msg staff pending", "thinking…");
  thread.append(pending);
  thread.scrollTop = thread.scrollHeight;

  try {
    const data = await api(`/api/chat/${encodeURIComponent(persona)}`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    pending.classList.remove("pending");
    pending.textContent = data.answer;
  } catch (err) {
    pending.classList.remove("pending");
    pending.textContent = `Could not answer: ${err.message}`;
  } finally {
    state.sending = false;
    thread.scrollTop = thread.scrollHeight;
    input.focus();
  }
}

/* --------------------------------------------------------------- shell */
function banner(message) {
  const b = el("banner");
  if (!message) { b.classList.add("hidden"); return; }
  b.textContent = message;
  b.classList.remove("hidden");
}

function renderHeader(data) {
  const t = data.treasury;
  el("t-equity").textContent = money(t.equity_cents);
  const net = el("t-net");
  net.textContent = money(t.net_pnl_cents);
  net.className = `tile-value ${signClass(t.net_pnl_cents)}`;
  el("t-cash").textContent = money(t.cash_cents);
  el("t-cycle").textContent = data.busy
    ? "running…"
    : (data.seconds_until_next === null ? "paused"
       : (data.seconds_until_next > 0 ? `in ${Math.ceil(data.seconds_until_next / 60)}m` : "due"));
  el("brainchip").textContent = data.brain;

  const toggle = el("btn-toggle");
  toggle.textContent = data.running ? "Pause the office" : "Start the office";
  toggle.classList.toggle("btn-primary", !data.running);
  el("btn-run").disabled = !!data.busy;
  el("feedhint").textContent = `Standup ${data.cycle} · every ${data.interval_minutes} min`;

  if (t.halted) banner(`The office is halted: ${t.halt_reason}`);
  else if (data.last_error) banner(`Last standup failed — ${data.last_error}`);
  else banner(null);
}

async function refresh() {
  try {
    const data = await api("/api/state");
    renderHeader(data);
    renderIdeas(data);
    renderFloor(data);
    renderVentures(data);
    renderTasks(data);
    renderFeed(data);
    renderWallet(data.wallet || {});
  } catch (err) {
    banner(`Cannot reach the office: ${err.message}`);
  }
}

/* --------------------------------------------------------------- wire */
el("btn-run").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try { await api("/api/standup", { method: "POST" }); } catch (err) { banner(err.message); }
  setTimeout(refresh, 600);
});

el("btn-toggle").addEventListener("click", async () => {
  const running = el("btn-toggle").textContent.startsWith("Pause");
  try { await api("/api/running", { method: "POST", body: JSON.stringify({ running: !running }) }); }
  catch (err) { banner(err.message); }
  refresh();
});

el("idea-close").addEventListener("click", closeIdea);

el("pitchform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = el("pitch-body");
  const body = box.value.trim();
  if (!body) { banner("Write the idea first."); return; }
  const button = e.target.querySelector("button");
  button.disabled = true;
  el("pitch-hint").textContent = "Taking it to them…";
  try {
    await api("/api/ideas", { method: "POST", body: JSON.stringify({ body }) });
    box.value = "";
    el("pitch-hint").textContent = "In. They are reading it now.";
    banner(null);
    setTimeout(refresh, 700);
  } catch (err) {
    banner(err.message);
    el("pitch-hint").textContent = "The more concrete, the more use they are.";
  } finally {
    button.disabled = false;
  }
});

el("drawer-close").addEventListener("click", closeDrawer);
el("composer").addEventListener("submit", send);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  closeDrawer();
  closeIdea();
});

el("w-copy").addEventListener("click", async () => {
  const addr = el("w-addr").textContent;
  try { await navigator.clipboard.writeText(addr); el("w-copy").textContent = "copied"; }
  catch { el("w-copy").textContent = "select it"; }
  setTimeout(() => { el("w-copy").textContent = "copy"; }, 1500);
});

el("incomeform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const venture = el("income-venture").value;
  const amount = parseFloat(el("income-amount").value);
  const memo = el("income-memo").value.trim();
  if (!venture || !(amount > 0)) { banner("Pick a venture and an amount above zero."); return; }
  try {
    await api("/api/income", { method: "POST", body: JSON.stringify({ venture, amount, memo }) });
    el("income-amount").value = ""; el("income-memo").value = "";
    banner(null); refresh();
  } catch (err) { banner(err.message); }
});

refresh();
setInterval(() => { if (!state.sending) refresh(); }, POLL_MS);
