const {test}=require('node:test');const assert=require('node:assert/strict');
const {Engine}=require('../organizer/downloads/js/runner');
test('Hanime reuses its browser session when only the originating tab closes',async()=>{
 let launches=0,newPages=0;
 const replacement={isClosed:()=>false};
 const context={pages:()=>[{isClosed:()=>true}],newPage:async()=>{newPages++;return replacement;},close:async()=>{}};
 const engine=new Engine({state:'fixture-state',browser:'Chrome',visible:true},
  {launchPersistentContext:async()=>{launches++;return context;}});
 await engine.browser('hanime');await engine.browser('hanime');
 assert.equal(launches,1);assert.equal(newPages,1);assert.equal(engine.page,replacement);
 await engine.close();
});
test('Hanime and Iwara share compatible launch options but use separate sessions',async()=>{
 const calls=[];
 const chromium={launchPersistentContext:async(profile,options)=>{
  calls.push({profile,options});return {pages:()=>[{isClosed:()=>false}],close:async()=>{}};
 }};
 const engine=new Engine({state:'fixture-state',browser:'Chromium',visible:true,
  iwaraProfile:'iwara-session',hanimeProfile:'hanime-session'},chromium);
 await engine.browser('iwara');await engine.browser('hanime');
 assert.equal(calls[0].profile,'iwara-session');assert.equal(calls[1].profile,'hanime-session');
 assert.deepEqual(calls[0].options,calls[1].options);assert.equal(calls[1].options.channel,'chrome');
 await engine.close();
});
test('compatibility can be disabled without changing selected browser',async()=>{
 let options;
 const engine=new Engine({state:'fixture-state',browser:'Edge',visible:true,hanimeLegacy:false,hanimeProfile:'ignored'},
  {launchPersistentContext:async(_,o)=>{options=o;return {pages:()=>[{}],close:async()=>{}};}});
 await engine.browser('hanime');assert.equal(options.channel,'msedge');assert.equal(options.args,undefined);
 await engine.close();
});
