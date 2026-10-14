const assert=require('node:assert/strict');
const {chromium}=require('./playwright.cjs');
(async()=>{
 const base=process.env.READMASTER_URL,id=process.env.READMASTER_FIXTURE_ID,fault=process.env.READMASTER_FAULT_ID;
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(base);await page.locator('#startupSplash').waitFor({state:'hidden'});
  await page.locator('#fileInput').setInputFiles(process.env.READMASTER_TEST_PDF);
  const sentence=page.locator(`.sentence[data-sentence="${fault}"]`);
  await sentence.waitFor();assert.equal(await sentence.textContent(),'该方法需要标注数据。');
  await page.locator('#processButton').click();
  const request=page.waitForResponse(r=>r.url().endsWith('/api/start')&&r.request().method()==='POST');
  await page.locator('#translateForm button[type="submit"]').click();
  const started=await request;
  assert.equal(started.status(),200);
  const deadline=Date.now()+900000;
  let accepted=false;
  while(Date.now()<deadline){
   const d=await(await page.request.get(base+'/api/document/'+id)).json();
   if(['failed','needs_review'].includes(d.state)&&d.quality?.status!=='evaluating') throw Error(d.error||d.quality?.reason||'Quality failed');
   if(d.state==='complete'&&d.quality?.status==='accepted'){
    const values=Object.values(d.quality.scores);assert(values.length&&values.filter(v=>v>.7).length*100>=97*values.length&&Math.min(...values)>=.5);accepted=true;break;
   }
   await page.waitForTimeout(1000);
  }
  assert(accepted,'Actual native QE did not finish before deadline');
  await page.waitForFunction(fault=>/(不|无需)/.test(document.querySelector(`.sentence[data-sentence="${fault}"]`)?.textContent||''),fault);
  assert.match(await sentence.textContent(),/(不|无需)/);
  assert.equal(await page.locator('#exportButton,#exportDialog').count(),0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({actual_comet_repair_visible:true,export_ui_removed:true,browser_errors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
