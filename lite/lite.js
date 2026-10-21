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
const TASK_KEY='shuyu-lite-task',BATCH_KEY='shuyu-lite-batch';
// Lite has its own Claude settings on the backend, separate from the full page's.
const PROFILE='lite';
let session={local:true};let signedIn;const authReady=new Promise(resolve=>signedIn=resolve);
const lite={book:null,job:null,page:1,selected:new Set(),seenDefaults:new Set(),timer:null,renderKey:'',search:0,generation:0,version:null,
  uploads:[],batchBooks:null,batch:null,batchTimer:null};

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
  if(!response.ok)throw Object.assign(new Error(result.error||'操作未完成'),{wereadLogin:!!result.weread_login,netdiskLogin:result.netdisk_login||''});return result;
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
  $('#library-source').hidden=tab.dataset.source!=='library';$('#upload-form').hidden=tab.dataset.source!=='upload';$('#netdisk-form').hidden=tab.dataset.source!=='netdisk';
  if(tab.dataset.source==='netdisk')refreshNetdisk();};

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
const ARCHIVE=/\.(zip|rar|7z|tar|gz|bz2|xz)$/i;
async function detectNames(file){
  // An archive is unpacked on the Windows side, where every novel inside gets its own title and author.
  if(ARCHIVE.test(file.name))return {archive:true,title:'',author:''};
  const named=nameFromFile(file.name),epub=/\.epub$/i.test(file.name);
  let inner={title:'',author:''};if(!epub)try{inner=nameFromText(await readOpening(file));}catch{}
  // An EPUB's own title and author are read on the Windows side when the file name does not name the book.
  return {title:named.explicit?named.title:epub?'':inner.title||named.title,author:named.author||inner.author,epub};
}
function renderUploads(){
  $('#upload-list').hidden=!lite.uploads.length;
  $('#upload-list').innerHTML=lite.uploads.map((item,i)=>`<li><div class="upload-file"><span>${escapeHTML(item.file.name)} · ${sizeText(item.file.size)}</span><button class="link-button" type="button" data-remove="${i}">移除</button></div>
    ${item.archive?'<p class="hint">压缩包：上传后自动解压，识别里面每本小说的书名和作者。</p>':`<div class="options"><label>书名<input data-index="${i}" data-field="title" maxlength="60" value="${escapeHTML(item.title)}" placeholder="${item.epub?'上传时读取电子书里的信息':'没有识别到'}"></label>
    <label>作者<input data-index="${i}" data-field="author" maxlength="30" value="${escapeHTML(item.author)}" placeholder="${item.epub?'上传时读取电子书里的信息':'没有识别到'}"></label></div>`}</li>`).join('');
  $('button[type="submit"]',$('#upload-form')).textContent=lite.uploads.length>1?`上传这 ${lite.uploads.length} 本到 Windows 书库`:'上传到 Windows 书库';
}
$('#upload-file').onchange=async()=>{
  $('#upload-error').hidden=true;const files=[...$('#upload-file').files];
  const added=await Promise.all(files.map(async file=>({file,...await detectNames(file),edited:{}})));
  lite.uploads.push(...added);$('#upload-file').value='';renderUploads();
};
// Only a field the reader edits is sent; the backend reads the rest from the file itself.
$('#upload-list').oninput=event=>{const input=event.target.closest('[data-field]');if(!input)return;const item=lite.uploads[input.dataset.index];item[input.dataset.field]=input.value;item.edited[input.dataset.field]=true;};
$('#upload-list').onclick=event=>{const button=event.target.closest('[data-remove]');if(button){lite.uploads.splice(Number(button.dataset.remove),1);renderUploads();}};
$('#upload-form').onsubmit=async event=>{
  event.preventDefault();const button=$('button[type="submit"]',event.target);$('#upload-error').hidden=true;
  const fail=message=>{$('#upload-error').textContent=message;$('#upload-error').hidden=false;};
  if(!lite.uploads.length)return fail('请选择要上传的小说文件。');
  for(const item of lite.uploads){
    if(!item.archive&&!/\.(txt|md|epub)$/i.test(item.file.name))return fail(`${item.file.name}：只能上传 TXT、MD、EPUB 小说或压缩包。`);
    if(item.file.size>(item.archive?512:64)*1024*1024)return fail(`${item.file.name}：只能上传 ${item.archive?'512 MB 以内的压缩包':'64 MB 以内的小说'}。`);
    if(item.edited.title&&!item.title.trim())return fail(`${item.file.name}：请填写书名。`);
  }
  button.disabled=true;const books=[],failed=[],notes=[],total=lite.uploads.length;
  for(const [i,item] of lite.uploads.entries()){
    button.textContent=total>1?`正在上传 ${i+1} / ${total}…`:'正在上传到 Windows 书库…';
    try{
      const result=await api('/api/lite/upload?'+new URLSearchParams({name:item.file.name,...Object.fromEntries(['title','author'].filter(key=>item.edited[key]).map(key=>[key,item[key].trim()]))}),undefined,item.file);
      if(result.books){
        books.push(...result.books);
        notes.push(`${item.file.name}：取出 ${result.books.length} 本小说${result.other_files?`，另有 ${result.other_files} 个 PDF、Word 等文件没有导入`:''}${result.failed.length?`；${result.failed.length} 处没能取出：${result.failed.slice(0,3).join('；')}`:''}`);
      }else books.push(result);
      item.done=true;}
    catch(error){if(error instanceof OfflineError)break;failed.push(`${item.file.name}：${error.message}`);}
  }
  button.disabled=false;
  // Only the books that did not go up stay in the list, ready to try again.
  lite.uploads=lite.uploads.filter(item=>!item.done);renderUploads();
  if(failed.length||notes.length)fail([...(failed.length?[`有 ${failed.length} 个文件没有上传：`,...failed]:[]),...notes].join('\n'));
  if(books.length)await openSaved(books);
};
// Uploaded or downloaded from a share: one book goes straight to setup, several form a batch.
async function openSaved(books){
  toast(books.length>1?`已保存 ${books.length} 本到 Windows 书库的 Lite上传 文件夹`:'已保存到 Windows 书库：'+books[0].rel);
  if(books.length===1)await chooseBook(books[0].id);else chooseBatch(books);
}

