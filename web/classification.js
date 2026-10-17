'use strict';
const taxonomy={levels:['清水','肉文'],relationships:['BL','GL','GB','BG','大女主'],pairings:['1v1','np'],tags:['末日','ABO','兽世','古代言情','现代言情','星际','虫族','校园']};
let classificationPollBusy=false,classificationPrevious=-1,wordGeneration=0;
const filterGroups=[['第一分类','level',taxonomy.levels],['第二分类','relationship',taxonomy.relationships],['BG 子分类','pairing',taxonomy.pairings],['更新状态','publication',['连载','断更','完结']],['题材 tag','tag',taxonomy.tags]];
function filterGroupHTML(title,field,values){return `<fieldset class="filter-group"><legend>${title}</legend>${values.map((value,i)=>`<label class="filter-option"><input type="checkbox" data-filter="${field}" value="${value}" ${field==='kind'?`data-kind="${value}"`:''}><span class="filter-dot" style="--dot:${['#ba784d','#53827d','#9a7d99','#78904d','#64859e'][i%5]}"></span><span>${escapeHTML(value==='GB'?'GB 女操男':value==='大女主'?'大女主 · 无 CP':value==='np'?'np · 1v2 及以上':({text:'TXT 文本',epub:'EPUB',pdf:'PDF',comic:'漫画',other:'其他'}[value]||value))}</span><small data-facet="${field}" data-value="${value}">—</small></label>`).join('')}</fieldset>`;}
$('#filter-options').innerHTML=filterGroups.map(args=>filterGroupHTML(...args)).join('');
$('#kind-filters').innerHTML=filterGroupHTML('文件格式','kind',['text','epub','pdf','comic','other']);
renderFilters();
let facetRefreshBusy=false;
async function refreshFacets(){if(facetRefreshBusy)return;facetRefreshBusy=true;try{const data=await api('/api/facets');$$('[data-facet]').forEach(el=>el.textContent=number(data[el.dataset.facet]?.[el.dataset.value]||0));}catch(error){onError(error);}finally{facetRefreshBusy=false;}}
refreshFacets();
async function refreshClassification(){
  if(classificationPollBusy)return;classificationPollBusy=true;
  try{const data=await api('/api/classification');const done=data.statuses.classified||0,total=Object.values(data.statuses).reduce((a,b)=>a+b,0);
    $('#classification-summary').textContent=`分类完整 ${number(done)} / ${number(total)} · 已统计字数 ${number(data.counted)}${data.state.running?' · 本地整理进行中':''}`;
    $('#classification-progress').textContent=`${data.state.phase} · 本轮 ${number(data.state.done)} / ${number(data.state.total)} · ${number(data.state.errors)} 项文字提取受限`;
    $('#classification-start').disabled=data.state.running;$('#classification-retry').disabled=data.state.running;$('#classification-stop').hidden=!data.state.running||data.state.external;
    if(classificationPrevious!==-1&&classificationPrevious!==data.counted&&!$('#detail-dialog').open){await loadBooks(false);refreshFacets();}classificationPrevious=data.counted;
  }catch(error){$('#classification-summary').textContent='分类功能未连接，请重启爱妻的书屿并刷新页面。';}finally{classificationPollBusy=false;}
}
$('#classification-settings-button').onclick=()=>{$('#classification-error').hidden=true;$('#classification-dialog').showModal();refreshClassification();};
$('#classification-start').onclick=async()=>{try{$('#classification-error').hidden=true;await api('/api/classification/start',{});await refreshClassification();}catch(error){$('#classification-error').textContent=error.message;$('#classification-error').hidden=false;}};
$('#classification-retry').onclick=async()=>{try{$('#classification-error').hidden=true;await api('/api/classification/start',{mode:'errors'});await refreshClassification();}catch(error){$('#classification-error').textContent=error.message;$('#classification-error').hidden=false;}};
$('#classification-stop').onclick=async()=>{await api('/api/classification/stop',{});toast('正在保存已完成的本地整理结果');};
function classificationSourceLink(value){
  if(!value)return '';
  try{const url=new URL(value);if(url.protocol==='https:')return ` <a href="${escapeHTML(url.href)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`;}catch{}
  return '';
}
function renderPublication(book){
  $('#publication-panel')?.remove();const value=book.publication||{};
  $('#book-classification').insertAdjacentHTML('afterend',`<details id="publication-panel" class="publication-editor"><summary>更新状态 · <span id="publication-label">${escapeHTML(value.status||'待确认')}</span></summary><p class="field-help">按作者最后更新时间计算满六个月为断更；完结作品不转为断更。本地文件日期不代表作者更新时间。</p><form id="publication-form"><div class="publication-fields"><label>作品状态<select id="publication-state"><option value="">待确认</option>${['连载','断更','完结'].map(v=>`<option ${value.state===v?'selected':''}>${v}</option>`).join('')}</select></label><label>作者最后更新时间<input id="publication-date" type="date" value="${escapeHTML(value.last_update||'')}"></label></div><label class="field-label" for="publication-source">核验来源（可选）</label><input class="full-input" id="publication-source" type="url" placeholder="作品发布页网址" value="${escapeHTML(value.source_url||'')}"><p class="field-help">${escapeHTML(value.evidence||'没有可靠依据时保留待确认。')}${classificationSourceLink(value.source_url)}</p><p id="publication-error" class="notice" hidden></p><button class="button secondary" type="submit">保存更新状态</button></form></details>`);
  $('#publication-form').onsubmit=async event=>{event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;try{const panel=$('#publication-panel');book.publication=await api('/api/publication/save',{id:book.id,state:$('#publication-state').value,last_update:$('#publication-date').value,source_url:$('#publication-source').value});if(panel.isConnected)renderPublication(book);await loadBooks(false);refreshFacets();toast(`已保存：${book.publication.status||'待确认'}`);}catch(error){$('#publication-error').textContent=error.message;$('#publication-error').hidden=false;}finally{button.disabled=false;}};
}
function renderBookClassification(book){
  const value=book.classification||{},slot=$('#book-classification');
  const options=(values,current)=>'<option value="">待确认</option>'+values.map(v=>`<option value="${v}" ${v===current?'selected':''}>${v==='GB'?'GB 女操男':v==='大女主'?'大女主 · 女主无 CP':v}</option>`).join('');
  slot.innerHTML=`<div class="book-classification">${classificationPills(value)}</div><small class="classification-method">${value.reviewed?'已人工确认':value.method==='local'?'本地识别 · 待核验':'分类待确认'}${value.status==='conflict'?' · 检出冲突标记':''}</small><details class="classification-editor"><summary>修正分类 / 查看依据</summary><form id="classification-form"><div class="classification-edit-fields"><label>第一分类<select id="edit-level">${options(taxonomy.levels,value.level)}</select></label><label>第二分类<select id="edit-relationship">${options(taxonomy.relationships,value.relationship)}</select></label><label>BG 子分类<select id="edit-pairing" ${value.relationship!=='BG'?'disabled':''}>${options(taxonomy.pairings,value.pairing)}</select></label></div><div class="classification-tag-options">${taxonomy.tags.map(tag=>`<label class="check-line"><input type="checkbox" name="book-tag" value="${tag}" ${(value.tags||[]).includes(tag)?'checked':''}>${tag}</label>`).join('')}</div><p class="field-help">清水表示无成人性交描写；大女主表示女主无 CP。题材 tag 可多选。</p><div class="classification-evidence">${(value.evidence||[]).map(e=>`<p>${escapeHTML(e.value||'')} · ${escapeHTML(e.source||'')}：${escapeHTML(e.reason||'')}${classificationSourceLink(e.source_url)}</p>`).join('')||'尚无足够分类依据。'}</div><p id="classification-save-error" class="notice" hidden></p><button class="button secondary" type="submit">保存分类</button></form><button id="claude-classify-book" class="text-button">使用 Claude 为本书提出分类建议</button><p class="field-help">此按钮会按 AI 设置使用 API 或本机订阅，发送简介和抽样片段；确认后才保存。当前这轮本地整理不调用 Claude。</p><div id="classification-ai-preview" hidden></div></details>`;
  $('#edit-relationship').onchange=()=>{$('#edit-pairing').disabled=$('#edit-relationship').value!=='BG';if($('#edit-pairing').disabled)$('#edit-pairing').value='';};
  $('#classification-form').onsubmit=async event=>{event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;
    try{const saved=await api('/api/classification/save',{id:book.id,level:$('#edit-level').value,relationship:$('#edit-relationship').value,pairing:$('#edit-pairing').disabled?'':$('#edit-pairing').value,tags:$$('input[name="book-tag"]:checked',slot).map(e=>e.value)});book.classification=saved;if(slot.isConnected)renderBookClassification(book);await loadBooks(false);refreshClassification();refreshFacets();}
    catch(error){$('#classification-save-error').textContent=error.message;$('#classification-save-error').hidden=false;}finally{button.disabled=false;}
  };
  $('#claude-classify-book').onclick=async()=>{
    const button=$('#claude-classify-book');button.disabled=true;const preview=$('#classification-ai-preview');preview.hidden=false;preview.textContent='Claude 正在分析简介和抽样片段…';
    try{const result=await api('/api/classification/suggest',{id:book.id,version_id:$('#book-version').value});if(!preview.isConnected)return;
      preview.innerHTML=`<div class="book-classification">${classificationPills(result)}</div><p class="field-help">${escapeHTML(result.reason)} · ${Math.round(result.confidence*100)}% 置信度。抽样无法证明全文没有成人描写。</p><button id="apply-classification-suggestion" class="button secondary">填入编辑表单，待我确认</button>`;
      $('#apply-classification-suggestion').onclick=()=>{$('#edit-level').value=result.level;$('#edit-relationship').value=result.relationship;$('#edit-relationship').onchange();$('#edit-pairing').value=result.pairing;$$('input[name="book-tag"]',slot).forEach(e=>e.checked=result.tags.includes(e.value));preview.textContent='建议已填入，请检查后点击「保存分类」。';};
    }catch(error){if(preview.isConnected)preview.textContent=error.message;}finally{button.disabled=false;}
  };
}
async function showVersionWords(book,versionId,force=false){
  const generation=++wordGeneration,display=$('#version-words');$('#recount-words').disabled=true;
  const cached=versionId?book.versions.find(v=>v.id===versionId)?.word_count:book.text_metrics?.word_count;
  $('#word-count-help').textContent='文字和数字各计 1，不含标点空白；原有附注计入。';
  display.textContent=cached!==null&&cached!==undefined&&!force?`${number(cached)} 字`:'正在统计当前版本字数…';
  try{const data=await api('/api/text-metrics',{id:book.id,version_id:versionId,force});if(generation!==wordGeneration||!display.isConnected)return;
    display.textContent=data.word_count===null?'无法可靠统计字数':`${number(data.word_count)} 字`;if(data.error)$('#word-count-help').textContent=data.error;
    if(versionId){const version=book.versions.find(v=>v.id===versionId);if(version)version.word_count=data.word_count;}else book.text_metrics=data;
  }catch(error){if(display.isConnected)display.textContent=error.message;}finally{if(generation===wordGeneration&&$('#recount-words'))$('#recount-words').disabled=false;}
}
refreshClassification();setInterval(refreshClassification,15000);

let publicationDay=new Date().toLocaleDateString();
setInterval(()=>{const day=new Date().toLocaleDateString();if(day!==publicationDay){publicationDay=day;loadBooks(false);refreshFacets();}},60000);
