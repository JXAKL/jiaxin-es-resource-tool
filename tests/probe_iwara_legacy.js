// One homepage navigation; observe only. No reload, login submission or downloads.
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {Engine}=require('../organizer/downloads/js/runner');
async function main(){
 const state=path.resolve(process.argv[2]);
 const platform=process.argv[3]||'iwara';
 if(!['iwara','hanime'].includes(platform))throw Error('Unsupported probe');
 const engine=new Engine({state,output:path.join(state,'unused-output'),browser:'Chrome',visible:true,
  [platform+'Legacy']:true,[platform+'Profile']:path.join(state,'download-browser',platform+'-legacy-chrome'),requestInterval:8,perMinute:6},chromium);
 try{
  await engine.browser(platform);let navigations=0;const codes=[];
  engine.page.on('framenavigated',frame=>{if(frame===engine.page.mainFrame())navigations++;});
  engine.page.on('response',r=>{if(r.request().isNavigationRequest()&&r.frame()===engine.page.mainFrame())codes.push(r.status());});
  await engine.page.beforeNavigation();
  await engine.page.goto(platform==='iwara'?'https://www.iwara.tv/':'https://hanime1.me/',{waitUntil:'domcontentloaded',timeout:60000});
  await engine.page.waitForTimeout(15000);
  const result=await engine.page.evaluate(()=>({title:document.title,
   challenge:/just a moment|verify.*human|security verification|请稍候|安全验证/i.test(document.title) || !!document.querySelector('#challenge-form,#cf-challenge-running,iframe[src*="challenges.cloudflare.com"]'),
   videoLinks:document.querySelectorAll('a[href*="/video/"],a[href*="/watch?"]').length,
   loginVisible:[...document.querySelectorAll('a,button')].some(n=>/^login$|^登录$/i.test(n.textContent.trim()))}));
  fs.writeFileSync(path.join(state,'homepage-result.json'),JSON.stringify({...result,navigations,codes},null,2));
  console.log(JSON.stringify({...result,navigations,codes}));
 }finally{await engine.close();}
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