// Or a share link from Quark (夸克网盘) or Baidu (百度网盘): the Windows backend saves the novels into the reader's
// own drive, downloads them into the library and stores each like an upload.
const netdisk={signing:'',timer:null,pending:false};
const NETDISK_BUSY=['waiting','scanned','checking'];
function renderNetdisk(status){
  $('#netdisk-accounts').innerHTML=Object.entries(status).map(([key,d])=>`<li><span class="weread-dot${d.signed_in?' on':''}" aria-hidden="true"></span><span>${escapeHTML(d.label)}：${d.signed_in?'已登录'+(d.name?' · '+escapeHTML(d.name):''):'未登录'}</span>${d.signed_in?`<button class="link-button" type="button" data-netdisk-logout="${key}">退出</button>`:`<button class="link-button" type="button" data-netdisk-login="${key}">扫码登录</button>`}</li>`).join('');
}
function renderNetdiskLogin(drive){
  const login=drive.login||{},shown=['waiting','scanned'].includes(login.state),key=login.link||login.image||'';
  $('#netdisk-dialog-title').textContent=`扫码登录${drive.label}`;$('#netdisk-qr').hidden=!shown;
  if(shown&&$('#netdisk-qr').dataset.key!==key){$('#netdisk-qr').innerHTML=login.qr?qrSVG(login.qr):`<img src="${escapeHTML(login.image)}" alt="${escapeHTML(drive.label)}登录二维码">`;$('#netdisk-qr').dataset.key=key;}
  $('#netdisk-login-status').textContent={waiting:`用${drive.app}扫一扫这个二维码，然后在手机上确认登录。二维码过期后会自动换新。`,scanned:'已扫码，请在手机上确认登录。',
    checking:`正在登录${drive.label}…`,failed:login.error||'登录没有完成，请重新获取二维码。'}[login.state]||'';
  $('#netdisk-retry').hidden=login.state!=='failed';
}
async function refreshNetdisk(){try{renderNetdisk(await api('/api/netdisk'));}catch(error){onError(error);}}
async function pollNetdisk(){
  clearTimeout(netdisk.timer);if(!$('#netdisk-dialog').open)return;
  try{
    const status=await api('/api/netdisk'),drive=status[netdisk.signing];renderNetdisk(status);if(!drive||!$('#netdisk-dialog').open)return;
    renderNetdiskLogin(drive);
    // Closing the dialog drops the pending download, so take it first.
    if(drive.signed_in){const pending=netdisk.pending;$('#netdisk-dialog').close();toast(`${drive.label}已登录`);if(pending)fetchNetdisk();return;}
    if(NETDISK_BUSY.includes(drive.login.state))netdisk.timer=setTimeout(pollNetdisk,2000);
  }catch(error){onError(error);}
}
async function signInNetdisk(key){
  try{
    netdisk.signing=key;renderNetdiskLogin(await api('/api/netdisk/login',{drive:key}));
    if(!$('#netdisk-dialog').open)$('#netdisk-dialog').showModal();clearTimeout(netdisk.timer);netdisk.timer=setTimeout(pollNetdisk,2000);
  }catch(error){netdisk.pending=false;onError(error);}
}
$('#netdisk-accounts').onclick=async event=>{
  const login=event.target.closest('[data-netdisk-login]'),logout=event.target.closest('[data-netdisk-logout]');
  if(login)signInNetdisk(login.dataset.netdiskLogin);
  if(logout)try{await api('/api/netdisk/logout',{drive:logout.dataset.netdiskLogout});refreshNetdisk();}catch(error){onError(error);}
};
$('#netdisk-retry').onclick=()=>signInNetdisk(netdisk.signing);
$('#netdisk-close').onclick=()=>$('#netdisk-dialog').close();
$('#netdisk-dialog').addEventListener('close',()=>{clearTimeout(netdisk.timer);netdisk.pending=false;});
$('#netdisk-form').onsubmit=event=>{event.preventDefault();fetchNetdisk();};
async function fetchNetdisk(){
  const button=$('button[type="submit"]',$('#netdisk-form')),text=$('#netdisk-text').value.trim(),progress=$('#netdisk-progress');
  const fail=message=>{$('#netdisk-error').textContent=message;$('#netdisk-error').hidden=!message;};
  const signIn=key=>{netdisk.pending=true;signInNetdisk(key);};
  fail('');if(!text)return fail('请粘贴夸克网盘或百度网盘的分享链接。');
  button.disabled=true;
  try{
    let task=await api('/api/netdisk/fetch',{text});
    while(!['done','failed'].includes(task.state)){
      progress.hidden=false;progress.textContent=task.message;
      await new Promise(resolve=>setTimeout(resolve,1500));
      task=await api('/api/netdisk/task?'+new URLSearchParams({task:task.id}));
    }
    progress.hidden=true;
    fail([...(task.state==='failed'?[task.message]:[]),...(task.failed.length?[`有 ${task.failed.length} 处没有下载：`,...task.failed]:[]),...task.notes,
      ...(task.skipped.length?[`跳过了 ${task.skipped.length} 个不是小说的文件：${task.skipped.slice(0,5).join('、')}${task.skipped.length>5?' 等':''}`]:[])].join('\n'));
    if(task.login){refreshNetdisk();return signIn(task.login);}
    if(task.books.length){$('#netdisk-text').value='';await openSaved(task.books);}
  }catch(error){progress.hidden=true;if(error.netdiskLogin)signIn(error.netdiskLogin);else if(!(error instanceof OfflineError))fail(error.message);}
  finally{button.disabled=false;}
}
async function chooseBook(id,versionId){
  try{
    const book=await api('/api/book?'+new URLSearchParams({id}));lite.book=book;const versions=book.versions||[];
    $('#chosen-title').textContent=book.title;$('#chosen-meta').textContent=[book.author,formats[book.ext]||book.ext.slice(1).toUpperCase()].filter(Boolean).join(' · ');
    $('#version').innerHTML=versions.map(v=>`<option value="${v.id}">${escapeHTML(v.label)}</option>`).join('')+'<option value="">原始版本</option>';
    $('#version').value=versionId??(versions[0]?.id||'');
    $('#encoding').disabled=['.epub','.eqpub'].includes(book.ext);
    $('#version-count').textContent=`· ${versions.length+1} 个`;
    $('#version-list').innerHTML=[...versions.map(v=>({id:v.id,label:v.label,meta:`${formatDate(v.created)} · 采用 ${v.edit_count} 项修改 · ${sizeText(v.size)}`})),{id:'',label:'原始版本',meta:book.rel.startsWith('Lite上传/')?'Lite 上传的原文件':'书库里的原文件'}]
      .map(v=>`<li><div><strong>${escapeHTML(v.label)}</strong><span>${escapeHTML(v.meta)}</span></div><div class="version-actions"><button class="button" type="button" data-weread="${v.id}">传到微信读书</button><button class="button" type="button" data-download="${v.id}">下载</button></div></li>`).join('');
    $('#search-area').hidden=true;$('#chosen').hidden=false;showStep('setup');
  }catch(error){onError(error);}
}
$('#change-book').onclick=()=>{lite.book=null;$('#chosen').hidden=true;$('#search-area').hidden=false;showStep();$('#search').focus();};
function showStep(step){
  for(const name of ['setup','review','batch','done'])$('#'+name).hidden=name!==step;$('#pick').hidden=['review','batch','done'].includes(step);
  $('#setup-batch').hidden=!lite.batchBooks;$('#chosen').hidden=!lite.book||!!lite.batchBooks;
  $('button[type="submit"]',$('#setup-form')).textContent=lite.batchBooks?`为这 ${lite.batchBooks.length} 本生成计划并依次处理`:'生成处理计划';
}
// Several uploads at once: the same choices for every book, processed one after another.
function chooseBatch(books){
  lite.book=null;lite.batchBooks=books;$('#search-area').hidden=true;$('#chosen').hidden=true;$('#batch-chosen').hidden=false;
  $('#batch-count').textContent=`这一批 ${books.length} 本`;$('#batch-titles').textContent=books.map(b=>'《'+b.title+'》').join('、');
  $('#setup-batch').textContent=`这 ${books.length} 本会用同样的设置、从原始版本开始，一本接一本处理。费用只按实际用量计算，一次处理多本不会多花钱。`;
  $('#encoding').disabled=false;showStep('setup');
}
$('#batch-change').onclick=()=>{lite.batchBooks=null;$('#batch-chosen').hidden=true;$('#search-area').hidden=false;showStep();};

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
  const options={mode:$('#mode').value,encoding:$('#encoding').disabled?'auto':$('#encoding').value,categories,instruction,profile:PROFILE};
  try{
    if(lite.batchBooks){
      const entries=[];
      for(const book of lite.batchBooks){
        try{const job=await api('/api/clean/prepare',{id:book.id,version_id:'',...options,encoding:/\.epub$/i.test(book.name)?'auto':options.encoding});
          await api('/api/clean/start',{job_id:job.id,queue:true});entries.push({id:job.id,title:job.title,book_id:job.book_id});}
        catch(error){if(error instanceof OfflineError)throw error;entries.push({title:book.title,book_id:book.id,error:error.message});}
      }
      lite.batchBooks=null;$('#batch-chosen').hidden=true;$('#search-area').hidden=false;openBatch(entries);
    }else{
      const job=await api('/api/clean/prepare',{id:lite.book.id,version_id:$('#version').value,...options});
      store.set(TASK_KEY,JSON.stringify({id:job.id,title:job.title}));openJob(job);
    }
  }catch(error){setupError(error.message);}finally{button.disabled=false;}
};
function setupError(message){$('#setup-error').textContent=message;$('#setup-error').hidden=false;}

