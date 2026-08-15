/*
 * game.js — the engine: fixed-timestep physics, a letterboxed canvas and a
 * deliberately gentle rule set.
 *
 * Design rule for this game: nothing can ever be lost. Bumping a pot plant
 * costs you a moment, falling in the creek gives you a splash and a lift back
 * up — there is no game over, no lives, no red text. Points only ever go up.
 */

import {
  drawTree, drawPalm, drawGumTree, drawHouse, drawCloud, drawBalloon,
  drawPallets, drawTrolleys, drawStepLadder,
  drawDreamPlanet, drawCloudTower,
  drawToken, drawObstacle, roundRect, star,
} from "./art.js";
import { drawCharacter, footfall, stridePhase, BLEND } from "./sprites.js";
import { sound } from "./audio.js";
import { CHAPTERS, buildLevel, sceneryFor, starsFor, GROUND_Y, SEA_TOP, CLOUD_TOP,
  WORLD_W, WORLD_H } from "./chapters.js";

const GRAVITY = 2300;
const JUMP_V = -790;
const FLOAT_GRAVITY = 0.34;   // gravity multiplier while the finger stays down
const COYOTE = 0.14;          // grace period after walking off an edge
const BUFFER = 0.18;          // tap slightly early and it still counts
const RECOVERY_IN = 90;       // px in from the edge a splash lifts him onto
export const PLAYER_W = 46;     // exported for the ground-ahead sweep in the suite
const PLAYER_H = 74;
const CAM_X = 300;
// A respawn teleports the player to `recoverySpot`, and the camera follows the
// player exactly — so the whole background used to pan a few hundred pixels on
// one frame, which reads as a cut rather than a lift back up. The camera keeps
// its exact follow and carries the teleport as *slack*: the gap the jump left
// it with, decayed toward zero. Slack is zero in ordinary play, so nothing
// about the ordinary camera moves. Capped so the player it is lagging behind
// cannot be left off the side of the screen while it catches up.
export const CAM_BLEND = 0.3;   // seconds; exponential, so frame-rate independent
export const CAM_SLACK = 200;   // px, against CAM_X = 300

// How far down the canvas the world's ground line sits once the screen is taller
// than the world is (#251). The world is 16:9 and a phone held upright is not, so
// fitting it by width leaves 600px of spare height; painting that as sky and ground
// is what stops it being a navy wall, and this is where the horizon lands in what
// is left. Three quarters down, the way a photograph of a backyard is mostly sky —
// centred would put as much flat grass under the player as sky over her.
// Only ever a *lower* bound on the centred position, so a screen with a little
// spare height (a 16:10 laptop) does not move at all.
export const GROUND_ON_SCREEN = 0.74;

