/*
 * chapters.js — the ten chapters of "Ana Bingo!".
 *
 * Each chapter is a small data description plus a `build(rng)` that lays out the
 * level: ground platforms, collectibles, soft obstacles and cameo spots. Levels
 * are generated from a fixed seed so every play (and every test run) sees the
 * same course — a three-year-old likes knowing what comes next.
 *
 * Two arcs of five (#351). Chapters 1-5 are the lost bunny, found at the end of
 * the fifth; 6-10 are the day the family spends collecting Nana's birthday
 * present. The second arc exists because "five more" cannot mean five more
 * reskins: chapters 1-5 are one mechanic (run, jump, collect) wearing five
 * paint jobs, and each of 6-10 brings **one idea no other chapter has**:
 *
 *   6  party   platforms that bounce you        `bounce` on a platform
 *   7  shops   a floor that carries you         `belt` on a platform
 *   8  rain    columns of wind that lift you    `updrafts` from `build`
 *   9  pool    swimming: hold to go *up*        `swim` on the chapter
 *  10  nanas   the whole cast lining the route  `crowd` on the chapter
 *
 * Every one of them gives rather than takes — the design rule is that nothing
 * can be lost, so variety may not arrive as a way to fail (game.js header).
 *
 * `cameoSays` is the one thing that chapter's cameo calls out as you run past
 * them (#364). It lives here rather than in lines.js because it is prose, not
 * an assembled line, and prose belongs in the file it belongs to — lines.js
 * says so itself. Keep the ten distinct: `render_voices.py` records one clip
 * per line and dedupes by text, so two chapters sharing a sentence would share
 * a reading, in whichever cameo's voice got there first.
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
 * The top of the cloud sea the dream chapter floats over — its far horizon.
 *
 * Exported for the same reason as SEA_TOP: the band `renderBackground` fills and
 * the line sleepytime's planets rest on are one number, and a second copy of it
 * is how the beach's palms ended up under the water (#210).
 *
 * Sits below the star field and well above the ground line, so it reads as
 * something a long way down rather than as a floor the player could land on.
 */
export const CLOUD_TOP = GROUND_Y - 88;

/**
 * How far above the world the sky is drawn out to, in world units (#326).
 *
 * The world is 540 tall and 16:9; a phone held upright is not, so `resize()`
 * hands the renderer a `viewTop` of about -573 and everything above y=0 was one
 * flat colour — the top 45% of the screen, measured, with no cloud, no star and
 * no gradient in it. A canvas gradient clamps to its end colour, so the sky's
 * own top colour was simply extended forever.
 *
 * Content up there is authored against *this* number rather than against
 * `viewTop`, so the picture at a given world y is the same on every screen and a
 * taller phone sees more of the same sky rather than a different one. Deeper
 * than the reference iPhone's band on purpose, and the cloud field tiles past
 * it, so a screen taller still does not run out.
 */
export const SKY_TOP = -600;

/**
 * The narrowest gap the beach parts its sand by — a rock pool, not a channel.
 *
 * Exported so the test that says "ch4 reads as a beach" can measure against the
 * chapter's own number rather than a copy of it. The old build's gaps were 440,
 * which is 46% of the 960-wide view: at that width the water is the picture and
 * the sand is stepping stones in it (#214).
 */
export const POOL_MIN = 150;

/**
 * The far edge of chapter 9's pool — the deck the loungers stand on (#351).
 *
 * The same deal as SEA_TOP and CLOUD_TOP, and exported for the same reason:
 * the band `renderBackground` fills and the line `sceneryFor` stands the
 * loungers on are one number, and every time that number has been written twice
 * the scenery has ended up under the water (#210).
 */
