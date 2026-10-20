'use strict';
// Shuyu Lite: AI processing only. Books, jobs and versions all live on the Windows backend, as in the full page.
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
const escapeHTML=(v='')=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=n=>new Intl.NumberFormat('zh-CN').format(n||0);
const sizeText=n=>n>=1048576?`${(n/1048576).toFixed(1)} MB`:n>=1024?`${Math.round(n/1024)} KB`:`${n} B`;
const formatDate=n=>n?new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'';
const wordsText=n=>n===null||n===undefined?'字数待统计':n>=10000?`${(n/10000).toFixed(1)} 万字`:`${number(n)} 字`;
const formats={'.txt':'TXT','.text':'TXT','.md':'MD','.epub':'EPUB','.eqpub':'EPUB'};
const names={author_note:'作者话 / 广告',garbled:'乱码修复',censored:'屏蔽字推测',custom:'自定义要求'};
const store={get:key=>{try{return localStorage.getItem(key)||'';}catch{return '';}},set:(key,value)=>{try{value?localStorage.setItem(key,value):localStorage.removeItem(key);}catch{}}};
// Same address and sign-in keys as the full page; on GitHub Pages both pages share one origin, so one login covers both.
const hostedPage=location.hostname.endsWith('.github.io');
const DEFAULT_SERVER='https://windows.tailc0b5f5.ts.net';
const serverBase=()=>hostedPage?store.get('shuyu-server')||DEFAULT_SERVER:'';
const TASK_KEY='shuyu-lite-task';
let session={local:true};let signedIn;const authReady=new Promise(resolve=>signedIn=resolve);
const lite={book:null,job:null,page:1,selected:new Set(),seenDefaults:new Set(),timer:null,renderKey:'',search:0,generation:0,version:null};

// The offline prompt already explains it, so these are never shown again as an error toast.
class OfflineError extends Error{constructor(){super('连不上 Windows 电脑上的书屿');}}
// Every request goes through here, so an unreachable Windows backend always shows the same prompt.
// A tunnel whose backend is down answers 502–504 instead of failing to connect.
async function reach(path,options={}){
  const token=store.get('shuyu-token');if(token)options.headers={...options.headers,Authorization:'Bearer '+token};
  try{const response=await fetch(serverBase()+path,options);if([502,503,504].includes(response.status))throw new TypeError('backend down');return response;}
  catch(error){if(error instanceof TypeError){showOffline();throw new OfflineError();}throw error;}
}
const request=(path,data)=>reach(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
async function api(path,data,file){
  await authReady;const response=file?await reach(path,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file}):await request(path,data);
  if(response.status===401){store.set('shuyu-token','');showLogin('登录已过期，请重新登录。');throw new Error('登录已过期，请重新登录');}
  let result;try{result=await response.json();}catch{showOffline();throw new OfflineError();}
  if(!response.ok)throw new Error(result.error||'操作未完成');return result;
}
let started=false;
function showOffline(){const dialog=$('#offline');$('#offline-server').hidden=!hostedPage;$('#offline-detail').textContent=hostedPage?'当前连接的地址：'+serverBase():'';if(!dialog.open)dialog.showModal();}
$('#offline').addEventListener('cancel',event=>{if(!started)event.preventDefault();});
$('#offline-retry').onclick=async()=>{
  const button=$('#offline-retry');button.disabled=true;$('#offline-detail').textContent='正在连接…';
  try{const response=await fetch(serverBase()+'/api/session');if(!response.ok)throw new Error();
    if(!started)return location.reload();$('#offline').close();toast('已重新连接');if(lite.job)refreshJob();}
  catch{$('#offline-detail').textContent='还是连不上。确认电脑和书屿都在运行后再试一次。';}finally{button.disabled=false;}
};
$('#offline-server').onclick=()=>{$('#offline').close();showLogin('',true);};
let toastTimer;
function toast(message,error=false){const el=$('#toast');el.textContent=message;el.classList.toggle('error',error);el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,error?7000:3500);}
function onError(error){if(error instanceof OfflineError)return;console.error(error);toast(error.message||'连接已中断，请确认书屿后台正在运行',true);}

