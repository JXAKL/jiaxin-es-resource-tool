'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {DownloadError, preserve, retryAfter} = require('./transfer');

function runWorker(job, signal, progress) {
  signal?.throwIfAborted();
  return new Promise((resolve,reject)=>{
    const exe = path.join(__dirname,'vendor','revival','RevivalWorker.exe');
    const child = spawn(exe,[],{windowsHide:true, cwd:path.dirname(exe), stdio:['pipe','pipe','pipe']});
    const cancel = () => child.stdin.end('STOP\n');
    signal?.addEventListener('abort',cancel,{once:true});
    let diagnostic = '';
    child.stderr.setEncoding('utf8').on('data',chunk=>{ diagnostic=(diagnostic+chunk).slice(-2000); });
    child.stdin.on('error',()=>{}); // A completed worker may close stdin before a simultaneous stop.
    let previousBytes=null, previousTime=0;
    readline.createInterface({input:child.stdout}).on('line',line=>{
      if (!/^\d+$/.test(line)) return;
      const downloaded=Number(line),now=Date.now();
      const speed=previousBytes===null?0:1000*Math.max(0,downloaded-previousBytes)/Math.max(1,now-previousTime);
      previousBytes=downloaded;previousTime=now;
      progress({downloaded,total:job.size,speed});
    });
    child.once('error',reject);
    child.once('close',code=>{
      signal?.removeEventListener('abort',cancel);
      if (signal?.aborted) return reject(signal.reason);
      if (code === 0) return resolve();
      if (code === 3 || /\b509\b/.test(diagnostic))
        return reject(new DownloadError('MEGA_QUOTA','MEGA 流量额度已用完。',{pauseQueue:true,retryAfterMs:retryAfter(diagnostic.trim())}));
      if (code===4 || /\b429\b/.test(diagnostic))
        return reject(new DownloadError('MEGA_RATE','MEGA 请求过于频繁。',{pauseQueue:true,retryAfterMs:retryAfter(diagnostic.trim())}));
      reject(new DownloadError('REVIVAL_TRANSFER','Revival 下载失败：'+(diagnostic.trim()||'退出码 '+code)));
    });
    child.stdin.write(JSON.stringify(job)+'\n');
  });
}

async function transferRevival(file,store,source,options,{signal,onProgress}) {
  const {verify} = require('./mega-local');
  const {node} = file, row=store.register(source,file);
  if (await verify(row.local_path,node,signal)) {
    store.update(row,{status:'SKIPPED',platform:'mega',local_size:file.size,error:null});
    return;
  }
  const parent=path.dirname(row.local_path);
  fs.mkdirSync(parent,{recursive:true});
  let dir=row.revival_stage;
  if (!dir || path.dirname(dir)!==parent || !/^\.es-revival-[A-Za-z0-9]+$/.test(path.basename(dir)) ||
      !fs.existsSync(dir) || !fs.lstatSync(dir).isDirectory()) dir=fs.mkdtempSync(path.join(parent,'.es-revival-'));
  store.update(row,{revival_stage:dir,platform:'mega',status:'DOWNLOADING'});
  const payload=path.join(dir,'payload.bin');
  try {
    if (!await verify(payload,node,signal)) {
      if (fs.existsSync(payload)) preserve(payload,'invalid-mac');
      const id=node.downloadId;
      const location=Array.isArray(id)?{n:id[1],_querystring:{n:id[0]}}:{p:id};
      const remote=await node.api.request({a:'g',g:1,ssl:2,...location});
      if (typeof remote.g!=='string' || !remote.g.startsWith('https://') || remote.s!==file.size)
        throw new DownloadError('REVIVAL_LINK','MEGA 返回的传输地址或文件大小无效。');
      await runWorker({url:remote.g,key:node.key.toString('base64url'),id:file.id,directory:dir,size:file.size,
        rate:options.rateLimit||0,connections:options.megaConnections||2,proxy:options.proxy||''},signal,onProgress);
      // The pinned engine verifies the whole MetaMAC before reporting FileDownloadSucceeded.
      // Recovered and previously delivered files above still require independent verification.
      if (!fs.existsSync(payload) || fs.statSync(payload).size!==file.size)
        throw new DownloadError('REVIVAL_OUTPUT','未找到与已校验结果一致的输出文件。');
    }
    signal?.throwIfAborted();
    if (fs.existsSync(row.local_path)) preserve(row.local_path,'previous-version');
    fs.renameSync(payload,row.local_path);
    store.update(row,{status:'COMPLETED',local_size:file.size,error:null,revival_stage:null});
    try { fs.rmSync(path.join(dir,'offsets.json'),{force:true}); fs.rmdirSync(dir); } catch { /* keep diagnostic files */ }
    onProgress({downloaded:file.size,total:file.size});
  } catch (error) {store.update(row,{status:'PENDING'});throw error;}
}
module.exports={transferRevival,runWorker};
