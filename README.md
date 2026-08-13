# For Real Life!

**Play it: https://for-real-life-game-production.up.railway.app**

A fan-made, unofficial side-scrolling adventure inspired by *Bluey* — built for a
3-year-old to play on a phone, and for her dad to enjoy watching.

**Fan-made and unofficial. Not affiliated with Ludo Studio, ABC or BBC.**
No copyrighted art, audio or assets are used or shipped. Every character, prop and
backdrop is drawn from scratch on a `<canvas>` at runtime, and all music and sound
effects are synthesised in the browser with the WebAudio API.

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
rating at the end. A **Character Gallery** holds bios for 25 characters.

### Toddler-friendly by design

* One control: **tap to jump, hold to float**. Auto-run is on by default.
* **No fail state** — falling in the creek is a splash and a friendly lift back up,
  bumping something is a "Whoops!" and a slow-down. Nothing is ever lost.
* Big tap targets, generous hitboxes, large readable HUD, no reading required to play.

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
touch input, WebAudio unlock-on-tap and console errors.

```bash
python -m pytest tests -q                                # boots its own server
python -m pytest tests -q --base-url=https://<live-url>  # against the deployed site
```

31 tests, ~10s either way. With no `--base-url` the session starts `node server.js`
on a free port and stops it afterwards, so the suite needs nothing running and does
not disturb a dev server on 3000. They share one browser page per viewport and run
in file order, because the game is a sequence: a chapter has to be played before
there is any progress to persist.

`tools/ship.py` runs it twice, and the two runs answer different questions: the
local one gates the push (is the working tree playable?), the `--base-url` one
gates the release (is what Railway is serving playable?).

## Layout

```
server.js               static file server ($PORT)
public/js/game.js       engine: physics, level generation, scoring
public/js/chapters.js   the five chapters + story text
public/js/art.js        every character and prop, drawn procedurally
public/js/audio.js      WebAudio music + SFX
public/js/main.js       screens, HUD, gallery, save data
public/data/characters.json  25 character bios
tests/test_game.py      end-to-end suite (pytest, --base-url)
tests/conftest.py       browser + page fixtures
```