// Step 3: run, review and save.
function openJob(job){clearTimeout(lite.timer);clearTimeout(lite.batchTimer);const inBatch=!!lite.batch?.some(entry=>entry.id===job.id);$('#discard').hidden=inBatch;$('#review-back').hidden=!inBatch;lite.generation++;Object.assign(lite,{job,renderKey:'',open:new Set(),groupPages:{}});lite.selected.clear();lite.seenDefaults.clear();$('#label').value='';$('#resume').hidden=true;showStep('review');renderJob(job);}
function selectionCount(){$('#selection').textContent=`已选择 ${lite.selected.size} 项修改`;$('#save').textContent=`采用 ${lite.selected.size} 项，另存为新版本`;$('#save').disabled=!lite.selected.size;}
function renderJob(job){
  lite.job=job;
  job.defaults.forEach(id=>{if(!lite.seenDefaults.has(id)){lite.selected.add(id);lite.seenDefaults.add(id);}});
  const terminal=job.transport==='terminal',tasks=(job.options?.categories||[]).map(c=>names[c]).join('、');
  $('#job-summary').textContent=`《${job.title}》 · ${tasks} · 正文 ${number(job.characters)} 字 · ${number(job.chunks)} 个片段，含上下文约 ${number(job.send_characters)} 字 · 编码 ${job.encoding} · ${job.model}，${terminal?'通过本机 Claude Code 订阅':'发送到 '+job.destination}`;
  $('#job-instruction').hidden=!job.options?.instruction;$('#job-instruction').textContent='你的要求：'+(job.options?.instruction||'');
  const status={prepared:'计划已就绪，还没有发送任何内容。',queued:`排队中，前面还有 ${job.queue_position} 本。`,running:'Claude 正在检查…',ready:job.chunks?'检查完成，请逐项确认。':'没有发现明显标记。可以放弃这个任务，改用「逐段检查全文」再试。',paused:'已停止后续请求，可以继续，或先采用已有建议。',failed:'检查中断，可以重试，或先采用已有建议。'};
  const inputTokens=(job.usage.input_tokens||0)+(job.usage.cache_creation_input_tokens||0)+(job.usage.cache_read_input_tokens||0);
  $('#job-status').textContent=`${status[job.status]||job.status} ${job.progress} / ${job.chunks} · ${job.patch_total} 项建议${!job.can_start&&job.chunks?' · 还没有配置 Claude，请先点右上角的「Claude 设置」。':''}${job.rejected?` · ${job.rejected} 项无效或重叠的建议已排除`:''}${inputTokens?` · 用量 ${number(inputTokens)} 输入 / ${number(job.usage.output_tokens)} 输出 tokens`:''}`;
  $('#progress').max=Math.max(1,job.chunks);$('#progress').value=job.progress;
  $('#job-error').hidden=!job.error;$('#job-error').textContent=job.error;
  $('#start').hidden=['running','ready','queued'].includes(job.status);$('#start').disabled=!job.can_start&&job.chunks>0;$('#start').textContent=job.chunks===0?'无需发送，完成检查':job.status==='prepared'?(terminal?'使用本机 Claude 订阅开始检查':'发送片段给 Claude，开始检查'):'继续发送未完成的片段';
  $('#stop').hidden=!['running','queued'].includes(job.status);$('#stop').disabled=job.cancel&&job.status==='running';$('#stop').textContent=job.status==='queued'?'取消排队':job.cancel?'等待当前请求结束…':'停止后续请求';
  $('#save-panel').hidden=!['ready','paused','failed'].includes(job.status)||!job.patch_total;
  const renderKey=JSON.stringify([job.id,job.patch_total,job.status]);
  if(renderKey!==lite.renderKey){lite.renderKey=renderKey;renderGroups(job);}
  syncGroups();selectionCount();
}
// Suggestions are grouped by kind: a count per kind, one checkbox for the whole kind, details only when opened.
const groupOrder=['author_note','garbled','censored','custom'];
const groupHints={author_note:'删除作者有话说、求票和站点广告。',garbled:'修复乱码，默认不勾选，建议展开看看。',censored:'根据上下文推测屏蔽字，不能保证是原文，默认不勾选，建议逐条核对。',custom:'按你的要求修改。'};
function renderGroups(job){
  // A Windows backend that has not updated yet sends no groups: list its first suggestions one by one instead.
  if(!job.groups){$('#patches').innerHTML=(job.patch_total?'<p class="notice">Windows 电脑上的书屿还没更新到最新版本，暂时只逐条显示前几项建议；更新后会按类别合并显示。</p>':'')+job.patches.map(patchHTML).join('');return;}
  const kinds=groupOrder.filter(kind=>job.groups?.[kind]?.total);
  $('#patches').innerHTML=kinds.map(kind=>`<section class="group" data-group="${kind}"><div class="group-head"><input type="checkbox" data-group-check="${kind}" aria-label="全选${names[kind]}"><button class="group-toggle" type="button" data-group-toggle="${kind}" aria-expanded="${lite.open.has(kind)}"><strong>${names[kind]}</strong><span data-group-count="${kind}"></span><span class="caret" aria-hidden="true">▸</span></button></div><p class="hint">${groupHints[kind]}</p><div class="group-body" data-group-body="${kind}" ${lite.open.has(kind)?'':'hidden'}></div></section>`).join('')||'<p class="hint">暂时没有修改建议。</p>';
  kinds.filter(kind=>lite.open.has(kind)).forEach(kind=>loadGroup(kind));
}
function syncGroups(){
  const groups=lite.job?.groups||{};
  for(const kind of Object.keys(groups)){
    const box=$(`[data-group-check="${kind}"]`);if(!box)continue;
    const ids=groups[kind].ids,chosen=ids.filter(id=>lite.selected.has(id)).length;
    box.checked=chosen===ids.length;box.indeterminate=chosen>0&&chosen<ids.length;
    $(`[data-group-count="${kind}"]`).textContent=`${ids.length} 项 · 已选 ${chosen} 项`;
  }
  $$('[data-patch]').forEach(el=>el.checked=lite.selected.has(el.dataset.patch));
}
function patchHTML(p){return `<article class="patch"><label class="patch-heading"><input type="checkbox" data-patch="${p.id}" ${lite.selected.has(p.id)?'checked':''}><span>${Math.round(p.confidence*100)}% 置信度</span></label><p class="hint">${escapeHTML(p.reason)}</p><div class="patch-compare"><div><span>原文</span><pre>${escapeHTML(p.before)}</pre></div><div><span>${p.after?'修改为':'建议删除'}</span><pre>${p.after?escapeHTML(p.after):'（删除这段内容）'}</pre></div></div></article>`;}
async function loadGroup(kind){
  const generation=lite.generation,page=lite.groupPages[kind]||1,body=$(`[data-group-body="${kind}"]`);if(!body)return;
  if(!body.children.length)body.innerHTML='<p class="hint">正在读取…</p>';
  try{const data=await api('/api/clean/job?'+new URLSearchParams({job_id:lite.job.id,page,category:kind}));
    if(generation!==lite.generation||!lite.open.has(kind))return;lite.groupPages[kind]=data.page;
    $(`[data-group-body="${kind}"]`).innerHTML=data.patches.map(patchHTML).join('')+(data.pages>1?`<div class="pages"><button class="button" type="button" data-group-page="${kind}" data-to="${data.page-1}" ${data.page<=1?'disabled':''}>上一页</button><span>${data.page} / ${data.pages}</span><button class="button" type="button" data-group-page="${kind}" data-to="${data.page+1}" ${data.page>=data.pages?'disabled':''}>下一页</button></div>`:'');
    syncGroups();}
  catch(error){onError(error);}
}
async function refreshJob(){
  clearTimeout(lite.timer);if(!lite.job)return;const generation=lite.generation;
  try{const job=await api('/api/clean/job?'+new URLSearchParams({job_id:lite.job.id}));if(generation!==lite.generation)return;renderJob(job);if(['running','queued'].includes(job.status))lite.timer=setTimeout(refreshJob,1800);}
  catch(error){if(generation===lite.generation){onError(error);lite.timer=setTimeout(refreshJob,4000);}}
}
async function jobAction(action){
  const button=$('#'+action);button.disabled=true;
  // Another job may be running, from Lite or the full page; this one then waits its turn.
  try{await api('/api/clean/'+(action==='stop'?'cancel':action),{job_id:lite.job.id,queue:true});await refreshJob();}catch(error){onError(error);button.disabled=false;}
}
$('#start').onclick=()=>jobAction('start');$('#stop').onclick=()=>jobAction('stop');
$('#patches').onchange=event=>{
  const kind=event.target.dataset.groupCheck,id=event.target.dataset.patch;
  if(kind)for(const each of lite.job.groups[kind].ids)event.target.checked?lite.selected.add(each):lite.selected.delete(each);
  else if(id)event.target.checked?lite.selected.add(id):lite.selected.delete(id);
  syncGroups();selectionCount();
};
$('#patches').onclick=event=>{
  const toggle=event.target.closest('[data-group-toggle]'),pager=event.target.closest('[data-group-page]');
  if(toggle){const kind=toggle.dataset.groupToggle,open=!lite.open.has(kind);open?lite.open.add(kind):lite.open.delete(kind);
    toggle.setAttribute('aria-expanded',String(open));$(`[data-group-body="${kind}"]`).hidden=!open;if(open)loadGroup(kind);}
  if(pager&&!pager.disabled){lite.groupPages[pager.dataset.groupPage]=Number(pager.dataset.to);loadGroup(pager.dataset.groupPage);}
};
$('#select-none').onclick=()=>{lite.selected.clear();syncGroups();selectionCount();};
$('#discard').onclick=async()=>{
  const job=lite.job;clearTimeout(lite.timer);lite.generation++;lite.job=null;store.set(TASK_KEY,'');
  if(job?.status==='running')try{await api('/api/clean/cancel',{job_id:job.id});}catch{}
  if(lite.book&&lite.book.id===job?.book_id){$('#search-area').hidden=true;$('#chosen').hidden=false;showStep('setup');}else{lite.book=null;$('#chosen').hidden=true;$('#search-area').hidden=false;showStep();}
};
$('#save').onclick=async()=>{
  const button=$('#save');button.disabled=true;
  try{const version=await api('/api/clean/save',{job_id:lite.job.id,selected:[...lite.selected],label:$('#label').value});
    lite.version=version;store.set(TASK_KEY,'');clearTimeout(lite.timer);$('#share').textContent='分享到其他应用';
    const entry=lite.batch?.find(entry=>entry.id===lite.job.id);if(entry){entry.saved=version.label;store.set(BATCH_KEY,JSON.stringify(lite.batch));}
    $('#back-to-batch').hidden=!entry;
    $('#done-message').textContent=`《${lite.job.title}》已另存为「${version.label}」，采用了 ${version.edit_count} 项修改。原文件和已有版本都保留着。`;showStep('done');}
  catch(error){onError(error);button.disabled=false;}
};
async function download(bookId,versionId){
  try{const result=await api('/api/download/ticket',{id:bookId,version_id:versionId});toast('正在下载…');location.href=serverBase()+result.url;}catch(error){onError(error);}
}
$('#download').onclick=()=>download(lite.version.book_id,lite.version.id);
$('#weread').onclick=()=>sendToWeRead(lite.version.book_id,lite.version.id,$('#weread'));
$('#share').onclick=()=>shareFile(lite.version.book_id,lite.version.id,$('#share'));
// Step 1 also offers the chosen version as it is, without processing.
$('#direct-download').onclick=()=>download(lite.book.id,$('#version').value);
$('#direct-weread').onclick=()=>sendToWeRead(lite.book.id,$('#version').value,$('#direct-weread'));
$('#direct-share').onclick=()=>shareFile(lite.book.id,$('#version').value,$('#direct-share'));
$('#version-list').onclick=event=>{
  const button=event.target.closest('[data-download]'),send=event.target.closest('[data-weread]');
  if(button)download(lite.book.id,button.dataset.download);if(send)sendToWeRead(lite.book.id,send.dataset.weread,send);
};

