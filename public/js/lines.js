/**
 * The lines a character says in their own voice, in one place (#361).
 *
 * Everything else the game says out loud lives in the file it belongs to —
 * a chapter's story is in `chapters.js`, a bio is in `characters.json` — and
 * `scripts/render_voices.py` reads those files to know what to record. These
 * two are different: they are *assembled* from a name at the moment they are
 * said, so there is no line of prose anywhere to read.
 *
 * Written down once, as functions, and called by both sides: the game calls
 * them to say the line, and the render script calls them through node to know
 * which lines to record. A copy of the wording in the script instead would be
 * a second author for the same sentence, and the way that fails is silent — a
 * recording of a sentence the game never says, with the game falling back to
 * the robot voice for the one it does (that is exactly what happened to the
 * character picker's heading, #357).
 */

/** What a caught friend says as they join the run (#306). */
export function greeting(heroName, name) {
  return `Hi ${heroName}, I'm ${name}!`;
}

/** How a character introduces themselves when their bio is opened. */
export function hello(name) {
  return `G'day! I'm ${name}.`;
}
