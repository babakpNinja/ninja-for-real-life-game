/*
 * sprites.js — draws the real character artwork, two ways.
 *
 * First choice is a pose frame: public/data/poses.json maps a character and a
 * state to the side-on renders in public/assets/poses/, and the whole frame is
 * drawn as one piece — flipped to face the way it is travelling, with a bob, a
 * squash and a lean about the feet on top (`frameMotion`). Nothing is cut up,
 * so nothing can tear. That is how a run cycle happens.
 *
 * Everything else falls through to the rig below, which is the same character
 * standing still, kept alive rather than made to run.
 *
 * Each sprite is one front-facing render (see public/data/asset-credits.json for
 * where every file came from). A rig in public/data/rigs.json names two lines
 * across it — the neck and the hip — which split it into head / torso / legs.
 * The parts are drawn back to front and each one overlaps the joint below it, so
 * a rotated part never opens a gap: the torso covers the hip seam, the head
 * covers the neck seam.
 *
 * A rig may also name optional extras, measured by hand off
 * `build_rigs.py --grid <id>`: a `tail` box, a list of `ears` boxes, one `eyes`
 * box per eye and a `lid` patch. A tail or ear box is *cut out* of whichever
 * band it sits in and redrawn rotated about its own pivot, so nothing is ever
 * painted twice. The eyes are not parts: a blink is wiped over each of them in
 * the average colour of the `lid` patch, a square of plain face. Every extra is
 * optional — a rig without them draws exactly as it did before they existed.
 *
 * Nothing here is required. If an image has not loaded yet, or a character has
 * no rig, the caller still gets a dog: we fall back to art.js's drawDog.
 */

import { drawDog } from "./art.js";

/** One image cache. The artwork is keyed by character id, poses by file path. */
function cache() {
  return {
    images: new Map(), // key -> HTMLImageElement once decoded
    pending: new Set(),
    failed: new Map(), // key -> { tries, retryAt }, everything not showing yet
  };
}
const art = cache();
const poseArt = cache();
const drawn = new Map(); // id -> "pose" | "rig" | "fallback", how it was last drawn
let rigs = {};
let poses = {}; // id -> state -> [frame, ...]
let credits = null;

/** Parts overlap their joint by this fraction of sprite height. */
const SEAM = 0.05;

/*
 * A dropped request is not a verdict. This is played on a phone in a car: one
 * tunnel while the gallery is pulling 25 images and that character used to be
 * the procedural dog until someone reloaded the page — which a three-year-old
 * does not do. So a failure is retried, with a delay that doubles, and then
 * given up on: `sprite()` is called from the render loop, so an unconditional
 * retry would be one request per character per frame.
 */
const TRIES = 5;
const BACKOFF = 500; // ms before the first retry; doubles each time
const BACKOFF_MAX = 2000; // ...up to this, so giving up takes ~5s, not ~8

// ...and the tunnel ending is a real event, not a guess. Coming back online
// clears the record, so everything that gave up is asked for once more on the
// next frame. Without this, a long tunnel is still permanent: five tries take
// under eight seconds and the connection is back a minute later.
if (typeof window !== "undefined" && window.addEventListener) {
  window.addEventListener("online", () => {
    art.failed.clear();
    poseArt.failed.clear();
  });
}

export function artState() {
  const { images, pending, failed } = art;
  return {
    loaded: [...images.keys()],
    pending: [...pending],
    // Still 'not showing its artwork right now', which is what every caller and
    // test reads it as — an id leaves this set only by actually loading.
    failed: [...failed.keys()],
    // ...and the ids nothing will ask for again, which is a different thing: a
    // file that is genuinely gone, rather than a connection that dropped.
    gaveUp: [...failed].filter(([, f]) => f.tries >= TRIES).map(([id]) => id),
    tries: Object.fromEntries([...failed].map(([id, f]) => [id, f.tries])),
    rigged: Object.keys(rigs),
    drawn: Object.fromEntries(drawn),
    // Pose frames are a separate cache keyed by path, deliberately: everything
    // above is per character, and a caller asking "is bluey loaded?" means her
    // render, not whether her run frame happens to have arrived too.
    posed: Object.keys(poses),
    poseFrames: [...poseArt.images.keys()],
    credits,
  };
}

