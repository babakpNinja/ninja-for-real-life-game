/*
 * audio.js — all music and sound effects are synthesised live with WebAudio.
 * No sampled audio from the show (or anywhere else) is used.
 *
 * iOS/Android autoplay policy: the AudioContext stays suspended until the first
 * real user gesture, so unlock() must be called from a tap/click handler.
 */

const SCALE = [0, 2, 4, 7, 9]; // major pentatonic — cheerful, hard to play a wrong note

export class Sound {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.musicGain = null;
    this.sfxGain = null;
    this.verb = null;
    this.muted = false;
    // set by the UI: called with the new mute whenever it changes, so nothing
    // has to keep a second copy of "is the sound off" in step (#296)
    this.onmute = null;
    this.timer = null;
    this.step = 0;
    this.theme = null;
    this.ready = false;
    // which read() the utterances now in the queue belong to. cancel() makes the
    // *previous* read end — as 'canceled'/'interrupted' — and its handlers would
    // otherwise report that as the outcome of the read that replaced it (#290)
    this.speechRun = 0;
  }

  unlock() {
    if (this.ctx) {
      if (this.ctx.state === "suspended") this.ctx.resume();
      return this.ready;
    }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = 0.9;
    // tanh on the way out. Nothing here is loud enough to clip on its own, but
    // a chapter can fire four cues over the music in the same tenth of a second
    // and the sum of them was the one thing that could go crunchy (#305).
    const shaper = this.ctx.createWaveShaper();
    const curve = new Float32Array(1024);
    for (let i = 0; i < curve.length; i++) {
      const x = (i / (curve.length - 1)) * 2 - 1;
      curve[i] = Math.tanh(x * 1.5) / Math.tanh(1.5);
    }
    shaper.curve = curve;
    shaper.oversample = "2x";
    this.master.connect(shaper).connect(this.ctx.destination);

    // A small bright room, made of decaying noise. Every voice sends a share of
    // itself here, which is most of what "not a beeper" sounds like: a cue that
    // ends into something instead of just stopping.
    this.verb = this.ctx.createConvolver();
    this.verb.buffer = this.room(0.9);
    const verbGain = this.ctx.createGain();
    verbGain.gain.value = 0.5;
    this.verb.connect(verbGain).connect(this.master);

    this.musicGain = this.ctx.createGain();
    this.musicGain.gain.value = 0.16;
    this.musicGain.connect(this.master);

    this.sfxGain = this.ctx.createGain();
    this.sfxGain.gain.value = 0.3;
    this.sfxGain.connect(this.master);

    // a tiny silent blip — this is what actually unlocks audio on iOS
    const o = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    g.gain.value = 0.0001;
    o.connect(g).connect(this.master);
    o.start();
    o.stop(this.ctx.currentTime + 0.02);

    if (this.ctx.state === "suspended") this.ctx.resume();
    this.ready = true;
    return true;
  }

  /**
   * An impulse response for the convolver: noise that dies away.
   *
   * The first few milliseconds are ramped in so the room does not answer with a
   * copy of the click it was built to hide, and the two channels are drawn
   * separately — the same noise in both ears is a corridor, different noise is
   * a room.
   */
  room(seconds) {
    const len = Math.max(1, Math.floor(this.ctx.sampleRate * seconds));
    const buf = this.ctx.createBuffer(2, len, this.ctx.sampleRate);
    // A room is one room: the same shape every time the game starts, not a new
    // one per boot. So the noise is drawn from a fixed seed rather than from
    // Math.random — which also means two renders of one cue differ only where a
    // *player* differs from themselves, and nothing else.
    let seed = 0x9e3779b9;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    for (let c = 0; c < buf.numberOfChannels; c++) {
      const d = buf.getChannelData(c);
      for (let i = 0; i < len; i++) {
        const t = i / len;
        d[i] = (rnd() * 2 - 1) * Math.pow(1 - t, 2.4) * Math.min(1, i / 240);
      }
    }
    return buf;
  }

  setMuted(m) {
    this.muted = m;
    if (this.master) this.master.gain.value = m ? 0 : 0.9;
    // speech does not go through the master gain, so muting has to reach it
    // by hand — otherwise the story keeps talking over a silenced game
    if (m) this.hush();
    // the screen is told by whoever is silent, rather than by each caller
    // remembering to redraw afterwards: a mute the UI did not initiate used to
    // leave a 🔊 on the HUD over a silent game (#296)
    if (this.onmute) this.onmute(m);
  }

  /* ------------------------------------------------- reading out loud -- */

  /**
   * Say a few lines out loud, in the browser's own voice.
   *
   * The player this is built for is three, and the story card is three
   * paragraphs of grey text she cannot read (#255). Nothing is synthesised
   * here — this is the one part of the audio stack that is not WebAudio, which
   * is why mute is checked in this method rather than at a gain node, and why
   * `hush()` exists at all: a queued utterance outlives the screen that asked
   * for it and would go on talking over the next one.
   *
   * Speech needs the same user gesture WebAudio does, and every screen that
   * calls this was reached by a tap. A browser with no `speechSynthesis` (or a
   * muted one) is not an error: it gets the same silent card it has now.
   *
   * Returns whether anything was queued, and answers `onend` or `onerror` at
   * most once, for the read as a whole: the caller is a button that has to say
   * what it is doing, and "reading" that never comes back is worse than silence
   * (#290).
   *
   * **No test here can hear this.** Measured in the suite's own browser
   * (headless Chromium 1217, 2026-08-15, #289):
   *
   *     getVoices()  -> 0
   *     speak(u)     -> onerror 'synthesis-failed', no onstart, no onend
   *     speaking / pending stay false at 50, 200, 800 and 2000ms
   *
   * So every assertion in the suite is about what this method *asked for* — the
   * utterance texts, the rate, that `cancel()` was called — and nothing would
   * notice if a real browser rejected them. A voice can be made to exist in the
   * container, but not in the browser the game is tested in: it took
   * `espeak-ng` + `speech-dispatcher` (with an ALSA null sink, since there is no
   * audio device), a *headed* Chromium under Xvfb, and `SPEECHD_ADDRESS`
   * pointing at a hand-started daemon — headless or without either one gives 0
   * voices — and even then `speak()` still ended in 'synthesis-failed'.
   *
   * What would change the answer: a browser build that speaks without an audio
   * device, or a check that reads the espeak module's output rather than the
   * page's events. Until then `test_this_browser_cannot_speak` in test_game.py
   * pins the numbers above, so the day the environment gains a voice the suite
   * says so instead of quietly staying green over a stub.
   */
  read(lines, { onend, onerror } = {}) {
    const synth = window.speechSynthesis;
    if (!synth || this.muted) return false;
    const texts = [];
    for (const line of lines) {
      const text = String(line).replace(/\s+/g, " ").trim();
      if (text) texts.push(text);
    }
    if (!texts.length) return false;
    const run = ++this.speechRun;
    synth.cancel();
    // one answer per read, from whichever comes first: `onend` is asked of the
    // last utterance only, because the caller wants the end of the *story*, not
    // the end of its first paragraph
    let answered = false;
    const once = (fn) => (e) => {
      if (answered || run !== this.speechRun || !fn) return;
      answered = true;
      fn(e);
    };
    texts.forEach((text, i) => {
      const say = new SpeechSynthesisUtterance(text);
      say.rate = 0.92;              // slower than default: it is being read to a child
      say.pitch = 1.1;
      if (i === texts.length - 1) say.onend = once(onend);
      // a device with the API and no usable voice answers 'synthesis-failed'
      // here and nothing else ever happens — the one way to tell "it is talking"
      // from "it will never talk" (#289)
      say.onerror = once(onerror);
      synth.speak(say);
    });
    return true;
  }

  hush() {
    this.speechRun++;              // whatever is in the queue is no longer anyone's
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  /* ------------------------------------------------------------ voices -- */

  /**
   * When a voice asked for `when` seconds from now should actually start.
   *
   * The clamp is not defensive dressing: the arpeggios jitter their timing, so
   * the first note of one can ask for a couple of milliseconds *ago*, and
   * `setValueAtTime` throws on a negative time. Live it never bit — a real
   * context's clock is minutes in — but an `OfflineAudioContext` starts at 0,
   * which is exactly where `scripts/render_audio.py` found it (#305). A cue
   * asked for in the past plays now.
   */
  at(when) {
    return this.ctx.currentTime + Math.max(0, when || 0);
  }

  /**
   * The wobble. Every "a person played this" decision — how far off the written
   * pitch a note lands, how hard it was hit, how late it arrives — comes through
   * here rather than calling `Math.random()` at the site.
   *
   * Not a wrapper for its own sake: it is the one place that can be held still.
   * With this returning a constant the whole game plays identically every time,
   * which is the thing `test_the_same_cue_twice_is_not_the_same_recording` is
   * about, and there is no other single line that can put it back.
   *
   * The noise sources do *not* come through here: their randomness is the sound
   * itself, not a performance, and a silent burst would fail a different test
   * for a reason that has nothing to do with being robotic.
   */
  rand() {
    return Math.random();
  }

  /**
   * One note, and the only place a pitch is turned into sound (#305).
   *
   * Babak said the audio was "robotic and pretty bad", twice. Measured against
   * the old version with `scripts/render_audio.py`, three things were doing it:
   *
   *   - **the click.** The old envelope reached full gain 12ms after the note
   *     started, which is a transient, not an attack. Rendered, the cues peaked
   *     4-5ms in. `attack` here is 18ms by default and the ramp is a curve.
   *   - **one oscillator, one frequency.** A single wave at exactly 520.00Hz
   *     every single time is the sound of a machine; two of them a few cents
   *     apart, landing a hair off the written pitch, is the sound of an
   *     instrument. Hence `detune` and the ±0.6% jitter below.
   *   - **no room.** Everything was bone dry and none of it decayed into
   *     anything. `verb` sends a share to the convolver built in `unlock()`.
   *
   * `cutoff` is the other half of "bright and harsh": a triangle at 800Hz has
   * real energy above 5kHz, and the lowpass — sweeping down as the note dies,
   * like a struck thing losing its edge — is what takes the fizz off.
   */
  tone(freq, dur, {
    type = "triangle", gain = 0.3, when = 0, attack = 0.018, cutoff = 2400,
    detune = 8, from = 0, verb = 0.22, dest = null,
  } = {}) {
    if (!this.ctx || this.muted) return;
    const t0 = this.at(when);
    const f = freq * (1 + (this.rand() - 0.5) * 0.006);
    const osc = this.ctx.createOscillator();
    const osc2 = this.ctx.createOscillator();
    const filter = this.ctx.createBiquadFilter();
    const g = this.ctx.createGain();

    osc.type = type;
    osc2.type = "sine";
    osc.detune.value = -detune / 2;
    osc2.detune.value = detune / 2;
    for (const o of [osc, osc2]) {
      if (from) {
        // a hop, a fall: the pitch arrives rather than being stated
        o.frequency.setValueAtTime(f * from, t0);
        o.frequency.exponentialRampToValueAtTime(f, t0 + Math.min(0.12, dur * 0.6));
      } else {
        o.frequency.setValueAtTime(f, t0);
      }
    }
    filter.type = "lowpass";
    filter.Q.value = 0.7;
    filter.frequency.setValueAtTime(cutoff, t0);
    filter.frequency.exponentialRampToValueAtTime(Math.max(220, cutoff * 0.4), t0 + dur);

    const peak = Math.max(0.0002, gain * (0.92 + this.rand() * 0.16));
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + attack);
    g.gain.exponentialRampToValueAtTime(peak * 0.55, t0 + attack + dur * 0.25);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + dur);

    osc.connect(filter);
    osc2.connect(filter);
    filter.connect(g);
    g.connect(dest || this.sfxGain);
    this.send(g, verb);
    osc.start(t0); osc2.start(t0);
    const off = t0 + attack + dur + 0.05;
    osc.stop(off); osc2.stop(off);
  }

  /** Plucked string — the ukulele-ish backbone of the soundtrack. */
  pluck(freq, dur, gain, when = 0, dest = null) {
    this.tone(freq, dur, {
      type: "triangle", gain, when, attack: 0.012, cutoff: 1900, detune: 6,
      verb: 0.3, dest: dest || this.musicGain,
    });
  }

  /**
   * Filtered noise: scuffs, splashes, digging, the shaker.
   *
   * `cutoff` is new and is what stops these being fizz — the old version put a
   * bandpass at (say) 2400Hz and let everything above it through, which
   * measured as a third of the energy over 4kHz on `bop` and `dig`.
   */
  noise(dur, gain, when = 0, freq = 1200, { q = 0.8, cutoff = 3600, verb = 0.18, curve = 1.6 } = {}) {
    if (!this.ctx || this.muted) return;
    const t0 = this.at(when);
    const len = Math.max(1, Math.floor(this.ctx.sampleRate * dur));
    const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, curve);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    const band = this.ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = freq;
    band.Q.value = q;
    const lid = this.ctx.createBiquadFilter();
    lid.type = "lowpass";
    lid.frequency.value = cutoff;
    const g = this.ctx.createGain();
    // even a noise burst gets a couple of milliseconds to arrive: a buffer that
    // starts at full gain is a click with a tail
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), t0 + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(band).connect(lid).connect(g);
    g.connect(this.sfxGain);
    this.send(g, verb);
    src.start(t0);
  }

  /** Send a share of a voice to the room. Silent no-op when there is no room. */
  send(node, amount) {
    if (!this.verb || !amount) return;
    const s = this.ctx.createGain();
    s.gain.value = amount;
    node.connect(s).connect(this.verb);
  }

  /* --------------------------------------------------------------- sfx -- */

  /**
   * An arpeggio, with the timing of somebody playing it (#305).
   *
   * The old chords were laid out on an exact 70ms grid, which is a sequencer.
   * The gap here wanders by a few milliseconds a note and the notes get quieter
   * as the run goes up, the way a hand does.
   */
  arp(root, steps, { dur = 0.3, gap = 0.075, gain = 0.24, type = "triangle", cutoff = 2600 } = {}) {
    steps.forEach((n, i) => {
      const when = i * gap + (this.rand() - 0.5) * gap * 0.22;
      this.tone(root * Math.pow(2, n / 12), dur, {
        type, gain: gain * (1 - i * 0.06), when, cutoff, verb: 0.3,
      });
    });
  }

  // the hop: pitch arriving from below, plus the scuff of a paw leaving grass
  jump()      { this.tone(520, 0.2, { from: 0.72, gain: 0.24, cutoff: 2400, attack: 0.014 });
                this.noise(0.09, 0.05, 0, 1500, { cutoff: 2600, curve: 2.4 }); }
  collect(n)  { const f = 660 * Math.pow(1.0595, (n % 6) * 2);
                this.tone(f, 0.22, { gain: 0.24, cutoff: 3000, attack: 0.016 });
                this.tone(f * 2, 0.3, { type: "sine", gain: 0.1, when: 0.02, cutoff: 3200, verb: 0.36 }); }
  bop()       { this.tone(760, 0.16, { type: "sine", gain: 0.26, cutoff: 2200, attack: 0.012 });
                this.noise(0.07, 0.06, 0, 1400, { cutoff: 2400, curve: 2.6 }); }
  stumble()   { this.tone(200, 0.24, { type: "sine", gain: 0.22, cutoff: 900, from: 1.35, attack: 0.01 });
                this.noise(0.14, 0.08, 0, 420, { cutoff: 1100, curve: 1.8 }); }
  splash()    { this.noise(0.34, 0.13, 0, 780, { cutoff: 2800, q: 0.6, curve: 1.3, verb: 0.3 });
                this.tone(280, 0.26, { type: "sine", gain: 0.13, cutoff: 900, from: 1.4 }); }
  dig()       { this.noise(0.2, 0.13, 0, 420, { cutoff: 1000, q: 0.7, curve: 1.4 });
                this.tone(150, 0.14, { type: "sine", gain: 0.1, cutoff: 500 }); }
  treasure()  { this.arp(523.25, [0, 4, 7, 12], { dur: 0.34, gap: 0.08, gain: 0.24 }); }
  cheer()     { this.arp(523.25, [0, 4, 7, 12, 16, 19], { dur: 0.5, gap: 0.105, gain: 0.22, cutoff: 2400 }); }
  // the one cue that fires on every menu tap, so it is the one most worth not
  // being a click: a soft 24ms swell, quieter than anything in a chapter
  ui()        { this.tone(660, 0.14, { type: "sine", gain: 0.16, cutoff: 2000, attack: 0.024, verb: 0.16 }); }
  // a friend joining the run (#306): two voices arriving a beat apart, because
  // what happened is that there are now two of you
  friend()    { this.arp(440, [0, 5, 9, 12], { dur: 0.32, gap: 0.085, gain: 0.22 }); }
  // the special move going off: a rising fifth, so it reads as something
  // starting rather than as one more collect
  ability()   { this.arp(392, [0, 7, 12], { dur: 0.26, gap: 0.065, gain: 0.24, cutoff: 2000 });
                this.noise(0.16, 0.05, 0, 1800, { cutoff: 3000, curve: 1.2, verb: 0.3 }); }
  // and coming back: quieter than anything the player *did*, because nobody
  // pressed anything to make it happen
  recharged() { this.tone(784, 0.16, { type: "sine", gain: 0.11, cutoff: 2000, verb: 0.34 });
                this.tone(1046.5, 0.2, { type: "sine", gain: 0.08, when: 0.07, cutoff: 2400, verb: 0.34 }); }

  /* ------------------------------------------------------------- music -- */

  /**
   * Each chapter gets its own little loop: a bass note, a pentatonic melody and
   * (except at bedtime) a soft shaker. Everything is generated, nothing is a file.
   *
   * Three changes for #305, all of them about the loop sounding *played* rather
   * than computed:
   *
   *   - **a tune, not a formula.** The melody used to be
   *     `SCALE[(s * 3 + floor(s / 8)) % 5]` — a sequence with no phrase, no
   *     repeat and no shape, which is why it wandered. Each theme now has a
   *     written motif that comes round again, so there is something to
   *     recognise.
   *   - **swing.** Offbeats land 18% of a step late. Perfectly even eighths are
   *     the most machine-like thing in a loop.
   *   - **accents.** The downbeat is loudest, the offbeat quietest, and every
   *     note is nudged by `tone`'s own jitter. A bar at one velocity is a
   *     metronome.
   */
  playTheme(name) {
    if (!this.ctx) return;
    if (this.theme === name && this.timer) return;
    this.stopMusic();
    this.theme = name;
    this.step = 0;

    // The motifs are written in scale degrees (indices into SCALE, `null` for a
    // rest), one per 16th, so each theme is a phrase somebody could hum back.
    const themes = {
      menu:      { root: 261.63, tempo: 300, mood: "bright",
                   motif: [0, null, 2, null, 4, 2, null, 1, 2, null, 4, null, 3, null, 2, null] },
      backyard:  { root: 293.66, tempo: 270, mood: "bright",
                   motif: [0, null, 1, 2, null, 4, null, 2, 1, null, 2, null, 0, null, null, 4] },
      creek:     { root: 246.94, tempo: 290, mood: "bright",
                   motif: [2, null, 4, null, 3, 2, null, 0, 2, null, 3, null, 4, null, 2, null] },
      hammerbarn:{ root: 329.63, tempo: 240, mood: "busy",
                   motif: [0, 2, null, 3, 2, null, 4, 3, 2, null, 1, 0, 2, null, 3, null] },
      beach:     { root: 220.00, tempo: 280, mood: "bright",
                   motif: [0, null, 2, null, 3, null, 4, 3, 2, null, 0, null, 2, null, 1, null] },
      // the lullaby's notes sit at 0, 5, 8 and 13: two of the four land on an
      // offbeat, so even the slowest loop in the game gets the swing (#305)
      sleepytime:{ root: 196.00, tempo: 460, mood: "sleepy",
                   motif: [0, null, null, null, null, 2, null, null, 1, null, null, null, null, 4, null, null] },
    };
    const cfg = themes[name] || themes.menu;
    const beat = cfg.tempo / 1000;

    const tick = () => {
      if (!this.ctx || this.muted) return;
      const s = this.step++;
      const bar = s % 16;
      // a hand does not hit the tick: every note is drawn a few milliseconds
      // late, and the offbeats a good deal later than that. The drift is a
      // share of the beat rather than a fixed number of milliseconds, so the
      // slow lullaby — whose only audible onsets are its downbeat plucks —
      // wanders as much as its tempo deserves instead of sitting on a grid.
      const drift = () => this.rand() * beat * 0.09;
      const swing = (bar % 2 === 1 ? beat * 0.18 : 0) + drift();

      // bass on the downbeats
      if (bar % 4 === 0) {
        const bassNote = [0, 0, 7, 5][(Math.floor(s / 4)) % 4];
        this.pluck(cfg.root / 2 * Math.pow(2, bassNote / 12), 0.5, 0.2, drift());
      }
      // melody
      if (cfg.mood === "sleepy") {
        const n = cfg.motif[bar];
        if (n !== null) {
          this.tone(cfg.root * 2 * Math.pow(2, SCALE[n] / 12), 1.5, {
            type: "sine", gain: 0.1, when: swing, attack: 0.09, cutoff: 1100,
            verb: 0.45, dest: this.musicGain,
          });
        }
      } else {
        const n = cfg.motif[bar];
        if (n !== null) {
          // an accent on the beat, a shrug on the and — the same eight notes at
          // one volume are a metronome
          const vel = bar % 4 === 0 ? 0.15 : bar % 2 === 0 ? 0.12 : 0.09;
          const oct = s % 32 >= 16 ? 2 : 1;   // the phrase comes round an octave up
          this.pluck(cfg.root * oct * Math.pow(2, SCALE[n] / 12), 0.36, vel, swing);
        }
        // shaker: quieter, and rolled off well below where fizz lives
        if (bar % 2 === 1) this.noise(0.06, 0.022, swing, 4200, { cutoff: 5200, q: 0.5, verb: 0.24, curve: 2.2 });
      }
    };

    tick();
    this.timer = setInterval(tick, cfg.tempo);
  }

  stopMusic() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.theme = null;
  }
}

export const sound = new Sound();
