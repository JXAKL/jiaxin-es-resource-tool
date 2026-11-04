const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {chromium}=require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const {saveMega}=require('../organizer/downloads/js/mega');
const {Engine}=require('../organizer/downloads/js/runner');
const {StateStore}=require('../organizer/downloads/js/transfer');

test('MEGA visible controls, explicit cloud selection, success/duplicate/unknown results',async()=>{
  const browser=await chromium.launch({channel:'chromium',headless:true});
  try {
    for(const mode of ['success','duplicate','unknown','missing-cloud']) {
      const page=await browser.newPage();let submitted=0;
      const navigate=async()=>page.setContent(`
        <div aria-hidden="true"><button id="primary" onclick="this.remove();document.querySelector('.fm-picker-dialog').hidden=false"> <i></i><span>保存到MEGA</span> </button></div>
        <div class="mega-dialog fm-picker-dialog" hidden>
          ${mode==='missing-cloud'?'':'<button class="fm-picker-dialog-button" data-section="cloud-drive" onclick="window.cloud=true;document.querySelector(\'.dialog-picker-button\').disabled=false">云盘</button>'}
          <button class="dialog-picker-button active" disabled onclick="if(!window.cloud)throw Error('cloud required');this.parentElement.hidden=true;document.querySelector('#result').hidden=false">保存到MEGA</button>
        </div>
        ${mode==='success'?'<div id="result" class="toast-notification" hidden>1个文件已保存到 云盘 查看</div>':mode==='duplicate'?'<div id="result" class="mega-dialog duplicate-conflict" hidden><button class="action-block a1" onclick="window.overwrite=true">保存并更新</button><button class="action-block a2" onclick="this.parentElement.hidden=true;window.skipped=true">不保存</button><button class="action-block a3" onclick="window.renamed=true">保存并重命名</button></div>':'<div id="result" hidden>处理中</div>'}`);
      const promise=saveMega(page,'fixture',{navigate,onSubmitted:()=>{submitted++},timeout:mode==='duplicate'?4000:700});
      if(mode==='success') assert.equal((await promise).status,'网盘已保存');
      else if(mode==='duplicate') {
        assert.equal((await promise).status,'重复已跳过');
        assert.deepEqual(await page.evaluate(()=>[!!window.skipped,!!window.overwrite,!!window.renamed]),[true,false,false]);
      } else await assert.rejects(promise,{code:mode==='unknown'?'MEGA_UNCONFIRMED':'MEGA_PAGE_NOT_READY'});
      assert.equal(submitted,mode==='missing-cloud'?0:1);
      await page.close();
    }
  } finally {await browser.close();}
});

test('previous cloud submission survives restart and cannot silently resubmit',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'mega-state-'));
  try {
    const source='https://mega.nz/file/fixture#test',store=new StateStore(root,'resource_download_state.json');
    store.task(source,{platform:'mega',status:'CLOUD_SUBMITTED'});
    const engine=new Engine({output:root,megaSave:true,interval:0},null);
    engine.browser=async()=>{throw Error('must not launch browser or resubmit')};
    await engine.run([{platform:'mega',url:source}]);
    const saved=new StateStore(root,'resource_download_state.json').data.tasks[source];
    assert.equal(saved.platform,'mega');assert.equal(saved.status,'CLOUD_UNCONFIRMED');
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
