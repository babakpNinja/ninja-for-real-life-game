/*
 * sprites.js — draws the real character artwork, two ways.
 *
 * First choice is a pose frame: public/data/poses.json maps a character and a
 * state to the side-on renders in public/assets/poses/, drawn flipped to face
 * the way it is travelling, with a bob, a squash and a lean about the feet on
 * top (`frameMotion`). A stride render is cut once, at the hip its own entry in
 * public/data/pose-joints.json names, and the band below it swings to the
 * cadence — one drawing cannot run, and the wiki has no cycle whose feet are
 * inside its own artwork (see `poseFrame`). Every other pose is drawn whole.
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
let poseJoints = {}; // frame path -> {hip, pivot}, the ones that get cut
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
    // ...and the frames still on their way, for a caller that needs the picture
    // to have stopped changing before it measures anything: a run frame that
    // decodes between two renders swaps the character's drawing under them.
    posePending: [...poseArt.pending],
    credits,
  };
}

export async function loadArt() {
  const grab = (p) => fetch(p).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const [c, r, p, j] = await Promise.all([
    grab("data/asset-credits.json"),
    grab("data/rigs.json"),
    grab("data/poses.json"),
    grab("data/pose-joints.json"),
  ]);
  credits = c;
  rigs = (r && r.rigs) || {};
  poses = (p && p.frames) || {};
  // a frame with no joint — or a page that never got this file — is drawn
  // whole, which is exactly what every pose did before the hip existed
  poseJoints = (j && j.joints) || {};
  return { credits, rigs, poses, poseJoints };
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

/*
 * A character with no run artwork does not run: it bounds.
 *
 * The rig cannot swing legs (see `poseFor`), so its old run was a 4%-of-a-body
 * bob, and Chilli — the hero of a whole chapter — slid along the beach bobbing
 * (#168). There is no side-on running render of her to fetch: I listed all 55
 * files under her name and all 72 under Muffin's, and the only dynamic Chilli is
 * a dance. A dog with its legs together is not a jogging dog, though, it is a
 * bounding one. So the rig bounds: gather on contact, push, fly, land. The whole
 * body arcs, squashes and pitches as one drawing, and nothing is cut apart.
 *
 * The cadence is unchanged. The arc's contacts are the zeroes of
 * `|sin(t * STRIDE)|` — the same instants `footfall` reports — so a bounding
 * character lands where its own dust comes up and stays in step with one running
 * on real artwork beside it.
 */
const BOUND = {
  lift: 0.155, // sprite heights at the top of the arc
  squash: 0.1, // how far the body gathers at contact
  stretch: 0.05, // and draws out at the apex
  tilt: 0.13, // pitch into the bound, radians, never let all the way to zero
};

/**
 * The whole animation vocabulary, as numbers rather than drawing code:
 *   lift    — how far off the ground, in sprite heights
 *   sx, sy  — squash and stretch about the feet
 *   tilt    — whole-body pitch about the feet, radians
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
 * (poses.json) is the way to get a run cycle; the rig's job is to move a
 * character that has no pose art without ever taking its legs apart.
 */
