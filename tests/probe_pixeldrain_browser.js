// Explicit live test: one small existing script through the normal website UI.
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {Engine}=require('../organizer/downloads/js/runner');
const {pixeldrainFiles}=require('../organizer/downloads/js/adapters');
const {pixeldrainDownload}=require('../organizer/downloads/js/browser-download');
const {validFile}=require('../organizer/downloads/js/transfer');
async function main(){
 const engine=new Engine({state:path.join(process.env.LOCALAPPDATA,'EroScriptsOrganizer','.state'),browser:'Chrome',visible:true},chromium);
 try{
  const context=await engine.browser('pixeldrain');
  const files=await pixeldrainFiles(context,'https://pixeldrain.com/l/qHUWKNnB');
  const info=files.find(f=>f.name.endsWith('.roll.funscript'));
  const download=await pixeldrainDownload(engine.page,info,{signal:engine.signal,onWaiting:text=>console.log(text)});
  const dest=path.resolve('build/live-download-check/pixeldrain/website.funscript');
  await download.saveAs(dest);
  if(await download.failure() || !await validFile(dest,info.size,info.md5))throw Error('File verification failed');
  const body=JSON.parse(fs.readFileSync(dest,'utf8'));
  const result={outcome:'PRODUCTION_BROWSER_DOWNLOAD_VERIFIED',bytes:fs.statSync(dest).size,actions:body.actions.length};
  fs.writeFileSync(path.resolve('build/live-download-check/pixeldrain/website-result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 }finally{await engine.close();}
}
main().catch(e=>{console.error(e.code||e.name,e.message);process.exitCode=1;});