// Phones: hand the file to another app, such as the WeRead app, through the system share sheet.
const prepared={};
async function shareFile(bookId,versionId,button){
  const key=bookId+'/'+versionId,label='分享到其他应用';
  try{
    let file=prepared[key];
    if(!file){
      button.disabled=true;button.textContent='正在准备文件…';
      const {url}=await api('/api/download/ticket',{id:bookId,version_id:versionId});
      const response=await reach(url);if(!response.ok)throw new Error('文件没能取回，请重试。');
      const name=decodeURIComponent((response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/)||[])[1]||'小说.txt');
      file=prepared[key]=new File([await response.arrayBuffer()],name,{type:/\.epub$/i.test(name)?'application/epub+zip':'text/plain'});
    }
    if(!navigator.canShare({files:[file]}))throw new Error('这台设备不能分享这种文件，请先下载到这台设备。');
    await navigator.share({files:[file]});button.textContent=label;
  }catch(error){
    // Waiting for the file can use up the tap that allows sharing; the next tap shares at once.
    if(error.name==='NotAllowedError'&&prepared[key])button.textContent='文件已准备好，再点一次分享';
    else{button.textContent=label;if(error.name!=='AbortError')onError(error);}
  }finally{button.disabled=false;}
}

$('#continue').onclick=()=>{lite.job=null;lite.batchBooks=null;chooseBook(lite.version.book_id,lite.version.id);};
$('#back-to-batch').onclick=$('#review-back').onclick=()=>{clearTimeout(lite.timer);lite.generation++;lite.job=null;openBatch(lite.batch);};
$('#again').onclick=()=>{lite.book=null;lite.job=null;lite.version=null;$('#chosen').hidden=true;$('#search-area').hidden=false;$('#search').value='';showStep();search();};