export async function loadArt() {
  const grab = (p) => fetch(p).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const [c, r, p] = await Promise.all([
    grab("data/asset-credits.json"),
    grab("data/rigs.json"),
    grab("data/poses.json"),
  ]);
  credits = c;
  rigs = (r && r.rigs) || {};
  poses = (p && p.frames) || {};
  return { credits, rigs, poses };
}

export function creditFor(id) {
  return credits && credits.assets ? credits.assets[id] : null;
}

export function notice() {
  return (credits && credits.notice) || "";
}

/** The one-line version, for the menu. Same sentence the long notice starts with. */
export function noticeShort() {
  return (credits && credits.notice_short) || "";
}

/**
 * The image for a character, kicking off its download the first time it is
 * asked for — and again, later, if that download failed. Returns null until it
 * is ready, which is why every draw path has a fallback: the gallery must not
 * block on 25 downloads.
 */
function load(store, key, url) {
  const { images, pending, failed } = store;
  if (images.has(key)) return images.get(key);
  if (pending.has(key)) return null;
  const gone = failed.get(key);
  if (gone && (gone.tries >= TRIES || Date.now() < gone.retryAt)) return null;
  pending.add(key);
  const img = new Image();
  img.decoding = "async";
  img.onload = () => { pending.delete(key); failed.delete(key); images.set(key, img); };
  img.onerror = () => {
    pending.delete(key);
    const tries = (gone ? gone.tries : 0) + 1;
    const wait = Math.min(BACKOFF * 2 ** (tries - 1), BACKOFF_MAX);
    failed.set(key, { tries, retryAt: Date.now() + wait });
  };
  // A retry asks for a different URL: whatever went wrong the first time —
  // an error response, a proxy — must not be answered from the cache.
  img.src = gone ? `${url}?retry=${gone.tries}` : url;
  return null;
}

export function sprite(id) {
  const entry = creditFor(id);
  if (!entry) return art.images.get(id) || null;
  return load(art, id, entry.file);
}

/** Warm the cache for characters we know are about to be on screen. */
export function preload(ids) {
  ids.forEach((id) => {
    sprite(id);
    // ...and their action poses. A frame that arrives mid-chapter would pop
    // the character from the rig to the pose render in the middle of a run.
    Object.values(poses[id] || {}).forEach((frames) =>
      frames.forEach((f) => load(poseArt, f, f)));
  });
}

/* ------------------------------------------------------------------ poses -- */

/**
 * The whole animation vocabulary, as numbers rather than drawing code:
 *   lift    — how far off the ground, in sprite heights
 *   sx, sy  — squash and stretch about the feet
 *   lean    — torso rotation about the hip, radians
 *   head    — head rotation about the neck, on top of the lean
 *   tail    — wag about the tail's root
 *   ear     — ear rotation, mirrored left/right, lagging the head
 *
 * There is deliberately no leg rotation here. The source artwork is one
 * front-facing standing render, so a band cut below the hip is not a leg: it
 * is a rectangle holding both legs, the gap between them and — for Muffin —
 * her tail. Swinging that slid a grey slab out sideways at hip height, which
 * is most of what "the characters look messed up" was. A pose the artist drew
 * (poses.json) is the way to get a run cycle; the rig's job is now to keep a
 * character that has no pose art *alive* — breathing, wag, ears, blink —
 * without ever taking its legs apart.
 */
