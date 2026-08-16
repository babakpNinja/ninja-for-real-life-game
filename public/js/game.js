/*
 * game.js — the engine: fixed-timestep physics, a letterboxed canvas and a
 * deliberately gentle rule set.
 *
 * Design rule for this game: nothing can ever be lost. Bumping a pot plant
 * costs you a moment, falling in the creek gives you a splash and a lift back
 * up — there is no game over, no lives, no red text. Points only ever go up.
 */

import {
  drawTree, drawPalm, drawGumTree, drawHouse, drawCloud, drawBird, drawBalloon,
  drawPallets, drawTrolleys, drawStepLadder,
  drawDreamPlanet, drawCloudTower,
  drawToken, drawObstacle, roundRect, star,
} from "./art.js";
import { drawCharacter, footfall, stridePhase, BLEND } from "./sprites.js";
import { abilityFor, heroFor, PLAYABLE } from "./abilities.js";
import { sound } from "./audio.js";
import { CHAPTERS, buildLevel, sceneryFor, starsFor, highSky, IDLE_SKY, GROUND_Y, SEA_TOP,
  CLOUD_TOP, SKY_TOP, SKY_TILE, SKY_BAND, WORLD_W, WORLD_H } from "./chapters.js";

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

// Upright, fitting all 960px of world across a 390px phone draws Bluey 19px tall
// — a thumbnail under half a screen of empty sky (#261). So portrait draws the
// world this much bigger than "fit the width", which necessarily shows less level
// at once. Landscape and every desktop are untouched: there the world already
// fits by height with room to spare, and `Math.min` below still guarantees it.
export const PORTRAIT_ZOOM = 1.5;

// How much level stays behind the player, in world px — `CAM_X` on a wide screen,
// this upright. The zoom has to come out of somewhere, and the view behind is
// where nothing ever comes from: an auto-runner's hazards are all ahead of him.
// Measured on the real physics rather than reasoned about: every pit in the game,
// swept for the whole stretch of ground a jump can clear it from. A gap is 96px
// and a held jump carries about 220, so that stretch is short and it ends at the
// pit's own edge — what the view ahead buys is the time before it *opens*, which
// is all the warning there is. Upright that is 1.15s at hammerbarn's 292px/s,
// against 1.63s with no zoom at all; the tightest of those windows is 0.09s wide,
// so the seconds before it are the whole game.
export const PORTRAIT_LEAD = 120;

// How long a special move takes to come fully on, and to go fully off again.
// Every ability changes a number the picture is drawn from — speed, gravity, the
// pull on a chip — and switching one of those on a single frame is a jolt: the
// legs jump a cadence, the fall rate steps. So nothing reads `active` as a
// boolean; they all multiply by `abilityLevel()`, which eases 0 → 1 → 0 over
// this. Kept shorter than the shortest ability so there is always a middle where
// the move is at full strength.
const ABILITY_RAMP = 0.28;   // seconds, each end

// Catching a friend (#306). They trail you by a fixed *distance along the path
// you took*, not by a fixed time: the chapters run at 220–292 px/s, and a time
// lag turns into a longer gap the faster the chapter until the last of them is
// off the back of the screen. A gap of about a body and a half reads as a line
// of them running together rather than as one dog wearing three coats.
const FRIEND_GAP = 78;
// How much path is worth keeping: the furthest follower, and a little slack so
// the oldest sample is still *behind* them and there is something to interpolate
// towards. Anything older can never be asked for again.
const FRIEND_TRAIL = FRIEND_GAP * 4;
const FRIEND_SCORE = 2;      // "double the scores when I hit the targets"

