const {chromium}=require('./playwright.cjs');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));await page.clock.install();
 const id='progress-fixture';
 let fixture={id,name:'进度检查.pdf',state:'processing',terms:[],outline:[],pages:[{number:1,width:600,height:800}],quality:{status:'evaluating'},
  stats:{accepted:0,needs_review:1,pending:0,processing:1},
  blocks:[0,1].map(i=>({id:'b'+i,page:1,bbox:[60,80+i*120,540,150+i*120],source:'Original sentence '+i+'.',font_size:12,kind:'prose',status:'needs_review',
   sentences:[{id:'s'+i,source:'Original sentence '+i+'.'}],translations:i?{}:{s0:'已保存的第一段译文。'}})),
  processing_progress:{state:'processing',percent:50,total_units:2,done_units:1,page:1,phase:'translate',eta_seconds:60}};
 let polls=0;
 await page.route('http://readmaster.test/**',async route=>{
  const p=new URL(route.request().url()).pathname;
  if(p==='/api/documents')return route.fulfill({json:[]});
  if(p==='/api/models')return route.fulfill({json:{onboarded:true}});
  if(p==='/api/document/'+id){polls++;return route.fulfill({json:fixture});}
  if(p.startsWith('/api/'))return route.fulfill({json:{}});
  const file=path.join(process.cwd(),process.env.READMASTER_WEB||'web',p==='/'?'index.html':p.slice(1));
  return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});
 });
 await page.goto('http://readmaster.test/');await page.clock.fastForward(1000);
 await page.locator('#startupSplash').waitFor({state:'hidden'});
 const activeFixture=structuredClone(fixture);
 fixture.state='ready';fixture.blocks.forEach(block=>{block.translations={};});
 fixture.processing_progress={...fixture.processing_progress,state:'ready',percent:0,done_units:0,eta_seconds:null};
 await page.evaluate(id=>loadDocument(id),id);
 assert(!await page.locator('#translationProgress').isVisible());
 assert(!await page.locator('#translationComplete').isVisible());
 fixture=activeFixture;
 await page.evaluate(id=>loadDocument(id),id);
 assert(await page.locator('#translationProgress').isVisible());
 assert.equal(await page.locator('#progressPercent').textContent(),'50%');
 assert.match(await page.locator('#progressEta').textContent(),/1 分钟/);
 async function sync(){const before=polls;await page.clock.fastForward(2500);await page.waitForFunction(()=>!polling);assert(polls>before);}
 fixture.state='paused';fixture.processing_progress.state='paused';await sync();
 assert.equal(await page.locator('#progressPercent').textContent(),'50%');
 fixture.state='processing';fixture.processing_progress={...fixture.processing_progress,state:'processing',eta_seconds:25};await sync();
 assert.equal(await page.locator('#progressPercent').textContent(),'50%');
 assert.match(await page.locator('#progressEta').textContent(),/不到 1 分钟/);
 fs.mkdirSync('.build',{recursive:true});await page.screenshot({path:'.build/progress-saved-desktop.png'});
 fixture.blocks[1].translations.s1='第二段译文也已保存。';fixture.processing_progress={...fixture.processing_progress,percent:100,done_units:2,state:'complete',draft_complete:true,phase:'evaluate'};
 await sync();
 assert.equal(await page.locator('#translationComplete').textContent(),'翻译完成');
 assert(await page.locator('#translationComplete').isVisible());
 for(const selector of ['#translationProgress','#processButton','#pauseButton'])assert(!await page.locator(selector).isVisible());
 assert.equal(fixture.state,'processing');
 const before=polls;fixture.blocks[0].translations.s0='后台更新后的第一段译文。';
 await page.clock.fastForward(2000);assert.equal(polls,before);
 await page.clock.fastForward(500);await page.waitForFunction(()=>document.querySelector('[data-sentence="s0"]').textContent==='后台更新后的第一段译文。');
 assert.equal(await page.locator('#pageInput').inputValue(),'1');
 await page.setViewportSize({width:900,height:700});await page.screenshot({path:'.build/progress-complete-compact.png'});
 await page.locator('#libraryButton').click();assert(!await page.locator('#translationComplete').isVisible());
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({savedProgress:true,resumeProgress:true,sessionEta:true,draftCompleteDuringReview:true,syncEvery2_5Seconds:true,browserErrors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
