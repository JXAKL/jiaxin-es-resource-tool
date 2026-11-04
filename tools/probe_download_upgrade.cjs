// Explicit live probe: only one existing public share, one small file, no cloud writes.
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const {parseLink,flatten,downloadMega}=require('../organizer/downloads/js/mega-local');
const mega=require('../organizer/downloads/js/vendor/megajs.cjs');
const network=require('../organizer/downloads/js/vendor/network.cjs');
async function main() {
  const candidates=[path.join(process.env.LOCALAPPDATA,'EroScriptsOrganizer/.state/download-queue.json'),path.join(root,'.state/download-queue.json')];
  const queuePath=process.argv[2]||candidates.find(p=>fs.existsSync(p));
  if(!queuePath)throw Error('No existing download queue available for a live sample');
  const queue=JSON.parse(fs.readFileSync(queuePath,'utf8'));
  const item=queue.items.find(x=>/^https:\/\/(www\.)?mega\.(nz|io)\//.test(x.url));
  if(!item)throw Error('No MEGA share in existing queue');
  const proxy=queue.options?.proxy||process.env.ES_TEST_PROXY||'';
  const dispatcher=proxy?new network.ProxyAgent(proxy):null;
  const control=new AbortController(),timer=setTimeout(()=>control.abort(),120000);
  let last=0,count=0;
  const pace={async wait(signal){const ms=8000-(Date.now()-last);if(ms>0)await require('node:timers/promises').setTimeout(ms,undefined,{signal});last=Date.now();count++;}};
  const api=new mega.API(false,{fetch:async(url,init={})=>{
    await pace.wait(control.signal);
    return network.fetch(url,{...init,...(dispatcher?{dispatcher}:{}),signal:control.signal});
  }});
  try {
    const selection=parseLink(item.url),node=mega.File.fromURL(selection.base,{api});
    await node.loadAttributes();
    const files=flatten(node,selection).filter(x=>x.size>0&&x.size<=2*1024*1024).sort((a,b)=>a.size-b.size);
    if(!files.length)throw Error('Share metadata loaded; no small file within the 2 MiB probe limit');
    const chosen=files[0],id=Array.isArray(chosen.node.downloadId)?chosen.node.downloadId.at(-1):null;
    const source=id?selection.base+'/file/'+id:selection.base;
    const output=path.join(root,'build/live-upgrade-2.1.13');
    const result=await downloadMega(source,{output,megaConnections:1,proxy,rateLimit:2*1048576},{signal:control.signal,pacer:pace});
    console.log(JSON.stringify({provider:'MEGA',files:result,bytes:chosen.size,verified:true,metadataRequests:count,proxyUsed:!!proxy,cloudWrites:false}));
  } finally {clearTimeout(timer);api.close();await dispatcher?.destroy();}
}
main().catch(e=>{console.log(JSON.stringify({verified:false,error:String(e.message).replace(/https?:\/\/\S+/g,'[address omitted]')}));process.exitCode=1;});
