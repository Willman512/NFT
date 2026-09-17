/* The office, drawn.
 *
 * Everything on screen is derived from real state: where someone stands,
 * whether they are typing or waiting, and who is at the table. Nothing here
 * invents activity to look busy — an idle office looks idle, which is the
 * only way watching it tells you anything.
 *
 * Plain SVG built in JS so the coordinates live in one place, with CSS doing
 * the animation. No canvas, no framework, no build step.
 */

const W = 1000;
const H = 640;
const NS = "http://www.w3.org/2000/svg";

/* Where each desk sits, and where its owner stands to use it. */
const DESKS = {
  manager:    { x: 500, y: 150, stand: { x: 500, y: 214 }, label: "Del" },
  researcher: { x: 160, y: 300, stand: { x: 252, y: 312 }, label: "Tam" },
  salesman:   { x: 840, y: 300, stand: { x: 748, y: 312 }, label: "Rory" },
  banker:     { x: 160, y: 470, stand: { x: 252, y: 482 }, label: "Ines" },
  maker:      { x: 840, y: 470, stand: { x: 748, y: 482 }, label: "Juno" },
};

/* Around the meeting table, for a standup. */
const SEATS = {
  manager:    { x: 500, y: 320 },
  researcher: { x: 386, y: 356 },
  salesman:   { x: 614, y: 356 },
  banker:     { x: 420, y: 452 },
  maker:      { x: 580, y: 452 },
};

/* In front of you, when someone needs an answer. */
/* In front of you, when someone needs an answer. Far enough above the desk
   that their name plate does not land on top of it. */
const ASKING = {
  manager:    { x: 500, y: 528 },
  researcher: { x: 392, y: 534 },
  salesman:   { x: 608, y: 534 },
  banker:     { x: 440, y: 554 },
  maker:      { x: 560, y: 554 },
};

const TABLE = { x: 500, y: 388 };
/* Bubbles are nudged apart per person, otherwise a standup buries the room
   under five overlapping speech bubbles and you can see none of it. */
const BUBBLE_BIAS = {
  manager:    { x: 0,    y: -34 },
  researcher: { x: -102, y: -2 },
  salesman:   { x: 102,  y: -2 },
  banker:     { x: -112, y: 34 },
  maker:      { x: 112,  y: 34 },
};
/* How long the room stays gathered after a standup lands. */
const MEETING_HOLD_MS = 8000;
const TRAY = { x: 92, y: 132 };

const el = (tag, attrs = {}, parent = null) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};

export class Scene {
  constructor(root, { onPersonClick } = {}) {
    this.root = root;
    this.onPersonClick = onPersonClick || (() => {});
    this.actors = {};
    this.bubbles = {};
    this.lastRemarkId = {};
    this.staff = {};
    this.lastCycle = null;
    this.meetingUntil = 0;
    this.holdTimer = null;
    this.build();
  }

