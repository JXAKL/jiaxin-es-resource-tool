"use strict";
const { DownloadError, httpError, contentId, sanitize } = require("./transfer");
const SITE = "https://gofile.io";
const API_HOST = "api.gofile.io";
function isDownloadUrl(raw, id) {
  try {
    const u = new URL(raw);
    return u.protocol === "https:" && (u.hostname === "gofile.io" || u.hostname.endsWith(".gofile.io")) &&
      u.pathname.startsWith("/download/") && (!id || u.pathname.split("/").includes(id));
  } catch { return false; }
}
function normalize(body) {
  if (!body || body.status !== "ok") {
    const status = String(body?.status || "missing_status");
    throw new DownloadError("API_ERROR", `GoFile API 返回 ${status}`, { retryable: /timeout|temporar|server/i.test(status) });
  }
  const node = body.data;
  if (!node || !["file", "folder"].includes(node.type)) throw new DownloadError("PARSE_ERROR", "GoFile 响应缺少文件类型");
  return { ...node, pagination: body.metadata || node.pagination };
}
async function collectFiles(node, loadFolder, dirs = [], ancestors = new Set(), depth = 0) {
  if (depth > 50 || ancestors.has(node.id)) throw new DownloadError("PARSE_ERROR", "文件夹嵌套过深或存在循环");
  if (node.type === "file") {
    if (node.isFrozen) throw new DownloadError("COLD_STORAGE", "文件位于冷存储，当前免费访问不能下载");
    if (node.overloaded) throw new DownloadError("SERVER_OVERLOADED", "服务器暂停免费访问，请稍后重试", { retryable: true });
    if (!node.id || !node.name || node.size === undefined || node.size === null || !Number.isSafeInteger(Number(node.size)) || Number(node.size) < 0)
      throw new DownloadError("PARSE_ERROR", "文件缺少 ID、名称或精确大小");
    return [{ id: String(node.id), name: node.name, size: Number(node.size), dirs,
      md5: /^[a-f\d]{32}$/i.test(node.md5 || "") ? node.md5 : null,
      pageUrl: `${SITE}/d/${node.code || node.id}`, link: node.link || node.directLink }];
  }
  if (node.type !== "folder") throw new DownloadError("PARSE_ERROR", "未知内容类型");
  const branch = new Set(ancestors); branch.add(node.id);
  const folderName = node.name || node.id || "folder";
  if (node.children === undefined && node.childs === undefined) node = await loadFolder(node.code || node.id);
  const children = node.children ?? node.childs;
  if (!children || typeof children !== "object") throw new DownloadError("PARSE_ERROR", "文件夹内容尚未加载或无权访问");
  const entries = Array.isArray(children) ? children : Object.entries(children).map(([id, child]) => ({ ...child, id: child.id || id }));
  if (node.hasNextPage || node.pagination?.hasNextPage || Number(node.childrenCount || 0) > entries.length)
    throw new DownloadError("PAGINATION_REQUIRED", "文件夹列表尚未完整返回，拒绝把部分列表当成完成");
  const out = [], folderNames = new Map();
  for (const child of entries) {
    let next = child;
    if (child.type === "folder") {
      const key = sanitize(child.name).toLowerCase();
      if (folderNames.has(key) && folderNames.get(key) !== child.id) next = { ...child, name: `${child.name} [${child.id}]` };
      folderNames.set(key, child.id);
    }
    out.push(...await collectFiles(next, loadFolder, [...dirs, folderName], branch, depth + 1));
  }
  return out;
}

// Let the official website obtain dynamic tokens. Never persist/log credentials.
class GoFileClient {
  constructor(context, page, { log = console.log, timeoutMs = 60000, signal } = {}) {
    this.context = context; this.page = page; this.log = log; this.timeoutMs = timeoutMs; this.signal = signal; this.userAgent = null;
  }
  async getContent(url) {
    this.signal?.throwIfAborted();
    const cid = contentId(url);
    if (!cid) throw new DownloadError("INVALID_URL", "不是有效的 GoFile 分享链接");
    await this.page.beforeNavigation?.();
    this.log(`[解析] 等待 GoFile 内容响应（最多 ${this.timeoutMs / 1000} 秒）`);
    let handler, timer, abort;
    const pending = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new DownloadError("PARSE_TIMEOUT", "网页未返回内容列表；请检查登录、验证提示或网络", { retryable: true })), this.timeoutMs);
      abort = () => reject(new DownloadError("STOPPED", "用户停止"));
      this.signal?.addEventListener("abort", abort, { once: true });
      handler = async response => {
        const u = new URL(response.url());
        if (u.hostname !== API_HOST || u.pathname !== `/contents/${cid}`) return;
        try {
          if (response.status() >= 400) throw httpError(response.status(), await response.allHeaders());
          resolve(normalize(await response.json()));
        } catch (e) { reject(e); }
      };
      this.page.on("response", handler);
    });
    pending.catch(() => {});
    try {
      const nav = await this.page.goto(url, { waitUntil: "domcontentloaded", timeout: this.timeoutMs });
      if (nav && nav.status() >= 400) throw httpError(nav.status(), await nav.allHeaders());
      const node = await pending;
      if (!this.userAgent) this.userAgent = await this.page.evaluate(() => navigator.userAgent);
      return node;
    } finally {
      clearTimeout(timer); this.page.off("response", handler); this.signal?.removeEventListener("abort", abort);
    }
  }
  async files(url) {
    const root = await this.getContent(url);
    const result = await collectFiles(root, id => this.getContent(`${SITE}/d/${id}`));
    if (root.type === "file") result[0].pageUrl = url;
    return result;
  }
  async resolve(info, initial = false) {
    this.signal?.throwIfAborted();
    const node = initial && this.page.url() === info.pageUrl ? info : await this.getContent(info.pageUrl);
    if (node.isFrozen) throw new DownloadError("COLD_STORAGE", "文件已进入冷存储，当前免费访问不能下载");
    if (node.overloaded) throw new DownloadError("SERVER_OVERLOADED", "服务器暂停免费访问，请稍后重试", { retryable: true });
    if (node.id !== info.id || Number(node.size) !== info.size) throw new DownloadError("REMOTE_CHANGED", "远程文件 ID 或大小已变化，请重新解析任务");
    let direct = node.link || node.directLink;
    if (isDownloadUrl(direct, info.id)) return direct;
    const candidates = await this.page.locator('a[href*="/download/"], video source[src], video[src]').evaluateAll(els =>
      els.map(el => el.getAttribute("href") || el.getAttribute("src")));
    direct = candidates.find(url => isDownloadUrl(url, info.id));
    if (direct) return direct;
    throw new DownloadError("NO_DOWNLOAD_URL", "已读取信息，但未找到该文件的公开下载地址；不会保存分享网页", { refresh: true });
  }
  async headersFor(url) {
    if (!isDownloadUrl(url)) throw new DownloadError("NOT_FILE", "下载地址跳转到了分享网页或非 GoFile 地址", { refresh: true });
    const cookies = await this.context.cookies(url);
    return { "User-Agent": this.userAgent, Referer: `${SITE}/`,
      ...(cookies.length ? { Cookie: cookies.map(c => `${c.name}=${c.value}`).join("; ") } : {}) };
  }
}
module.exports = { GoFileClient, collectFiles, normalize, isDownloadUrl };
