#!/usr/bin/env python3
"""Record every line the game says out loud, once, at build time.

`sound.read()` has always been `window.speechSynthesis` — the operating system's
voice, reading three paragraphs of story to a three-year-old. Babak's words were
"still robotic and pretty bad", and no amount of `rate`/`pitch` fixes that: the
voice is not ours to choose. So the lines are spoken once here, committed as mp3,
and played back as files. A browser calling a TTS API would need the key in the
page, and Railway's filesystem is ephemeral, so build time is the only place this
can happen (#305, #311).

**The route is not the obvious one.** OpenAI's `/v1/audio/speech` returns raw
bytes, and everything here reaches OpenAI through the Pipedream Connect proxy,
which JSON-parses every response — a binary body fails on byte one and surfaces
as an HTTP 502 that looks like an outage and is not. `gpt-audio` with
`modalities:["text","audio"]` returns the same mp3 base64-encoded *inside* the
JSON body, which the proxy carries. See #308 for the repro.

That substitution has a cost worth knowing about: this is a chat model being
asked to read aloud, not a TTS endpoint, so it can decide a clause is a stage
direction and drop it. It has been caught doing exactly that — "Voice alloy.
Chapter one..." came back without the first two words, twice, deterministically.
Every clip is therefore checked against the `transcript` the API returns, and a
line that will not come back intact fails the render rather than shipping a
recording that says something else. `--check` asks the other half of the
question: whether the manifest still covers the lines the game would speak today.

The manifest is keyed by the line's own text rather than by a hash, so the
runtime lookup is `voices[text]` with no crypto in the page, and a diff of
`voices.json` reads as English.

    python scripts/render_voices.py --check     # is the manifest still true?
    python scripts/render_voices.py             # record whatever is missing
    python scripts/render_voices.py --all       # re-record everything
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent.parent
PUBLIC = APP / "public"
AUDIO = PUBLIC / "audio"
MANIFEST = PUBLIC / "data" / "voices.json"

# --------------------------------------------------------------- casting --

#: The steering `gpt-4o-mini-tts` takes in `instructions` has to go in the system
#: message on this route. That makes casting a paragraph of English per voice
#: rather than a model parameter, which is the one nice thing about it.
#:
#: `SCRIPT` is not politeness. Without it the model treats a short line with no
#: sentence around it as a request rather than as a script: "The backyard" — a
#: chapter's location subtitle — came back as "In the backyard I see a green
#: lawn, a wooden table with a potted plant on…", three times running. The game
#: is full of two-word lines (every character's name is one), so the instruction
#: that a line is a line however short it is has to be the loudest thing here.
SCRIPT = ("Every message you are given is a line of script to perform, however "
          "short — a name on its own, or two words, is still a line. Never "
          "answer it, describe it, or react to it, and never add a word that is "
          "not in it. Speak exactly those words and nothing else. ")

#: voice + direction per character. The direction is not decoration: with 25
#: characters on 13 voices it is the thing that keeps two school friends from
#: being the same performance.
VOICES = {
    # the family
    "bluey":      ("nova",    "a bright, confident six-year-old, full of beans"),
    "bingo":      ("coral",   "a smaller, softer four-year-old, gentle and thoughtful"),
    "bandit":     ("ash",     "a warm Australian larrikin dad, dry and playful"),
    "chilli":     ("marin",   "a calm, warm mum, unhurried and kind"),
    # the cousins
    "muffin":     ("shimmer", "a loud, overtired four-year-old who is absolutely certain"),
    "socks":      ("coral",   "a delighted three-year-old, tiny and simple, barely words"),
    "stripe":     ("cedar",   "a hearty, competitive uncle who never lost his teenage energy"),
    "trixie":     ("nova",    "a cheerful, easygoing aunt, adult and unhurried"),
    # the grandparents
    "nana_chris": ("ballad",  "a soft, unhurried grandma with a smile in her voice"),
    "bob":        ("onyx",    "a gravelly grandad, slow and fond"),
    # the friends
    "lucky":      ("alloy",   "an easygoing six-year-old mate, relaxed and game for anything"),
    "judo":       ("shimmer", "a small, quick five-year-old, words tumbling out"),
    "honey":      ("fable",   "a shy, careful six-year-old, soft and precise"),
    "coco":       ("nova",    "an eager, slightly dramatic six-year-old trying very hard"),
    "snickers":   ("verse",   "a dreamy six-year-old, a beat behind everyone else"),
    "chloe":      ("marin",   "a thoughtful, kind six-year-old, gentle"),
    "mackenzie":  ("echo",    "an earnest six-year-old with a soft New Zealand lilt"),
    "rusty":      ("cedar",   "a laid-back country kid, easy and unhurried"),
    "indy":       ("fable",   "a gentle, imaginative six-year-old, half in a daydream"),
    "winton":     ("ash",     "a slow, literal six-year-old, deadpan"),
    "jean_luc":   ("verse",   "a softly spoken six-year-old with a French-Canadian lilt"),
    # the grown-ups
    "calypso":    ("sage",    "a serene teacher, unhurried, never raises her voice"),
    "frisky":     ("nova",    "a bubbly grown-up, quick and warm"),
    "rad":        ("cedar",   "a cool older uncle, relaxed and a bit too pleased with himself"),
    # not a dog
    "chattermax": ("shimmer", "a plastic singing toy at maximum volume, relentlessly cheerful and a bit tinny"),
}

#: The narrator is not a character: every line *about* a character — a bio, a
#: chapter's story, the results — is narration, and it is one voice throughout.
#: The table above is for lines a character **says**.
NARRATOR = ("sage",
            "You are a voice-over artist reading a picture book aloud to a "
            "three-year-old: warm, unhurried, an Australian parent at bedtime.")

#: role -> (voice, system prompt). One entry per character plus the narrator, so
#: `say()` and the manifest keep taking a role and knowing nothing about who is
#: in the cast.
CASTS = {
    "narrator": (NARRATOR[0], SCRIPT + NARRATOR[1]),
    **{cid: (voice, SCRIPT + f"You are voicing {direction}, a character in an "
             "Australian children's cartoon. Stay in that character for every "
             "word of the line.")
       for cid, (voice, direction) in VOICES.items()},
}


def normalise(text) -> str:
    """The same collapsing `sound.read()` does before it speaks a line.

    The manifest is keyed by this, so the page can look a line up with the
    string it already has instead of having to agree with Python about a hash.
    """
    return re.sub(r"\s+", " ", str(text)).strip()


def spoken_form(text: str) -> str:
    """What comes back from a reading, reduced to the words themselves.

    Only for the drift check. Case and punctuation move around freely between
    the line and its transcript ("Chapter one" -> "Chapter One.") and that is a
    reading, not a drift. A *dropped clause* is the failure this exists to
    catch, and it survives this reduction.
    """
    return re.sub(r"[^a-z0-9 ]", " ", normalise(text).lower()).strip()


# ------------------------------------------------------------- the lines --

def js_eval(path: Path, expr: str):
    """Evaluate `expr` inside a leaf ES module, where `m` is the module itself.

    `chapters.js` and `abilities.js` hold most of the game's prose. A regex over
    the source would make this a second author for every line of it and would go
    quietly wrong the first time a story paragraph contained a brace. Node is
    already the thing that agrees with the browser, so node does the reading —
    the copy to `.mjs` is only because a bare `.js` with no package.json is
    CommonJS to node, which would refuse the `export`.

    An expression rather than only the exports, because two of the lines are
    *functions* of a name (`lines.js`) and an exported function does not survive
    `JSON.stringify`. Calling it here is what keeps the wording in one place: the
    game says whatever that function returns, and so does this.
    """
    with tempfile.TemporaryDirectory() as tmp:
        mjs = Path(tmp) / (path.stem + ".mjs")
        shutil.copy(path, mjs)
        proc = subprocess.run(
            ["node", "--input-type=module", "-e",
             f"import * as m from {json.dumps(str(mjs))};"
             f"process.stdout.write(JSON.stringify({expr}));"],
            capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"could not read {path.name}: {(proc.stderr or '').strip()[:400]}")
    return json.loads(proc.stdout)


def js_module(path: Path) -> dict:
    """A leaf ES module's exports, by running it rather than parsing it."""
    return js_eval(path, "m")


