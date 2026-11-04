'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {DatabaseSync}=require('node:sqlite');
const {archiveBrowserDownloads:archiveHanimeDownloads}=require('../organizer/downloads/js/download-history');

function fixture(t,name='hanime') {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'download-history-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const profile=path.join(root,'download-browser',name);
  fs.mkdirSync(path.join(profile,'Default'),{recursive:true});
  const file=path.join(profile,'Default','History'),db=new DatabaseSync(file);
  db.exec("CREATE TABLE downloads(id INTEGER PRIMARY KEY,start_time INTEGER); INSERT INTO downloads VALUES(1,13435745235678901); CREATE TABLE downloads_url_chains(id INTEGER,chain_index INTEGER,url TEXT); INSERT INTO downloads_url_chains VALUES(1,0,'https://example.test/file'); CREATE TABLE urls(id INTEGER,url TEXT); INSERT INTO urls VALUES(1,'https://example.test/watch');");
  db.close();
  fs.writeFileSync(path.join(profile,'Default','Cookies'),'login remains');
  return {root,profile,file};
}
test('archives exact download records while preserving browsing history and login files',t=>{
  const {root,profile,file}=fixture(t);
  const result=archiveHanimeDownloads(root,profile);assert.equal(result.archived,1);
  const archive=JSON.parse(fs.readFileSync(result.archive,'utf8'));
  assert.equal(archive.tables[0].rows[0].start_time.$integer,'13435745235678901');
  assert.equal(archive.tables[1].rows[0].url,'https://example.test/file');
  const db=new DatabaseSync(file);
  try {
  assert.equal(db.prepare('SELECT count(*) AS n FROM downloads').get().n,0);
  assert.equal(db.prepare('SELECT count(*) AS n FROM downloads_url_chains').get().n,0);
  assert.equal(db.prepare('SELECT url FROM urls').get().url,'https://example.test/watch');
  assert.equal(fs.readFileSync(path.join(profile,'Default','Cookies'),'utf8'),'login remains');
  assert.equal(archiveHanimeDownloads(root,profile).archived,0);
  } finally {db.close();}
});
test('does not modify another site or a profile outside the app session directory',t=>{
  const {root,profile,file}=fixture(t,'iwara');const before=fs.readFileSync(file);
  assert.equal(archiveHanimeDownloads(root,profile).archived,0);
  assert.equal(archiveHanimeDownloads(path.join(root,'elsewhere'),profile).archived,0);
  assert.deepEqual(fs.readFileSync(file),before);
  assert.equal(archiveHanimeDownloads(root,path.join(root,'download-browser','hanime')).archived,0);
});
test('locked database and backup failure both leave all download records intact',t=>{
  const {root,profile,file}=fixture(t,'hanime-legacy-chrome');
  const db=new DatabaseSync(file);
  try {
  db.exec('BEGIN EXCLUSIVE');
  assert.throws(()=>archiveHanimeDownloads(root,profile),/locked/);
  db.exec('ROLLBACK');
  fs.writeFileSync(path.join(profile,'tool-download-history'),'not a directory');
  assert.throws(()=>archiveHanimeDownloads(root,profile));
  assert.equal(db.prepare('SELECT count(*) AS n FROM downloads').get().n,1);
  assert.equal(db.prepare('SELECT count(*) AS n FROM downloads_url_chains').get().n,1);
  } finally {db.close();}
});
