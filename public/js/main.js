/*
 * main.js — the shell around the engine: menus, story cards, the character
 * gallery, results and everything that is saved between visits.
 *
 * The canvas only ever draws the game world; every word on screen is real DOM,
 * so it stays crisp on a phone and a screen reader can find it.
 */

import { Game } from "./game.js";
import { CHAPTERS, starsFor, STARS_PER_CHAPTER } from "./chapters.js";
import {
  drawCharacter, loadArt, preload, creditFor, notice, noticeShort, artState,
} from "./sprites.js";
import { sound } from "./audio.js";
import { greeting, hello } from "./lines.js";
import { PLAYABLE, ABILITIES, abilityFor, heroFor } from "./abilities.js";
import {
  fullscreenSupported, fullscreenOn, enterFullscreen, leaveFullscreen, onFullscreenChange,
} from "./fullscreen.js";
import { homeScreenHintWanted, HOME_SCREEN_HINT } from "./homescreen.js";

const SAVE_KEY = "forreallife.save.v1";

/*
 * The licensing notice is authored in scripts/fetch_assets.py and shipped in
 * data/asset-credits.json; the menu and the credits screen render it from
 * there. This is what they show if that fetch failed — a screen that says
 * nothing about who owns the artwork is not an acceptable failure mode.
 * `fetch_assets.py --check` fails if this string drifts from the credits file.
 */
const NOTICE_SHORT = "Fan-made, unofficial and non-commercial. Bluey © Ludo Studio Pty Ltd.";

const el = (id) => document.getElementById(id);
const canvas = el("game");
const stage = el("stage");
const overlay = el("overlay");
const hud = el("hud");
// Held on to, not looked up: it rides along with the screen that is up, and
// every `showOverlay` throws that screen's markup away (#277).
const hint = el("rotate-hint");

let characters = [];
let game = null;
let save = load();

/* ------------------------------------------------------------------ save -- */

