# Ana Bingo!

**Play it: https://for-real-life-game-production.up.railway.app**

(It was called *For Real Life!* until August 2026.) <!-- old-name-on-purpose: this sentence exists to explain the URL, so it has to say it -->
The URL keeps the old name on purpose: it is the link that has already been shared,
and renaming the Railway service would break it. Every *visible* copy of the name is
checked against `GAME_NAME` in `scripts/fetch_assets.py` by `fetch_assets.py --check`.

A fan-made, unofficial side-scrolling adventure inspired by *Bluey* — built for a
3-year-old to play on a phone, and for her dad to enjoy watching.

> Fan-made, unofficial and non-commercial. Bluey © Ludo Studio Pty Ltd. Character
> names and artwork are the property of their respective owners. Artwork retrieved
> from the community Bluey wiki; each image links to its source page.

That paragraph is not written here: it is the `notice` field of
[`public/data/asset-credits.json`](public/data/asset-credits.json), authored in
`scripts/fetch_assets.py`, and `fetch_assets.py --check` fails if this file, the
boot splash or the in-game credits screen drifts from it. This paragraph used to
claim the opposite — that none of the art here was anyone else's — and it stayed
that way after the artwork landed, because nothing failed.

Not affiliated with, endorsed by or sponsored by Ludo Studio, the ABC, BBC Studios
or Disney. The characters are the show's own artwork, used here for personal,
non-commercial fan use — every file is listed with its source and the date it was
fetched, the same list the in-game **Credits** screen and each bio card render
from. The site asks not to be indexed (`robots.txt` + `noindex`). If you own this
artwork and would rather it were not here, it comes down — open an issue.

Everything else is original: the props, the backdrops and the parallax are drawn
from scratch on a `<canvas>` at runtime, and all music and sound effects are
synthesised in the browser with the WebAudio API.

## The game

Five connected chapters — Bluey's favourite toy, Floppy, has gone missing, and the
search takes the family across Brisbane and back home again:

| # | Chapter | Setting | You play |
|---|---------|---------|----------|
| 1 | Keepy Uppy | The backyard | Bluey |
| 2 | The Creek | The creek | Bingo |
| 3 | Hammerbarn Dash | The hardware store | Bandit |
| 4 | Treasure Beach | The beach | Chilli |
| 5 | Sleepytime | A dream | Bingo |

Each chapter has a story card you can read aloud, a playable Heeler, a cameo friend
who says g'day as you run past, collectibles, one hidden "dollarbuck", and a star
rating at the end. A **Character Gallery** holds bios for 25 characters, each with
its own attribution.

### Toddler-friendly by design

* One control: **tap to jump, hold to float**. Auto-run is on by default.
* **No fail state** — falling in the creek is a splash and a friendly lift back up,
  bumping something is a "Whoops!" and a slow-down. Nothing is ever lost.
* Big tap targets, generous hitboxes, large readable HUD, no reading required to play.

### How the characters move

Where the artist drew the action, that drawing is what you see.
`public/data/poses.json` maps a character and a state — run, jump, cheer — to the
side-on renders in `public/assets/poses/`, and `public/js/sprites.js` draws the
whole frame, flipped to face the way it is travelling, with a bob, a squash and a
lean about the feet on top. Nothing is cut up, so nothing can tear.

**Each state is a still, and the motion is applied on top of it.** The wiki has
exactly one action render of each character, so the seven that exist are seven
single frames; the code draws `frames[0]` and never advances. It used to step
through the list at 12fps, which could never happen with one frame in it — and if
a second ever landed it would have been a strobe between two unrelated drawings
(`Bluey-Running` is a three-quarter view facing right, `Bluey-Leaping` is front-on
with the legs splayed), not a run cycle. `poses.json` still takes a list per state,
so real cycle artwork is data; the code that walks it should be written against
frames that actually cycle rather than kept warm in the hope of them.

What sells the run instead is the cadence. `STRIDE` in `sprites.js` is the run
clock in radians per second, shared by the pose motion and the rig so a character
with artwork keeps time with one falling back to the cut-out. The game reads the
same clock through `footfall()` — which asks whether the step just simulated
crossed a foot contact, so it reports the same footfalls whatever the frame rate —
and kicks up a little dust under the feet each time one lands.

Everything else is one flat front-facing render animated as a cut-out rig.
`public/data/rigs.json` names two lines across it — the neck and the hip — which
cut it into head / torso / legs, and those parts are drawn back to front with each
one overlapping the joint below it, so a rotated part never opens a seam. A rig can
also name a tail, a pair of ears and the eyes: the tail and ears are cut out and
swung about their own pivots, and a blink is wiped over the eyes in a colour
measured off the face. The legs are **not** rotated — a band cut below the hip of a
front-facing render is not a leg but a rectangle holding both legs, the gap between
them and, for Muffin, her tail, and swinging it slid a grey slab out sideways. So
the rig's job is to keep a character alive — breathing, wag, ears, blink — and a
pose frame's job is to make it run. Both are just numbers per part (`poseFor`,
`frameMotion`), which is why **adding a character is data, not code**: an image, a
credit entry and a rig. Every extra is optional — a rig with none of them draws
exactly as it did before they existed. Twenty-three of the twenty-five blink and
thirteen flop their ears; the boxes for those were proposed from the artwork by
`--suggest` and then looked at, and where a rule found nothing the character
carries a note in `PARTS` saying why, so a gap is a decision and not an oversight.
"Looked at once" is not much of a guarantee for 69 face boxes, so `--check` reads
each one back off the artwork it was measured on: an eye box has to be mostly eye
white with a pupil in it, which a box that slid onto a muzzle is not, and a lid
patch has to be one flat colour of face — it gets averaged to a single colour to
paint the blink, so a patch straddling a marking blinks in a colour that is on the
character nowhere, and one hanging off the side of a head blinks in background.
Two of the twenty-three were wrong that way until the check was written.

