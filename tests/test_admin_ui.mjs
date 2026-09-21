import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../public/admin.js',import.meta.url),'utf8');
const key='fake-local-admin-key-12345678901234567890';
function harness(fetcher) {
  class Element {
    value='';textContent='';hidden=false;dataset={};children=[];events={};
    append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=nodes;}
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
  assert.equal(h.el('records').children[0].children[3].children[0].children[0].textContent,'<img src=x onerror=alert(1)>');
  assert.equal(h.el('duration').textContent,'25 sec');
  h.el('logout').fire('click');assert.equal(h.el('records').children.length,0);assert.equal(h.el('dashboard').hidden,true);
  assert.doesNotMatch(source,/innerHTML|localStorage|sessionStorage/);
});

const allText=element=>[element.textContent,...element.children.map(allText)].join(' ');

test('self-reported names render as text while older sessions stay unnamed',async()=>{
  const items=[{...data.items[0],participant_name:'<img src=x onerror=alert(1)>'},data.items[0]];
  const h=harness(async()=>({ok:true,json:async()=>({...data,items,total:2})}));
  h.el('login').fire('submit');await flush();
  const rows=h.el('records').children;
  assert.equal(rows[0].children[1].children[0].textContent,'<img src=x onerror=alert(1)>');
  assert.match(allText(rows[0]),/Self-reported · not verified/);
  assert.match(allText(rows[0]),/alice/);
  assert.match(allText(rows[1]),/Name not provided/);
});
test('statuses distinguish successful activity, zero-time attempts and interrupted reporting',async()=>{
  const items=[
    {...data.items[0],state:'ended',started:null,seconds:0,reason:'closed'},
    {...data.items[0],state:'ended',reason:'stopped'},
    {...data.items[0],state:'interrupted',has_gaps:1},
    {...data.items[0],state:'failed',reason:'provider_error'},
    {...data.items[0],state:'active',seconds:0},
  ];
  const h=harness(async()=>({ok:true,json:async()=>({...data,items,total:items.length})}));
  h.el('login').fire('submit');await flush();
  const rows=h.el('records').children;
  assert.match(allText(rows[0]),/No video confirmed/);assert.match(allText(rows[0]),/No measured time/);
  assert.match(allText(rows[1]),/Finished/);assert.match(allText(rows[1]),/Zoom Web/);
  assert.match(allText(rows[2]),/Reports stopped/);assert.match(allText(rows[2]),/Reporting gaps excluded/);
  assert.match(allText(rows[3]),/Connection failed/);
  assert.match(allText(rows[4]),/Activity reported; no timed interval/);
});
test('date shortcuts, validation, clear filters, and refresh use the applied query',async()=>{
  const h=harness(async()=>({ok:true,json:async()=>data}));
  h.el('login').fire('submit');await flush();
  h.el('after').value='2026-09-20';h.el('before').value='2026-09-01';h.el('filters').fire('submit');await flush();
  assert.equal(h.requests.length,1);assert.match(h.el('message').textContent,/on or before/);
  h.el('owner').value='alice';h.el('rangeWeek').fire('click');await flush();
  const query=new URLSearchParams(h.requests.at(-1)[0].split('?')[1]);
  assert.equal(query.get('owner'),'alice');assert.ok(Number(query.get('before'))>Number(query.get('after')));
  h.el('owner').value='not-applied';h.el('refresh').fire('click');await flush();
  assert.match(h.requests.at(-1)[0],/owner=alice/);
  h.el('resetFilters').fire('click');await flush();
  assert.equal(h.el('owner').value,'');assert.doesNotMatch(h.requests.at(-1)[0],/after=|before=/);
});
test('empty states explain no activity versus no filter matches',async()=>{
  const h=harness(async()=>({ok:true,json:async()=>({...data,total:0,seconds:0,users:0,items:[]})}));
  h.el('login').fire('submit');await flush();
  assert.equal(h.el('empty').hidden,false);assert.equal(h.el('emptyTitle').textContent,'No activity yet');
  h.el('owner').value='alice';h.el('filters').fire('submit');await flush();
  assert.equal(h.el('emptyTitle').textContent,'No matching sessions');
  assert.equal(h.el('next').disabled,true);assert.equal(h.el('previous').disabled,true);
});
test('avatar changes remain available and session details retain technical timestamps',async()=>{
  const item={...data.items[0],id:'test-session',seconds:3665,avatars:[{name:'First avatar',seconds:60},{name:'Second avatar',seconds:3605}]};
  const h=harness(async()=>({ok:true,json:async()=>({...data,seconds:3665,items:[item]})}));
  h.el('login').fire('submit');await flush();
  assert.equal(h.el('duration').textContent,'1 hr 1 min 5 sec');
  const text=allText(h.el('records'));
  for(const value of ['First avatar','Second avatar','+1 avatar selection','Session details','test-session','1 min measured'])assert.ok(text.includes(value));
});
test('main website exposes the admin link in a separate tab without adding a credential',()=>{
  const html=fs.readFileSync(new URL('../index.html',import.meta.url),'utf8');
  assert.match(html,/<a class="admin-link" href="\/admin.html" target="_blank" rel="noopener"/);
  assert.match(html,/Admin dashboard/);
  assert.doesNotMatch(html,/VCAM_ADMIN_KEY_SHA256|fake-local-admin-key/);
});
test('an authorization rejection clears previously displayed records',async()=>{
  let valid=true;
  const h=harness(async()=>valid?{ok:true,json:async()=>data}:{ok:false,status:403,json:async()=>({detail:'Administrator access required.'})});
  h.el('login').fire('submit');await flush();valid=false;
  h.el('refresh').fire('click');await flush();
  assert.equal(h.el('dashboard').hidden,true);assert.equal(h.el('records').children.length,0);
  assert.match(h.el('message').textContent,/Administrator access required/);
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
