// Opt-in live diagnostics, never part of application startup or self-test.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {Engine} = require('../organizer/downloads/js/runner');
const {pageFiles, pixeldrainFiles, metadata} = require('../organizer/downloads/js/adapters');
const {GoFileClient} = require('../organizer/downloads/js/gofile');
const {HttpSession, validFile} = require('../organizer/downloads/js/transfer');
const examples = {
  iwara:'https://www.iwara.tv/video/54FBbhPG6F2LSS/lyfe-exid-upanddown',
  rule34:'https://rule34video.com/video/3113435/bremerton-bassknight-gang-bang-ngon/',
  hanime:'https://hanime1.me/watch?v=110270',
  pixeldrain:'https://pixeldrain.com/l/qHUWKNnB',
  gofile:'https://gofile.io/d/l6wiKnLl',
};
async function main() {
  const results=[];
  for (const platform of (process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(examples))) {
    const root=path.resolve(__dirname,'../build/live-download-check',platform);
    fs.mkdirSync(root,{recursive:true});
    const state=path.join(process.env.LOCALAPPDATA,'EroScriptsOrganizer','.state');
    const engine=new Engine({state,browser:process.env.LIVE_BROWSER || 'Chromium',visible:true},chromium);
    let session, info, context, result={platform};
    try {
      context=await engine.browser(platform); const page=engine.page, url=examples[platform];
      const failures=[];
      page.on('requestfailed', r=>failures.push({host:new URL(r.url()).hostname,error:r.failure()?.errorText}));
      if(process.env.LIVE_ENGINE === '1') {
        engine.options.output=root;
        engine.config={...engine.config,connectTimeoutMs:15000,readTimeoutMs:15000,retryDelays:[1500,3000]};
        await engine.run([{platform,url}]);
        const {StateStore}=require('../organizer/downloads/js/transfer');
        const store=new StateStore(root,'resource_download_state.json');
        result={...result,outcome:await store.completed(url)?'ENGINE_DOWNLOAD_VERIFIED':'ENGINE_FAILED',error:store.data.tasks[url]?.error};
        fs.writeFileSync(path.join(root,'engine-result.json'),JSON.stringify(result,null,2));
        console.log(JSON.stringify(result));continue;
      }
      const client=platform==='gofile'?new GoFileClient(context,page,{signal:engine.signal,timeoutMs:45000,log:()=>{}}):null;
      let files=client?await client.files(url):platform==='pixeldrain'?await pixeldrainFiles(context,url):await pageFiles(page,url,platform);
      result.count=files.length;
      info=files.slice().sort((a,b)=>(a.size??Infinity)-(b.size??Infinity))[0];
      if (!info) throw new Error('No files');
      if (client) info.link=await client.resolve(info);
      const userAgent=await page.evaluate(()=>navigator.userAgent);
      session=new HttpSession({headersFor:async target=>({ 'User-Agent':userAgent,Referer:new URL(url).origin+'/', Cookie:(await context.cookies(target)).map(x=>x.name+'='+x.value).join('; ')})});
      if (info.browserDownload) {
        const dest=path.join(root,'download.bin');
        await info.browserDownload.saveAs(dest);
        result={...result,bytes:fs.statSync(dest).size,outcome:await info.browserDownload.failure()?'DOWNLOAD_FAILED':'BROWSER_DOWNLOAD_COMPLETED'};
      } else {
        info=await metadata(session,info,engine.signal);
        result.bytes=info.size;
        const dest=path.join(root,'download.bin');
        // Keep diagnostics bounded; a sampled large transfer is never called complete.
        if(info.size<=250*1024*1024) {
          await session.download({url:info.link,dest,expectedSize:info.size,md5:info.md5,signal:AbortSignal.timeout(180000)});
          result.outcome=await validFile(dest,info.size,info.md5)?'FULL_DOWNLOAD_VERIFIED':'VERIFICATION_FAILED';
        } else {
          const response=await session.open(info.link,{Range:'bytes=0-1048575'},AbortSignal.timeout(45000));
          let received=0;
          try {for await(const chunk of response){received+=chunk.length;if(received>=1048576)break;}}
          finally {response.destroy();}
          result={...result,outcome:'LARGE_FILE_SAMPLED_ONLY',httpStatus:response.statusCode,sampledBytes:received};
        }
      }
    } catch(error) {
      result.outcome='FAILED';result.code=error.code||error.name;result.error=error.message.slice(0,500);
      if(info?.link && context && error.status===403) {
        result.directHost=new URL(info.link).hostname;
        const probe=await context.request.get(info.link,{headers:{Range:'bytes=0-1023',Referer:examples[platform]},timeout:30000}).catch(()=>null);
        if(probe) {
          result.browserHttpStatus=probe.status();result.contentType=probe.headers()['content-type'];
          if(probe.status()===403)result.responseMessage=(await probe.text()).slice(0,300);
          await probe.dispose();
        }
      }
      if(engine.page) {
        result.controls=await engine.page.locator('button,a,[role="button"],input[type="password"]').evaluateAll(nodes=>nodes.filter(n=>n.getBoundingClientRect().width&&n.getBoundingClientRect().height).map(n=>({tag:n.tagName,text:(n.innerText||n.textContent||'').trim().slice(0,50),class:n.className,href:n.getAttribute('href')?.split('?')[0],html:n.tagName==='BUTTON'?n.outerHTML.slice(0,700):undefined})).filter(n=>/download|source|sign in|login|下载|下載|登录|登入|确认|確認|18/i.test(n.text)||n.tag==='INPUT')).catch(()=>[]);
      }
    } finally {session?.close();await engine.close();}
    fs.writeFileSync(path.join(root,'result.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));results.push(result);
  }
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
