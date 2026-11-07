'use strict';
const {test}=require('node:test'), assert=require('node:assert/strict');
const path=require('node:path'),os=require('node:os'),fs=require('node:fs'),http=require('node:http');
const {Readable}=require('node:stream');
const {runWorker}=require('../organizer/downloads/js/revival');
const {MegaCooldown}=require('../organizer/downloads/js/mega-cooldown');
const mega=require('../organizer/downloads/js/vendor/megajs.cjs');
function temp(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ES Revival '));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;
}
async function listen(t,handle) {
  const server=http.createServer(handle);
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  t.after(()=>{server.closeAllConnections();return new Promise(r=>server.close(r));});
  return 'http://127.0.0.1:'+server.address().port+'/fixture';
}
test('real Revival engine resumes durable ranges across processes and changed parallelism',{skip:process.platform!=='win32'},async t=>{
  const directory=temp(t),plain=require('crypto').randomBytes(12*1024*1024);
  const cipher=mega.encrypt(),chunks=[];
  Readable.from([Buffer.from(plain)]).pipe(cipher);
  for await (const chunk of cipher) chunks.push(chunk);
  const encrypted=Buffer.concat(chunks),ranges=[];
  const url=await listen(t,(req,res)=>{
    const match=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range||'');
    const start=match?Number(match[1]):0, end=match&&match[2]?Number(match[2]):plain.length-1;
    ranges.push(start);
    res.writeHead(match?206:200,{'Content-Length':end-start+1,'Content-Range':`bytes ${start}-${end}/${plain.length}`});
    let offset=start;
    const timer=setInterval(()=>{const next=Math.min(offset+65536,end+1);res.write(encrypted.subarray(offset,next));offset=next;if(offset>end)res.end();},45);
    res.on('close',()=>clearInterval(timer));
  });
  const job={directory,url,key:cipher.key.toString('base64url'),size:plain.length,id:'fixture',connections:2,rate:0};
  const stop=new AbortController();
  await assert.rejects(runWorker(job,AbortSignal.any([stop.signal,AbortSignal.timeout(30000)]),()=>{
    const file=path.join(directory,'offsets.json');
    if(fs.existsSync(file)&&JSON.parse(fs.readFileSync(file)).some(n=>n>0))stop.abort();
  }));
  assert.ok(stop.signal.aborted);
  const before=JSON.parse(fs.readFileSync(path.join(directory,'offsets.json')));
  assert.ok(before.some(n=>n>0));ranges.length=0;
  await runWorker({...job,connections:4},AbortSignal.timeout(30000),()=>{});
  assert.ok(ranges.some(start=>start>0 && start!==plain.length/2));
  assert.deepEqual(fs.readFileSync(path.join(directory,'payload.bin')),plain);
});
test('actual provider quota response defers MEGA only, retries after cooldown and allows stop',async t=>{
  const {Engine}=require('../organizer/downloads/js/runner');
  const {StateStore}=require('../organizer/downloads/js/transfer');
  const dir=temp(t), store=new StateStore(dir,'resource_download_state.json');
  const other='https://gofile.io/d/fixture',url='https://mega.nz/file/fixture#'+Buffer.alloc(32,1).toString('base64url');
  const row=store.register(other,{id:'other',name:'other',dirs:[],size:1});
  fs.writeFileSync(row.local_path,'x');store.update(row,{status:'COMPLETED'});
  store.task(other,{status:'COMPLETED',file_ids:['other']});
  const engine=new Engine({output:dir,state:dir,megaMode:'revival',requestInterval:0,perMinute:0},null);
  let requests=0, waits=0;const events=[];
  t.mock.method(console,'log',s=>{if(s.startsWith('RESOURCE_EVENT '))events.push(JSON.parse(s.slice(15)));});
  t.mock.method(global,'fetch',async()=>{requests++;return new Response('',{status:509,headers:{'x-mega-time-left':'9000'}});});
  t.mock.method(MegaCooldown.prototype,'wait',async function(){
    waits++;
    assert.ok(this.remaining()>8900000);
    assert.ok(events.some(e=>e.url===other && e.status==='已校验跳过'));
    if(waits===1)this.state.until=0;
    else {await engine.stop();throw engine.signal.reason;}
  });
  await engine.run([{url,platform:'mega'},{url:other,platform:'gofile'}]);
  assert.equal(requests,2);assert.equal(waits,2);
  assert.ok(events.at(-1).stopped);
});
test('quota cooldown survives restart, respects server hints, distinguishes HTTP 429',t=>{
  const root=temp(t), hour=3600000, start=Date.now(), c=new MegaCooldown(root);
  c.hit({code:'MEGA_RATE',retryAfterMs:90000},start);
  assert.equal(c.remaining(start),90000);
  c.hit({code:'MEGA_QUOTA'},start+90001);
  const resumed=new MegaCooldown(root);
  assert.equal(resumed.remaining(start+90001),hour);
  resumed.hit({code:'MEGA_QUOTA',retryAfterMs:3*hour},start+hour+90002);
  assert.equal(resumed.state.level,1);assert.equal(resumed.remaining(start+hour+90002),3*hour);
});
for(const status of [429,509])test('Revival data server HTTP '+status+' reaches queue as a distinct wait',{skip:process.platform!=='win32'},async t=>{
  const directory=temp(t);let hits=0;
  const url=await listen(t,(_req,res)=>{hits++;res.writeHead(status,{'Retry-After':'9000'});res.end();});
  await assert.rejects(runWorker({directory,url,key:Buffer.alloc(32,1).toString('base64url'),size:128,
    id:'quota-fixture',rate:0,connections:1},AbortSignal.timeout(20000),()=>{}),
    {code:status===429?'MEGA_RATE':'MEGA_QUOTA',retryAfterMs:9000000});
  assert.ok(hits<=2);
});
