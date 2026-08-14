/*
 * chapters.js — the five chapters of "Ana Bingo!".
 *
 * Each chapter is a small data description plus a `build(rng)` that lays out the
 * level: ground platforms, collectibles, soft obstacles and cameo spots. Levels
 * are generated from a fixed seed so every play (and every test run) sees the
 * same course — a three-year-old likes knowing what comes next.
 */

export const GROUND_Y = 452;      // logical world units; canvas is 960x540
export const WORLD_H = 540;
export const WORLD_W = 960;

/**
 * The top of the sea on the chapters that have one — the far shoreline.
 *
 * Exported because two things have to agree about it: the band `renderBackground`
 * fills, and where the beach's palms stand (`horizon` below). They were the same
 * number written twice, and the palms were standing 120px under the water.
 */
export const SEA_TOP = GROUND_Y - 120;

/**
 * The narrowest gap the beach parts its sand by — a rock pool, not a channel.
 *
 * Exported so the test that says "ch4 reads as a beach" can measure against the
 * chapter's own number rather than a copy of it. The old build's gaps were 440,
 * which is 46% of the 960-wide view: at that width the water is the picture and
 * the sand is stepping stones in it (#214).
 */
export const POOL_MIN = 150;

/** Tiny deterministic RNG (mulberry32). */
export function makeRng(seed) {
  let a = seed >>> 0;
  return function rng() {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* --------------------------------------------------------------- helpers -- */

const plat = (x, w, y = GROUND_Y) => ({ x, w, y });
const token = (x, y) => ({ x, y, taken: false });

/** A run of collectibles in a gentle arc — reads as "follow the trail". */
function arc(x0, count, gap, yTop, yBase = GROUND_Y - 70) {
  const out = [];
  for (let i = 0; i < count; i++) {
    const p = count === 1 ? 0.5 : i / (count - 1);
    const y = yBase - Math.sin(p * Math.PI) * (yBase - yTop);
    out.push(token(x0 + i * gap, y));
  }
  return out;
}

/* -------------------------------------------------------------- chapters -- */

export const CHAPTERS = [
  {
    id: "backyard",
    n: 1,
    title: "Keepy Uppy",
    where: "The backyard",
    hero: "bluey",
    cameo: "bandit",
    theme: "backyard",
    horizon: GROUND_Y,      // the hills in the far layer meet the ground line
    tokenKind: "balloon",
    tokenName: "balloons",
    length: 5200,
    speed: 236,
    sky: ["#8FD3F4", "#CFF0FF"],
    ground: ["#7FBF6A", "#5E9E52"],
    story: [
      "Bluey's favourite toy, Floppy the bunny, has gone missing!",
      "But first — Dad says nobody leaves the backyard until the balloon touches the ground. That's the rule of Keepy Uppy.",
    ],
    joke: "Bandit has already 'accidentally' sat down twice. Classic Dad.",
    outro: "The balloon floats over the fence and away toward the creek. Floppy must be that way!",
    hasBalloon: true,
    build(rng) {
      const plats = [plat(-200, 1200)];
      const tokens = [];
      const obstacles = [];
      let x = 900;
      while (x < this.length - 300) {
        const w = 420 + Math.floor(rng() * 260);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 90, 4, 74, GROUND_Y - 210));
        if (rng() > 0.45) {
          obstacles.push({ x: x + w - 150, y: GROUND_Y - 52, w: 74, h: 52, kind: "bush" });
        }
        x += w + 96;    // small hops, very forgiving
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "creek",
    n: 2,
    title: "The Creek",
    where: "Down at the creek",
    hero: "bingo",
    cameo: "chilli",
    theme: "creek",
    horizon: GROUND_Y,      // same hills
    tokenKind: "sticker",
    tokenName: "stickers",
    length: 5600,
    speed: 244,
    sky: ["#9FE0D0", "#E7FBF3"],
    ground: ["#87B96A", "#5E8F4C"],
    water: "#5EC5D6",
    story: [
      "Down at the creek Bingo finds one of Floppy's ears stuck on a stick.",
      "The stepping stones wobble, the water is only ankle deep, and Mum says it's fine to get wet.",
    ],
    joke: "Mum brought the good towels. This was a mistake and she knows it.",
    outro: "A trail of bunny fluff leads up the hill, straight to the big hardware shop.",
    build(rng) {
      const plats = [plat(-200, 1100)];
      const tokens = [];
      const obstacles = [];
      let x = 860;
      while (x < this.length - 400) {
        // a little cluster of stepping stones at different heights
        const stones = 3 + Math.floor(rng() * 2);
        for (let i = 0; i < stones; i++) {
          const y = GROUND_Y - 30 - Math.round(rng() * 3) * 34;
          plats.push(plat(x, 150, y));
          tokens.push(token(x + 74, y - 58));
          x += 150 + 92;
        }
        // then a bank of solid ground to breathe on
        const w = 380 + Math.floor(rng() * 160);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 80, 3, 78, GROUND_Y - 190));
        if (rng() > 0.5) obstacles.push({ x: x + w - 130, y: GROUND_Y - 46, w: 66, h: 46, kind: "rock" });
        x += w + 100;
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "hammerbarn",
    n: 3,
    title: "Hammerbarn Dash",
    where: "The big hardware shop",
    hero: "bandit",
    cameo: "muffin",
    theme: "hammerbarn",
    horizon: GROUND_Y,      // the warehouse row stands on the shop floor
    tokenKind: "light",
    tokenName: "fairy lights",
    length: 6000,
    speed: 292,
    sky: ["#FFD9A0", "#FFF1DC"],
    ground: ["#C9C3BA", "#A8A199"],
    story: [
      "Everyone piles into Hammerbarn. Dad only came for one thing and has already forgotten what it was.",
      "Muffin has claimed the trolley. Grab the fairy lights and don't clip the pot plants!",
    ],
    joke: "Aisle 7: where dads go to look at wheelbarrows they will never buy.",
    outro: "Bingo spots a photo on the noticeboard — Floppy, at the beach, in someone's lost-property box.",
    build(rng) {
      const plats = [plat(-200, 1200)];
      const tokens = [];
      const obstacles = [];
      let x = 1000;
      while (x < this.length - 400) {
        const w = 500 + Math.floor(rng() * 200);
        plats.push(plat(x, w));
        const kinds = ["box", "plant", "box"];
        obstacles.push({ x: x + 150, y: GROUND_Y - 58, w: 64, h: 58, kind: kinds[Math.floor(rng() * kinds.length)] });
        if (w > 620) obstacles.push({ x: x + 430, y: GROUND_Y - 58, w: 64, h: 58, kind: "plant" });
        tokens.push(...arc(x + 120, 5, 66, GROUND_Y - 200));
        // a shelf to leap onto for the brave
        plats.push(plat(x + w - 260, 190, GROUND_Y - 150));
        tokens.push(...arc(x + w - 230, 3, 60, GROUND_Y - 250, GROUND_Y - 200));
        x += w + 110;
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "beach",
    n: 4,
    title: "Treasure Beach",
    where: "The beach",
    hero: "chilli",
    cameo: "lucky",
    theme: "beach",
    // The far layer here is sea, not land: the only surface behind the play
    // area is the shoreline at the top of it. Standing a palm on GROUND_Y put
    // it 120px under water, trunk and all.
    horizon: SEA_TOP,
    tokenKind: "shell",
    tokenName: "shells",
    length: 6200,
    speed: 268,
    sky: ["#7FD8F7", "#FFF3D6"],
    ground: ["#F3DCA8", "#DFC085"],
    water: "#3FB8D8",
    story: [
      "The lost-property box is right at the far end of the beach, past the rock pools.",
      "Mum draws a treasure map in the sand. X marks the spot — follow the shells!",
    ],
    joke: "There is sand in the sandwiches. There is always sand in the sandwiches.",
    outro: "There he is — Floppy! A bit sandy, a bit damp, and very pleased to be found.",
    /**
     * Long runs of sand, parted by rock pools you can see across.
     *
     * This used to be built like the creek: 430-650px slabs with a 440px gap
     * between them and the rock ledge floating in the middle of it. The near
     * water fills every gap to the bottom of the world, so at 440px — nearly
     * half the 960-wide view — the sand read as stepping stones in a lake and
     * the ledge as a raft (#214). A beach is sand with the sea on *one* side,
     * and that side is the far shoreline at SEA_TOP, which is already there.
     *
     * So the sand runs long, the gaps are pools rather than channels, and the
     * ledge is a rock standing on the sand at the end of a run instead of over
     * open water. Same jumps, same shell arcs; the gap is now the small part of
     * the picture.
     */
    build(rng) {
      const plats = [plat(-200, 1200)];
      const tokens = [];
      const obstacles = [];
      let x = 980;
      while (x < this.length - 400) {
        const w = 620 + Math.floor(rng() * 260);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 90, 4, 80, GROUND_Y - 205));
        if (rng() > 0.4) obstacles.push({ x: x + w - 420, y: GROUND_Y - 60, w: 82, h: 60, kind: "sandcastle" });
        // a rock ledge up on the dry sand, with the pool just past its far edge
        const ledge = x + w - 270;
        plats.push(plat(ledge, 210, GROUND_Y - 120));
        tokens.push(...arc(ledge + 40, 3, 62, GROUND_Y - 220, GROUND_Y - 170));
        x += w + POOL_MIN + Math.floor(rng() * 40);
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "sleepytime",
    n: 5,
    title: "Sleepytime",
    where: "A dream, on the way home",
    hero: "bingo",
    cameo: "nana_chris",
    theme: "sleepytime",
    // A dream sky. The far layer is clouds, so there is no surface at any y —
    // hence no standing scenery, rather than gum trees whose trunks ran down
    // past the floating platforms and ended in mid-air.
    horizon: null,
    tokenKind: "star",
    tokenName: "dream stars",
    length: 5400,
    speed: 220,
    gravityScale: 0.66,     // dreams are floaty
    sky: ["#2B2E68", "#6E5FA8"],
    ground: ["#4C4A8C", "#343266"],
    story: [
      "In the car on the way home Bingo falls asleep with Floppy under one arm.",
      "She dreams she is floating past the planets, all the way to the warm real sun — which is Mum.",
    ],
    joke: "Adults: yes, this is the one that makes you cry. Sorry.",
    outro: "Bingo wakes up in her own bed, in her own house, with everyone home. The end — for now.",
    finale: true,
    build(rng) {
      const plats = [plat(-200, 1100)];
      const tokens = [];
      const obstacles = [];
      let x = 840;
      while (x < this.length - 400) {
        const steps = 2 + Math.floor(rng() * 2);
        for (let i = 0; i < steps; i++) {
          const y = GROUND_Y - 40 - Math.round(rng() * 4) * 40;
          plats.push(plat(x, 230, y));
          tokens.push(...arc(x + 40, 3, 62, y - 150, y - 60));
          x += 230 + 110;
        }
        const w = 420 + Math.floor(rng() * 140);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 90, 4, 74, GROUND_Y - 230));
        if (rng() > 0.6) obstacles.push({ x: x + w - 150, y: GROUND_Y - 70, w: 90, h: 70, kind: "cloud" });
        x += w + 110;
      }
      plats.push(plat(x, 800));
      return { plats, tokens, obstacles };
    },
  },
];

/**
 * The standing scenery of the mid parallax layer: trees, the house, and the
 * pallets, trolleys and stepladders of the hardware shop.
 *
 * Every item stands on `ch.horizon`, and a chapter whose background has no
 * surface (`horizon: null`) has none. That is the whole rule, and it is here
 * rather than in the renderer so a test can read the list without drawing it.
 *
 * Why a horizon and not the ground the player runs on: this layer scrolls at
 * 0.6 of the camera, so an item has no fixed world x — it slides across the
 * platforms as you move, and asking "what is under it" gives a different answer
 * every frame. Distant scenery has to stand on something continuous, which is
 * what the far layer draws: hills in the first two chapters, the warehouse row
 * in the third, the sea's far edge in the fourth, and nothing at all in a dream.
 *
 * `x` is a coordinate in that layer, not in the world.
 */
export function sceneryFor(ch) {
  const y = ch.horizon;
  if (y === null || y === undefined) return [];
  const out = [];
  if (ch.id === "backyard") {
    out.push({ kind: "house", x: 260, y, scale: 1.1 });
    for (let i = 0; i < 12; i++) {
      out.push({ kind: "gum", x: 900 + i * 520, y, scale: 1 + (i % 2) * 0.3 });
    }
  } else if (ch.id === "creek") {
    for (let i = 0; i < 16; i++) {
      out.push({ kind: "tree", x: 200 + i * 340, y, scale: 0.9 + (i % 3) * 0.2,
                 leaf: "#6FAF63", trunk: "#8B6A4F" });
    }
  } else if (ch.id === "hammerbarn") {
    // The aisle's middle distance (#213): shelving in the far layer, the player
    // in the front one, and nothing in between. Sparse on purpose — this is
    // depth, not clutter — and cycled so no two neighbours are the same thing.
    const kinds = ["pallets", "trolleys", "pallets", "ladder"];
    for (let i = 0; i < 9; i++) {
      out.push({ kind: kinds[i % kinds.length], x: 320 + i * 430, y, scale: 1.05 });
    }
  } else if (ch.id === "beach") {
    // smaller than they were: they now stand at the shoreline, which is further
    // away than the sand the player is on
    for (let i = 0; i < 12; i++) {
      out.push({ kind: "tree", x: 300 + i * 520, y, scale: 0.8,
                 leaf: "#67B47F", trunk: "#A5764F" });
    }
  }
  return out;
}

/** Build a chapter's level data (deterministic per chapter index). */
export function buildLevel(index) {
  const ch = CHAPTERS[index];
  const rng = makeRng(1000 + index * 7919);
  const level = ch.build(rng);

  // one hidden "dollarbucks" per chapter, tucked up high for the grown-ups
  const high = level.tokens.filter((t) => t.y < GROUND_Y - 150);
  const pick = high.length ? high[Math.floor(high.length / 2)] : null;
  level.secret = {
    x: pick ? pick.x + 320 : ch.length * 0.6,
    y: (pick ? pick.y : GROUND_Y - 200) - 120,
    taken: false,
  };
  level.total = level.tokens.length;
  return level;
}

export const STAR_THRESHOLDS = [0.35, 0.65, 0.9]; // share of collectibles for 1/2/3 stars

export function starsFor(collected, total) {
  if (!total) return 1;
  const share = collected / total;
  return STAR_THRESHOLDS.reduce((s, t) => (share >= t ? s + 1 : s), 0) || 1;
}