export class Game {
  constructor(canvas, characters) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.characters = characters;
    this.scale = 1;
    this.offX = 0;
    this.offY = 0;
    this.camLead = CAM_X;     // narrowed upright by resize(), where the screen is known
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
    // The zoom only ever enters through the *width* term, so the world still fits
    // the height on every screen and a landscape phone — bound by its height —
    // scales exactly as it did (#261).
    const zoom = h > w ? PORTRAIT_ZOOM : 1;
    this.scale = Math.min(zoom * w / WORLD_W, h / WORLD_H);
    // Centred while the picture fits across, and left-aligned once it does not:
    // the half that would hang off the left is level the player has already run
    // past, and hanging it off the right instead is warning he has not had yet.
    const spare = w - WORLD_W * this.scale;
    this.offX = Math.max(0, spare / 2);
    // With less of the level on screen, holding 300px of it behind him would take
    // the whole zoom out of the view ahead. Only when the picture really is wider
    // than the screen: a zoom that the height swallowed has cost nothing to pay for.
    this.camLead = spare < 0 ? PORTRAIT_LEAD : CAM_X;
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

  // Where the picture is on the page, in CSS pixels. The canvas is the whole
  // window, but the world is drawn into a band inside it — upright that band is
  // a fifth of a tall phone, sitting low. Anything the *page* puts over the game
  // and means to be about the game belongs against this rect rather than the
  // window, or it lands in the empty sky above Bluey (#254).
  // Clamped to the canvas, because upright the band is now drawn wider than the
  // phone (#261) and what a caller is asking for is where the picture is *on the
  // screen* — not where it would reach if the screen were wide enough to hold it.
  sceneRect() {
    const r = this.canvas.getBoundingClientRect();
    const left = Math.max(r.left, r.left + this.offX);
    const right = Math.min(r.right, r.left + this.offX + WORLD_W * this.scale);
    const top = Math.max(r.top, r.top + this.offY);
    const bottom = Math.min(r.bottom, r.top + this.offY + WORLD_H * this.scale);
    return { left, top, width: right - left, height: bottom - top, right, bottom };
  }

  palette(id) {
    const c = this.characters.find((x) => x.id === id) || this.characters[0];
    return c.palette;
  }

  /**
   * Begin a chapter, played as `heroId` if the player has chosen somebody.
   *
   * The hero is settled here and read from `this.hero` everywhere after, rather
   * than each site reaching for `ch.hero`: a chapter still has an owner (it is
   * whose story it is, and it is who the story card shows if nothing is chosen),
   * but who is *on screen* is the player's choice, and those two disagreeing is
   * a game that draws a different dog from the one on the button.
   */
  start(chapterIndex, heroId) {
    const ch = CHAPTERS[chapterIndex];
    const level = buildLevel(chapterIndex);
    this.chapterIndex = chapterIndex;
    this.ch = ch;
    this.hero = heroFor(heroId, ch);
    this.ability = abilityFor(this.hero);
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
      // The special move: seconds of it left, and seconds until it can be used
      // again. Both count down in `step`, so both are in game time and a paused
      // game does not quietly recharge.
      abil: 0,
      cool: 0,
    };
    this.abilityUses = 0;
    // who is waiting to be caught, and the path they will follow once they are
    this.friends = this.placeFriends(level, ch);
    this.joined = 0;
    this.path = [{ x: this.player.x, y: this.player.y, d: 0, state: "run", facing: 1, strode: 0 }];
    this.pathD = 0;
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

  /**
   * Fire the special move. Answers whether it actually went off, so the button
   * that called it can say no out loud instead of looking broken.
   *
   * Refused while it is already running, not just while cooling down: a second
   * press mid-Zoomies that silently restarted the clock would make the move
   * longer the more you mash it, which is the one strategy this design does not
   * want a three-year-old to find.
   */
  useAbility() {
    const a = this.ability;
    const p = this.player;
    if (!a || this.mode !== "playing" || this.paused) return false;
    if (p.abil > 0 || p.cool > 0) return false;
    p.abil = a.duration;
    p.cool = a.duration + a.cooldown;
    this.abilityUses++;
    // The one ability that is a shove rather than a window: spend it here, at
    // the press, because a launch applied gradually over a ramp is not a launch.
    if (a.launch) {
      p.vy = JUMP_V * a.launch;
      p.onGround = false;
      p.coyote = 0;
      this.puff(p.x, p.y, 14);
    }
    sound.ability();
    this.toast(`${a.emoji} ${a.name}!`);
    this.onEvent({ type: "ability", hero: this.hero, name: a.name });
    return true;
  }

