// Opt-in real-site diagnostics. Never runs from the shipped application's self-test.
// Reads only the selected download queue item; does not print share keys or credentials.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../.packaging-venv/Lib/site-packages/playwright/driver/package');
const { Engine } = require('../organizer/downloads/js/runner');
const { navigate, saveMega } = require('../organizer/downloads/js/adapters');

async function snapshot(page) {
  return page.locator('button, [role="button"], a, [role="dialog"], .mega-dialog, .toast-notification, .toast-rack')
    .evaluateAll(nodes => nodes.filter(n => n.getBoundingClientRect().width && n.getBoundingClientRect().height)
      .map(n => ({tag:n.tagName, role:n.getAttribute('role'), text:(n.innerText || n.textContent || '').trim().slice(0,180),
        class:n.className, section:n.getAttribute('data-section'), label:n.getAttribute('aria-label'),
        visible:getComputedStyle(n).visibility, html:n.tagName==='BUTTON'?n.outerHTML.slice(0,1100):undefined}))
      .filter(n => /save|import|cloud|login|log in|sign in|download|保存|导入|雲端|云盘|登录|登陆|登錄|下載|下载|duplicate|已存在|skip|跳过|error|失败|錯誤/i.test(n.text)));
}

async function main() {
  const state = path.join(process.env.LOCALAPPDATA, 'EroScriptsOrganizer', '.state');
  const queue = JSON.parse(fs.readFileSync(path.join(state, 'download-queue.json'), 'utf8'));
  const index = Number(process.argv[2] || 0);
  const item = queue.items.filter(x => new URL(x.url).hostname === 'mega.nz')[index];
  if (!item) throw new Error('No selected MEGA queue item');
  const engine = new Engine({state, browser:queue.options.browser, visible:true}, chromium);
  const output = path.resolve(__dirname, '../build/live-download-check');
  fs.mkdirSync(output, {recursive:true});
  try {
    const started = Date.now();
    await engine.browser('mega');
    const failures = [];
    engine.page.on('requestfailed', request => failures.push({host:new URL(request.url()).hostname, error:request.failure()?.errorText}));
    await navigate(engine.page, item.url);
    console.log('NAVIGATION_MS', Date.now()-started);
    await engine.page.locator('button').filter({visible:true}).first().waitFor({state:'visible', timeout:30000}).catch(() => {});
    await engine.page.waitForTimeout(5000);
    const result = {kind:new URL(item.url).pathname.split('/')[1], title:await engine.page.title(),
      controls:await snapshot(engine.page), failures};
    fs.writeFileSync(path.join(output, `mega-${index}-before.json`), JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
    if (process.argv.includes('--picker')) {
      console.log('SAVE_ROLE_MATCHES', await engine.page.getByRole('button', {name:/^(保存到\s*MEGA|Save to MEGA|Import to MEGA)$/i}).count());
      await engine.page.locator('button').filter({hasText:/^\s*(保存到\s*MEGA|Save to MEGA|Import to MEGA)\s*$/i}).filter({visible:true}).first().click({timeout:30000});
      await engine.page.locator('.fm-picker-dialog-button, .dialog-picker-button, input[type="password"]').filter({visible:true}).first().waitFor({state:'visible',timeout:30000}).catch(()=>{});
      const picker = {elapsedMs:Date.now()-started, controls:await snapshot(engine.page),
        pages:await Promise.all(engine.context.pages().map(async p=>({title:await p.title(),path:new URL(p.url()).pathname})))};
      fs.writeFileSync(path.join(output, `mega-${index}-picker.json`), JSON.stringify(picker,null,2));
      console.log('PICKER', JSON.stringify(picker,null,2));
      const currentDialog = engine.page.locator('.fm-picker-dialog').filter({visible:true}).first();
      await currentDialog.locator('button.fm-picker-dialog-button[data-section="cloud-drive"]').click();
      console.log('PICKER_BUTTONS', JSON.stringify(await currentDialog.locator('button').filter({visible:true}).evaluateAll(nodes=>nodes.map(n=>({text:n.innerText,class:n.className,disabled:n.disabled})))));
      if (process.argv.includes('--confirm')) {
        const dialog = engine.page.locator('.fm-picker-dialog').filter({visible:true}).first();
        await dialog.locator('button.fm-picker-dialog-button[data-section="cloud-drive"]').click();
        await dialog.locator('button.dialog-picker-button.active').click();
        const observations = [];
        for(let i=0;i<15;i++) {
          await engine.page.waitForTimeout(1000);
          const messages = await snapshot(engine.page);
          observations.push({second:i+1, controls:messages});
        }
        fs.writeFileSync(path.join(output, `mega-${index}-confirmed.json`), JSON.stringify(observations,null,2));
        console.log('AFTER_CONFIRM', JSON.stringify(observations.filter((x,i)=>!i||JSON.stringify(x.controls)!==JSON.stringify(observations[i-1].controls)),null,2));
      }
    }
    if (process.argv.includes('--save')) {
      const saved = await saveMega(engine.page, item.url);
      console.log('LIVE_SAVE_RESULT', JSON.stringify(saved));
      console.log('AFTER', JSON.stringify(await snapshot(engine.page)));
    }
  } finally { await engine.close(); }
}
main().catch(error => { console.error(error.code || error.name, error.message); process.exitCode = 1; });
