const assert=require('node:assert/strict');
const {countWords}=require('../web/word-count.js');
assert.equal(countWords('你好，世界。'),4);
assert.equal(countWords('ReAct combines reasoning and acting.'),5);
assert.equal(countWords('结果提升 34 个百分点，GPT-3 达到 95.5%。'),13);
assert.equal(countWords('state-of-the-art isn’t easy'),3);
assert.equal(countWords('𠀀 A\u0301 1,000'),3);
assert.equal(countWords(' \n，。!?🎉'),0);
console.log('Mixed Chinese/English word counts passed');