def js_calls(path: Path, fn: str, arglists: list[list]) -> list[str]:
    """`fn(*args)` for each args in `arglists` — one node run, not N."""
    return js_eval(path, f"{json.dumps(arglists)}.map((a) => m.{fn}(...a))")


def lines_for() -> list[tuple[str, str]]:
    """Every line the game speaks, as (role, text), in a stable order.

    Read out of the same files the game reads, so a chapter renamed or a bio
    reworded turns up on the next `--check` instead of being discovered by ear.

    What is deliberately *not* here: the results lines carrying a number the
    player earned. Those are assembled at runtime from a score, so there is no
    fixed text to record, and they keep the browser voice. That is the reason
    the runtime falls back per line rather than per screen.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(role: str, text) -> None:
        t = normalise(text or "")
        if t and t not in seen:
            seen.add(t)
            out.append((role, t))

    for ch in js_module(PUBLIC / "js" / "chapters.js")["CHAPTERS"]:
        add("narrator", f"Chapter {ch['n']}. {ch['title']}.")
        add("narrator", ch.get("where"))
        for s in ch.get("story") or []:
            add("narrator", s)
        add("narrator", ch.get("joke"))
        add("narrator", ch.get("outro"))

    # ["characters"], the same key main.js reads: the file's top level is a note
    # about the game being a fan tribute, and that note is not spoken.
    chars = json.loads((PUBLIC / "data" / "characters.json").read_text())["characters"]
    by_id = {c["id"]: c for c in chars}

    ab = js_module(PUBLIC / "js" / "abilities.js")
    # The picker's heading, quoted out of main.js rather than typed here. There
    # are two of them (the first time, and "somebody else?" once a hero is
    # chosen) and this line was written down as "Who do you want to play as?" —
    # a sentence the game has never said. A hand-typed quote of another file
    # drifts silently; a recording of it is simply never played.
    main = (PUBLIC / "js" / "main.js").read_text()
    for heading in re.findall(r'"([^"]+)"', " ".join(re.findall(r"title[:=][^\n]*", main))):
        add("narrator", heading)
    for cid in ab["PLAYABLE"]:
        a = ab["ABILITIES"][cid]
        # in their own voice, not the one shared "kid" (#361): the picker is four
        # dogs introducing themselves, and four introductions in one performance
        # is a list being read out
        add(cid, f"{by_id.get(cid, {}).get('name', cid)}. {a['name']}. {a['blurb']}")

    # The two assembled lines, quoted by calling the functions the game calls
    # (lines.js) rather than by writing the wording down a second time.
    #
    # Who can say the greeting is not "the whole cast": `placeFriends` puts out
    # PLAYABLE minus the hero, because a caught friend *runs* with you and only
    # those four have run artwork (#306/#215). Twenty-five characters x four
    # heroes would be 96 clips, 84 of which nothing in the game can ever reach —
    # and `--check` would call every one of them an orphan.
    name = lambda cid: by_id.get(cid, {}).get("name", cid)          # noqa: E731
    LINES = PUBLIC / "js" / "lines.js"
    pairs = [[hero, cid] for cid in ab["PLAYABLE"] for hero in ab["PLAYABLE"]
             if hero != cid]
    for (hero, cid), text in zip(
            pairs, js_calls(LINES, "greeting", [[name(h), name(c)] for h, c in pairs])):
        add(cid, text)

    # The hello every character has: the bio screen opens with the dog saying who
    # they are, and the narrator reads the rest. It is the only line 11 of the 25
    # ever get — nobody catches Chattermax — and without it half the cast is a
    # voice in a table that never makes a sound.
    ids = [c["id"] for c in chars]
    for cid, text in zip(ids, js_calls(LINES, "hello", [[name(c)] for c in ids])):
        add(cid, text)

    for c in chars:
        add("narrator", c["name"])
        age = f", age {c['age']}" if c.get("age") else ""
        add("narrator", f"{c['species']}. {c['role']}{age}.")
        add("narrator", c.get("personality"))
        add("narrator", c.get("funFact"))

    for stars in ("no stars this time", "one star", "two stars", "three stars"):
        add("narrator", f"{stars}.")
    add("narrator", "You found the hidden dollarbucks!")
    add("narrator", "The hidden dollarbucks is still hidden.")
    return out


# ---------------------------------------------------------------- render --

def filename(text: str) -> str:
    """A stable name for a line's recording: readable stem, hashed tail.

    The stem is so that a directory listing means something to a human; the tail
    is so two lines that open the same way cannot collide.
    """
    stem = re.sub(r"[^a-z0-9]+", "-", normalise(text).lower())[:40].strip("-")
    return f"{stem or 'line'}-{hashlib.sha1(normalise(text).encode()).hexdigest()[:8]}.mp3"


class Drifted(RuntimeError):
    """The reading came back saying something other than the line."""


#: A recording holds at least this many bytes of mp3 per character of the line.
#:
#: The transcript check above asks whether the model *said* the right words; it
#: cannot see whether the audio it attached holds them. One clip in the first
#: full render came back with a word-perfect transcript and 1149 bytes of mp3 —
#: 0.1 seconds, silence — for a 79-character sentence, and it took playing it in
#: a browser to notice. Everything else in that render sat between 657 and 1600
#: bytes per character (median 885), so a floor here is nowhere near any real
#: reading: it is the difference between a clip and the absence of one.
MIN_BYTES_PER_CHAR = 200


class Empty(RuntimeError):
    """The reading said the right words and attached almost no audio."""


#: The line does not go in the user message on its own. Left bare, 13 of the
#: first 171 came back as a *reply* rather than a reading — "Who do you want to
#: play as?" was answered ("I'm here to help, you can choose any character…"),
#: "Bob is not supposed to be doing that" was agreed with, "Lucky" was expanded
#: to "Lucky the frog", and every bio's opening clause was dropped as though it
#: were a heading. The same system prompt plus this one sentence of framing took
#: all 13 to a word-perfect reading. The system message says what the voice is;
#: this says what the message is.
PERFORM = "Perform this line of the script, word for word, and say nothing else:\n\n"


def say(text: str, role: str) -> bytes:
    """One line, read once, or an exception. Never a recording of other words."""
    voice, system = CASTS[role]
    body = {"model": "gpt-audio", "modalities": ["text", "audio"],
            "audio": {"voice": voice, "format": "mp3"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": PERFORM + text}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(body, fh)
        req = fh.name
    try:
        # --json-file, not --json: a story paragraph is long enough to make the
        # argv spelling a size limit waiting to be hit.
        proc = subprocess.run(
            [sys.executable, "tools/pdx.py", "http", "openai", "POST",
             "https://api.openai.com/v1/chat/completions", "--json-file", req],
            cwd=REPO, capture_output=True, text=True)
    finally:
        Path(req).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout or proc.stderr or "no output").strip()[-300:])
    au = json.loads(proc.stdout)["response"]["body"]["choices"][0]["message"]["audio"]
    heard, wanted = spoken_form(au.get("transcript", "")), spoken_form(text)
    if heard != wanted:
        raise Drifted(f"asked for {wanted!r}, was read {heard!r}")
    data = base64.b64decode(au["data"])
    if len(data) < MIN_BYTES_PER_CHAR * len(normalise(text)):
        raise Empty(f"{len(data)} bytes of mp3 for {len(normalise(text))} characters — "
                    f"the transcript was right and the audio is silence")
    return data


def record(job: tuple[str, str], tries: int = 3) -> tuple[str, str, bytes | None, str]:
    """`say` with retries. Drift is retried too — it is usually, but not always,
    a one-off, and the deterministic kind has to end as a failure rather than as
    a clip of the wrong words."""
    role, text = job
    last = ""
    for _ in range(tries):
        try:
            return role, text, say(text, role), ""
        except Drifted as e:
            last = f"drifted: {e}"
        except Empty as e:
            last = f"empty: {e}"
        except Exception as e:                                # noqa: BLE001
            last = str(e)
    return role, text, None, last


# ----------------------------------------------------------------- main --

def load_manifest() -> dict:
    if not MANIFEST.exists():
        return {"voice": {k: v[0] for k, v in CASTS.items()}, "lines": {}}
    return json.loads(MANIFEST.read_text())


def check(manifest: dict, wanted: list[tuple[str, str]]) -> int:
    """Does the manifest still describe the lines the game would speak today?

    Five separate answers, because they have different fixes: a line with no
    recording (someone edited the prose), a recording whose file is gone, a
    recording that is on disk and holds no audio, a recording read by somebody
    other than the character who says it, and a recording for a line nothing
    says any more (dead weight in the deploy).

    The silent one is asked of the file, not of the manifest's `bytes`: a clip
    that plays as 0.1 seconds of nothing is exactly as broken as a missing one
    and looks exactly as healthy in a listing.

    The miscast one is why recasting a line is not a silent no-op: the four
    picker lines moved from the shared `kid` voice to each dog's own (#361), and
    a check that only asks "is there a file?" would have called that green while
    Bingo went on being read by Bluey's voice.
    """
    lines = manifest.get("lines") or {}
    missing = [t for _, t in wanted if t not in lines]
    here = [t for _, t in wanted
            if t in lines and (AUDIO / lines[t]["file"]).is_file()]
    gone = [t for _, t in wanted
            if t in lines and not (AUDIO / lines[t]["file"]).is_file()]
    silent = [t for t in here
              if (AUDIO / lines[t]["file"]).stat().st_size < MIN_BYTES_PER_CHAR * len(t)]
    miscast = [t for r, t in wanted if t in lines and lines[t].get("role") != r]
    orphan = [t for t in lines if t not in {t for _, t in wanted}]
    for label, items in (("no recording", missing), ("file missing", gone),
                         ("silent recording", silent),
                         ("read by the wrong voice", miscast),
                         ("recorded but never spoken", orphan)):
        for t in items[:8]:
            print(f"  {label}: {t[:70]!r}")
        if len(items) > 8:
            print(f"  … and {len(items) - 8} more {label}")
    print(f"{len(wanted)} line(s) spoken, {len(lines)} recorded — "
          f"{len(missing)} unrecorded, {len(gone)} missing a file, "
          f"{len(silent)} silent, {len(miscast)} miscast, {len(orphan)} orphaned")
    return 1 if (missing or gone or silent or miscast or orphan) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift between the manifest and the game's prose; record nothing")
    ap.add_argument("--all", action="store_true", help="re-record lines that already have a file")
    ap.add_argument("--limit", type=int, default=0, help="stop after N recordings (for a trial run)")
    ap.add_argument("--jobs", type=int, default=4, help="concurrent readings")
    args = ap.parse_args()

    wanted = lines_for()
    manifest = load_manifest()
    if args.check:
        return check(manifest, wanted)

    lines = manifest.setdefault("lines", {})

    def wants_recording(role: str, t: str) -> bool:
        if args.all or t not in lines:
            return True
        # recast counts as unrecorded: the clip on disk is the right words in
        # the wrong mouth, and nothing downstream would ever notice
        if lines[t].get("role") != role:
            return True
        f = AUDIO / lines[t]["file"]
        # a silent clip is re-recorded like a missing one: it is a file that
        # exists and says nothing, and leaving it needs a hand-run --all
        return not f.is_file() or f.stat().st_size < MIN_BYTES_PER_CHAR * len(t)

    todo = [(r, t) for r, t in wanted if wants_recording(r, t)]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(wanted)} line(s) spoken, {len(todo)} to record")
    if not todo:
        return 0

    AUDIO.mkdir(parents=True, exist_ok=True)
    failed = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for role, text, data, err in pool.map(record, todo):
            if data is None:
                failed.append((text, err))
                print(f"  FAIL {text[:60]!r} — {err[:120]}")
                continue
            name = filename(text)
            (AUDIO / name).write_bytes(data)
            lines[text] = {"file": name, "voice": CASTS[role][0], "role": role,
                           "bytes": len(data)}
            done += 1
            print(f"  {done:>3}/{len(todo)} {len(data):>7} B  {text[:58]}")

    manifest["voice"] = {k: v[0] for k, v in CASTS.items()}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    total = sum(v["bytes"] for v in lines.values())
    print(f"recorded {done}, failed {len(failed)}; "
          f"{len(lines)} clip(s), {total / 1e6:.1f} MB in {AUDIO.relative_to(APP)}")
    # A partial render is not a failure — it is resumable, and the next run
    # picks up exactly what is missing. A failure is what is left when it stops
    # being able to make progress, which is what a non-zero exit is for.
    return 1 if failed and not done else 0


if __name__ == "__main__":
    raise SystemExit(main())
