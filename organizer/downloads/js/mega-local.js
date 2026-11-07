'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {Transform,Readable}=require('node:stream');
const {pipeline}=require('node:stream/promises');
const {DownloadError,StateStore,sizeOf,preserve,httpError,retryAfter}=require('./transfer');
const {rateLimit}=require('./rate-limit');
const sdk=()=>require('./vendor/megajs.cjs');

function streamingResponse(response) {
  if(!response.body?.getReader)return response;
  // megajs' web-reader loop ignores write backpressure. Its Node stream path
  // respects it; adapt only streaming downloads, never metadata/chunk buffers.
  const body=Readable.fromWeb(response.body),pipe=body.pipe.bind(body);
  body.pipe=(destination,...args)=>{
    body.once('error',error=>destination.destroy(error));
    destination.once('close',()=>body.destroy());
    return pipe(destination,...args);
  };
  return {body,status:response.status,ok:response.ok,headers:response.headers};
}

function parseLink(raw) {
  const u=new URL(raw);
  if(u.protocol!=='https:'||!['mega.nz','mega.io','mega.co.nz','www.mega.nz'].includes(u.hostname)||u.username||u.password||u.port)
    throw new DownloadError('MEGA_LINK','MEGA 地址无效');
  u.hostname='mega.nz';
  const legacy=/^#(F)?!([\w-]+)!([\w-]+)(?:!([\w-]+))?$/.exec(u.hash);
  if(legacy) {u.pathname='/'+(legacy[1]?'folder':'file')+'/'+legacy[2];u.hash=legacy[3]+(legacy[4]?'/file/'+legacy[4]:'');}
  const match=/^#([\w-]+)(?:\/(file|folder)\/([\w-]+))?$/.exec(u.hash);
  if(!/^\/(file|folder)\/[\w-]+\/?$/.test(u.pathname)||!match||u.search)
    throw new DownloadError('MEGA_LINK','需要包含完整密钥的 MEGA 文件或文件夹链接');
  const directory=u.pathname.startsWith('/folder/');
  if(Buffer.from(match[1],'base64url').length!==(directory?16:32))
    throw new DownloadError('MEGA_KEY','MEGA 分享密钥长度不正确');
  u.hash=match[1];
  return {base:u.href,child:match[3]||'',kind:match[2]||''};
}
function nodeId(node) {return Array.isArray(node.downloadId)?node.downloadId.at(-1):node.downloadId||node.nodeId;}
function flatten(root,selection={}) {
  let selected=root;
  if(selection.child) {
    const pending=[root],seen=new Set();selected=null;
    while(pending.length) {
      const node=pending.pop();if(seen.has(node))continue;seen.add(node);
      if(seen.size>100000)throw new DownloadError('MEGA_TREE','分享目录过大或无效');
      if(nodeId(node)===selection.child){selected=node;break;}
      pending.push(...(node.children||[]));
    }
    if(!selected||selected.directory!==(selection.kind==='folder'))throw new DownloadError('MEGA_MISSING','分享中的指定文件或子目录不存在');
  }
  const result=[],pending=[{node:selected,dirs:[]}],seen=new Set();
  while(pending.length) {
    const {node,dirs}=pending.pop();if(seen.has(node))continue;seen.add(node);
    if(seen.size>100000)throw new DownloadError('MEGA_TREE','分享目录过大或无效');
    if(node.directory) {for(const child of node.children||[])pending.push({node:child,dirs:[...dirs,node.name||'MEGA']});continue;}
    if(!node.name||!node.key||node.key.length!==32||!Number.isSafeInteger(node.size)||node.size<0)
      throw new DownloadError('MEGA_METADATA','分享文件的信息或解密密钥不完整');
    const identity=crypto.createHash('sha256').update(String(nodeId(node))).update(node.key).digest('hex').slice(0,24);
    result.push({node,id:'mega-'+identity,name:node.name,size:node.size,dirs,md5:null});
  }
  if(!result.length)throw new DownloadError('MEGA_EMPTY','分享目录为空');
  return result.sort((a,b)=>a.id.localeCompare(b.id));
}
function megaError(error) {
  if(error instanceof DownloadError)return error;
  if(/EOVERQUOTA|over.?quota|bandwidth|\b509\b|ESHAREROVERQUOTA/i.test(error.message||''))
    return new DownloadError('MEGA_QUOTA','MEGA 流量额度不足，已保留断点。请待网站额度恢复后继续；不会反复请求。',{pauseQueue:true});
  if(/ENOENT|EACCESS|EBLOCKED/.test(error.message||''))
    return new DownloadError('MEGA_UNAVAILABLE','MEGA 分享已失效、被移除或当前无权访问；未重复请求。');
  const code=error.cause?.code||error.code;
  if(['ECONNRESET','ETIMEDOUT','ENOTFOUND','EAI_AGAIN','UND_ERR_CONNECT_TIMEOUT','UND_ERR_SOCKET'].includes(code)||error.name==='TimeoutError')
    return new DownloadError('MEGA_CONNECTION','MEGA 连接中断或超时，已保留断点。',{retryable:true});
  return error;
}
async function verify(file,node,signal) {
  if(!fs.existsSync(file)||sizeOf(file)!==node.size)return false;
  try {await pipeline(fs.createReadStream(file),sdk().verify(node.key),{signal});return true;}
  catch(error) {if(signal?.aborted)throw error; if(/MAC verification failed/.test(error.message))return false;throw error;}
}
async function transferFile(info,store,source,{signal,connections=2,rate=0,onProgress=()=>{},log=()=>{}}={}) {
  const {node}=info,row=store.register(source,info);
  store.update(row,{platform:'mega'});
  if(await verify(row.local_path,node,signal)){store.update(row,{status:'SKIPPED',local_size:info.size,error:null});return;}
  const part=row.local_path+'.part';fs.mkdirSync(path.dirname(part),{recursive:true});
  let offset=sizeOf(part);
  if(offset>info.size){preserve(part,'oversize');offset=0;}
  if(offset===info.size && fs.existsSync(part) && !await verify(part,node,signal)){preserve(part,'invalid-mac');offset=0;}
  store.update(row,{status:'DOWNLOADING',error:null});
  try {
    if(offset<info.size || !fs.existsSync(part)) {
      const stream=node.download({start:offset,forceHttps:true,maxConnections:connections,
        initialChunkSize:1024*1024,chunkSizeIncrement:0,maxChunkSize:1024*1024,handleRetries:(_n,e,cb)=>cb(e)});
      stream.on('error',()=>{});
      let received=offset,lastAt=Date.now(),lastBytes=offset,lastData=Date.now();
      const monitor=new Transform({transform(chunk,encoding,done) {
        lastData=Date.now();received+=chunk.length;
        if(received>info.size)return done(new DownloadError('MEGA_SIZE','MEGA 返回的数据超过文件大小'));
        const now=Date.now();
        if(now-lastAt>=500){onProgress({downloaded:received,total:info.size,speed:(received-lastBytes)*1000/(now-lastAt)});lastAt=now;lastBytes=received;}
        done(null,chunk);
      }});
      onProgress({downloaded:offset,total:info.size,speed:0});
      // Slow user-selected throttling must not be mistaken for a connection stall.
      const limiter=rate>0?rateLimit(rate,signal):null;
      const timer=setInterval(()=>{
        if(limiter?.waiting)lastData=Date.now();
        else if(Date.now()-lastData>60000)stream.destroy(new DownloadError('MEGA_TIMEOUT','MEGA 读取超时，断点已保留',{retryable:true}));
      },1000);
      try {await pipeline(stream,...(limiter?[limiter]:[]),monitor,fs.createWriteStream(part,{flags:offset?'a':'w'}),{signal});}
      finally {clearInterval(timer);stream.destroy();}
    }
    log('MEGA：核对完整文件解密校验值（含已续传部分）…');
    if(!await verify(part,node,signal)){preserve(part,'invalid-mac');throw new DownloadError('MEGA_MAC','MEGA 完整文件校验未通过，未标记完成');}
    signal?.throwIfAborted();
    if(fs.existsSync(row.local_path))preserve(row.local_path,'previous-version');
    fs.renameSync(part,row.local_path);
    store.update(row,{status:'COMPLETED',local_size:info.size,error:null});
    onProgress({downloaded:info.size,total:info.size,speed:0});
  } catch(error) {store.update(row,{status:'PENDING',local_size:sizeOf(part),error:signal?.aborted?'已停止':megaError(error).message});throw megaError(error);}
}
async function downloadMega(source,options,{signal,pacer,onProgress=()=>{},log=()=>{},interact,session={}}={}) {
  const store=new StateStore(options.output,'mega_local_download_state.json');
  const {protectedPayload,unlock}=require('./mega-password');
  const protectedBytes=protectedPayload(source);
  const separateKey=/^https:\/\/mega\.nz\/(?:file|folder)\/[\w-]+\/?$/.test(source);
  let resolved=session.resolved || source, message='';
  while (!session.resolved && (protectedBytes || separateKey)) {
    if (!interact)throw new DownloadError('MEGA_INPUT','请在下载模块中单独输入此链接的密码或解密密钥。');
    const value=await interact({kind:protectedBytes?'password':'key',message});
    if(typeof value!=='string')throw new DownloadError('MEGA_SKIP','已跳过本次下载。');
    try {
      resolved=protectedBytes?await unlock(protectedBytes,value):source+'#'+value.trim().replace(/^#/,'');
      parseLink(resolved);break;
    } catch(error) {
      if(!['MEGA_KEY','MEGA_PASSWORD','MEGA_LINK'].includes(error.code))throw error;
      message=protectedBytes?error.message:'解密密钥格式不正确，请重新输入。';
    }
  }
  const selection=parseLink(resolved);
  const cmd = options.megaMode === 'megacmd' ? require('./megacmd') : null;
  const run = cmd && cmd.commander(cmd.clientPath(options.megacmdPath));
  const network=options.proxy?require('./vendor/network.cjs'):null;
  const dispatcher=network?new network.ProxyAgent(options.proxy):null;
  const timeoutMs=options.transfer?.connectTimeoutMs||60000;
  const api=new (sdk().API)(false,{fetch:async(url,init={})=>{
    signal?.throwIfAborted();
    const metadata=new URL(url).pathname.endsWith('/cs');
    if(metadata)await pacer?.wait(signal);
    const streaming=options.megaConnections===1&&!metadata;
    // Streaming transfers may take hours: only headers have a total timeout;
    // the pipeline watches inactivity, while stop remains attached throughout.
    const headers=new AbortController();
    const headerTimer=streaming?setTimeout(()=>headers.abort(new DownloadError('MEGA_CONNECTION','MEGA 响应头超时',{retryable:true})),timeoutMs):null;
    let response;
    try {response=await (network?.fetch||fetch)(url,{...init,...(dispatcher?{dispatcher}:{}),
      signal:AbortSignal.any([signal,init.signal,streaming?headers.signal:AbortSignal.timeout(timeoutMs)].filter(Boolean))});}
    finally {if(headerTimer)clearTimeout(headerTimer);}
    if(response.status===509||response.status===429) {
      const quota=response.status===509;
      const wait=Math.max(retryAfter(response.headers.get('retry-after'))||0,
        quota ? retryAfter(response.headers.get('x-mega-time-left'))||0 : 0);
      await response.body?.cancel();
      throw new DownloadError(quota?'MEGA_QUOTA':'MEGA_RATE',quota?'MEGA 流量额度不足':'MEGA 请求过于频繁',
        {pauseQueue:true,retryAfterMs:wait});
    }
    if(!response.ok)throw httpError(response.status,Object.fromEntries(response.headers));
    return streaming?streamingResponse(response):response;
  }});
  try {
    const node=sdk().File.fromURL(selection.base,{api});await node.loadAttributes();signal?.throwIfAborted();
    let files=flatten(node,selection);
    if(protectedBytes || separateKey)session.resolved=resolved;
    const saved=store.data.tasks[source]?.picked_ids;
    if (session.ids || protectedBytes || separateKey || saved || (interact && options.megaPick && node.directory)) {
      let ids=session.ids;
      if (!ids && interact) ids=await interact({kind:'files',files:files.map(({id,name,dirs,size})=>({id,name,dirs,size})),selected:saved||[]});
      if (!ids && !interact)ids=saved;
      if (!Array.isArray(ids)||!ids.length)throw new DownloadError('MEGA_SKIP','已跳过本次下载。');
      const wanted=new Set(ids);
      files=files.filter(f=>wanted.has(f.id));
      if(new Set(files.map(f=>f.id)).size!==wanted.size)throw new DownloadError('MEGA_CHANGED','所选文件已变动，请重新打开清单核对。');
      session.ids=ids;
      store.task(source,{picked_ids:ids});
    }
    store.task(source,{platform:'mega',status:'DOWNLOADING',file_ids:files.map(x=>x.id)});
    for(let i=0;i<files.length;i++) {
      signal?.throwIfAborted();
      const file = files[i];
      const progress = p => onProgress({...p,filename:file.name,fileIndex:i+1,fileCount:files.length});
      if (options.megaMode === 'revival') {
        await require('./revival').transferRevival(file,store,source,options,{signal,onProgress:progress});
      } else if (cmd) {
        const url = node.directory ? selection.base+'/file/'+nodeId(file.node) : selection.base;
        await cmd.transferCmd(file,store,source,{signal,run,url,onProgress:progress,log,
          beforeSubmit:()=>pacer?.wait(signal)});
      } else {
        await transferFile(file,store,source,{signal,connections:options.megaConnections||2,
          rate:options.rateLimit||0,log,onProgress:progress});
      }
    }
    store.task(source,{status:'COMPLETED',error:null});return files.length;
  } catch(error){store.task(source,{status:'PENDING',error:signal?.aborted?'已停止':megaError(error).message});throw megaError(error);}
  finally {api.close();await dispatcher?.destroy();}
}
module.exports={parseLink,flatten,verify,transferFile,downloadMega,megaError,streamingResponse};
