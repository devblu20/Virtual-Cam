(() => {
 'use strict';
 const $=id=>document.getElementById(id); let key='',data=null,current=null,busy=false,generation=0,photoGeneration=0,controller=null;const photoUrls=new Set();
 const message=text=>{$('message').textContent=text;};
 async function api(path,method='GET',body){
   const response=await fetch('/api/admin/'+path,{method,headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',credentials:'omit',redirect:'error',signal:controller.signal});
   const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Request failed.');return result;
 }
 async function photoApi(path,method='GET',body,headers={}){
   const response=await fetch('/api/admin/'+path,{method,headers:{Authorization:'Bearer '+key,...headers},body,cache:'no-store',credentials:'omit',redirect:'error',signal:controller.signal});
   if(response.status===204)return null;
   const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Photo request failed.');return result;
 }
 function clearPhotos(){photoGeneration++;for(const url of photoUrls)URL.revokeObjectURL(url);photoUrls.clear();$('photoGrid').replaceChildren();$('photoEmpty').textContent='No photos saved for this user yet.';$('photoEmpty').hidden=false;$('photoCount').textContent='0 / 20';$('photoMessage').textContent='';$('photoInput').value='';$('photoName').value='';}
 function lock(){generation++;controller?.abort();key='';data=current=null;$('adminKey').value='';$('issuedKey').value='';$('prompt').value='';$('defaultPrompt').value='';$('userList').replaceChildren();clearPhotos();$('workspace').hidden=true;$('issued').hidden=true;$('edit').hidden=true;$('login').hidden=false;message('Locked.');}
 async function action(work){if(busy)return;busy=true;const own=generation;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await work(own);}catch(e){if(own===generation)message(e.name==='AbortError'?'Request cancelled.':e.message);}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
 function counts(){$('count').textContent=$('prompt').value.length+' / 1024 characters';$('defaultCount').textContent=$('defaultPrompt').value.length+' / 1024 characters';$('prompt').disabled=!$('custom').checked;}
 async function loadOriginalPhoto(image,photo,owner,own){
   try{const response=await fetch('/api/admin/users/'+encodeURIComponent(owner)+'/photos/'+encodeURIComponent(photo.id),{headers:{Authorization:'Bearer '+key},cache:'no-store',credentials:'omit',redirect:'error',signal:controller.signal});if(!response.ok)throw Error('Original photo unavailable.');const blob=await response.blob();if(own!==photoGeneration||current?.id!==owner)return;const url=URL.createObjectURL(blob);photoUrls.add(url);image.src=url;}
   catch(error){if(error.name!=='AbortError')image.dataset.preview='thumbnail';}
 }
 function photoCard(photo,owner,own){
   const card=document.createElement('article');card.className='photo-card';
   const image=document.createElement('img');image.src=photo.thumbnail;image.alt=photo.name;image.loading='lazy';
   const copy=document.createElement('div');const name=document.createElement('strong');name.textContent=photo.name;
   const details=document.createElement('span');details.textContent=photo.width+' × '+photo.height;
   const saved=document.createElement('span');saved.className='photo-saved';saved.textContent='Saved in Photo Library';
   const remove=document.createElement('button');remove.type='button';remove.className='photo-delete';remove.textContent='Delete';
   remove.onclick=()=>{if(!current||current.id!==owner||!confirm('Delete “'+photo.name+'” from this user’s Photo Library?'))return;void action(async()=>{await photoApi('users/'+encodeURIComponent(owner)+'/photos/'+encodeURIComponent(photo.id),'DELETE');await loadPhotos(owner);$('photoMessage').textContent='Photo deleted.';});};
   copy.append(name,details,saved);card.append(image,copy,remove);void loadOriginalPhoto(image,photo,owner,own);return card;
 }
 async function loadPhotos(owner){
   const own=++photoGeneration;$('photoGrid').replaceChildren();$('photoEmpty').textContent='No photos saved for this user yet.';$('photoEmpty').hidden=true;$('photoCount').textContent='Loading…';$('photoMessage').textContent='';
   try{const result=await photoApi('users/'+encodeURIComponent(owner)+'/photos');if(own!==photoGeneration||current?.id!==owner)return;$('photoCount').textContent=result.references.length+' / '+result.limit;$('photoEmpty').hidden=result.references.length!==0;for(const photo of result.references)$('photoGrid').append(photoCard(photo,owner,own));}
   catch(error){if(own!==photoGeneration||current?.id!==owner||error.name==='AbortError')return;$('photoCount').textContent='Unavailable';$('photoEmpty').hidden=false;$('photoEmpty').textContent='Photos could not be loaded.';$('photoMessage').textContent=error.message;}
 }
 function select(id){current=data.users.find(u=>u.id===id)||null;$('edit').hidden=!current;clearPhotos();if(!current)return;$('userId').textContent=current.id;$('name').value=current.name;$('room').value=current.room;$('active').checked=!!current.active;$('custom').checked=current.prompt!==null;$('prompt').value=current.prompt??data.default.prompt;counts();void loadPhotos(current.id);}
 async function load(own,selected=current?.id){const result=await api('users');if(own!==generation)return;data=result;$('runtimeModel').value=data.runtime.model;$('runtimeEnhance').checked=data.runtime.enhance;$('runtimeAnchor').checked=data.runtime.selfAnchor;$('userList').replaceChildren();for(const u of data.users){const o=document.createElement('option');o.value=u.id;o.textContent=u.name+(u.active?'':' (disabled)')+' · '+u.id.slice(0,8);$('userList').append(o);}$('defaultPrompt').value=data.default.prompt;$('workspace').hidden=false;$('login').hidden=true;if(selected&&data.users.some(u=>u.id===selected))$('userList').value=selected;select($('userList').value);counts();}
 function showKey(value){$('issuedKey').value=value;$('issued').hidden=false;$('issued').scrollIntoView({block:'nearest'});}
 $('login').onsubmit=e=>{e.preventDefault();key=$('adminKey').value.trim();controller=new AbortController();void action(async own=>{await load(own);if(own===generation){$('adminKey').value='';message('Ready. Select a user or create one.');}});};
 $('refresh').onclick=()=>void action(async own=>{await load(own);message('Refreshed.');});$('lock').onclick=lock;$('hideKey').onclick=()=>{$('issuedKey').value='';$('issued').hidden=true;};
 $('create').onsubmit=e=>{e.preventDefault();void action(async own=>{const r=await api('users','POST',{name:$('newName').value});if(own!==generation)return;showKey(r.accessKey);$('newName').value='';await load(own,r.user.id);message('User created. Save and privately share their key.');});};
 $('edit').onsubmit=e=>{e.preventDefault();if(!current)return;void action(async own=>{await api('users/'+encodeURIComponent(current.id),'PUT',{revision:current.revision,name:$('name').value,active:$('active').checked,room:$('room').value,prompt:$('custom').checked?$('prompt').value:null});await load(own);message('Saved. Prompt changes apply on the next session.');});};
 $('rotate').onclick=()=>{if(!current||!confirm('Replace this user’s key? Their previous key will stop working. Unsaved form edits are not applied.'))return;void action(async own=>{const r=await api('users/'+encodeURIComponent(current.id)+'/rotate-key','POST',{revision:current.revision});if(own!==generation)return;showKey(r.accessKey);await load(own);message('Key replaced. Save the new key before leaving.');});};
 $('uploadPhoto').onclick=()=>{if(!current)return;const owner=current.id,file=$('photoInput').files?.[0],name=$('photoName').value.trim()||file?.name.replace(/\.[^.]+$/,'');if(!file){$('photoMessage').textContent='Choose a photo first.';return;}if(!['image/jpeg','image/png','image/webp'].includes(file.type)){$('photoMessage').textContent='Choose a JPG, PNG or WebP photo.';return;}if(file.size>2*1024*1024){$('photoMessage').textContent='Photo must be 2 MB or smaller.';return;}void action(async()=>{await photoApi('users/'+encodeURIComponent(owner)+'/photos','POST',file,{'Content-Type':file.type,'X-Reference-Name':encodeURIComponent((name||'Reference photo').slice(0,80))});if(current?.id!==owner)return;$('photoInput').value='';$('photoName').value='';await loadPhotos(owner);$('photoMessage').textContent='Saved permanently in this user’s Photo Library. No extra Save button is needed.';message('Photo saved for '+current.name+'.');});};
 $('runtime').onsubmit=e=>{e.preventDefault();void action(async own=>{await api('runtime','PUT',{revision:data.runtime.revision,model:$('runtimeModel').value,enhance:$('runtimeEnhance').checked,selfAnchor:$('runtimeAnchor').checked});await load(own);message('Processing settings saved for new sessions.');});};
 $('defaults').onsubmit=e=>{e.preventDefault();void action(async own=>{await api('default-prompt','PUT',{revision:data.default.revision,prompt:$('defaultPrompt').value});await load(own);message('Default saved for the next 0.4.1 sessions.');});};
 $('userList').onchange=()=>select($('userList').value);$('custom').onchange=counts;$('prompt').oninput=counts;$('defaultPrompt').oninput=counts;window.addEventListener('pagehide',lock);
})();
