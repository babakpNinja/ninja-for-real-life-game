/*
 * homescreen.js — what to say on the phone that has no Fullscreen API.
 *
 * `fullscreen.js` deliberately renders no control where the browser cannot
 * grant one, and the browser that cannot is iPhone Safari — the phone this game
 * is actually played on. So the person who asked for full screen got nothing on
 * their own device (#354).
 *
 * iOS's own answer is a home-screen bookmark: launched from the home screen the
 * page runs standalone (no address bar, no tabs), which is full screen in every
 * way that matters to a three-year-old. That is not something a button can do —
 * only the player can, through the Share sheet — so this is a *hint*, one line
 * of fine print, never a control that would do nothing when pressed (#310).
 *
 * Four things have to be true before it is worth saying, and they are asked in
 * this order because each one is cheaper and more decisive than the next:
 *
 *   - there is no full-screen control here  (otherwise say nothing: there is a
 *                                            real way in and it is better)
 *   - this is not already a standalone app  (`navigator.standalone`, iOS's own
 *                                            flag, set when launched from the
 *                                            home screen)
 *   - the page is not displayed as an app   (`display-mode`, which is how every
 *                                            other browser answers the same
 *                                            question, and which iOS 16.4+ also
 *                                            answers)
 *   - the browser looks like an iPhone      (the instruction names Safari's
 *                                            Share sheet, and telling a locked
 *                                            down desktop iframe to use it
 *                                            would be nonsense)
 *
 * Nothing here reads the game's state, and nothing here draws: main.js decides
 * where the line goes.
 */

/** The exact words, in one place, so the test and the menu cannot drift apart. */
export const HOME_SCREEN_HINT = "Full screen: Share → Add to Home Screen";

/**
 * Is this a phone whose only route to full screen is the home screen?
 *
 * `nav` and `win` are arguments rather than globals for the same reason
 * `fullscreenSupported` takes its node: every case below is a browser this
 * suite cannot actually run, so they have to be describable.
 */
export function homeScreenHintWanted(fullscreenHere, nav = navigator, win = window) {
  if (fullscreenHere) return false;
  if (nav && nav.standalone === true) return false;
  if (displayedAsApp(win)) return false;
  return isIPhone(nav);
}

/** Launched as an app rather than opened in a tab, by anyone's reckoning. */
function displayedAsApp(win) {
  if (!win || typeof win.matchMedia !== "function") return false;
  // `standalone` is the manifest's `display`; `fullscreen` and `minimal-ui` are
  // the other two that mean "no address bar", and a browser that reports one of
  // them has already given the player what this line would ask them to go and get.
  return ["standalone", "fullscreen", "minimal-ui"].some((mode) => {
    const q = win.matchMedia(`(display-mode: ${mode})`);
    return !!(q && q.matches);
  });
}

/**
 * An iPhone, including the ones that lie.
 *
 * iPadOS reports itself as a Mac, which is why `maxTouchPoints` is asked as
 * well — but an iPad *has* the element Fullscreen API, so it never reaches
 * here; the touch check is what stops a future Mac-shaped iOS device from
 * being missed rather than something this game needs today.
 */
function isIPhone(nav) {
  if (!nav) return false;
  // `indexOf` and not a regular expression literal: four checks in this app read
  // this source as text, and `code_only()` in scripts/js_source.py understands
  // strings but not regexes — one `/` in the wrong place and a parse of some
  // other file's comments would start at the wrong character (#233).
  const ua = String(nav.userAgent || "");
  const has = (s) => ua.indexOf(s) !== -1;
  if (has("iPhone") || has("iPod")) return true;
  return (has("iPad") || has("Macintosh")) && (nav.maxTouchPoints || 0) > 1;
}
