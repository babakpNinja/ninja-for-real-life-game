/*
 * main.js — the shell around the engine: menus, story cards, the character
 * gallery, results and everything that is saved between visits.
 *
 * The canvas only ever draws the game world; every word on screen is real DOM,
 * so it stays crisp on a phone and a screen reader can find it.
 */

import { Game } from "./game.js";
import { CHAPTERS, starsFor } from "./chapters.js";
import { drawCharacter, loadArt, preload, creditFor, notice, artState } from "./sprites.js";
import { sound } from "./audio.js";

const SAVE_KEY = "forreallife.save.v1";

const el = (id) => document.getElementById(id);
const canvas = el("game");
const overlay = el("overlay");
const hud = el("hud");

let characters = [];
let game = null;
let save = load();

/* ------------------------------------------------------------------ save -- */

function blankSave() {
  return { chapters: {}, unlocked: 0, totalScore: 0, plays: 0, muted: false };
}

function load() {
  try {
    const raw = localStorage.getItem(SAVE_KEY);
    if (!raw) return blankSave();
    return Object.assign(blankSave(), JSON.parse(raw));
  } catch (e) {
    return blankSave();
  }
}

function store() {
  try {
    localStorage.setItem(SAVE_KEY, JSON.stringify(save));
  } catch (e) {
    /* private mode — the game still works, it just forgets */
  }
}

function recordRun(r) {
  const prev = save.chapters[r.chapter] || { best: 0, stars: 0, collected: 0, secret: false };
  save.chapters[r.chapter] = {
    best: Math.max(prev.best, r.score),
    stars: Math.max(prev.stars, r.stars),
    collected: Math.max(prev.collected, r.collected),
    total: r.total,
    secret: prev.secret || r.secret,
  };
  save.unlocked = Math.max(save.unlocked, Math.min(r.chapter + 1, CHAPTERS.length - 1));
  save.totalScore = Object.values(save.chapters).reduce((s, c) => s + c.best, 0);
  save.plays += 1;
  store();
}

const starsText = (n) => "★★★".slice(0, n) + "☆☆☆".slice(0, 3 - n);

/* --------------------------------------------------------------- screens -- */

function showOverlay(html, { transparent = false } = {}) {
  overlay.className = "screen" + (transparent ? " transparent" : "");
  overlay.innerHTML = `<div class="panel">${html}</div>`;
  overlay.classList.remove("hidden");
  overlay.scrollTop = 0;
}

function hideOverlay() {
  overlay.classList.add("hidden");
  overlay.innerHTML = "";
}

function on(id, fn) {
  const node = el(id);
  if (node) node.addEventListener("click", () => { sound.unlock(); sound.ui(); fn(); });
}

/** A small looping portrait of a character, used all over the menus. */
function portrait(node, ch, state = "idle", facing = 1) {
  const c = node.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = node.clientWidth || 80;
  const h = node.clientHeight || 80;
  node.width = w * dpr;
  node.height = h * dpr;
  const draw = () => {
    if (!node.isConnected) return;
    const t = performance.now() / 1000;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    drawCharacter(c, ch.id, w / 2, h - 6, Math.min(w, h) * 0.94, ch.palette, t, state, facing);
    requestAnimationFrame(draw);
  };
  draw();
}

/* ------------------------------------------------------------------ menu -- */

function menu() {
  if (game) game.stop();
  hud.classList.add("hidden");
  const resume = save.unlocked > 0 || Object.keys(save.chapters).length;
  showOverlay(`
    <h1 class="title">For Real&nbsp;Life!</h1>
    <p class="subtitle">A backyard adventure with the heeler family</p>
    <div id="menu-dogs"><canvas></canvas></div>
    <button class="big-btn" id="btn-play">▶ ${resume ? "Keep playing" : "Play"}</button>
    <div class="btn-row">
      <button class="med-btn" id="btn-chapters">Chapters</button>
      <button class="med-btn" id="btn-gallery">Characters</button>
      <button class="med-btn" id="btn-stats">Stats</button>
    </div>
    <p class="tap-hint">Tap anywhere to jump · hold to float</p>
    <p class="credits">Fan-made, unofficial and non-commercial — a personal project, not for sale.<br />
    Bluey © Ludo Studio Pty Ltd. Characters and artwork are the property of their respective owners.<br />
    <button class="link-btn" id="btn-credits">About &amp; credits →</button></p>
  `);
  drawMenuDogs();
  on("btn-play", () => storyCard(Math.min(save.unlocked, CHAPTERS.length - 1)));
  on("btn-chapters", chapterSelect);
  on("btn-gallery", gallery);
  on("btn-stats", stats);
  on("btn-credits", credits);
}