// Step 3 for a batch: one row per book, each reviewed and saved on its own.
const batchStates={prepared:'等待开始',queued:'排队中',running:'Claude 正在检查',ready:'检查完成',paused:'已停止',failed:'检查中断'};
function openBatch(entries){lite.batch=entries;store.set(BATCH_KEY,JSON.stringify(entries));$('#resume').hidden=true;showStep('batch');refreshBatch();}
async function refreshBatch(){
  clearTimeout(lite.batchTimer);const entries=lite.batch;if(!entries||$('#batch').hidden)return;
  try{
    for(const entry of entries)if(entry.id)Object.assign(entry,{job:await api('/api/clean/job?'+new URLSearchParams({job_id:entry.id}))});
    if(entries!==lite.batch)return;
    $('#batch-list').innerHTML=entries.map((entry,i)=>{
      const job=entry.job;if(!job)return `<li><strong>《${escapeHTML(entry.title)}》</strong><span class="error-text">${escapeHTML(entry.error||'没有开始')}</span></li>`;
      const state=job.status==='queued'?`排队中，前面还有 ${job.queue_position} 本`:batchStates[job.status]||job.status;
      const done=['ready','paused','failed'].includes(job.status);
      return `<li><strong>《${escapeHTML(entry.title)}》</strong><span>${state} · ${job.progress} / ${job.chunks} 个片段 · ${job.patch_total} 项建议${entry.saved?` · 已另存为「${escapeHTML(entry.saved)}」`:''}${job.error?' · '+escapeHTML(job.error):''}</span><progress max="${Math.max(1,job.chunks)}" value="${job.progress}"></progress>${done&&job.patch_total?`<button class="button${entry.saved?'':' primary'}" type="button" data-review="${i}">${entry.saved?'再看一次':'确认建议'}</button>`:''}</li>`;
    }).join('');
    const jobs=entries.map(entry=>entry.job).filter(Boolean),busy=jobs.some(job=>['running','queued'].includes(job.status));
    const usage=jobs.reduce((sum,job)=>sum+(job.usage.input_tokens||0)+(job.usage.cache_creation_input_tokens||0)+(job.usage.cache_read_input_tokens||0),0);
    $('#batch-summary').textContent=`${jobs.filter(job=>['ready','paused','failed'].includes(job.status)).length} / ${entries.length} 本已处理完${usage?` · 用量 ${number(usage)} 输入 / ${number(jobs.reduce((sum,job)=>sum+(job.usage.output_tokens||0),0))} 输出 tokens`:''}`;
    $('#batch-stop').hidden=!busy;
    if(busy)lite.batchTimer=setTimeout(refreshBatch,2500);
  }catch(error){onError(error);lite.batchTimer=setTimeout(refreshBatch,5000);}
}
$('#batch-list').onclick=event=>{const button=event.target.closest('[data-review]');if(button)openJob(lite.batch[button.dataset.review].job);};
$('#batch-stop').onclick=async()=>{
  for(const entry of lite.batch||[])if(['running','queued'].includes(entry.job?.status))try{await api('/api/clean/cancel',{job_id:entry.id});}catch(error){onError(error);}
  refreshBatch();
};
$('#batch-close').onclick=()=>{clearTimeout(lite.batchTimer);lite.batch=null;store.set(BATCH_KEY,'');$('#search').value='';showStep();search();};

