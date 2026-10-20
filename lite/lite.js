'use strict';
// Shuyu Lite: AI processing only. Books, jobs and versions all live on the Windows backend, as in the full page.
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
const escapeHTML=(v='')=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=n=>new Intl.NumberFormat('zh-CN').format(n||0);
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

async function request(path,data){const headers={};const token=store.get('shuyu-token');if(token)headers.Authorization='Bearer '+token;if(data!==undefined)headers['Content-Type']='application/json';return fetch(serverBase()+path,data===undefined?(token?{headers}:{}):{method:'POST',headers,body:JSON.stringify(data)});}
async function api(path,data){await authReady;const response=await request(path,data);if(response.status===401){store.set('shuyu-token','');showLogin('登录已过期，请重新登录。');throw new Error('登录已过期，请重新登录');}const result=await response.json();if(!response.ok)throw new Error(result.error||'操作未完成');return result;}
let toastTimer;
function toast(message,error=false){const el=$('#toast');el.textContent=message;el.classList.toggle('error',error);el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,error?7000:3500);}
function onError(error){console.error(error);toast(error.message||'连接已中断，请确认书屿后台正在运行',true);}

function showLogin(message=''){
  $('#app').hidden=true;$('#login').hidden=false;$('#logout').hidden=true;
  const unreachable=message.startsWith('无法连接');
  $('#login-server-field').hidden=!hostedPage||!(unreachable||store.get('shuyu-server'));$('#login-change-server').hidden=!hostedPage||!$('#login-server-field').hidden;
  $('#login-server').value=serverBase();$('#login-message').textContent=message||'输入访问密码登录。这台设备之后会保持登录，完整版书屿也一起登录。';$('#login-message').classList.toggle('notice',!!message);
}
$('#login-change-server').onclick=()=>{$('#login-server-field').hidden=false;$('#login-change-server').hidden=true;$('#login-server').focus();};
$('#login-form').onsubmit=async event=>{event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;
  try{if(hostedPage){const server=$('#login-server').value.trim().replace(/\/+$/,'');if(!/^https:\/\//.test(server))throw new Error('网页版地址需要以 https:// 开头。');store.set('shuyu-server',server===DEFAULT_SERVER?'':server);}
    const response=await request('/api/login',{password:$('#login-password').value});const result=await response.json();if(!response.ok)throw new Error(result.error||'登录失败');store.set('shuyu-token',result.token);location.reload();}
  catch(error){showLogin(error instanceof TypeError?'无法连接书屿后台，请确认地址正确、Windows 电脑开机且服务正在运行。':error.message);button.disabled=false;}};
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
async function chooseBook(id,versionId){
  try{
    const book=await api('/api/book?'+new URLSearchParams({id}));lite.book=book;const versions=book.versions||[];
    $('#chosen-title').textContent=book.title;$('#chosen-meta').textContent=[book.author,formats[book.ext]||book.ext.slice(1).toUpperCase()].filter(Boolean).join(' · ');
    $('#version').innerHTML=versions.map(v=>`<option value="${v.id}">${escapeHTML(v.label)}</option>`).join('')+'<option value="">原始版本</option>';
    $('#version').value=versionId??(versions[0]?.id||'');
    $('#encoding').disabled=['.epub','.eqpub'].includes(book.ext);
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
$('#download').onclick=async()=>{
  try{const result=await api('/api/download/ticket',{id:lite.version.book_id,version_id:lite.version.id});location.href=serverBase()+result.url;}catch(error){onError(error);}
};
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
  catch{return showLogin('无法连接书屿后台，请确认地址正确、Windows 电脑开机且服务正在运行。');}
  if(!session.authenticated){store.set('shuyu-token','');return showLogin();}
  signedIn();$('#app').hidden=false;$('#logout').hidden=session.local;syncSetup();search();offerResume();
})();