Sprites load lazily — the menu family at boot, a chapter's cast when its story card
opens, the gallery's 25 as you scroll. Until an image is there (or if one 404s) the
old hand-drawn dog is still drawn in its place, so nothing ever renders empty.
A failed request is retried with a doubling backoff (five tries, ~5s) and then
dropped, because `sprite()` is called from the render loop and an unconditional
retry would be a request per character per *frame*; the `online` event clears the
record, so a tunnel is not permanent. This matters more than it sounds: one
dropped connection used to mean that character stayed a procedural dog until
someone reloaded the page, and a three-year-old does not reload the page.

```bash
python3 scripts/fetch_assets.py --check   # credited, a cut-out, and the notice above unchanged
python3 scripts/build_rigs.py --check     # neck above hip, pivots agree with parts, every eye box
                                          #  is still eye white with a pupil in it, and every lid
                                          #  patch is still one flat colour of face
python3 scripts/build_rigs.py --sheet     # joint lines drawn over every sprite
python3 scripts/build_rigs.py --suggest   # propose ear/eye boxes, as PARTS source to paste
python3 scripts/shots.py                  # regenerate shots/
python3 scripts/rig_frames.py bluey       # a strip of run frames, to look at
python3 scripts/rig_frames.py bluey --blink
```

### Scoring

10 points per collectible, 250 for the hidden dollarbuck, a distance/finish bonus,
and 1–3 stars per chapter. Totals and unlocked chapters persist in `localStorage`.

## Running it

```bash
npm start          # http://localhost:3000  (no dependencies)
```

Serves `public/` and listens on `$PORT`, bound to `0.0.0.0` (Railway-ready).

`GET /api/health` (also `/healthz`) reports what this particular container is
running — `revision` is the deployed commit, `characters` and `chapters` are
counted off the files on disk, so a truncated copy shows up as a number that is
too small rather than as a page that looks fine until someone opens the gallery:

```json
{"status": "ok", "revision": "b48c6c8…", "characters": 25, "chapters": 5}
```

That one endpoint feeds `tools/uptime.py`'s content floors, `demoready`'s
"is the container current?" check, and `mirror.py push bluey-game --wait`.

## Tests

Playwright end-to-end suite — plays every chapter to the finish line, checks
scoring, stars, persistence, the gallery, mobile viewports (iPhone + Pixel),
touch input, WebAudio unlock-on-tap and console errors. Four of them open a page
of their own: one paints the cameo friends, whom the fast-forwarded chapters
never render, and three break the network — the artwork blocked outright (the
fallback dogs carry the whole game), blocked forever (the retries have to stop,
then resume when the connection returns), and dropped exactly once (that
character still ends up drawn from its own artwork).

```bash
python -m pytest tests -q                                # boots its own server
python -m pytest tests -q --base-url=https://<live-url>  # against the deployed site
```

98 tests, ~30s either way. With no `--base-url` the session starts `node server.js`
on a free port and stops it afterwards, so the suite needs nothing running and does
not disturb a dev server on 3000. They share one browser page per viewport and run
in file order, because the game is a sequence: a chapter has to be played before
there is any progress to persist.

The pixel half of `build_rigs.py --check` is the one check here with no second
opinion — the blink test builds its region of interest out of the rig's own eye
box, so it agrees with a box drawn anywhere, and it asks that the eye *changed*,
not that it changed to a colour off this dog. Its thresholds are broken on purpose
on a rotation (`tools/mutate.py`) to prove the suite still notices.

`tools/ship.py` runs it twice, and the two runs answer different questions: the
local one gates the push (is the working tree playable?), the `--base-url` one
gates the release (is what Railway is serving playable?).

## Layout

```
server.js               static file server ($PORT)
public/js/game.js       engine: physics, level generation, scoring
public/js/chapters.js   the five chapters + story text
public/js/art.js        props, backdrops and the fallback dog, drawn procedurally
public/js/sprites.js    pose frames + the cut-out rig: loads the artwork and animates it
public/js/audio.js      WebAudio music + SFX
public/js/main.js       screens, HUD, gallery, credits, save data
public/assets/characters/  25 character images (~2.2 MB total)
public/assets/poses/       the side-on action renders, one per character/state
public/data/characters.json      25 character bios
public/data/asset-credits.json   where each image came from, and when
public/data/poses.json           which frames a character uses for which state
public/data/rigs.json            neck/hip/leg pivots per character
scripts/fetch_assets.py  fetches the artwork and writes the credits file
scripts/build_rigs.py    derives the rigs, with hand-measured overrides
scripts/shots.py         walks the screens and writes shots/
tests/test_game.py      end-to-end suite (pytest, --base-url)
tests/test_prose.py     runs the --check scripts, and proves the pixel half can fail
tests/conftest.py       browser + page fixtures
```
