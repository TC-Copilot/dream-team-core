const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const listeners = {};
const deleted = [];
let precached = [];
let waited;
let reply;

const context = {
  URL,
  Response,
  fetch: async () => ({ status: 200, clone() { return this; } }),
  self: {
    location: { origin: "http://127.0.0.1:9999" },
    addEventListener(type, callback) { listeners[type] = callback; },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  },
  caches: {
    async keys() { return ["dream-team-precache-v8", "dream-team-runtime-v8", "other-app-cache"]; },
    async delete(name) { deleted.push(name); return true; },
    async open(name) {
      return {
        async addAll(urls) { precached = [...urls]; },
        async put() {},
      };
    },
    async match() { return undefined; },
  },
};

const source = fs.readFileSync(path.join(__dirname, "..", "app", "static", "sw.js"), "utf8");
vm.runInNewContext(source, context);
assert.ok(listeners.message, "service worker message listener is registered");
assert.ok(listeners.activate, "service worker activation listener is registered");

(async () => {
  listeners.activate({ waitUntil(promise) { waited = promise; } });
  await waited;
  assert.deepStrictEqual(
    deleted.sort(),
    ["dream-team-precache-v8", "dream-team-runtime-v8"],
    "activation deletes only stale Dream Team caches",
  );
  assert.ok(!deleted.includes("other-app-cache"));

  deleted.length = 0;
  listeners.message({
    data: { type: "REFRESH_APP_CACHES" },
    ports: [{ postMessage(value) { reply = value; } }],
    waitUntil(promise) { waited = promise; },
  });
  await waited;
  assert.deepStrictEqual(
    deleted.sort(),
    ["dream-team-precache-v8", "dream-team-runtime-v8"],
    "only Dream Team caches are deleted",
  );
  assert.ok(!deleted.includes("other-app-cache"));
  assert.ok(precached.includes("/app.js"));
  assert.ok(precached.includes("/pwa.js"));
  assert.strictEqual(reply.ok, true);
  assert.strictEqual(reply.cacheVersion, "v11");
  console.log("[ok] service worker cache refresh");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
