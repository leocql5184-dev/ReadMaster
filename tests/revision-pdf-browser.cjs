const assert=require('node:assert/strict');
const {chromium}=require('./playwright.cjs');
(async()=>{
 const base=process.env.READMASTER_URL,id=process.env.READMASTER_FIXTURE_ID;
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.request.post(base+'/api/models/skip',{headers:{'X-ReadMaster':'1'},data:{}});
  await page.goto(base);await page.locator('#startupSplash').waitFor({state:'hidden'});
  await page.locator('#fileInput').setInputFiles(process.env.READMASTER_TEST_PDF);
  const started=await page.request.post(base+'/api/start',{headers:{'X-ReadMaster':'1'},data:{id,start_page:1,end_page:1}});
  assert.equal(started.status(),200);
  await page.waitForFunction(()=>{const e=document.querySelector('.sentence');return e&&/(未|没有|不)/.test(e.textContent);},{},{timeout:300000});
  const doc=await (await page.request.get(base+'/api/document/'+id)).json();
  assert.equal(doc.state,'complete');assert.equal(doc.blocks.filter(b=>b.sentences.length).length,1);
  const block=doc.blocks.find(b=>b.sentences.length),first=block.sentences[0].id;
  assert.equal(await page.locator(`.sentence[data-sentence="${first}"]`).textContent(),block.translations[first]);
  assert.match(block.translations[first],/(未|没有|不)/);
  assert.equal(block.translations[block.sentences[1].id],'样本包含二十名患者。');
  assert.equal(await page.locator('#exportButton,#exportDialog').count(),0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({live_revision_visible_in_reader:true,unchanged_sentence_preserved:true,export_ui_removed:true,browser_errors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