function poseFor(state, t) {
  const step = t * STRIDE;
  switch (state) {
    case "run": {
      const swing = Math.sin(step);
      const air = Math.abs(Math.sin(step));
      return {
        lift: air * 0.045,
        sx: 1 + (1 - air) * 0.03,
        sy: 1 - (1 - air) * 0.04,
        // small: with no leg swing under it, a big lean is just the body
        // sliding off its own hips
        lean: 0.02 + swing * 0.015,
        head: -0.02 + Math.sin(step * 0.5) * 0.04,
        tail: Math.sin(step * 2) * 0.34,
        ear: Math.sin(step - 0.7) * 0.11,
      };
    }
    case "jump":
      return {
        lift: 0, sx: 0.96, sy: 1.06, lean: 0.1, head: 0.06,
        tail: 0.3, ear: -0.16,
      };
    case "float": {
      const sway = Math.sin(t * 5);
      return {
        lift: 0,
        sx: 1,
        sy: 1,
        lean: -0.04 + sway * 0.05,
        head: 0.1,
        tail: sway * 0.22,
        ear: -0.1 + sway * 0.06,
      };
    }
    case "cheer": {
      const hop = Math.abs(Math.sin(t * 6));
      return {
        lift: hop * 0.07,
        sx: 1 - hop * 0.03,
        sy: 1 + hop * 0.05,
        lean: Math.sin(t * 6) * 0.04,
        head: -0.09 + Math.sin(t * 12) * 0.04,
        tail: Math.sin(t * 12) * 0.5,
        ear: Math.sin(t * 6 - 0.6) * 0.15,
      };
    }
    default: {
      // idle: breathing, a slow head tilt, a shift of weight
      const breath = Math.sin(t * 2);
      return {
        lift: 0.004 + breath * 0.004,
        sx: 1 - breath * 0.012,
        sy: 1 + breath * 0.012,
        lean: Math.sin(t * 1.3) * 0.015,
        head: Math.sin(t * 1.6 + 0.6) * 0.05,
        tail: Math.sin(t * 2.2) * 0.13,
        ear: Math.sin(t * 1.6) * 0.03,
      };
    }
  }
}

/* ------------------------------------------------------------ pose frames -- */

/**
 * The run cadence, in radians per second: `|sin(t * STRIDE)|` is 0 the instant a
 * foot is down and 1 at full stretch. Both the rig and the pose motion swing to
 * it, so a character falling back to the rig keeps time with one that has
 * artwork — and the game reads it too, through `footfall`, so the dust comes up
 * where the feet land instead of on a rhythm of its own.
 */
export const STRIDE = 11;

/**
 * Did a foot come down between `prev` and `now`?
 *
 * Contacts are the zeroes of the swing above, one every half turn, so the
 * question is whether `t * STRIDE` crossed a multiple of π in that interval —
 * which is frame-rate independent: a long frame that steps over a whole contact
 * still reports it, and a short one cannot report the same contact twice.
 */
export function footfall(prev, now) {
  const beat = Math.PI / STRIDE;
  return Math.floor(now / beat) > Math.floor(prev / beat);
}

/**
 * How long a character takes to change state, in seconds.
 *
 * `state` flips on a single frame — the instant a foot touches down the lean of
 * a jump is replaced by the lean of a run — and the drawing used to follow it in
 * one step. Over this long the old state is faded into the new one instead.
 *
 * Short on purpose: long enough that no frame carries the whole change, short
 * enough that a landing still lands. It is a *duration*, not the exponential
 * smoothing you would reach for first, because the smoothed value would have to
 * live somewhere — and the same character is drawn more than once per frame in
 * different states (the menu family, the gallery, the cameo), so one stored
 * value per id would be dragged between them. A duration is a pure function of
 * the caller's own clock, so every one of those draws is independent.
 */
export const BLEND = 0.13;

/**
 * `a` and `b` are the number bags `poseFor`/`frameMotion` return; this is the
 * one part-way between them. Keys are taken from `b`, the state being moved to,
 * so a missing key on the old side reads as "already there" rather than NaN.
 */
function mix(a, b, k) {
  const out = {};
  for (const key of Object.keys(b)) {
    const from = typeof a[key] === "number" ? a[key] : b[key];
    out[key] = from + (b[key] - from) * k;
  }
  return out;
}

/**
 * The crossfade a caller asked for, as a number 0..1, or 1 for "no crossfade":
 * no blend given, the state has not actually changed, or the change is done.
 */
function blendAmount(blend, state) {
  if (!blend || blend.from === undefined || blend.from === state) return 1;
  return Math.max(0, Math.min(1, blend.k));
}

/**
 * Motion applied to a *whole* pose render — no cutting, no rotating parts.
 *
 * The rig above exists because one standing render has to do everything. Where
 * the artist drew the pose (public/data/poses.json), the drawing is already
 * right and the only job left is to keep it alive: a bob, a squash on contact,
 * a lean. Nothing here can tear a character, because nothing here takes one
 * apart.
 *
 * `tilt` rotates about the feet rather than the hip, which is what a whole
 * body does; the rig's `lean` rotates the torso against stationary legs.
 */
