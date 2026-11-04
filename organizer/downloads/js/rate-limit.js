'use strict';
const {Transform}=require('node:stream');
const {setTimeout:delay}=require('node:timers/promises');

// Backpressure applies to saved bytes, bounded chunks avoid large bursts.
function rateLimit(bytesPerSecond,signal) {
  let due=performance.now();
  return new Transform({transform(chunk,encoding,done) {
    const send=async()=>{
      const size=Math.max(1024,Math.min(65536,Math.ceil(bytesPerSecond/10)));
      for(let offset=0;offset<chunk.length;offset+=size) {
        signal?.throwIfAborted();
        const part=chunk.subarray(offset,offset+size);
        if(bytesPerSecond>0) {
          due=Math.max(due,performance.now())+part.length/bytesPerSecond*1000;
          this.waiting=true;
          try {await delay(Math.max(0,due-performance.now()),undefined,{signal});}
          finally {this.waiting=false;}
        }
        this.push(part);
      }
    };
    send().then(()=>done(),done);
  }});
}
module.exports={rateLimit};
