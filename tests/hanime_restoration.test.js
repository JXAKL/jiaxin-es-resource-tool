'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {pageFiles,bestLink,metadata}=require('../organizer/downloads/js/adapters');
const {Engine}=require('../organizer/downloads/js/runner');
const {HttpSession,StateStore}=require('../organizer/downloads/js/transfer');

test('Hanime original DOM flow: hidden menu, delayed data-url, highest quality, referer, one file request and verified skip',async()=>{
 const requests=[];
 const payload=Buffer.alloc(16384,0x57);
 payload.writeUInt32BE(24,0);payload.write('ftyp',4,'ascii');
 const server=http.createServer((req,res)=>{
  requests.push({url:req.url,referer:req.headers.referer,range:req.headers.range,cookie:req.headers.cookie});
  const u=new URL(req.url,'http://localhost');
  if(u.pathname==='/'){
   res.setHeader('Set-Cookie','hanime-home=ready; Path=/');
   res.end('<title>Home</title><video autoplay muted src="/preview.mp4"></video><script>document.querySelector("video").play().catch(()=>{window.playBlocked=true;});</script>');
  }
  else if(u.pathname==='/watch') {
   // First matching anchor stays hidden. The actual entry appears via the old menu.
   const visible=u.searchParams.get('v')==='wide';
   res.end(`<a hidden href="/download?v=wrong">Hidden duplicate</a><button class="video-show-action-btn dropdown-toggle" onclick="document.querySelector('#entry').hidden=false">more_horiz</button><a id="entry" ${visible?'':'hidden'} target="_blank" href="/download?v=${u.searchParams.get('v')}">Download</a>`);
  } else if(u.pathname==='/download') {
   res.end(`<title>Fixture</title><table id="quality"></table><script>setTimeout(()=>{
    document.querySelector('#quality').innerHTML='<tr><td>720p</td><td><a download="Test title" data-url="/file-720.mp4?secure=a%2Bb%3D">下載</a></td></tr><tr><td>2160p</td><td><a download="Test title" data-url="/file-2160.mp4?secure=a%2Bb%3D">下載</a></td></tr><tr><td>1080p</td><td><a download="Test title" data-url="/file-1080.mp4">下載</a></td></tr>';
    for(const a of document.querySelectorAll('a[data-url]')){
      if(location.search.includes('inline'))a.dataset.url+='&inline=1';
      a.onclick=()=>{window.open(a.dataset.url);location.href='/advert';};
    }
   },100)</script>`);
  } else if(u.pathname==='/empty') res.end('<a download="broken.mp4">Download</a>');
  else if(u.pathname==='/fake')res.end('<a download="fake.mp4" data-url="/fake-file" onclick="location.href=this.dataset.url">1080p</a>');
  else if(u.pathname==='/fake-file') {res.writeHead(200,{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="fake.mp4"'});res.end('<html>not a video</html>');}
  else if(u.pathname.startsWith('/file-')) {
   res.writeHead(200,{'Content-Type':'video/mp4','Content-Length':payload.length,
     ...(u.searchParams.has('inline')?{}:{'Content-Disposition':'attachment; filename="server-name.mp4"'})});res.end(payload);
  } else {res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base=`http://127.0.0.1:${server.address().port}`;
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'hanime-restore-'));
 const browser=await chromium.launch({channel:'chromium',headless:true});
 const context=await browser.newContext({viewport:{width:640,height:720}}),page=await context.newPage();
 const engine=new Engine({visible:true,output:root,requestInterval:0,perMinute:0,transfer:{retryDelays:[]}},null);
 engine.browser=async()=>{engine.page=page;return context;};
 try {
  const source=base+'/download?v=narrow';
  await engine.run([{platform:'hanime',url:source}]);
  const store=new StateStore(root,'resource_download_state.json');
  assert.equal(await store.completed(source),true);
  assert.equal(requests[0].url,'/','homepage is opened before the download tab navigates');
  assert.equal(page.url(),base+'/');
  assert.notEqual(engine.page,page);
  assert.equal(await page.evaluate(()=>window.playBlocked),true);
  assert.equal(await page.locator('video').evaluate(v=>v.paused),true);
  assert.equal(requests.some(r=>r.url==='/preview.mp4'),false,'playback streaming must not reach the server');
  const row=Object.values(store.data.files)[0];
  assert.equal(row.filename,'Test title.mp4');
  assert.deepEqual(fs.readFileSync(row.local_path),payload);
  assert.equal(requests.filter(x=>x.url.startsWith('/watch')).length,1);
  assert.match(requests.find(x=>x.url.startsWith('/watch')).cookie,/hanime-home=ready/);
  const downloadPages=requests.filter(x=>x.url.startsWith('/download'));
  assert.equal(downloadPages.length,1);assert.equal(downloadPages[0].referer,base+'/watch?v=narrow');
  assert.deepEqual(requests.filter(x=>x.url.startsWith('/file-')).map(x=>x.url),['/file-2160.mp4?secure=a%2Bb%3D']);
  const before=requests.length;await engine.run([{platform:'hanime',url:source}]);
  assert.equal(requests.length,before,'completed items cause no website/file requests');
  const inlineSource=base+'/watch?v=inline';
  await engine.run([{platform:'hanime',url:inlineSource}]);
  const inlineStore=new StateStore(root,'resource_download_state.json');
  assert.equal(await inlineStore.completed(inlineSource),true,'a popup video page must be saved through the browser');
  assert.equal(context.pages().length,2,'homepage and download tab remain after saving');
  assert.equal(page.url(),base+'/');
  assert.equal(requests.filter(r=>r.url==='/').length,1,'homepage is not reloaded per task');
  const inlineRow=inlineStore.data.files[inlineStore.data.tasks[inlineSource].file_ids[0]];
  assert.deepEqual(fs.readFileSync(inlineRow.local_path),payload);
  const [wide]=await pageFiles(engine.page,base+'/watch?v=wide','hanime',{});
  assert.equal(wide.link,base+'/file-2160.mp4?secure=a%2Bb%3D');
  assert.equal(context.pages().length,2,'only the homepage and download tab remain');
  assert.equal(bestLink([{href:'',text:'4K'}],base),undefined);
  await assert.rejects(pageFiles(engine.page,base+'/empty','hanime',{}),{code:'NO_DOWNLOAD_LINK'});
  await engine.run([{platform:'hanime',url:base+'/fake'},{platform:'hanime',url:base+'/never-visit'}]);
  assert.equal(await new StateStore(root,'resource_download_state.json').completed(base+'/fake'),false);
  assert.equal(fs.existsSync(path.join(root,'fake.mp4')),false);
  assert.equal(requests.some(x=>x.url==='/never-visit'),false);
 } finally {
  engine.session?.close();await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));
  fs.rmSync(root,{recursive:true,force:true});
 }
});

test('download capture ignores ads, follows file redirects and removes listeners on timeout/stop',async()=>{
 const {EventEmitter}=require('node:events');
 const {clickQualityDownload}=require('../organizer/downloads/js/hanime-download');
 const context=new EventEmitter(),page=new EventEmitter(),popup=new EventEmitter();
 page.context=()=>context;popup.isClosed=()=>false;let cleaned=0;popup.close=async()=>cleaned++;
 const expected='https://files.example/video.mp4?secure=a%2Bb%3D';
 let clicks=0,cancelled=0;
 const actual={url:()=>expected};
 const result=await clickQualityDownload(page,{click:async()=>{
  clicks++;context.emit('page',popup);
  page.emit('download',{url:()=> 'https://ads.example/ad.exe',cancel:async()=>cancelled++});
  popup.emit('download',actual);
 }},expected,{timeout:1000});
 assert.equal(result.download,actual);assert.equal(clicks,1);assert.equal(cancelled,1);
 await result.cleanup();assert.equal(cleaned,1);assert.equal(popup.listenerCount('download'),0);
 const redirected={url:()=> 'https://cdn.example/file.mp4'};
 const from={url:()=>expected};
 assert.equal((await clickQualityDownload(page,{click:async()=>{
  context.emit('request',{redirectedFrom:()=>from,url:redirected.url});page.emit('download',redirected);
 }},expected,{timeout:1000})).download,redirected);
 await assert.rejects(clickQualityDownload(page,{click:async()=>{}},expected,{timeout:10}),{code:'BROWSER_DOWNLOAD_NOT_STARTED',pauseQueue:true});
 const controller=new AbortController();
 await assert.rejects(clickQualityDownload(page,{click:async()=>controller.abort()},expected,{signal:controller.signal,timeout:1000}));
 for(const event of ['page','close','request'])assert.equal(context.listenerCount(event),0);
 assert.equal(page.listenerCount('download'),0);
});
