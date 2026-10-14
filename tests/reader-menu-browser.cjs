const assert=require('node:assert/strict');
const {chromium}=require('./playwright.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage();
  await page.setContent('<div id="pages"><span class="sentence">这里允许复制文字。</span></div><input id="search"><div id="notice" hidden><span id="noticeText"></span></div>');
  await page.addStyleTag({path:'web/style.css'});
  await page.addScriptTag({path:'web/word-count.js'});
  await page.addScriptTag({path:'web/reader-menu.js'});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.evaluate(()=>{
   Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied=text;}}});
   const range=document.createRange();range.selectNodeContents(document.querySelector('.sentence'));getSelection().addRange(range);
  });
  await page.locator('#pages').dispatchEvent('contextmenu',{clientX:80,clientY:30});
  assert.deepEqual(await page.locator('[role="menuitem"]').allTextContents(),['复制']);
  await page.locator('[role="menuitem"]').click();
  assert.equal(await page.evaluate(()=>window.copied),'这里允许复制文字。');
  assert.equal(await page.locator('#pages').textContent(),'这里允许复制文字。');
  const keys=await page.evaluate(()=>Object.fromEntries(['c','x','v','s','p'].map(key=>[key,document.body.dispatchEvent(new KeyboardEvent('keydown',{key,ctrlKey:true,bubbles:true,cancelable:true}))])));
  assert.deepEqual(keys,{c:true,x:false,v:false,s:false,p:false});
  assert.deepEqual(await page.evaluate(()=>['cut','paste','beforeinput'].map(type=>document.querySelector('#pages').dispatchEvent(new Event(type,{bubbles:true,cancelable:true})))),[false,false,false]);
  assert.equal(await page.locator('#search').evaluate(el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'v',ctrlKey:true,bubbles:true,cancelable:true}))),true);
  await page.locator('#search').fill('搜索输入正常');
  await page.evaluate(()=>getSelection().removeAllRanges());
  await page.locator('#pages').dispatchEvent('contextmenu',{clientX:80,clientY:30});
  assert.equal(await page.locator('[role="menuitem"]').isDisabled(),true);
  console.log('PASS: copy-only menu, exact selected text, Ctrl+C allowed, document edits blocked, input unaffected');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
