'use strict';
const {pbkdf2,createHmac,timingSafeEqual}=require('node:crypto');
const derive=require('node:util').promisify(pbkdf2);
const {DownloadError}=require('./transfer');

function protectedPayload(url) {
  if (!/^https:\/\/(?:www\.)?(?:mega\.nz|mega\.io|mega\.co\.nz)\/?#P!/.test(url)) return null;
  const encoded=new URL(url).hash.slice(3);
  const bytes=Buffer.from(encoded,'base64url');
  if (bytes.toString('base64url')!==encoded || ![0,1,2].includes(bytes[0]) ||
      ![0,1].includes(bytes[1]) || bytes.length!==(bytes[1]===0?88:104))
    throw new DownloadError('MEGA_LINK','密码分享格式无效或不属于可下载的文件／文件夹。');
  return bytes;
}

async function unlock(bytes, password) {
  const key=await derive(password.trim(),bytes.subarray(8,40),bytes[0]===0?1000:100000,64,'sha512');
  const payload=bytes.subarray(0,-32),macKey=key.subarray(32);
  // Algorithm 1 used reversed HMAC arguments in the original web client.
  const mac=bytes[0]===1?createHmac('sha256',payload).update(macKey):createHmac('sha256',macKey).update(payload);
  if (!timingSafeEqual(mac.digest(),bytes.subarray(-32))) throw new DownloadError('MEGA_PASSWORD','密码不正确或链接已损坏，请重新输入。');
  const clear=Buffer.from(payload.subarray(40));
  for(let i=0;i<clear.length;i++)clear[i]^=key[i];
  return 'https://mega.nz/'+(bytes[1]?'file':'folder')+'/'+bytes.subarray(2,8).toString('base64url')+'#'+clear.toString('base64url');
}
module.exports={protectedPayload,unlock};
