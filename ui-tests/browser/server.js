// Deterministic static server for the real Sextant dashboard files.
// The browser tests intercept /api/** themselves, so this only serves the
// production index.html, app.css, and app.js from sextant_control/static.
const http = require("http");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const staticRoot = path.join(root, "sextant_control", "static");
const host = process.env.UI_HOST || "127.0.0.1";
const port = Number(process.env.UI_PORT || 4173);

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function resolveStatic(pathname) {
  if (pathname === "/" || pathname === "/index.html") {
    return path.join(staticRoot, "index.html");
  }
  if (!pathname.startsWith("/static/")) return null;
  const relative = path.normalize(decodeURIComponent(pathname.slice("/static/".length)));
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  const resolved = path.join(staticRoot, relative);
  if (!resolved.startsWith(staticRoot + path.sep)) return null;
  return resolved;
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, `http://${request.headers.host || `${host}:${port}`}`);
  if (url.pathname === "/favicon.ico") {
    response.writeHead(204);
    response.end();
    return;
  }
  const file = resolveStatic(url.pathname);
  if (!file) {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
    return;
  }
  fs.readFile(file, (error, data) => {
    if (error) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }
    response.writeHead(200, {
      "Content-Type": contentTypes[path.extname(file)] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    response.end(data);
  });
});

server.listen(port, host, () => {
  console.log(`UI harness listening on http://${host}:${port}`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
