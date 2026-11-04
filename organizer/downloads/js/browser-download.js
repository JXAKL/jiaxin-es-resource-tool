"use strict";
const {DownloadError, sanitize}=require('./transfer');

// Use the site's normal Download control. CAPTCHA/login completion belongs to
// the user; this module neither solves challenges nor constructs signed URLs.
async function pixeldrainDownload(page, info, {signal, onWaiting=()=>{}, timeout=600000}={}) {
  let downloaded, closed=false;
  const receive=value=>{downloaded=value;};
  const close=()=>{closed=true;};
  page.on('download',receive); page.on('close',close);
  const stop=()=>{
    signal?.throwIfAborted();
    if(closed || page.isClosed()) throw new DownloadError('BROWSER_CLOSED','官网下载窗口已关闭；此文件未完成');
  };
  try {
    stop();
    await page.beforeNavigation?.();
    const response=await page.goto(info.pageUrl,{waitUntil:'domcontentloaded',timeout:45000});
    if(response && response.status()>=400 && ![401,403].includes(response.status()))
      throw new DownloadError('PAGE_UNAVAILABLE',`官网分享页返回 HTTP ${response.status()}`);
    const label=info.archive ? /^(?:download\s+)?DL all files$/i : /^(?:download\s+)?Download$/i;
    const button=page.getByRole('button',{name:label}).filter({visible:true}).first();
    const deadline=Date.now()+timeout;
    let clicked=false, notified=false, challengeNotified=false;
    while(!downloaded) {
      stop();
      if(Date.now()>deadline) throw new DownloadError('WEBSITE_WAIT_TIMEOUT','等待官网下载或人工验证超时；未标为完成，可稍后继续');
      if(!clicked && await button.isVisible().catch(()=>false)) {
        await page.beforeNavigation?.();
        stop();
        await button.click(); clicked=true;
      }
      if(!notified) {
        onWaiting(info.archive ? '正在通过官网“下载全部”保存整个相册，不逐个请求文件。' : '正在等待官网下载，收到文件后自动保存。');
        notified=true;
      }
      if(!downloaded && !challengeNotified && await page.locator('iframe[title*="reCAPTCHA"], input[type="password"]').filter({visible:true}).count()) {
        onWaiting('官网显示登录或人机验证，自动下载暂未开始；保留当前页面，不重复请求。');
        challengeNotified=true;
      }
      await page.waitForTimeout(200);
    }
    if(info.archive ? !/\.zip$/i.test(downloaded.suggestedFilename()) :
      info.name && sanitize(downloaded.suggestedFilename()) !== sanitize(info.name)) {
      await downloaded.cancel().catch(()=>{});
      throw new DownloadError('WRONG_BROWSER_FILE','浏览器返回的文件名与当前任务不同，未写入该任务；请只操作当前下载文件');
    }
    return downloaded;
  } catch(error) {
    if(closed || page.isClosed()) throw new DownloadError('BROWSER_CLOSED','官网下载窗口已关闭；此文件未完成');
    throw error;
  } finally {page.off('download',receive);page.off('close',close);}
}
async function pixeldrainPageFiles(page,url,options={}) {
  const archive=new URL(url).pathname.startsWith('/l/');
  const download=await pixeldrainDownload(page,{pageUrl:url,archive},options);
  return [{id:'pixeldrain-'+require('node:crypto').createHash('sha256').update(url).digest('hex').slice(0,24),
    name:sanitize(download.suggestedFilename()),dirs:[],browserDownload:download}];
}
module.exports={pixeldrainDownload,pixeldrainPageFiles};