/* --------------------------------------------------------------- credits -- */

/** Where the artwork came from, and where to go and watch the real thing. */
function credits() {
  const shows = [
    ["bluey.tv", "https://www.bluey.tv/", "The official Bluey site"],
    ["ABC iview", "https://iview.abc.net.au/show/bluey", "Watch in Australia"],
    ["Disney+", "https://www.disneyplus.com/", "Watch in the US and elsewhere"],
    ["BBC iPlayer", "https://www.bbc.co.uk/iplayer/episodes/m000hcvz/bluey", "Watch in the UK"],
    ["Ludo Studio", "https://www.ludostudio.com.au/", "The people who make it"],
  ];
  showOverlay(`
    <h2>About &amp; credits</h2>
    <p class="subtitle">Made by a dad, for one three-year-old.</p>
    <div class="credits-body">
      <p><b>This is a fan-made, unofficial, non-commercial game.</b> It is not affiliated with,
      endorsed by or connected to Ludo Studio, the ABC, BBC Studios or Disney. It is not for sale
      and carries no advertising.</p>
      <p><b>Bluey © Ludo Studio Pty Ltd.</b> Bluey characters, names and artwork are the property
      of their respective owners. ${notice() ? "Character artwork was retrieved from the community Bluey wiki; every character's bio card links to the page its picture came from." : ""}</p>
      <p>Everything else here — the levels, the backgrounds, the music and the code — was made
      for this game.</p>
      <h3>Watch the real thing</h3>
      <ul class="link-list">
        ${shows.map(([n, u, d]) => `<li><a href="${u}" target="_blank" rel="noopener noreferrer">${n}</a> — ${d}</li>`).join("")}
      </ul>
      <p class="fine">If you own this artwork and would like it taken down, it will be — it is one
      person's tablet, not a website.</p>
    </div>
    <button class="med-btn" id="btn-back">← Menu</button>
  `);
  on("btn-back", menu);
}

/** The family lined up along the bottom of the title panel. */
function drawMenuDogs() {
  const wrap = el("menu-dogs");
  if (!wrap) return;
  const node = wrap.querySelector("canvas");
  const c = node.getContext("2d");
  const cast = ["bluey", "bingo", "bandit", "chilli", "muffin"]
    .map((id) => characters.find((x) => x.id === id))
    .filter(Boolean);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const draw = () => {
    if (!node.isConnected) return;
    const w = node.clientWidth, h = node.clientHeight;
    node.width = w * dpr;
    node.height = h * dpr;
    const t = performance.now() / 1000;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    const gap = w / (cast.length + 1);
    cast.forEach((ch, i) => {
      const bounce = Math.sin(t * 2.2 + i * 0.7) * 4;
      // leave room under the feet: the art draws a contact shadow below the baseline
      drawCharacter(c, ch.id, gap * (i + 1), h - 14 + bounce, h * 0.78, ch.palette, t + i, "idle", i % 2 ? -1 : 1);
    });
    requestAnimationFrame(draw);
  };
  draw();
}

/* -------------------------------------------------------- chapter select -- */

function chapterSelect() {
  const cards = CHAPTERS.map((ch, i) => {
    const done = save.chapters[i];
    const locked = i > save.unlocked;
    const hero = characters.find((c) => c.id === ch.hero);
    return `
      <button class="chapter-card ${locked ? "locked" : ""}" data-ch="${i}" ${locked ? "disabled" : ""}>
        <canvas data-hero="${ch.hero}"></canvas>
        <div>
          <b>${ch.n}. ${ch.title}</b>
          <span>${locked ? "Finish the chapter before" : ch.where} · ${hero ? hero.name : ""}</span>
          <div class="stars">${done ? starsText(done.stars) : locked ? "🔒" : "☆☆☆"}</div>
        </div>
      </button>`;
  }).join("");
  showOverlay(`
    <h2>Chapters</h2>
    <p class="subtitle">One lost bunny, five adventures.</p>
    <div class="chapter-list">${cards}</div>
    <button class="med-btn" id="btn-back">← Back</button>
  `);
  overlay.querySelectorAll(".chapter-card canvas").forEach((node) => {
    const c = characters.find((x) => x.id === node.dataset.hero);
    if (c) portrait(node, c);
  });
  overlay.querySelectorAll(".chapter-card[data-ch]").forEach((b) => {
    b.addEventListener("click", () => {
      sound.unlock(); sound.ui();
      storyCard(Number(b.dataset.ch));
    });
  });
  on("btn-back", menu);
}

