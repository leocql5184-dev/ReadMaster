// Prefer the project's installed dependency; allow an explicit external installation.
const path=require('node:path');
const os=require('node:os');
if(process.env.READMASTER_PLAYWRIGHT){
 module.exports=require(process.env.READMASTER_PLAYWRIGHT);
}else{
 try{module.exports=require('playwright');}
 catch(error){
  if(error.code!=='MODULE_NOT_FOUND')throw error;
  module.exports=require(path.join(os.homedir(),'.cache','codex-runtimes','codex-primary-runtime','dependencies','node','node_modules','playwright'));
 }
}
