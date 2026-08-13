/*
 * A dependency-free static file server. The whole game is client-side, so this
 * only needs to hand out files from public/ and listen on $PORT for Railway.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "public");
const PORT = process.env.PORT || 3000;

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
};

// What a half-copied deploy would get wrong. Counted once at boot: the files in
// a container never change under it, and health is polled every push.
const CONTENT = (() => {
  const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");
  const count = (fn) => {
    try {
      return fn();
    } catch (err) {
      return 0;   // 0 is the honest answer; the uptime floor turns it into a failure
    }
  };
  return {
    characters: count(() => JSON.parse(read("data/characters.json")).characters.length),
    chapters: count(() => (read("js/chapters.js").match(/^ {4}id: "/gm) || []).length),
  };
})();

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  if (url.pathname === "/api/health" || url.pathname === "/healthz") {
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    return res.end(JSON.stringify({
      status: "ok",
      // which commit is actually serving — Railway injects it at deploy time, and it
      // is the only thing that proves a push swapped the container rather than the
      // old one still answering
      revision: process.env.RAILWAY_GIT_COMMIT_SHA || process.env.DEPLOY_REVISION || null,
      ...CONTENT,
    }));
  }

  let rel = decodeURIComponent(url.pathname);
  if (rel === "/") rel = "/index.html";
  const file = path.join(ROOT, path.normalize(rel));
  if (!file.startsWith(ROOT)) {
    res.writeHead(403);
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
      "Cache-Control": file.endsWith("index.html") ? "no-cache" : "public, max-age=300",
    });
    res.end(buf);
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`For Real Life! listening on ${PORT}`);
});
