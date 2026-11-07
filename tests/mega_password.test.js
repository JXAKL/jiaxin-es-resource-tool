'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {protectedPayload,unlock}=require('../organizer/downloads/js/mega-password');
const crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {Readable}=require('node:stream');
const sdk=require('../organizer/downloads/js/vendor/megajs.cjs');
const {downloadMega}=require('../organizer/downloads/js/mega-local');
const fixtures=[
  ['AQFoYW5kbGUAAQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eH0V1DRb_sOEfmDO4wdanR900OXAQk5ptTuARIuQuaNbOZXdNemlOedhKDgr5foC8vLFFsh0SOKG0F2oyZT-Z3d8','file','AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8'],
  ['AgBoYW5kbGUAAQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eH0V1DRb_sOEfmDO4wdanR93_5Ev5hnPl-FIYXuPKpnn6eHSujv3hc0KHsCnp8SWTnA','folder','AAECAwQFBgcICQoLDA0ODw'],
];
for(const [encoded,type,key] of fixtures)test('authenticates password-protected '+type+' before resolving share',async()=>{
  const payload=protectedPayload('https://mega.nz/#P!'+encoded);
  await assert.rejects(unlock(payload,'wrong'),{code:'MEGA_PASSWORD'});
  assert.equal(await unlock(payload,' 密码 STOP / FINISH_LOGIN '),'https://mega.nz/'+type+'/aGFuZGxl#'+key);
  payload[15]^=1;
  await assert.rejects(unlock(payload,'密码 STOP / FINISH_LOGIN'),{code:'MEGA_PASSWORD'});
});

test('protected folder downloads and verifies only the chosen encrypted payload, restores selection and keeps secrets off disk',async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'es-pick-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const folderKey=Buffer.from(fixtures[1][2],'base64url'),password='密码 STOP / FINISH_LOGIN';
  const source='https://mega.nz/#P!'+fixtures[1][0];
  function encrypt(mode,key,data,iv) {
    const c=crypto.createCipheriv(mode,key,iv);c.setAutoPadding(false);
    return Buffer.concat([c.update(data),c.final()]).toString('base64url');
  }
  function metadata(id,name,key,attrKey,type,size) {
    const raw=Buffer.from('MEGA'+JSON.stringify({n:name}));
    const padded=Buffer.alloc(Math.ceil(raw.length/16)*16);raw.copy(padded);
    return {h:id,p:type?undefined:'root',t:type,s:size,k:'root:'+encrypt('aes-128-ecb',folderKey,key,null),
      a:encrypt('aes-128-cbc',attrKey,padded,Buffer.alloc(16))};
  }
  const nodes=[metadata('root','分享',folderKey,folderKey,1,0)],payloads={},requests=[];
  for(const [id,name] of [['vid','video.mp4'],['axis','video.pitch.funscript']]) {
    const secret=crypto.randomBytes(24),enc=sdk.encrypt(secret),parts=[],plain=Buffer.from(name);
    Readable.from([Buffer.from(plain)]).pipe(enc);for await(const part of enc)parts.push(part);
    nodes.push(metadata(id,name,enc.key,secret.subarray(0,16),0,plain.length));payloads[id]=Buffer.concat(parts);
  }
  t.mock.method(global,'fetch',async(url,init)=>{
    if(new URL(url).pathname==='/cs') {
      const [q]=JSON.parse(init.body);
      if(q.a==='f')return Response.json([{f:nodes}]);
      requests.push(q.n);
      return Response.json([{s:payloads[q.n].length,g:'https://fixture.invalid/'+q.n}]);
    }
    const id=new URL(url).pathname.split('/')[1];
    requests.push(id);return new Response(payloads[id]);
  });
  const options={output:root,megaMode:'local',megaConnections:1};
  let tries=0,selected;
  await downloadMega(source,options,{interact:p=>{
    if(p.kind==='password')return ++tries===1?'bad':password;
    assert.equal(p.files.length,2);
    selected=p.files.find(f=>f.name.endsWith('.funscript')).id;return [selected];
  }});
  assert.equal(tries,2);assert.deepEqual(requests,['axis','axis']);
  assert.deepEqual(fs.readdirSync(path.join(root,'分享')),['video.pitch.funscript']);
  assert.equal(fs.readFileSync(path.join(root,'分享','video.pitch.funscript'),'utf8'),'video.pitch.funscript');
  await assert.rejects(downloadMega(source,options,{interact:p=>{
    if(p.kind==='password')return password;
    assert.deepEqual(p.selected,[selected]);return null;
  }}),{code:'MEGA_SKIP'});
  assert.deepEqual(requests,['axis','axis']);
  const state=fs.readFileSync(path.join(root,'mega_local_download_state.json'),'utf8');
  assert.ok(!state.includes(password)&&!state.includes(fixtures[1][2]));
});
