const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {chromium}=require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {pixeldrainDownload,pixeldrainPageFiles}=require('../organizer/downloads/js/browser-download');
const {navigate,pageFiles}=require('../organizer/downloads/js/adapters');
const {Engine}=require('../organizer/downloads/js/runner');
const {StateStore}=require('../organizer/downloads/js/transfer');
test('official button, delayed manual completion, mismatch, cancellation and auth page',async()=>{
 const payload=Buffer.from('{"actions":[{"at":0,"pos":50}]}');
 let gateRequests=0,nextRequests=0;
 const hanimeRequests=[];
 const server=http.createServer((req,res)=>{
  if(req.url==='/file') {res.writeHead(200,{'Content-Type':'application/json','Content-Length':payload.length,'Content-Disposition':'attachment; filename="sample.funscript"'});res.end(payload);}
  else if(req.url==='/zip') {res.writeHead(200,{'Content-Type':'application/zip','Content-Disposition':'attachment; filename="album.zip"'});res.end(Buffer.from('504b0304','hex'));}
  else if(req.url==='/l/album')res.end('<button onclick="location.href=\'/zip\'">download DL all files</button><button>download Download</button>');
  else if(req.url==='/auth') {res.writeHead(403,{'Content-Type':'text/html'});res.end('<h1>Sign in</h1><input type="password">');}
  else if(req.url==='/watch?gate') {hanimeRequests.push(req.url);res.writeHead(403,{'Content-Type':'text/html'});res.end('<button id="complete-watch" onclick="document.querySelector(\'#ready\').hidden=false">Complete local fixture</button><div id="ready" hidden><a href="/download?gate">Download page</a></div>');}
  else if(req.url==='/download?gate') {hanimeRequests.push(req.url);res.end('<title>Just a moment</title><div id="challenge-form"></div><button id="complete-download" onclick="document.querySelector(\'#ready\').hidden=false;document.title=\'Fixture\';document.querySelector(\'#challenge-form\').remove()">Complete local fixture</button><div id="ready" hidden><a download="fixture.mp4" href="/file">1080p</a></div>');}
  else if(req.url==='/iwara-gate') {gateRequests++;res.writeHead(403,{'Content-Type':'text/html'});res.end('<button id="complete" onclick="document.querySelector(\'#ready\').hidden=false">Complete local fixture</button><div id="ready" hidden><button class="downloadButton">Download</button><a download href="/file">Source</a></div>');}
  else if(req.url==='/never-visit') {nextRequests++;res.end('unexpected next task');}
  else if(req.url==='/iwara') res.end('<button class="downloadButton">Download</button><a download="sample.funscript" href="/auth" onclick="location.href=\'/file\';return false">Source</a>');
  else if(req.url==='/manual')res.end('<button onclick="document.querySelector(\'#gate\').hidden=false">download Download</button><div id="gate" hidden>Manual fixture action<button id="complete" onclick="location.href=\'/file\'">Complete fixture</button></div>');
  else if(req.url==='/empty')res.end('<h1>Waiting for sign in</h1>');
  else res.end('<button onclick="location.href=\'/file\'">download Download</button>');
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base=`http://127.0.0.1:${server.address().port}`;
 const browser=await chromium.launch({channel:'chromium',headless:true});
 const context=await browser.newContext({acceptDownloads:true}),page=await context.newPage();
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'download-browser-'));
 try{
  for(const route of ['normal','manual']) {
   let notices=0;
   const pending=pixeldrainDownload(page,{pageUrl:base+'/'+route,name:'sample.funscript'},{onWaiting:()=>{notices++;},timeout:3000});
   if(route==='manual')await page.locator('#complete').click(); // Local fixture, never a real challenge.
   const download=await pending;
   const dest=path.join(root,route+'.funscript');await download.saveAs(dest);
   assert.deepEqual(fs.readFileSync(dest),payload);assert.equal(notices,1);
   assert.equal(page.listenerCount('download'),0);
  }
  await assert.rejects(pixeldrainDownload(page,{pageUrl:base+'/normal',name:'other.funscript'},{timeout:2000}),{code:'WRONG_BROWSER_FILE'});
  const controller=new AbortController();
  await assert.rejects(pixeldrainDownload(page,{pageUrl:base+'/empty',name:'sample.funscript'},
   {signal:controller.signal,onWaiting:()=>controller.abort(),timeout:2000}));
  assert.equal(page.listenerCount('download'),0);
  const [album]=await pixeldrainPageFiles(page,base+'/l/album',{timeout:2000});
  assert.equal(album.name,'album.zip');assert.ok(album.browserDownload);
  await assert.rejects(navigate(page,base+'/auth'),{status:403});
  const engine=new Engine({visible:true,output:root,requestInterval:0,perMinute:0},null);engine.browser=async()=>{engine.page=page;return context;};
  engine.loginDone=true;
  const login=engine.login({platform:'iwara',url:base+'/auth'});
  await page.getByRole('heading',{name:'Sign in'}).waitFor();
  await page.waitForTimeout(200);
  assert.equal(engine.loginDone,false);assert.equal(page.isClosed(),false);
  engine.loginDone=true;await login;
  engine.options.output=root;engine.config.retryDelays=[1];
  let directRequests=0;
  engine.pacer.wait=async()=>{directRequests++;};
  await engine.run([{platform:'iwara',url:base+'/iwara'}]);
  assert.equal(directRequests,0,'Iwara uses the browser Source button without direct HTTP metadata/download calls');
  assert.equal(await new StateStore(root,'resource_download_state.json').completed(base+'/iwara'),true);
  let waiting=0;
  const gated=pageFiles(page,base+'/iwara-gate','iwara',{native:true,authTimeout:3000,onWaiting:()=>waiting++});
  await page.locator('#complete').waitFor();await page.waitForTimeout(200);
  assert.equal(gateRequests,1);
  await page.locator('#complete').click(); // Local fixture only; no real verification automation.
  const [released]=await gated;assert.ok(released.browserDownload);assert.equal(waiting,1);
  assert.equal(gateRequests,1,'manual completion must not trigger another navigation');
  engine.options.visible=false;
  await engine.run([{platform:'iwara',url:base+'/iwara-gate'},{platform:'iwara',url:base+'/never-visit'}]);
  assert.equal(gateRequests,2,'one navigation in the new task, no 403 retries');
  assert.equal(nextRequests,0,'a blocked website must pause the queue');
  assert.equal(new StateStore(root,'resource_download_state.json').data.tasks[base+'/iwara-gate'].status,'PENDING');
  let hanimeWaits=0;
  const hanime=pageFiles(page,base+'/watch?gate','hanime',{authTimeout:3000,onWaiting:()=>hanimeWaits++});
  await page.locator('#complete-watch').click();
  await page.locator('#complete-download').waitFor();
  await page.waitForTimeout(100);
  await page.locator('#complete-download').click();
  const [hanimeFile]=await hanime;
  assert.equal(hanimeFile.link,base+'/file');
  assert.ok(hanimeWaits>=1);
  assert.deepEqual(hanimeRequests,['/watch?gate','/download?gate'],'both pages must avoid automatic challenge refresh');
  await engine.run([{platform:'hanime',url:base+'/watch?gate'},{platform:'hanime',url:base+'/never-visit'}]);
  assert.equal(nextRequests,0);
  assert.equal(hanimeRequests.length,3);
  assert.equal(new StateStore(root,'resource_download_state.json').data.tasks[base+'/watch?gate'].status,'PENDING');
  engine.session?.close();
 } finally {
  await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));
  if(path.dirname(root)!==os.tmpdir())throw Error('unexpected test path');
  fs.rmSync(root,{recursive:true,force:true});
 }
});