function frameMotion(state, t) {
  switch (state) {
    case "run": {
      const stride = t * STRIDE;
      const air = Math.abs(Math.sin(stride)); // 0 at contact, 1 at full stretch
      return {
        lift: air * 0.05,
        sx: 1 + (1 - air) * 0.04,
        sy: 1 - (1 - air) * 0.05,
        tilt: 0.03 + Math.sin(stride * 2) * 0.015,
      };
    }
    case "jump":
      return { lift: 0, sx: 0.97, sy: 1.05, tilt: -0.07 };
    case "float": {
      const sway = Math.sin(t * 5);
      return { lift: 0, sx: 1, sy: 1, tilt: -0.05 + sway * 0.06 };
    }
    case "cheer": {
      const hop = Math.abs(Math.sin(t * 6));
      return { lift: hop * 0.08, sx: 1 - hop * 0.04, sy: 1 + hop * 0.05,
               tilt: Math.sin(t * 6) * 0.05 };
    }
    default: {
      const breath = Math.sin(t * 2);
      return {
        lift: 0.004 + breath * 0.004,
        sx: 1 - breath * 0.012,
        sy: 1 + breath * 0.012,
        tilt: Math.sin(t * 1.3) * 0.02,
      };
    }
  }
}

/**
 * The pose to draw for `id` in `state`, or null if there is no pose artwork for
 * it or it has not arrived yet — in which case the caller uses the rig.
 *
 * One render per state, and it does not cycle. This used to step through the
 * list at 12fps, which was cycling code that could never run: the wiki holds
 * exactly one action render of each character (I listed all 462 files under
 * their names to be sure), so every state ships a single frame and the modulo
 * was always 0. Worse than dead — the two candidates for a second Bluey run
 * frame are `Bluey-Running` (three-quarter view, facing right) and
 * `Bluey-Leaping` (front-on, legs splayed); alternating those twelve times a
 * second is a strobe between two different drawings, not a run cycle. A still
 * that bobs and squashes to `STRIDE` reads better than that, and the dust at
 * its feet does the rest.
 *
 * `poses.json` still stores a list, so real frames are data if a set ever
 * exists — but the code that walks it will be written against artwork that
 * actually cycles, not kept warm in the hope of it.
 */
function poseFrame(id, state) {
  const frames = (poses[id] || {})[state];
  if (!frames || !frames.length) return null;
  const path = frames[0];
  const img = load(poseArt, path, path);
  return img && img.width ? img : null;
}

/* --------------------------------------------------------------- blinking -- */

const BLINK_EVERY = 3.4; // seconds between blinks
const BLINK_FOR = 0.16; // how long one takes, top to bottom and back

/**
 * How shut the eyes are right now, 0..1. Offset per character so a room full
 * of dogs does not blink in unison — from the id's own letters, because
 * anything random would re-roll on every frame.
 *
 * Exported so that a caller wanting to *see* a blink can search for one rather
 * than reimplement this offset and drift away from it.
 */
export function blinkAmount(id, t) {
  let seed = 0;
  for (let i = 0; i < id.length; i++) seed = (seed * 31 + id.charCodeAt(i)) % 997;
  const phase = (t + (seed / 997) * BLINK_EVERY) % BLINK_EVERY;
  if (phase > BLINK_FOR) return 0;
  return Math.sin((phase / BLINK_FOR) * Math.PI);
}

/* ------------------------------------------------------------------- draw -- */

/**
 * A pose render is the same dog drawn at a different height on the page: a
 * running Bluey is crouched, so scaling her to the same pixel height as the
 * standing render makes her *grow* the moment she starts running. This is the
 * fraction of the nominal size a pose is drawn at, measured by drawing the two
 * side by side (scripts/rig_frames.py) until the heads match.
 */
const POSE_SIZE = 0.86;

