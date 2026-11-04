const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {DownloadPacer}=require('../organizer/downloads/js/pacing');
test('request intervals and rolling cap apply across restart, with cancellable cooldown',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'download-pacing-'));
 try{
  let clock=100000;const sleeps=[];
  const opts={interval:8,perMinute:2,now:()=>clock,sleep:async ms=>{sleeps.push(ms);clock+=ms;}};
  const file=path.join(root,'pacing.json');const first=new DownloadPacer(file,opts);
  await first.wait();assert.equal(clock,100000);
  await first.wait();assert.equal(clock,108000);
  const again=new DownloadPacer(file,opts);await again.wait();assert.equal(clock,160000);
  again.cooldown(120000);
  const restored=new DownloadPacer(file,opts);await restored.wait();assert.equal(clock,280000);
  const stop=new AbortController();stop.abort();
  await assert.rejects(restored.wait(stop.signal));
  assert.ok(sleeps.every(ms=>ms<=1000));
  assert.throws(()=>new DownloadPacer(file,{interval:NaN}));
  assert.throws(()=>new DownloadPacer(file,{perMinute:1.5}));
 }finally{if(path.dirname(root)!==os.tmpdir())throw Error('unexpected test path');fs.rmSync(root,{recursive:true});}
});