/* ------------------------------------------------------------ story card -- */

function storyCard(index) {
  const ch = CHAPTERS[index];
  const hero = characters.find((c) => c.id === ch.hero);
  // fetch this chapter's cast while the story is being read, so the cameo is
  // never drawn as the fallback dog for the first second it is on screen
  preload([ch.hero, ch.cameo].filter(Boolean));
  showOverlay(`
    <h2>Chapter ${ch.n} — ${ch.title}</h2>
    <p class="subtitle">${ch.where}</p>
    <div id="story-dog" style="height:110px"><canvas style="width:110px;height:110px"></canvas></div>
    ${ch.story.map((s) => `<p class="story">${s}</p>`).join("")}
    <p class="joke">${ch.joke}</p>
    <button class="big-btn" id="btn-go">▶ Play as ${hero ? hero.name : "Bluey"}</button>
    <div class="btn-row">
      <button class="med-btn" id="btn-auto">${save.walk ? "Auto-run: off" : "Auto-run: on"}</button>
      <button class="med-btn" id="btn-back">← Menu</button>
    </div>
  `);
  const node = overlay.querySelector("#story-dog canvas");
  if (node && hero) portrait(node, hero, "cheer");
  on("btn-go", () => play(index));
  on("btn-auto", () => { save.walk = !save.walk; store(); storyCard(index); });
  on("btn-back", menu);
}

/* ------------------------------------------------------------------ play -- */

function play(index) {
  hideOverlay();
  hud.classList.remove("hidden");
  sound.unlock();
  sound.setMuted(!!save.muted);
  game.autoRun = !save.walk;
  game.start(index);
  updateHud();
}

function updateHud() {
  if (!game || game.mode !== "playing") return;
  el("hud-score").textContent = game.score;
  el("hud-tokens").textContent = `${game.collected}/${game.level.total}`;
  const pct = Math.max(0, Math.min(1, game.player.x / game.ch.length));
  el("hud-progress-fill").style.width = `${pct * 100}%`;
  el("hud-progress-dog").style.left = `${pct * 100}%`;
  requestAnimationFrame(updateHud);
}

const TOAST_MAX = 3;
const TOAST_REPEAT_MS = 1200;
const toastSeen = new Map();

function toast(text) {
  const layer = el("toast-layer");
  if (!layer) return;
  // the same message can fire many times in a row (splashing, stumbling) —
  // repeat it sparingly and never let more than a few stack up on screen.
  const now = performance.now();
  if (now - (toastSeen.get(text) || -Infinity) < TOAST_REPEAT_MS) return;
  toastSeen.set(text, now);
  while (layer.childElementCount >= TOAST_MAX) layer.firstElementChild.remove();
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = text;
  layer.appendChild(node);
  setTimeout(() => node.remove(), 1500);
}

/* --------------------------------------------------------------- results -- */

