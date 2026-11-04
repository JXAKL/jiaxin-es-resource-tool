"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const { DEFAULTS, HttpSession, StateStore, parseLinks, sanitize, withRetries, httpError, retryAfter } = require("../organizer/downloads/js/transfer");
const { collectFiles, normalize, isDownloadUrl, GoFileClient } = require("../organizer/downloads/js/gofile");
const payload = Buffer.alloc(128 * 1024, 0x57);
const md5 = crypto.createHash("md5").update(payload).digest("hex");

test('Pixeldrain explicit verification restriction does not auto-retry', async () => {
  const {Readable}=require('node:stream');
  const {responseError}=require('../organizer/downloads/js/transfer');
  const response=Readable.from([Buffer.from(JSON.stringify({value:'file_rate_limited_captcha_required',message:'hotlink subscription'}))]);
  response.statusCode=403;response.headers={'content-type':'application/json'};
  const error=await responseError(response,'https://pixeldrain.com/api/file/fixture');
  assert.equal(error.code,'SITE_VERIFICATION_REQUIRED');
  let attempts=0;
  await assert.rejects(withRetries(async()=>{attempts++;throw error;}),{code:error.code});
  assert.equal(attempts,1);
});

test('JSON funscript downloads are allowed but HTML errors remain rejected', async t => {
  const script=Buffer.from('{"version":"1.0","actions":[{"at":0,"pos":50}]}');
  const f=await fixture(t,(req,res)=>{res.writeHead(200,{'content-type':'application/json','content-length':script.length});res.end(script);});
  const dest=path.join(f.dir,'fixture.funscript');
  await f.session.download({url:f.url,dest,expectedSize:script.length});
  assert.deepEqual(fs.readFileSync(dest),script);
  await assert.rejects(f.session.download({url:f.url,dest:f.dest,expectedSize:script.length}),{code:'NOT_FILE'});
});
async function fixture(t, handler, options = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gofile-regression-"));
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const session = new HttpSession({ ...options, progressIntervalMs: 20 });
  t.after(async () => { session.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); fs.rmSync(dir, { recursive: true }); });
  return { dir, session, url: `http://127.0.0.1:${server.address().port}`, dest: path.join(dir, "video.mp4") };
}
function full(req, res) { res.writeHead(200, { "Content-Type": "video/mp4", "Content-Length": payload.length }); res.end(payload); }

