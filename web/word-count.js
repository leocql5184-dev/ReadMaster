(function(root){
 'use strict';
 function countWords(text){
  text=String(text||'');
  const han=(text.match(/\p{Script=Han}/gu)||[]).length;
  const other=text.replace(/\p{Script=Han}/gu,' ');
  return han+(other.match(/[\p{L}\p{N}\p{M}]+(?:[’'\-][\p{L}\p{N}\p{M}]+|[.,]\p{N}+)*/gu)||[]).length;
 }
 function selectedText(selection,pages){
  if(!selection||selection.isCollapsed||!pages)return '';
  const fragments=[];
  for(let i=0;i<selection.rangeCount;i++){
   const range=selection.getRangeAt(i),groups=new Map();
   const take=node=>{
    if(!pages.contains(node)||!range.intersectsNode(node))return;
    const layer=node.parentElement?.closest('.sentence,.original-line');if(!layer)return;
    const start=range.startContainer===node?range.startOffset:0,end=range.endContainer===node?range.endOffset:node.length;
    const text=node.textContent.slice(start,end);if(text)groups.set(layer,(groups.get(layer)||'')+text);
   };
   const ancestor=range.commonAncestorContainer;
   if(ancestor.nodeType===3)take(ancestor);
   else{const walker=pages.ownerDocument.createTreeWalker(ancestor,4);let node;while((node=walker.nextNode()))take(node);}
   fragments.push([...groups.values()].join('\n'));
  }
  return fragments.join('\n');
 }
 root.ReadMasterWordCount={countWords,selectedText};
 if(typeof module!=='undefined'&&module.exports)module.exports=root.ReadMasterWordCount;
})(typeof window!=='undefined'?window:globalThis);