function results(r) {
  const ch = CHAPTERS[r.chapter];
  const next = r.chapter + 1;
  const best = (save.chapters[r.chapter] || {}).best || 0;
  hud.classList.add("hidden");
  showOverlay(`
    <h2>${ch.title} — done!</h2>
    <div class="stars-big">${starsText(r.stars)}</div>
    <p class="story">${ch.outro}</p>
    <table class="stats">
      <tr><td>${ch.tokenName} found</td><td>${r.collected} / ${r.total}</td></tr>
      ${ch.hasBalloon ? `<tr><td>keepy uppy bops</td><td>${r.bops}</td></tr>` : ""}
      <tr><td>hidden dollarbucks</td><td>${r.secret ? "found! 💰" : "still hidden"}</td></tr>
      <tr><td>chapter bonus</td><td>+${r.bonus}</td></tr>
      <tr><th>score</th><th>${r.score}</th></tr>
      <tr><td>your best here</td><td>${best}</td></tr>
    </table>
    ${next < CHAPTERS.length
      ? `<button class="big-btn" id="btn-next">▶ Chapter ${next + 1}: ${CHAPTERS[next].title}</button>`
      : `<p class="story"><b>You found Floppy and got everyone home. 🐾</b></p>
         <button class="big-btn" id="btn-again">▶ Play again</button>`}
    <div class="btn-row">
      <button class="med-btn" id="btn-retry">↻ This chapter</button>
      <button class="med-btn" id="btn-chapters">Chapters</button>
      <button class="med-btn" id="btn-back">Menu</button>
    </div>
  `, { transparent: true });
  on("btn-next", () => storyCard(next));
  on("btn-again", () => storyCard(0));
  on("btn-retry", () => play(r.chapter));
  on("btn-chapters", chapterSelect);
  on("btn-back", menu);
}

/* --------------------------------------------------------------- gallery -- */

function gallery() {
  const cards = characters.map((c) => `
    <button class="char-card" data-id="${c.id}">
      <canvas data-pal="${c.id}"></canvas>
      <b>${c.name}</b>
      <span>${c.role}</span>
    </button>`).join("");
  showOverlay(`
    <h2>Everyone you'll meet</h2>
    <p class="subtitle">${characters.length} characters — tap one to read about them.</p>
    <div class="gallery">${cards}</div>
    <button class="med-btn" id="btn-back">← Back</button>
  `);
  overlay.querySelectorAll(".char-card canvas").forEach((node) => {
    const c = characters.find((x) => x.id === node.dataset.pal);
    if (c) portrait(node, c, "idle", 1);
  });
  overlay.querySelectorAll(".char-card").forEach((b) => {
    b.addEventListener("click", () => { sound.unlock(); sound.ui(); bio(b.dataset.id); });
  });
  on("btn-back", menu);
}

/** Where this character's picture came from — shown on their own bio card. */
function attribution(c) {
  const src = creditFor(c.id);
  if (!src) return "";
  return `<p class="attrib" data-attrib="${c.id}">Artwork: ${c.name} © Ludo Studio Pty Ltd —
    <a href="${src.source}" target="_blank" rel="noopener noreferrer">source</a>
    (retrieved ${src.retrieved})</p>`;
}

function bio(id) {
  const c = characters.find((x) => x.id === id);
  if (!c) return gallery();
  showOverlay(`
    <div class="bio">
      <div class="bio-head">
        <canvas id="bio-dog"></canvas>
        <div>
          <h3>${c.name}</h3>
          <div class="meta">${c.species} · ${c.role}${c.age ? ` · age ${c.age}` : ""}</div>
        </div>
      </div>
      <p>${c.personality}</p>
      <p class="fun">💡 ${c.funFact}</p>
      ${attribution(c)}
    </div>
    <div class="btn-row">
      <button class="med-btn" id="btn-back">← All characters</button>
      <button class="med-btn" id="btn-menu">Menu</button>
    </div>
  `, { transparent: true });
  portrait(el("bio-dog"), c, "cheer");
  on("btn-back", gallery);
  on("btn-menu", menu);
}

/* ----------------------------------------------------------------- stats -- */

function stats() {
  const rows = CHAPTERS.map((ch, i) => {
    const d = save.chapters[i];
    return `<tr>
      <td>${ch.n}. ${ch.title}</td>
      <td>${d ? starsText(d.stars) : "—"}</td>
      <td>${d ? d.best : "—"}</td>
    </tr>`;
  }).join("");
  const found = CHAPTERS.filter((_, i) => (save.chapters[i] || {}).secret).length;
  showOverlay(`
    <h2>Stats</h2>
    <table class="stats">
      <tr><th>Chapter</th><th>Stars</th><th>Best</th></tr>
      ${rows}
      <tr><th>Total</th><th>${Object.values(save.chapters).reduce((s, c) => s + c.stars, 0)}/15</th><th>${save.totalScore}</th></tr>
    </table>
    <p class="story">Hidden dollarbucks found: <b>${found} / ${CHAPTERS.length}</b></p>
    <p class="joke">Chapters played: ${save.plays}</p>
    <div class="btn-row">
      <button class="med-btn" id="btn-reset">Start over</button>
      <button class="med-btn" id="btn-back">← Back</button>
    </div>
  `);
  on("btn-reset", () => { save = blankSave(); store(); stats(); });
  on("btn-back", menu);
}