function shadow(ctx, x, y, size, alpha) {
  ctx.save();
  ctx.globalAlpha = Math.max(0, alpha);
  ctx.fillStyle = "#000000";
  ctx.beginPath();
  ctx.ellipse(x, y + 1, size * 0.3, size * 0.055, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function rotateAbout(ctx, px, py, angle) {
  ctx.translate(px, py);
  ctx.rotate(angle);
  ctx.translate(-px, -py);
}

/** A box in image pixels: [x0, y0, x1, y1]. */
function px(box, img) {
  return [box[0] * img.width, box[1] * img.height, box[2] * img.width, box[3] * img.height];
}

/** The same box in whole pixels, so a cut-out and its hole share an edge. */
function ipx(box, img) {
  return px(box, img).map(Math.round);
}

/**
 * Draw part of the image at its own position, minus any holes — the regions a
 * cut-out part was taken from, so the part is not painted twice.
 *
 * One clip and one draw, rather than tiling the leftover rectangles: the body
 * is scaled and rotated while this runs, so pieces cut on a fraction of a
 * device pixel leave hairline seams down the middle of a dog.
 */
function drawMinus(ctx, img, rect, holes) {
  let [x0, y0, x1, y1] = rect.map(Math.round);
  x0 = Math.max(0, x0); y0 = Math.max(0, y0);
  x1 = Math.min(img.width, x1); y1 = Math.min(img.height, y1);
  if (x1 - x0 <= 0 || y1 - y0 <= 0) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, y0, x1 - x0, y1 - y0);
  holes.forEach((h) => {
    // clipped to the rect first: an even-odd hole hanging over the edge would
    // turn the overhang into a second region to *draw*
    const [hx0, hy0] = [Math.max(h[0], x0), Math.max(h[1], y0)];
    const [hx1, hy1] = [Math.min(h[2], x1), Math.min(h[3], y1)];
    if (hx1 > hx0 && hy1 > hy0) ctx.rect(hx0, hy0, hx1 - hx0, hy1 - hy0);
  });
  ctx.clip("evenodd");
  ctx.drawImage(img, 0, 0);
  ctx.restore();
}

/** Draw a horizontal band of the source image at its own position. */
function band(ctx, img, y0, y1, holes = []) {
  drawMinus(ctx, img, [0, y0, img.width, y1], holes);
}

/** Draw one cut-out box, rotated about its pivot. */
function part(ctx, img, box, pivot, angle) {
  const [x0, y0, x1, y1] = ipx(box, img);
  ctx.save();
  rotateAbout(ctx, pivot[0] * img.width, pivot[1] * img.height, angle);
  ctx.drawImage(img, x0, y0, x1 - x0, y1 - y0, x0, y0, x1 - x0, y1 - y0);
  ctx.restore();
}

const lids = new Map(); // id -> "rgb(...)", the face colour its lids are drawn in
const pixelCache = new Map(); // image -> its ImageData, read back once

/**
 * The colour to draw an eyelid in: the average of the rig's `lid` patch, a
 * measured square of plain face beside the eyes.
 *
 * Two cleverer versions of this are in the history and both are worse. Copying
 * the pixels straight down from above the eye drags whatever marking sits there
 * down with it — Bandit gets a black eye, Chilli one brown lid. Voting on the
 * colours around the eye picks up the eye's own heavy outline, or the ear
 * behind it. The artwork is hand-measured everywhere else here; so is this.
 */
function pixels(img) {
  if (pixelCache.has(img)) return pixelCache.get(img);
  const c = document.createElement("canvas");
  c.width = img.width; c.height = img.height;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const data = g.getImageData(0, 0, img.width, img.height);
  pixelCache.set(img, data);
  return data;
}

function lidColour(img, id, box) {
  if (lids.has(id)) return lids.get(id);
  const [x0, y0, x1, y1] = ipx(box, img);
  const { data } = pixels(img);
  let n = 0, r = 0, g = 0, b = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * img.width + x) * 4;
      if (data[i + 3] < 200) continue;
      n++; r += data[i]; g += data[i + 1]; b += data[i + 2];
    }
  }
  const colour = n ? `rgb(${Math.round(r / n)},${Math.round(g / n)},${Math.round(b / n)})` : null;
  lids.set(id, colour);
  return colour;
}

/**
 * Wipe an eyelid down over one eye, in that eye's own surrounding fur colour.
 * Clipped to the ellipse the box encloses, because an eye is round: unclipped,
 * a half-shut lid is a rectangular slab laid across the face.
 */
