/*
 * abilities.js — who you can play as, and the one special move each of them has.
 *
 * Playing as somebody is a *choice made once* and then lived with for the whole
 * game, so the roster here is not "every character in the show" — it is every
 * character the game can draw running. `poses.json` has genuine side-on run
 * artwork for exactly these four. Muffin is deliberately absent: there is no
 * running render of her, and a hero drawn from pose art in one state and from
 * the rig in the next reads as two different drawings mid-play (#215, #220).
 * Adding her here would put that defect back, in the one place a player looks
 * at for the whole chapter. See the note above POSES in scripts/fetch_assets.py.
 *
 * The move itself is deliberately one button, no aiming and no combo: the
 * player this is for is three. `cooldown` is generous enough that mashing it
 * is not a strategy, and short enough that a small person who presses it the
 * instant it lights up is never waiting long.
 *
 * `apply` is not here — an ability is data, and the engine reads it. Where an
 * ability has to *change* the physics it does so through the fields below, so
 * a new move is a row in this table plus at most one branch in game.js.
 *
 * `color` is what the move looks like: the aura around whoever used it and the
 * motes it leaves behind. It is each character's own coat, lightened, because
 * the thing that is glowing is them.
 */

/** In roster order — this is the order they are offered in, and it is the family. */
export const PLAYABLE = ["bluey", "bingo", "bandit", "chilli"];

export const ABILITIES = {
  bluey: {
    name: "Zoomies",
    emoji: "💨",
    color: "#8FE3FF",
    blurb: "Runs super fast for a bit!",
    // For a grown-up reading over a shoulder: what it actually does.
    detail: "A burst of speed, with a sparkle trail.",
    duration: 2.4,
    cooldown: 5.5,
    speed: 1.75,
  },
  bingo: {
    name: "Floaty",
    emoji: "🎈",
    color: "#FFB3D0",
    blurb: "Floats down as light as a feather.",
    detail: "Falls slowly for a few seconds, even without holding the screen.",
    duration: 3.2,
    cooldown: 5.5,
    // The gravity she falls under while it is on. Lighter than FLOAT_GRAVITY,
    // and unlike it, it does not need a finger held down.
    fall: 0.22,
  },
  bandit: {
    name: "Big Bounce",
    emoji: "🦘",
    color: "#9DBBDD",
    blurb: "One enormous dad-sized jump.",
    detail: "An instant launch, much higher than a normal jump.",
    // Not a window like the others: it is one shove, and then it is over.
    // Kept non-zero so the aura has something to fade out over.
    duration: 0.7,
    cooldown: 5.5,
    launch: 1.42,
  },
  chilli: {
    name: "Sniff Out",
    emoji: "👃",
    color: "#FFC98A",
    blurb: "Sniffs out treats and pulls them in!",
    detail: "Nearby collectibles come to her on their own.",
    duration: 3.0,
    cooldown: 5.5,
    magnet: 300,   // px; anything unclaimed inside this comes to her
  },
};

/** The move for a character, or null for anyone who has none (every cameo). */
export function abilityFor(id) {
  return ABILITIES[id] || null;
}

/**
 * The hero a chapter is played as: whoever was chosen, else the chapter's own.
 *
 * One function rather than `save.hero || ch.hero` at each site, because there
 * are five of those sites (the engine, the story card, the HUD, the preload and
 * the results screen) and a chosen hero that four of them agree about is a bug
 * with a picture of the wrong dog in it.
 */
export function heroFor(chosen, chapter) {
  if (chosen && PLAYABLE.includes(chosen)) return chosen;
  return chapter ? chapter.hero : PLAYABLE[0];
}
