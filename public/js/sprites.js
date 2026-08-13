/*
 * sprites.js — draws the real character artwork as an animated cut-out rig.
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

const images = new Map(); // id -> HTMLImageElement once decoded
const pending = new Set();
const failed = new Set();
const drawn = new Map(); // id -> "rig" | "fallback", how it was last drawn
let rigs = {};
let credits = null;

/** Parts overlap their joint by this fraction of sprite height. */
const SEAM = 0.05;

export function artState() {
  return {
    loaded: [...images.keys()],
    pending: [...pending],
    failed: [...failed],
    rigged: Object.keys(rigs),
    drawn: Object.fromEntries(drawn),
    credits,
  };
}

export async function loadArt() {
  const grab = (p) => fetch(p).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const [c, r] = await Promise.all([grab("data/asset-credits.json"), grab("data/rigs.json")]);
  credits = c;
  rigs = (r && r.rigs) || {};
  return { credits, rigs };
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
 * asked for. Returns null until it is ready, which is why every draw path
 * has a fallback: the gallery must not block on 25 downloads.
 */
export function sprite(id) {
  if (images.has(id)) return images.get(id);
  if (pending.has(id) || failed.has(id)) return null;
  const entry = creditFor(id);
  if (!entry) return null;
  pending.add(id);
  const img = new Image();
  img.decoding = "async";
  img.onload = () => { pending.delete(id); images.set(id, img); };
  img.onerror = () => { pending.delete(id); failed.add(id); };
  img.src = entry.file;
  return null;
}

/** Warm the cache for characters we know are about to be on screen. */
export function preload(ids) {
  ids.forEach((id) => sprite(id));
}

/* ------------------------------------------------------------------ poses -- */

/**
 * The whole animation vocabulary, as numbers rather than drawing code:
 *   lift    — how far off the ground, in sprite heights
 *   sx, sy  — squash and stretch about the feet
 *   lean    — torso rotation about the hip, radians
 *   head    — head rotation about the neck, on top of the lean
 *   legs    — rotation per leg part about its own hip pivot
 *   tail    — wag about the tail's root
 *   ear     — ear rotation, mirrored left/right, lagging the head
 */
function poseFor(state, t) {
  const step = t * 11; // run cadence
  switch (state) {
    case "run": {
      const swing = Math.sin(step);
      const air = Math.abs(Math.sin(step));
      return {
        lift: air * 0.045,
        sx: 1 + (1 - air) * 0.03,
        sy: 1 - (1 - air) * 0.04,
        lean: 0.05 + swing * 0.02,
        head: -0.02 + Math.sin(step * 0.5) * 0.04,
        legs: [swing * 0.4, -swing * 0.4],
        tail: Math.sin(step * 2) * 0.34,
        ear: Math.sin(step - 0.7) * 0.11,
      };
    }
    case "jump":
      return {
        lift: 0, sx: 0.96, sy: 1.06, lean: 0.1, head: 0.06, legs: [-0.55, -0.3],
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
        legs: [0.5 + sway * 0.1, 0.6 - sway * 0.1],
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
        legs: [Math.sin(t * 6) * 0.25, -Math.sin(t * 6) * 0.25],
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
        legs: [Math.sin(t * 1.3) * 0.05, Math.sin(t * 1.3) * 0.05],
        tail: Math.sin(t * 2.2) * 0.13,
        ear: Math.sin(t * 1.6) * 0.03,
      };
    }
  }
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

/**
 * Draw character `id` standing with its feet at (x, y), `size` px tall.
 * Signature matches art.js's drawDog so the two are interchangeable, with the
 * character id added: drawCharacter(ctx, id, x, y, size, pal, t, state, facing).
 */
export function drawCharacter(ctx, id, x, y, size, pal, t, state = "run", facing = 1) {
  const img = sprite(id);
  const rig = rigs[id];
  if (!img || !img.width || !rig) {
    drawn.set(id, "fallback");
    drawDog(ctx, x, y, size, pal, t, state, facing);
    return false;
  }
  drawn.set(id, "rig");

  const p = poseFor(state, t);
  const airborne = state === "jump" || state === "float";

  // contact shadow — the baked-in one is stripped from every asset so that
  // this one can track how far off the ground the character actually is
  ctx.save();
  ctx.globalAlpha = airborne ? 0.1 : 0.2 - p.lift * 1.2;
  ctx.fillStyle = "#000000";
  ctx.beginPath();
  ctx.ellipse(x, y + 1, size * 0.3, size * 0.055, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

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

  // legs: each part swings about its own hip pivot, and carries a little
  // material from above the hip so the rotation has something to hinge on
  const pivots = rig.legPivots || [0.5];
  const bounds = legBounds(pivots, img.width);
  pivots.forEach((pv, i) => {
    ctx.save();
    rotateAbout(ctx, pv * img.width, hipY, p.legs[i % p.legs.length] || 0);
    const [x0, x1] = bounds[i];
    drawMinus(ctx, img, [x0, hipY - seam, x1, img.height], holes);
    ctx.restore();
  });

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

/** Split the leg band into one column per pivot, cutting midway between them. */
function legBounds(pivots, width) {
  return pivots.map((p, i) => {
    const lo = i === 0 ? 0 : ((pivots[i - 1] + p) / 2) * width;
    const hi = i === pivots.length - 1 ? width : ((p + pivots[i + 1]) / 2) * width;
    return [Math.round(lo), Math.round(hi)];
  });
}