  /* ------------------------------------------------------------ scenery */
  build() {
    this.root.innerHTML = "";
    this.root.classList.add("scene");

    const svg = el("svg", {
      viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: "xMidYMid meet",
      role: "img",
      "aria-label": "A view of the office floor",
    }, this.root);
    this.svg = svg;

    const defs = el("defs", {}, svg);
    const glow = el("filter", { id: "softglow", x: "-30%", y: "-30%", width: "160%", height: "160%" }, defs);
    el("feGaussianBlur", { stdDeviation: "6", result: "b" }, glow);
    const merge = el("feMerge", {}, glow);
    el("feMergeNode", { in: "b" }, merge);
    el("feMergeNode", { in: "SourceGraphic" }, merge);

    /* floor */
    el("rect", { x: 0, y: 0, width: W, height: H, class: "sc-floor" }, svg);
    const boards = el("g", { class: "sc-boards" }, svg);
    for (let x = 0; x <= W; x += 50) el("line", { x1: x, y1: 96, x2: x, y2: H, class: "sc-board" }, boards);
    for (let y = 96; y <= H; y += 50) el("line", { x1: 0, y1: y, x2: W, y2: y, class: "sc-board" }, boards);

    /* back wall with windows */
    el("rect", { x: 0, y: 0, width: W, height: 96, class: "sc-wall" }, svg);
    for (const wx of [250, 640]) {
      el("rect", { x: wx, y: 18, width: 170, height: 60, rx: 6, class: "sc-window" }, svg);
      el("line", { x1: wx + 85, y1: 18, x2: wx + 85, y2: 78, class: "sc-mullion" }, svg);
    }

    /* the clock tells you when they next meet */
    this.clock = el("g", { class: "sc-clock", transform: "translate(500, 48)" }, svg);
    el("circle", { r: 26, class: "sc-clockface" }, this.clock);
    el("circle", { r: 26, class: "sc-clockring" }, this.clock);
    this.clockArc = el("path", { class: "sc-clockarc", d: "" }, this.clock);
    this.clockHand = el("line", { x1: 0, y1: 0, x2: 0, y2: -17, class: "sc-clockhand" }, this.clock);
    el("circle", { r: 2.4, class: "sc-clockpin" }, this.clock);

    /* the in-tray where your ideas land */
    const tray = el("g", { transform: `translate(${TRAY.x}, ${TRAY.y})` }, svg);
    el("rect", { x: -54, y: -34, width: 108, height: 70, rx: 9, class: "sc-tray" }, tray);
    el("text", { x: 0, y: -44, class: "sc-caption" }, tray).textContent = "IDEAS IN";
    this.trayCards = el("g", {}, tray);
    this.trayCount = el("text", { x: 0, y: 54, class: "sc-caption" }, tray);

    /* the meeting table */
    const table = el("g", { transform: `translate(${TABLE.x}, ${TABLE.y})` }, svg);
    this.tableRing = el("ellipse", { rx: 150, ry: 92, class: "sc-tablering" }, table);
    el("ellipse", { rx: 128, ry: 72, class: "sc-tableshadow" }, table);
    el("ellipse", { cy: -6, rx: 128, ry: 72, class: "sc-table" }, table);
    this.tableLabel = el("text", { y: 2, class: "sc-tablelabel" }, table);

    /* a couple of plants, because every office has them */
    for (const [px, py] of [[70, 560], [930, 148]]) this.plant(svg, px, py);

    /* where you sit */
    const you = el("g", { transform: `translate(500, ${H - 22})` }, svg);
    el("rect", { x: -92, y: -26, width: 184, height: 34, rx: 10, class: "sc-youdesk" }, you);
    el("text", { y: -4, class: "sc-youlabel" }, you).textContent = "YOU";

    /* desks, then people on top of them */
    this.deskLayer = el("g", {}, svg);
    for (const [key, desk] of Object.entries(DESKS)) this.desk(this.deskLayer, key, desk);
    this.actorLayer = el("g", {}, svg);

    /* HTML speech bubbles float above the drawing */
    this.overlay = document.createElement("div");
    this.overlay.className = "sc-overlay";
    this.root.appendChild(this.overlay);
  }

  plant(svg, x, y) {
    const g = el("g", { transform: `translate(${x}, ${y})`, class: "sc-plant" }, svg);
    el("path", { d: "M-13 0 L-9 -26 L9 -26 L13 0 Z", class: "sc-pot" }, g);
    for (const [rot, len] of [[-32, 30], [0, 38], [30, 28]]) {
      el("ellipse", {
        cx: 0, cy: -34, rx: 7, ry: len / 2,
        transform: `rotate(${rot}) translate(0 ${-len / 2 + 4})`,
        class: "sc-leaf",
      }, g);
    }
  }

  desk(parent, key, desk) {
    const g = el("g", { transform: `translate(${desk.x}, ${desk.y})`, class: "sc-desk", "data-desk": key }, parent);
    el("rect", { x: -58, y: -30, width: 116, height: 60, rx: 8, class: "sc-desktop" }, g);
    el("rect", { x: -22, y: -20, width: 44, height: 28, rx: 3, class: "sc-monitor" }, g);
    el("rect", { x: -16, y: 12, width: 32, height: 7, rx: 2, class: "sc-keyboard" }, g);
    el("circle", { cx: 40, cy: 14, r: 5, class: "sc-mug" }, g);
  }

