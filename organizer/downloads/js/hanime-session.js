'use strict';
const {DownloadError}=require('./transfer');
const sessions=new WeakMap();

function preventPlayback() {
  const stop=media=>{media.autoplay=false;media.removeAttribute('autoplay');media.preload='none';media.pause();};
  HTMLMediaElement.prototype.play=function(){stop(this);return Promise.reject(new DOMException('Playback disabled during download','NotAllowedError'));};
  document.addEventListener('play',event=>{if(event.target instanceof HTMLMediaElement)stop(event.target);},true);
  const scan=()=>document.querySelectorAll('video,audio').forEach(stop);
  new MutationObserver(scan).observe(document,{childList:true,subtree:true});
  document.addEventListener('DOMContentLoaded',scan,{once:true});
}

async function prepareHanimePages(context, initialPage, url, {navigate,signal,beforeNavigation,visible=true,onWaiting=()=>{},authTimeout=600000}={}) {
  let session=sessions.get(context);
  if(!session) {
    await context.addInitScript(preventPlayback);
    // Block playback streaming only. Native file downloads are document
    // requests, so they continue through the browser's download manager.
    await context.route('**/*',route=>route.request().resourceType()==='media'?route.abort():route.continue());
    session={origins:new Map()};sessions.set(context,session);
  }
  const origin=new URL(url).origin;
  let tabs=session.origins.get(origin);
  if(!tabs){tabs={home:initialPage};session.origins.set(origin,tabs);}
  if(tabs.home.isClosed()){tabs.home=await context.newPage();tabs.ready=false;}
  tabs.home.beforeNavigation=beforeNavigation;
  if(!tabs.ready) {
    const homeUrl=origin+'/';
    if(tabs.home.url()!==homeUrl)await navigate(tabs.home,homeUrl,{allowAuth:true});
    const blocked=async()=>/just a moment|verify.*human|security verification|请稍候|安全验证/i.test(await tabs.home.title()) ||
      await tabs.home.locator('#challenge-form, #cf-challenge-running, iframe[src*="challenges.cloudflare.com"]').count()>0;
    if(await blocked()) {
      onWaiting('Hanime1：主页尚未放行，保留主页等待，不打开下载页或刷新。');
      const deadline=Date.now()+authTimeout;
      while(await blocked()) {
        signal?.throwIfAborted();
        if(!visible || Date.now()>=deadline)throw new DownloadError('SITE_VERIFICATION_REQUIRED','Hanime 主页尚未放行，下载任务已保留。',{pauseQueue:true});
        await tabs.home.waitForTimeout(500);
      }
    }
    tabs.ready=true;
  }
  signal?.throwIfAborted();
  if(!tabs.download || tabs.download.isClosed())tabs.download=await context.newPage();
  tabs.download.beforeNavigation=beforeNavigation;
  return tabs;
}
module.exports={prepareHanimePages,preventPlayback};