// Lite's own Claude settings: API or the Windows machine's Claude Code subscription.
async function openAISettings(){
  try{const config=await api('/api/ai/config?'+new URLSearchParams({profile:PROFILE}));
    $('#ai-transport').value=config.transport;$('#ai-url').value=config.base_url;$('#ai-terminal-path').value=config.terminal_path;$('#ai-key').value='';$('#ai-clear-key').checked=false;
    $('#ai-inherited').textContent=config.inherited?'还没有单独设置，现在沿用完整版书屿的设置。保存后，Lite 和完整版就各用各的。':'Lite 使用自己的设置，和完整版书屿互不影响。';
    $('#ai-inherit').hidden=config.inherited;$('#ai-key-help').textContent=config.key_set?'已有可用的 Key；留空保持不变。':'请填写 Claude API Key，只保存在 Windows 电脑上。';
    $('#ai-result').textContent='';syncAITransport();if(!$('#ai-dialog').open)$('#ai-dialog').showModal();}
  catch(error){onError(error);}
}
function syncAITransport(){const terminal=$('#ai-transport').value==='terminal';$('#ai-api').hidden=terminal;$('#ai-terminal').hidden=!terminal;}
$('#ai-transport').onchange=syncAITransport;
async function saveAI(action){
  const buttons=$$('#ai-form button');buttons.forEach(b=>b.disabled=true);
  try{
    const data={profile:PROFILE,transport:$('#ai-transport').value,terminal_path:$('#ai-terminal-path').value};
    if(data.transport==='api')Object.assign(data,{base_url:$('#ai-url').value,api_key:$('#ai-key').value,clear_key:$('#ai-clear-key').checked});
    await api('/api/ai/config',data);
    if(action==='test'){$('#ai-result').textContent='正在用一条测试文字连接 Claude…';const result=await api('/api/ai/test',{profile:PROFILE});$('#ai-result').textContent=`连接成功 · ${result.model} · ${result.transport==='terminal'?'Claude Code 订阅':'API'}`;}
    else if(action==='status'){$('#ai-result').textContent='正在检测订阅登录…';const result=await api('/api/ai/terminal?'+new URLSearchParams({profile:PROFILE}));$('#ai-result').textContent=result.message;}
    else{$('#ai-dialog').close();toast('Lite 的 Claude 设置已保存');}
    if(lite.job)refreshJob();
  }catch(error){$('#ai-result').textContent=error.message;}finally{buttons.forEach(b=>b.disabled=false);}
}
$('#ai-open').onclick=openAISettings;$('#ai-close').onclick=()=>$('#ai-dialog').close();
$('#ai-form').onsubmit=event=>{event.preventDefault();saveAI();};$('#ai-test').onclick=()=>saveAI('test');$('#ai-terminal-check').onclick=()=>saveAI('status');
$('#ai-inherit').onclick=async()=>{try{await api('/api/ai/config',{profile:PROFILE,inherit:true});toast('Lite 改回沿用完整版的设置');openAISettings();}catch(error){$('#ai-result').textContent=error.message;}};

