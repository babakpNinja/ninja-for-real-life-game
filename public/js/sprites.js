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
      };
    }
    case "jump":
      return { lift: 0, sx: 0.96, sy: 1.06, lean: 0.1, head: 0.06, legs: [-0.55, -0.3] };
    case "float": {
      const sway = Math.sin(t * 5);
      return {
        lift: 0,
        sx: 1,
        sy: 1,
        lean: -0.04 + sway * 0.05,
        head: 0.1,
        legs: [0.5 + sway * 0.1, 0.6 - sway * 0.1],
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
      };
    }
  }
}

/* ------------------------------------------------------------------- draw -- */

function rotateAbout(ctx, px, py, angle) {
  ctx.translate(px, py);
  ctx.rotate(angle);
  ctx.translate(-px, -py);
}

/** Draw a horizontal band of the source image at its own position. */
function band(ctx, img, y0, y1) {
  const h = Math.min(img.height, y1) - y0;
  if (h <= 0) return;
  ctx.drawImage(img, 0, y0, img.width, h, 0, y0, img.width, h);
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

  // legs first: each part swings about its own hip pivot, and carries a little
  // material from above the hip so the rotation has something to hinge on
  const pivots = rig.legPivots || [0.5];
  const bounds = legBounds(pivots, img.width);
  pivots.forEach((px, i) => {
    ctx.save();
    rotateAbout(ctx, px * img.width, hipY, p.legs[i % p.legs.length] || 0);
    const [x0, x1] = bounds[i];
    ctx.drawImage(
      img, x0, hipY - seam, x1 - x0, img.height - hipY + seam,
      x0, hipY - seam, x1 - x0, img.height - hipY + seam
    );
    ctx.restore();
  });

  // torso leans about the hip and covers the hip seam
  ctx.save();
  rotateAbout(ctx, img.width / 2, hipY, p.lean);
  band(ctx, img, neckY, hipY + seam);

  // head sits inside the torso's transform and covers the neck seam
  ctx.save();
  rotateAbout(ctx, img.width / 2, neckY, p.head);
  band(ctx, img, 0, neckY + seam);
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