/* ----------------------------------------------------------------- pause -- */

function pause() {
  if (!game || game.mode !== "playing") return;
  game.paused = true;
  hud.classList.add("hidden");
  showOverlay(`
    <h2>Paused</h2>
    <p class="subtitle">Chapter ${game.ch.n} — ${game.ch.title}</p>
    <button class="big-btn" id="btn-resume">▶ Keep playing</button>
    <div class="btn-row">
      <button class="med-btn" id="btn-retry">↻ Restart</button>
      <button class="med-btn" id="btn-chapters">Chapters</button>
      <button class="med-btn" id="btn-back">Menu</button>
    </div>
  `, { transparent: true });
  on("btn-resume", () => {
    hideOverlay();
    hud.classList.remove("hidden");
    game.paused = false;
    updateHud();
  });
  on("btn-retry", () => play(game.chapterIndex));
  on("btn-chapters", chapterSelect);
  on("btn-back", menu);
}

/* ----------------------------------------------------------------- input -- */

function wireInput() {
  const playing = () => game && game.mode === "playing" && !game.paused && overlay.classList.contains("hidden");

  const down = (e) => {
    sound.unlock();
    if (!playing()) return;
    if (e.target.closest && e.target.closest("button")) return;
    e.preventDefault();
    game.press();
  };
  const up = () => { if (game) game.release(); };

  canvas.addEventListener("pointerdown", down);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);

  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    if (e.code === "Space" || e.code === "ArrowUp" || e.code === "KeyW") {
      e.preventDefault();
      if (playing()) game.press();
    }
    if (e.code === "ArrowLeft") game.keys.left = true;
    if (e.code === "ArrowRight") game.keys.right = true;
    if (e.code === "Escape" || e.code === "KeyP") playing() ? pause() : null;
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" || e.code === "ArrowUp" || e.code === "KeyW") game.release();
    if (e.code === "ArrowLeft") game.keys.left = false;
    if (e.code === "ArrowRight") game.keys.right = false;
  });

  el("btn-pause").addEventListener("click", pause);
  el("btn-mute").addEventListener("click", () => {
    save.muted = !save.muted;
    store();
    sound.unlock();
    sound.setMuted(save.muted);
    el("btn-mute").textContent = save.muted ? "🔇" : "🔊";
  });

  // a phone held upright still plays, it just gets a nudge
  const checkOrientation = () => {
    const portraitMode = window.innerHeight > window.innerWidth * 1.1;
    el("rotate-hint").classList.toggle("hidden", !portraitMode);
  };
  window.addEventListener("resize", checkOrientation);
  window.addEventListener("orientationchange", () => setTimeout(checkOrientation, 250));
  checkOrientation();

  // never let the page itself scroll or zoom under little fingers
  document.addEventListener("gesturestart", (e) => e.preventDefault());
  document.addEventListener("touchmove", (e) => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && game && game.mode === "playing" && !game.paused) pause();
  });
}

/* ------------------------------------------------------------------ boot -- */

async function boot() {
  const res = await fetch("data/characters.json");
  const data = await res.json();
  characters = data.characters;

  // Character artwork: the manifest first, then eagerly fetch only the cast the
  // menu and the first chapters need. The other twenty load when the gallery
  // asks for them, so a phone on mobile data is not made to wait for 25 images.
  await loadArt();
  preload(["bluey", "bingo", "bandit", "chilli", "muffin"]);

  game = new Game(canvas, characters);
  game.onEvent = (ev) => {
    if (ev.type === "toast") toast(ev.text);
    if (ev.type === "complete") { recordRun(ev); setTimeout(() => results(ev), 900); }
  };
  window.game = game;                       // handy for tests and for curious dads
  window.__art = artState;                  // which sprites actually decoded
  window.__cast = CHAPTERS.map((c) => [c.hero, c.cameo]);  // who each chapter needs
  window.__ready = true;

  el("btn-mute").textContent = save.muted ? "🔇" : "🔊";
  wireInput();
  menu();
}

boot();

export { starsFor };