function poseFor(state, t, step) {
  switch (state) {
    case "run": {
      const swing = Math.sin(step);
      // 0 the instant a foot is down, 1 at the top; `air * (2 - air)` is off the
      // ground quickly and hangs at the apex, the way a body under gravity does
      const air = Math.abs(swing);
      const arc = air * (2 - air);
      return {
        lift: BOUND.lift * arc,
        // gathered on contact, drawn out in the air — the squash is about the
        // feet, so they stay on the ground while the body compresses
        sx: 1 + BOUND.squash * (1 - arc) - BOUND.stretch * 0.7 * arc,
        sy: 1 - BOUND.squash * (1 - arc) + BOUND.stretch * arc,
        tilt: BOUND.tilt * (0.45 + 0.55 * arc),
        // small: with no leg swing under it, a big lean is just the body
        // sliding off its own hips. The travelling pitch is `tilt`, which turns
        // the whole drawing about the feet and cannot shear anything.
        lean: 0.02 + swing * 0.015,
        head: -0.02 + Math.sin(step * 0.5) * 0.04,
        tail: Math.sin(step * 2) * 0.34,
        ear: Math.sin(step - 0.7) * 0.11,
      };
    }
    case "jump":
      return {
        lift: 0, sx: 0.96, sy: 1.06, tilt: -0.04, lean: 0.1, head: 0.06,
        tail: 0.3, ear: -0.16,
      };
    case "float": {
      const sway = Math.sin(t * 5);
      return {
        lift: 0,
        sx: 1,
        sy: 1,
        tilt: 0,
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
        tilt: 0,
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
        tilt: 0,
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
 * The speed the cadence above is right at, in px/s, and the radians of stride
 * that buys per pixel travelled.
 *
 * `STRIDE` on its own is a *wall-clock* rhythm, and that is a footskate: the
 * five chapters run at 220–292 px/s and a slowed player at 0.45 of that, so
 * the ground moved past at anything from 99 to 292 px/s while the legs kept
 * one fixed beat. A dog whose feet plant every 0.29s regardless of how far it
 * got is skating, and no amount of squash on top reads as anything else —
 * this, not the still frame, is the larger half of why running looked like
 * sliding.
 *
 * So the phase is carried by *distance*: `stridePhase` below. 250 is the
 * middle of the chapter speeds, which makes this a pure refactor at that
 * speed — the look everyone signed off on is the look at 250 px/s.
 */
export const NOMINAL_SPEED = 250;

/**
 * The stride phase of something that has travelled `px` pixels on foot.
 *
 * Distance *on foot*, which is why the game accumulates it rather than passing
 * `p.x`: a chapter crossed partly in the air covers ground that no step paid
 * for, and counting it would put the feet back out of step with the floor.
 */
export function stridePhase(px) {
  return (px * STRIDE) / NOMINAL_SPEED;
}

/**
 * Did a foot come down between stride phase `prev` and `now`?
 *
 * Contacts are the zeroes of the swing above, one every half turn, so the
 * question is whether the phase crossed a multiple of π in that interval —
 * which is frame-rate independent: a long frame that steps over a whole contact
 * still reports it, and a short one cannot report the same contact twice.
 *
 * Both arguments are *stride phase*, not seconds (`stridePhase(px)`), so the
 * dust comes up every 71px of ground rather than every 0.29s. The two were the
 * same thing while the cadence was a wall-clock rhythm; they are not once a
 * slowed player takes 2.2 steps in the time an unslowed one takes 1.
 */
export function footfall(prev, now) {
  return Math.floor(now / Math.PI) > Math.floor(prev / Math.PI);
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
 * How far the legs of a stride render swing either side of the pose the artist
 * drew, in radians. 0.22 is about 12.5 degrees.
 *
 * Chosen off `build_pose_joints.py --sheet`, which draws the same cut at
 * several angles: at 20 degrees the hip material that the torso is supposed to
 * cover swings out past the body outline on Bingo and Bandit — a notch in the
 * silhouette — and at 7 it is a twitch. This is the largest angle where all
 * four renders still read as one dog.
 */
const LEG_SWING = 0.22;

/**
 * How far a borrowed run render's legs are swung to read as a leap, in radians.
 *
 * Only a *borrowed* one moves: this is applied to whatever `poseJoint` names a
 * hip in, and the only frames with a hip are the four run renders
 * (`pose-joints.json`), so a character with its own jump drawing — Bluey's
 * `Bluey-Leaping`, Bingo's crop of `Jump_bluey_bingo` — is drawn exactly as
 * before, whole. The data decides who this touches, not a list of names here.
 *
 * Same magnitude as `LEG_SWING` on purpose rather than by coincidence: that is
 * the largest angle `build_pose_joints.py --sheet` shows all four renders still
 * reading as one dog at, and a jump has no reason to be the one place that
 * bound is exceeded. It is negative because the artwork's own jumps are
 * *stretched*, not tucked — in both drawings the legs trail behind a body
 * pitched forward — and a negative swing carries the run's leg mass back the
 * same way (#215).
 */
const JUMP_SWING = -LEG_SWING;

/**
 * Motion applied to a pose render: a bob, a squash on contact, a lean about the
 * feet, and — for a stride — how far the legs are swung from where they were
 * drawn.
 *
 * `swing` is zero at every contact, so a foot is planted in exactly the pose
 * the artist drew and `footfall` puffs its dust there; the drawing is what
 * carries the stride, and this only carries it between two of them.
 *
 * `tilt` rotates about the feet rather than the hip, which is what a whole
 * body does; the rig's `lean` rotates the torso against stationary legs. Every
 * state names `swing` even where it is 0, so a change of state interpolates the
 * legs back rather than snapping them (see `mix`, which takes its keys from the
 * state being entered).
 */
function frameMotion(state, t, stride) {
  switch (state) {
    case "run": {
      const air = Math.abs(Math.sin(stride)); // 0 at contact, 1 at full stretch
      return {
        lift: air * 0.05,
        sx: 1 + (1 - air) * 0.04,
        sy: 1 - (1 - air) * 0.05,
        tilt: 0.03 + Math.sin(stride * 2) * 0.015,
        swing: Math.sin(stride) * LEG_SWING,
      };
    }
    case "jump":
      return { lift: 0, sx: 0.97, sy: 1.05, tilt: -0.07, swing: JUMP_SWING };
    case "float": {
      const sway = Math.sin(t * 5);
      return { lift: 0, sx: 1, sy: 1, tilt: -0.05 + sway * 0.06,
               swing: JUMP_SWING };
    }
    // The cheer keeps `swing: 0` on a borrowed drawing on purpose (#231), which
    // is worth writing down because it is the one state where a zero reads as a
    // shrug. Bandit has no cheer render, so he celebrates in his running one —
    // legs mid-stride — and the only lever here is a rotation of the whole band
    // below the hip: it can point that spread somewhere else, which is what the
    // jump does, but one rigid band cannot bring the legs together, so there is
    // no angle at which a stride stands still. The alternative is letting a
    // borrowed cheer fall to the rig, and that is the upright dog you would want
    // — measured, 13% taller than the stride at the same nominal size, standing
    // where this one leans. It also makes him a hand-drawn dog while he runs and
    // an assembled one the instant he stops, crossfading between the two at the
    // finish line: #215 moved rather than fixed, and
    // `test_a_hero_is_the_same_kind_of_drawing_all_the_way_through` holds that
    // line. So the stride stays and the hop, squash and tilt carry the
    // celebration. The price is the gallery card, where Bandit is the one dog
    // leaning into a run among 24 standing ones. It was looked at before it was
    // kept, and `test_a_borrowed_cheer_keeps_the_legs_the_artist_drew` is where
    // the decision is asserted rather than assumed.
    case "cheer": {
      const hop = Math.abs(Math.sin(t * 6));
      return { lift: hop * 0.08, sx: 1 - hop * 0.04, sy: 1 + hop * 0.05,
               tilt: Math.sin(t * 6) * 0.05, swing: 0 };
    }
    default: {
      const breath = Math.sin(t * 2);
      return {
        lift: 0.004 + breath * 0.004,
        sx: 1 - breath * 0.012,
        sy: 1 + breath * 0.012,
        tilt: Math.sin(t * 1.3) * 0.02,
        swing: 0,
      };
    }
  }
}

/**
 * A state drawn by another state's artwork, where that is the better drawing.
 *
 * Read as a *chain*: float borrows the jump, and a jump nobody has drawn
 * borrows the run. `poseFile` walks it until a state has frames.
 *
 * `float` is the jump held down — the player keeps a finger on the screen and
 * the fall slows — so it is the same body in the air, and nobody has ever drawn
 * a separate one. Without this line a jump that is held swaps the artist's
 * leaping render for the rig half-way up and back again on the way down, which
 * is the sliced-limbs look reappearing mid-jump on the one character who has
 * the artwork to avoid it.
 *
 * `jump` and `cheer` fall back to the run for the opposite reason: not because
 * it is the same drawing, but because the alternative is a *different
 * character*. Bandit has no jump or cheer render on the wiki and Chilli has no
 * jump — all 13,961 files were listed for #206/#207 — so both used to drop to
 * the rig the instant they left the ground, and the rig's source is a
 * front-facing standing render. The dog you were running with became a
 * front-on dog at a different scale, standing upright with his arms down: not
 * mangled, but read as the wrong character entirely (#215).
 *
 * Their run render is the same side-on cutout family, it is the same size, and
 * `pose-joints.json` names a hip in it — so `frameMotion` can tuck the legs
 * under and pitch the body, which is a leap. A borrowed pose in motion beats a
 * costume change.
 *
 * This is a floor and not an override: `poseFile` asks for the state's own
 * frames first, so the day somebody draws a real leaping Bandit, that drawing
 * is what leaps. fetch_assets.py reads this object (`pose_fallbacks`) so its
 * coverage check follows the same chain.
 */
const POSE_FALLBACK = { float: "jump", jump: "run", cheer: "run" };

/**
 * The pose to draw for `id` in `state`, or null if there is no pose artwork for
 * it or it has not arrived yet — in which case the caller uses the rig.
 *
 * One render per state, and it does not cycle. This used to step through the
 * list at 12fps, which was cycling code that could never run: every state
 * ships a single frame, so the modulo was always 0.
 *
 * The frames do exist and they are not usable. Listing all 13,961 files on the
 * wiki turns up 379 animated GIFs, two of which are the thing this wants —
 * `BlueyRun.gif` (31 frames) and `BingoRun.gif` (42), each holding a real
 * side-on run cycle a few frames long. Both are capture from the show, and the
 * show frames a run as a close-up: keyed out and trimmed, Bluey's cycle is cut
 * across the shins and Bingo's below the knee. A sprite whose feet are outside
 * its own artwork cannot stand on the floor, and `drawPose` anchors on the
 * bottom of the image, so the cut edge would *become* the contact point.
 *
 * The still ones are no better as a pair: the two candidates for a second Bluey
 * run frame are `Bluey-Running` (three-quarter view, facing right) and
 * `Bluey-Leaping` (front-on, legs splayed), and alternating those twelve times
 * a second is a strobe between two drawings, not a run cycle.
 *
 * So the cycle is made, not found: `poseJoint` below names a hip on the one
 * drawing there is and `drawPose` swings the band under it, which is the rig's
 * trick applied to a side-on drawing rather than a standing one. `poses.json`
 * still stores a list, so real frames stay data if a usable cycle ever turns up.
 *
 * Where a state has no artwork, POSE_FALLBACK may name one that is the same
 * drawing before the rig is reached; anything not there falls to the rig.
 */
function poseFrame(id, state) {
  const path = poseFile(id, state);
  if (!path) return null;
  const img = load(poseArt, path, path);
  return img && img.width ? img : null;
}

/**
 * The file `id` in `state` is drawn from, before it has loaded.
 *
 * Walks POSE_FALLBACK for as long as the state it lands on has no frames, so
 * a held jump by a character with no jump drawing reaches the run render
 * (float -> jump -> run) rather than stopping one link short and falling to the
 * rig. Bounded by the number of states in the chain and `seen`, because a
 * fallback that ever pointed in a circle would otherwise hang the frame.
 */
function poseFile(id, state) {
  const set = poses[id] || {};
  const seen = new Set();
  for (let want = state; want && !seen.has(want); want = POSE_FALLBACK[want]) {
    seen.add(want);
    const frames = set[want];
    if (frames && frames.length) return frames[0];
  }
  return null;
}

/**
 * Where that drawing is cut so its legs can swing — `{hip, pivot}` as fractions
 * of the image — or null for a pose that is drawn whole.
 *
 * Measured off the artwork by scripts/build_pose_joints.py, which is also where
 * the four hip lines are authored and what checks they still sit on a hip.
 * Exported so a test can ask where the cut is instead of hardcoding a number
 * that would go stale the day someone re-measures one.
 */
export function poseJoint(id, state) {
  const path = poseFile(id, state);
  return (path && poseJoints[path]) || null;
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

/**
 * The contact shadow. The artwork's baked-in one is stripped from every asset so
 * that this one can track how far off the ground the character actually is: it
 * fades *and* draws in as the body rises, which is most of what says "in the
 * air" for a character whose legs cannot move (the bound in `poseFor`).
 *
 * `lift` is the state's, in sprite heights. `airborne` is the other kind of off
 * the ground — a jump the game itself is moving the character through, where the
 * whole sprite has already left the floor and the shadow travels under it.
 */
function shadow(ctx, x, y, size, lift, airborne) {
  const up = airborne ? 0 : Math.min(Math.max(lift, 0), 0.2);
  ctx.save();
  ctx.globalAlpha = Math.max(0, airborne ? 0.1 : 0.2 - up * 1.2);
  ctx.fillStyle = "#000000";
  ctx.beginPath();
  const draw = 1 - up * 1.6;
  ctx.ellipse(x, y + 1, size * 0.3 * draw, size * 0.055 * draw, 0, 0, Math.PI * 2);
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

/**
 * One pose render, drawn about the origin the transform has already set.
 *
 * With no joint, or with the legs where they were drawn, that is one drawImage
 * and nothing can tear. Given a joint and a swing it is two bands: the legs,
 * turned about the hip, and then the rest of the body drawn over the top of
 * them. Both bands carry material across the hip line — the legs keep some of
 * the belly above it so a swing cannot open a gap, and the body reaches below
 * it so it covers the seam. That is the rig's arrangement (see `draw`), on
 * purpose: one way of hiding a joint, in one place.
 */
function drawPose(ctx, img, size, joint, swing) {
  ctx.save();
  const s = (size * POSE_SIZE) / img.height;
  ctx.scale(s, s);
  ctx.translate(-img.width / 2, -img.height);
  if (joint && Math.abs(swing) > 0.0005) {
    const hipY = joint.hip * img.height;
    const seam = SEAM * img.height;
    ctx.save();
    // clipped to everything below the hip *before* the swing, so the belly the
    // band carries can fill the wedge a turn opens without any of it ending up
    // above the hip: rotated, the far end of that strip lifts a good 40px, and
    // wherever the body above is transparent it would show as a shard of leg
    // floating by the hip. Wide enough not to clip the leg itself sideways.
    ctx.beginPath();
    ctx.rect(-img.width, hipY, img.width * 3, img.height * 2);
    ctx.clip();
    rotateAbout(ctx, joint.pivot * img.width, hipY, swing);
    band(ctx, img, hipY - seam, img.height);
    ctx.restore();
    band(ctx, img, 0, hipY + seam);
  } else {
    ctx.drawImage(img, 0, 0);
  }
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
                              blend = null, phase) {
  // A caller that does not move gets the old wall-clock rhythm: the menu family,
  // the gallery row and the cameo are cheering on the spot, and there is no
  // distance for their cadence to come from.
  const step = phase === undefined ? t * STRIDE : phase;
  const fade = blendAmount(blend, state);
  const from = fade < 1 ? blend.from : null;
  // Same artwork either side (or none either side) means one drawing covers the
  // change: it is already moving on motion part-way between the two states.
  // Nothing to mix at the very ends of the fade either — and skipping them keeps
  // the two commonest frames of a change off the scratch canvas entirely.
  if (from && fade > 0.002 && fade < 0.998
      && poseFrame(id, from) !== poseFrame(id, state)) {
    return crossfade(ctx, x, y, size, fade,
                     (c) => draw(c, id, x, y, size, pal, t, step, state, facing, from, fade, from),
                     (c) => draw(c, id, x, y, size, pal, t, step, state, facing, from, fade));
  }
  return draw(ctx, id, x, y, size, pal, t, step, state, facing, from, fade,
              fade <= 0.002 ? from : state);
}

/**
 * One drawing of the character: `art` says which state's artwork to use, while
 * `state`, `from` and `fade` say how it moves. They come apart during a
 * crossfade, where the state being left is drawn on the motion of the state
 * being entered, so the two drawings sit in the same place and the fade reads as
 * one character changing rather than two overlaid.
 */
function draw(ctx, id, x, y, size, pal, t, step, state, facing, from, fade, art = state) {
  const airborne = state === "jump" || state === "float";

  // the artist's own drawing of this pose, if there is one — preferred over
  // anything the rig can assemble out of a standing render
  const frame = poseFrame(id, art);
  if (frame) {
    const m = from ? mix(frameMotion(from, t, step), frameMotion(state, t, step), fade)
                   : frameMotion(state, t, step);
    drawn.set(id, "pose");
    shadow(ctx, x, y, size, m.lift, airborne);
    ctx.save();
    ctx.translate(x, y - m.lift * size);
    ctx.scale(facing, 1);
    ctx.rotate(m.tilt); // about the feet: the whole dog leans, nothing shears
    ctx.scale(m.sx, m.sy);
    // the joint belongs to the artwork, so it is `art`'s and not the state's:
    // mid-crossfade the old drawing is on the new state's motion
    drawPose(ctx, frame, size, poseJoint(id, art), m.swing || 0);
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

  const p = from ? mix(poseFor(from, t, step), poseFor(state, t, step), fade)
                 : poseFor(state, t, step);

  shadow(ctx, x, y, size, p.lift, airborne);

  const k = size / img.height;
  const hipY = rig.hip * img.height;
  const neckY = rig.neck * img.height;
  const seam = SEAM * img.height;

  ctx.save();
  ctx.translate(x, y - p.lift * size);
  ctx.scale(facing, 1);
  // the whole rig pitches about its feet, exactly as a pose frame does: every
  // part is drawn inside this transform, so there is no seam for it to open
  ctx.rotate(p.tilt || 0);
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

