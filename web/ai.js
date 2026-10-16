'use strict';
const cleaning={book:null,version:'',job:null,page:1,selected:new Set(),seenDefaults:new Set(),timer:null,generation:0,renderKey:''};
const categoryNames={author_note:'作者话 / 广告',garbled:'乱码修复',censored:'屏蔽字推测'};
async function aiSettings(){
  try{const config=await api('/api/ai/config');$('#ai-url').value=config.base_url;$('#ai-model').value=config.model;$('#ai-key').value='';$('#ai-clear-key').checked=false;$('#ai-key-help').textContent=config.key_set?'Key 已配置；留空保持不变。':'请填写 Claude API Key，只保存在本机。';$('#ai-test-result').textContent='';if(!$('#ai-settings-dialog').open)$('#ai-settings-dialog').showModal();}catch(error){onError(error);}
}
$('#ai-settings-button').onclick=aiSettings;$('#clean-api-settings').onclick=aiSettings;
async function saveAI(test=false){
  const buttons=$$('#ai-settings-form button');buttons.forEach(b=>b.disabled=true);
  try{
    await api('/api/ai/config',{base_url:$('#ai-url').value,api_key:$('#ai-key').value,clear_key:$('#ai-clear-key').checked});$('#ai-key').value='';$('#ai-clear-key').checked=false;$('#ai-key-help').textContent='设置已保存；Key 留空保持不变。';
    if(test){$('#ai-test-result').textContent='正在使用一条测试文字连接接口…';const result=await api('/api/ai/test',{});$('#ai-test-result').textContent=`连接成功 · ${result.model}`;}else{$('#ai-settings-dialog').close();toast('Claude 设置已保存');}
    if(cleaning.job)await refreshClean();
  }catch(error){$('#ai-test-result').textContent=error.message;onError(error);}finally{buttons.forEach(b=>b.disabled=false);}
}
$('#ai-settings-form').onsubmit=event=>{event.preventDefault();saveAI();};$('#ai-test').onclick=()=>saveAI(true);
function showClean(){if(!$('#clean-dialog').open)$('#clean-dialog').showModal();}
function cleanError(error){$('#clean-operation-error').textContent=error.message;$('#clean-operation-error').hidden=false;onError(error);}
function openCleanup(book,version=''){
  clearTimeout(cleaning.timer);Object.assign(cleaning,{book,version,job:null,page:1,renderKey:'',generation:cleaning.generation+1});cleaning.selected.clear();cleaning.seenDefaults.clear();
  $('#clean-title').textContent=`清理《${book.title}》`;$('#clean-operation-error').hidden=true;$('#clean-options').hidden=false;$('#clean-review').hidden=true;$('#clean-label').value='';$('#clean-patches').innerHTML='';$('#clean-encoding').disabled=['.epub','.eqpub'].includes(book.ext);showClean();
}
async function resumeCleanup(id){
  clearTimeout(cleaning.timer);cleaning.generation++;cleaning.job={id};cleaning.page=1;cleaning.renderKey='';cleaning.selected.clear();cleaning.seenDefaults.clear();$('#clean-label').value='';$('#clean-operation-error').hidden=true;$('#clean-options').hidden=true;$('#clean-review').hidden=false;showClean();await refreshClean();
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
  $('#clean-summary').textContent=`${job.model} · 正文 ${number(job.characters)} 字 · ${number(job.chunks)} 个片段，含上下文约 ${number(job.send_characters)} 字 · 编码 ${job.encoding}。发送到 ${job.destination}`;
  const status={prepared:'本地计划已就绪；尚未发送小说内容。',running:'Claude 正在检查…',ready:job.chunks?'检查完成，请逐项确认建议。':'没有发现明显标记；可以关闭，或新建任务选择全文检查。',paused:'已停止后续请求，可继续检查或采用已有建议。',failed:'检查中断，可重试当前片段或采用已有建议。'};
  $('#clean-status').textContent=`${status[job.status]||job.status} ${job.progress} / ${job.chunks} · ${job.patch_total} 项建议${!job.key_set&&job.chunks?' · 请先在 Claude AI 设置中填写 API Key。':''}${job.rejected?` · ${job.rejected} 项无效或重叠建议已排除`:''}${job.usage.input_tokens?` · 用量 ${number(job.usage.input_tokens)} 输入 / ${number(job.usage.output_tokens)} 输出 tokens`:''}`;
  $('#clean-progress').max=Math.max(1,job.chunks);$('#clean-progress').value=job.progress;
  $('#clean-error').hidden=!job.error;$('#clean-error').textContent=job.error;
  $('#clean-start').hidden=['running','ready'].includes(job.status);$('#clean-start').disabled=!job.key_set&&job.chunks>0;$('#clean-start').textContent=job.chunks===0?'无需发送，完成检查':job.status==='prepared'?'发送片段给 Claude，开始检查':'继续发送未完成片段';
  $('#clean-stop').hidden=job.status!=='running';$('#clean-stop').disabled=job.cancel;$('#clean-stop').textContent=job.cancel?'等待当前请求结束…':'停止后续请求';
  $('#clean-save-panel').hidden=!['ready','paused','failed'].includes(job.status);
  const renderKey=JSON.stringify([job.id,job.page,job.patch_total,job.status]);
  if(renderKey!==cleaning.renderKey){
    cleaning.renderKey=renderKey;
    $('#clean-patches').innerHTML=job.patches.map(p=>`<article class="clean-patch"><label class="patch-heading"><input type="checkbox" data-patch="${p.id}" ${cleaning.selected.has(p.id)?'checked':''}><strong>${categoryNames[p.category]}</strong><span>${Math.round(p.confidence*100)}% 置信度</span></label><p class="field-help">${escapeHTML(p.reason)}${p.category==='censored'?' · 仅为推测，请核对。':''}</p><div class="patch-comparison"><div><span>原文</span><pre>${escapeHTML(p.before)}</pre></div><div><span>${p.after?'修改建议':'建议删除'}</span><pre>${p.after?escapeHTML(p.after):'（删除这段内容）'}</pre></div></div></article>`).join('')||'<p class="field-help">暂时没有修改建议。</p>';
    $('#clean-pages').innerHTML=job.pages>1?`<button data-clean-page="${job.page-1}" ${job.page<=1?'disabled':''}>上一页</button><span>${job.page} / ${job.pages}</span><button data-clean-page="${job.page+1}" ${job.page>=job.pages?'disabled':''}>下一页</button>`:'';
  }
  selectionCount();
}
async function refreshClean(){
  clearTimeout(cleaning.timer);if(!cleaning.job)return;const generation=cleaning.generation,id=cleaning.job.id;
  try{const job=await api('/api/clean/job?'+new URLSearchParams({job_id:id,page:cleaning.page}));if(generation!==cleaning.generation)return;$('#clean-operation-error').hidden=true;renderClean(job);if(job.status==='running'&&$('#clean-dialog').open)cleaning.timer=setTimeout(refreshClean,1800);}catch(error){if(generation===cleaning.generation){cleanError(error);if($('#clean-dialog').open)cleaning.timer=setTimeout(refreshClean,4000);}}
}
async function cleanAction(action){
  const button=$('#clean-'+action);button.disabled=true;$('#clean-operation-error').hidden=true;
  try{await api('/api/clean/'+(action==='stop'?'cancel':action),{job_id:cleaning.job.id});await refreshClean();}catch(error){cleanError(error);}finally{button.disabled=action==='stop'?!!cleaning.job?.cancel:!cleaning.job?.key_set&&cleaning.job?.chunks>0;}
}
$('#clean-start').onclick=()=>cleanAction('start');$('#clean-stop').onclick=()=>cleanAction('stop');
$('#clean-patches').onchange=event=>{const id=event.target.dataset.patch;if(!id)return;if(event.target.checked)cleaning.selected.add(id);else cleaning.selected.delete(id);selectionCount();};
$('#clean-none').onclick=()=>{cleaning.selected.clear();$$('[data-patch]').forEach(el=>el.checked=false);selectionCount();};
$('#clean-pages').onclick=event=>{const button=event.target.closest('[data-clean-page]');if(button&&!button.disabled){cleaning.page=Number(button.dataset.cleanPage);refreshClean();}};
$('#clean-save').onclick=async()=>{
  const button=$('#clean-save');button.disabled=true;$('#clean-operation-error').hidden=true;
  try{const version=await api('/api/clean/save',{job_id:cleaning.job.id,selected:[...cleaning.selected],label:$('#clean-label').value});$('#clean-dialog').close();await loadBooks(false);await openDetail(version.book_id,version.id);toast(`已新建「${version.label}」，原文件和已有版本均保留`);}catch(error){cleanError(error);}finally{button.disabled=false;}
};
$('#clean-dialog').addEventListener('close',()=>clearTimeout(cleaning.timer));
