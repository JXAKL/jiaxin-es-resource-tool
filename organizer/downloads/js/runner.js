"use strict";
const fs = require('node:fs');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { DEFAULTS, StateStore, HttpSession, DownloadError, validFile, sizeOf, withRetries, classify, preserve } = require('./transfer');
const { GoFileClient } = require('./gofile');
const {pixeldrainPageFiles}=require('./browser-download');
const {DownloadPacer}=require('./pacing');
const {archiveBrowserDownloads}=require('./download-history');
const {prepareHanimePages}=require('./hanime-session');
const { pageFiles, metadata, saveMega, navigate } = require('./adapters');
const emit = (type, data = {}) => console.log('RESOURCE_EVENT ' + JSON.stringify({ type, ...data }));
const log = text => emit('log', { text: String(text) });

class Engine {
  constructor(options, chromium) {
    this.options = options; this.chromium = chromium; this.context = null; this.platform = null;
    this.controller = new AbortController(); this.signal = this.controller.signal;
    this.config = { ...DEFAULTS, retryDelays: [3000, 10000, 30000], rateLimit:options.rateLimit||0,proxy:options.proxy||'',...options.transfer };
    this.pacer = new DownloadPacer(path.join(options.state || options.output || '.', 'download-pacing.json'),
      {interval:options.requestInterval ?? 8,perMinute:options.perMinute ?? 6,log});
  }
  async stop() { this.controller.abort(); await this.close(); }
  async browser(platform) {
    if(platform==='hanime' && this.hanimeContext && this.context!==this.hanimeContext) {
      await this.context?.close().catch(()=>{});
      this.context=this.hanimeContext;this.page=this.hanimePage;this.platform='hanime';
    }
    if (this.context && this.platform === platform && !this.page.isClosed()) return this.context;
    if (this.context && this.platform === 'hanime' && platform === 'hanime') {
      try {
        // The website may close its originating tab after opening a download.
        // Losing a tab does not mean its browser/session needs to be replaced.
        this.page = await this.context.newPage();
        this.page.beforeNavigation = () => this.pacer.wait(this.signal);
        return this.context;
      } catch { /* Only a closed/unusable context falls through to relaunch. */ }
    }
    if(this.context!==this.hanimeContext)await this.context?.close().catch(() => {});
    this.signal.throwIfAborted();
    // The folder contains full Chromium, not a second headless-shell binary.
    const legacy = ['iwara','hanime'].includes(platform) && this.options[platform+'Legacy']!==false;
    const profile = legacy && this.options[platform+'Profile'];
    const channel = profile ? 'chrome' : this.options.browser === 'Chrome' ? 'chrome' : this.options.browser === 'Edge' ? 'msedge' : 'chromium';
    if(legacy) log(`${platform==='iwara'?'Iwara':'Hanime1'}：兼容启动方式；浏览器 ${channel}，会话${profile?'使用独立兼容目录':'使用现有目录'}。`);
    const sessionPath=profile || path.join(this.options.state, 'download-browser', platform);
    if(['hanime','iwara','pixeldrain'].includes(platform)) {
      try {
        const result=archiveBrowserDownloads(this.options.state,sessionPath,platform);
        if(result.archived)log(`${platform}：已备份并移出 ${result.archived} 条旧浏览器下载记录，避免会话重启后保存崩溃；登录与工具下载记录保留。`);
      } catch(error) {
        throw new DownloadError('BROWSER_HISTORY_BUSY',`${platform} 会话准备未完成，未继续启动浏览器：`+error.message,{pauseQueue:true});
      }
    }
    this.context = await this.chromium.launchPersistentContext(sessionPath, {
      channel, headless: !this.options.visible, viewport: { width: 1280, height: 850 }, acceptDownloads: true,
      ...(this.options.proxy?{proxy:{server:this.options.proxy}}:{}),
      ...(legacy ? {viewport:{width:1920,height:1080},args:['--disable-blink-features=AutomationControlled']} : {}),
    });
    this.platform = platform;
    this.page = this.context.pages()[0] || await this.context.newPage();
    if(platform==='hanime'){this.hanimeContext=this.context;this.hanimePage=this.page;}
    this.page.beforeNavigation = () => this.pacer.wait(this.signal);
    return this.context;
  }
  async login(item) {
    await this.browser(item.platform);
    this.loginDone = false;
    const status = await navigate(this.page, item.url, {allowAuth:true});
    if ([401,403].includes(status)) log('网站要求登录或验证；保留浏览器窗口，请在官网完成后再保存登录。');
    emit('login', { text: '请在浏览器完成登录或验证，再点击“保存登录”返回。' });
    while (!this.signal.aborted && !this.loginDone) {
      if (this.page.isClosed()) throw new DownloadError('BROWSER_CLOSED','登录窗口已关闭，尚未确认登录完成');
      await delay(150, undefined, { signal: this.signal });
    }
    if(!this.signal.aborted) {
      const count=(await (this.context||this.page.context()).cookies(new URL(item.url).origin)).length;
      emit('session',{platform:item.platform,checkedAt:new Date().toISOString(),cookies:count});
    }
  }
  async saveBrowserFile(source,info,store,platform) {
    const staging=path.join(this.options.output,'.resource-browser-'+info.id+'.part');
    await info.browserDownload.saveAs(staging);
    const failure=await info.browserDownload.failure();
    if(failure || !sizeOf(staging)) throw new DownloadError('BROWSER_DOWNLOAD_FAILED',failure || '浏览器返回空文件');
    if(platform==='hanime') {
      const header=Buffer.alloc(32),fd=fs.openSync(staging,'r');
      let length;
      try {length=fs.readSync(fd,header,0,header.length,0);} finally {fs.closeSync(fd);}
      const mp4=length>=12 && ['ftyp','styp','moov','mdat','wide','free','skip','moof'].includes(header.toString('ascii',4,8));
      const webm=length>=4 && header.subarray(0,4).equals(Buffer.from('1a45dfa3','hex'));
      const avi=length>=12 && header.toString('ascii',0,4)==='RIFF' && header.toString('ascii',8,12)==='AVI ';
      if(!mp4 && !webm && !avi) throw new DownloadError('NOT_VIDEO',
        'Hanime 浏览器返回内容不是支持的视频文件；未标记完成，已保留临时文件供检查。',{pauseQueue:true});
    }
    if(Number.isSafeInteger(info.size) && !await validFile(staging,info.size,info.md5))
      throw new DownloadError('BROWSER_SIZE_MISMATCH','浏览器下载文件与官网大小或校验值不符');
    info.size=sizeOf(staging);
    const row=store.register(source,info);
    fs.mkdirSync(path.dirname(row.local_path),{recursive:true});
    if(fs.existsSync(row.local_path)) preserve(row.local_path,'previous-version');
    fs.renameSync(staging,row.local_path);
    store.update(row,{status:'COMPLETED',local_size:info.size,platform,error:null});
  }
  async run(items) {
    const store = new StateStore(this.options.output, 'resource_download_state.json');
    let completed = 0, failed = 0;
    for (let index = 0; index < items.length; index++) {
      this.signal.throwIfAborted();
      const item = items[index], source = item.url;
      const localMega=item.platform==='mega' && this.options.megaMode==='local';
      emit('task', { url: source, status: '处理中', index: index + 1, total: items.length });
      try {
        if(localMega) {
          const {downloadMega}=require('./mega-local');
          const count=await withRetries(()=>downloadMega(source,this.options,{signal:this.signal,pacer:this.pacer,log,
            onProgress:p=>emit('progress',{url:source,...p})}),{config:this.config,signal:this.signal,
              onRetry:(e,n,ms)=>emit('retry',{url:source,text:`${e.message} ${ms/1000} 秒后重试（${n}/${this.config.retryDelays.length}）`})});
          emit('task',{url:source,status:'完成',detail:`MEGA ${count} 个文件已下载到本地并通过完整解密校验`});completed++;
          if(index<items.length-1)await delay(Math.max(0,this.options.interval||0)*1000,undefined,{signal:this.signal});
          continue;
        }
        if (item.platform === 'mega') {
          if (!this.options.megaSave) throw new DownloadError('MEGA_DISABLED', 'MEGA 是保存到账号网盘，请开启该选项后单独处理');
          const previous = store.data.tasks[source];
          if (['CLOUD_SUBMITTED', 'CLOUD_UNCONFIRMED'].includes(previous?.status))
            throw new DownloadError('MEGA_UNCONFIRMED', '上次已经提交保存但未确认结果，请在 MEGA 中核对；本项不会自动重复提交');
          if (['CLOUD_SAVED', 'CLOUD_SKIPPED'].includes(previous?.status)) {
            emit('task', {url:source, status:previous.status === 'CLOUD_SAVED' ? '网盘已保存' : '重复已跳过', detail:previous.detail});
            completed++; continue;
          }
          await this.browser('mega');
          const result = await saveMega(this.page, source, {log, onSubmitted: () =>
            store.task(source, {platform:'mega', status:'CLOUD_SUBMITTED', error:null})});
          store.task(source, {platform:'mega', status:result.status === '网盘已保存' ? 'CLOUD_SAVED' : 'CLOUD_SKIPPED', detail:result.detail, error:null});
          emit('task', {url:source, status:result.status, detail:result.detail});
          completed++;
          if (index < items.length - 1) await delay(Math.max(0, this.options.interval || 0)*1000, undefined, {signal:this.signal});
          continue;
        }
        if ((store.data.tasks[source]?.quality||'best')===(this.options.quality||'best') && await store.completed(source)) {
          emit('task', { url: source, status: '已校验跳过', detail: '本目录中的已有文件大小及可用校验值一致' });
          completed++; continue;
        }
        const context = await this.browser(item.platform);
        if(item.platform==='hanime') {
          const tabs=await prepareHanimePages(context,this.page,source,{navigate,signal:this.signal,
            beforeNavigation:()=>this.pacer.wait(this.signal),visible:this.options.visible,onWaiting:log});
          this.page=tabs.download;this.hanimePage=this.page;
        }
        let client;
        const userAgent = await this.page.evaluate(() => navigator.userAgent);
        this.session?.close();
        this.session = new HttpSession({ ...this.config, beforeRequest:()=>this.pacer.wait(this.signal), headersFor: async url => {
          const cookies = await context.cookies(url);
          return { 'User-Agent': userAgent, Referer: new URL(source).origin + '/',
            ...(cookies.length ? { Cookie: cookies.map(c => c.name + '=' + c.value).join('; ') } : {}) };
        }});
        if (item.platform === 'gofile') client = new GoFileClient(context, this.page, { signal: this.signal, log });
        const parse = () => item.platform === 'gofile' ? client.files(source) : item.platform === 'pixeldrain' ?
          pixeldrainPageFiles(this.page,source,{signal:this.signal,onWaiting:text=>{
            emit('task',{url:source,status:'等待官网下载',detail:text});log(text);
          }}) : pageFiles(this.page, source, item.platform, {native:['iwara','hanime'].includes(item.platform),signal:this.signal,quality:this.options.quality||'best',
            onResolved:log,
            visible:this.options.visible,onWaiting:text=>{emit('task',{url:source,status:'等待网站验证',detail:text});log(text);}});
        if(item.platform==='pixeldrain' && !this.options.visible)
          throw new DownloadError('VISIBLE_BROWSER_REQUIRED','Pixeldrain 使用官网整包下载按钮，请开启“显示浏览器”');
        const retry = { config: this.config, signal: this.signal,
          onRetry: (e, attempt, ms) => {
            if(e.status===429)this.pacer.cooldown(Math.max(ms,e.retryAfterMs||0,this.config.rateLimitPauseMs));
            emit('retry', { url: source, text: `${e.code}；${ms / 1000} 秒后自动重试（${attempt}/${this.config.retryDelays.length}）` });
          } };
        const files = await withRetries(parse,retry);
        if (!files.length) throw new DownloadError('EMPTY_CONTENT', '未获得文件列表');
        store.task(source, { platform: item.platform, status: 'DOWNLOADING', quality:this.options.quality||'best',file_ids: files.map(x => x.id) });
        for (let fileIndex = 0; fileIndex < files.length; fileIndex++) {
          this.signal.throwIfAborted();
          let info = files[fileIndex], row;
          if(info.browserDownload) {
            try { await this.saveBrowserFile(source,info,store,item.platform); }
            finally { await info.cleanupBrowserDownload?.(); }
            continue;
          }
          try {
          await withRetries(async attempt => {
            if (item.platform === 'gofile') info.link = await client.resolve(info, attempt === 0 && files.length === 1);
            else if (attempt && item.platform !== 'pixeldrain') {
              const fresh = await parse();
              const match = fresh.find(x => x.id === info.id);
              if (!match) throw new DownloadError('REMOTE_CHANGED', '重试时原文件不再存在');
              info = match;
            }
            info = await metadata(this.session, info, this.signal);
            row = store.register(source, info);
            store.update(row, { platform: item.platform });
            if (await validFile(row.local_path, info.size, info.md5)) {
              store.update(row, { status: 'SKIPPED', local_size: info.size, error: null }); return;
            }
            store.update(row, { status: 'DOWNLOADING', retry_count: attempt, error: null });
            let savedAt = 0;
            await this.session.download({ url: info.link, dest: row.local_path, expectedSize: info.size, md5: info.md5,
              validator: row.validator, onValidator: value => store.update(row, { validator: value }), signal: this.signal, onLog: log,
              onProgress: p => {
                emit('progress', { url: source, filename: info.name, fileIndex: fileIndex + 1, fileCount: files.length, ...p });
                if (Date.now() - savedAt > 5000) { store.update(row, { local_size: p.downloaded }); savedAt = Date.now(); }
              }});
            store.update(row, { status: 'COMPLETED', local_size: info.size, error: null });
          }, retry);
          } catch(error) {
            if(item.platform!=='iwara' || this.signal.aborted) throw error;
            log('Iwara 直链未完成，改用官网 Source 下载按钮。');
            const fresh=await pageFiles(this.page,source,'iwara',{native:true,quality:this.options.quality||'best',signal:this.signal});
            const native=fresh.find(x=>x.id===info.id);
            if(!native?.browserDownload) throw error;
            await this.saveBrowserFile(source,{...native,size:info.size,md5:info.md5},store,item.platform);
          }
        }
        store.task(source, { status: 'COMPLETED', error: null });
        emit('task', { url: source, status: '完成', detail: `${files.length} 个文件已完成；直链校验大小，官网下载核对浏览器完成状态` }); completed++;
      } catch (raw) {
        const error = classify(raw);
        if(localMega) {
          emit('task',{url:source,status:this.signal.aborted||error.pauseQueue?'待继续':'失败',detail:error.message});
          if(this.signal.aborted)break;
          failed++;
          if(error.pauseQueue){emit('summary',{completed,failed,paused:true,reason:error.message});return;}
          if(index<items.length-1)await delay(Math.max(0,this.options.interval||0)*1000,undefined,{signal:this.signal});
          continue; // Local download errors must never mutate cloud-save submission history.
        }
        if (this.signal.aborted && item.platform === 'mega' && ['CLOUD_SAVED','CLOUD_SKIPPED'].includes(store.data.tasks[source]?.status)) break;
        const cloudUncertain = item.platform === 'mega' && (error.code === 'MEGA_UNCONFIRMED' ||
          ['CLOUD_SUBMITTED', 'CLOUD_UNCONFIRMED'].includes(store.data.tasks[source]?.status));
        if (cloudUncertain) {
          store.task(source, {platform:'mega', status:'CLOUD_UNCONFIRMED', error:error.message});
          emit('task', {url:source, status:'需核对', detail:error.message});
          failed++;
          if (this.signal.aborted) break;
          continue;
        }
        if (this.signal.aborted) {
          store.task(source, { platform:item.platform, status: 'PENDING', error: '已停止，保留断点' });
          emit('task', { url: source, status: '待继续', detail: '断点已保留' }); break;
        }
        failed++;
        store.task(source, { platform:item.platform, status: 'FAILED', error: error.code + ': ' + error.message });
        emit('task', { url: source, status: error.code === 'MEGA_UNCONFIRMED' ? '需核对' : '失败', detail: error.code + ': ' + error.message });
        // Honor server throttling across tasks as well as within a transfer retry.
        if (error.status === 429) {
          const pause = Math.max(error.retryAfterMs || 0, this.config.rateLimitPauseMs);
          this.pacer.cooldown(pause);
          await delay(pause, undefined, { signal: this.signal });
        }
        if(error.pauseQueue) {
          store.task(source,{platform:item.platform,status:'PENDING',error:error.message});
          emit('task',{url:source,status:'待继续',detail:error.message});
          emit('summary',{completed,failed,stopped:false,paused:true,reason:error.message});
          return;
        }
      }
      if (index < items.length - 1) await delay(Math.max(0, this.options.interval || 0) * 1000, undefined, { signal: this.signal });
    }
    emit('summary', { completed, failed, stopped: this.signal.aborted });
  }
  async close() {
    this.session?.close();
    for(const context of new Set([this.context,this.hanimeContext]))await context?.close().catch(()=>{});
    this.context=null;this.hanimeContext=null;this.hanimePage=null;
  }
}
async function main() {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const { chromium } = require(process.env.ES_RESOURCE_PLAYWRIGHT);
  const engine = new Engine(job.options, chromium);
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', text => { if (text.includes('STOP')) void engine.stop(); if (text.includes('FINISH_LOGIN')) engine.loginDone = true; });
  process.stdin.resume();
  try {
    if (job.login) await engine.login(job.items[0]); else await engine.run(job.items);
  } catch (error) {
    if (!engine.signal.aborted) { emit('error', { text: error.message }); process.exitCode = 1; }
  } finally { await engine.close(); process.stdin.pause(); }
}
if (require.main === module) main().catch(error => { emit('error', { text: error.message }); process.exitCode = 1; });
module.exports = { Engine };
