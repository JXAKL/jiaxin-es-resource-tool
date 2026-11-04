'use strict';
const {DownloadError}=require('./transfer');

// A quality button can either download directly or open the actual video page.
// Keep both paths inside the same browser session and bind them to this file.
async function clickQualityDownload(page, control, expectedUrl, {signal, timeout=120000, filename='video.mp4', onVideoPage=()=>{}}={}) {
  signal?.throwIfAborted();
  const context=page.context(), observed=new Map();
  let timer, resolveResult, rejectResult, settled=false, disposed=false;
  const pending=new Promise((resolve,reject)=>{resolveResult=resolve;rejectResult=reject;});
  pending.catch(()=>{});
  const fail=error=>{if(!settled){settled=true;rejectResult(error);}};
  const aborted=()=>fail(signal.reason || new DownloadError('STOPPED','已停止'));
  const closed=()=>fail(new DownloadError('BROWSER_CLOSED','下载浏览器已关闭；本项没有标记完成'));
  const normalize=raw=>{try{const u=new URL(raw);u.hash='';return u.href;}catch{return '';}};
  const allowed=new Set([normalize(expectedUrl)]);
  const redirect=request=>{
    const previous=request.redirectedFrom();
    if(previous && allowed.has(normalize(previous.url())))allowed.add(normalize(request.url()));
  };
  const watchVideoPage=async p=>{
    if(typeof p.locator!=='function')return;
    let saved=false;
    while(!settled && !disposed && !signal?.aborted && !p.isClosed()) {
      // Only the file page opened for this exact quality is eligible. A video
      // elsewhere on the source/download page is not the requested file.
      if(!saved && allowed.has(normalize(p.url()))) {
        const source=await p.locator('video').evaluateAll(nodes=>nodes.map(n=>{
          n.autoplay=false;n.removeAttribute('autoplay');n.preload='none';n.pause();
          return n.currentSrc || n.src;
        }).filter(Boolean)).catch(()=>[]);
        const videoUrl=source.find(raw=>allowed.has(normalize(raw)));
        if(videoUrl) {
          saved=true;onVideoPage();
          // Chrome displays inline media as a video document. Its same-origin
          // download link invokes the browser's own save operation, using the
          // existing cookies and connection settings (no separate HTTP client).
          await p.evaluate(({url,name})=>{
            const link=document.createElement('a');link.href=url;link.download=name;
            document.body.appendChild(link);link.click();link.remove();
          },{url:videoUrl,name:filename});
        }
      }
      if(!settled && !disposed)await p.waitForTimeout(250).catch(()=>{});
    }
  };
  const observe=p=>{
    if(observed.has(p))return;
    const listener=download=>{
      if(settled)return;
      if(!allowed.has(normalize(download.url()))){void download.cancel().catch(()=>{});return;}
      settled=true;resolveResult(download);
    };
    observed.set(p,listener);p.on('download',listener);
    void watchVideoPage(p).catch(error=>{
      if(!settled && !disposed && !p.isClosed())fail(new DownloadError('VIDEO_PAGE_SAVE_FAILED',
        '已打开视频页，但浏览器保存未完成：'+error.message,{pauseQueue:true}));
    });
  };
  context.on('page',observe);context.on('close',closed);context.on('request',redirect);observe(page);
  signal?.addEventListener('abort',aborted,{once:true});
  timer=setTimeout(()=>fail(new DownloadError('BROWSER_DOWNLOAD_NOT_STARTED',
    '点击 Hanime 官网画质按钮后未收到对应文件下载；未重复点击或切换下一条链接。',{pauseQueue:true})),timeout);
  try {
    await page.beforeNavigation?.();
    signal?.throwIfAborted();
    // A navigation to an ad may make click wait fail after the download starts.
    const originalUrl=page.url?.();
    await control.click({timeout:Math.min(timeout,30000),noWaitAfter:true}).catch(error=>{
      if(!settled && observed.size===1 && page.url?.()===originalUrl)fail(new DownloadError('BROWSER_DOWNLOAD_CLICK_FAILED',
        'Hanime 官网下载按钮操作未完成，已保留当前任务：'+error.message,{pauseQueue:true}));
    });
    const download=await pending;
    return {download,cleanup:async()=>{
      for(const p of observed.keys())if(p!==page && !p.isClosed())await p.close().catch(()=>{});
    }};
  } finally {
    disposed=true;
    clearTimeout(timer);signal?.removeEventListener('abort',aborted);
    context.off('page',observe);context.off('close',closed);
    context.off('request',redirect);
    for(const [p,listener] of observed)p.off('download',listener);
  }
}
module.exports={clickQualityDownload};
