const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('./playwright.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
 const page=await browser.newPage({viewport:{width:1200,height:750},reducedMotion:'reduce'}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const blocks=[{page:1,y:80},{page:1,y:650},{page:2,y:500}].map(({page,y},i)=>({id:'b'+i,page,bbox:[60,y,540,y+60],source:'Original target in paragraph.',font_size:12,kind:'prose',status:'accepted',lines:[],sentences:[{id:'s'+i,source:'Original target in paragraph.'}],translations:{['s'+i]:'这段有关键词，只有关键词应该标黄。'}}));
 const doc={id:'search-test',name:'查找测试',pages:[1,2].map(number=>({number,width:600,height:800})),blocks,terms:[],outline:[],stats:{accepted:3,needs_review:0,pending:0,processing:0},state:'complete',quality:{status:'accepted'}};
 await page.route('http://readmaster.test/**',async route=>{
  const u=new URL(route.request().url()),p=u.pathname;
  if(p==='/api/documents')return route.fulfill({json:[]});
  if(p==='/api/models')return route.fulfill({json:{onboarded:true}});
  if(p==='/api/document/search-test')return route.fulfill({json:doc});
  if(p.endsWith('/search'))return route.fulfill({json:u.searchParams.get('q').toLowerCase()==='target'?{b0:[[100,80,135,92]],b1:[[100,650,135,662]],b2:[[100,500,135,512]]}:{}});
  if(p.startsWith('/api/'))return route.fulfill({json:{}});
  const file=path.join(process.cwd(),'web',p==='/'?'index.html':p.slice(1));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});
 });
 await page.goto('http://readmaster.test/');await page.locator('#startupSplash').waitFor({state:'hidden'});
 await page.evaluate(()=>loadDocument('search-test'));await page.locator('#searchButton').click();await page.locator('#searchInput').fill('关键词');
 await page.waitForFunction(()=>document.querySelectorAll('.search-word').length===2);
 assert.deepEqual(await page.locator('.search-word').allTextContents(),['关键词','关键词']);
 assert.equal(await page.locator('.search-hit').count(),0);
 async function visible(){const r=await page.locator('.search-word,.search-hit').first().boundingBox(),c=await page.locator('#canvas').boundingBox();assert(r.y>=c.y&&r.y+r.height<=c.y+c.height);}
 await visible();const first=await page.locator('#canvas').evaluate(e=>e.scrollTop);
 await page.locator('#searchNext').click();await visible();assert((await page.locator('#canvas').evaluate(e=>e.scrollTop))>first+100);
 await page.locator('#searchNext').click();await visible();assert.equal(await page.locator('#pageInput').inputValue(),'2');
 await page.locator('#searchPrev').click();await visible();assert.equal(await page.locator('#pageInput').inputValue(),'1');
 await page.locator('#searchInput').fill('TARGET');await page.locator('.search-hit').waitFor();await visible();
 assert.equal(await page.locator('[data-block="b0"]').count(),0);
 const hit=await page.locator('.search-hit').boundingBox();assert(hit.width<100);
 await page.locator('#searchClose').click();assert.equal(await page.locator('.search-hit,.search-word').count(),0);assert.equal(await page.locator('[data-block="b0"]').count(),1);
 await page.locator('#searchButton').click();await page.locator('#searchInput').fill('关键词');await page.locator('.search-word').first().waitFor();
 await page.screenshot({path:'.build/search-word-desktop.png'});
 await page.setViewportSize({width:900,height:650});await page.locator('#searchNext').click();await visible();await page.screenshot({path:'.build/search-word-compact.png'});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({wordHighlight:true,samePageJump:true,crossPageJump:true,nativeWordBounds:true,clearRestoresTranslation:true,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
