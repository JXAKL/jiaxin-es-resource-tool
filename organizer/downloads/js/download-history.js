'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');

// Chromium can crash restoring native downloads from a previous persistent
// session. Archive just those records in this app's native-download profiles. Cookies,
// URLs/visits, files on disk and resource_download_state.json are untouched.
function archiveBrowserDownloads(state, profile, platform='hanime') {
  if(!state || !profile || !['hanime','iwara','pixeldrain'].includes(platform))return {archived:0};
  const root=path.resolve(state,'download-browser'), target=path.resolve(profile);
  if(![platform,platform+'-legacy-chrome'].some(name=>path.join(root,name).toLowerCase()===target.toLowerCase()))return {archived:0};
  const history=path.join(target,'Default','History');
  if(!fs.existsSync(history))return {archived:0};
  // Never follow a profile/Default/History junction into a personal profile.
  for(const item of [root,target,path.dirname(history),history]) {
    if(fs.lstatSync(item).isSymbolicLink() || fs.realpathSync(item).toLowerCase()!==path.resolve(item).toLowerCase())
      throw new Error('会话路径包含目录链接，未修改浏览器记录');
  }
  const {DatabaseSync}=require('node:sqlite');
  let db, archive, committed=false;
  try {
    db=new DatabaseSync(history,{enableForeignKeyConstraints:false});
    db.exec('PRAGMA busy_timeout=100; BEGIN EXCLUSIVE');
    const tables=[];
    for(const name of ['downloads','downloads_url_chains']) {
      const schema=db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name=?").get(name);
      if(!schema)continue;
      const query=db.prepare(`SELECT * FROM ${name}`);query.setReadBigInts(true);
      tables.push({name,schema:schema.sql,rows:query.all()});
    }
    const count=tables.reduce((n,t)=>n+t.rows.length,0);
    if(!count){db.exec('ROLLBACK');return {archived:0};}
    const folder=path.join(target,'tool-download-history');
    fs.mkdirSync(folder,{recursive:true});
    if(fs.realpathSync(folder).toLowerCase()!==path.resolve(folder).toLowerCase())throw new Error('下载记录备份路径异常');
    archive=path.join(folder,`${Date.now()}-${randomUUID()}.json`);
    const contents=JSON.stringify({version:1,tables},(_,v)=>typeof v==='bigint'?{$integer:String(v)}:
      ArrayBuffer.isView(v)?{$bytes:Buffer.from(v).toString('base64')}:v);
    const fd=fs.openSync(archive,'wx',0o600);
    try {fs.writeFileSync(fd,contents);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
    // A durable archive must exist before committing any removal.
    for(const name of ['downloads_url_chains','downloads'])if(tables.some(t=>t.name===name))db.exec(`DELETE FROM ${name}`);
    db.exec('COMMIT');committed=true;
    return {archived:tables.find(t=>t.name==='downloads')?.rows.length || count,archive};
  } finally {
    if(db){if(!committed)try{db.exec('ROLLBACK');}catch{} db.close();}
  }
}
module.exports={archiveBrowserDownloads};