function blink(ctx, img, colour, eye, amount) {
  if (amount <= 0.01) return;
  const [x0, y0, x1, y1] = px(eye, img);
  const w = x1 - x0, h = y1 - y0;
  ctx.save();
  ctx.beginPath();
  ctx.ellipse((x0 + x1) / 2, (y0 + y1) / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = colour;
  ctx.fillRect(x0, y0, w, h * amount);
  ctx.restore();
}

/** One pose render, drawn about the origin the transform has already set. */
function drawPose(ctx, img, size) {
  ctx.save();
  const s = (size * POSE_SIZE) / img.height;
  ctx.scale(s, s);
  ctx.translate(-img.width / 2, -img.height);
  ctx.drawImage(img, 0, 0);
  ctx.restore();
}

// Somewhere to mix two drawings of a character before either is shown. See
// `crossfade`.
let scratch = null;

/**
 * Draw `before` and `after` mixed `fade` of the way from one to the other.
 *
 * Both go on a scratch canvas, and the mix is `after` drawn over `before` with
 * `lighter`, which adds premultiplied — so the result is exactly
 * `before * (1 - fade) + after * fade`, in colour and in coverage. The obvious
 * version, drawing one at `1 - fade` and the other over it at `fade` straight
 * onto the scene, is not that: where the two drawings overlap it leaves a
 * `fade * (1 - fade)` share of the *background* showing through, up to a quarter
 * of it half way, so the character goes see-through in the middle of every
 * change. It also loses the rig, which is a dozen cut-outs overlapping at the
 * joints on purpose: at anything under full alpha each seam it hides comes back
 * as a dark band across the neck and the hip.
 *
 * The scratch is device pixels, taken off the transform already on `ctx`, and it
 * is copied back one pixel for one with no transform at either end — a copy at a
 * fractional offset, or scaled by the ceiling of its own size, resamples the
 * whole character, which reads as a soft pop on the frames either side of the
 * change.
 */
function crossfade(ctx, x, y, size, fade, before, after) {
  const w = size * 2.6;
  const h = size * 2.2;
  const feetY = h - size * 0.4; // room below the feet for the contact shadow
  const m = ctx.getTransform();
  const scale = Math.hypot(m.a, m.b) || 1;
  const X = Math.round(m.a * (x - w / 2) + m.c * (y - feetY) + m.e);
  const Y = Math.round(m.b * (x - w / 2) + m.d * (y - feetY) + m.f);
  const need = [Math.ceil(w * scale) + 2, Math.ceil(h * scale) + 2];
  scratch = scratch || document.createElement("canvas");
  if (scratch.width < need[0] || scratch.height < need[1]) {
    [scratch.width, scratch.height] = need; // only ever grows; sizing clears it
  }
  const c = scratch.getContext("2d");
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.globalCompositeOperation = "source-over";
  c.clearRect(0, 0, scratch.width, scratch.height);
  c.setTransform(m.a, m.b, m.c, m.d, m.e - X, m.f - Y);
  c.globalAlpha = 1 - fade;
  before(c);
  c.globalCompositeOperation = "lighter";
  c.globalAlpha = fade;
  const out = after(c);

  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(scratch, X, Y);
  ctx.restore();
  return out;
}

/**
 * Draw character `id` standing with its feet at (x, y), `size` px tall.
 * Signature matches art.js's drawDog so the two are interchangeable, with the
 * character id added: drawCharacter(ctx, id, x, y, size, pal, t, state, facing).
 *
 * `blend` is optional and says the character has only just changed state:
 * `{from: <the state before>, k: 0..1}`, k being how far through `BLEND` the
 * change is. Given one, the motion is part-way between the two states; and where
 * the two states are drawn from different artwork — or by different means at all,
 * which is the common case, since only Bluey has a drawing of a jump and the rest
 * of the cast falls back to the rig for it — the old drawing is faded out under
 * the new one. Callers with no state machine (the menus, the gallery, the cameo)
 * leave it out and get exactly what they got before.
 */
export function drawCharacter(ctx, id, x, y, size, pal, t, state = "run", facing = 1,
                              blend = null) {
  const fade = blendAmount(blend, state);
  const from = fade < 1 ? blend.from : null;
  // Same artwork either side (or none either side) means one drawing covers the
  // change: it is already moving on motion part-way between the two states.
  // Nothing to mix at the very ends of the fade either — and skipping them keeps
  // the two commonest frames of a change off the scratch canvas entirely.
  if (from && fade > 0.002 && fade < 0.998
      && poseFrame(id, from) !== poseFrame(id, state)) {
    return crossfade(ctx, x, y, size, fade,
                     (c) => draw(c, id, x, y, size, pal, t, state, facing, from, fade, from),
                     (c) => draw(c, id, x, y, size, pal, t, state, facing, from, fade));
  }
  return draw(ctx, id, x, y, size, pal, t, state, facing, from, fade,
              fade <= 0.002 ? from : state);
}

/**
 * One drawing of the character: `art` says which state's artwork to use, while
 * `state`, `from` and `fade` say how it moves. They come apart during a
 * crossfade, where the state being left is drawn on the motion of the state
 * being entered, so the two drawings sit in the same place and the fade reads as
 * one character changing rather than two overlaid.
 */
function draw(ctx, id, x, y, size, pal, t, state, facing, from, fade, art = state) {
  const airborne = state === "jump" || state === "float";

  // the artist's own drawing of this pose, if there is one — preferred over
  // anything the rig can assemble out of a standing render
  const frame = poseFrame(id, art);
  if (frame) {
    const m = from ? mix(frameMotion(from, t), frameMotion(state, t), fade)
                   : frameMotion(state, t);
    drawn.set(id, "pose");
    shadow(ctx, x, y, size, airborne ? 0.1 : 0.2 - m.lift * 1.2);
    ctx.save();
    ctx.translate(x, y - m.lift * size);
    ctx.scale(facing, 1);
    ctx.rotate(m.tilt); // about the feet: the whole dog leans, nothing shears
    ctx.scale(m.sx, m.sy);
    drawPose(ctx, frame, size);
    ctx.restore();
    return true;
  }

  const img = sprite(id);
  const rig = rigs[id];
  if (!img || !img.width || !rig) {
    drawn.set(id, "fallback");
    drawDog(ctx, x, y, size, pal, t, state, facing);
    return false;
  }
  drawn.set(id, "rig");

  const p = from ? mix(poseFor(from, t), poseFor(state, t), fade) : poseFor(state, t);

  // contact shadow — the baked-in one is stripped from every asset so that
  // this one can track how far off the ground the character actually is
  shadow(ctx, x, y, size, airborne ? 0.1 : 0.2 - p.lift * 1.2);

  const k = size / img.height;
  const hipY = rig.hip * img.height;
  const neckY = rig.neck * img.height;
  const seam = SEAM * img.height;

  ctx.save();
  ctx.translate(x, y - p.lift * size);
  ctx.scale(facing, 1);
  ctx.scale(p.sx, p.sy); // about the feet, so they stay on the ground
  ctx.scale(k, k);
  ctx.translate(-img.width / 2, -img.height);

  // the tail hangs behind the body, and its box is cut out of everything the
  // body draws so it is never painted in two places at once
  const holes = [];
  if (rig.tail) {
    holes.push(ipx(rig.tail.box, img));
    part(ctx, img, rig.tail.box, rig.tail.pivot, p.tail || 0);
  }

  // legs: drawn whole and upright. See poseFor — nothing rotates them, so the
  // band is one piece and carries a little material from above the hip for the
  // leaning torso to cover.
  drawMinus(ctx, img, [0, hipY - seam, img.width, img.height], holes);

  // torso leans about the hip and covers the hip seam
  ctx.save();
  rotateAbout(ctx, img.width / 2, hipY, p.lean);
  band(ctx, img, neckY, hipY + seam, holes);

  // head sits inside the torso's transform and covers the neck seam
  ctx.save();
  rotateAbout(ctx, img.width / 2, neckY, p.head);

  // ears rotate behind the head, mirrored, and are cut out of it; each keeps a
  // little material below its pivot for the head band to cover
  const ears = rig.ears || [];
  const earHoles = ears.map((e) => ipx([e.box[0], 0, e.box[2], e.box[3]], img));
  ears.forEach((e, i) => {
    const dir = i === 0 ? -1 : 1;
    part(ctx, img, [e.box[0], 0, e.box[2], e.box[3] + SEAM], e.pivot, (p.ear || 0) * dir);
  });
  band(ctx, img, 0, neckY + seam, earHoles);
  const shut = blinkAmount(id, t);
  if (rig.eyes && rig.lid && shut > 0.01) {
    const colour = lidColour(img, id, rig.lid);
    if (colour) rig.eyes.forEach((eye) => blink(ctx, img, colour, eye, shut));
  }
  ctx.restore();
  ctx.restore();

  ctx.restore();
  return true;
}