function showLogin(message='',changeServer=false){
  $('#app').hidden=true;$('#login').hidden=false;$('#logout').hidden=true;
  $('#login-server-field').hidden=!hostedPage||!(changeServer||store.get('shuyu-server'));$('#login-change-server').hidden=!hostedPage||!$('#login-server-field').hidden;
  $('#login-server').value=serverBase();$('#login-message').textContent=message||'输入访问密码登录。这台设备之后会保持登录，完整版书屿也一起登录。';$('#login-message').classList.toggle('notice',!!message);
}
$('#login-change-server').onclick=()=>{$('#login-server-field').hidden=false;$('#login-change-server').hidden=true;$('#login-server').focus();};
$('#login-form').onsubmit=async event=>{event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;
  try{if(hostedPage){const server=$('#login-server').value.trim().replace(/\/+$/,'');if(!/^https:\/\//.test(server))throw new Error('网页版地址需要以 https:// 开头。');store.set('shuyu-server',server===DEFAULT_SERVER?'':server);}
    const response=await request('/api/login',{password:$('#login-password').value});const result=await response.json();if(!response.ok)throw new Error(result.error||'登录失败');store.set('shuyu-token',result.token);location.reload();}
  catch(error){showLogin(error.message,!$('#login-server-field').hidden);button.disabled=false;}};
$('#logout').onclick=async()=>{try{await api('/api/logout',{});}catch{}store.set('shuyu-token','');location.reload();};

// Step 1: pick a book.
let searchTimer;
async function search(){
  const id=++lite.search,q=$('#search').value.trim();
  try{const data=await api('/api/books?'+new URLSearchParams({q,kind:'text,epub',sort:'added',limit:30}));if(id!==lite.search)return;
    $('#results').innerHTML=data.items.map(b=>`<button class="result" type="button" data-book="${b.id}"><strong>${escapeHTML(b.title)}</strong><span>${escapeHTML([b.author,formats[b.ext]||b.ext.slice(1).toUpperCase(),wordsText(b.word_count)].filter(Boolean).join(' · '))}</span></button>`).join('');
    $('#result-count').textContent=data.total?(q?`找到 ${number(data.total)} 本`:'最近发现的书')+(data.total>data.items.length?`，显示前 ${data.items.length} 本`:''):q?'没有找到这本书。换个关键词试试。':'书库里还没有 TXT 或 EPUB 小说。';
  }catch(error){if(id===lite.search)onError(error);}
}
$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(search,240);};
$('#results').onclick=event=>{const button=event.target.closest('[data-book]');if(button)chooseBook(button.dataset.book);};
$('.source-tabs').onclick=event=>{const tab=event.target.closest('[data-source]');if(!tab)return;
  $$('.source-tab').forEach(el=>{el.classList.toggle('active',el===tab);el.setAttribute('aria-selected',String(el===tab));});
  $('#library-source').hidden=tab.dataset.source!=='library';$('#upload-form').hidden=tab.dataset.source!=='upload';};

// Or upload one: it is saved into the Windows library and then processed like any other book.
// The same rules as book_names.py on the backend, so what is shown here is what gets saved.
// Only a field the reader edits is sent; the backend reads the rest from the file itself.
function nameFromFile(name){
  const stem=name.replace(/\.[^.]+$/,''),titled=stem.match(/《(.+?)》/);
  const author=((stem.match(/(?:作者[：:\s]*|\s+by\s*)([^（(\[【]+)/i)||[])[1]||'').replace(/^[\s_-]+|[\s_-]+$/g,'');
  const title=titled?titled[1]:stem.replace(/^[\[【].*?[\]】]\s*/,'').split(/作者[：:]|\s+by\s+/i)[0];
  return {title:title.trim()||stem,author,explicit:!!titled};
}
function nameFromText(text){
  let title='',author='',match;
  for(const line of text.slice(0,3000).split(/\r?\n/).filter(line=>line.trim()).slice(0,30)){
    if(!title&&(match=line.match(/^\s*(?:书名|标题|作品名称?|小说名称?)\s*[：:]\s*《?(.+?)》?\s*$/)||line.match(/^\s*《(.+?)》/)))title=match[1].trim();
    if(!author&&(match=line.match(/作者\s*[：:]\s*([^\s（(\[【]+)/)))author=match[1].trim();
  }
  return {title,author};
}
async function readOpening(file){
  const bytes=new Uint8Array(await file.slice(0,65536).arrayBuffer());
  if(bytes[0]===0xff&&bytes[1]===0xfe||bytes[0]===0xfe&&bytes[1]===0xff)return new TextDecoder('utf-16').decode(bytes);
  try{return new TextDecoder('utf-8',{fatal:true}).decode(bytes,{stream:true});}catch{return new TextDecoder('gb18030').decode(bytes);}
}
$('#upload-file').onchange=async()=>{
  const file=$('#upload-file').files[0];$('#upload-error').hidden=true;$('#upload-names').hidden=!file;if(!file)return;
  $('#upload-file-name').textContent=`${file.name} · ${(file.size/1048576).toFixed(1)} MB`;
  for(const input of [$('#upload-title'),$('#upload-author')]){input.value='';delete input.dataset.edited;}
  const named=nameFromFile(file.name),epub=/\.epub$/i.test(file.name);
  let inner={title:'',author:''};if(!epub)try{inner=nameFromText(await readOpening(file));}catch{}
  if(file!==$('#upload-file').files[0])return;
  // An EPUB's own title and author are read on the Windows side when the file name does not name the book.
  $('#upload-title').value=named.explicit?named.title:epub?'':inner.title||named.title;
  $('#upload-author').value=named.author||inner.author;
  $('#upload-title').placeholder=$('#upload-author').placeholder=epub?'上传时读取电子书里的信息':'没有识别到';
  $('#upload-detected').textContent=epub&&!named.explicit?'书名和作者会在上传时从电子书里读取；需要的话可以直接填写。':'已自动识别，可以直接上传；需要的话可以修改。';
};
for(const input of [$('#upload-title'),$('#upload-author')])input.oninput=()=>{input.dataset.edited='1';};
$('#upload-form').onsubmit=async event=>{
  event.preventDefault();const file=$('#upload-file').files[0],button=$('button[type="submit"]',event.target);$('#upload-error').hidden=true;
  const fail=message=>{$('#upload-error').textContent=message;$('#upload-error').hidden=false;};
  if(!file)return fail('请选择要上传的小说文件。');
  if(!/\.(txt|md|epub)$/i.test(file.name))return fail('只能上传 TXT、MD 或 EPUB 小说。');
  if(file.size>64*1024*1024)return fail('只能上传 64 MB 以内的文件。');
  if('edited' in $('#upload-title').dataset&&!$('#upload-title').value.trim())return fail('请填写书名。');
  button.disabled=true;button.textContent='正在上传到 Windows 书库…';
  try{const book=await api('/api/lite/upload?'+new URLSearchParams({name:file.name,...Object.fromEntries([['title',$('#upload-title')],['author',$('#upload-author')]].filter(([,input])=>'edited' in input.dataset).map(([key,input])=>[key,input.value.trim()]))}),undefined,file);
    $('#upload-form').reset();$('#upload-names').hidden=true;$('#upload-file-name').textContent='选择 TXT、MD 或 EPUB 文件（64 MB 以内）';
    toast('已保存到 Windows 书库：'+book.rel);await chooseBook(book.id);}
  catch(error){fail(error.message);}finally{button.disabled=false;button.textContent='上传到 Windows 书库';}
};
async function chooseBook(id,versionId){
  try{
    const book=await api('/api/book?'+new URLSearchParams({id}));lite.book=book;const versions=book.versions||[];
    $('#chosen-title').textContent=book.title;$('#chosen-meta').textContent=[book.author,formats[book.ext]||book.ext.slice(1).toUpperCase()].filter(Boolean).join(' · ');
    $('#version').innerHTML=versions.map(v=>`<option value="${v.id}">${escapeHTML(v.label)}</option>`).join('')+'<option value="">原始版本</option>';
    $('#version').value=versionId??(versions[0]?.id||'');
    $('#encoding').disabled=['.epub','.eqpub'].includes(book.ext);
    $('#version-count').textContent=`· ${versions.length+1} 个`;
    $('#version-list').innerHTML=[...versions.map(v=>({id:v.id,label:v.label,meta:`${formatDate(v.created)} · 采用 ${v.edit_count} 项修改 · ${sizeText(v.size)}`})),{id:'',label:'原始版本',meta:book.rel.startsWith('Lite上传/')?'Lite 上传的原文件':'书库里的原文件'}]
      .map(v=>`<li><div><strong>${escapeHTML(v.label)}</strong><span>${escapeHTML(v.meta)}</span></div><button class="button" type="button" data-download="${v.id}">下载</button></li>`).join('');
    $('#search-area').hidden=true;$('#chosen').hidden=false;showStep('setup');
  }catch(error){onError(error);}
}
$('#change-book').onclick=()=>{lite.book=null;$('#chosen').hidden=true;$('#search-area').hidden=false;showStep();$('#search').focus();};
function showStep(step){for(const name of ['setup','review','done'])$('#'+name).hidden=name!==step;$('#pick').hidden=step==='review'||step==='done';}

// Step 2: choose what to do.
function syncSetup(){
  // A custom instruction always checks the whole text; the reader's own choice comes back when it is turned off.
  const custom=$('#custom-toggle').checked,mode=$('#mode');$('#custom-fields').hidden=!custom;
  if(custom&&!mode.disabled){lite.mode=mode.value;mode.value='full';}else if(!custom&&mode.disabled)mode.value=lite.mode||'focused';
  mode.disabled=custom;
  $('#mode-hint').textContent=custom?'按你的要求修改时，Claude 需要逐段检查全文，用量较大。':'疑似片段检查可能漏掉没有明显标记的内容。屏蔽字只是根据上下文推测，不能保证是原文；乱码和屏蔽字的建议默认不勾选。';
}
$('#setup-form').onchange=syncSetup;
$('#setup-form').onsubmit=async event=>{
  event.preventDefault();const button=$('button[type="submit"]',event.target);$('#setup-error').hidden=true;
  const categories=$$('input[name="task"]:checked').map(el=>el.value),instruction=$('#instruction').value.trim();
  if(!categories.length)return setupError('请至少选择一项。');
  if(categories.includes('custom')&&!instruction)return setupError('请写下你的要求。');
  button.disabled=true;
  try{const job=await api('/api/clean/prepare',{id:lite.book.id,version_id:$('#version').value,mode:$('#mode').value,encoding:$('#encoding').disabled?'auto':$('#encoding').value,categories,instruction});
    store.set(TASK_KEY,JSON.stringify({id:job.id,title:job.title}));openJob(job);}
  catch(error){setupError(error.message);}finally{button.disabled=false;}
};
function setupError(message){$('#setup-error').textContent=message;$('#setup-error').hidden=false;}

// Step 3: run, review and save.
function openJob(job){clearTimeout(lite.timer);lite.generation++;Object.assign(lite,{job,page:1,renderKey:''});lite.selected.clear();lite.seenDefaults.clear();$('#label').value='';$('#resume').hidden=true;showStep('review');renderJob(job);}
function selectionCount(){$('#selection').textContent=`已选择 ${lite.selected.size} 项修改`;$('#save').textContent=`采用 ${lite.selected.size} 项，另存为新版本`;$('#save').disabled=!lite.selected.size;}
function renderJob(job){
  lite.job=job;
  job.defaults.forEach(id=>{if(!lite.seenDefaults.has(id)){lite.selected.add(id);lite.seenDefaults.add(id);}});
  const terminal=job.transport==='terminal',tasks=(job.options?.categories||[]).map(c=>names[c]).join('、');
  $('#job-summary').textContent=`《${job.title}》 · ${tasks} · 正文 ${number(job.characters)} 字 · ${number(job.chunks)} 个片段，含上下文约 ${number(job.send_characters)} 字 · 编码 ${job.encoding} · ${job.model}，${terminal?'通过本机 Claude Code 订阅':'发送到 '+job.destination}`;
  $('#job-instruction').hidden=!job.options?.instruction;$('#job-instruction').textContent='你的要求：'+(job.options?.instruction||'');
  const status={prepared:'计划已就绪，还没有发送任何内容。',running:'Claude 正在检查…',ready:job.chunks?'检查完成，请逐项确认。':'没有发现明显标记。可以放弃这个任务，改用「逐段检查全文」再试。',paused:'已停止后续请求，可以继续，或先采用已有建议。',failed:'检查中断，可以重试，或先采用已有建议。'};
  const inputTokens=(job.usage.input_tokens||0)+(job.usage.cache_creation_input_tokens||0)+(job.usage.cache_read_input_tokens||0);
  $('#job-status').textContent=`${status[job.status]||job.status} ${job.progress} / ${job.chunks} · ${job.patch_total} 项建议${!job.can_start&&job.chunks?' · 还没有配置 Claude，请先在完整版书屿的「书库设置 → Claude AI 设置」里配置。':''}${job.rejected?` · ${job.rejected} 项无效或重叠的建议已排除`:''}${inputTokens?` · 用量 ${number(inputTokens)} 输入 / ${number(job.usage.output_tokens)} 输出 tokens`:''}`;
  $('#progress').max=Math.max(1,job.chunks);$('#progress').value=job.progress;
  $('#job-error').hidden=!job.error;$('#job-error').textContent=job.error;
  $('#start').hidden=['running','ready'].includes(job.status);$('#start').disabled=!job.can_start&&job.chunks>0;$('#start').textContent=job.chunks===0?'无需发送，完成检查':job.status==='prepared'?(terminal?'使用本机 Claude 订阅开始检查':'发送片段给 Claude，开始检查'):'继续发送未完成的片段';
  $('#stop').hidden=job.status!=='running';$('#stop').disabled=job.cancel;$('#stop').textContent=job.cancel?'等待当前请求结束…':'停止后续请求';
  $('#save-panel').hidden=!['ready','paused','failed'].includes(job.status)||!job.patch_total;
  const renderKey=JSON.stringify([job.id,job.page,job.patch_total,job.status]);
  if(renderKey!==lite.renderKey){
    lite.renderKey=renderKey;
    $('#patches').innerHTML=job.patches.map(p=>`<article class="patch"><label class="patch-heading"><input type="checkbox" data-patch="${p.id}" ${lite.selected.has(p.id)?'checked':''}><strong>${names[p.category]||p.category}</strong><span>${Math.round(p.confidence*100)}% 置信度</span></label><p class="hint">${escapeHTML(p.reason)}${p.category==='censored'?' · 仅为推测，请核对。':''}</p><div class="patch-compare"><div><span>原文</span><pre>${escapeHTML(p.before)}</pre></div><div><span>${p.after?'修改为':'建议删除'}</span><pre>${p.after?escapeHTML(p.after):'（删除这段内容）'}</pre></div></div></article>`).join('');
    $('#pages').innerHTML=job.pages>1?`<button class="button" type="button" data-page="${job.page-1}" ${job.page<=1?'disabled':''}>上一页</button><span>${job.page} / ${job.pages}</span><button class="button" type="button" data-page="${job.page+1}" ${job.page>=job.pages?'disabled':''}>下一页</button>`:'';
  }
  selectionCount();
}
async function refreshJob(){
  clearTimeout(lite.timer);if(!lite.job)return;const generation=lite.generation;
  try{const job=await api('/api/clean/job?'+new URLSearchParams({job_id:lite.job.id,page:lite.page}));if(generation!==lite.generation)return;renderJob(job);if(job.status==='running')lite.timer=setTimeout(refreshJob,1800);}
  catch(error){if(generation===lite.generation){onError(error);lite.timer=setTimeout(refreshJob,4000);}}
}
async function jobAction(action){
  const button=$('#'+action);button.disabled=true;
  try{await api('/api/clean/'+(action==='stop'?'cancel':action),{job_id:lite.job.id});await refreshJob();}catch(error){onError(error);button.disabled=false;}
}
$('#start').onclick=()=>jobAction('start');$('#stop').onclick=()=>jobAction('stop');
$('#patches').onchange=event=>{const id=event.target.dataset.patch;if(!id)return;if(event.target.checked)lite.selected.add(id);else lite.selected.delete(id);selectionCount();};
$('#select-none').onclick=()=>{lite.selected.clear();$$('[data-patch]').forEach(el=>el.checked=false);selectionCount();};
$('#pages').onclick=event=>{const button=event.target.closest('[data-page]');if(button&&!button.disabled){lite.page=Number(button.dataset.page);refreshJob();}};
$('#discard').onclick=async()=>{
  const job=lite.job;clearTimeout(lite.timer);lite.generation++;lite.job=null;store.set(TASK_KEY,'');
  if(job?.status==='running')try{await api('/api/clean/cancel',{job_id:job.id});}catch{}
  if(lite.book&&lite.book.id===job?.book_id){$('#search-area').hidden=true;$('#chosen').hidden=false;showStep('setup');}else{lite.book=null;$('#chosen').hidden=true;$('#search-area').hidden=false;showStep();}
};
$('#save').onclick=async()=>{
  const button=$('#save');button.disabled=true;
  try{const version=await api('/api/clean/save',{job_id:lite.job.id,selected:[...lite.selected],label:$('#label').value});
    lite.version=version;store.set(TASK_KEY,'');clearTimeout(lite.timer);
    $('#done-message').textContent=`《${lite.job.title}》已另存为「${version.label}」，采用了 ${version.edit_count} 项修改。原文件和已有版本都保留着。`;showStep('done');}
  catch(error){onError(error);button.disabled=false;}
};
async function download(bookId,versionId){
  try{const result=await api('/api/download/ticket',{id:bookId,version_id:versionId});toast('正在下载…');location.href=serverBase()+result.url;}catch(error){onError(error);}
}
$('#download').onclick=()=>download(lite.version.book_id,lite.version.id);
$('#version-list').onclick=event=>{const button=event.target.closest('[data-download]');if(button)download(lite.book.id,button.dataset.download);};
$('#continue').onclick=()=>{lite.job=null;chooseBook(lite.version.book_id,lite.version.id);};
$('#again').onclick=()=>{lite.book=null;lite.job=null;lite.version=null;$('#chosen').hidden=true;$('#search-area').hidden=false;$('#search').value='';showStep();search();};

// A task keeps running on the backend after the page closes, so offer to return to it.
async function offerResume(){
  let last=null;try{last=JSON.parse(store.get(TASK_KEY)||'null');}catch{}
  if(!last?.id)return;
  $('#resume').textContent=`继续上次的任务：《${last.title}》`;$('#resume').hidden=false;
  $('#resume').onclick=async()=>{try{const job=await api('/api/clean/job?'+new URLSearchParams({job_id:last.id}));openJob(job);if(job.status==='running')refreshJob();try{lite.book=await api('/api/book?'+new URLSearchParams({id:job.book_id}));}catch{}}catch(error){store.set(TASK_KEY,'');$('#resume').hidden=true;onError(error);}};
}

(async()=>{
  try{const response=await request('/api/session');if(!response.ok)throw new Error();session=await response.json();}
  catch{return showOffline();}
  if(!session.authenticated){store.set('shuyu-token','');return showLogin();}
  started=true;signedIn();$('#app').hidden=false;$('#logout').hidden=session.local;syncSetup();search();offerResume();
})();