  /* ------------------------------------------------------------- people */
  ensureActors(staff) {
    for (const person of staff) {
      this.staff[person.key] = person;
      if (this.actors[person.key]) continue;
      const desk = DESKS[person.key];
      if (!desk) continue;

      const g = el("g", {
        class: "actor",
        "data-person": person.key,
        tabindex: "0",
        role: "button",
        "aria-label": `${person.name}, ${person.title}. Open a conversation.`,
      }, this.actorLayer);
      g.style.setProperty("--seat", person.colour);
      g.style.transform = `translate(${desk.stand.x}px, ${desk.stand.y}px)`;

      el("ellipse", { cy: 26, rx: 17, ry: 6, class: "ac-shadow" }, g);
      const ring = el("circle", { cy: 4, r: 30, class: "ac-ring" }, g);
      const legs = el("g", { class: "ac-legs" }, g);
      el("rect", { x: -8, y: 12, width: 6, height: 14, rx: 3, class: "ac-leg ac-leg-l" }, legs);
      el("rect", { x: 2, y: 12, width: 6, height: 14, rx: 3, class: "ac-leg ac-leg-r" }, legs);
      const body = el("g", { class: "ac-body" }, g);
      el("rect", { x: -13, y: -8, width: 26, height: 24, rx: 10, class: "ac-torso" }, body);
      el("rect", { x: -20, y: -4, width: 8, height: 16, rx: 4, class: "ac-arm ac-arm-l" }, body);
      el("rect", { x: 12, y: -4, width: 8, height: 16, rx: 4, class: "ac-arm ac-arm-r" }, body);
      el("circle", { cy: -18, r: 12, class: "ac-head" }, body);
      el("path", { d: "M-12 -20 a12 12 0 0 1 24 0 z", class: "ac-hair" }, body);
      el("circle", { cx: -4, cy: -17, r: 1.7, class: "ac-eye" }, body);
      el("circle", { cx: 4, cy: -17, r: 1.7, class: "ac-eye" }, body);
      const mark = el("g", { class: "ac-mark" }, g);
      el("circle", { cx: 0, cy: -40, r: 11, class: "ac-markbg" }, mark);
      el("text", { x: 0, y: -36, class: "ac-markglyph" }, mark).textContent = "?";
      el("text", { y: 42, class: "ac-name" }, g).textContent = desk.label;

      g.addEventListener("click", () => this.onPersonClick(person.key));
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.onPersonClick(person.key); }
      });
      this.actors[person.key] = { g, ring, at: { ...desk.stand } };
    }
  }

  moveTo(key, point, anim) {
    const actor = this.actors[key];
    if (!actor) return;
    const moving = Math.hypot(point.x - actor.at.x, point.y - actor.at.y) > 2;
    if (moving) {
      actor.at = { ...point };
      actor.g.style.transform = `translate(${point.x}px, ${point.y}px)`;
      actor.g.classList.add("walking");
      clearTimeout(actor.walkTimer);
      // The transform transition is 1.4s; drop the leg animation when it lands.
      actor.walkTimer = setTimeout(() => actor.g.classList.remove("walking"), 1450);
    }
    for (const state of ["idle", "typing", "asking", "talking"]) {
      actor.g.classList.toggle(state, state === anim);
    }
  }

  /* ------------------------------------------------------------ updating */
  update(data) {
    this.ensureActors(data.staff || []);
    this.lastData = data;

    /* A scripted standup is over in well under a second, so watching for
       `busy` would mean the meeting is almost never seen. A finished standup
       holds the room instead: the cycle number going up is the real event,
       and it is worth showing for long enough to read. */
    if (this.lastCycle !== null && data.cycle > this.lastCycle) {
      this.meetingUntil = Date.now() + MEETING_HOLD_MS;
      clearTimeout(this.holdTimer);
      this.holdTimer = setTimeout(() => {
        // Send everyone back to their desks once the hold expires.
        if (this.lastData) this.update(this.lastData);
      }, MEETING_HOLD_MS + 60);
    }
    this.lastCycle = data.cycle;

    const meeting = !!data.busy || Date.now() < this.meetingUntil;

    for (const key of Object.keys(this.actors)) {
      const job = (data.desks || {})[key];
      let spot = DESKS[key].stand;
      let anim = "idle";

      if (meeting) {
        spot = SEATS[key] || spot;
        anim = "talking";
      } else if (job && job.status === "blocked" && job.blocked_on === "you") {
        spot = ASKING[key] || spot;
        anim = "asking";
      } else if (job && job.status === "doing") {
        anim = "typing";
      }
      this.moveTo(key, spot, anim);
    }

    this.tableRing.classList.toggle("live", meeting);
    this.tableLabel.textContent = meeting ? "standup" : "";
    this.updateTray(data);
    this.updateClock(data);
    this.updateBubbles(data);
  }

  updateTray(data) {
    const ideas = (data.ideas || []).filter((i) => i.status !== "delivered");
    this.trayCards.innerHTML = "";
    ideas.slice(0, 3).forEach((idea, i) => {
      el("rect", {
        x: -40 + i * 6, y: -22 + i * 9, width: 80, height: 18, rx: 4,
        class: `sc-card sc-card-${idea.status}`,
      }, this.trayCards);
    });
    const open = ideas.length;
    this.trayCount.textContent = open
      ? `${open} idea${open === 1 ? "" : "s"}`
      : "empty";
  }

  updateClock(data) {
    const now = new Date();
    const mins = now.getHours() % 12 * 60 + now.getMinutes();
    this.clockHand.setAttribute("transform", `rotate(${(mins / 720) * 360})`);

    // The arc is how much of the wait until the next standup has gone.
    const total = Math.max(1, (data.interval_minutes || 60) * 60);
    const left = data.seconds_until_next;
    if (left === null || left === undefined) {
      this.clockArc.setAttribute("d", "");
      return;
    }
    const done = Math.max(0, Math.min(1, 1 - left / total));
    const a = done * Math.PI * 2 - Math.PI / 2;
    const r = 22;
    const large = done > 0.5 ? 1 : 0;
    this.clockArc.setAttribute(
      "d",
      done <= 0.001 ? "" :
      `M 0 ${-r} A ${r} ${r} 0 ${large} 1 ${(r * Math.cos(a)).toFixed(2)} ${(r * Math.sin(a)).toFixed(2)}`
    );
  }

  /* A bubble appears when someone says something new, and fades on its own. */
  updateBubbles(data) {
    const latest = data.latest_by_persona || {};
    for (const [key, remark] of Object.entries(latest)) {
      if (!this.actors[key]) continue;
      if (this.lastRemarkId[key] === remark.id) continue;
      this.lastRemarkId[key] = remark.id;
      // Do not replay the backlog the first time the page loads.
      if (this.primed) this.say(key, remark.text);
    }
    this.primed = true;
    this.positionBubbles();
  }

  say(key, text) {
    const actor = this.actors[key];
    if (!actor) return;
    let bubble = this.bubbles[key];
    if (!bubble) {
      bubble = document.createElement("div");
      bubble.className = "sc-bubble";
      this.overlay.appendChild(bubble);
      this.bubbles[key] = bubble;
    }
    bubble.textContent = text.length > 96 ? text.slice(0, 95).trimEnd() + "…" : text;
    bubble.classList.add("show");
    clearTimeout(bubble.timer);
    bubble.timer = setTimeout(() => bubble.classList.remove("show"), 9000);
    this.positionBubbles();
  }

  positionBubbles() {
    for (const [key, bubble] of Object.entries(this.bubbles)) {
      const actor = this.actors[key];
      if (!actor) continue;
      const bias = BUBBLE_BIAS[key] || { x: 0, y: 0 };
      const x = Math.min(Math.max(actor.at.x + bias.x, 120), W - 120);
      const y = Math.min(Math.max(actor.at.y + bias.y, 120), H - 40);
      bubble.style.left = `${(x / W) * 100}%`;
      bubble.style.top = `${(y / H) * 100}%`;
    }
  }
}