export const POOL_DECK = GROUND_Y - 150;

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
    cameoSays: "Keepy uppy! Don't let it touch the ground!",
    theme: "backyard",
    horizon: GROUND_Y,      // the hills in the far layer meet the ground line
    tokenKind: "balloon",
    tokenName: "balloons",
    length: 5200,
    speed: 236,
    sky: ["#8FD3F4", "#CFF0FF"],
    skyHigh: "#4FA8DC",
    ground: ["#7FBF6A", "#5E9E52"],
    deep: { fill: ["#6B4A2A", "#3A2412"], grit: "#8A6236", kind: "root" },
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
    cameoSays: "Go on, get your feet wet. That's what creeks are for.",
    theme: "creek",
    horizon: GROUND_Y,      // same hills
    tokenKind: "sticker",
    tokenName: "stickers",
    length: 5600,
    speed: 244,
    sky: ["#9FE0D0", "#E7FBF3"],
    skyHigh: "#59B8AC",
    ground: ["#87B96A", "#5E8F4C"],
    deep: { fill: ["#3F6B63", "#1E3B39"], grit: "#7FA79A", kind: "stone" },
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
    cameoSays: "This is my trolley and I'm the driver!",
    theme: "hammerbarn",
    horizon: GROUND_Y,      // the warehouse row stands on the shop floor
    tokenKind: "light",
    tokenName: "fairy lights",
    length: 6000,
    speed: 292,
    sky: ["#FFD9A0", "#FFF1DC"],
    skyHigh: "#EBB273",
    ground: ["#C9C3BA", "#A8A199"],
    deep: { fill: ["#8E877C", "#4E4941"], grit: "#F0C24A", kind: "mark" },
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
    cameoSays: "Follow the shells! The good rock pools are down that end.",
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
    skyHigh: "#3EA9DE",
    ground: ["#F3DCA8", "#DFC085"],
    deep: { fill: ["#C9A06A", "#7E5F38"], grit: "#FFF0D8", kind: "shell" },
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
    cameoSays: "Sweet dreams, possum. Say hello to the sun for me.",
    theme: "sleepytime",
    // The cloud sea, far below the dream (#228). This chapter had no surface at
    // any y and so no middle distance at all — the same hole hammerbarn had
    // before #213, and closed the same way round: paint the floor first, then
    // stand things on it. Not GROUND_Y: a horizon on the ground line is what put
    // #210's tree trunks down through the floating platforms and into mid-air.
    horizon: CLOUD_TOP,
    tokenKind: "star",
    tokenName: "dream stars",
    length: 5400,
    speed: 220,
    gravityScale: 0.66,     // dreams are floaty
    sky: ["#2B2E68", "#6E5FA8"],
    skyHigh: "#14163C",
    ground: ["#4C4A8C", "#343266"],
    deep: { fill: ["#2E2C5C", "#141230"], grit: "#FFF7D6", kind: "spark" },
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

  /* ------------------------------------------- arc two: Nana's birthday -- */

  {
    id: "party",
    n: 6,
    title: "Jumping Castle",
    where: "The park, Saturday morning",
    hero: "bluey",
    cameo: "coco",
    cameoSays: "Bounce with me! I can nearly touch the top!",
    theme: "party",
    horizon: GROUND_Y,
    hills: "#A6D98C",     // the park backs onto the same hills as the backyard
    tokenKind: "cupcake",
    tokenName: "cupcakes",
    length: 5600,
    speed: 240,
    sky: ["#9BE0F5", "#FFE7F1"],
    skyHigh: "#54B4DC",
    ground: ["#82C46C", "#5FA054"],
    deep: { fill: ["#6B4A2A", "#3A2412"], grit: "#8A6236", kind: "root" },
    story: [
      "Floppy is home! And there is a new job: it is Nana's birthday, and nobody can think of a present.",
      "Bingo works it out — the present is a whole day of Nana's favourite games. First stop, the jumping castle: bounce high and get the cupcakes for her cake!",
    ],
    joke: "Dad has been told he is too big for the jumping castle. Dad disagrees.",
    outro: "Cupcakes: got. Next on Nana's list — the escalators at the shops. She loves those.",
    /**
     * Ground, then a castle that throws you up: `bounce` (#351).
     *
     * The one thing this chapter does that no other does. A platform with a
     * `bounce` launches whoever lands on it, no button pressed, at that
     * multiple of a normal jump — 1.62 clears about 350px against a jump's
     * 135, which is what puts the high cupcakes in reach. It only ever gives:
     * landing on it is a bigger jump, never a fall.
     */
    build(rng) {
      const plats = [plat(-200, 1200)];
      const tokens = [];
      const obstacles = [];
      let x = 1000;
      while (x < this.length - 500) {
        const w = 640 + Math.floor(rng() * 200);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 90, 4, 72, GROUND_Y - 200));
        if (rng() > 0.5) {
          obstacles.push({ x: x + 330, y: GROUND_Y - 52, w: 74, h: 52, kind: "bush" });
        }
        // The castle itself, standing on the far end of the same grass rather
        // than over a gap of its own: it is 26px up, and platforms are landed on
        // from above only, so a castle with air under it would be a 300px hole
        // in the ground with a lid on it.
        const bx = x + w - 320;
        plats.push({ ...plat(bx, 300, GROUND_Y - 26), bounce: 1.62 });
        tokens.push(...arc(bx + 50, 5, 52, GROUND_Y - 330, GROUND_Y - 190));
        x += w + 96;    // the same forgiving hop as chapter one
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "shops",
    n: 7,
    title: "Escalator Shops",
    where: "The shopping centre",
    hero: "bandit",
    cameo: "stripe",
    cameoSays: "Up the escalator, down the travelator. Best ride in the shops.",
    theme: "shops",
    horizon: GROUND_Y,
    tokenKind: "ticket",
    tokenName: "raffle tickets",
    length: 6000,
    speed: 250,
    sky: ["#FFE6C7", "#FFF6EA"],
    skyHigh: "#E9BE8C",
    ground: ["#D6CFE0", "#B3AAC4"],
    deep: { fill: ["#8E877C", "#4E4941"], grit: "#F0C24A", kind: "mark" },
    story: [
      "Nana's second favourite thing in the world is the shops, mostly for the moving floor.",
      "The travelators run one way, the escalators go up, and the raffle tickets for her lucky dip are all over both. Ride them!",
    ],
    joke: "Bandit has been up and down the travelator four times and bought nothing.",
    outro: "Tickets: got. And outside, it has started absolutely bucketing down.",
    /**
     * A floor that carries you: `belt` (#351).
     *
     * The one thing this chapter does that no other does. A platform with a
     * `belt` adds that many px/s while you stand on it, so the ground moves
     * under a dog who is not moving. Every direction is survivable — the
     * backwards ones are 85 against a run of 250, so the worst a travelator
     * going the wrong way can do is make you strut on the spot, which is the
     * joke rather than a punishment.
     */
    build(rng) {
      const plats = [plat(-200, 1200)];
      const tokens = [];
      const obstacles = [];
      let x = 1000;
      let i = 0;
      while (x < this.length - 600) {
        const w = 520 + Math.floor(rng() * 180);
        const against = i % 3 === 2;
        plats.push({ ...plat(x, w), belt: against ? -85 : 170 });
        tokens.push(...arc(x + 80, 5, 70, GROUND_Y - 190));
        x += w;
        // a still stretch of shop floor to stand on, with an escalator running
        // up over it to the mezzanine
        const s = 380 + Math.floor(rng() * 140);
        plats.push(plat(x, s));
        if (rng() > 0.45) {
          obstacles.push({ x: x + s - 150, y: GROUND_Y - 58, w: 64, h: 58, kind: "box" });
        }
        plats.push({ ...plat(x + 70, 250, GROUND_Y - 150), belt: 150 });
        tokens.push(...arc(x + 100, 3, 62, GROUND_Y - 260, GROUND_Y - 200));
        x += s + 130;
        i++;
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "rain",
    n: 8,
    title: "Puddle Walk",
    where: "The street home, in the rain",
    hero: "bingo",
    cameo: "judo",
    cameoSays: "The reddest leaves are down by the big puddle!",
    theme: "rain",
    horizon: GROUND_Y,
    hills: "#6E9A72",     // the same hills, greyed out behind the rain
    tokenKind: "leaf",
    tokenName: "leaves",
    length: 5800,
    speed: 238,
    sky: ["#9FB6C6", "#DDE7EC"],
    skyHigh: "#6F8B9E",
    ground: ["#6F9E63", "#4F7A4A"],
    deep: { fill: ["#3F5A50", "#1E2E2A"], grit: "#8FB0A0", kind: "stone" },
    water: "#7FA9BD",
    rain: true,
    // it is pouring: the sun is behind all that, not blazing through it (#355)
    overcast: true,
    story: [
      "It is pouring. Mum says a bit of rain never hurt anybody and hands out the umbrellas.",
      "Hold on tight — the gusts come up the street in swirls of leaves, and Nana wants the reddest ones for her table.",
    ],
    joke: "There is one puddle on this street so deep it has been given a name.",
    outro: "Leaves: got, and everyone is soaked through. Uncle Stripe says one word: pool.",
    /**
     * Wind you can ride: `updrafts` (#351).
     *
     * The one thing this chapter does that no other does, and the first upward
     * force in the game. A gust is a rectangle over a puddle; inside it you are
     * pushed up instead of down, so the puddle you would have splashed into is
     * the thing that carries you over. Wider than the gap at both edges, so the
     * lift has already caught you by the time you run off the kerb.
     */
    build(rng) {
      const plats = [plat(-200, 1200)];
      const tokens = [];
      const obstacles = [];
      const updrafts = [];
      // Flush against the starting pad, which ends at 1000: a 50px lip between
      // the two would be a pit with no gust over it, and this chapter's gaps are
      // all meant to be the wind's.
      let x = 1000;
      while (x < this.length - 600) {
        const w = 420 + Math.floor(rng() * 180);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 80, 4, 74, GROUND_Y - 185));
        if (rng() > 0.5) {
          obstacles.push({ x: x + w - 150, y: GROUND_Y - 46, w: 66, h: 46, kind: "rock" });
        }
        x += w;
        const gap = 210;
        updrafts.push({ x: x - 30, y: GROUND_Y - 330, w: gap + 60, h: 340 });
        tokens.push(...arc(x + 30, 4, 46, GROUND_Y - 300, GROUND_Y - 110));
        x += gap;
      }
      plats.push(plat(x, 700));
      return { plats, tokens, obstacles, updrafts };
    },
  },

  {
    id: "pool",
    n: 9,
    title: "The Pool",
    where: "Uncle Stripe's pool",
    hero: "chilli",
    cameo: "socks",
    cameoSays: "Bubble! Bubble! More bubble!",
    theme: "pool",
    horizon: POOL_DECK,
    tokenKind: "bubble",
    tokenName: "bubbles",
    length: 5800,
    speed: 232,
    swim: true,
    gravityScale: 0.3,
    sky: ["#7FD8F7", "#CFF3FF"],
    skyHigh: "#3EA9DE",
    ground: ["#8FD6E8", "#5FAFC8"],
    deep: { fill: ["#2E7E9B", "#11455C"], grit: "#BFEBFA", kind: "shell" },
    water: "#4FC3E0",
    story: [
      "Uncle Stripe's pool, and everybody is already in.",
      "Nana's favourite trick is the bubble ring. Hold on to swim up, let go to sink down, and catch every bubble you can!",
    ],
    joke: "Somebody has done a bombie. It was, obviously, Bandit.",
    outro: "Bubbles: got. Towels, car, seatbelts — Nana's for tea, and we are only a bit late.",
    /**
     * Swimming: `swim` (#351).
     *
     * The one thing this chapter does that no other does. Chapter 5 is floaty —
     * you fall slowly — and this one is buoyant: holding the screen pushes you
     * *up*, so for the first time the player can climb under their own steam,
     * and the course is built vertically rather than as a line of gaps.
     *
     * The pool floor runs the whole length: nobody falls out of a pool, and a
     * chapter where you can go up on purpose has no need of a pit to make it
     * interesting.
     */
    build(rng) {
      const plats = [plat(-200, this.length + 900)];
      const tokens = [];
      const obstacles = [];
      let x = 1000;
      while (x < this.length - 500) {
        // a ring of bubbles going up, deepest first, then one to come back down
        const rise = 3 + Math.floor(rng() * 2);
        for (let i = 0; i < rise; i++) {
          const y = GROUND_Y - 70 - i * 96;
          tokens.push(...arc(x + i * 150, 3, 58, y - 30, y));
        }
        // a tiled step to stand on part way along, so there is somewhere to land
        plats.push(plat(x + 120, 260, GROUND_Y - 120));
        if (rng() > 0.5) {
          obstacles.push({ x: x + 620, y: GROUND_Y - 54, w: 78, h: 54, kind: "float" });
        }
        x += 560 + Math.floor(rng() * 200);
      }
      return { plats, tokens, obstacles };
    },
  },

  {
    id: "nanas",
    n: 10,
    title: "Nana's Birthday",
    where: "Nana's backyard, at dusk",
    hero: "bluey",
    cameo: "nana_chris",
    cameoSays: "There you are, love! Bring those candles up to the cake.",
    theme: "nanas",
    horizon: GROUND_Y,
    hills: "#46654B",     // the same hills again, gone dark with the evening
    tokenKind: "candle",
    tokenName: "candles",
    length: 6200,
    speed: 244,
    sky: ["#F7A98C", "#4C3F72"],
    skyHigh: "#2C2551",
    // the sun is going down behind the range, not blazing in the top corner: the
    // other half of the ask #355 answered for the rain (#363)
    dusk: true,
    ground: ["#5E8A55", "#3E6440"],
    deep: { fill: ["#4A3A22", "#241A10"], grit: "#C79A55", kind: "root" },
    crowd: true,
    story: [
      "Nana's backyard at dusk, the fairy lights on, and every single person you have ever played with is here.",
      "Carry the candles all the way up to the cake — and say g'day to everyone on the way past. Ready? Go!",
    ],
    joke: "Twenty-five guests, one cake. Nana has counted the candles twice already.",
    outro: "Happy birthday, Nana. Best day ever — and that is the whole ten. 🎂",
    finale: true,
    /**
     * Everybody (#351).
     *
     * The one thing this chapter does that no other does: `crowd` puts the
     * entire cast along the route, cheering, instead of the single cameo every
     * other chapter has. The level itself is deliberately the gentlest in the
     * game — a curtain call is for looking at, so the jumps are small and the
     * candles are strung at head height along the path.
     */
    build(rng) {
      const plats = [plat(-200, 1300)];
      const tokens = [];
      const obstacles = [];
      let x = 1100;
      while (x < this.length - 400) {
        const w = 520 + Math.floor(rng() * 200);
        plats.push(plat(x, w));
        tokens.push(...arc(x + 90, 5, 76, GROUND_Y - 200));
        // a low table of a ledge, for the ones who want to jump on the furniture
        plats.push(plat(x + w - 300, 220, GROUND_Y - 130));
        tokens.push(...arc(x + w - 270, 3, 62, GROUND_Y - 240, GROUND_Y - 180));
        if (rng() > 0.6) {
          obstacles.push({ x: x + 220, y: GROUND_Y - 52, w: 74, h: 52, kind: "bush" });
        }
        x += w + 96;
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
 * in the third, the sea's far edge in the fourth, and the cloud sea in the fifth.
 *
 * `x` is a coordinate in that layer, not in the world.
 */
export function sceneryFor(ch) {
  const y = ch.horizon;
  if (y === null || y === undefined) return [];
  const out = [];
  if (ch.id === "backyard") {
    out.push({ kind: "house", x: 260, y, scale: 1.1 });
    // The Hills hoist, which is the one thing in this game's middle distances
    // that only stands here (#351): a house and a gum tree are also Nana's, and
    // a chapter dressed entirely in another chapter's props is #229's defect.
    out.push({ kind: "hoist", x: 600, y, scale: 1 });
    for (let i = 0; i < 12; i++) {
      out.push({ kind: "gum", x: 900 + i * 520, y, scale: 1 + (i % 2) * 0.3 });
    }
  } else if (ch.id === "creek") {
    // Mossy boulders along the bank, every fourth prop: the round oaks are the
    // wet street's trees too, so without them the creek is dressed in nothing
    // of its own.
    for (let i = 0; i < 16; i++) {
      out.push(i % 4 === 3
        ? { kind: "rocks", x: 200 + i * 340, y, scale: 1 + (i % 3) * 0.15 }
        : { kind: "tree", x: 200 + i * 340, y, scale: 0.9 + (i % 3) * 0.2,
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
    // Palms, at last (#229). Everything here called them palms — this docstring,
    // SEA_TOP's, the placement test's — while the chapter pushed `kind: "tree"`
    // and got the creek's round oak with a greener leaf. The one chapter dressed
    // in another chapter's biome.
    //
    // Smaller than the creek's trees: they stand at the shoreline, which is
    // further away than the sand the player is on. Every third one is a striped
    // brolly — the pool's deck has palms in pots now, so the palms alone are no
    // longer only the beach's (#351).
    for (let i = 0; i < 12; i++) {
      out.push({ kind: i % 3 === 1 ? "brolly" : "palm", x: 300 + i * 520, y, scale: 0.8 });
    }
  } else if (ch.id === "sleepytime") {
    // What a dream can have standing in it (#228): the planets the story says
    // she floats past, resting in the cloud sea, and towers of cloud between
    // them. Soft and round on purpose — nothing here should have an edge the
    // gum trees would have, or the middle distance stops being a dream.
    const kinds = ["planet", "tower", "tower", "planet", "tower"];
    for (let i = 0; i < 9; i++) {
      out.push({ kind: kinds[i % kinds.length], x: 300 + i * 400, y,
                 scale: 0.9 + (i % 3) * 0.15 });
    }
  } else if (ch.id === "party") {
    // The park: the fete's marquees, and the gum trees it is held under. The
    // castle the player bounces on is in the *play* layer — what stands here is
    // the rest of the party, a long way off.
    out.push({ kind: "marquee", x: 340, y, scale: 1.05 });
    for (let i = 0; i < 10; i++) {
      out.push({ kind: i % 3 === 1 ? "marquee" : "gum", x: 900 + i * 470, y,
                 scale: 1 + (i % 2) * 0.25 });
    }
  } else if (ch.id === "shops") {
    // The row of shopfronts down the far side of the centre, with the odd
    // abandoned trolley in front of it — hammerbarn's trick (#213) in a
    // different shop: the floor is painted first, then things stand on it.
    const kinds = ["shopfront", "shopfront", "trolleys"];
    for (let i = 0; i < 11; i++) {
      out.push({ kind: kinds[i % kinds.length], x: 300 + i * 400, y, scale: 1 });
    }
  } else if (ch.id === "rain") {
    // The street: houses, the wet trees between them, and a lit street lamp on
    // every fourth corner — the lamp is this chapter's own, since the houses are
    // home's and the trees are the creek's. Autumn colours, because the leaves
    // the gusts are full of have to have come from somewhere.
    for (let i = 0; i < 14; i++) {
      if (i % 4 === 0) {
        out.push({ kind: "house", x: 260 + i * 380, y, scale: 0.95 });
      } else if (i % 4 === 2) {
        out.push({ kind: "lamp", x: 260 + i * 380, y, scale: 1 });
      } else {
        out.push({ kind: "tree", x: 260 + i * 380, y, scale: 0.85 + (i % 3) * 0.2,
                   leaf: i % 2 ? "#B4693C" : "#8C9E52", trunk: "#6B5140" });
      }
    }
  } else if (ch.id === "pool") {
    // The far deck, above the water: loungers and the palms in their pots. The
    // deck itself is the floor `renderBackground` paints at POOL_DECK — without
    // it these would be sunbathing in mid-water (#210, #213, #228, all three).
    const kinds = ["lounger", "palm", "lounger"];
    for (let i = 0; i < 10; i++) {
      out.push({ kind: kinds[i % kinds.length], x: 300 + i * 430, y, scale: 0.8 });
    }
  } else if (ch.id === "nanas") {
    // Nana's, at dusk: her house, her gum trees, and the bunting strung between
    // them for the party.
    out.push({ kind: "house", x: 300, y, scale: 1.15 });
    const kinds = ["bunting", "gum", "bunting", "gum", "gum"];
    for (let i = 0; i < 12; i++) {
      const kind = kinds[i % kinds.length];
      // The gums vary; the bunting does not. Its poles stand 70px either side of
      // its middle — further from it than any other prop's feet are from theirs —
      // and scaling that up puts them outside the band a prop is measured in.
      out.push({ kind, x: 900 + i * 440, y,
                 scale: kind === "bunting" ? 1 : 1 + (i % 2) * 0.25 });
    }
  }
  return out;
}

/** The x period the high sky repeats over — it has to tile, the world does not. */
export const SKY_TILE = 1600;

/** The band the high sky is authored in: above the world, clear of the old art. */
export const SKY_BAND = [SKY_TOP + 40, -90];

/**
 * The menu's sky, in the shape a chapter's is (#329).
 *
 * The idle screen behind the menu card is not a chapter — there is no level
 * loaded yet — but it is drawn by the same renderer and needs the same sky, so
 * it gets the same fields rather than a second implementation: `sky` is the
 * gradient it already used, `skyHigh` is what that gradient becomes on the way
 * up, and `n` seeds `highSky` to a field of its own, so the menu is not a
 * photograph of chapter one.
 *
 * `deep` is the same deal at the other end (#328): the menu's flat green apron
 * had the same hole the chapters' did, and it gets the same pass rather than a
 * copy of it — which is the whole lesson of #329.
 */
export const IDLE_SKY = {
  id: "idle",
  n: 0,
  sky: ["#8FD3F4", "#E8F7FF"],
  skyHigh: "#4FA8DC",
  deep: { fill: ["#6B4A2A", "#3A2412"], grit: "#8A6236", kind: "root" },
};

/**
 * What is up in the sky above the world, for a phone held upright (#326).
 *
 * The old sky was authored entirely inside 0..540 — clouds at y 70..162, a sun at
 * 96, stars stopping at the cloud sea. Upright, the renderer is handed a `viewTop`
 * near -573, and the topmost drawn thing sat 390px down an 844px screen: the top
 * 46% was one flat colour. This fills that band, and only that band — the lowest
 * item is at -90, so a laptop (which sees to about -30) is not shown anything new.
 *
 * Deterministic per chapter, and a function of world position only, so the same
 * chapter draws the same sky on every screen: a taller phone sees further up the
 * same picture rather than a different one.
 *
 * Placed rather than random: x is one item per even slot across the tile, and the
 * heights are a permutation (step 5, coprime with 14) of evenly spaced bands. A
 * plain `rng()` for both put the whole top third of some tiles empty, which is
 * the bug this is fixing, one screen along.
 */
export function highSky(ch) {
  const rng = makeRng(ch.n * 7919 + 13);
  const [top, bot] = SKY_BAND;
  const out = [];
  const night = ch.id === "sleepytime";
  const n = night ? 70 : 14;
  for (let i = 0; i < n; i++) {
    const slot = night ? (i * 29) % n : (i * 5) % n;
    const x = ((i + 0.15 + rng() * 0.7) / n) * SKY_TILE;
    const y = top + ((slot + rng() * 0.9) / n) * (bot - top);
    const depth = (y - top) / (bot - top);   // 0 at the top of the band, 1 at the bottom
    if (night) {
      out.push({ kind: "star", x, y, scale: 0.7 + depth * 0.9, alpha: 0.45 + depth * 0.45 });
    } else {
      // Higher is further, so it is smaller and thinner — the band has to read as
      // depth rather than as a second row of the clouds already down at y=70.
      out.push({ kind: "cloud", x, y, scale: 0.45 + depth * 0.6, alpha: 0.3 + depth * 0.35 });
    }
  }
  if (!night) {
    // Three birds, because a sky of nothing but haze is still a gradient with
    // lumps in it. High, small, and not on the clouds' rhythm.
    for (let i = 0; i < 3; i++) {
      out.push({ kind: "bird", x: (i + 0.4) / 3 * SKY_TILE, y: top + 30 + i * 96, scale: 0.8 + (i % 2) * 0.5, alpha: 0.5 });
    }
  }
  return out;
}

/** The x period the deep ground repeats over, as `SKY_TILE` is for the sky. */
export const DEEP_TILE = 1200;

/**
 * How far below the world the ground is drawn out to, in world units (#328).
 *
 * The mirror of `SKY_TOP`. Upright, `viewBot` is about 813 on the reference
 * phone and 869 on a narrower, taller one; the fill and the band are authored
 * past both so a taller screen sees more of the same ground rather than the end
 * of it.
 */
export const DEEP_BOT = 1000;

/**
 * The band the deep ground is authored in: below the world, clear of the old art.
 *
 * The top is the line no wide screen reaches — a 1280x800 laptop sees to y=570,
 * a 16:9 window to 540 — so everything here is invisible on the screens whose
 * picture must not change, exactly as `SKY_BAND` is above one. The bottom is
 * past `viewBot` on every phone measured, so the fill never runs out under the
 * player's feet.
 */
export const DEEP_BAND = [580, 940];

/**
 * How far below the band's top edge the first thing in it may be anchored.
 *
 * Every piece of this art is drawn *around* its anchor — a stone's ellipse is 10
 * up and 10 down, a root reaches 26 back — so an item anchored on the line draws
 * across it, and a laptop is shown 13 pixels of a fix meant for a phone. Measured
 * against the widest of them at the largest scale it is drawn at.
 */
export const DEEP_LIP = 44;

/**
 * What is under the floor, for a phone held upright (#328).
 *
 * The other end of #326, and the same shape of bug: the ground is one fill from
 * `GROUND_Y` to `viewBot` and every ground detail is authored at or just above
 * y=452, because on a 16:9 screen that is all there ever was. Measured on the
 * live site at 390x844: the lowest drawn thing was 628-630px down an 844px
 * phone on three of the five chapters, i.e. a quarter of the screen was one flat
 * colour with nothing in it.
 *
 * Deterministic per chapter and a function of world position only, so the two
 * screens agree about what is at a given depth. Themed per chapter rather than
 * one shared texture, because this is *foreground*: something generic down there
 * reads as a second floor the player should be able to land on, so the backyard
 * gets soil and roots, hammerbarn a painted concrete floor, the beach wet sand
 * and shells.
 */
export function deepGround(ch) {
  const rng = makeRng(ch.n * 6151 + 29);
  const [top, bot] = DEEP_BAND;
  const kind = ch.deep.kind;
  const items = [];
  const n = 26;
  for (let i = 0; i < n; i++) {
    // Placed, not rolled: one item per slot, the slots walked in a step coprime
    // with n. Two plain rng() draws left whole depths of some tiles empty, which
    // is the bug this is fixing one screen along (#326's own note).
    const slot = (i * 7) % n;
    const x = ((i + 0.1 + rng() * 0.8) / n) * DEEP_TILE;
    const y = top + DEEP_LIP + ((slot + rng() * 0.9) / n) * (bot - top - DEEP_LIP);
    const depth = (y - top) / (bot - top);   // 0 at the floor, 1 at the deepest
    // Deeper is further into the dark, so it is fainter — the band has to read
    // as depth rather than as a second layer of the ground detail up at y=452.
    items.push({ kind, x, y, scale: 0.55 + rng() * 0.8 - depth * 0.2,
                 alpha: 0.85 - depth * 0.45 });
  }

  // And things that run *down* the band, which is what stops it reading as the
  // flat colour it replaces. Measured: scattered items alone came to 1-3 colour
  // changes per row of the band, against 3.1-3.5 in the sky #326 added at the
  // other end and 8-14 in the world's own picture. A strand crosses every row it
  // spans, so a handful of them is what carries the whole band.
  const strands = [];
  const m = 7;
  for (let i = 0; i < m; i++) {
    strands.push({
      kind,
      x: ((i + 0.15 + rng() * 0.7) / m) * DEEP_TILE,
      // Started clear of the band's top edge, not on it: a strand is stroked
      // with a round cap up to 5 wide, so one starting at `top` puts a couple of
      // pixels *above* the line this whole band is supposed to stay below.
      top: top + 12 + rng() * 18,
      len: (bot - top) * (0.55 + rng() * 0.45) + 50,
      // Hammerbarn's floor is poured concrete: its joints are sawn straight, and
      // a wobbling one would read as a crack in a floor that is meant to be flat.
      sway: kind === "mark" ? 0 : 14 + rng() * 24,
      phase: rng() * 6.283,
      w: 2 + rng() * 3,
      alpha: 0.55 + rng() * 0.35,
    });
  }
  return { items, strands };
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

/**
 * The most stars one chapter can hand out (#351).
 *
 * `starsFor` counts the thresholds it clears, so the ceiling *is* the length of
 * that ladder — and the stats table used to print the total out of a hard-coded
 * 15, which is 5 chapters x 3 stars written as one number that knew about
 * neither. With ten chapters that read "30 stars available" as 15. Exported so
 * the table can multiply the two things it actually depends on.
 */
export const STARS_PER_CHAPTER = STAR_THRESHOLDS.length;

export function starsFor(collected, total) {
  if (!total) return 1;
  const share = collected / total;
  return STAR_THRESHOLDS.reduce((s, t) => (share >= t ? s + 1 : s), 0) || 1;
}
