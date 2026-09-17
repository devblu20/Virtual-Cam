import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../public/admin.js',import.meta.url),'utf8');
const key='fake-local-admin-key-12345678901234567890';
function harness(fetcher) {
  class Element {
    value='';textContent='';hidden=false;dataset={};children=[];events={};
    append(node){this.children.push(node);} replaceChildren(...nodes){this.children=nodes;}
    addEventListener(name,fn){this.events[name]=fn;}
    fire(name){this.events[name]?.({preventDefault(){}});}
  }
  const elements=new Map(),events={},requests=[];
  const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  el('adminKey').value=key;
  vm.runInNewContext(source,{document:{getElementById:el,createElement:()=>new Element()},window:{addEventListener:(n,f)=>events[n]=f},
    AbortController,URLSearchParams,Date,fetch:async(...args)=>{requests.push(args);return fetcher(...args);}});
  return {el,events,requests};
}
const flush=async()=>{for(let n=0;n<5;n++)await new Promise(setImmediate);};
const data={total:1,seconds:25,users:1,asOf:1000,items:[{created:990,started:995,ended:null,last_seen:1000,source:'extension',platform:'zoom',client_version:'0.3.12',owner:'alice',seconds:25,state:'active',avatars:[{name:'<img src=x onerror=alert(1)>',seconds:25}]}]};
test('admin keeps key out of URLs, renders untrusted names as text, clears key/rows on lock',async()=>{
  const h=harness(async()=>({ok:true,json:async()=>data}));
  h.el('login').fire('submit');await flush();
  assert.equal(h.requests[0][1].headers.Authorization,'Bearer '+key);assert.doesNotMatch(h.requests[0][0],/fake-local/);
  assert.equal(h.el('adminKey').value,'');assert.equal(h.el('dashboard').hidden,false);
  assert.equal(h.el('records').children[0].children[3].children[0].textContent,'<img src=x onerror=alert(1)> · ≈ 0h 0m 25s');
  h.el('logout').fire('click');assert.equal(h.el('records').children.length,0);assert.equal(h.el('dashboard').hidden,true);
  assert.doesNotMatch(source,/innerHTML|localStorage|sessionStorage/);
});
test('locking prevents an in-flight response from repopulating records',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);
  const h=harness(()=>pending);h.el('login').fire('submit');h.el('logout').fire('click');
  resolve({ok:true,json:async()=>data});await flush();
  assert.equal(h.el('dashboard').hidden,true);assert.equal(h.el('records').children.length,0);
});
test('bad credentials and missing storage show errors rather than fabricated empty history',async()=>{
  const h=harness(async()=>({ok:false,json:async()=>({detail:'Administrator access required.'})}));
  h.el('login').fire('submit');await flush();
  assert.match(h.el('message').textContent,/Administrator access required/);assert.equal(h.el('message').dataset.error,'true');
  assert.equal(h.el('records').children.length,0);
});