export class Game {
  constructor(canvas, characters) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.characters = characters;
    this.scale = 1;
    this.offX = 0;
    this.offY = 0;
    this.running = false;
    this.paused = false;
    this.mode = "idle";       // idle | playing | finished
    this.t = 0;
    this.acc = 0;
    this.last = 0;
    this.holding = false;
    this.jumpBuffer = 0;
    this.autoRun = true;
    this.keys = { left: false, right: false };
    this.toasts = [];
    this.particles = [];
    this.camSlack = 0;
    this.onEvent = () => {};
    this.resize();
    window.addEventListener("resize", () => this.resize());
    window.addEventListener("orientationchange", () => setTimeout(() => this.resize(), 200));
  }

  /* --------------------------------------------------------------- setup -- */

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.scale = Math.min(w / WORLD_W, h / WORLD_H);
    this.offX = (w - WORLD_W * this.scale) / 2;
    // Where the world band sits vertically. Centred while there is little spare
    // height, and pushed down once there is a lot, so the extra becomes sky rather
    // than being split evenly with the grass. `Math.max` against the centred
    // position is what keeps every screen that is roughly 16:9 exactly where it was.
    const slack = h - WORLD_H * this.scale;
    const centred = slack / 2;
    this.offY = slack <= 0 ? centred
      : Math.min(slack, Math.max(centred, h * GROUND_ON_SCREEN - GROUND_Y * this.scale));
    // What the canvas actually shows, in world units — past the top and bottom of
    // the world wherever the band does not reach. Every fill that used to stop dead
    // at 0 or WORLD_H reads these instead, which is the whole of #251: the bars were
    // never *drawn* navy, they were the one colour under the canvas showing through.
    this.viewTop = Math.min(0, -this.offY / this.scale);
    this.viewBot = Math.max(WORLD_H, (h - this.offY) / this.scale);
    this.dpr = dpr;
    if (this.mode !== "playing") this.render();
  }

  palette(id) {
    const c = this.characters.find((x) => x.id === id) || this.characters[0];
    return c.palette;
  }

  start(chapterIndex) {
    const ch = CHAPTERS[chapterIndex];
    const level = buildLevel(chapterIndex);
    this.chapterIndex = chapterIndex;
    this.ch = ch;
    this.level = level;
    this.mode = "playing";
    this.paused = false;
    this.t = 0;
    this.score = 0;
    this.collected = 0;
    this.bops = 0;
    this.stumbles = 0;
    this.splashes = 0;
    this.secretFound = false;
    this.toasts = [];
    this.particles = [];
    this.player = {
      x: 120, y: GROUND_Y, vy: 0, onGround: true, coyote: 0, state: "run", facing: 1, slow: 0,
      // the state being left, and when the change happened: the drawing crosses
      // from one to the other over BLEND rather than on a single frame
      was: "run", changedAt: this.t,
      // Ground covered on foot, which is what the legs are paid for. Not p.x:
      // a chapter is partly crossed in the air, and counting that distance
      // would put the feet back out of step with the floor.
      strode: 0,
    };
    this.camSlack = 0;
    this.balloon = ch.hasBalloon
      ? { x: 300, y: GROUND_Y - 300, vy: 0, vx: 0, hue: 0 }
      : null;
    this.cameoX = ch.length * 0.55;
    this.cameoShown = false;
    this.finished = false;
    sound.playTheme(ch.theme);
    this.loop();
  }

  stop() {
    this.running = false;
    this.mode = "idle";
    sound.stopMusic();
  }

  /* --------------------------------------------------------------- input -- */

  press() {
    this.jumpBuffer = BUFFER;
    this.holding = true;
  }

  release() {
    this.holding = false;
  }

  /* ---------------------------------------------------------------- loop -- */

  loop() {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    const frame = (now) => {
      if (!this.running) return;
      let dt = (now - this.last) / 1000;
      this.last = now;
      if (dt > 0.1) dt = 0.1;            // tab was backgrounded — don't teleport
      if (!this.paused && this.mode === "playing") {
        this.acc += dt;
        while (this.acc >= 1 / 120) {     // fixed step keeps phones and laptops equal
          this.step(1 / 120);
          this.acc -= 1 / 120;
        }
      }
      this.render();
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  /* -------------------------------------------------------------- physics -- */

  step(dt) {
    const p = this.player;
    const ch = this.ch;
    this.t += dt;
    if (this.jumpBuffer > 0) this.jumpBuffer -= dt;
    if (p.slow > 0) p.slow -= dt;
    // the camera catching up after a teleport; nothing to do the rest of the time
    if (this.camSlack) {
      this.camSlack *= Math.exp(-dt / CAM_BLEND);
      if (Math.abs(this.camSlack) < 0.5) this.camSlack = 0;
    }

    // horizontal
    let speed = ch.speed * (p.slow > 0 ? 0.45 : 1);
    if (!this.autoRun) {
      const dir = (this.keys.right ? 1 : 0) - (this.keys.left ? 1 : 0);
      speed = dir === 0 ? 0 : ch.speed * dir * (p.slow > 0 ? 0.45 : 1);
      if (dir !== 0) p.facing = dir;
    }
    const before = p.x;
    p.x += speed * dt;
    if (p.x < 40) p.x = 40;
    const strodeBefore = p.strode;
    if (p.onGround) p.strode += Math.abs(p.x - before);

    // jump
    if (this.jumpBuffer > 0 && (p.onGround || p.coyote > 0)) {
      p.vy = JUMP_V;
      p.onGround = false;
      p.coyote = 0;
      this.jumpBuffer = 0;
      sound.jump();
      this.puff(p.x, p.y, 6);
    }

    // gravity, with a float while the finger is held on the way down
    const g = GRAVITY * (ch.gravityScale || 1) * (this.holding && p.vy > 0 ? FLOAT_GRAVITY : 1);
    p.vy += g * dt;
    if (p.vy > 1400) p.vy = 1400;
    const prevY = p.y;
    p.y += p.vy * dt;

    // land on platforms (only from above, so you never clip through a ledge)
    let landed = false;
    for (const s of this.level.plats) {
      if (p.x + PLAYER_W / 2 < s.x || p.x - PLAYER_W / 2 > s.x + s.w) continue;
      if (prevY <= s.y + 8 && p.y >= s.y && p.vy >= 0) {
        p.y = s.y;
        p.vy = 0;
        landed = true;
        if (!p.onGround) this.puff(p.x, p.y, 4);
        p.onGround = true;
        break;
      }
    }
    if (!landed) {
      if (p.onGround) p.coyote = COYOTE;
      p.onGround = false;
    }
    if (p.coyote > 0) p.coyote -= dt;

    const state = p.onGround
      ? (speed === 0 ? "idle" : "run")
      : (this.holding && p.vy > 0 ? "float" : "jump");
    if (state !== p.state) {
      // where the crossfade starts from: the state actually on screen, which
      // during a quick jump-float-jump is a state part-way out of its own blend
      p.was = p.state;
      p.changedAt = this.t;
      p.state = state;
    }

    // Dust where the feet land. `footfall` answers from the same cadence the
    // legs and the bob swing to, so the puffs land on the beat at any frame
    // rate — and, since that cadence is now distance, at any speed: a slowed
    // player scuffs every 71px of beach, not every 0.29s.
    if (p.state === "run"
        && footfall(stridePhase(strodeBefore), stridePhase(p.strode))) {
      this.scuff(p.x, p.y, p.facing);
    }

    // fell in the water / off the edge — a splash and a friendly lift back up
    if (p.y > WORLD_H + 40) {
      this.splashes++;
      sound.splash();
      this.toast("Splash! 💦");
      const before = this.camAt();
      const spot = this.recoverySpot(p.x);
      p.x = spot.x;
      p.y = spot.y - 10;
      p.vy = -260;
      p.slow = 0.35;
      // hold the camera where it was and let it travel back over CAM_BLEND,
      // instead of the world jumping under a player who has not moved on screen
      const gap = before - this.camTarget();     // the pure target: the old slack
      this.camSlack = Math.max(-CAM_SLACK, Math.min(CAM_SLACK, gap));  // is carried in `before`
    }

    // soft obstacles: a stumble, never a loss
    for (const o of this.level.obstacles) {
      if (o.hit && this.t - o.hit < 1) continue;
      if (Math.abs(p.x - (o.x + o.w / 2)) < o.w / 2 + PLAYER_W / 2 - 8 &&
          p.y > o.y + 6 && p.y - PLAYER_H < o.y + o.h) {
        o.hit = this.t;
        if (o.kind === "cloud") {          // dream clouds bounce you up instead
          // and no `this.puff(` with it: the dust is what a foot kicks off the
          // ground, and a cloud is not ground — it is also the one bounce the
          // player triggers over and over, so dust here reads as a smoke trail
          p.vy = JUMP_V * 0.85;
          sound.bop();
          this.toast("Boing!");
        } else {
          p.slow = 0.6;
          p.x -= 16;
          sound.stumble();
          this.stumbles++;
          this.toast("Whoops!");
        }
      }
    }

    // collectibles
    for (const tk of this.level.tokens) {
      if (tk.taken) continue;
      if (Math.abs(tk.x - p.x) < 46 && Math.abs(tk.y - (p.y - PLAYER_H / 2)) < 62) {
        tk.taken = true;
        this.collected++;
        this.score += 10;
        sound.collect(this.collected);
        this.sparkle(tk.x, tk.y);
      }
    }
    const sec = this.level.secret;
    if (!sec.taken && Math.abs(sec.x - p.x) < 52 && Math.abs(sec.y - (p.y - PLAYER_H / 2)) < 66) {
      sec.taken = true;
      this.secretFound = true;
      this.score += 250;
      sound.treasure();
      this.sparkle(sec.x, sec.y, 26);
      this.toast("Dollarbucks! 💰 +250");
    }

    if (this.balloon) this.stepBalloon(dt);

    // the cameo friend waves as you pass
    if (!this.cameoShown && p.x > this.cameoX) {
      this.cameoShown = true;
      const c = this.characters.find((x) => x.id === this.ch.cameo);
      if (c) this.toast(`${c.name} says g'day! 👋`);
      this.onEvent({ type: "cameo", character: this.ch.cameo });
    }

    // particles + toasts
    for (const q of this.particles) {
      q.life -= dt;
      q.x += q.vx * dt;
      q.y += q.vy * dt;
      q.vy += 320 * dt;
    }
    this.particles = this.particles.filter((q) => q.life > 0);
    this.toasts = this.toasts.filter((x) => this.t - x.t < 1.5);

    if (p.x >= this.ch.length && !this.finished) this.finish();
  }

  // Where a splash puts him down: the far side of the water he went into, not
  // the ledge he walked off. He runs forward on his own and only jumps when a
  // finger says so, so a lift back *behind* the gap is a lift into the same gap
  // — a game left alone drowned at the first pit for as long as you watched it.
  // The landing spot is RECOVERY_IN past the edge he arrives on, or the middle
  // of a stone too small for that, so there is room to stand before the next one.
  //
  // There is always one: every chapter's ground runs several hundred px past its
  // finish line and `step()` ends the chapter at `p.x >= ch.length`, so no fall
  // the game can produce lands beyond the last ledge. That used to be a `if
  // (!next) return this.lastSafe` — a branch no chapter could reach, which kept a
  // `lastSafe` field updated on every landing to feed it, and which would have
  // put him down *behind* the water he fell into: the #176 bug it was written
  // before. The sweep in test_game.py holds the invariant instead, over every x
  // of every chapter rather than the handful of pits one play happens to find.
  recoverySpot(fellAt) {
    let next = null;
    for (const s of this.level.plats) {
      // room to stand *ahead* of him, so the ledge he just left is not "next"
      if (s.x + s.w < fellAt + PLAYER_W) continue;
      if (!next || s.x < next.x) next = s;
    }
    return { x: next.x + Math.min(RECOVERY_IN, next.w / 2), y: next.y };
  }

  stepBalloon(dt) {
    const b = this.balloon;
    const p = this.player;
    b.vy += 210 * dt;                                   // balloons fall slowly
    b.y += b.vy * dt;
    b.vx += ((p.x + 130 - b.x) * 1.4 - b.vx) * dt * 2.2; // drifts along with you
    b.x += b.vx * dt;

    const head = p.y - PLAYER_H;
    if (Math.abs(b.x - p.x) < 74 && Math.abs(b.y - head) < 72 && b.vy > -120) {
      b.vy = -430;
      b.vx += (b.x - p.x) * 1.2;
      this.bops++;
      this.score += 15;
      sound.bop();
      this.sparkle(b.x, b.y, 8);
      if (this.bops % 5 === 0) this.toast(`Keepy uppy! ×${this.bops}`);
    }
    if (b.y > GROUND_Y - 26) {                           // it touched down: no drama
      b.y = GROUND_Y - 26;
      b.vy = -380;
      this.toast("Pick it up!");
    }
    if (b.y < 40) { b.y = 40; b.vy = 60; }
  }

  finish() {
    this.finished = true;
    this.mode = "finished";
    const total = this.level.total;
    const stars = starsFor(this.collected, total);
    const bonus = 100 + stars * 50;
    this.score += bonus;
    sound.cheer();
    this.player.state = "cheer";
    this.onEvent({
      type: "complete",
      chapter: this.chapterIndex,
      score: this.score,
      collected: this.collected,
      total,
      stars,
      bops: this.bops,
      secret: this.secretFound,
      bonus,
    });
  }

  /* --------------------------------------------------------------- juice -- */

  toast(text) { this.toasts.push({ text, t: this.t }); this.onEvent({ type: "toast", text }); }

  puff(x, y, n) {
    for (let i = 0; i < n; i++) {
      this.particles.push({
        x, y, vx: (Math.random() - 0.5) * 90, vy: -Math.random() * 60,
        life: 0.4, r: 4 + Math.random() * 5, color: "rgba(255,255,255,0.75)",
      });
    }
  }

  /** A kick of dust under one foot, thrown back the way the runner came. */
  scuff(x, y, facing) {
    for (let i = 0; i < 3; i++) {
      this.particles.push({
        // warm and not white: the path under the runner is nearly white, and a
        // white puff at a third of a second's alpha was invisible on it —
        // present in `particles`, and present in nothing anyone could see
        x: x - facing * 6, y: y - 3,
        vx: -facing * (40 + Math.random() * 70), vy: -25 - Math.random() * 45,
        life: 0.45, r: 3 + Math.random() * 4, color: "rgba(176,160,133,0.9)",
      });
    }
  }

  sparkle(x, y, n = 12) {
    const colors = ["#FFD166", "#F25F5C", "#6EC5B8", "#FFFFFF"];
    for (let i = 0; i < n; i++) {
      this.particles.push({
        x, y, vx: (Math.random() - 0.5) * 260, vy: -Math.random() * 240,
        life: 0.6, r: 3 + Math.random() * 4, color: colors[i % colors.length],
      });
    }
  }

  /* -------------------------------------------------------------- render -- */

  /** Where the camera would sit with no teleport to recover from. */
  camTarget() {
    return Math.max(0, this.player.x - CAM_X);
  }

  /** Where it is drawn from: the target, plus whatever slack is left to spend.
   *  Everything that reads the camera — the sky, both parallax layers and the
   *  world itself — takes this one number, so they cannot come apart. */
  camAt() {
    return Math.max(0, this.camTarget() + this.camSlack);
  }

  render() {
    const ctx = this.ctx;
    const w = this.canvas.width, h = this.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#12263A";
    ctx.fillRect(0, 0, w, h);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.translate(this.offX, this.offY);
    ctx.scale(this.scale, this.scale);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, this.viewTop, WORLD_W, this.viewBot - this.viewTop);
    ctx.clip();

    if (this.mode === "idle" || !this.ch) {
      this.renderIdleSky(ctx);
      ctx.restore();
      return;
    }

    const camX = this.camAt();
    this.renderBackground(ctx, camX);

    ctx.save();
    ctx.translate(-camX, 0);
    this.renderLevel(ctx, camX);
    ctx.restore();

    ctx.restore();
  }

  renderIdleSky(ctx) {
    const g = ctx.createLinearGradient(0, 0, 0, WORLD_H);
    g.addColorStop(0, "#8FD3F4");
    g.addColorStop(1, "#E8F7FF");
    ctx.fillStyle = g;
    // Past both ends of the world where the screen is taller than it (#251). A canvas
    // gradient clamps to its end colours outside its own range, so the sky above y=0
    // is the sky's own top colour and no seam appears at the join.
    ctx.fillRect(0, this.viewTop, WORLD_W, this.viewBot - this.viewTop);
    const t = performance.now() / 1000;
    for (let i = 0; i < 5; i++) drawCloud(ctx, ((i * 260 + t * 12) % 1200) - 120, 80 + i * 40, 1 + (i % 3) * 0.3, 0.85);
    ctx.fillStyle = "#7FBF6A";
    ctx.fillRect(0, GROUND_Y, WORLD_W, this.viewBot - GROUND_Y);
  }

  renderBackground(ctx, camX) {
    const ch = this.ch;
    const g = ctx.createLinearGradient(0, 0, 0, GROUND_Y);
    g.addColorStop(0, ch.sky[0]);
    g.addColorStop(1, ch.sky[1]);
    ctx.fillStyle = g;
    ctx.fillRect(0, this.viewTop, WORLD_W, this.viewBot - this.viewTop);

    if (ch.id === "sleepytime") {
      // stars + a big friendly moon. The field stops short of the cloud sea
      // (#228) — a star below its top edge is a star underwater, and it is also
      // a speck of not-sky in the column of whatever is standing there.
      for (let i = 0; i < 60; i++) {
        const sx = (i * 137.5) % WORLD_W;
        const sy = (i * 61.7) % (CLOUD_TOP - 40);
        const tw = 0.5 + 0.5 * Math.sin(this.t * 2 + i);
        ctx.globalAlpha = 0.35 + tw * 0.5;
        ctx.fillStyle = "#FFF7D6";
        ctx.fillRect(sx, sy, 2.5, 2.5);
      }
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#FFF3C4";
      ctx.beginPath();
      ctx.arc(770, 110, 54, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "rgba(255,243,196,0.18)";
      ctx.beginPath();
      ctx.arc(770, 110, 86, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.fillStyle = "#FFE9A8";
      ctx.beginPath();
      ctx.arc(820, 96, 46, 0, Math.PI * 2);
      ctx.fill();
      for (let i = 0; i < 6; i++) {
        const x = ((i * 320 - camX * 0.18) % 1600 + 1600) % 1600 - 200;
        drawCloud(ctx, x, 70 + (i % 3) * 46, 1 + (i % 3) * 0.35, 0.9);
      }
    }

    // far parallax layer
    ctx.save();
    ctx.translate(-camX * 0.3, 0);
    if (ch.id === "beach") {
      ctx.fillStyle = ch.water;
      ctx.fillRect(-500, SEA_TOP, 6000, GROUND_Y + 10 - SEA_TOP);
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      for (let i = 0; i < 40; i++) {
        const x = i * 180 + Math.sin(this.t + i) * 20;
        ctx.fillRect(x, SEA_TOP + 20 + (i % 3) * 22, 60, 4);
      }
    } else if (ch.id === "hammerbarn") {
      // The shop floor this chapter's horizon names (#213). Without it the
      // aisles ended in mid-cream: the only surface in the picture was the
      // shelving itself, so anything standing in the middle distance was
      // standing on a line the background does not draw.
      ctx.fillStyle = "#BDB5AA";
      ctx.fillRect(-600, GROUND_Y, 7200, this.viewBot - GROUND_Y);
      for (let i = 0; i < 14; i++) {
        const x = i * 420;
        ctx.fillStyle = "#E4DED4";
        // 10px of lit floor under the racking, the same gap the hills of
        // chapters 1-2 leave above their ground line
        roundRect(ctx, x, GROUND_Y - 240, 300, 230, 8);
        ctx.fill();
        ctx.fillStyle = "#CFC7BB";
        for (let r = 0; r < 3; r++) ctx.fillRect(x + 16, GROUND_Y - 210 + r * 66, 268, 12);
      }
    } else if (ch.id === "sleepytime") {
      // The cloud sea this chapter's horizon names (#228). It had clouds and
      // nothing else, which meant no surface at any y, which meant the one
      // chapter in the game with an empty middle distance — hammerbarn's hole
      // before #213, and the same fix: the background has to paint the floor
      // before anything can be stood on it.
      //
      // Flat-topped, and the puffs along it are clipped to below the line. A
      // cloud top that bulged over would be sky turning solid a few px above a
      // planet's foot, i.e. a planet sunk in the cloud rather than resting on
      // it — the clip is what keeps that from depending on where it lands.
      ctx.fillStyle = "#4A4788";
      ctx.fillRect(-600, CLOUD_TOP + 14, 7200, this.viewBot - CLOUD_TOP - 14);
      const haze = ctx.createLinearGradient(0, CLOUD_TOP, 0, CLOUD_TOP + 14);
      haze.addColorStop(0, "rgba(74,71,136,0)");   // the sea, fading up into sky
      haze.addColorStop(1, "rgba(74,71,136,1)");
      ctx.fillStyle = haze;
      ctx.fillRect(-600, CLOUD_TOP, 7200, 14);
      ctx.save();
      ctx.beginPath();
      ctx.rect(-600, CLOUD_TOP, 7200, this.viewBot - CLOUD_TOP);
      ctx.clip();
      for (let i = 0; i < 22; i++) {
        // moonlight along the tops, and a darker roll under it
        drawCloud(ctx, i * 330 - 200, CLOUD_TOP + 6, 1.9, 0.15);
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = "#3C3A72";
        ctx.beginPath();
        ctx.ellipse(i * 330 - 20, CLOUD_TOP + 92, 210, 46, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      ctx.restore();
      // and the clouds still drifting up in the dream itself, above the sea
      for (let i = 0; i < 12; i++) {
        drawCloud(ctx, i * 520 + 100, 150 + (i % 3) * 52, 1.6, 0.5);
      }
    } else {
      for (let i = 0; i < 16; i++) {
        const x = i * 360;
        ctx.fillStyle = ch.id === "creek" ? "#8FBF7C" : "#9AD08A";
        ctx.beginPath();
        ctx.ellipse(x, GROUND_Y - 10, 240, 110, 0, Math.PI, 0);
        ctx.fill();
      }
    }
    ctx.restore();

    // mid parallax: the standing scenery, on the horizon the chapter declares.
    // Which items and where is `sceneryFor`, not this loop — everything here
    // stands on one line, and that line is the chapter's business.
    ctx.save();
    ctx.translate(-camX * 0.6, 0);
    for (const it of sceneryFor(ch)) {
      if (it.kind === "house") drawHouse(ctx, it.x, it.y, it.scale);
      else if (it.kind === "gum") drawGumTree(ctx, it.x, it.y, it.scale);
      else if (it.kind === "pallets") drawPallets(ctx, it.x, it.y, it.scale);
      else if (it.kind === "trolleys") drawTrolleys(ctx, it.x, it.y, it.scale);
      else if (it.kind === "ladder") drawStepLadder(ctx, it.x, it.y, it.scale);
      else if (it.kind === "planet") drawDreamPlanet(ctx, it.x, it.y, it.scale);
      else if (it.kind === "tower") drawCloudTower(ctx, it.x, it.y, it.scale);
      else if (it.kind === "palm") drawPalm(ctx, it.x, it.y, it.scale);
      else drawTree(ctx, it.x, it.y, it.scale, it.leaf, it.trunk);
    }
    ctx.restore();
  }

  renderLevel(ctx, camX) {
    const ch = this.ch;
    const left = camX - 120, right = camX + WORLD_W + 120;

    // water under the gaps (creek + beach)
    if (ch.water) {
      ctx.fillStyle = ch.water;
      ctx.fillRect(left, GROUND_Y + 6, right - left, this.viewBot - GROUND_Y);
      ctx.fillStyle = "rgba(255,255,255,0.35)";
      for (let x = Math.floor(left / 90) * 90; x < right; x += 90) {
        ctx.fillRect(x + Math.sin(this.t * 2 + x) * 8, GROUND_Y + 24, 46, 5);
      }
    }

    // platforms
    for (const s of this.level.plats) {
      if (s.x + s.w < left || s.x > right) continue;
      const grad = ctx.createLinearGradient(0, s.y, 0, s.y + 90);
      grad.addColorStop(0, ch.ground[0]);
      grad.addColorStop(1, ch.ground[1]);
      ctx.fillStyle = grad;
      const h = s.y >= GROUND_Y ? this.viewBot - s.y + 20 : 34;
      roundRect(ctx, s.x, s.y, s.w, h, s.y >= GROUND_Y ? 10 : 14);
      ctx.fill();
      // a lighter lip so the landing edge is obvious
      ctx.fillStyle = "rgba(255,255,255,0.28)";
      roundRect(ctx, s.x + 4, s.y + 3, s.w - 8, 7, 4);
      ctx.fill();
      if (ch.id === "backyard" || ch.id === "creek") {
        ctx.strokeStyle = "rgba(255,255,255,0.35)";
        ctx.lineWidth = 2;
        for (let x = s.x + 14; x < s.x + s.w - 10; x += 26) {
          ctx.beginPath();
          ctx.moveTo(x, s.y + 2);
          ctx.lineTo(x + 5, s.y - 9);
          ctx.stroke();
        }
      }
    }

    for (const o of this.level.obstacles) {
      if (o.x + o.w < left || o.x > right) continue;
      drawObstacle(ctx, o, this.t);
    }

    for (const tk of this.level.tokens) {
      if (tk.taken || tk.x < left || tk.x > right) continue;
      drawToken(ctx, tk.x, tk.y, 17, ch.tokenKind, this.t);
    }
    const sec = this.level.secret;
    if (!sec.taken && sec.x > left && sec.x < right) {
      ctx.save();
      ctx.translate(sec.x, sec.y + Math.sin(this.t * 3) * 6);
      ctx.fillStyle = "#3FAE6B";
      roundRect(ctx, -22, -14, 44, 28, 6);
      ctx.fill();
      ctx.fillStyle = "#FFF7E6";
      ctx.font = "bold 18px system-ui";
      ctx.textAlign = "center";
      ctx.fillText("$", 0, 6);
      ctx.restore();
    }

    // cameo friend standing near the middle of the level
    const cam = this.characters.find((c) => c.id === ch.cameo);
    if (cam && this.cameoX > left && this.cameoX < right) {
      drawCharacter(ctx, cam.id, this.cameoX, GROUND_Y, 74, cam.palette, this.t, "cheer", -1);
    }

    // the finish: Floppy the bunny / home
    this.renderGoal(ctx, left, right);

    if (this.balloon) drawBalloon(ctx, this.balloon.x, this.balloon.y, 30, "#F25F5C");

    for (const q of this.particles) {
      ctx.globalAlpha = Math.max(0, q.life * 1.6);
      ctx.fillStyle = q.color;
      ctx.beginPath();
      ctx.arc(q.x, q.y, q.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    const p = this.player;
    drawCharacter(ctx, ch.hero, p.x, p.y, 92, this.palette(ch.hero), this.t, p.state, p.facing,
                  { from: p.was, k: (this.t - p.changedAt) / BLEND },
                  stridePhase(p.strode));
  }

  renderGoal(ctx, left, right) {
    const gx = this.ch.length + 40;
    if (gx < left || gx > right) return;
    ctx.save();
    ctx.translate(gx, GROUND_Y);
    // bunting between two poles
    ctx.strokeStyle = "#B8763F";
    ctx.lineWidth = 8;
    ctx.beginPath();
    ctx.moveTo(-60, 0); ctx.lineTo(-60, -180);
    ctx.moveTo(90, 0); ctx.lineTo(90, -180);
    ctx.stroke();
    ctx.strokeStyle = "#FFF7E6";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(-60, -180);
    ctx.quadraticCurveTo(15, -140, 90, -180);
    ctx.stroke();
    const flags = ["#F25F5C", "#FFD166", "#6EC5B8", "#4A90D9", "#F49AC1"];
    for (let i = 0; i < 5; i++) {
      const fx = -50 + i * 30;
      const fy = -178 + Math.sin((i / 4) * Math.PI) * 34;
      ctx.fillStyle = flags[i];
      ctx.beginPath();
      ctx.moveTo(fx - 11, fy);
      ctx.lineTo(fx + 11, fy);
      ctx.lineTo(fx, fy + 26);
      ctx.closePath();
      ctx.fill();
    }
    // Floppy waiting at the end of every chapter (in ch5 it's the bed)
    ctx.translate(15, -6);
    ctx.fillStyle = "#F6E7D2";
    roundRect(ctx, -22, -60, 44, 60, 20);
    ctx.fill();
    ctx.fillStyle = "#F6E7D2";
    roundRect(ctx, -16, -104, 12, 48, 6);
    ctx.fill();
    roundRect(ctx, 4, -104, 12, 48, 6);
    ctx.fill();
    ctx.fillStyle = "#22303F";
    ctx.beginPath(); ctx.arc(-7, -46, 3.4, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(7, -46, 3.4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#F49AC1";
    ctx.beginPath(); ctx.arc(0, -38, 4, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
}

export { GROUND_Y, WORLD_W, WORLD_H, star };
