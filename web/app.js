'use strict';
const $ = id => document.getElementById(id);
const icons = {
 settings:'<circle cx="10" cy="10" r="3"/><path d="M10 1v3m0 12v3M1 10h3m12 0h3M3.6 3.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/>',
 side:'<rect x="2" y="3" width="16" height="14" rx="1"/><path d="M7 3v14"/>',
 left:'<path d="m12 4-6 6 6 6"/>',right:'<path d="m8 4 6 6-6 6"/>',minus:'<path d="M4 10h12"/>',plus:'<path d="M4 10h12M10 4v12"/>'
};
for(const [id,name] of Object.entries({sidebarButton:'side',prevButton:'left',nextButton:'right',zoomOut:'minus',zoomIn:'plus'})) $(id).innerHTML=`<svg viewBox="0 0 20 20" aria-hidden="true">${icons[name]}</svg>`;
let doc=null, chinese=true, pageNumber=1, observer=null,  polling=false, sidebar=true;
const openDocuments=new Map();let documentLoad=0;
function rememberReading(){if(doc&&openDocuments.has(doc.id))Object.assign(openDocuments.get(doc.id),{page:pageNumber,scroll:$('canvas').scrollTop,chinese,zoom:$('zoom').value});}
function renderDocumentTabs(){
 $('documentTabs').replaceChildren();$('libraryButton').classList.toggle('active',!doc);
 for(const [id,item] of openDocuments){
  const group=document.createElement('div');group.className='document-tab'+(doc?.id===id?' active':'');
  const tab=document.createElement('button');tab.className='document-tab-title';tab.textContent=item.name;tab.title=item.name;tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(doc?.id===id));tab.onclick=()=>loadDocument(id);
  const close=document.createElement('button');close.className='document-tab-close';close.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 6 8 8m0-8-8 8"/></svg>';close.setAttribute('aria-label','关闭标签 '+item.name);close.title='关闭标签';close.onclick=()=>closeDocumentTab(id);
  tab.onkeydown=e=>{const ids=[...openDocuments.keys()],index=ids.indexOf(id);let next;if(e.key==='ArrowRight')next=ids[(index+1)%ids.length];if(e.key==='ArrowLeft')next=ids[(index+ids.length-1)%ids.length];if(e.key==='Home')next=ids[0];if(e.key==='End')next=ids.at(-1);if(e.key==='Delete'){e.preventDefault();closeDocumentTab(id);return;}if(next){e.preventDefault();loadDocument(next);}};
  group.dataset.id=id;group.append(tab,close);$('documentTabs').append(group);
 }
 $('documentTabs').querySelector('.active')?.scrollIntoView({block:'nearest',inline:'nearest'});
}
function closeDocumentTab(id){const ids=[...openDocuments.keys()],index=ids.indexOf(id),active=doc?.id===id;openDocuments.delete(id);if(active){const next=ids[index+1]||ids[index-1];$('libraryButton').click();if(next)loadDocument(next);}else renderDocumentTabs();}
const sourceSentenceIds=new Set(),sourceSentences=new Map();
const roleNames={terms:'提取术语',translate:'翻译',evaluate:'翻译中',revise:'翻译中',accept:'翻译中'};
function draftComplete(){const blocks=doc?.blocks.filter(b=>b.sentences.length)||[];return blocks.length>0&&blocks.every(b=>b.sentences.every(s=>b.translations[s.id]));}
function updateProgress(){
 updateWordCount();
 const panel=$('translationProgress'),p=doc?.processing_progress;
 const finished=draftComplete();
 $('translationComplete').hidden=!finished;
 $('translationComplete').textContent='翻译完成';
 panel.hidden=!doc||doc.state==='ready'||finished||(!p&&!['processing','paused','failed'].includes(doc.state));if(panel.hidden)return;
 const state=p?.state||doc.state,percent=p?.percent;
 panel.dataset.state=state;
 $('progressPercent').textContent=percent===undefined?'—':percent+'%';
 $('progressFill').style.width=(percent||0)+'%';
 $('progressTrack').setAttribute('aria-valuenow',String(percent||0));
 const title={complete:'处理完成',needs_review:'待继续翻译',partial:'待继续翻译',paused:'已暂停',failed:'处理失败'}[state]||`第 ${p?.page||doc.active_page||1} 页 · ${roleNames[p?.phase||doc.active_role]||'准备处理'}`;
 $('progressStage').textContent=title;$('progressModel').textContent=p?.model||'';
 $('progressCount').textContent=p?`已处理 ${p.done_units} / ${p.total_units} 句块`:'继续翻译后更新进度';
 $('progressEta').textContent=state==='complete'?'预计剩余：已完成':state==='paused'?'预计剩余：已暂停':state==='failed'?'预计剩余：暂不可用':state==='needs_review'||state==='partial'?'预计剩余：请继续翻译':p?.overrun?'预计剩余：正在重新估算':p?.eta_seconds!=null?`预计剩余：${p.eta_seconds<60?'不到 1 分钟':'约 '+Math.ceil(p.eta_seconds/60)+' 分钟'}`:'预计剩余：正在估算';
 $('progressEta').title='从本次开始或继续翻译起，完成剩余待处理内容还需要的时间；不包含已完成内容和暂停时间。';
 if(state==='processing'&&p?.estimate_scope==='initial_translation'&&p.eta_seconds!=null){
  $('progressEta').textContent=$('progressEta').textContent.replace('预计剩余','翻译剩余');
  $('progressEta').title='这是当前翻译步骤的预计时间，全文完成时间可能更长。';
 }
 panel.title=[$('progressStage').textContent,$('progressCount').textContent,$('progressModel').textContent].filter(Boolean).join(' · ');
}
async function api(path,body){
 const opts=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-ReadMaster':'1'},body:JSON.stringify(body)};
 const response=await fetch(path,opts);const value=await response.json();if(!response.ok)throw new Error(value.error||'请求失败');return value;
}
function notice(text){$('noticeText').textContent=text;$('notice').hidden=false;}
$('noticeClose').onclick=()=>{$('notice').hidden=true;};
function failure(error){notice(error.message||String(error));}
function esc(text){return String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function termMarkup(source){
 const terms=[...(doc?.terms||[])].sort((a,b)=>b.source.length-a.source.length);
 if(!terms.length)return esc(source);
 const pattern=terms.map(t=>t.source.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
 const re=new RegExp(pattern,'gi');let result='',pos=0;for(const match of source.matchAll(re)){
 result+=esc(source.slice(pos,match.index))+`<mark>${esc(match[0])}</mark>`;pos=match.index+match[0].length;
 }return result+esc(source.slice(pos));
}
async function refreshLibrary(){
 try{const list=await api('/api/documents');$('recentList').replaceChildren();
 for(const item of list){const row=document.createElement('div');row.className='library-row';const button=document.createElement('button');button.className='library-open';const name=document.createElement('span');name.textContent=item.name;const pages=document.createElement('small');const states={complete:'翻译完成',partial:'待继续翻译',needs_review:'待继续翻译',paused:'已暂停',processing:'翻译中',failed:'可恢复',ready:'原文已保存'};pages.textContent=`${item.pages} 页 · ${states[item.state]||'本地保存'}`;button.title=`本地阅读历史 · 上次读到第 ${item.reading_page||1} 页${item.translated_blocks?' · '+item.translated_blocks+' 段译文已保存':''}`;button.append(name,pages);button.onclick=()=>loadDocument(item.id);
 const remove=document.createElement('button');remove.className='library-delete';remove.textContent='删除';remove.setAttribute('aria-label','删除 '+item.name);remove.disabled=item.state==='processing';remove.title=remove.disabled?'先暂停翻译，等待当前调用结束后删除':'删除本地文献和译文';remove.onclick=()=>{pendingDelete=item;$('deleteName').textContent=item.name;$('deleteError').textContent='';$('deleteConfirm').disabled=false;$('deleteDialog').showModal();};row.append(button,remove);$('recentList').append(row);}
 }catch(e){failure(e);}
}
let pendingDelete=null;
for(const button of $('deleteDialog').querySelectorAll('.close-delete'))button.onclick=()=>{$('deleteDialog').close();pendingDelete=null;};
$('deleteConfirm').onclick=async()=>{
 if(!pendingDelete)return;const id=pendingDelete.id;$('deleteConfirm').disabled=true;
 try{await api('/api/delete',{id});if(localStorage.getItem('readmaster.last')===id)localStorage.removeItem('readmaster.last');$('deleteDialog').close();pendingDelete=null;if(openDocuments.has(id))closeDocumentTab(id);await refreshLibrary();notice('已删除本地文献、译文和缓存，原始 PDF 文件未改动。');}
 catch(e){$('deleteError').textContent=e.message||String(e);}
 finally{$('deleteConfirm').disabled=false;}
};
async function loadDocument(id){
 const request=++documentLoad;rememberReading();
 try{const next=await api('/api/document/'+id);if(request!==documentLoad)return;
 restoreSentence();doc=next;
 if(!openDocuments.has(id))openDocuments.set(id,{name:doc.name,page:doc.progress?.page||1,chinese:true,zoom:'fit'});
 const saved=openDocuments.get(id);saved.name=doc.name;pageNumber=saved.page;chinese=saved.chinese;$('zoom').value=saved.zoom;
 $('chineseButton').classList.toggle('selected',chinese);$('originalButton').classList.toggle('selected',!chinese);
 searchMatches=[];searchIndex=0;$('searchInput').value='';$('searchCount').textContent='0 个结果';$('detailPanel').hidden=true;$('notice').hidden=true;
 $('empty').hidden=true;renderDocumentTabs();
 $('pageTotal').textContent='/ '+doc.pages.length;$('pageInput').max=doc.pages.length;$('processButton').disabled=false;
 renderPages();renderNavigation();updateState();requestAnimationFrame(()=>{if(request!==documentLoad)return;goPage(pageNumber,false);if(saved.scroll!==undefined)$('canvas').scrollTo({top:saved.scroll,behavior:'instant'});});localStorage.setItem('readmaster.last',id);
 }catch(e){if(request===documentLoad)failure(e);}
}
async function importFile(file){
 if(!file)return;if(!file.name.toLowerCase().endsWith('.pdf'))return notice('请拖入 PDF 文件。');
 if(file.size>80*1024*1024)return notice('第一版支持不超过 80 MB 的文件。');
 $('statusText').textContent='正在导入并识别论文页面…';$('processButton').disabled=true;
 try{const response=await fetch('/api/import?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-ReadMaster':'1','Content-Type':'application/pdf'},body:file});const value=await response.json();if(!response.ok)throw new Error(value.error);await loadDocument(value.id);
 notice(value.cached?'这篇论文已在本机文献库中，已恢复阅读。':'论文已打开。点击“翻译论文”开始；尚未翻译的区域显示原文。');
 }catch(e){failure(e);$('statusText').textContent='导入失败，请重新打开';}finally{$('fileInput').value='';}
}
$('fileInput').onchange=e=>importFile(e.target.files[0]);
for(const id of ['openButton','emptyOpen'])$(id).onclick=()=>$('fileInput').click();
let dragDepth=0;
window.addEventListener('dragenter',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();dragDepth++;$('dropOverlay').hidden=false;}});
window.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();e.dataTransfer.dropEffect='copy';}});
window.addEventListener('dragleave',()=>{dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)$('dropOverlay').hidden=true;});
window.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('dropOverlay').hidden=true;importFile(e.dataTransfer.files[0]);});
function pageWidth(page){const available=$('canvas').clientWidth-(window.innerWidth<700?16:64);const scale=$('zoom').value;return scale==='fit'?Math.max(240,Math.min(860,available)):page.width*Number(scale)*1.3333;}
function renderPages(){
 sourceSentences.clear();observer?.disconnect();$('pages').replaceChildren();if(!doc)return;
 updateWordCount();
 for(const page of doc.pages){const wrapper=document.createElement('article');wrapper.className='pdf-page';wrapper.dataset.page=page.number;wrapper.setAttribute('aria-label',`第 ${page.number} 页`);
 const width=pageWidth(page);wrapper.style.width=width+'px';wrapper.style.height=(width*page.height/page.width)+'px';
 const img=document.createElement('img');img.className='page-image';img.alt=`论文第 ${page.number} 页`;img.loading='lazy';img.src=`/api/document/${doc.id}/image?page=${page.number}`;
 const overlay=document.createElement('div');overlay.className='page-overlay';wrapper.append(img,overlay);$('pages').append(wrapper);renderOverlay(page,overlay,width);
 }
 observer=new IntersectionObserver(updateCurrentPage,{root:$('canvas'),threshold:[0,.2,.5,.8]});
 document.querySelectorAll('.pdf-page').forEach(p=>observer.observe(p));
}
function renderOverlay(page,overlay,width){
 for(const [id,item] of sourceSentences)if(overlay.contains(item.span))sourceSentences.delete(id);
 overlay.replaceChildren();const factor=width/page.width;
 for(const block of doc.blocks.filter(b=>b.page===page.number)){
   if(chinese&&Object.keys(block.translations).length&&!searchOriginalBlock(block))continue;
   for(const line of block.lines||[]){const text=document.createElement('span');text.className='original-line';text.textContent=line.text;const [x0,y0,x1,y1]=line.bbox;Object.assign(text.style,{left:x0*factor+'px',top:y0*factor+'px',width:(x1-x0)*factor+'px',height:(y1-y0)*factor+'px',fontSize:line.font_size*factor+'px'});overlay.append(text);}
 }
 if(!chinese){drawSearchHit(page,overlay,factor);return;}
 const blocks=doc.blocks.filter(b=>b.page===page.number&&b.sentences.length);
 let translated=0,waiting=0;
 for(const block of blocks){if(searchOriginalBlock(block))continue;if(!Object.keys(block.translations).length){waiting++;continue;}translated++;
 const [x0,y0,x1,y1]=block.bbox;const element=document.createElement('div');element.className='prose-block'+(block.kind==='title'?' paper-title':'');element.dataset.block=block.id;
 Object.assign(element.style,{left:x0*factor+'px',top:y0*factor+'px',width:(x1-x0)*factor+'px',height:(y1-y0)*factor+'px'});
 for(const sentence of block.sentences){const span=document.createElement('span');span.className='sentence'+(block.status==='needs_review'?' needs-review':'');span.dataset.sentence=sentence.id;span.tabIndex=0;span.setAttribute('role','button');span.setAttribute('aria-label','点击切换中文与英文原句');span.textContent=block.translations[sentence.id]||sentence.source;bindSentence(span,sentence,block);element.append(span);}
 overlay.append(element);fitBlock(element,block,factor);
 for(const sentence of block.sentences)if(sourceSentenceIds.has(sentence.id))revealSentence(element.querySelector(`[data-sentence="${sentence.id}"]`),sentence);
 }
 const badge=document.createElement('span');badge.className='page-badge';badge.textContent=translated?(waiting?'部分中文 · 其余原文':'中文 · 图表保留原样'):'原文 · 等待翻译';overlay.append(badge);drawSearchHit(page,overlay,factor);
}
function fitBlock(element,block,factor){
 let font=block.font_size*factor;const minimum=7*factor;
 element.style.fontSize=font+'px';
 while(element.scrollHeight>element.clientHeight+2&&font>minimum){font-=.35;element.style.fontSize=font+'px';}
 if(element.scrollHeight>element.clientHeight+2)element.classList.add('overflow-block');
}
function restoreSentence(){
 for(const {span,text} of sourceSentences.values()){
  span.textContent=text;span.classList.remove('showing-source');span.setAttribute('aria-pressed','false');
  span.parentElement?.classList.remove('reading-source');
 }
 sourceSentences.clear();sourceSentenceIds.clear();
}
function revealSentence(span,sentence){
 if(sourceSentences.has(sentence.id))return;
 sourceSentences.set(sentence.id,{span,text:span.textContent});sourceSentenceIds.add(sentence.id);
 span.innerHTML=termMarkup(sentence.source);span.classList.add('showing-source');span.setAttribute('aria-pressed','true');
 span.parentElement.classList.add('reading-source');
}
function toggleSentence(span,sentence){
 const item=sourceSentences.get(sentence.id);
 if(!item){revealSentence(span,sentence);return;}
 span.textContent=item.text;span.classList.remove('showing-source');span.setAttribute('aria-pressed','false');
 sourceSentences.delete(sentence.id);sourceSentenceIds.delete(sentence.id);
 if(!span.parentElement.querySelector('.showing-source'))span.parentElement.classList.remove('reading-source');
}
function bindSentence(span,sentence,block){
 span.setAttribute('aria-pressed','false');
 span.addEventListener('click',()=>{if(window.getSelection().isCollapsed)toggleSentence(span,sentence);});
 span.addEventListener('keydown',e=>{if((e.key===' '||e.key==='Enter')&&!e.repeat){e.preventDefault();toggleSentence(span,sentence);}});
}
window.addEventListener('keydown',e=>{if(e.key==='Escape')restoreSentence();if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='f'){e.preventDefault();$('searchBar').hidden=false;$('searchInput').focus();}});
function renderNavigation(){
 $('thumbs').replaceChildren();$('outline').replaceChildren();
 for(const page of doc.pages){const button=document.createElement('button');button.className='thumb';button.dataset.page=page.number;button.setAttribute('aria-label',`跳转第 ${page.number} 页`);const image=document.createElement('img');image.loading='lazy';image.alt='';image.width=Math.ceil(page.width);image.height=Math.ceil(page.height);image.style.height='auto';image.src=`/api/document/${doc.id}/image?page=${page.number}&thumb=1`;const label=document.createElement('span');label.textContent=page.number;button.append(image,label);button.onclick=()=>goPage(page.number);$('thumbs').append(button);}
 renderOutline();
}
let outlineSignature='';
function renderOutline(){
 outlineSignature=doc.id+JSON.stringify(doc.outline)+doc.state;
 const top=$('outline').scrollTop;$('outline').replaceChildren();
 if(!doc.outline.length){const note=document.createElement('p');note.textContent='此 PDF 没有书签目录，可使用缩略图定位。';$('outline').append(note);return;}
 if(doc.outline.some(item=>!item.translation)){const translate=document.createElement('button');translate.className='outline-translate';translate.textContent=doc.state==='processing'?'目录等待处理':'翻译目录';translate.disabled=doc.state==='processing';translate.onclick=async()=>{translate.disabled=true;try{await api('/api/start',{id:doc.id,outline_only:true});doc=await api('/api/document/'+doc.id);updateState();renderOutline();notice('已开始翻译目录，完成后会保存在本机。');}catch(e){failure(e);translate.disabled=false;}};$('outline').append(translate);}
 for(const item of doc.outline){const button=document.createElement('button');button.className='outline-entry';button.style.paddingLeft=(7+(item.level-1)*10)+'px';const original=document.createElement('span');original.className='outline-original';original.lang='en';original.textContent=item.title;const translated=document.createElement('span');translated.className='outline-translated';translated.lang='zh-CN';translated.textContent=item.translation||'中文标题待翻译';button.append(original,translated);button.onclick=()=>goPage(item.page);$('outline').append(button);}
 $('outline').scrollTop=top;
}
function syncThumbnail(){
 const list=$('thumbs'),thumb=list.querySelector(`.thumb[data-page="${pageNumber}"]`);if(!thumb||!list.getClientRects().length)return;
 const view=list.getBoundingClientRect(),rect=thumb.getBoundingClientRect();if(rect.top<view.top+8||rect.bottom>view.bottom-8)list.scrollTop+=rect.top+rect.height/2-view.top-view.height/2;
}
function updateCurrentPage(){
 if(!doc)return;const view=$('canvas').getBoundingClientRect();let page=pageNumber,largest=0;
 for(const element of $('pages').children){const rect=element.getBoundingClientRect(),visible=Math.max(0,Math.min(rect.bottom,view.bottom)-Math.max(rect.top,view.top));if(visible>largest){largest=visible;page=Number(element.dataset.page);}}
 if(largest&&page!==pageNumber)setCurrent(page);
}
let navigationFrame;
$('canvas').addEventListener('scroll',()=>{cancelAnimationFrame(navigationFrame);navigationFrame=requestAnimationFrame(updateCurrentPage);},{passive:true});
let progressTimer;
let wordCountSignature='',totalWordCount=0,wordCountFrame;
function updateWordCount(refreshTotal=true){
 const counter=$('wordCount');counter.hidden=!doc;if(!doc){wordCountSignature='';totalWordCount=0;return;}
 if(refreshTotal){const text=doc.blocks.map(block=>chinese&&Object.keys(block.translations).length?block.sentences.map(s=>block.translations[s.id]||s.source).join(''):block.source).join('\n');
 const signature=doc.id+chinese+text;
 if(signature!==wordCountSignature){wordCountSignature=signature;totalWordCount=ReadMasterWordCount.countWords(text);}}
 const selectionText=ReadMasterWordCount.selectedText(window.getSelection(),$('pages')),selected=ReadMasterWordCount.countWords(selectionText),hasSelection=Boolean(selectionText);
 const format=n=>n.toLocaleString('zh-CN');
 counter.textContent=hasSelection?`字数：${format(selected)} / ${format(totalWordCount)}`:`字数：${format(totalWordCount)}`;
 counter.title=`${hasSelection?'选中 '+format(selected)+'，':''}全文 '+format(totalWordCount)+'。汉字每字计 1，英文单词和数字每项计 1，不计空白和标点。按当前阅读语言统计，未翻译区域使用原文；临时查看英文原句不改变全文字数。`;
}
document.addEventListener('selectionchange',()=>{cancelAnimationFrame(wordCountFrame);wordCountFrame=requestAnimationFrame(()=>updateWordCount(false));});
function setCurrent(number){pageNumber=number;$('pageInput').value=number;$('pageStatus').textContent=`第 ${number} / ${doc.pages.length} 页`;document.querySelectorAll('.thumb').forEach(t=>{const active=Number(t.dataset.page)===number;t.classList.toggle('active',active);if(active)t.setAttribute('aria-current','page');else t.removeAttribute('aria-current');});syncThumbnail();clearTimeout(progressTimer);const ident=doc.id;progressTimer=setTimeout(()=>api('/api/progress',{id:ident,page:number}).catch(()=>{}),700);}
function goPage(number,smooth=true){if(!doc)return;number=Math.max(1,Math.min(doc.pages.length,number));const element=document.querySelector(`.pdf-page[data-page="${number}"]`);if(element){$('canvas').scrollTo({top:element.offsetTop-$('pages').offsetTop-15,behavior:smooth?'smooth':'instant'});setCurrent(number);}}
$('prevButton').onclick=()=>goPage(pageNumber-1);$('nextButton').onclick=()=>goPage(pageNumber+1);$('pageInput').onchange=e=>goPage(Number(e.target.value));
function resizePages(){if(!doc)return;const current=pageNumber;renderPages();requestAnimationFrame(()=>{$('searchInput').value.trim()?search(0):goPage(current,false);});}
$('zoom').onchange=resizePages;
for(const [id,step] of [['zoomOut',-.15],['zoomIn',.15]])$(id).onclick=()=>{const value=$('zoom').value==='fit'?1:Number($('zoom').value);const next=Math.max(.5,Math.min(2,value+step));let option=[...$('zoom').options].find(o=>Number(o.value)===next);if(!option){option=new Option(Math.round(next*100)+'%',String(next));$('zoom').add(option);}$('zoom').value=String(next);resizePages();};
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(resizePages,180);});
$('sidebarButton').onclick=()=>{sidebar=!sidebar;$('sidebar').hidden=!sidebar;resizePages();};
for(const [id,value] of [['chineseButton',true],['originalButton',false]])$(id).onclick=()=>{chinese=value;$('chineseButton').classList.toggle('selected',value);$('originalButton').classList.toggle('selected',!value);resizePages();};
$('showMarks').onchange=e=>$('pages').classList.toggle('no-marks',!e.target.checked);
for(const [id,isThumb] of [['thumbTab',true],['outlineTab',false]])$(id).onclick=()=>{$('thumbs').hidden=!isThumb;$('outline').hidden=isThumb;$('thumbTab').classList.toggle('active',isThumb);$('outlineTab').classList.toggle('active',!isThumb);if(isThumb)syncThumbnail();};
$('libraryButton').onclick=()=>{documentLoad++;rememberReading();restoreSentence();doc=null;clearTimeout(progressTimer);observer?.disconnect();$('pages').replaceChildren();$('thumbs').replaceChildren();$('outline').replaceChildren();$('detailPanel').hidden=true;$('empty').hidden=false;renderDocumentTabs();$('statusText').textContent='本机文献库';$('pageStatus').textContent='';$('pageTotal').textContent='/ 0';$('processButton').disabled=true;$('processButton').hidden=false;$('pauseButton').hidden=true;updateProgress();refreshLibrary();};
let termsSignature='';
function showTerms(){if(!doc)return notice('请先打开论文。');$('detailPanel').hidden=false;$('panelTitle').textContent='技术术语';termsSignature=doc.id+JSON.stringify(doc.terms);$('panelBody').replaceChildren();if(!doc.terms.length){$('panelBody').textContent='开始翻译后提取术语；原文中的技术词会特别标注。';return;}for(const term of doc.terms){const entry=document.createElement('section');entry.className='term-entry';const title=document.createElement('strong');title.textContent=term.source;const zh=document.createElement('div');zh.className='term-zh';zh.textContent=term.target;entry.append(title,zh);const definition=typeof term.definition==='string'?term.definition.trim():'';if(definition&&!/^(上下文释义[，,、\s]*需核对|暂无释义|待核对|待生成|生成中)[。.…]*$/.test(definition)){const p=document.createElement('p');p.textContent=definition;entry.append(p);}$('panelBody').append(entry);}resizePages();}
$('termsButton').onclick=showTerms;$('closePanel').onclick=()=>{$('detailPanel').hidden=true;resizePages();};
function updateState(){if(!doc)return;if(!$('detailPanel').hidden&&$('panelTitle').textContent==='技术术语'&&termsSignature!==doc.id+JSON.stringify(doc.terms))showTerms();const s=doc.stats;const total=s.accepted+s.needs_review+s.pending+s.processing;
 if(outlineSignature!==doc.id+JSON.stringify(doc.outline)+doc.state)renderOutline();
 updateProgress();
 const labels={ready:'原文已就绪',processing:`第 ${doc.active_page||1} 页 · ${roleNames[doc.active_role]||'准备处理'}`,partial:'待继续翻译',needs_review:'尚未完成，可继续翻译',complete:'处理完成',paused:'已暂停',failed:'处理失败'};
 const q=doc.quality,scored=q?.scores&&Object.keys(q.scores).length,threshold=Number(q?.threshold??.7),low=scored?Object.values(q.scores).filter(v=>q.threshold_operator==='>='?v<threshold:v<=threshold).length:0;
 const qualityText=scored?` · > ${threshold.toFixed(2)}：${((scored-low)*100/scored).toFixed(2)}%（要求 ≥ ${q.required_percentage??97}%） · 最低分 ${Math.min(...Object.values(q.scores)).toFixed(3)}（要求 ≥ ${Number(q.minimum_allowed_score??.5).toFixed(2)}）`:q?.status==='not_evaluated'&&s.accepted?' · 可继续翻译':'';
 $('statusText').textContent=`${labels[doc.state]||doc.state} · ${s.accepted}/${total} 块验收通过${s.needs_review?' · '+s.needs_review+' 块待核查':''}${qualityText}`;
 const hasFullTranslation=doc.blocks.filter(b=>b.sentences.length).every(b=>b.sentences.every(s=>b.translations[s.id]));
 $('processButton').hidden=hasFullTranslation;$('processButton').disabled=doc.state==='processing';$('processButton').textContent=(hasFullTranslation||s.accepted||s.needs_review)?'继续翻译':'翻译论文';$('pauseButton').hidden=hasFullTranslation||doc.state!=='processing';
 if(doc.state==='failed')notice(doc.error||'处理失败，请检查模型连接后重试。');
}
setInterval(async()=>{if(!doc||polling)return;polling=true;try{const next=await api('/api/document/'+doc.id);if(!doc||next.id!==doc.id)return;const previous=doc;const signature=(d,page)=>JSON.stringify(d.blocks.filter(b=>page===undefined||b.page===page).map(b=>[b.status,b.translations]));const changed=signature(previous)!==signature(next);doc=next;updateState();if(changed){const focusedId=document.activeElement?.dataset?.sentence;for(const p of doc.pages){if(signature(previous,p.number)===signature(doc,p.number))continue;const overlay=document.querySelector(`.pdf-page[data-page="${p.number}"] .page-overlay`);if(overlay){renderOverlay(p,overlay,pageWidth(p));if(focusedId)for(const span of overlay.querySelectorAll('.sentence'))if(focusedId===span.dataset.sentence)span.focus({preventScroll:true});}}}}catch(e){failure(e);}finally{polling=false;}},2500);
for(const button of document.querySelectorAll('.close-dialog'))button.onclick=()=>button.closest('dialog').close();
$('settingsButton').onclick=()=>window.ReadMasterModels.open();
$('processButton').onclick=()=>{if(!doc)return notice('请先打开 PDF。');$('startPage').value=1;$('endPage').value=doc.pages.length;$('startPage').max=doc.pages.length;$('endPage').max=doc.pages.length;$('translateDialog').showModal();};
$('translateForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/start',{id:doc.id,start_page:Number($('startPage').value),end_page:Number($('endPage').value)});$('translateDialog').close();doc=await api('/api/document/'+doc.id);updateState();notice('已开始处理。可以继续阅读；按页码范围试译可减少等待。');}catch(e){failure(e);}};
$('pauseButton').onclick=async()=>{try{const result=await api('/api/pause',{id:doc.id});notice(result.message);}catch(e){failure(e);}};
$('quitButton').onclick=async()=>{try{await api('/api/shutdown',{});notice('已退出 ReadMaster，可以关闭此网页。');restoreSentence();doc=null;updateProgress();}catch(e){failure(e);}};
let searchMatches=[],searchIndex=0,searchKey='',searchRects={},searchRequest=0;
function searchOriginalBlock(block){return chinese&&searchMatches[searchIndex]?.id===block.id&&searchMatches[searchIndex]?.original;}
function markSearchText(element,query){
 const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT),nodes=[];let node,offset=0;
 while(node=walker.nextNode()){nodes.push({node,start:offset});offset+=node.length;}
 const text=element.textContent.toLowerCase(),needle=query.toLowerCase(),ranges=[];
 for(let pos=text.indexOf(needle);pos!==-1;pos=text.indexOf(needle,pos+needle.length))ranges.push([pos,pos+needle.length]);
 for(const {node,start} of nodes){const end=start+node.length,parts=ranges.filter(([a,b])=>a<end&&b>start);if(!parts.length)continue;
  const fragment=document.createDocumentFragment();let cursor=0;
  for(const [a,b] of parts){const left=Math.max(a,start)-start,right=Math.min(b,end)-start;fragment.append(node.textContent.slice(cursor,left));const mark=document.createElement('mark');mark.className='search-word';mark.textContent=node.textContent.slice(left,right);fragment.append(mark);cursor=right;}
  fragment.append(node.textContent.slice(cursor));node.replaceWith(fragment);
 }
}
function drawSearchHit(page,overlay,factor){
 const match=searchMatches[searchIndex],query=$('searchInput').value.trim();if(!match||!query||match.page!==page.number)return;
 const element=[...overlay.querySelectorAll('[data-block]')].find(e=>e.dataset.block===match.id);
 if(element&&!match.original){markSearchText(element,query);return;}
 for(const [x0,y0,x1,y1] of searchRects[match.id]||[]){const hit=document.createElement('div');hit.className='search-hit';Object.assign(hit.style,{left:x0*factor+'px',top:y0*factor+'px',width:(x1-x0)*factor+'px',height:(y1-y0)*factor+'px'});overlay.append(hit);}
}
async function search(move=0,navigate=true){
 const request=++searchRequest;if(!doc)return;const ident=doc.id,text=$('searchInput').value.trim(),key=ident+'\n'+text;
 if(text&&key!==searchKey){try{const rects=await api('/api/document/'+ident+'/search?q='+encodeURIComponent(text));if(request!==searchRequest||doc?.id!==ident)return;searchRects=rects;searchKey=key;}catch(error){if(request===searchRequest)failure(error);return;}}
 const previous=searchMatches[searchIndex]?.id;
 searchMatches=text?doc.blocks.flatMap(b=>{const translated=chinese&&b.sentences.map(s=>b.translations[s.id]||s.source).join('').toLowerCase().includes(text.toLowerCase())&&Object.keys(b.translations).length;
  return translated||searchRects[b.id]?.length?[{id:b.id,page:b.page,original:!translated}]:[];}):[];
 if(!navigate&&previous){const kept=searchMatches.findIndex(b=>b.id===previous);if(kept>=0)searchIndex=kept;}
 searchIndex=searchMatches.length?(searchIndex+move+searchMatches.length)%searchMatches.length:0;
 $('searchCount').textContent=searchMatches.length?`${searchIndex+1} / ${searchMatches.length}`:'0 个结果';
 for(const page of doc.pages){const overlay=document.querySelector(`.pdf-page[data-page="${page.number}"] .page-overlay`);if(overlay)renderOverlay(page,overlay,pageWidth(page));}
 if(navigate&&searchMatches.length){const match=searchMatches[searchIndex],page=document.querySelector(`.pdf-page[data-page="${match.page}"]`),hit=page?.querySelector('.search-word,.search-hit');if(hit){hit.scrollIntoView({block:'center',inline:'center',behavior:'instant'});setCurrent(match.page);}}
}
$('searchButton').onclick=()=>{if($('searchBar').hidden){$('searchBar').hidden=false;$('searchInput').focus();}else $('searchClose').click();};
$('searchInput').oninput=()=>{searchIndex=0;search();};$('searchPrev').onclick=()=>search(-1);$('searchNext').onclick=()=>search(1);
$('searchClose').onclick=()=>{$('searchBar').hidden=true;$('searchInput').value='';search();};
$('processButton').disabled=true;
(async()=>{try{await refreshLibrary();const last=localStorage.getItem('readmaster.last');if(last)await loadDocument(last);}finally{await window.ReadMasterLoading?.finish();await window.ReadMasterModels?.onStartup();}})();
