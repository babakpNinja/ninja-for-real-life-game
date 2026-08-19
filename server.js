/*
 * A dependency-free static file server. The whole game is client-side, so this
 * only needs to hand out files from public/ and listen on $PORT for Railway.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const ROOT = path.join(__dirname, "public");
const PORT = process.env.PORT || 3000;

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  // robots.txt, whose only reader is a crawler, and which asks not to be
  // indexed: Google's spec wants text/plain and treats another type as no
  // file at all, so this one was silently doing nothing (#369).
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  // ...and the smaller copy of every character beside it. Served as
  // application/octet-stream a browser will not decode it into an <img>.
  ".webp": "image/webp",
  // the 209 recorded lines (#357). Chrome will sniff an octet-stream and play
  // it anyway; Safari will not, and the game falls back to the robot voice.
  ".mp3": "audio/mpeg",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
};

// What a half-copied deploy would get wrong. Counted once at boot — the files in
// a container never change under it — from the same structures the game loads,
// so reformatting a source file cannot move the numbers. 0 is the honest answer
// when something is missing; the uptime floor turns it into a failure.
const CONTENT = { characters: 0, chapters: 0 };

async function countContent() {
  try {
    const raw = fs.readFileSync(path.join(ROOT, "data/characters.json"), "utf8");
    CONTENT.characters = JSON.parse(raw).characters.length;
  } catch (err) {
    console.error("health: cannot count characters —", err.message);
  }
  try {
    const mod = await import(pathToFileURL(path.join(ROOT, "js/chapters.js")).href);
    CONTENT.chapters = mod.CHAPTERS.length;
  } catch (err) {
    console.error("health: cannot count chapters —", err.message);
  }
}

const server = http.createServer((req, res) => {
  // The belt to robots.txt's braces (#461). That file is only obeyed if a
  // crawler fetches it *and* it comes back as text/plain, which it did not for
  // as long as this deploy existed (#369) — a header depends on neither, and
  // rides on the responses (a 404, a directory-traversal 403) that no
  // `Disallow:` line covers. Set first, above every exit below — including the
  // 400s a request this cannot parse gets — so a route or a refusal added later
  // cannot forget it: Node merges headers set here with the object
  // passed to writeHead, and none of those repeat this name.
  res.setHeader("X-Robots-Tag", "noindex, nofollow");

  // A request line this cannot read is a 400, not the end of the process (#486).
  // `decodeURIComponent("%zz")` throws URIError and `new URL` throws on a Host
  // header that is not a hostname; there is nowhere above this to catch either,
  // so one malformed path from any scanner used to kill the container. Railway
  // restarts it in seconds, which is exactly why nothing here noticed: the
  // uptime check saw a demo that was up, and whoever was mid-click saw a page
  // that stopped loading. Both are parsed together because both are the same
  // answer — this server could not understand the request at all.
  let url, rel;
  try {
    url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    rel = decodeURIComponent(url.pathname);
  } catch {
    res.writeHead(400, { "Content-Type": "text/plain" });
    return res.end("Bad request");
  }

  if (url.pathname === "/api/health" || url.pathname === "/healthz") {
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    return res.end(JSON.stringify({
      status: "ok",
      // which commit is actually serving — Railway injects it at deploy time, and it
      // is the only thing that proves a push swapped the container rather than the
      // old one still answering
      revision: process.env.RAILWAY_GIT_COMMIT_SHA || process.env.DEPLOY_REVISION || null,
      // how long *this process* has been running, to a tenth of a second. Every
      // other number here describes the build and none of them moves when the
      // process dies and Railway starts another one — which is how a crash any
      // scanner could trigger (#486) stayed invisible for the life of a deploy.
      // A check that remembers this between runs can see a restart it slept
      // through (#487); tenths, so a test can watch it grow without sleeping a
      // whole second.
      uptime_s: Math.round(process.uptime() * 10) / 10,
      ...CONTENT,
    }));
  }

  // The third malformed shape, and the one that does not look like the other
  // two: a NUL byte survives decoding, and `fs.readFile` rejects it by throwing
  // *synchronously* out of the call below — same dead process, a different line.
  if (rel.includes("\0")) {
    res.writeHead(400, { "Content-Type": "text/plain" });
    return res.end("Bad request");
  }

  if (rel === "/") rel = "/index.html";
  // Resolved *underneath* ROOT rather than normalised as an absolute path, so
  // that the refusal below is the thing stopping a traversal rather than a
  // side-effect nobody tested (#486). `path.normalize("/../../etc/passwd")` is
  // "/etc/passwd" — an absolute path has nowhere above its root to go — so the
  // old `path.join(ROOT, …)` landed back inside ROOT whatever was asked, the
  // 403 was unreachable, and `/%2e%2e%2fpublic-not/secret` quietly became a 404
  // for a file under public/. Against `.${rel}` the `..` segments survive
  // resolution and the check answers them.
  const file = path.resolve(ROOT, `.${rel}`);
  // `startsWith(ROOT)` alone would also accept a sibling whose name merely
  // begins with it — /app/public-not is not /app/public — so the separator is
  // part of the question.
  if (file !== ROOT && !file.startsWith(ROOT + path.sep)) {
    res.writeHead(403, { "Content-Type": "text/plain" });
    return res.end("nope");
  }

  fs.readFile(file, (err, buf) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      return res.end("Not found");
    }
    const type = TYPES[path.extname(file).toLowerCase()] || "application/octet-stream";
    res.writeHead(200, {
      "Content-Type": type,
      // stated rather than chunked, so a truncated or substituted file can be
      // told apart from the one that was built (#332)
      "Content-Length": buf.length,
      "Cache-Control": file.endsWith("index.html") ? "no-cache" : "public, max-age=300",
    });
    res.end(buf);
  });
});

// count first, then listen: a health poll landing in the first milliseconds
// would otherwise read 0 chapters and be told the deploy is truncated
countContent().then(() => {
  server.listen(PORT, "0.0.0.0", () => {
    console.log(`Ana Bingo! listening on ${PORT} ` +
                `(${CONTENT.characters} characters, ${CONTENT.chapters} chapters)`);
  });
});
