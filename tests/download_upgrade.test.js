'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),http=require('node:http');
const {Readable,Writable}=require('node:stream');
const {pipeline}=require('node:stream/promises');
const mega=require('../organizer/downloads/js/vendor/megajs.cjs');
const {parseLink,flatten,transferFile,verify,downloadMega,megaError,streamingResponse}=require('../organizer/downloads/js/mega-local');
const {StateStore,HttpSession}=require('../organizer/downloads/js/transfer');
const {bestLink}=require('../organizer/downloads/js/adapters');
const {Engine}=require('../organizer/downloads/js/runner');

async function fixture(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'es-mega-upgrade-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const plain=Buffer.alloc(96*1024,0x73);plain.write('{"actions":[]}');
  const secret=crypto.randomBytes(24),enc=mega.encrypt(secret),parts=[];
  await pipeline(Readable.from([Buffer.from(plain)]),enc,new Writable({write(c,e,cb){parts.push(c);cb();}}));
  const key=enc.key,cipher=Buffer.concat(parts);
  const calls=[];
  const node={name:'fixture.funscript',key,size:plain.length,downloadId:'FileOne',directory:false,
    download(options){calls.push(options);return Readable.from([plain.subarray(options.start||0)]);}};
  return {root,plain,secret,cipher,key,node,calls,info:{node,id:'mega-fixture',name:node.name,size:plain.length,dirs:['Share','Scripts'],md5:null}};
}
test('quality cap selects available lower quality, never unknown or above cap',()=>{
  const links=[{href:'/source',text:'Source'},{href:'/1080',text:'1080p'},{href:'/480',text:'480p'},{href:'/unknown'}];
  assert.equal(bestLink(links,'https://example.invalid').href,'https://example.invalid/source');
  assert.equal(bestLink(links,'https://example.invalid','720').href,'https://example.invalid/480');
  assert.equal(bestLink(links,'https://example.invalid','360'),undefined);
});
test('single-connection SDK adapter applies backpressure and propagates body errors',async()=>{
  let pulled=0;
  const response=new Response(new ReadableStream({pull(controller){pulled++;controller.enqueue(new Uint8Array(65536));}}));
  const adapted=streamingResponse(response);
  const sink=new Writable({write(c,e,cb){setTimeout(cb,60);}});
  const done=new Promise(resolve=>sink.once('close',resolve));
  adapted.body.pipe(sink);
  await new Promise(r=>setTimeout(r,110));sink.destroy();await done;
  assert.ok(pulled<12,'Large transfer was eagerly buffered');
  const broken=streamingResponse(new Response(new ReadableStream({pull(c){c.error(Error('fixture failure'));}})));
  const target=new Writable({write(c,e,cb){cb();}});
  const error=new Promise(resolve=>target.once('error',resolve));broken.body.pipe(target);
  assert.match((await error).message,/fixture failure/);
});
test('MEGA legacy links and folder deep links retain selection and reject wrong key',()=>{
  const key=Buffer.alloc(16,1).toString('base64url');
  const value=parseLink(`https://mega.io/folder/Folder#${key}/folder/Child`);
  assert.equal(value.child,'Child');assert.equal(value.kind,'folder');assert.ok(value.base.startsWith('https://mega.nz/'));
  assert.equal(parseLink(`https://mega.nz/#F!Folder!${key}!File`).child,'File');
  assert.throws(()=>parseLink('https://mega.nz/file/A#short'),{code:'MEGA_KEY'});
});
test('recursive folder scope includes all axes, excludes siblings and handles cycle',async t=>{
  const f=await fixture(t),one=f.node,two={...f.node,name:'fixture.pitch.funscript',downloadId:['Share','Axis']};
  const child={directory:true,name:'Scripts',downloadId:['Share','Child'],children:[one,two]};
  const root={directory:true,name:'Share',children:[child,{...one,name:'unrelated',downloadId:'Other'}]};
  root.children.push(root);
  const files=flatten(root,{child:'Child',kind:'folder'});
  assert.equal(files.length,2);assert.deepEqual(files[0].dirs,['Scripts']);
  assert.equal(flatten(root,{child:'Axis',kind:'file'}).length,1);
  assert.throws(()=>flatten(root,{child:'Missing',kind:'file'}),{code:'MEGA_MISSING'});
});
test('MEGA resumes then verifies full plaintext, detects same-size tampering and protects names',async t=>{
  const f=await fixture(t),store=new StateStore(f.root,'state.json');
  const row=store.register('source',f.info);fs.mkdirSync(path.dirname(row.local_path),{recursive:true});
  fs.writeFileSync(row.local_path+'.part',f.plain.subarray(0,123));
  await transferFile(f.info,store,'source',{connections:3});
  assert.equal(f.calls[0].start,123);assert.equal(f.calls[0].maxConnections,3);
  assert.deepEqual(fs.readFileSync(row.local_path),f.plain);
  await transferFile(f.info,store,'source');assert.equal(f.calls.length,1);
  fs.writeFileSync(row.local_path,Buffer.alloc(f.plain.length));
  await transferFile(f.info,store,'source');assert.equal(f.calls.length,2);
  assert.ok(fs.readdirSync(path.dirname(row.local_path)).some(n=>n.includes('previous-version')));
});
test('MEGA corrupted partial cannot become completed after range download',async t=>{
  const f=await fixture(t),store=new StateStore(f.root,'state.json'),row=store.register('source',f.info);
  fs.mkdirSync(path.dirname(row.local_path),{recursive:true});fs.writeFileSync(row.local_path+'.part',Buffer.alloc(123));
  await assert.rejects(transferFile(f.info,store,'source'),{code:'MEGA_MAC'});
  assert.equal(fs.existsSync(row.local_path),false);assert.equal(row.status,'PENDING');
});
test('MEGA cancellation during throttle keeps partial and never finalizes',async t=>{
  const f=await fixture(t),store=new StateStore(f.root,'state.json'),control=new AbortController();
  const pending=transferFile(f.info,store,'source',{signal:control.signal,rate:64*1024});
  setTimeout(()=>control.abort(),240);
  await assert.rejects(pending);const row=store.data.files[f.info.id];
  assert.ok(fs.statSync(row.local_path+'.part').size>0);assert.equal(fs.existsSync(row.local_path),false);
});
test('quota and local errors never overwrite uncertain cloud submissions',async t=>{
  const f=await fixture(t),store=new StateStore(f.root,'resource_download_state.json');
  const source='https://mega.nz/file/Fixture#invalid';store.task(source,{status:'CLOUD_SUBMITTED'});
  const engine=new Engine({output:f.root,state:f.root,megaMode:'local',interval:0},null);
  await engine.run([{url:source,platform:'mega'}]);await engine.close();
  assert.equal(new StateStore(f.root,'resource_download_state.json').data.tasks[source].status,'CLOUD_SUBMITTED');
  assert.equal(megaError(Error('EOVERQUOTA (-17)')).pauseQueue,true);
});
test('actual MEGA SDK metadata, encrypted transfer and MAC verification through loopback',async t=>{
  const f=await fixture(t);
  const attr=Buffer.from('MEGA'+JSON.stringify({n:f.node.name}));
  const padded=Buffer.alloc(Math.ceil(attr.length/16)*16);attr.copy(padded);
  const aes=crypto.createCipheriv('aes-128-cbc',f.secret.subarray(0,16),Buffer.alloc(16));aes.setAutoPadding(false);
  const at=Buffer.concat([aes.update(padded),aes.final()]).toString('base64url');
  let base,requests=0;
  const server=http.createServer(async(req,res)=>{
    requests++;
    if(req.url.startsWith('/cs')) {for await(const _ of req){}res.setHeader('content-type','application/json');res.end(JSON.stringify([{s:f.plain.length,at,g:base+'/file'}]));}
    else {
      const m=/\/(\d+)-(\d+)$/.exec(req.url),data=f.cipher.subarray(+m[1],+m[2]+1);
      res.write(data.subarray(0,1024));setTimeout(()=>res.end(data.subarray(1024)),450);
    }
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;
  const realFetch=global.fetch;global.fetch=(url,init)=>realFetch(String(url).startsWith('https://g.api.mega.co.nz/')?base+new URL(url).pathname+new URL(url).search:url,init);
  try {
    const source=`https://mega.nz/file/Fixture#${f.key.toString('base64url')}`;
    assert.equal(await downloadMega(source,{output:f.root,megaConnections:1,transfer:{connectTimeoutMs:200}}),1);
    const store=new StateStore(f.root,'mega_local_download_state.json');
    assert.equal(await store.completed(source),true);assert.ok(requests>=3);
    assert.deepEqual(fs.readFileSync(Object.values(store.data.files)[0].local_path),f.plain);
  } finally {global.fetch=realFetch;server.closeAllConnections();await new Promise(r=>server.close(r));}
});
test('HTTP rate cap is measured and does not trigger read/low-speed retries',async t=>{
  const f=await fixture(t),server=http.createServer((req,res)=>{res.setHeader('content-length',f.plain.length);res.end(f.plain);});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const session=new HttpSession({rateLimit:128*1024,readTimeoutMs:100,slowWindowMs:100,minBytesPerSecond:1024*1024,progressIntervalMs:20});
  try {const start=performance.now();await session.download({url:`http://127.0.0.1:${server.address().port}/file`,dest:path.join(f.root,'http.funscript'),expectedSize:f.plain.length});
    assert.ok(performance.now()-start>=650);
  } finally {session.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('HTTP downloads use the explicit proxy without resolving the target locally',async t=>{
  const f=await fixture(t),urls=[];
  const proxy=http.createServer((req,res)=>{urls.push(req.url);res.setHeader('content-length',f.plain.length);res.end(f.plain);});
  await new Promise(r=>proxy.listen(0,'127.0.0.1',r));
  const session=new HttpSession({proxy:`http://127.0.0.1:${proxy.address().port}`});
  try {await session.download({url:'http://unresolvable-fixture.invalid/file',dest:path.join(f.root,'proxy.funscript'),expectedSize:f.plain.length});
    assert.deepEqual(urls,['http://unresolvable-fixture.invalid/file']);
    assert.deepEqual(fs.readFileSync(path.join(f.root,'proxy.funscript')),f.plain);
  } finally {session.close();proxy.closeAllConnections();await new Promise(r=>proxy.close(r));}
});
