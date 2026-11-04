"use strict";
const path = require("node:path");
const crypto = require("node:crypto");
const { DownloadError, httpError, sanitize } = require("./transfer");

function quality(text) {
  if (/\bsource\b|原画|原始/i.test(text)) return 10000;
  if (/\b4k\b/i.test(text)) return 2160;
  return Number(/\b(\d{3,4})\s*p\b/i.exec(text)?.[1] || /\b\d{3,4}\s*[x×]\s*(\d{3,4})\b/i.exec(text)?.[1] || 0);
}
function bestLink(links, base, preference='best') {
  const valid=links.filter(x => typeof x.href === 'string' && x.href.trim()).map(x => {
    try { return { ...x, href: new URL(x.href, base).href }; } catch { return null; }
  }).filter(x => x && /^https?:\/\//i.test(x.href) && !new URL(x.href).username)
    .sort((a, b) => quality(b.text || '') - quality(a.text || ''));
  if(preference==='best')return valid[0];
  const cap=Number(preference);
  return valid.find(x=>quality(x.text||'')>0&&quality(x.text||'')<=cap);
}
function sourceId(url) { return crypto.createHash("sha256").update(url).digest("hex").slice(0, 24); }
async function navigate(page, url, {allowAuth = false, referer} = {}) {
  try {
    await page.beforeNavigation?.();
    const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000, ...(referer ? {referer} : {}) });
    if (response && response.status() >= 400 && !(allowAuth && [401,403].includes(response.status()))) throw httpError(response.status(), await response.allHeaders());
    return response?.status();
  } catch (error) {
    if (error instanceof DownloadError) throw error;
    throw new DownloadError("PAGE_CONNECTION", error.message, { retryable: true });
  }
}
async function jsonRequest(context, url) {
  const response = await context.request.get(url, { timeout: 30000 });
  try {
    if (!response.ok()) throw httpError(response.status(), response.headers());
    return await response.json();
  } finally { await response.dispose(); }
}
async function pixeldrainFiles(context, url) {
  const u = new URL(url), id = u.pathname.split('/')[2];
  const list = u.pathname.startsWith('/l/');
  const data = await jsonRequest(context, `https://pixeldrain.com/api/${list ? 'list' : 'file'}/${id}${list ? '' : '/info'}`);
  const files = list ? data.files : [data];
  if (!Array.isArray(files) || !files.length || data.success === false)
    throw new DownloadError("EMPTY_CONTENT", data.message || "Pixeldrain 未返回文件列表");
  return files.map(file => {
    if (!file.id || !file.name || !Number.isSafeInteger(file.size) || file.size < 0)
      throw new DownloadError("INVALID_METADATA", "Pixeldrain 文件信息不完整");
    return { id: 'pixeldrain-' + file.id, name: file.name, size: file.size,
      dirs: list ? [sanitize(data.title || id)] : [], pageUrl:`https://pixeldrain.com/u/${file.id}`,
      link: `https://pixeldrain.com/api/file/${file.id}?download`, md5: null };
  });
}
async function waitForSiteReady(page, control, status, label, {signal, visible=true, onWaiting=()=>{}, authTimeout=600000}={}) {
  const challenge = async()=>[401,403].includes(status) || await page.locator('#challenge-form, #cf-challenge-running, iframe[src*="challenges.cloudflare.com"]').count()>0 ||
    /just a moment|verify.*human|security verification|请稍候|安全验证/i.test(await page.title());
  if(!await challenge()) await control.waitFor({state:'visible',timeout:30000}).catch(()=>{});
  if(!await control.isVisible() && await challenge()) {
    if(!visible) throw new DownloadError('SITE_VERIFICATION_REQUIRED',`${label} 要求网站验证，队列已暂停；请开启显示浏览器。`,{pauseQueue:true});
    onWaiting(`${label} 正在要求登录或验证；保留当前页面，工具不刷新、不切换下一项，页面放行后自动继续。`);
    const deadline=Date.now()+authTimeout;
    while(!await control.isVisible()) {
      signal?.throwIfAborted();
      if(Date.now()>=deadline) throw new DownloadError('SITE_VERIFICATION_REQUIRED',`${label} 仍未放行，队列已暂停；未重试访问或请求后续链接。`,{pauseQueue:true});
      await page.waitForTimeout(500);
    }
  }
  signal?.throwIfAborted();
}
async function pageFiles(page, url, platform, options = {}) {
  const {native=false,signal}=options;
  if (platform === 'hanime') return hanimeFiles(page, url, options);
  let status = await navigate(page, url, {allowAuth:['iwara','hanime'].includes(platform)});
  if (platform === 'iwara') {
    const button = page.locator('.downloadButton, [class*="downloadButton"], button[aria-label*="download" i]').first();
    await waitForSiteReady(page, button, status, 'Iwara', options);
    if (!await button.isVisible()) throw new DownloadError('NO_DOWNLOAD_LINK','Iwara 下载按钮尚未出现，请检查登录、验证或网络');
    await button.click();
    let source = page.locator('a, button, li, [role="menuitem"]').filter({hasText:/^\s*Source\s*$/i}).filter({visible:true}).first();
    if(options.quality && options.quality!=='best') {
      const choices=page.locator('a, button, li, [role="menuitem"]').filter({hasText:/^\s*(?:Source|\d{3,4}p)\s*$/i}).filter({visible:true});
      await choices.first().waitFor({state:'visible',timeout:20000});
      const rows=await choices.evaluateAll(nodes=>nodes.map((n,index)=>({index,text:n.textContent,href:'https://quality.invalid/'+index})));
      const chosen=bestLink(rows,page.url(),options.quality);
      if(!chosen)throw new DownloadError('QUALITY_UNAVAILABLE','Iwara 没有符合画质上限的下载项；可改为最高画质重试');
      source=choices.nth(chosen.index);
    }
    await source.waitFor({state:'visible',timeout:20000}).catch(()=>{});
    if (!await source.isVisible()) throw new DownloadError('SOURCE_UNAVAILABLE','Iwara 未提供 Source 原始文件下载选项');
    const href = await source.evaluate(n=>n.getAttribute('href')||n.querySelector('a[href]')?.getAttribute('href'));
    if (!native && href && /^https?:/i.test(new URL(href,page.url()).href)) {
      const chosen = {href:new URL(href,page.url()).href,name:await source.getAttribute('download')};
      return [await pageInfo(page,url,platform,chosen,options.quality)];
    }
    await page.beforeNavigation?.();
    const pending = page.waitForEvent('download', {timeout:120000});
    pending.catch(()=>{});
    await source.click();
    const download = await pending;
    return [{id:platform+'-'+sourceId(url)+(options.quality && options.quality!=='best'?'-q'+options.quality:''),name:sanitize(download.suggestedFilename()),dirs:[],browserDownload:download}];
  }
  const selectors = platform === 'rule34' ? 'a.tag_item_download' :
    'a[download], a.download-link, a[href*=".mp4"], a[href*=".webm"]';
  await page.locator(selectors).first().waitFor({ state: 'attached', timeout: 20000 }).catch(() => {});
  const links = await page.locator(selectors).evaluateAll(nodes => nodes.map(n => ({
    href: n.href || n.src || '', text: [n.textContent || '', n.getAttribute('label') || '', n.getAttribute('data-quality') || ''].join(' '),
    name: n.getAttribute('download') || ''
  })));
  const chosen = bestLink(links, page.url(),options.quality);
  if (!chosen) throw new DownloadError("NO_DOWNLOAD_LINK", "页面没有可读取的下载链接。请在登录/验证后重试；尚未把本项标记完成。");
  return [await pageInfo(page,url,platform,chosen,options.quality)];
}
// Restore the original downloader's watch -> visible menu -> download page flow.
// Hanime's quality controls are JS anchors with data-url, usually no href.
async function hanimeFiles(page, url, options) {
  const watch = new URL(url);
  if (watch.pathname === '/download' && watch.searchParams.has('v')) watch.pathname = '/watch';
  let status = await navigate(page, watch.href, {allowAuth:true});
  if (watch.pathname === '/watch') {
    const entries = page.locator('a[href*="/download?"]').filter({visible:true});
    const more = page.locator('.video-show-action-btn.dropdown-toggle').filter({visible:true}).first();
    await waitForSiteReady(page, entries.or(more).first(), status, 'Hanime1', options);
    if (!await entries.first().isVisible() && await more.isVisible()) {
      options.signal?.throwIfAborted();
      await more.click();
    }
    const entry = entries.first();
    await entry.waitFor({state:'visible',timeout:30000});
    const href = await entry.getAttribute('href');
    if (!href) throw new DownloadError('NO_DOWNLOAD_LINK','Hanime1 下载入口没有地址');
    const destination = new URL(href, watch);
    if (destination.origin !== watch.origin || destination.pathname !== '/download' ||
        (watch.searchParams.has('v') && destination.searchParams.get('v') !== watch.searchParams.get('v')))
      throw new DownloadError('WRONG_DOWNLOAD_PAGE','Hanime1 下载入口与当前视频不一致');
    // Original implementation sends the watch page as Referer, reusing one tab.
    status = await navigate(page, destination.href, {allowAuth:true,referer:watch.href});
  }
  const controls = page.locator('a[data-url], a[download], a.download-link, a[href*=".mp4"], a[href*=".webm"]').filter({visible:true});
  await waitForSiteReady(page, controls.first(), status, 'Hanime1', options);
  const links = await controls.evaluateAll(nodes => nodes.map((n,index) => ({
    index,
    href: n.getAttribute('data-url') || n.getAttribute('href') || '',
    text: [n.closest('tr')?.textContent || n.textContent || '', n.getAttribute('data-quality') || '',
           n.getAttribute('data-url') || n.getAttribute('href') || ''].join(' '),
    name: n.getAttribute('download') || ''
  })));
  const chosen = bestLink(links, page.url(),options.quality);
  if (!chosen) throw new DownloadError('NO_DOWNLOAD_LINK','Hanime1 未出现有效画质下载地址；没有将页面地址当作文件');
  const info = await pageInfo(page,url,'hanime',chosen,options.quality);
  if (!/\.(mp4|webm|mkv|mov|avi)$/i.test(info.name)) info.name = sanitize(info.name + '.mp4');
  if (options.native) {
    options.onResolved?.('Hanime1：点击官网所选画质下载按钮，等待浏览器保存文件。');
    const {clickQualityDownload}=require('./hanime-download');
    const captured=await clickQualityDownload(page,controls.nth(chosen.index),chosen.href,
      {signal:options.signal,timeout:options.downloadTimeout,filename:info.name,
        onVideoPage:()=>options.onResolved?.('Hanime1：已识别弹出的视频页，正在通过浏览器保存所选画质视频。')});
    info.browserDownload=captured.download;
    info.cleanupBrowserDownload=captured.cleanup;
    // Keep the originating context and popup alive until saveAs finishes.
    return [info];
  }
  options.onResolved?.('Hanime1：已读取官网所选画质文件地址，开始保存并校验文件。');
  return [info];
}
async function pageInfo(page,url,platform,chosen,preference='best') {
  const direct = new URL(chosen.href);
  const pageTitle = (await page.title()).replace(/\s*[-|]\s*(?:hanime1|iwara|rule34video).*$/i, '').trim();
  const filename = chosen.name || direct.searchParams.get('download_filename') || direct.searchParams.get('download') ||
    (/\.(mp4|webm|mkv|mov|avi)$/i.test(direct.pathname) ? decodeURIComponent(path.basename(direct.pathname)) : `${pageTitle || platform + '-' + sourceId(url)}.mp4`);
  return { id: platform + '-' + sourceId(url)+(preference!=='best'?'-q'+preference:''), name: sanitize(filename), dirs: [], link: chosen.href, size: null, md5: null };
}
async function metadata(session, info, signal) {
  if (Number.isSafeInteger(info.size) && info.size >= 0) return info;
  const response = await session.open(info.link, {}, signal);
  try {
    if (response.statusCode !== 200) throw httpError(response.statusCode, response.headers);
    const size = Number(response.headers['content-length']);
    if (!Number.isSafeInteger(size) || size <= 0) throw new DownloadError("SIZE_UNKNOWN", "服务器未提供精确文件大小，无法进行可靠的完整性校验");
    if (/text\/html|application\/json/i.test(response.headers['content-type'] || ''))
      throw new DownloadError("NOT_FILE", "下载地址返回了网页，未保存为视频", { refresh: true });
    return { ...info, size };
  } finally { response.destroy(); }
}
async function saveMega(page, url, options = {}) {
  return require('./mega').saveMega(page, url, {navigate, ...options});
}
module.exports = { quality, bestLink, sourceId, navigate, pixeldrainFiles, pageFiles, metadata, saveMega };
