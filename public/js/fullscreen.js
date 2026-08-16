/*
 * fullscreen.js — the Fullscreen API, and the browsers that only half have it.
 *
 * Three separate things can be true, and the game has to tell them apart:
 *
 *   - the API is there and allowed        → offer the control
 *   - the API is there and *forbidden*    → `fullscreenEnabled` is false (an
 *                                           iframe without `allowfullscreen`,
 *                                           which is how a preview pane embeds
 *                                           this game)
 *   - the API is not there at all         → iPhone Safari, which is the phone
 *                                           this game is actually played on: it
 *                                           has `webkitEnterFullscreen` on
 *                                           <video> and nothing at all for an
 *                                           element (iPad does have it)
 *
 * The last two both mean "do not show a control", and a control that is shown
 * and does nothing is worse than no control: a three-year-old presses it twice
 * as hard. So `fullscreenSupported` asks both questions — is it allowed *here*,
 * and can this element be asked — and every caller is guarded on it.
 *
 * Nothing in here reads or writes the game's state. It is the browser's half of
 * full-screen only; the preference, the buttons and the redraw live in main.js.
 */

// Standard first in every list: a browser that has both (Chrome) must answer the
// same name to all of them, or it would listen for one event and read the other's
// element and never see itself change.
const REQUEST = ["requestFullscreen", "webkitRequestFullscreen"];
const EXIT = ["exitFullscreen", "webkitExitFullscreen"];
const ELEMENT = ["fullscreenElement", "webkitFullscreenElement"];
const ENABLED = ["fullscreenEnabled", "webkitFullscreenEnabled"];

function method(obj, names) {
  if (!obj) return null;
  const name = names.find((n) => typeof obj[n] === "function");
  return name ? obj[name].bind(obj) : null;
}

// `!== undefined` and not truthiness: `fullscreenEnabled: false` is an *answer*
// — full-screen is forbidden here — and falling through to the prefixed name
// would let a browser that says no be overruled by its own older spelling.
function property(obj, names) {
  if (!obj) return undefined;
  const name = names.find((n) => obj[n] !== undefined);
  return name === undefined ? undefined : obj[name];
}

/** Can `node` be made full-screen on this page, right now? */
export function fullscreenSupported(node, doc = document) {
  return !!property(doc, ENABLED) && !!method(node, REQUEST);
}

/** Is anything full-screen? (Not "did we ask for it" — see main.js.) */
export function fullscreenOn(doc = document) {
  return !!property(doc, ELEMENT);
}

/** What is full-screen, if anything — the element, so a caller can check which. */
export function fullscreenElement(doc = document) {
  return property(doc, ELEMENT) || null;
}

/**
 * Ask for full-screen, and resolve to whether it happened.
 *
 * The old prefixed form returns `undefined` rather than a promise, and the
 * standard one *rejects* when it was not called inside a user gesture — which
 * is the ordinary case on a page that has just been reloaded, not an error to
 * throw at the player. Both are answered the same way: `false`, and whatever
 * asked can decide what to say.
 */
export function enterFullscreen(node) {
  const go = method(node, REQUEST);
  if (!go) return Promise.resolve(false);
  try {
    return Promise.resolve(go()).then(() => true, () => false);
  } catch (e) {
    return Promise.resolve(false);
  }
}

/** Leave full-screen, if we are in it. */
export function leaveFullscreen(doc = document) {
  const out = method(doc, EXIT);
  if (!out || !fullscreenOn(doc)) return Promise.resolve(false);
  try {
    return Promise.resolve(out()).then(() => true, () => false);
  } catch (e) {
    return Promise.resolve(false);
  }
}

/**
 * The one event name this document actually fires.
 *
 * Chrome fires *both* `fullscreenchange` and `webkitfullscreenchange`, so
 * listening for the pair would run the handler twice per transition — and the
 * handler writes the save and re-lays-out the world. One name, chosen by the
 * same question `fullscreenSupported` asks.
 */
export function changeEvent(doc = document) {
  return doc && doc.fullscreenEnabled !== undefined
    ? "fullscreenchange" : "webkitfullscreenchange";
}

/** Call `fn` whenever full-screen is entered or left, by anyone. */
export function onFullscreenChange(fn, doc = document) {
  const name = changeEvent(doc);
  doc.addEventListener(name, fn);
  return () => doc.removeEventListener(name, fn);
}
