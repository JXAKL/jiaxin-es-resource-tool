// Bundle standalone open-source network libraries, not KML application code.
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'), modules=path.resolve(process.argv[2]||path.join(root,'../KML/node_modules'));
const target=path.join(root,'organizer/downloads/js/vendor');
fs.mkdirSync(target,{recursive:true});
const result=require(path.join(modules,'esbuild')).buildSync({
  entryPoints:[path.join(modules,'megajs/dist/main.node-cjs.js')],bundle:true,platform:'node',format:'cjs',
  target:'node22',minify:true,metafile:true,outfile:path.join(target,'megajs.cjs'),legalComments:'eof'});
const net=require(path.join(modules,'esbuild')).buildSync({
  stdin:{contents:`const {fetch,ProxyAgent}=require(${JSON.stringify(path.join(modules,'undici'))});
    module.exports={fetch,ProxyAgent,...require(${JSON.stringify(path.join(modules,'http-proxy-agent'))}),...require(${JSON.stringify(path.join(modules,'https-proxy-agent'))})};`,resolveDir:modules},
  bundle:true,platform:'node',format:'cjs',target:'node22',minify:true,metafile:true,
  outfile:path.join(target,'network.cjs'),legalComments:'eof'});
const packages=new Set();
for(const input of [...Object.keys(result.metafile.inputs),...Object.keys(net.metafile.inputs)].filter(p=>p!=='<stdin>')) {
  let dir=path.dirname(path.resolve(input));
  while(dir!==path.dirname(dir)) {
    if(fs.existsSync(path.join(dir,'package.json'))){packages.add(dir);break;}
    dir=path.dirname(dir);
  }
}
const notices=[],manifest=[];
for(const dir of [...packages].sort()) {
  const pkg=JSON.parse(fs.readFileSync(path.join(dir,'package.json'),'utf8'));
  manifest.push({name:pkg.name,version:pkg.version,license:pkg.license});
  const files=fs.readdirSync(dir).filter(n=>/^(license|copying)/i.test(n));
  if(!files.length)throw Error('Missing license: '+pkg.name);
  notices.push(`${pkg.name} ${pkg.version}\n${files.map(n=>fs.readFileSync(path.join(dir,n),'utf8')).join('\n')}`);
}
fs.writeFileSync(path.join(target,'THIRD_PARTY_LICENSES.txt'),notices.join('\n\n--------------------\n\n'));
fs.writeFileSync(path.join(target,'versions.json'),JSON.stringify(manifest,null,2));
console.log(JSON.stringify({bytes:fs.statSync(path.join(target,'megajs.cjs')).size+fs.statSync(path.join(target,'network.cjs')).size,packages:manifest}));
