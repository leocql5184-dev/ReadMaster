const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('./playwright.cjs');
(async()=>{
 const base=process.env.READMASTER_URL,id=process.env.READMASTER_FIXTURE_ID;
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.request.post(base+'/api/models/skip',{headers:{'X-ReadMaster':'1'},data:{}});
  await page.goto(base);await page.locator('#startupSplash').waitFor({state:'hidden'});
  await page.locator('#fileInput').setInputFiles('D:/readmaster/.build/babel-pdf-fixture/scientific-source.pdf');
  const sentence=page.locator('.sentence[data-sentence="p001_b003_001"]');await sentence.waitFor();
  await page.locator('.page-image').first().evaluate(img=>img.complete?Promise.resolve():new Promise(r=>img.onload=r));
  const initial=await sentence.textContent(),frame=await sentence.locator('..').boundingBox();
  const counter=await page.locator('#wordCount').textContent();assert.match(counter,/字数：\d+/);
  await sentence.hover();assert.equal(await sentence.textContent(),initial);
  await sentence.click();assert.match(await sentence.textContent(),/proposed scientific method/);
  assert.equal(await sentence.locator('mark').count(),1);
  assert.deepEqual(await sentence.locator('..').boundingBox(),frame);
  assert.equal(await page.locator('#wordCount').textContent(),counter);
  await page.mouse.move(1,1);assert.match(await sentence.textContent(),/proposed scientific method/);
  const other=page.locator('.sentence[data-sentence="p001_b002_001"]'),otherText=await other.textContent();
  await other.click();assert.match(await sentence.textContent(),/proposed scientific method/);
  await other.click();assert.equal(await other.textContent(),otherText);
  await sentence.click();assert.equal(await sentence.textContent(),initial);
  await sentence.focus();await page.keyboard.press('Enter');assert.match(await sentence.textContent(),/proposed scientific method/);
  await page.keyboard.press('Escape');assert.equal(await sentence.textContent(),initial);
  await sentence.evaluate(e=>{const range=document.createRange();range.selectNodeContents(e);const s=window.getSelection();s.removeAllRanges();s.addRange(range);});
  await page.waitForFunction(()=>document.querySelector('#wordCount').textContent.includes('/'));
  await page.evaluate(()=>window.getSelection().removeAllRanges());
  await page.locator('#termsButton').click();assert.match(await page.locator('#panelBody').textContent(),/科研方法/);
  await page.locator('#closePanel').click();
  await page.locator('#searchButton').click();await page.locator('#searchInput').fill('accuracy');
  await page.waitForFunction(()=>!document.querySelector('#searchCount').textContent.startsWith('0 '));
  await page.locator('#searchClose').click();
  await page.locator('#outlineTab').click();assert.match(await page.locator('.outline-entry').first().textContent(),/Methods.*方法/);
  await page.locator('.outline-entry').first().click();
  await page.locator('#zoom').selectOption('1.25');await page.locator('#originalButton').click();assert.equal(await page.locator('.sentence').count(),0);
  await page.locator('#chineseButton').click();await sentence.waitFor();
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/progress')&&r.status()===200);
  await page.locator('#pageInput').fill('1');await page.locator('#pageInput').dispatchEvent('change');await saved;
  const doc=await (await page.request.get(base+'/api/document/'+id)).json();assert.equal(doc.progress.page,1);
  const ids=await page.locator('.sentence').evaluateAll(es=>es.map(e=>e.dataset.sentence));
  assert.equal(new Set(ids).size,6);assert.equal(ids.length,6);
  await page.locator('#settingsButton').click();await page.locator('#newModelProfile').click();
  assert.deepEqual(await page.locator('#modelVendor option:not([disabled])').evaluateAll(es=>es.map(e=>e.value)),['openai','deepseek','zhipu']);
  await page.locator('#modelVendor').selectOption('zhipu');assert.match(await page.locator('#modelEndpoint').inputValue(),/open\.bigmodel\.cn/);
  await page.locator('#modelProvider').selectOption('codex');assert.ok(await page.locator('#modelCodexFields').isVisible());
  await page.locator('#modelsClose').click();
  assert.equal(await page.locator('#exportButton,#exportDialog').count(),0);assert.deepEqual(errors,[]);
  await page.screenshot({path:'D:/readmaster/.build/babel-reader-regression.png'});
  await page.locator('#libraryButton').click();assert.ok(await page.locator('#empty').isVisible());
  assert.equal(await page.locator('.source-popup').count(),0);assert.deepEqual(errors,[]);
  const report={passed:true,fixture:'synthetic approved PDF with glossary and outline fixtures',
   click_hover_keyboard:true,independent_sentence_ids:true,terminology_highlight_and_panel:true,
   outline_navigation:true,search:true,word_count_and_selection:true,zoom_and_language:true,
   reading_progress_saved:true,four_model_entries:true,export_ui_removed:true,library_reset:true,browser_errors:errors};
  fs.writeFileSync('D:/readmaster/.build/babel-reader-regression.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
