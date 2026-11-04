"use strict";
const fs=require('node:fs');const path=require('node:path');
const {setTimeout:delay}=require('node:timers/promises');
class DownloadPacer {
 constructor(file,{interval=8,perMinute=6,now=Date.now,sleep=delay,log=()=>{}}={}) {
  if(!Number.isFinite(interval)||interval<0||!Number.isInteger(perMinute)||perMinute<0)throw Error('下载请求间隔/每分钟上限无效');
  Object.assign(this,{file,interval:interval*1000,perMinute,now,sleep,log});this.data={recent:[],last:0,cooldown:0};
  try{const data=JSON.parse(fs.readFileSync(file,'utf8'));if(Array.isArray(data.recent))this.data={
   recent:data.recent.filter(t=>Number.isFinite(t)&&t>=0).sort((a,b)=>a-b),
   last:Number.isFinite(data.last)&&data.last>=0?data.last:0,
   cooldown:Number.isFinite(data.cooldown)&&data.cooldown>=0?data.cooldown:0};}catch{}
 }
 save(){fs.mkdirSync(path.dirname(this.file),{recursive:true});const tmp=this.file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(this.data));fs.renameSync(tmp,this.file);}
 async wait(signal) {
  signal?.throwIfAborted();
  let logged=false;
  while(true){
   const now=this.now();this.data.recent=this.data.recent.filter(t=>t>now-60000);
   let until=Math.max(this.data.last?this.data.last+this.interval:0,this.data.cooldown||0);
   if(this.perMinute&&this.data.recent.length>=this.perMinute)until=Math.max(until,this.data.recent[this.data.recent.length-this.perMinute]+60000);
   if(until<=now)break;
   if(!logged){this.log(`下载访问节流：等待 ${Math.ceil((until-now)/1000)} 秒；可停止。`);logged=true;}
   await this.sleep(Math.min(1000,until-now),undefined,{signal});signal?.throwIfAborted();
  }
  const now=this.now();this.data.last=now;this.data.recent.push(now);this.save();
 }
 cooldown(ms){this.data.cooldown=Math.max(this.data.cooldown||0,this.now()+ms);this.save();}
}
module.exports={DownloadPacer};
