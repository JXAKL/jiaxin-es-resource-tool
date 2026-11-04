"use strict";
// All HTTP/browser traffic in this check stays on the local fixture server.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { Engine } = require('./runner');
const { pageFiles, bestLink, metadata } = require('./adapters');
const { HttpSession, StateStore } = require('./transfer');

async function main() {
  const { chromium } = require(process.env.ES_RESOURCE_PLAYWRIGHT);
  const root = path.resolve(process.argv[2]);
  const payload = Buffer.alloc(65536, 0x57);
  payload.writeUInt32BE(24,0); payload.write('ftyp',4,'ascii');
  let requests = 0;
  const server = http.createServer((req, res) => {
    if (req.url === '/slow') {
      res.writeHead(200, {'Content-Type':'video/mp4', 'Content-Length':payload.length});
      res.write(payload.subarray(0, 1024));
      const timer = setTimeout(() => res.end(payload.subarray(1024)), 2000);
      res.on('close', () => clearTimeout(timer));
    } else if (req.url.startsWith('/video')) {
      requests++;
      res.writeHead(200, {'Content-Type':'video/mp4', 'Content-Length':payload.length}); res.end(payload);
    } else if (req.url === '/html') {
      res.writeHead(200, {'Content-Type':'text/html', 'Content-Length':18}); res.end('<html>error</html>');
    } else {
      res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
      if (req.url === '/watch') res.end('<title>Fixture</title><a href="/download?fixture">Download</a>');
      else if (req.url === '/iwara') res.end('<title>Fixture</title><button class="downloadButton">Download</button><a download="iwara.mp4" href="/video-iwara">Source</a><a download="iwara-480.mp4" href="/video-iwara480">480p</a>');
      else if (req.url === '/rule') res.end('<title>Fixture</title><a class="tag_item_download" href="/video-rule">1080p</a><a class="tag_item_download" href="/video-rule480">480p</a>');
      else res.end('<title>Fixture</title><a class="download-link" download="fixture.mp4" href="/video">1080p</a><a class="download-link" download="fixture-480.mp4" href="/video480">480p</a>');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const engine = new Engine({state:path.join(root, 'state'), output:path.join(root, 'output'),
    browser:'Chromium', visible:false, interval:0, requestInterval:0, perMinute:0, transfer:{retryDelays:[1]}}, chromium);
  const session = new HttpSession({progressIntervalMs:20});
  try {
    const item = {url:base+'/watch', platform:'hanime'};
    await engine.run([item]);
    const store = new StateStore(engine.options.output, 'resource_download_state.json');
    assert.equal(await store.completed(item.url), true);
    assert.deepEqual(fs.readFileSync(Object.values(store.data.files)[0].local_path), payload);
    const home=engine.context.pages().find(p=>p.url()===base+'/');
    assert.ok(home,'Hanime homepage remains open separately');
    assert.notEqual(home,engine.page);
    const previous = requests;
    await engine.run([item]);
    assert.equal(requests, previous, 'Verified completed file must not be downloaded again');
    engine.options.quality='720';await engine.run([item]);
    assert.equal(new StateStore(engine.options.output,'resource_download_state.json').data.tasks[item.url].quality,'720');
    assert.ok(requests>previous,'Changed quality must not reuse previous quality task');
    engine.options.quality='best';
    const page = engine.page;
    assert.equal((await pageFiles(page, base+'/iwara', 'iwara'))[0].name, 'iwara.mp4');
    assert.equal((await pageFiles(page,base+'/iwara','iwara',{quality:'720'}))[0].name,'iwara-480.mp4');
    await engine.run([{url:base+'/iwara',platform:'iwara'}]);
    assert.equal(home.isClosed(),false,'switching websites must retain the Hanime homepage');
    assert.equal(await new StateStore(engine.options.output,'resource_download_state.json').completed(base+'/iwara'),true);
    assert.equal((await pageFiles(engine.page, base+'/rule', 'rule34'))[0].link, base+'/video-rule');
    assert.equal((await pageFiles(engine.page,base+'/rule','rule34',{quality:'720'}))[0].link,base+'/video-rule480');
    assert.equal(bestLink([{href:'/a',text:'720p'}, {href:'/b',text:'1080p'}], base).href, base+'/b');
    await assert.rejects(metadata(session, {link:base+'/html'}, engine.signal), {code:'NOT_FILE'});
    await engine.run([{url:base+'/disabled-mega', platform:'mega'}, {url:base+'/next', platform:'hanime'}]);
    const updated = new StateStore(engine.options.output, 'resource_download_state.json');
    assert.equal(updated.data.tasks[base+'/disabled-mega'].status, 'FAILED');
    assert.equal(await updated.completed(base+'/next'), true, 'A failed item must not stop later items');
    assert.equal(home.url(),base+'/');assert.equal(home.isClosed(),false);
    const cancel = new AbortController(), partial = path.join(root, 'cancelled.mp4');
    await assert.rejects(session.download({url:base+'/slow', dest:partial, expectedSize:payload.length,
      signal:cancel.signal, onProgress:p => {if (p.downloaded > 0) cancel.abort();}}));
    assert.equal(fs.existsSync(partial), false);
    assert.ok(fs.statSync(partial+'.part').size > 0, 'Stopping must retain the partial file');
    await engine.stop();
    assert.equal(engine.signal.aborted, true);
    const {Readable,Writable}=require('node:stream'),{pipeline}=require('node:stream/promises');
    const mega=require('./vendor/megajs.cjs'),{transferFile}=require('./mega-local');
    const original=Buffer.from('small packaged SDK test'),encryption=mega.encrypt(Buffer.alloc(24,17));
    await pipeline(Readable.from([Buffer.from(original)]),encryption,new Writable({write(c,e,cb){cb();}}));
    const node={key:encryption.key,size:original.length,download:()=>Readable.from([original])};
    const megaStore=new StateStore(path.join(root,'mega'),'mega_local_download_state.json');
    await transferFile({node,id:'fixture',name:'fixture.funscript',size:node.size,dirs:[]},megaStore,'fixture');
    assert.deepEqual(fs.readFileSync(megaStore.data.files.fixture.local_path),original);
    console.log('DOWNLOAD_CHECK ' + JSON.stringify({bundled_node:true, browser_loopback:true,
      hanime_page_navigation:true, iwara_rule34_page_links:true, downloaded_bytes:payload.length,
      video_quality_controls:true,mega_bundled_sdk_integrity:true,
      validated_skip:true, rejects_html:true, failure_continues:true, cancellation_retains_partial:true}));
  } finally {
    session.close(); await engine.close();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
