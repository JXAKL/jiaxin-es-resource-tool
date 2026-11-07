'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const {setTimeout: wait} = require('node:timers/promises');
const {DownloadError, preserve} = require('./transfer');
const execute = promisify(execFile);

function clientPath(setting) {
  const input = setting || path.join(process.env.LOCALAPPDATA || '', 'MEGAcmd');
  const file = path.basename(input).toLowerCase() === 'megaclient.exe' ? input : path.join(input, 'MegaClient.exe');
  if (!path.isAbsolute(file) || !fs.existsSync(file) || !fs.statSync(file).isFile())
    throw new DownloadError('MEGACMD_MISSING', '请安装官方 MEGAcmd，或在下载设置选择包含 MegaClient.exe 的目录。');
  return file;
}

function commander(file) {
  return async args => {
    try {
      const {stdout} = await execute(file, args, {windowsHide:true, timeout:20000, maxBuffer:8*1024*1024});
      return stdout;
    } catch (err) {
      // execFile's error message contains the command and the share key.
      const diagnostic = String(err.stderr || '') + String(err.stdout || '');
      if (/quota|bandwidth|509/i.test(diagnostic))
        throw new DownloadError('MEGA_QUOTA','MEGAcmd 报告流量额度不足，稍后可继续。',{pauseQueue:true});
      throw new DownloadError('MEGACMD_COMMAND', 'MEGAcmd '+args[0]+' 执行失败（'+(err.killed?'超时':err.code)+'），请检查官方客户端的连接与登录状态。');
    }
  };
}

async function transfers(run, dir) {
  const output = await run(['transfers','--only-downloads','--show-completed','--limit=10000',
    '--path-display-size=1024','--col-separator=|','--output-cols=TAG,DESTINYPATH,PROGRESS,STATE']);
  return output.split(/\r?\n/).map(line => line.split('|').map(x => x.trim()))
    .filter(([tag, dest,, state]) => /^\d+$/.test(tag) && dest && state && path.relative(dir, path.dirname(dest)) === '')
    .map(([tag,dest,percent,state]) => ({tag,dest,percent:parseFloat(percent)||0,state:state.toUpperCase()}));
}

const active = row => !['COMPLETED','CANCELED','CANCELLED','FAILED'].includes(row.state);
function stageFor(row, store) {
  const parent = path.dirname(row.local_path);
  fs.mkdirSync(parent,{recursive:true});
  const saved = row.cmd_stage;
  if (saved && path.dirname(saved) === parent && /^\.es-megacmd-[a-zA-Z0-9]+$/.test(path.basename(saved)) &&
      fs.existsSync(saved) && fs.lstatSync(saved).isDirectory()) return saved;
  const dir = fs.mkdtempSync(path.join(parent,'.es-megacmd-'));
  store.update(row,{cmd_stage:dir,cmd_sent:false});
  return dir;
}

async function transferCmd(info, store, source, {signal, run, url, onProgress=()=>{}, log=()=>{}, beforeSubmit, pollMs=1500}) {
  const {verify} = require('./mega-local');
  const row = store.register(source,info);
  if (await verify(row.local_path,info.node,signal)) {
    store.update(row,{platform:'mega',status:'SKIPPED',local_size:info.size,error:null});
    return;
  }
  const dir = stageFor(row,store);
  const owned = () => transfers(run,dir);
  const inspect = async () => {
    for (const entry of fs.readdirSync(dir,{withFileTypes:true})) {
      if (!entry.isFile()) continue;
      const candidate = path.join(dir,entry.name);
      if (await verify(candidate, info.node, signal)) return candidate;
    }
    return null;
  };
  let lastPercent = -1, lastChange = Date.now();
  let pending = row.cmd_sent;
  try {
    signal?.throwIfAborted();
    let entries = await owned();
    let job = entries.find(active);
    const previousTags = new Set(entries.filter(row=>!active(row)).map(row=>row.tag));
    let ready = job ? null : await inspect();
    if (!job && !ready) {
      if (pending && !entries.length && Date.now()-Number(row.cmd_sent_at || 0)<60000)
        throw new DownloadError('MEGACMD_PENDING','上次提交仍在确认，请稍后继续，避免重复下载。');
      await beforeSubmit?.();
      signal?.throwIfAborted();
      store.update(row,{cmd_sent:true,cmd_sent_at:Date.now(),status:'DOWNLOADING',platform:'mega'});
      pending = true;
      await run(['get','-q',url,dir]);
      lastChange = Date.now();
    } else if (job?.state === 'PAUSED') {
      await run(['transfers','-r',job.tag]);
    }
    while (!ready) {
      signal?.throwIfAborted();
      entries = await owned();
      job = entries.find(active);
      if (!job) {
        ready = await inspect();
        if (ready) break;
        if (entries.some(row=>!previousTags.has(row.tag)) || Date.now()-lastChange > 45000)
          throw new DownloadError('MEGACMD_INCOMPLETE','官方客户端未交付可通过完整校验的文件，请核对连接后继续。');
      } else {
        if (job.percent !== lastPercent) {
          lastPercent=job.percent; lastChange=Date.now();
          onProgress({downloaded:Math.floor(info.size*Math.min(100,job.percent)/100),total:info.size});
        }
        // ACTIVE may report the same rounded percentage for hours on large files.
        if (job.state !== 'ACTIVE' && Date.now()-lastChange > 10*60*1000) {
          await run(['transfers','-p',job.tag]);
          throw new DownloadError('MEGACMD_STALLED','官方客户端长时间未推进，已暂停该文件，稍后可继续。',{pauseQueue:true});
        }
      }
      await wait(pollMs,undefined,{signal});
    }
    signal?.throwIfAborted();
    if (fs.existsSync(row.local_path)) preserve(row.local_path,'previous-version');
    fs.renameSync(ready,row.local_path);
    store.update(row,{status:'COMPLETED',platform:'mega',local_size:info.size,cmd_stage:null,cmd_sent:false,error:null});
    try { fs.rmdirSync(dir); } catch { /* keep any other partial files */ }
    onProgress({downloaded:info.size,total:info.size});
  } catch (error) {
    if (signal?.aborted && (pending || row.cmd_sent)) {
      try {
        for (const job of (await owned()).filter(active)) await run(['transfers','-p',job.tag]);
      } catch { log('无法确认官方客户端已暂停，请在 MEGAcmd 中检查此任务。'); }
    }
    store.update(row,{status:'PENDING'});
    throw error;
  }
}
module.exports = {clientPath, commander, transferCmd};
