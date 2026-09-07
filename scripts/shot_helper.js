// Browser Use: clip-mode responsive screenshots (single app page)
// 由 node_repl 调用；参数化：width/height/pageKey/pageSel/outPath
async function shotPage(tab, width, height, pageKey, pageSel, outPath) {
  await tab.setViewportSize({ width, height });
  await tab.playwright.waitForTimeout(1200);
  await tab.playwright.evaluate((sel) => {
    const normal = document.querySelector('#chatModeSwitch button[data-mode="normal"]');
    if (pageKey === 'chat' && normal) normal.click();
    const el = sel ? document.querySelector(sel) : null;
    if (el) el.click();
    return true;
  }, pageSel);
  await tab.playwright.waitForTimeout(1000);
  const dim = await tab.playwright.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }));
  const shot = await tab.screenshot({ clip: { x: 0, y: 0, width: dim.w, height: dim.h } });
  fs.writeFileSync(outPath, Buffer.from(shot));
  return dim;
}
nodeRepl.write('helper ready');
