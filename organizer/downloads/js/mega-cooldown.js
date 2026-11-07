'use strict';
const fs=require('node:fs'), path=require('node:path');
const {setTimeout:delay}=require('node:timers/promises');
const hour=3600000;
class MegaCooldown {
  constructor(directory) {
    this.file=path.join(directory,'mega-cooldown.json');
    this.state=fs.existsSync(this.file)?JSON.parse(fs.readFileSync(this.file,'utf8')):{};
  }
  remaining(now=Date.now()) {return Math.max(0,(this.state.until||0)-now);}
  hit(error,now=Date.now()) {
    const quota=error.code==='MEGA_QUOTA';
    let level=this.state.level||0;
    if (quota && !this.remaining(now))
      level=now-(this.state.lastQuota||0)<24*hour?Math.min(2,level+1):0;
    const wait=quota?[hour,2*hour,6*hour][level]:60000;
    const until=Math.max(this.state.until||0,now+(error.retryAfterMs||0),now+wait);
    this.state={level,lastQuota:quota?now:this.state.lastQuota,until,quota};
    fs.mkdirSync(path.dirname(this.file),{recursive:true});
    fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.state));
    fs.renameSync(this.file+'.tmp',this.file);
  }
  detail() {
    const reason=this.state.quota?'MEGA 流量额度不足':'MEGA 请求过于频繁';
    return reason+'，将于 '+new Date(this.state.until).toLocaleString('zh-CN')+' 自动重试。';
  }
  async wait(signal) {
    while (this.remaining()) await delay(Math.min(this.remaining(),3600000),undefined,{signal});
  }
}
module.exports={MegaCooldown};
