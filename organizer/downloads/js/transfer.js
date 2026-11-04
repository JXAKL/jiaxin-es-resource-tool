"use strict";

// Transport and durable state are deliberately independent of browser/API details.
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const https = require("node:https");
const crypto = require("node:crypto");
const { Transform } = require("node:stream");
const { pipeline } = require("node:stream/promises");
const { setTimeout: delay } = require("node:timers/promises");
const {rateLimit}=require('./rate-limit');

const DEFAULTS = Object.freeze({
  retryDelays: [5000, 15000, 30000], connectTimeoutMs: 30000,
  readTimeoutMs: 30000, slowWindowMs: 60000, minBytesPerSecond: 16384,
  maxRedirects: 8, progressIntervalMs: 1000, rateLimitPauseMs: 120000,
});

class DownloadError extends Error {
  constructor(code, message, options = {}) {
    super(message); this.name = "DownloadError"; this.code = code;
    Object.assign(this, options);
  }
}
function classify(error) {
  if (error instanceof DownloadError) return error;
  const names = { ENOTFOUND: "DNS_ERROR", EAI_AGAIN: "DNS_ERROR", ECONNRESET: "CONNECTION_RESET",
    ETIMEDOUT: "CONNECT_TIMEOUT", ECONNREFUSED: "CONNECTION_REFUSED", ABORT_ERR: "STOPPED",
    ERR_STREAM_PREMATURE_CLOSE: "CONNECTION_RESET" };
  const code = names[error.code] || error.code || "UNKNOWN_ERROR";
  return new DownloadError(code, error.message, { retryable: ["DNS_ERROR", "CONNECTION_RESET",
    "CONNECT_TIMEOUT", "CONNECTION_REFUSED"].includes(code) });
}
function retryAfter(value, now = Date.now()) {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  if (Number.isFinite(n) && n >= 0) return n * 1000;
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - now) : null;
}
function httpError(status, headers = {}) {
  return new DownloadError(`HTTP_${status}`, `HTTP ${status}`, {
    status, retryable: status === 408 || status === 429 || status >= 500,
    refresh: status === 401 || status === 403 || status === 404 || status === 410,
    retryAfterMs: retryAfter(headers["retry-after"]),
  });
}
async function responseError(response, url) {
  const generic = httpError(response.statusCode, response.headers);
  // Preserve the site's stated restriction, instead of refreshing a CAPTCHA/paywall.
  if (response.statusCode !== 403 || new URL(url).hostname !== 'pixeldrain.com' ||
      !/application\/json/i.test(response.headers['content-type'] || '')) return generic;
  const chunks=[]; let size=0;
  const timer=setTimeout(()=>response.destroy(),5000);
  try {
    for await (const chunk of response) { size+=chunk.length;if(size>8192)return generic;chunks.push(chunk); }
    const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (/captcha_required|hotlink|subscription/i.test(String(body.value)+' '+String(body.message)))
      return new DownloadError('SITE_VERIFICATION_REQUIRED','Pixeldrain 要求在官网完成验证，或满足其订阅条件；请用“打开所选网站登录”查看。本项不会自动反复请求。',{status:403});
  } catch {} finally {clearTimeout(timer);}
  return generic;
}
function sanitize(name) {
  let s = String(name || "unnamed").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim().replace(/[. ]+$/g, "");
  if (/^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/i.test(s)) s = "_" + s;
  const ext = path.extname(s).slice(0, 20);
  if (s.length > 160) s = s.slice(0, 160 - ext.length) + ext;
  return s || "unnamed";
}
function contentId(raw) {
  try {
    const u = new URL(raw);
    if (u.protocol !== "https:" || !["gofile.io", "www.gofile.io"].includes(u.hostname.toLowerCase())) return null;
    return /^\/d\/([A-Za-z0-9_-]+)\/?$/.exec(u.pathname)?.[1] || null;
  } catch { return null; }
}
function parseLinks(text) {
  const matches = text.match(/https?:\/\/[^\s<>"'\[\]()]+/gi) || [];
  return [...new Set(matches.filter(u => contentId(u)))];
}
function sizeOf(file) { try { return fs.statSync(file).size; } catch (e) { if (e.code === "ENOENT") return 0; throw e; } }
function contained(root, target) {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return relative !== "" && !relative.startsWith(".." + path.sep) && relative !== ".." && !path.isAbsolute(relative);
}
function preserve(file, label) {
  if (!fs.existsSync(file)) return null;
  const backup = `${file}.${label}-${Date.now()}-${crypto.randomBytes(3).toString("hex")}`;
  fs.renameSync(file, backup); return backup;
}
async function checksum(file, expected) {
  if (!expected) return true;
  const hash = crypto.createHash("md5");
  for await (const data of fs.createReadStream(file)) hash.update(data);
  return hash.digest("hex").toLowerCase() === expected.toLowerCase();
}
async function validFile(file, expected, md5) {
  return fs.existsSync(file) && sizeOf(file) === expected && await checksum(file, md5);
}

class StateStore {
  constructor(root, filename = "gofile_download_state.json") {
    this.root = path.resolve(root); fs.mkdirSync(this.root, { recursive: true });
    this.file = path.join(this.root, filename);
    this.data = fs.existsSync(this.file) ? JSON.parse(fs.readFileSync(this.file, "utf8")) : { version: 1, files: {}, tasks: {} };
    if (this.data.version !== 1 || !this.data.files || !this.data.tasks) throw new Error("GoFile 状态文件格式不兼容，请保留文件并检查");
  }
  save() {
    const temp = this.file + ".tmp";
    const fd = fs.openSync(temp, "w");
    try { fs.writeFileSync(fd, JSON.stringify(this.data, null, 2), "utf8"); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    fs.renameSync(temp, this.file);
  }
  task(url, changes) {
    const now = new Date().toISOString();
    const t = this.data.tasks[url] ||= { source_url: url, platform: "gofile", content_id: contentId(url), created_at: now, status: "PENDING", file_ids: [] };
    Object.assign(t, changes, { updated_at: now }); this.save(); return t;
  }
  register(url, info) {
    const now = new Date().toISOString();
    let row = this.data.files[info.id];
    if (row && !contained(this.root, row.local_path)) throw new Error("状态文件中的保存路径超出下载目录");
    if (row && (row.expected_size !== info.size || row.md5 && info.md5 && row.md5 !== info.md5)) {
      preserve(row.local_path, "previous-version"); preserve(row.local_path + ".part", "previous-version");
      row.status = "PENDING";
    }
    if (!row) {
      let dest = path.join(this.root, ...info.dirs.map(sanitize), sanitize(info.name));
      const reserved = new Set(Object.values(this.data.files).map(x => x.local_path.toLowerCase()));
      const ext = path.extname(dest), base = dest.slice(0, dest.length - ext.length);
      if (reserved.has(dest.toLowerCase()) || fs.existsSync(dest) && !info.md5) dest = `${base} [${sanitize(info.id)}]${ext}`;
      let suffix = 2;
      while (reserved.has(dest.toLowerCase()) || fs.existsSync(dest) && !info.md5) dest = `${base} [${sanitize(info.id)}-${suffix++}]${ext}`;
      if (!contained(this.root, dest)) throw new Error("非法保存路径");
      row = this.data.files[info.id] = { source_url: url, platform: "gofile", content_id: contentId(url),
        file_id: info.id, filename: info.name, local_path: dest, local_size: 0, status: "PENDING", retry_count: 0, error: null, created_at: now };
    }
    Object.assign(row, { expected_size: info.size, md5: info.md5 || null, updated_at: now });
    this.save(); return row;
  }
  update(row, values) { Object.assign(row, values, { updated_at: new Date().toISOString() }); this.save(); }
  async completed(url) {
    const task = this.data.tasks[url];
    if (!task || !["COMPLETED", "SKIPPED"].includes(task.status) || !task.file_ids.length) return false;
    for (const id of task.file_ids) {
      const row = this.data.files[id];
      if (!row || !contained(this.root, row.local_path) || !["COMPLETED", "SKIPPED"].includes(row.status) ||
          !await validFile(row.local_path, row.expected_size, row.md5)) return false;
    }
    return true;
  }
}

class HttpSession {
  constructor({ headersFor = async () => ({}), ...config } = {}) {
    this.config = { ...DEFAULTS, ...config }; this.headersFor = headersFor;
    this.agents = { "http:": new http.Agent({ keepAlive: true, maxSockets: 1 }),
      "https:": new https.Agent({ keepAlive: true, maxSockets: 1 }) };
    if(config.proxy) {
      Object.values(this.agents).forEach(a=>a.destroy());
      const {HttpProxyAgent,HttpsProxyAgent}=require('./vendor/network.cjs');
      this.agents={'http:':new HttpProxyAgent(config.proxy,{keepAlive:true,maxSockets:1}),
                   'https:':new HttpsProxyAgent(config.proxy,{keepAlive:true,maxSockets:1})};
    }
  }
  close() { Object.values(this.agents).forEach(a => a.destroy()); }
  async open(raw, extra, signal, redirect = 0) {
    signal?.throwIfAborted();
    await this.config.beforeRequest?.();
    const url = new URL(raw);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new DownloadError("INVALID_URL", "非法下载地址");
    const headers = { ...await this.headersFor(url.href), "Accept-Encoding": "identity", ...extra };
    const response = await new Promise((resolve, reject) => {
      const req = (url.protocol === "https:" ? https : http).get(url, { headers, signal, agent: this.agents[url.protocol] }, resolve);
      const timer = setTimeout(() => req.destroy(new DownloadError("CONNECT_TIMEOUT", "连接或响应头超时", { retryable: true })), this.config.connectTimeoutMs);
      req.once("response", () => clearTimeout(timer)); req.once("error", e => { clearTimeout(timer); reject(e); });
    });
    // Attach immediately so a reset between headers and pipeline is never unhandled.
    response.on("error", () => {});
    if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
      const location = response.headers.location; response.destroy();
      if (!location || redirect >= this.config.maxRedirects) throw new DownloadError("REDIRECT_ERROR", "重定向过多或缺少目标");
      const next = new URL(location, url);
      if (url.protocol === "https:" && next.protocol !== "https:") throw new DownloadError("UNSAFE_REDIRECT", "拒绝 HTTPS 降级重定向");
      // Rebuild cookies for the new origin; file metadata lives outside redirects.
      return this.open(next.href, extra, signal, redirect + 1);
    }
    return response;
  }
  async download({ url, dest, expectedSize, md5, validator, onValidator = () => {}, onProgress = () => {}, onLog = () => {}, signal }) {
    if (!Number.isSafeInteger(expectedSize) || expectedSize < 0) throw new DownloadError("INVALID_SIZE", "缺少精确文件大小，拒绝标记完成");
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    const part = dest + ".part";
    let offset = sizeOf(part);
    if (offset > expectedSize) { onLog(`保留超长断点文件：${preserve(part, "invalid")}`); offset = 0; }
    // A fully sized .part still needs a successful HTTP response (or hash), never existence alone.
    if (offset === expectedSize && fs.existsSync(part)) {
      if (md5 && await checksum(part, md5)) { fs.renameSync(part, dest); return; }
      onLog(`保留未经完整确认的断点文件：${preserve(part, "unverified")}`); offset = 0;
    }
    const headers = offset ? { Range: `bytes=${offset}-`, ...(validator ? { "If-Range": validator } : {}) } : {};
    const res = await this.open(url, headers, signal);
    try {
      if (![200, 206].includes(res.statusCode)) throw await responseError(res, url);
      const mime = String(res.headers["content-type"] || "").toLowerCase();
      const textFile = /\.(html?|txt|json|xml|csv|md)$/i.test(dest);
      if (!textFile && (/text\/html|application\/xhtml\+xml/.test(mime) || /application\/json/.test(mime) && !/\.funscript$/i.test(dest))) throw new DownloadError("NOT_FILE", "服务器返回网页/JSON，未获得文件内容", { refresh: true });
      if (res.headers["content-encoding"] && res.headers["content-encoding"] !== "identity") throw new DownloadError("CONTENT_ENCODING", "响应被压缩，无法安全续传");
      if (res.statusCode === 206) {
        const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(res.headers["content-range"] || "");
        if (!range || +range[1] !== offset || +range[2] !== expectedSize - 1 || +range[3] !== expectedSize)
          throw new DownloadError("RANGE_MISMATCH", "Content-Range 与断点/远程大小不一致");
      } else if (offset) {
        onLog("Range 不支持或文件已变化（HTTP 200），从头写入 .part，禁止追加"); offset = 0;
      }
      const length = res.headers["content-length"];
      if (length !== undefined && Number(length) !== expectedSize - offset)
        throw new DownloadError("SIZE_MISMATCH", `响应长度 ${length} 与预期剩余 ${expectedSize - offset} 不一致`, { refresh: true });
      const etag = res.headers.etag;
      const nextValidator = etag && !etag.startsWith("W/") ? etag : res.headers["last-modified"];
      if (res.statusCode === 206 && validator && nextValidator && nextValidator !== validator)
        throw new DownloadError("REMOTE_CHANGED", "续传时远程文件校验标识变化");
      onValidator(nextValidator || null);
      let received = offset, lastBytes = offset, lastAt = Date.now(), lastData = Date.now(), windowBytes = offset, windowAt = lastAt;
      let prefix = Buffer.alloc(0), prefixChecked = offset > 0, watchdogError = null;
      const emit = () => {
        const now = Date.now(), speed = (received - lastBytes) * 1000 / Math.max(1, now - lastAt);
        onProgress({ downloaded: received, total: expectedSize, speed, eta: speed > 0 ? Math.ceil((expectedSize - received) / speed) : null });
        lastAt = now; lastBytes = received;
      };
      onProgress({ downloaded: offset, total: expectedSize, speed: 0, eta: null });
      const checkPrefix = () => {
        if (!textFile && /^\s*(?:<!doctype\s+html|<html|<\?xml)/i.test(prefix.toString("utf8")))
          throw new DownloadError("NOT_FILE", "文件内容是错误网页，已拒绝保存", { refresh: true });
      };
      const monitor = new Transform({
        transform(chunk, encoding, callback) {
          try {
            lastData = Date.now(); received += chunk.length;
            if (received > expectedSize) throw new DownloadError("SIZE_MISMATCH", "响应超过远程文件大小");
            if (!prefixChecked) {
              prefix = Buffer.concat([prefix, chunk]);
              if (prefix.length < 512 && received < expectedSize) return callback();
              checkPrefix(); prefixChecked = true; this.push(prefix); prefix = Buffer.alloc(0);
            } else this.push(chunk);
            callback();
          } catch (e) { callback(e); }
        },
        flush(callback) { try { if (prefix.length) { checkPrefix(); this.push(prefix); } callback(); } catch (e) { callback(e); } },
      });
      const limiter=this.config.rateLimit>0?rateLimit(this.config.rateLimit,signal):null;
      const timer = setInterval(() => {
        const now = Date.now(); emit();
        if (limiter?.waiting) {lastData=now;windowAt=now;windowBytes=received;return;}
        if (now - lastData >= this.config.readTimeoutMs) watchdogError = new DownloadError("READ_TIMEOUT", "读取超时，无新数据", { retryable: true });
        else if (this.config.slowWindowMs > 0 && now - windowAt >= this.config.slowWindowMs) {
          const speed = (received - windowBytes) * 1000 / (now - windowAt);
          const minimum=this.config.rateLimit>0?Math.min(this.config.minBytesPerSecond,this.config.rateLimit/2):this.config.minBytesPerSecond;
          if (speed < minimum) watchdogError = new DownloadError("LOW_SPEED", `持续低速 ${Math.round(speed)} B/s`, { retryable: true });
          windowAt = now; windowBytes = received;
        }
        if (watchdogError) res.destroy(watchdogError);
      }, this.config.progressIntervalMs);
      try {
        await pipeline(res, ...(limiter?[limiter]:[]), monitor, fs.createWriteStream(part, { flags: offset ? "a" : "w" }), { signal });
        if (!res.complete || received !== expectedSize || sizeOf(part) !== expectedSize)
          throw new DownloadError("SIZE_MISMATCH", `本地 ${sizeOf(part)} / 预期 ${expectedSize} 字节`, { retryable: true });
        if (!await checksum(part, md5)) {
          onLog(`保留校验失败文件：${preserve(part, "checksum-failed")}`);
          throw new DownloadError("CHECKSUM_MISMATCH", "MD5 校验失败", { retryable: true });
        }
        signal?.throwIfAborted();
        const fd = fs.openSync(part, "r+"); try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
        if (fs.existsSync(dest)) onLog(`保留原有未完成文件：${preserve(dest, "invalid")}`);
        fs.renameSync(part, dest); emit();
      } catch (e) { throw watchdogError || e; }
      finally { clearInterval(timer); }
    } finally { res.destroy(); }
  }
}

async function withRetries(operation, { config = DEFAULTS, signal, onRetry = () => {}, sleep = (ms) => delay(ms, undefined, { signal }) } = {}) {
  let refreshed = false;
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted();
    try { return await operation(attempt); }
    catch (raw) {
      const e = classify(raw);
      if (signal?.aborted || e.code === "STOPPED") throw e;
      const refresh = e.refresh && !refreshed;
      if ((!e.retryable && !refresh) || attempt >= config.retryDelays.length) throw e;
      refreshed ||= refresh;
      const wait = e.status === 429 ? (e.retryAfterMs ?? Math.max(5000, config.retryDelays[attempt]) * 2 ** attempt) : config.retryDelays[attempt];
      onRetry(e, attempt + 1, wait); await sleep(wait);
    }
  }
}

module.exports = { DEFAULTS, DownloadError, classify, retryAfter, httpError, responseError, sanitize, contentId, parseLinks,
  sizeOf, contained, preserve, validFile, StateStore, HttpSession, withRetries };