test("redirect to 3358-byte HTML is rejected; no final video or completion", async t => {
  const f = await fixture(t, (req, res) => {
    if (req.url === "/start") { res.writeHead(302, { Location: "/html" }); res.end(); }
    else { res.writeHead(200, { "Content-Type": "text/html" }); res.end("<!doctype html>".padEnd(3358, " ")); }
  });
  await assert.rejects(f.session.download({ url: f.url + "/start", dest: f.dest, expectedSize: 222709036 }), { code: "NOT_FILE" });
  assert.equal(fs.existsSync(f.dest), false);
});
test("relative redirect preserves expected size, even application/octet-stream", async t => {
  const f = await fixture(t, (req, res) => {
    if (req.url === "/start") { res.writeHead(302, { Location: "file" }); res.end(); }
    else { res.writeHead(200, { "Content-Length": 10 }); res.end("0123456789"); }
  });
  await assert.rejects(f.session.download({ url: f.url + "/start", dest: f.dest, expectedSize: 5000 }), { code: "SIZE_MISMATCH" });
  assert.equal(fs.existsSync(f.dest), false);
});
test("unknown-length fake HTML body never becomes a video", async t => {
  const f = await fixture(t, (req, res) => { res.writeHead(200, { "Content-Type": "application/octet-stream" }); res.write("<!doctype html>"); res.end("x".repeat(1000)); });
  await assert.rejects(f.session.download({ url: f.url, dest: f.dest, expectedSize: 1015 }), { code: "NOT_FILE" });
  assert.equal(fs.existsSync(f.dest), false);
});
test("normal transfer validates MD5 and sends progress", async t => {
  const f = await fixture(t, full), progress = [];
  await f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length, md5, onProgress: p => progress.push(p) });
  assert.deepEqual(fs.readFileSync(f.dest), payload);
  assert.equal(fs.existsSync(f.dest + ".part"), false);
  assert.equal(progress.at(-1).downloaded, payload.length);
});
test("resume uses Range and appends exactly the missing bytes", async t => {
  const f = await fixture(t, (req, res) => {
    assert.equal(req.headers.range, "bytes=4096-");
    res.writeHead(206, { "Content-Range": `bytes 4096-${payload.length - 1}/${payload.length}`, "Content-Length": payload.length - 4096 }); res.end(payload.subarray(4096));
  });
  fs.writeFileSync(f.dest + ".part", payload.subarray(0, 4096));
  await f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length, md5 });
  assert.deepEqual(fs.readFileSync(f.dest), payload);
});
test("server ignores Range: 200 truncates rather than corrupting output", async t => {
  const f = await fixture(t, full);
  fs.writeFileSync(f.dest + ".part", payload.subarray(0, 4096));
  await f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length, md5 });
  assert.deepEqual(fs.readFileSync(f.dest), payload);
});
test("wrong Content-Range leaves original partial untouched", async t => {
  const f = await fixture(t, (req, res) => { res.writeHead(206, { "Content-Range": `bytes 0-${payload.length - 1}/${payload.length}` }); res.end(payload); });
  fs.writeFileSync(f.dest + ".part", payload.subarray(0, 4096));
  await assert.rejects(f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length }), { code: "RANGE_MISMATCH" });
  assert.equal(fs.statSync(f.dest + ".part").size, 4096);
});
test("connection interruption retains part, then a new session resumes", async t => {
  let first = true;
  const f = await fixture(t, (req, res) => {
    if (first) { first = false; res.writeHead(200, { "Content-Length": payload.length }); res.write(payload.subarray(0, 8192)); setTimeout(() => res.destroy(), 30); }
    else { const from = Number(req.headers.range.match(/\d+/)[0]); res.writeHead(206, { "Content-Range": `bytes ${from}-${payload.length - 1}/${payload.length}` }); res.end(payload.subarray(from)); }
  });
  await assert.rejects(f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length }));
  assert.equal(fs.existsSync(f.dest), false);
  assert.ok(fs.statSync(f.dest + ".part").size > 0);
  const next = new HttpSession();
  try { await next.download({ url: f.url, dest: f.dest, expectedSize: payload.length, md5 }); } finally { next.close(); }
  assert.deepEqual(fs.readFileSync(f.dest), payload);
});
test("read watchdog fires even with no new chunks", async t => {
  const f = await fixture(t, (req, res) => { res.writeHead(200, { "Content-Length": payload.length }); res.flushHeaders(); }, { readTimeoutMs: 70 });
  await assert.rejects(f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length }), { code: "READ_TIMEOUT" });
  assert.equal(fs.existsSync(f.dest), false);
});
test("continuous trickle triggers low-speed watchdog", async t => {
  const f = await fixture(t, (req, res) => {
    res.writeHead(200, { "Content-Length": payload.length });
    const timer = setInterval(() => res.write(Buffer.alloc(5)), 10); res.on("close", () => clearInterval(timer));
  }, { slowWindowMs: 80, minBytesPerSecond: 100000, readTimeoutMs: 500 });
  await assert.rejects(f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length }), { code: "LOW_SPEED" });
});
test("429 Retry-After and finite 5xx backoff", async () => {
  let calls = 0; const waits = [];
  await assert.rejects(withRetries(async () => { calls++; throw httpError(429, { "retry-after": "7" }); }, { sleep: async ms => waits.push(ms) }), { status: 429 });
  assert.equal(calls, 4); assert.deepEqual(waits, [7000, 7000, 7000]);
  const errors = [];
  await assert.rejects(withRetries(async () => { throw httpError(503); }, { sleep: async ms => errors.push(ms) }), { status: 503 });
  assert.deepEqual(errors, [5000, 15000, 30000]);
  assert.equal(retryAfter(new Date(20000).toUTCString(), 10000), 10000);
});
test("expired URL refresh is bounded and can recover", async () => {
  const attempts = [];
  const result = await withRetries(async attempt => { attempts.push(attempt); if (!attempt) throw httpError(403); return "fresh"; }, { sleep: async () => {} });
  assert.equal(result, "fresh"); assert.deepEqual(attempts, [0, 1]);
  let count = 0;
  await assert.rejects(withRetries(async () => { count++; throw httpError(404); }, { sleep: async () => {} }));
  assert.equal(count, 2);
});
test("nested folders maintain response order; malformed response fails", async () => {
  const file = id => ({ type: "file", id, name: id + ".mp4", size: 3 });
  const root = { type: "folder", id: "root", name: "Root", children: { z: file("z"), sub: { type: "folder", id: "sub", name: "Sub" }, a: file("a") } };
  const files = await collectFiles(root, async () => ({ type: "folder", id: "sub", name: "Sub", childs: { b: file("b") } }));
  assert.deepEqual(files.map(f => f.id), ["z", "b", "a"]);
  assert.deepEqual(files[1].dirs, ["Root", "Sub"]);
  assert.throws(() => normalize({ status: "error-notFound" }), { code: "API_ERROR" });
});
test("URL filtering, reserved Windows names and duplicate names", async t => {
  assert.deepEqual(parseLinks("[https://gofile.io/d/A?q=1](https://gofile.io/d/A?q=1)\nhttps://bad.test/gofile.io/d/B\nhttps://iwara.tv/video/a"), ["https://gofile.io/d/A?q=1"]);
  assert.equal(sanitize("CON.mp4"), "_CON.mp4"); assert.equal(sanitize('a<>:"/\\|?*.mp4'), "a_________.mp4");
  const f = await fixture(t, full), state = new StateStore(f.dir);
  const a = state.register("https://gofile.io/d/A", { id: "a", name: "same.mp4", size: 5, dirs: [] });
  const b = state.register("https://gofile.io/d/B", { id: "b", name: "same.mp4", size: 5, dirs: [] });
  assert.notEqual(a.local_path, b.local_path);
  assert.equal(isDownloadUrl("https://gofile.io/d/A"), false);
});
test("streaming larger file validates without buffering whole transfer", async t => {
  const bytes = 32 * 1024 * 1024;
  const f = await fixture(t, async (req, res) => {
    res.writeHead(200, { "Content-Length": bytes });
    for (let n = 0; n < bytes / payload.length; n++) if (!res.write(payload)) await new Promise(resolve => res.once("drain", resolve));
    res.end();
  });
  await f.session.download({ url: f.url, dest: f.dest, expectedSize: bytes });
  assert.equal(fs.statSync(f.dest).size, bytes);
});