  /**
   * How far *on* the special move is right now: 0 off, 1 at full strength.
   *
   * Every effect multiplies by this rather than branching on it, so a move
   * arrives and leaves over ABILITY_RAMP instead of on one frame. Smoothstep
   * rather than the bare ramp so the rate of change is zero at both ends too —
   * a linear ramp still has a corner in it, and a corner in the speed is a
   * visible tick in the legs.
   */
  abilityLevel() {
    const a = this.ability;
    const p = this.player;
    if (!a || p.abil <= 0) return 0;
    const on = a.duration - p.abil;          // seconds since it started
    const k = Math.max(0, Math.min(1, Math.min(on, p.abil) / ABILITY_RAMP));
    return k * k * (3 - 2 * k);
  }

  /** What the HUD button needs to draw itself: 0..1 charge, and whether it is live. */
  abilityState() {
    const a = this.ability;
    const p = this.player;
    if (!a) return null;
    return {
      name: a.name,
      emoji: a.emoji,
      active: p.abil > 0,
      // charge counts *up* to 1 = ready, so the ring fills rather than drains
      charge: p.cool > 0 ? 1 - p.cool / (a.duration + a.cooldown) : 1,
      ready: p.abil <= 0 && p.cool <= 0,
    };
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
    if (p.abil > 0) p.abil -= dt;
    if (p.cool > 0) {
      p.cool -= dt;
      // the move is back — said quietly, because the button lighting up is the
      // real message and a small player is not watching the corner for it
      if (p.cool <= 0) sound.recharged();
    }
    // 0 while nobody has a move on, which is the whole of ordinary play: every
    // use of it below multiplies, so an unused ability changes no number at all
    const power = this.abilityLevel();
    const a = this.ability || {};
    // the camera catching up after a teleport; nothing to do the rest of the time
    if (this.camSlack) {
      this.camSlack *= Math.exp(-dt / CAM_BLEND);
      if (Math.abs(this.camSlack) < 0.5) this.camSlack = 0;
    }

    // horizontal. `boost` is 1 unless somebody's move makes them faster, and it
    // eases in and out with `power` rather than switching — the legs are driven
    // by distance covered, so a step change in speed is a step change in stride.
    const boost = 1 + (a.speed ? (a.speed - 1) * power : 0);
    let speed = ch.speed * (p.slow > 0 ? 0.45 : 1) * boost;
    if (!this.autoRun) {
      const dir = (this.keys.right ? 1 : 0) - (this.keys.left ? 1 : 0);
      speed = dir === 0 ? 0 : ch.speed * dir * (p.slow > 0 ? 0.45 : 1) * boost;
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

    // Gravity, with a float while the finger is held on the way down — and a
    // lighter one still for whoever's move is falling slowly, which unlike the
    // held float needs no finger at all. `Math.min` of the two rather than a
    // product: they are both answers to "how fast does she come down", and
    // holding the screen during Floaty should not make her hang in the air.
    let fall = this.holding && p.vy > 0 ? FLOAT_GRAVITY : 1;
    if (a.fall && p.vy > 0) fall = Math.min(fall, 1 + (a.fall - 1) * power);
    const g = GRAVITY * (ch.gravityScale || 1) * fall;
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
      // Capped against the lead as well as the constant: slack holds the camera
      // ahead of where it belongs, and upright there are only PORTRAIT_LEAD px of
      // screen to the player's left to spend on that before he is off it (#261).
      const cap = Math.min(CAM_SLACK, this.camLead - PLAYER_W - 20);
      this.camSlack = Math.max(-cap, Math.min(cap, gap));  // is carried in `before`
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

    // collectibles. Whoever can sniff them out pulls the near ones in first —
    // before the pickup test, so one that arrives this frame is picked up this
    // frame rather than hovering inside her for one more.
    //
    // The secret dollarbucks is deliberately not in this loop: it is the one
    // thing in a chapter that has to be *found*, and a move that drags it out of
    // its hiding place would find it for you.
    if (a.magnet && power > 0) {
      const reach = a.magnet * power;
      const cx = p.x, cy = p.y - PLAYER_H / 2;
      for (const tk of this.level.tokens) {
        if (tk.taken) continue;
        const dx = cx - tk.x, dy = cy - tk.y;
        if (Math.abs(dx) > reach || Math.abs(dy) > reach) continue;
        if (Math.hypot(dx, dy) > reach) continue;
        // an exponential approach, not a constant speed: it leaves its perch
        // gently and arrives quickly, which is what "pulled" looks like
        const k = 1 - Math.exp(-5.5 * power * dt);
        tk.x += dx * k;
        tk.y += dy * k;
      }
    }
    for (const tk of this.level.tokens) {
      if (tk.taken) continue;
      if (Math.abs(tk.x - p.x) < 46 && Math.abs(tk.y - (p.y - PLAYER_H / 2)) < 62) {
        tk.taken = true;
        this.collected++;
        this.award(10);
        sound.collect(this.collected);
        this.sparkle(tk.x, tk.y);
      }
    }
    const sec = this.level.secret;
    if (!sec.taken && Math.abs(sec.x - p.x) < 52 && Math.abs(sec.y - (p.y - PLAYER_H / 2)) < 66) {
      sec.taken = true;
      this.secretFound = true;
      this.award(250);
      sound.treasure();
      this.sparkle(sec.x, sec.y, 26);
      this.toast("Dollarbucks! 💰 +250");
    }

    if (power > 0) this.trail(power, dt);

    if (this.balloon) this.stepBalloon(dt);

    this.stepFriends();

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
      this.award(15);
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

  /* ------------------------------------------------------------- friends -- */

  /**
   * Who is out there to be caught in this chapter, and where they are standing.
   *
   * Everyone the artwork can draw *running*, minus whoever is being played as: a
   * caught friend runs the rest of the chapter with you, and a character with no
   * run frame would run it as the standing rig — the #215 defect on the move.
   * That is why this is PLAYABLE and not the whole cast; the cameo gets away
   * with the rig because the cameo never moves.
   *
   * They stand on a high ledge where the chapter has them, so catching one is a
   * jump you decide to make rather than something that happens to you on the
   * way past. The backyard has no high ledges at all and they wait on the grass,
   * which is the right difficulty for chapter one anyway.
   */
  placeFriends(level, ch) {
    const used = new Set();
    return PLAYABLE.filter((id) => id !== this.hero).map((id, i) => {
      const spot = this.standingSpot(level, ch.length * (0.28 + i * 0.25), used);
      return { id, atX: spot.x, atY: spot.y, joined: false, gap: 0,
               x: spot.x, y: spot.y, state: "idle", facing: -1, strode: 0 };
    });
  }

  /** The free ledge nearest a point along the level, preferring a high one. */
  standingSpot(level, want, used) {
    const wide = level.plats.filter((s) => s.w >= 90 && !used.has(s));
    const high = wide.filter((s) => s.y <= GROUND_Y - 80);
    const from = high.length ? high : wide;
    let best = from[0];
    const mid = (s) => s.x + s.w / 2;
    for (const s of from) if (Math.abs(mid(s) - want) < Math.abs(mid(best) - want)) best = s;
    used.add(best);
    // a wide ledge is mostly floor: stand them where they were aimed for, but
    // never off the end of the thing they are standing on
    const x = Math.max(best.x + 40, Math.min(best.x + best.w - 40, want));
    return { x, y: best.y };
  }

  /**
   * Catching a friend, and everyone who has joined following along.
   *
   * Followers do not simulate. They replay the path the player actually took, so
   * they jump where you jumped and land where you landed — there is no second
   * physics body that could disagree with the first about where the floor is.
   * The path is measured in distance travelled rather than in time, so the line
   * keeps its shape in a fast chapter and a slow one alike.
   */
  stepFriends() {
    const p = this.player;
    const last = this.path[this.path.length - 1];
    const moved = Math.hypot(p.x - last.x, p.y - last.y);
    if (moved > 0.5) {
      this.pathD += moved;
      this.path.push({ x: p.x, y: p.y, d: this.pathD,
                       state: p.state, facing: p.facing, strode: p.strode });
      let drop = 0;
      while (this.path[drop + 1] && this.path[drop + 1].d < this.pathD - FRIEND_TRAIL) drop++;
      if (drop) this.path.splice(0, drop);
    }
    for (const f of this.friends) {
      if (!f.joined) {
        if (Math.abs(f.atX - p.x) < 48 && Math.abs(f.atY - p.y) < 74) this.catchFriend(f);
        continue;
      }
      const at = this.pathAt(this.pathD - f.gap);
      f.x = at.x;
      f.y = at.y;
      f.state = at.state;
      f.facing = at.facing;
      f.strode = at.strode;
    }
  }

  catchFriend(f) {
    f.joined = true;
    this.joined++;
    f.gap = FRIEND_GAP * this.joined;   // in the order they were picked up
    const c = this.characters.find((x) => x.id === f.id);
    const name = c ? c.name : f.id;
    sound.friend();
    this.sparkle(f.atX, f.atY - 30, 18);
    this.toast(`${name}'s with you! ×${FRIEND_SCORE} points 🐾`);
    this.onEvent({ type: "friend", character: f.id, name, joined: this.joined });
  }

  /** Where the player was `d` px ago, between the two samples that bracket it. */
  pathAt(d) {
    const path = this.path;
    if (d <= path[0].d) return path[0];
    for (let i = path.length - 1; i > 0; i--) {
      const a = path[i - 1];
      if (a.d > d) continue;
      const b = path[i];
      const k = b.d === a.d ? 0 : (d - a.d) / (b.d - a.d);
      // the state is the one being left rather than a mix of two: a follower is
      // in the pose the player was in at that point, and poses do not average
      return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k,
               state: a.state, facing: a.facing, strode: a.strode + (b.strode - a.strode) * k };
    }
    return path[0];
  }

  /**
   * What a point is worth right now: double while anybody is running with you.
   *
   * Doubled and not multiplied per friend — the ask was "double the scores", and
   * a ×2/×3/×4 ladder makes the third friend worth more than the first and turns
   * a gentle game into an optimisation. One number, so the HUD can say it.
   */
  scoreMultiplier() {
    return this.friends && this.friends.some((f) => f.joined) ? FRIEND_SCORE : 1;
  }

  /**
   * Every point this game gives out goes through here.
   *
   * The multiplier is a property of the run, not of the token: keeping the four
   * places that score in agreement by hand is how a fifth one gets added later
   * and quietly does not double.
   */
  award(points) {
    const got = Math.round(points * this.scoreMultiplier());
    this.score += got;
    return got;
  }

  finish() {
    this.finished = true;
    this.mode = "finished";
    const total = this.level.total;
    const stars = starsFor(this.collected, total);
    const bonus = this.award(100 + stars * 50);
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
      friends: this.friends.filter((f) => f.joined).map((f) => f.id),
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

  /**
   * The motes a special move leaves behind it while it is on.
   *
   * Emitted on an accumulator rather than "one per frame, sometimes": a 120Hz
   * step and a 60Hz one would otherwise lay down twice as much trail on the
   * faster machine, and the trail is the main thing on screen that says the
   * move is still running. Scaled by `power` at both ends, so it thins out as
   * the move eases off instead of stopping dead with it.
   */
  trail(power, dt) {
    const a = this.ability;
    const p = this.player;
    this.trailAcc = (this.trailAcc || 0) + dt * power;
    if (this.trailAcc < 0.04) return;
    this.trailAcc = 0;
    this.particles.push({
      x: p.x - p.facing * (6 + Math.random() * 16),
      y: p.y - 14 - Math.random() * PLAYER_H * 0.8,
      // thrown back the way she came, and drifting up: these are not dust, and
      // the particle loop's own gravity brings them down again on its own
      vx: -p.facing * (30 + Math.random() * 90),
      vy: -60 - Math.random() * 80,
      life: 0.32 + Math.random() * 0.2 * power,
      r: 2.5 + Math.random() * 4.5,
      color: a.color,
    });
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
    return Math.max(0, this.player.x - this.camLead);
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
    g.addColorStop(0, IDLE_SKY.sky[0]);
    g.addColorStop(1, IDLE_SKY.sky[1]);
    ctx.fillStyle = g;
    // Past both ends of the world where the screen is taller than it (#251). A canvas
    // gradient clamps to its end colours outside its own range, so the sky above y=0
    // is the sky's own top colour and no seam appears at the join.
    ctx.fillRect(0, this.viewTop, WORLD_W, this.viewBot - this.viewTop);
    // The same pass a chapter's sky gets, with the menu's palette (#329). Behind
    // the menu card this screen is the first thing anyone sees, and it had the
    // pre-#326 shape: everything drawn between y=80 and the ground, and a clamped
    // gradient above it. Not a second copy of that fix — the same function.
    this.renderHighSky(ctx, 0, IDLE_SKY);
    const t = performance.now() / 1000;
    for (let i = 0; i < 5; i++) drawCloud(ctx, ((i * 260 + t * 12) % 1200) - 120, 80 + i * 40, 1 + (i % 3) * 0.3, 0.85);
    ctx.fillStyle = "#7FBF6A";
    ctx.fillRect(0, GROUND_Y, WORLD_W, this.viewBot - GROUND_Y);
  }

  /**
   * The sky above the world, drawn only on screens tall enough to see it (#326).
   *
   * Two separate passes rather than one taller gradient: the band from 0 down is
   * left exactly as it was drawn before, so a laptop's picture is unchanged and
   * the phone's is the same picture with more of the sky on top of it. Everything
   * here is a function of world y and camera x alone — no `viewTop` — so the two
   * screens agree about what is at a given height.
   *
   * `ch` is a parameter and not `this.ch` because the menu's idle screen has no
   * chapter loaded and needs this same sky (#329) — it passes `IDLE_SKY`, which
   * carries the two fields this reads. A second copy of this function is how the
   * idle screen missed #326 in the first place.
   */
  renderHighSky(ctx, camX, ch = this.ch) {
    if (this.viewTop >= SKY_BAND[1]) return;
    const hi = ctx.createLinearGradient(0, SKY_TOP, 0, 0);
    hi.addColorStop(0, ch.skyHigh);
    hi.addColorStop(1, ch.sky[0]);
    ctx.fillStyle = hi;
    ctx.fillRect(0, this.viewTop, WORLD_W, -this.viewTop);

    if (!this._highSky || this._highSkyFor !== ch.id) {
      this._highSky = highSky(ch);
      this._highSkyFor = ch.id;
    }
    for (const it of this._highSky) {
      if (it.y < this.viewTop - 60) continue;
      // Further away than the clouds at y=70, so it slides more slowly, and it
      // tiles because the sky is longer than the level is.
      const x = (((it.x - camX * 0.09) % SKY_TILE) + SKY_TILE) % SKY_TILE - 320;
      if (it.kind === "star") {
        const tw = 0.5 + 0.5 * Math.sin(this.t * 2 + it.x);
        ctx.globalAlpha = it.alpha * (0.55 + tw * 0.45);
        ctx.fillStyle = "#FFF7D6";
        ctx.fillRect(x, it.y, 2.5 * it.scale, 2.5 * it.scale);
        ctx.globalAlpha = 1;
      } else if (it.kind === "bird") {
        drawBird(ctx, x, it.y, it.scale, it.alpha);
      } else {
        drawCloud(ctx, x, it.y, it.scale, it.alpha);
      }
    }
    ctx.globalAlpha = 1;
  }

  renderBackground(ctx, camX) {
    const ch = this.ch;
    const g = ctx.createLinearGradient(0, 0, 0, GROUND_Y);
    g.addColorStop(0, ch.sky[0]);
    g.addColorStop(1, ch.sky[1]);
    ctx.fillStyle = g;
    ctx.fillRect(0, this.viewTop, WORLD_W, this.viewBot - this.viewTop);
    this.renderHighSky(ctx, camX);

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

    // friends waiting to be caught. Drawn standing: "idle" has no artwork for
    // anybody, so it falls through to the rig, which is a character standing
    // still — which is exactly what they are doing (#306).
    for (const f of this.friends) {
      if (f.joined || f.atX < left - 120 || f.atX > right + 120) continue;
      const bob = Math.sin(this.t * 2.2 + f.atX) * 3;
      drawCharacter(ctx, f.id, f.atX, f.atY + bob, 78, this.palette(f.id), this.t, "idle", -1);
      // "over here!" — a bubble drawn out of shapes rather than an emoji: a
      // canvas has only the fonts the device has, and a headless Chromium draws
      // 👋 as nothing at all. What is on the screen has to be in the drawing.
      ctx.save();
      ctx.globalAlpha = 0.7 + Math.sin(this.t * 4 + f.atX) * 0.3;
      ctx.fillStyle = "#FFF7E6";
      roundRect(ctx, f.atX - 14, f.atY - 108 + bob, 28, 26, 9);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(f.atX - 5, f.atY - 84 + bob);
      ctx.lineTo(f.atX + 5, f.atY - 84 + bob);
      ctx.lineTo(f.atX - 1, f.atY - 76 + bob);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "#1D3557";
      ctx.font = "bold 18px system-ui";
      ctx.textAlign = "center";
      ctx.fillText("!", f.atX, f.atY - 89 + bob);
      ctx.restore();
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

    // the line of them, furthest back first so the nearest is in front, and all
    // of them behind the player: it is the player's run they are following
    const p = this.player;
    for (const f of [...this.friends].filter((f) => f.joined).sort((a, b) => b.gap - a.gap)) {
      if (f.x < left - 120 || f.x > right + 120) continue;
      drawCharacter(ctx, f.id, f.x, f.y, 86, this.palette(f.id), this.t, f.state, f.facing,
                    null, stridePhase(f.strode));
    }

    this.renderAura(ctx);
    drawCharacter(ctx, this.hero, p.x, p.y, 92, this.palette(this.hero), this.t, p.state, p.facing,
                  { from: p.was, k: (this.t - p.changedAt) / BLEND },
                  stridePhase(p.strode));
  }

  /**
   * The glow around whoever has their move on. Drawn under the character, so it
   * is a light they are standing in rather than a film over their face.
   *
   * Its whole strength comes from `abilityLevel()`, the same easing the physics
   * uses: what the player feels change and what they see change arrive together,
   * and neither of them arrives on one frame. The pulse on top of that is slow
   * and shallow — it is meant to read as "still going", not as a flash.
   */
  renderAura(ctx) {
    const power = this.abilityLevel();
    if (power <= 0) return;
    const p = this.player;
    const cy = p.y - PLAYER_H / 2;
    const pulse = 1 + Math.sin(this.t * 7) * 0.06;
    const r = 74 * pulse;
    const g = ctx.createRadialGradient(p.x, cy, 8, p.x, cy, r);
    g.addColorStop(0, this.ability.color);
    g.addColorStop(1, `${this.ability.color}00`);   // its own colour, faded out
    ctx.save();
    ctx.globalAlpha = 0.42 * power;
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
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
