"use strict";
const { DownloadError } = require('./transfer');

const SAVE = /^\s*(?:保存到\s*MEGA|儲存到\s*MEGA|Save to MEGA|Import to MEGA)\s*$/i;
const SUCCESS = /(?:已保存到|已成功导入|导入成功|saved to|successfully (?:saved|imported)|imported successfully)/i;

async function saveMega(page, url, { navigate, log = () => {}, onSubmitted = () => {}, timeout = 60000 } = {}) {
  let submitted = false, skipped = 0;
  const visible = selector => page.locator(selector).filter({visible:true});
  try {
    log('MEGA：读取分享页，等待保存入口。');
    await navigate(page, url);
    // Live file/folder pages expose visible buttons that role lookup misses.
    // Folder buttons also contain surrounding whitespace.
    const save = visible('button').filter({hasText:SAVE}).first();
    await save.waitFor({state:'visible', timeout});
    await save.click();
    const dialog = visible('.fm-picker-dialog').first();
    try { await dialog.waitFor({state:'visible', timeout}); }
    catch {
      if (await visible('input[type="password"]').count())
        throw new DownloadError('MEGA_LOGIN_REQUIRED', 'MEGA 尚未登录，请通过“打开所选网站登录”完成登录后重试');
      throw new DownloadError('MEGA_PICKER_MISSING', 'MEGA 保存位置对话框未出现，尚未提交保存');
    }
    log('MEGA：选择云盘，等待保存按钮可用。');
    const cloud = dialog.locator('button.fm-picker-dialog-button[data-section="cloud-drive"]');
    await cloud.waitFor({state:'visible', timeout});
    await cloud.click();
    const confirm = dialog.locator('button.dialog-picker-button.active').filter({visible:true}).first();
    await confirm.waitFor({state:'visible', timeout});
    // An old success notification must disappear before this submission begins.
    await visible('.toast-notification').filter({hasText:SUCCESS}).first().waitFor({state:'hidden',timeout:10000});
    await page.beforeNavigation?.();
    await onSubmitted();
    submitted = true;
    await confirm.click();
    log('MEGA：已提交保存，等待结果；重复项目会选择“不保存”。');
    const deadline = Date.now() + timeout;
    let duplicateClosedAt = 0;
    while (Date.now() < deadline) {
      const duplicate = visible('.mega-dialog.duplicate-conflict').first();
      if (await duplicate.count()) {
        const skip = duplicate.locator('button.action-block.a2').filter({hasText:/不保存|不儲存|don.t save|skip/i});
        if (!await skip.count()) throw new DownloadError('MEGA_UNCONFIRMED', '出现重复项目，但没有识别到“不保存”操作，请在 MEGA 中核对');
        const before = await duplicate.innerText();
        await skip.click();
        skipped++;
        await page.waitForFunction(text => {
          const dialog = [...document.querySelectorAll('.mega-dialog.duplicate-conflict')].find(n => n.getBoundingClientRect().width && getComputedStyle(n).visibility !== 'hidden');
          return !dialog || dialog.innerText !== text;
        }, before, {timeout:10000});
        duplicateClosedAt = Date.now();
        continue;
      }
      const messages = await visible('.toast-notification, .toast-rack, [role="status"]').allInnerTexts();
      const message = messages.find(text => SUCCESS.test(text) && !/失败|无法|未能|failed|unable|could not/i.test(text));
      if (message) return {status:'网盘已保存', detail:message.trim().replace(/\s+/g,' ') + (skipped ? `；另跳过 ${skipped} 个重复项` : ''), skipped};
      const errors = await visible('.mega-dialog').filter({hasText:/空间不足|超出.*配额|not enough space|over quota|保存失败|import failed/i}).count();
      if (errors) throw new DownloadError('MEGA_UNCONFIRMED', 'MEGA 提示空间或保存错误，请在云盘中核对；本项不会自动重复提交');
      if (skipped && Date.now()-duplicateClosedAt >= 2500 && !await visible('.mega-dialog').count())
        return {status:'重复已跳过', detail:`已按“不保存”跳过 ${skipped} 个重复项，未覆盖或重命名现有文件`, skipped};
      await page.waitForTimeout(250);
    }
    throw new DownloadError('MEGA_UNCONFIRMED', '已提交保存，但未确认结果；请在 MEGA 中核对，本项不会自动重试');
  } catch (error) {
    if (submitted && error.code !== 'MEGA_UNCONFIRMED')
      throw new DownloadError('MEGA_UNCONFIRMED', '保存已提交，随后页面中断或结果不明；请核对云盘，本项不会自动重复提交');
    if (!submitted && !(error instanceof DownloadError))
      throw new DownloadError('MEGA_PAGE_NOT_READY', 'MEGA 页面或保存按钮未就绪，尚未提交保存。请检查页面、登录和网络后重试');
    throw error;
  }
}

module.exports = {saveMega};