// A task keeps running on the backend after the page closes, so offer to return to it.
async function offerResume(){
  let batch=null;try{batch=JSON.parse(store.get(BATCH_KEY)||'null');}catch{}
  if(batch?.length){
    $('#resume').textContent=`继续上次的一批（${batch.length} 本）`;$('#resume').hidden=false;$('#resume').onclick=()=>openBatch(batch);return;
  }
  let last=null;try{last=JSON.parse(store.get(TASK_KEY)||'null');}catch{}
  if(!last?.id)return;
  $('#resume').textContent=`继续上次的任务：《${last.title}》`;$('#resume').hidden=false;
  $('#resume').onclick=async()=>{try{const job=await api('/api/clean/job?'+new URLSearchParams({job_id:last.id}));openJob(job);if(job.status==='running')refreshJob();try{lite.book=await api('/api/book?'+new URLSearchParams({id:job.book_id}));}catch{}}catch(error){store.set(TASK_KEY,'');$('#resume').hidden=true;onError(error);}};
}

(async()=>{
  try{const response=await request('/api/session');if(!response.ok)throw new Error();session=await response.json();}
  catch{return showOffline();}
  if(!session.authenticated){store.set('shuyu-token','');return showLogin();}
  started=true;signedIn();$('#app').hidden=false;$('#ai-open').hidden=false;$('#logout').hidden=session.local;syncSetup();search();offerResume();
  $('#share').hidden=$('#direct-share').hidden=typeof navigator.canShare!=='function';
})();