test("browser parser waits for actual API response and reuses scoped cookies", async () => {
  const { EventEmitter } = require("node:events");
  const page = new EventEmitter();
  let delivered = false;
  page.goto = async () => {
    setTimeout(() => { delivered = true; page.emit("response", { url: () => "https://api.gofile.io/contents/A?page=1", status: () => 200,
      json: async () => ({ status: "ok", data: { id: "id", type: "file", name: "video.mp4", size: 7 } }) }); }, 30);
    return { status: () => 200 };
  };
  page.evaluate = async () => "browser-user-agent";
  const urls = [];
  const client = new GoFileClient({ cookies: async url => { urls.push(url); return [{ name: "accountToken", value: "test-only-cookie" }]; } }, page, { timeoutMs: 500, log() {} });
  const node = await client.getContent("https://gofile.io/d/A");
  assert.equal(delivered, true); assert.equal(node.size, 7); assert.equal(page.listenerCount("response"), 0);
  const link = "https://store3.gofile.io/download/web/id/video.mp4";
  assert.deepEqual(await client.headersFor(link), { "User-Agent": "browser-user-agent", Referer: "https://gofile.io/", Cookie: "accountToken=test-only-cookie" });
  assert.deepEqual(urls, [link]);
  await assert.rejects(client.headersFor("https://evil.test/file"), { code: "NOT_FILE" });
});
test("no response is a parsing failure, not an empty success", async () => {
  const { EventEmitter } = require("node:events");
  const page = new EventEmitter(); page.goto = async () => ({ status: () => 200 });
  const client = new GoFileClient({}, page, { timeoutMs: 25, log() {} });
  await assert.rejects(client.getContent("https://gofile.io/d/A"), { code: "PARSE_TIMEOUT" });
  assert.equal(page.listenerCount("response"), 0);
});
test("bad MD5 preserves evidence but does not create final file", async t => {
  const f = await fixture(t, full);
  await assert.rejects(f.session.download({ url: f.url, dest: f.dest, expectedSize: payload.length, md5: "0".repeat(32) }), { code: "CHECKSUM_MISMATCH" });
  assert.equal(fs.existsSync(f.dest), false);
  assert.ok(fs.readdirSync(f.dir).some(n => n.includes("checksum-failed")));
});
