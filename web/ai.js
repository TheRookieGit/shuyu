'use strict';
const cleaning={book:null,version:'',job:null,selected:new Set(),seenDefaults:new Set(),timer:null,generation:0,renderKey:'',open:new Set(),groupPages:{}};
const categoryNames={author_note:'作者话 / 广告',garbled:'乱码修复',censored:'屏蔽字推测',custom:'自定义要求'};
async function aiSettings(){
  try{const config=await api('/api/ai/config');$('#ai-transport').value=config.transport;$('#ai-terminal-path').value=config.terminal_path;$('#ai-url').value=config.base_url;$('#ai-model').value=config.model;$('#ai-key').value='';$('#ai-clear-key').checked=false;$('#ai-key-help').textContent=config.key_set?'Key 已配置；留空保持不变。':'请填写 Claude API Key，只保存在本机。';$('#ai-test-result').textContent='';$('#ai-terminal-status').textContent='可检测本机安装和订阅登录状态，检测本身不发送小说内容。';showAITransport();if(!$('#ai-settings-dialog').open)$('#ai-settings-dialog').showModal();}catch(error){onError(error);}
}
$('#ai-settings-button').onclick=aiSettings;$('#clean-api-settings').onclick=aiSettings;
function showAITransport(){const terminal=$('#ai-transport').value==='terminal';$('#ai-api-fields').hidden=terminal;$('#ai-terminal-fields').hidden=!terminal;$$('input', $('#ai-api-fields')).forEach(el=>el.disabled=terminal);$('#ai-test-result').textContent='';}
$('#ai-transport').onchange=showAITransport;
async function saveAI(action='save'){
  const buttons=$$('#ai-settings-form button');buttons.forEach(b=>b.disabled=true);
  try{
    const data={transport:$('#ai-transport').value,terminal_path:$('#ai-terminal-path').value};
    if(data.transport==='api')Object.assign(data,{base_url:$('#ai-url').value,api_key:$('#ai-key').value,clear_key:$('#ai-clear-key').checked});
    await api('/api/ai/config',data);$('#ai-key').value='';$('#ai-clear-key').checked=false;$('#ai-key-help').textContent='设置已保存；Key 留空保持不变。';
    if(action==='status'){
      $('#ai-terminal-status').textContent='正在检测 Claude 安装与订阅登录…';const result=await api('/api/ai/terminal');$('#ai-terminal-status').textContent=result.message+(result.path?' 程序位置：'+result.path:'');
    }else if(action==='test'){
      $('#ai-test-result').textContent='正在使用一条测试文字连接 Claude…';const result=await api('/api/ai/test',{});$('#ai-test-result').textContent=`连接成功 · ${result.model} · ${result.transport==='terminal'?'本机命令行 / 订阅登录':'API'}`;
    }else{$('#ai-settings-dialog').close();toast('Claude 设置已保存');}
    if(cleaning.job)await refreshClean();
  }catch(error){$('#ai-test-result').textContent=error.message;onError(error);}finally{buttons.forEach(b=>b.disabled=false);}
}
$('#ai-settings-form').onsubmit=event=>{event.preventDefault();saveAI();};$('#ai-test').onclick=()=>saveAI('test');$('#ai-terminal-check').onclick=()=>saveAI('status');
function showClean(){if(!$('#clean-dialog').open)$('#clean-dialog').showModal();}
function cleanError(error){$('#clean-operation-error').textContent=error.message;$('#clean-operation-error').hidden=false;onError(error);}
function openCleanup(book,version=''){
  clearTimeout(cleaning.timer);Object.assign(cleaning,{book,version,job:null,renderKey:'',open:new Set(),groupPages:{},generation:cleaning.generation+1});cleaning.selected.clear();cleaning.seenDefaults.clear();
  $('#clean-title').textContent=`清理《${book.title}》`;$('#clean-operation-error').hidden=true;$('#clean-options').hidden=false;$('#clean-review').hidden=true;$('#clean-label').value='';$('#clean-patches').innerHTML='';$('#clean-encoding').disabled=['.epub','.eqpub'].includes(book.ext);showClean();
}
async function resumeCleanup(id){
  clearTimeout(cleaning.timer);cleaning.generation++;cleaning.job={id};Object.assign(cleaning,{renderKey:'',open:new Set(),groupPages:{}});cleaning.selected.clear();cleaning.seenDefaults.clear();$('#clean-label').value='';$('#clean-operation-error').hidden=true;$('#clean-options').hidden=true;$('#clean-review').hidden=false;showClean();await refreshClean();
}
$('#clean-options').onsubmit=async event=>{
  event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;$('#clean-operation-error').hidden=true;const generation=cleaning.generation;
  try{const job=await api('/api/clean/prepare',{id:cleaning.book.id,version_id:cleaning.version,mode:$('#clean-mode').value,encoding:$('#clean-encoding').disabled?'auto':$('#clean-encoding').value,categories:$$('#clean-options input[name="category"]:checked').map(el=>el.value)});if(generation!==cleaning.generation)return;cleaning.job=job;sessionStorage.setItem('shuyu-clean-task',JSON.stringify({id:job.id,book_id:job.book_id}));$('#clean-options').hidden=true;$('#clean-review').hidden=false;renderClean(job);}catch(error){cleanError(error);}finally{button.disabled=false;}
};
function selectionCount(){$('#clean-selection').textContent=`已选择 ${cleaning.selected.size} 项修改`;$('#clean-save').textContent=`采用 ${cleaning.selected.size} 项，另存为新版本`;}
function renderClean(job){
  cleaning.job=job;$('#clean-title').textContent=`清理《${job.title}》`;
  if($('#detail-content').dataset.bookId===job.book_id&&$('#resume-clean')){$('#resume-clean').hidden=false;$('#resume-clean').onclick=()=>resumeCleanup(job.id);}
  job.defaults.forEach(id=>{if(!cleaning.seenDefaults.has(id)){cleaning.selected.add(id);cleaning.seenDefaults.add(id);}});
  const terminal=job.transport==='terminal';
  $('#clean-summary').textContent=`${job.model} · ${terminal?'本机命令行 / 订阅登录':'API'} · 正文 ${number(job.characters)} 字 · ${number(job.chunks)} 个片段，含上下文约 ${number(job.send_characters)} 字 · 编码 ${job.encoding}。${terminal?'通过本机 Claude Code 发送到 Claude 服务，占用订阅额度。':'发送到 '+job.destination}`;
  const status={prepared:'本地计划已就绪；尚未发送小说内容。',running:'Claude 正在检查…',ready:job.chunks?'检查完成，请逐项确认建议。':'没有发现明显标记；可以关闭，或新建任务选择全文检查。',paused:'已停止后续请求，可继续检查或采用已有建议。',failed:'检查中断，可重试当前片段或采用已有建议。'};
  const inputTokens=(job.usage.input_tokens||0)+(job.usage.cache_creation_input_tokens||0)+(job.usage.cache_read_input_tokens||0);
  $('#clean-status').textContent=`${status[job.status]||job.status} ${job.progress} / ${job.chunks} · ${job.patch_total} 项建议${!job.can_start&&job.chunks?(terminal?' · 请先安装或指定 Claude Code。':' · 请先在 Claude AI 设置中填写 API Key。'):''}${job.rejected?` · ${job.rejected} 项无效或重叠建议已排除`:''}${inputTokens?` · 用量 ${number(inputTokens)} 输入 / ${number(job.usage.output_tokens)} 输出 tokens`:''}`;
  $('#clean-progress').max=Math.max(1,job.chunks);$('#clean-progress').value=job.progress;
  $('#clean-error').hidden=!job.error;$('#clean-error').textContent=job.error;
  $('#clean-start').hidden=['running','ready'].includes(job.status);$('#clean-start').disabled=!job.can_start&&job.chunks>0;$('#clean-start').textContent=job.chunks===0?'无需发送，完成检查':job.status==='prepared'?(terminal?'使用本机 Claude 订阅开始检查':'发送片段给 Claude，开始检查'):'继续发送未完成片段';
  $('#clean-stop').hidden=job.status!=='running';$('#clean-stop').disabled=job.cancel;$('#clean-stop').textContent=job.cancel?'等待当前请求结束…':'停止后续请求';
  $('#clean-save-panel').hidden=!['ready','paused','failed'].includes(job.status);
  const renderKey=JSON.stringify([job.id,job.patch_total,job.status]);
  if(renderKey!==cleaning.renderKey){cleaning.renderKey=renderKey;renderCleanGroups(job);}
  syncCleanGroups();selectionCount();
}
// Suggestions are grouped by kind: a count per kind, one checkbox for the whole kind, details only when opened.
const cleanGroupOrder=['author_note','garbled','censored','custom'];
const cleanGroupHints={author_note:'删除作者有话说、求票和站点广告。',garbled:'修复乱码，默认不勾选，建议展开看看。',censored:'根据上下文推测屏蔽字，不能保证是原文，默认不勾选，建议逐条核对。',custom:'按你的要求修改。'};
function cleanPatchHTML(p){return `<article class="clean-patch"><label class="patch-heading"><input type="checkbox" data-patch="${p.id}" ${cleaning.selected.has(p.id)?'checked':''}><strong>${categoryNames[p.category]||p.category}</strong><span>${Math.round(p.confidence*100)}% 置信度</span></label><p class="field-help">${escapeHTML(p.reason)}${p.category==='censored'?' · 仅为推测，请核对。':''}</p><div class="patch-comparison"><div><span>原文</span><pre>${escapeHTML(p.before)}</pre></div><div><span>${p.after?'修改建议':'建议删除'}</span><pre>${p.after?escapeHTML(p.after):'（删除这段内容）'}</pre></div></div></article>`;}
function renderCleanGroups(job){
  $('#clean-pages').innerHTML='';
  // A Windows backend that has not updated yet sends no groups: list its first suggestions one by one instead.
  if(!job.groups){$('#clean-patches').innerHTML=job.patches.map(cleanPatchHTML).join('')||'<p class="field-help">暂时没有修改建议。</p>';return;}
  const kinds=cleanGroupOrder.filter(kind=>job.groups[kind]?.total).concat(Object.keys(job.groups).filter(kind=>!cleanGroupOrder.includes(kind)));
  $('#clean-patches').innerHTML=kinds.map(kind=>`<section class="clean-group" data-group="${kind}"><div class="clean-group-head"><input type="checkbox" data-group-check="${kind}" aria-label="全选${categoryNames[kind]||kind}"><button class="clean-group-toggle" type="button" data-group-toggle="${kind}" aria-expanded="${cleaning.open.has(kind)}"><strong>${categoryNames[kind]||kind}</strong><span data-group-count="${kind}"></span><span class="caret" aria-hidden="true">▸</span></button></div><p class="field-help">${cleanGroupHints[kind]||''}</p><div class="clean-group-body" data-group-body="${kind}" ${cleaning.open.has(kind)?'':'hidden'}></div></section>`).join('')||'<p class="field-help">暂时没有修改建议。</p>';
  kinds.filter(kind=>cleaning.open.has(kind)).forEach(loadCleanGroup);
}
function syncCleanGroups(){
  const groups=cleaning.job?.groups||{};
  for(const kind of Object.keys(groups)){
    const box=$(`[data-group-check="${kind}"]`);if(!box)continue;
    const ids=groups[kind].ids,chosen=ids.filter(id=>cleaning.selected.has(id)).length;
    box.checked=chosen===ids.length;box.indeterminate=chosen>0&&chosen<ids.length;
    $(`[data-group-count="${kind}"]`).textContent=`${ids.length} 项 · 已选 ${chosen} 项`;
  }
  $$('#clean-patches [data-patch]').forEach(el=>el.checked=cleaning.selected.has(el.dataset.patch));
}
async function loadCleanGroup(kind){
  const generation=cleaning.generation,page=cleaning.groupPages[kind]||1,body=$(`[data-group-body="${kind}"]`);if(!body)return;
  if(!body.children.length)body.innerHTML='<p class="field-help">正在读取…</p>';
  try{const data=await api('/api/clean/job?'+new URLSearchParams({job_id:cleaning.job.id,page,category:kind}));
    if(generation!==cleaning.generation||!cleaning.open.has(kind))return;cleaning.groupPages[kind]=data.page;
    $(`[data-group-body="${kind}"]`).innerHTML=data.patches.map(cleanPatchHTML).join('')+(data.pages>1?`<div class="pagination"><button type="button" data-group-page="${kind}" data-to="${data.page-1}" ${data.page<=1?'disabled':''}>上一页</button><span>${data.page} / ${data.pages}</span><button type="button" data-group-page="${kind}" data-to="${data.page+1}" ${data.page>=data.pages?'disabled':''}>下一页</button></div>`:'');
    syncCleanGroups();}
  catch(error){cleanError(error);}
}
async function refreshClean(){
  clearTimeout(cleaning.timer);if(!cleaning.job)return;const generation=cleaning.generation,id=cleaning.job.id;
  try{const job=await api('/api/clean/job?'+new URLSearchParams({job_id:id}));if(generation!==cleaning.generation)return;$('#clean-operation-error').hidden=true;renderClean(job);if(job.status==='running'&&$('#clean-dialog').open)cleaning.timer=setTimeout(refreshClean,1800);}catch(error){if(generation===cleaning.generation){cleanError(error);if($('#clean-dialog').open)cleaning.timer=setTimeout(refreshClean,4000);}}
}
async function cleanAction(action){
  const button=$('#clean-'+action);button.disabled=true;$('#clean-operation-error').hidden=true;
  try{await api('/api/clean/'+(action==='stop'?'cancel':action),{job_id:cleaning.job.id});await refreshClean();}catch(error){cleanError(error);}finally{button.disabled=action==='stop'?!!cleaning.job?.cancel:!cleaning.job?.can_start&&cleaning.job?.chunks>0;}
}
$('#clean-start').onclick=()=>cleanAction('start');$('#clean-stop').onclick=()=>cleanAction('stop');
$('#clean-patches').onchange=event=>{
  const kind=event.target.dataset.groupCheck,id=event.target.dataset.patch;
  if(kind)for(const each of cleaning.job.groups[kind].ids)event.target.checked?cleaning.selected.add(each):cleaning.selected.delete(each);
  else if(id)event.target.checked?cleaning.selected.add(id):cleaning.selected.delete(id);
  else return;
  syncCleanGroups();selectionCount();
};
$('#clean-patches').onclick=event=>{
  const toggle=event.target.closest('[data-group-toggle]'),pager=event.target.closest('[data-group-page]');
  if(toggle){const kind=toggle.dataset.groupToggle,open=!cleaning.open.has(kind);open?cleaning.open.add(kind):cleaning.open.delete(kind);
    toggle.setAttribute('aria-expanded',String(open));$(`[data-group-body="${kind}"]`).hidden=!open;if(open)loadCleanGroup(kind);}
  if(pager&&!pager.disabled){cleaning.groupPages[pager.dataset.groupPage]=Number(pager.dataset.to);loadCleanGroup(pager.dataset.groupPage);}
};
$('#clean-all').onclick=()=>{const groups=cleaning.job?.groups;if(groups)Object.values(groups).forEach(group=>group.ids.forEach(id=>cleaning.selected.add(id)));else $$('#clean-patches [data-patch]').forEach(el=>cleaning.selected.add(el.dataset.patch));syncCleanGroups();selectionCount();};
$('#clean-none').onclick=()=>{cleaning.selected.clear();syncCleanGroups();selectionCount();};
$('#clean-save').onclick=async()=>{
  const button=$('#clean-save');button.disabled=true;$('#clean-operation-error').hidden=true;
  try{const version=await api('/api/clean/save',{job_id:cleaning.job.id,selected:[...cleaning.selected],label:$('#clean-label').value});$('#clean-dialog').close();await loadBooks(false);await openDetail(version.book_id,version.id);toast(`已新建「${version.label}」，原文件和已有版本均保留`);}catch(error){cleanError(error);}finally{button.disabled=false;}
};
$('#clean-dialog').addEventListener('close',()=>clearTimeout(cleaning.timer));
const autoStates={queued:'等待',running:'正在清理',saved:'已生成清理版',review:'有建议待确认',clean:'无需清理',failed:'失败'};
async function renderAutoClean(){
  try{const data=await api('/api/auto-clean');const counts=data.counts;
    $('#auto-clean-enabled').checked=data.enabled;
    const parts=Object.entries(autoStates).filter(([key])=>counts[key]).map(([key,label])=>`${label} ${number(counts[key])}`);
    $('#auto-clean-status').textContent=(data.enabled?(parts.length?parts.join(' · '):'已开启，还没有新增小说。'):'已关闭，新增小说不会自动发送给 Claude。')+(data.blocked?' · '+data.blocked:'');
    $('#auto-clean-status').classList.toggle('error-text',!!data.blocked);
    $('#auto-clean-recent').innerHTML=data.recent.map(item=>`<button class="auto-clean-item" data-detail-id="${item.book_id}" type="button"><strong>${escapeHTML(item.title)}</strong><span>${escapeHTML(item.label)}${item.message?' · '+escapeHTML(item.message):''}</span></button>`).join('');
    $('#auto-clean-retry').hidden=!counts.failed;
  }catch(error){$('#auto-clean-status').textContent=error.message;}
}
$('#auto-clean-enabled').onchange=async event=>{try{await api('/api/auto-clean/config',{enabled:event.target.checked});await renderAutoClean();toast(event.target.checked?'已开启：之后新增的小说会自动清理':'已关闭新增小说自动清理');}catch(error){onError(error);}};
$('#auto-clean-retry').onclick=async()=>{try{await api('/api/auto-clean/retry',{});await renderAutoClean();toast('失败的书已重新排队');}catch(error){onError(error);}};
$('#auto-clean-recent').onclick=event=>{const item=event.target.closest('[data-detail-id]');if(item){$('#settings-dialog').close();openDetail(item.dataset.detailId);}};
function renderBookAutoClean(book){
  const value=book.auto_clean;if(!value)return;
  $('#version-info').insertAdjacentHTML('afterend',`<p class="auto-clean-note"><strong>自动清理 · ${escapeHTML(value.label)}</strong>${value.message?' · '+escapeHTML(value.message):''}${value.job_id&&value.status!=='running'?' <button id="auto-clean-review" class="text-button" type="button">查看清理任务</button>':''}</p>`);
  if($('#auto-clean-review'))$('#auto-clean-review').onclick=()=>{cleaning.book=book;resumeCleanup(value.job_id);};
}