// `hero: null` is not the same as "Bluey": it is what makes the first ▶ Play go
// through the character screen instead of past it, and what puts "who do you
// want to be?" before the first chapter exactly once (#302).
function blankSave() {
  return { chapters: {}, unlocked: 0, totalScore: 0, plays: 0, muted: false, hero: null,
           fullscreen: false };
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

/**
 * Put a screen up — and silence the one it replaces (#301).
 *
 * Three screens read themselves out loud now (#255, #293) and nothing cancelled
 * a read when you left one: `read()` clears the queue on its way in, so a read
 * only stopped if the screen you landed on also talked, and `hideOverlay()`
 * covers the single exit into the chapter. Everything else — a bio back to the
 * gallery, the results to the menu — kept talking over where you went.
 *
 * `keepReading` is opt-*out* because the one screen that must not be silenced
 * is silencing itself: the story card re-renders whole for the Auto-run label
 * and would cut its own story off mid-sentence. A caller who forgets the flag
 * gets a read that ends early, which is visible; the other default fails by
 * going on talking somewhere nobody is listening for it, which is this issue.
 *
 * Every screen marks one element `data-lead`: the thing it exists to show —
 * the score on the results, the total on the stats, the first card in a
 * gallery, the way back into the game on the pause screen. The panel scrolls
 * its own insides, so a screen can push its own headline below the fold while
 * the document never overflows and every fold test stays green; that is how one
 * extra results row hid the score (#309). The suite reads the attribute rather
 * than a list of selectors kept beside it, so a screen added later declares its
 * own lead or fails for not having one.
 */
function showOverlay(html, { transparent = false, keepReading = false } = {}) {
  if (!keepReading) sound.hush();
  overlay.className = "screen" + (transparent ? " transparent" : "");
  overlay.innerHTML = `<div class="panel"><div class="panel-body">${html}</div></div>`;
  // The block of buttons that ends a screen — the way forward and the way back —
  // is lifted out of the scrolling part and pinned under it, so a list longer
  // than the window scrolls instead of pushing Back off the bottom. On a phone
  // held sideways the characters screen ran 535px past the window and every
  // button on it was unreachable-looking, because the panel scrolls its own
  // insides and the page gives no sign of it (#269). The block and anything
  // written under it (the menu's fine print) go together, so the order a player
  // reads is the order it was written in.
  const body = overlay.querySelector(".panel-body");
  const actions = body.querySelector(":scope > .actions");
  if (actions) {
    const foot = document.createElement("div");
    foot.className = "panel-foot";
    while (actions.nextSibling) foot.appendChild(actions.nextSibling);
    foot.insertBefore(actions, foot.firstChild);
    body.after(foot);
  }
  placeHint();
  overlay.classList.remove("hidden");
  overlay.scrollTop = 0;
}

/**
 * Put the rotate pill at the top of the block of pinned buttons.
 *
 * It used to be `position: absolute; bottom: 8px` on the stage, from before the
 * panel reached the bottom of the window. Once #269 pinned the way out down
 * there, the pill was drawn on top of it: 32px of a 56px "← Back" under a
 * translucent black pill for the first six seconds of the screen a player is
 * looking for the way out on (#277). In the flow above those buttons it
 * shortens the body it sits over instead of covering anything.
 *
 * The screen is rebuilt from scratch on every `showOverlay`, so the pill has to
 * be moved back in each time, and parked on the stage while there is no screen
 * up — a detached node has no rect, and "no rect" reads as "covers nothing".
 */
function placeHint() {
  const foot = overlay.querySelector(".panel > .panel-foot");
  if (foot) foot.insertBefore(hint, foot.firstChild);
  else stage.appendChild(hint);
}

function hideOverlay() {
  overlay.classList.add("hidden");
  stage.appendChild(hint);
  overlay.innerHTML = "";
  // a queued utterance outlives the card that asked for it, and the screen it
  // would talk over is the chapter (#255)
  sound.hush();
}

function on(id, fn) {
  const node = el(id);
  if (node) node.addEventListener("click", () => { sound.unlock(); sound.ui(); fn(); });
}

/* ----------------------------------------------------------- rotate hint -- */

/**
 * "Turn your phone sideways" is worth saying once, briefly.
 *
 * Left up, it sits at the bottom of the screen for the whole session — and the
 * bottom of the screen during a chapter is the progress bar and the little dog
 * running along it. Measured on a 390x844 phone the pill covered the bar
 * completely (#252). So it is a message with a life: shown on the way into
 * portrait, taken away after HINT_MS, and never while a chapter is on.
 */
export const HINT_MS = 6000;
let hintTimer = null;

function rotateHint(show) {
  clearTimeout(hintTimer);
  hintTimer = null;
  hint.classList.toggle("hidden", !show);
  if (show) hintTimer = setTimeout(() => rotateHint(false), HINT_MS);
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

/* ------------------------------------------------------------ full screen -- */

/*
 * Big-picture mode (#350). Two ways in — the menu's fine print and the HUD —
 * one saved preference, and exactly one source of truth for the state: the
 * browser's `fullscreenchange`. Escape leaves full-screen, so does the OS, and
 * so does the browser when it feels like it; a button that drew itself from
 * what it was last asked to do would then be describing a screen nobody is
 * looking at (#296 is the same shape, one floor down, about the speaker icon).
 */

// Drawn, not typed: see the note beside `.icon-btn svg` in style.css. Corners
// pointing out is "make it bigger", corners pointing in is "put it back".
const CORNERS_OUT = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
  + '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>';
const CORNERS_IN = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
  + '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" /></svg>';

// The stage and not the body: everything the game draws or writes is inside it
// (the canvas, the HUD, every overlay screen), and asking for the body would
// take the browser's own scrollbar area along with it.
const fullscreenAvailable = () => fullscreenSupported(stage);

/**
 * The menu's way in — or, on a phone that has no way in, how to get one.
 *
 * The iPhone branch is a `<span>` and not a `<button>` on purpose: adding to
 * the home screen is done from Safari's own Share sheet and no script can open
 * it, so a button here could only ever be pressed twice as hard (#310, #354).
 */
function fullscreenLink() {
  if (fullscreenAvailable()) {
    return ` · <button class="link-btn" id="btn-full-menu">${
      fullscreenOn() ? "Leave full screen" : "Full screen"}</button>`;
  }
  if (homeScreenHintWanted(false)) {
    return ` · <span class="install-hint" id="full-hint">${HOME_SCREEN_HINT}</span>`;
  }
  return "";
}

/** Both controls, drawn from what the browser says is true right now. */
function paintFullscreen() {
  const on = fullscreenOn();
  const btn = el("btn-full");
  if (btn) {
    btn.classList.toggle("hidden", !fullscreenAvailable());
    btn.innerHTML = on ? CORNERS_IN : CORNERS_OUT;
    btn.setAttribute("aria-label", on ? "Leave full screen" : "Full screen");
  }
  const link = el("btn-full-menu");
  if (link) link.textContent = on ? "Leave full screen" : "Full screen";
}

function toggleFullscreen() {
  return fullscreenOn() ? leaveFullscreen() : enterFullscreen(stage);
}

/*
 * The window is a different shape now, and two things have to be told.
 *
 * Entering full-screen fires `resize` on a real screen and the engine listens
 * for it — but not in every browser, not in a headless one, and not reliably
 * *before* the frame after the transition. The engine's `resize()` is idempotent
 * arithmetic, so calling it here as well costs nothing and removes the dependency
 * on an event that may not come. `placeToasts` follows because it is positioned
 * from `sceneRect()`, and a stale rect puts the words in the sky (#254).
 *
 * Twice, 250ms apart, for the same reason `orientationchange` is handled twice
 * below: the transition animates, and the rect at the moment the event fires is
 * not always the rect it lands on.
 */
function relayout() {
  if (game) game.resize();
  placeToasts();
}

function fullscreenChanged() {
  // the *observed* state, not the intent: leaving with Escape is a decision too,
  // and remembering "full screen" after it would fight the player on every visit
  save.fullscreen = fullscreenOn();
  store();
  paintFullscreen();
  relayout();
  setTimeout(relayout, 250);
}

/**
 * Honour a saved preference next visit — which cannot be done on load.
 *
 * `requestFullscreen` outside a user gesture is refused by every browser, so
 * "remember full screen" can only mean "the first time they touch the screen".
 * One shot, in the capture phase so it goes in ahead of whatever the tap was
 * really for, and removed whether or not the browser granted it: a page that
 * asks again on every tap is a page that spends the player's taps.
 */
function armSavedFullscreen() {
  if (!save.fullscreen || !fullscreenAvailable() || fullscreenOn()) return;
  const once = () => {
    document.removeEventListener("pointerdown", once, true);
    enterFullscreen(stage);
  };
  document.addEventListener("pointerdown", once, true);
}

/* ------------------------------------------------- the tap before the menu -- */

// index.html paints a real menu so there is something to look at while this
// module and the artwork arrive — but `menu()` throws those buttons away and
// re-renders them, and on mobile data the module itself is seconds behind the
// markup. A tap in that window simply vanished (#284). The catcher therefore
// cannot live here: it is inline in index.html, running the moment the buttons
// exist, and it leaves the id in `window.__earlyTap` for this end of it.
function replayEarlyTap() {
  const id = window.__earlyTap;
  window.__earlyTap = null;
  // `menu()` has just rebuilt these, so this presses the *new* button with a
  // live handler on it, not the dead one the player tapped
  if (id && el(id)) el(id).click();
}

/* ------------------------------------------------------------------ menu -- */

/** The chapter ▶ Play means: the furthest one unlocked, clamped to the last. */
function currentChapter() {
  return Math.min(save.unlocked, CHAPTERS.length - 1);
}

/**
 * How to play, in the words of the device it is being read on (#256, #307).
 *
 * The line under the menu buttons is the only place the controls are written
 * down, and it said `Tap anywhere to jump · hold to float` to everybody. Two
 * things were wrong with that. On a laptop there is nothing to tap — the keys
 * have been wired the whole time (space / ↑ / W, Esc to pause) and the game
 * never mentioned one of them, so a parent hunting for pause had to guess.
 * And every hero has had a special move since #303, on a button in the corner
 * of the HUD, which is named exactly once: on the picker, which is shown at the
 * first ▶ Play and never again. Choose on day one, come back on day two, and
 * nothing on screen says the button is there or what it does.
 *
 * `(pointer: coarse)` and not the user agent: it is the question actually being
 * asked — is the thing pointing at this screen a finger. A laptop with a
 * touchscreen answers `fine` and gets the keyboard line, which is the right
 * line for somebody sitting at a keyboard.
 *
 * Pause is named for the keyboard only. On a phone the ⏸ is on the HUD where a
 * thumb can see it, and this line has to fit a 390px-wide window in landscape
 * (#266) — a clause everyone can already see is the one to leave out.
 */
function controlsHint() {
  const touch = window.matchMedia("(pointer: coarse)").matches;
  const me = save.hero ? characters.find((c) => c.id === save.hero) : null;
  const move = save.hero ? abilityFor(save.hero) : null;
  const parts = touch
    ? ["Tap anywhere to jump", "hold to float"]
    : ["Space to jump", "hold it to float"];
  if (move) {
    const whose = me ? `${me.name}'s ` : "";
    parts.push(touch
      ? `tap ${move.emoji} for ${whose}${move.name}`
      : `E for ${whose}${move.name} ${move.emoji}`);
  }
  if (!touch) parts.push("Esc to pause");
  return parts.join(" · ");
}

function menu() {
  if (game) game.stop();
  hud.classList.add("hidden");
  const resume = save.unlocked > 0 || Object.keys(save.chapters).length;
  const me = save.hero ? characters.find((c) => c.id === save.hero) : null;
  const myMove = save.hero ? abilityFor(save.hero) : null;
  showOverlay(`
    <h1 class="title">Ana&nbsp;Bingo!</h1>
    <p class="subtitle playing-as">${me
      ? `Playing as <b>${me.name}</b> · ${myMove.emoji} ${myMove.name}
         <button class="link-btn" id="btn-hero">change</button>`
      : "A backyard adventure with the heeler family"}</p>
    <div id="menu-dogs"><canvas></canvas></div>
    <div class="actions">
      <button class="big-btn" id="btn-play" data-lead>▶ ${resume ? "Keep playing" : "Play"}</button>
      <div class="btn-row">
        <button class="med-btn" id="btn-chapters">Chapters</button>
        <button class="med-btn" id="btn-gallery">Characters</button>
        <button class="med-btn" id="btn-stats">Stats</button>
      </div>
    </div>
    <p class="tap-hint">${controlsHint()}<br />
    <button class="link-btn" id="btn-story">${resume ? "The story so far" : "The story"} →</button>${fullscreenLink()}</p>
    <p class="credits">${noticeShort() || NOTICE_SHORT}<br />
    A personal project, not for sale.<br />
    <button class="link-btn" id="btn-credits">About &amp; credits →</button></p>
  `);
  drawMenuDogs();
  // straight into the chapter. It used to open the story card, so between the
  // menu and a moving dog there were two taps and three paragraphs of reading —
  // and the player this is for is three and cannot read them (#255). The story
  // is one tap away below, and it is read out loud when it gets there.
  // Nobody chosen yet means the first ▶ Play goes through "who do you want to
  // be?" — the question is asked once, at the only moment it is not an
  // interruption, and never again unless it is asked for. Afterwards ▶ Play is
  // still one tap into a moving dog (#255).
  on("btn-play", () => {
    const go = () => play(currentChapter());
    if (save.hero) return go();
    characterSelect({ then: go, back: menu, title: "Who do you want to be?" });
  });
  on("btn-hero", () => characterSelect({
    then: menu, back: menu,
    title: save.hero ? "Play as somebody else?" : "Who do you want to be?",
  }));
  on("btn-story", () => storyCard(currentChapter()));
  on("btn-chapters", chapterSelect);
  on("btn-gallery", gallery);
  on("btn-stats", stats);
  on("btn-credits", credits);
  on("btn-full-menu", toggleFullscreen);
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
      <p data-lead><b>This is a fan-made, unofficial, non-commercial game.</b> It is not affiliated with,
      endorsed by or connected to Ludo Studio, the ABC, BBC Studios or Disney. It is not for sale
      and carries no advertising.</p>
      <p class="notice">${notice() || NOTICE_SHORT}</p>
      <p>Every character's bio card links to the page its picture came from.</p>
      <p>Everything else here — the levels, the backgrounds, the music and the code — was made
      for this game.</p>
      <h3>Watch the real thing</h3>
      <ul class="link-list">
        ${shows.map(([n, u, d]) => `<li><a href="${u}" target="_blank" rel="noopener noreferrer">${n}</a> — ${d}</li>`).join("")}
      </ul>
      <p class="fine">If you own this artwork and would like it taken down, it will be — it is one
      person's tablet, not a website.</p>
    </div>
    <div class="actions"><button class="med-btn" id="btn-back">← Menu</button></div>
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

/* ------------------------------------------------------ character select -- */

/** Whoever is being played as — the chosen one, or the chapter's own. */
function heroOf(index) {
  return heroFor(save.hero, CHAPTERS[index]);
}

/**
 * Who do you want to be?
 *
 * Shown before the first chapter and reachable from the menu afterwards, and it
 * is the only screen in the game whose answer is remembered *across* chapters:
 * a three-year-old picks Bingo once and stays Bingo, rather than being handed
 * whoever chapter four happens to be about.
 *
 * The roster is `PLAYABLE`, not `characters` — see abilities.js for why it is
 * four and not twenty-five. Everyone else is still in the gallery, still in the
 * chapters and still waving from the middle of them.
 *
 * `then` is where picking somebody goes next: straight into the chapter when
 * this screen is standing between ▶ Play and the game, and back to the menu when
 * it was opened on purpose. Passed in rather than decided here, because "I came
 * to change my mind" and "I am starting" want different next screens and this
 * screen cannot tell them apart.
 */
function characterSelect({ then = menu, back = menu, title = "Who do you want to be?" } = {}) {
  const cast = PLAYABLE
    .map((id) => characters.find((c) => c.id === id))
    .filter(Boolean);
  const cards = cast.map((c, i) => {
    const a = abilityFor(c.id);
    return `
      <button class="hero-card ${save.hero === c.id ? "chosen" : ""}" data-id="${c.id}"${i === 0 ? " data-lead" : ""}>
        <canvas data-hero="${c.id}"></canvas>
        <div class="hero-copy">
          <b>${c.name}</b>
          <span class="hero-role">${c.role}</span>
          <span class="hero-move">${a.emoji} ${a.name}</span>
          <span class="hero-blurb">${a.blurb}</span>
        </div>
      </button>`;
  }).join("");
  showOverlay(`
    <h2>${title}</h2>
    <p class="subtitle">Everyone has their own special move. Tap the ${"✨"} button to use it!</p>
    <div class="hero-list">${cards}</div>
    <div class="actions"><button class="med-btn" id="btn-back">← Back</button></div>
  `);
  overlay.querySelectorAll(".hero-card canvas").forEach((node) => {
    const c = characters.find((x) => x.id === node.dataset.hero);
    // running, not standing: the run frame is the artwork that will be on screen
    // for the whole chapter, so this is a picture of the actual choice
    if (c) portrait(node, c, "run");
  });
  overlay.querySelectorAll(".hero-card").forEach((b) => {
    b.addEventListener("click", () => {
      sound.unlock(); sound.ui();
      save.hero = b.dataset.id;
      store();
      preload([save.hero]);
      then();
    });
  });
  // said out loud, like every other screen with words on it (#293): this one is
  // a question put to somebody who cannot read the four answers
  readAloud([title, ...cast.map((c) => {
    const a = abilityFor(c.id);
    return `${c.name}. ${a.name}. ${a.blurb}`;
  })]);
  on("btn-back", back);
}

/* -------------------------------------------------------- chapter select -- */

function chapterSelect() {
  const cards = CHAPTERS.map((ch, i) => {
    const done = save.chapters[i];
    const locked = i > save.unlocked;
    const hero = characters.find((c) => c.id === ch.hero);
    return `
      <button class="chapter-card ${locked ? "locked" : ""}" data-ch="${i}" ${locked ? "disabled" : ""}${i === 0 ? " data-lead" : ""}>
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
    <div class="actions"><button class="med-btn" id="btn-back">← Back</button></div>
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

/**
 * What the story card's speaker button says.
 *
 * The story is read once when the card opens and there is no way to hear it
 * again, and nothing on screen says it is talking — so a read that never starts
 * (muted, or a device with no voice) is indistinguishable from one still going
 * (#290). Four states, all of them out loud:
 *
 *   reading  — the queue is being spoken
 *   novoice  — nothing on this screen was heard: no recording for these lines
 *              (#357) *and* no browser voice, or one that answered
 *              'synthesis-failed'. It is not "speechSynthesis is missing" any
 *              more — a phone with no voice of its own plays the recordings
 *              perfectly well, and telling that player there is no voice here
 *              would be a lie about a card that just read itself to her
 *   muted    — the sound is off; the button says so instead of doing nothing
 *   idle     — tap it to hear the story again
 *
 * "Muted" is asked of `sound`, not of `save`: the thing that refuses to speak is
 * `Sound.read()`, and it refuses on `sound.muted`. `save.muted` is what is
 * *persisted* — the same fact only for as long as nothing mutes the mixer without
 * writing the save, or writes the save without telling the mixer. That gap was a
 * real bug once already (#290: a game saved muted read the story out loud after a
 * reload, label and behaviour disagreeing), so the label asks whoever does the
 * work (#294).
 */
function readLabel(state) {
  if (state === "reading") return "🔊 Reading…";
  if (state === "novoice") return "🔇 No voice here";
  return sound.muted ? "🔇 Sound is off" : "🔊 Read it again";
}

// Which of the four states the button is in, and which read it is about.
//
// The button itself is looked up rather than held: the card is re-rendered whole
// for Auto-run, and the read that answers may outlive the button that started
// it. But the *state* cannot live in that button's text either — the re-render
// builds a fresh one, and it opened on the idle label while the story was still
// being read, which is the one path back to the guessing #290 exists to end
// (#314). So it is held here, next to the only function that sets it.
//
// Stamped with `sound.speechRun`, the same counter `read()` matches its own
// `onend`/`onerror` against: arriving on a new screen hushes (#301) and bumps
// it, so the state the last screen left behind is stale by construction and the
// new button opens on the label its own read gives it.
let readState = null;
let readStateRun = -1;

function heldReadState() {
  return readStateRun === sound.speechRun ? readState : null;
}

function setReadLabel(state) {
  readState = state;
  readStateRun = sound.speechRun;
  const btn = document.getElementById("btn-read");
  if (btn) btn.textContent = readLabel(state);
}

// The affordance itself — placed, not pasted into each screen's markup. Three
// screens read themselves now, and a button that says "🔊 Read it again" on one
// and "Read" on another is two affordances to learn (#293).
//
// It goes at the top of the panel rather than in the pinned row of buttons that
// ends a screen, where the story card used to carry it: the results screen
// already ends in four buttons, and a fifth wraps that row onto a second line —
// which left 193px of a 395px panel to read the score through, tripping #269's
// peephole rule. Top of the panel is also where the text it reads starts, and
// it is the same corner on all three.
function placeReadButton() {
  const body = overlay.querySelector(".panel-body");
  if (!body || body.querySelector("#btn-read")) return;
  const row = document.createElement("div");
  row.className = "read-row";
  row.innerHTML =
    `<button class="med-btn" id="btn-read">${readLabel(heldReadState())}</button>`;
  body.insertBefore(row, body.firstChild);
}

/**
 * Give the screen that is up its voice: say `lines`, and wire `#btn-read`.
 *
 * The story card had all of this inline and it was the only screen with it
 * (#255, #290) — so the results screen, which is what a three-year-old is
 * looking at when she asks what happened, and the 25 character bios, each
 * reached by tapping a dog she recognises, were silent grey text (#293).
 * Extracted rather than copied twice: the four states the button reports
 * (reading / no voice / muted / idle) were three fixes' worth of getting right,
 * and a copy would have been a fourth screen's worth of getting wrong.
 *
 * `speak: false` is for a re-render of a screen already being read — the story
 * card redraws itself for a one-word label change, and starting the story again
 * from the top halfway through hearing it is worse than the stale label.
 */
function readAloud(lines, { speak = true } = {}) {
  placeReadButton();
  const readOut = () => {
    const queued = sound.read(lines, {
      onend: () => setReadLabel(),
      onerror: () => setReadLabel("novoice"),
    });
    // nothing queued and the sound is on means nothing here can say these lines
    // — no recording and no browser voice — and "Read it again" would be a
    // button that has never worked and never says so (#294)
    setReadLabel(queued ? "reading" : sound.muted ? null : "novoice");
  };
  if (speak) readOut();
  on("btn-read", readOut);
  return readOut;
}

function storyCard(index, { speak = true } = {}) {
  const ch = CHAPTERS[index];
  const hero = characters.find((c) => c.id === heroOf(index));
  // fetch this chapter's cast while the story is being read, so the cameo is
  // never drawn as the fallback dog for the first second it is on screen
  preload([heroOf(index), ch.cameo].filter(Boolean));
  showOverlay(`
    <h2>Chapter ${ch.n} — ${ch.title}</h2>
    <p class="subtitle">${ch.where}</p>
    <div id="story-dog"><canvas></canvas></div>
    ${ch.story.map((s, i) => `<p class="story"${i === 0 ? " data-lead" : ""}>${s}</p>`).join("")}
    <p class="joke">${ch.joke}</p>
    <div class="actions">
      <button class="big-btn" id="btn-go">▶ Play as ${hero ? hero.name : "Bluey"}</button>
      <div class="btn-row">
        <button class="med-btn" id="btn-auto">${save.walk ? "Auto-run: off" : "Auto-run: on"}</button>
        <button class="med-btn" id="btn-back">← Menu</button>
      </div>
    </div>
  `, { keepReading: !speak });
  const node = overlay.querySelector("#story-dog canvas");
  if (node && hero) portrait(node, hero, "cheer");
  // read to whoever is holding the phone, because the reason this card is no
  // longer in the way of ▶ Play is that they cannot read it (#255)
  readAloud([`Chapter ${ch.n}. ${ch.title}.`, ...ch.story, ch.joke], { speak });
  on("btn-go", () => play(index));
  // re-rendered for a one-word label change, so it does not start the story
  // over from the top halfway through hearing it
  on("btn-auto", () => { save.walk = !save.walk; store(); storyCard(index, { speak: false }); });
  on("btn-back", menu);
}

/* ------------------------------------------------------------------ play -- */

function play(index) {
  // the story card used to do this on the way past, and ▶ Play no longer goes
  // through it: without it the hero and the cameo are drawn as the fallback dog
  // for the first second of the chapter (#255)
  const ch = CHAPTERS[index];
  // whoever is being played as, not whoever the chapter is about: the wrong one
  // here is a fallback dog for the first second of every chapter (#255)
  if (ch) preload([heroOf(index), ch.cameo].filter(Boolean));
  hideOverlay();
  rotateHint(false);        // the bottom of the screen belongs to the HUD now (#252)
  hud.classList.remove("hidden");
  sound.unlock();
  sound.setMuted(!!save.muted);
  game.autoRun = !save.walk;
  game.start(index, save.hero);
  warmGreetings();
  abilityButton();
  updateHud();
}

/**
 * Fetch the greetings this run can produce, now rather than mid-jump (#366).
 *
 * A catch is not asked for the way a story card is: the player jumps into a
 * dog and the dog answers, so there is no moment between the request and the
 * line in which to load it. On slow 3G the clip arrived 2.5s after the catch,
 * by which time the greeting belongs to nothing on the screen.
 *
 * The set is taken from the run itself — `game.friends` is `placeFriends`,
 * which is PLAYABLE minus whoever is being played as — so it is three clips,
 * the three that can be heard in this chapter, and it changes with the hero
 * without anything here knowing why. Not the twelve greetings that exist, and
 * emphatically not the whole voice pack (#311).
 */
function warmGreetings() {
  const hero = characters.find((c) => c.id === game.hero);
  sound.warm(game.friends.map((f) => {
    const c = characters.find((x) => x.id === f.id);
    return greeting(hero ? hero.name : "Bluey", c ? c.name : f.id);
  }));
}

/**
 * The HUD's speaker icon, asked of the thing that is actually silent.
 *
 * Same pair as the story card's read button (#294), one level up: `save.muted`
 * is what is *persisted*, `sound.muted` is what refuses to make a noise. Boot
 * used to render this icon from the save one line before handing the same value
 * to the mixer, so the icon was a second opinion about a fact the mixer owns —
 * and it was already wrong for anything that mutes the mixer without writing the
 * save (#296).
 *
 * Subscribed to the mixer rather than called at each site that mutes: the two
 * literal sites were both "remember to redraw after setMuted", and a third
 * caller — `play()`, or a test — got no redraw at all. As `sound.onmute` it
 * reports the mute that took effect, from whoever took it.
 */
function muteIcon() {
  el("btn-mute").textContent = sound.muted ? "🔇" : "🔊";
}

/**
 * Put the special-move button on the screen, or take it away.
 *
 * Called when a chapter starts rather than every frame: whether there *is* a
 * move is settled by who is being played as, and only the charge on it changes
 * while playing. The colour is the move's own, so the ring, the glow and the
 * motes the engine draws are all the same colour without either side being
 * told about the other.
 */
function abilityButton() {
  const btn = el("btn-ability");
  const state = game && game.abilityState();
  if (!btn) return;
  btn.classList.toggle("hidden", !state);
  if (!state) return;
  el("ability-emoji").textContent = state.emoji;
  el("ability-name").textContent = state.name;
  btn.style.setProperty("--ability", (game.ability || {}).color || "#FFD166");
  btn.setAttribute("aria-label", `Special move: ${state.name}`);
}

function updateHud() {
  if (!game || game.mode !== "playing") return;
  el("hud-score").textContent = game.score;
  el("hud-tokens").textContent = `${game.collected}/${game.level.total}`;
  // double points, and who to thank for it: hidden until somebody has joined, so
  // the badge appearing is itself the news (#306)
  const mult = game.scoreMultiplier();
  el("hud-mult").classList.toggle("hidden", mult === 1);
  el("hud-mult").textContent = `×${mult} 🐾`;
  const pct = Math.max(0, Math.min(1, game.player.x / game.ch.length));
  el("hud-progress-fill").style.width = `${pct * 100}%`;
  el("hud-progress-dog").style.left = `${pct * 100}%`;
  // the ring is the cooldown, drawn from the engine's own clock: `--charge`
  // counts up to 1, so it fills back rather than draining away (#303)
  const move = game.abilityState();
  const btn = el("btn-ability");
  if (btn && move) {
    btn.style.setProperty("--charge", move.charge.toFixed(3));
    btn.classList.toggle("ready", move.ready);
    btn.classList.toggle("active", move.active);
  }
  requestAnimationFrame(updateHud);
}

const TOAST_MAX = 3;
const TOAST_REPEAT_MS = 1200;
const toastSeen = new Map();

// How far down the *picture* a toast sits. It was 26% of the window in the CSS,
// which is the same place on a laptop — there the picture fills the window — and
// nowhere near the game on a phone held upright, where the band is about a fifth
// of the screen and sits low (#254).
const TOAST_BAND = 0.26;

// Set from here rather than the stylesheet: only the engine knows where the band
// ended up, and it moves with every resize and rotate.
function placeToasts() {
  const layer = el("toast-layer");
  if (!layer || !game) return;
  const scene = game.sceneRect();
  layer.style.top = `${Math.round(scene.top + scene.height * TOAST_BAND)}px`;
}

function toast(text) {
  const layer = el("toast-layer");
  if (!layer) return;
  // before the pill goes in, so the first toast after a rotate is already in the
  // right place — a listener alone would put it right one frame late
  placeToasts();
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
    <p class="score-line" data-lead>Score ${r.score}</p>
    <p class="story">${ch.outro}</p>
    <table class="stats">
      <tr><td>${ch.tokenName} found</td><td>${r.collected} / ${r.total}</td></tr>
      ${ch.hasBalloon ? `<tr><td>keepy uppy bops</td><td>${r.bops}</td></tr>` : ""}
      <tr><td>hidden dollarbucks</td><td>${r.secret ? "found! 💰" : "still hidden"}</td></tr>
      <tr><td>chapter bonus</td><td>+${r.bonus}</td></tr>
      ${friendRow(r)}
      <tr><td>your best here</td><td>${best}</td></tr>
    </table>
    ${next < CHAPTERS.length ? "" : `<p class="story"><b>You found Floppy and got everyone home. 🐾</b></p>`}
    <div class="actions">
      ${next < CHAPTERS.length
        ? `<button class="big-btn" id="btn-next">▶ Chapter ${next + 1}: ${CHAPTERS[next].title}</button>`
        : `<button class="big-btn" id="btn-again">▶ Play again</button>`}
      <div class="btn-row">
        <button class="med-btn" id="btn-retry">↻ This chapter</button>
        <button class="med-btn" id="btn-chapters">Chapters</button>
        <button class="med-btn" id="btn-back">Menu</button>
      </div>
    </div>
  `, { transparent: true });
  // the same gate as the menu's, and reached by a player who has just finished a
  // chapter and wants the next one: straight in, and the story is still one tap
  // away under Chapters (#255)
  // said, not shown: this screen arrives on its own at the end of a chapter, and
  // the table is the answer to "what happened?" — the one question the player
  // this game is for cannot read the answer to (#293). Sentences rather than the
  // cells in order: "chippies found, 7 slash 10" is a table read out, not news.
  readAloud(resultLines(r));
  on("btn-next", () => play(next));
  on("btn-again", () => play(0));
  on("btn-retry", () => play(r.chapter));
  on("btn-chapters", chapterSelect);
  on("btn-back", menu);
}

/**
 * What the results screen says out loud, in the order it is read on screen.
 *
 * Its own function so a test can compare the two: every number on that table is
 * in here, and a screen that grows a row nothing says is the state this issue
 * was filed about.
 */
/**
 * Who joined the run, as a sentence: "Bingo", "Bingo and Bandit".
 *
 * One place, because the table and the read-aloud both need it and they have to
 * agree — the table is the answer to "what happened?" and the voice is the same
 * answer for a player who cannot read it (#293).
 */
function friendsWord(r) {
  const names = (r.friends || []).map((id) => {
    const c = (characters || []).find((x) => x.id === id);
    return c ? c.name : id;
  });
  if (!names.length) return "";
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * Under the score rather than above it: the panel body scrolls, and on a short
 * window the fold lands about here — a row inserted higher up pushed the score,
 * which is the headline, off the bottom of a screen that used to show it.
 */
function friendRow(r) {
  const who = friendsWord(r);
  return who ? `<tr><td>friends who joined</td><td>${who} — double points!</td></tr>` : "";
}

function resultLines(r) {
  const ch = CHAPTERS[r.chapter];
  const stars = ["no stars this time", "one star", "two stars", "three stars"][r.stars] || "";
  return [
    `${ch.title} — done!`,
    `${stars}.`,
    ch.outro,
    `You found ${r.collected} of ${r.total} ${ch.tokenName}.`,
    ch.hasBalloon ? `Keepy uppy bops: ${r.bops}.` : null,
    r.secret ? "You found the hidden dollarbucks!" : "The hidden dollarbucks is still hidden.",
    friendsWord(r) ? `${friendsWord(r)} ran with you, so everything was worth double.` : null,
    `Chapter bonus, ${r.bonus}. Your score, ${r.score}.`,
    `Your best here is ${(save.chapters[r.chapter] || {}).best || 0}.`,
  ].filter(Boolean);
}

/* --------------------------------------------------------------- gallery -- */

function gallery() {
  const cards = characters.map((c, i) => `
    <button class="char-card" data-id="${c.id}"${i === 0 ? " data-lead" : ""}>
      <canvas data-pal="${c.id}"></canvas>
      <b>${c.name}</b>
      <span>${c.role}</span>
    </button>`).join("");
  showOverlay(`
    <h2>Everyone you'll meet</h2>
    <p class="subtitle">${characters.length} characters — tap one to read about them.</p>
    <div class="gallery">${cards}</div>
    <div class="actions"><button class="med-btn" id="btn-back">← Back</button></div>
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
      <div class="bio-head" data-lead>
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
    <div class="actions">
      <div class="btn-row">
        <button class="med-btn" id="btn-back">← All characters</button>
        <button class="med-btn" id="btn-menu">Menu</button>
      </div>
    </div>
  `);  // over the idle scene, not over a chapter: no scrim (#335). It was the
      // only menu screen that dimmed the world behind it, which read as a card
      // floating over the gallery — and it replaces the gallery, it is not on
      // top of it.
  portrait(el("bio-dog"), c, "cheer");
  // the tap that got here was "tell me about this dog", and the answer was a
  // paragraph she cannot read (#293). The artwork credit is not read out: it is
  // for the grown-up holding the phone.
  // it opens with the dog themselves — every one of the 25 has a voice now, and
  // this hello is the only line eleven of them can ever say (#361). The rest is
  // the narrator's, because it is written *about* them, not by them.
  readAloud([hello(c.name), c.name,
             `${c.species}. ${c.role}${c.age ? `, age ${c.age}` : ""}.`,
             c.personality, c.funFact]);
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
    <!-- The total goes above the table, not under it (#351). It is what this
         screen is for, and at the foot of a list that grew to ten chapters it
         sat 41px below the fold on a phone held sideways — in a body that
         scrolls, with nothing on the screen saying so. -->
    <p class="stats-total" data-lead><b>Total</b>
      ${Object.values(save.chapters).reduce((s, c) => s + c.stars, 0)}/${CHAPTERS.length * STARS_PER_CHAPTER} stars
      · ${save.totalScore} points</p>
    <table class="stats">
      <tr><th>Chapter</th><th>Stars</th><th>Best</th></tr>
      ${rows}
    </table>
    <p class="story">Hidden dollarbucks found: <b>${found} / ${CHAPTERS.length}</b></p>
    <p class="joke">Chapters played: ${save.plays}</p>
    <div class="actions">
      <div class="btn-row">
        <button class="med-btn" id="btn-reset">Start over</button>
        <button class="med-btn" id="btn-back">← Back</button>
      </div>
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
    <div class="actions">
      <button class="big-btn" id="btn-resume" data-lead>▶ Keep playing</button>
      <div class="btn-row">
        <button class="med-btn" id="btn-retry">↻ Restart</button>
        <button class="med-btn" id="btn-chapters">Chapters</button>
        <button class="med-btn" id="btn-back">Menu</button>
      </div>
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
    e.preventDefault();
    game.press();
  };
  const up = () => { if (game) game.release(); };

  // The move. The jump listens on the canvas and this button is in the HUD, a
  // layer above it — so a thumb here fires the move and not a jump as well,
  // which would be one tap doing two things. That is a fact about the button
  // taking the tap, not about this handler: give it `pointer-events: none` and
  // the press falls straight through to the canvas — see abilities.json.
  const useAbility = () => { if (playing() && game.useAbility()) sound.unlock(); };
  el("btn-ability").addEventListener("click", useAbility);

  canvas.addEventListener("pointerdown", down);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);

  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    if (e.code === "Space" || e.code === "ArrowUp" || e.code === "KeyW") {
      e.preventDefault();
      if (playing()) game.press();
    }
    // a keyboard hand is nowhere near the bottom-right corner: Shift is under
    // the little finger and E is under the first, and neither is a jump
    if (e.code === "KeyE" || e.code === "ShiftLeft" || e.code === "ArrowDown") {
      e.preventDefault();
      useAbility();
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
  el("btn-full").addEventListener("click", () => {
    sound.unlock();
    sound.ui();
    toggleFullscreen();
  });
  // whoever changed it — a button here, Escape, the OS, another tab
  onFullscreenChange(fullscreenChanged);
  paintFullscreen();
  armSavedFullscreen();
  el("btn-mute").addEventListener("click", () => {
    save.muted = !save.muted;
    store();
    sound.unlock();
    sound.setMuted(save.muted);      // which redraws the icon
    // muting stops the story mid-sentence, so the card's speaker button is now
    // describing a read that is over (#290)
    setReadLabel();
  });

  // a phone held upright still plays, it just gets a nudge — on the way *into*
  // portrait, so turning back and forth is not answered with a pill that stays
  let wasPortrait = false;
  const checkOrientation = () => {
    const portraitMode = window.innerHeight > window.innerWidth * 1.1;
    const playing = game && game.mode === "playing";
    if (portraitMode && !wasPortrait && !playing) rotateHint(true);
    if (!portraitMode || playing) rotateHint(false);
    wasPortrait = portraitMode;
  };
  window.addEventListener("resize", checkOrientation);
  window.addEventListener("orientationchange", () => setTimeout(checkOrientation, 250));
  checkOrientation();

  // a toast already on screen when the window changes shape: the engine has moved
  // the picture, so the words about it move too. The 250ms is the engine's own
  // delay after a rotate — measured before it, the band is still the old one.
  window.addEventListener("resize", placeToasts);
  window.addEventListener("orientationchange", () => setTimeout(placeToasts, 250));

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

  // The recorded readings (#357): the manifest only — a few KB of line -> file,
  // no clip is fetched until a screen actually says one, because there are 171
  // of them and 8 MB is not what a phone should spend its first second on. A
  // deploy without it falls back to the browser voice, line by line, which is
  // what the game did before, so this failing is not worth stopping boot for.
  try {
    sound.useVoices(await (await fetch("data/voices.json")).json());
  } catch (e) {
    console.warn("no recorded voices:", e);
  }

  // Character artwork: the manifest first, then eagerly fetch only the cast the
  // menu and the first chapters need. The other twenty load when the gallery
  // asks for them, so a phone on mobile data is not made to wait for 25 images.
  await loadArt();
  preload(["bluey", "bingo", "bandit", "chilli", "muffin"]);

  game = new Game(canvas, characters);
  game.onEvent = (ev) => {
    if (ev.type === "toast") toast(ev.text);
    // the dog you just caught says hello in their own voice (#361). `recorded`
    // so a missing clip is silence rather than the browser's robot mid-run, and
    // no read button: this is the character talking, not a card being read.
    if (ev.type === "friend") {
      const hero = characters.find((c) => c.id === game.hero);
      sound.read([greeting(hero ? hero.name : "Bluey", ev.name)], { recorded: true });
    }
    // the chapter's cameo calls out their one line as you run past (#364). Same
    // `recorded` deal: the wording is chapters.js's `cameoSays`, so a chapter
    // that has not been rendered yet waves in silence.
    if (ev.type === "cameo" && ev.says) sound.read([ev.says], { recorded: true });
    if (ev.type === "complete") { recordRun(ev); setTimeout(() => results(ev), 900); }
  };
  window.game = game;                       // handy for tests and for curious dads
  window.__art = artState;                  // which sprites actually decoded
  window.__cast = CHAPTERS.map((c) => [c.hero, c.cameo]);  // who each chapter needs
  window.__sound = sound;                   // the mixer, so a test can mute it
                                            // *without* writing the save (#294)
  window.__ready = true;

  sound.onmute = muteIcon;   // before the first setMuted, so it draws the icon
  // the mixer was only told about a saved mute on the way into a chapter, and
  // the story card is reached before that: a game saved muted read the story out
  // loud on the first tap after a reload (#290)
  sound.setMuted(!!save.muted);
  wireInput();
  menu();
  replayEarlyTap();          // whatever was pressed while this was loading (#284)
}

boot();

export { starsFor };
