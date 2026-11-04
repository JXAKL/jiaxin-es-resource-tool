// Live login handoff; user enters account credentials only into the official site.
const fs=require('node:fs');const path=require('node:path');
const {chromium}=require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {Engine}=require('../organizer/downloads/js/runner');
const sites={iwara:'https://www.iwara.tv/video/54FBbhPG6F2LSS/lyfe-exid-upanddown',hanime:'https://hanime1.me/watch?v=110270'};
async function main(){
 const platform=process.argv[2];if(!sites[platform])throw Error('Unsupported test site');
 const engine=new Engine({state:path.join(process.env.LOCALAPPDATA,'EroScriptsOrganizer','.state'),browser:'Chrome',visible:true},chromium);
 process.stdin.setEncoding('utf8');process.stdin.on('data',text=>{if(text.includes('FINISH_LOGIN'))engine.loginDone=true;if(text.includes('STOP'))void engine.stop();});process.stdin.resume();
 try{
  await engine.login({platform,url:sites[platform]});
  console.log(JSON.stringify({platform,login_handoff_complete:true,host:new URL(engine.page.url()).hostname}));
 }finally{await engine.close();process.stdin.pause();}
}
main().catch(e=>{console.error(e.code||e.name,e.message);process.exitCode=1;});
