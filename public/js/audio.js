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
    this.master.connect(this.ctx.destination);

    this.musicGain = this.ctx.createGain();
    this.musicGain.gain.value = 0.28;
    this.musicGain.connect(this.master);

    this.sfxGain = this.ctx.createGain();
    this.sfxGain.gain.value = 0.5;
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

  blip(freq, dur, type = "triangle", gain = 0.3, when = 0, dest = null) {
    if (!this.ctx || this.muted) return;
    const t0 = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(dest || this.sfxGain);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  /** Plucked string — the ukulele-ish backbone of the soundtrack. */
  pluck(freq, dur, gain, when, dest) {
    if (!this.ctx || this.muted) return;
    const t0 = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const osc2 = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    const filter = this.ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.setValueAtTime(2600, t0);
    filter.frequency.exponentialRampToValueAtTime(700, t0 + dur);
    osc.type = "triangle";
    osc2.type = "sine";
    osc.frequency.value = freq;
    osc2.frequency.value = freq * 2.002;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(filter);
    osc2.connect(filter);
    filter.connect(g).connect(dest || this.musicGain);
    osc.start(t0); osc2.start(t0);
    osc.stop(t0 + dur + 0.05); osc2.stop(t0 + dur + 0.05);
  }

  noise(dur, gain, when = 0, freq = 1200) {
    if (!this.ctx || this.muted) return;
    const t0 = this.ctx.currentTime + when;
    const len = Math.max(1, Math.floor(this.ctx.sampleRate * dur));
    const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    const filter = this.ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = freq;
    const g = this.ctx.createGain();
    g.gain.value = gain;
    src.connect(filter).connect(g).connect(this.sfxGain);
    src.start(t0);
  }

  /* --------------------------------------------------------------- sfx -- */

  jump()      { this.blip(520, 0.16, "triangle", 0.28); this.blip(760, 0.12, "sine", 0.18, 0.05); }
  collect(n)  { const f = 660 * Math.pow(1.0595, (n % 6) * 2); this.blip(f, 0.16, "triangle", 0.3); this.blip(f * 2, 0.12, "sine", 0.16, 0.04); }
  bop()       { this.blip(880, 0.1, "sine", 0.3); this.noise(0.06, 0.12, 0, 2400); }
  stumble()   { this.blip(230, 0.18, "sine", 0.22); this.noise(0.12, 0.1, 0, 500); }
  splash()    { this.noise(0.3, 0.18, 0, 900); this.blip(300, 0.2, "sine", 0.14); }
  dig()       { this.noise(0.18, 0.16, 0, 600); }
  treasure()  { [0, 4, 7, 12].forEach((n, i) => this.blip(523.25 * Math.pow(2, n / 12), 0.3, "triangle", 0.26, i * 0.07)); }
  cheer()     { [0, 4, 7, 12, 16, 19].forEach((n, i) => this.blip(523.25 * Math.pow(2, n / 12), 0.45, "triangle", 0.24, i * 0.1)); }
  ui()        { this.blip(700, 0.08, "sine", 0.2); }

  /* ------------------------------------------------------------- music -- */

  /**
   * Each chapter gets its own little loop: a bass note, a pentatonic melody and
   * (except at bedtime) a soft shaker. Everything is generated, nothing is a file.
   */
  playTheme(name) {
    if (!this.ctx) return;
    if (this.theme === name && this.timer) return;
    this.stopMusic();
    this.theme = name;
    this.step = 0;

    const themes = {
      menu:      { root: 261.63, tempo: 300, mood: "bright" },
      backyard:  { root: 293.66, tempo: 270, mood: "bright" },
      creek:     { root: 246.94, tempo: 290, mood: "bright" },
      hammerbarn:{ root: 329.63, tempo: 240, mood: "busy" },
      beach:     { root: 220.00, tempo: 280, mood: "bright" },
      sleepytime:{ root: 196.00, tempo: 460, mood: "sleepy" },
    };
    const cfg = themes[name] || themes.menu;

    const tick = () => {
      if (!this.ctx || this.muted) return;
      const s = this.step++;
      const bar = s % 16;

      // bass on the downbeats
      if (bar % 4 === 0) {
        const bassNote = [0, 0, 7, 5][(Math.floor(s / 4)) % 4];
        this.pluck(cfg.root / 2 * Math.pow(2, bassNote / 12), 0.5, 0.22, 0);
      }
      // melody
      if (cfg.mood === "sleepy") {
        if (bar % 4 === 2) {
          const n = SCALE[(Math.floor(s / 3)) % SCALE.length];
          this.pluck(cfg.root * 2 * Math.pow(2, n / 12), 1.4, 0.1, 0);
        }
      } else {
        const pattern = cfg.mood === "busy"
          ? [1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1]
          : [1, 0, 1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0];
        if (pattern[bar]) {
          const n = SCALE[(s * 3 + Math.floor(s / 8)) % SCALE.length];
          const oct = bar > 8 ? 2 : 1;
          this.pluck(cfg.root * oct * Math.pow(2, n / 12), 0.34, 0.13, 0);
        }
        // shaker
        if (bar % 2 === 1) this.noise(0.05, 0.035, 0, 6000);
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
