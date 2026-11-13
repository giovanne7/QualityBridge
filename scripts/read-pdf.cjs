const fs = require('node:fs');
const zlib = require('node:zlib');
const source = fs.readFileSync(process.argv[2]).toString('latin1');
function ascii85(s) {
  const out = []; let group = [];
  for (const c of s.replace(/\s/g, '').replace(/~>$/, '')) {
    if (c === 'z' && !group.length) { out.push(0,0,0,0); continue; }
    group.push(c.charCodeAt(0)-33);
    if (group.length === 5) { emit(group,4); group=[]; }
  }
  if(group.length) { const n=group.length-1; while(group.length<5) group.push(84); emit(group,n); }
  function emit(g,n) { let v=g.reduce((a,b)=>a*85+b,0); const bytes=[v>>>24,(v>>>16)&255,(v>>>8)&255,v&255]; out.push(...bytes.slice(0,n)); }
  return Buffer.from(out);
}
for (const m of source.matchAll(/<<(.*?)>>\s*stream\r?\n([\s\S]*?)endstream/gs)) {
  if(m[1].includes('/Subtype /Image')) continue;
  let b=Buffer.from(m[2].trim(),'latin1');
  if(m[1].includes('ASCII85Decode')) b=ascii85(m[2].trim());
  if(m[1].includes('FlateDecode')) b=zlib.inflateSync(b);
  const strings=[...b.toString('latin1').matchAll(/\(((?:\\.|[^\\)])*)\)\s*Tj/g)].map(x=>x[1].replace(/\\([0-7]{1,3}|.)/g,(_,v)=>/^[0-7]+$/.test(v)?String.fromCharCode(parseInt(v,8)):v));
  if(strings.length) console.log(strings.join('\n'));
}
