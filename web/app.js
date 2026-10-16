'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escapeHTML = (v = '') => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  library:'<path d="M4 4h4v16H4zM10 4h4v16h-4zM16 5l4-1 4 15-4 1z"/>',
  'book-open':'<path d="M12 5C8 2 4 3 2 4v15c4-2 7-1 10 1 3-2 6-3 10-1V4c-2-1-6-2-10 1zM12 5v15"/>',
  heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  'check-circle':'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  folder:'<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
  settings:'<path d="m10 3-1 3-3 1-2 3 1 3-1 3 2 3 3 1 1 2h4l1-2 3-1 2-3-1-3 1-3-2-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  'arrow-up-right':'<path d="M7 17 17 7M7 7h10v10"/>',
  'arrow-right':'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  'arrow-left':'<path d="M20 12H4m6-6-6 6 6 6"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  refresh:'<path d="M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5M4 14a8 8 0 0 0 14 5l3-3m0 5v-5h-5"/>',
  sparkles:'<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4zM21 2v4m-2-2h4"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  upload:'<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  moon:'<path d="M20 14a8 8 0 0 1-10-10 9 9 0 1 0 10 10z"/>',
  'chevron-left':'<path d="m15 5-7 7 7 7"/>',
  'chevron-right':'<path d="m9 5 7 7-7 7"/>',
  file:'<path d="M14 2H5v20h14V7zM14 2v6h5M8 13h8M8 17h5"/>',
  archive:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M11 3v3h2v3h-2v3h2v3h-2v3h2"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="2"/><path d="m21 16-6-6-9 11"/>',
  play:'<path d="m8 4 12 8-12 8z"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.file}</svg>`;
function fillIcons(root = document) { $$('[data-icon]',root).forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
fillIcons();
const state = {view:'all',kind:'',folder:'',q:'',sort:'modified',page:1,layout:localStorage.getItem('shuyu-layout') || 'grid',items:[],stats:null,request:0,detail:null,entries:[],entriesLoading:false};
const views = {all:'全部藏书',reading:'正在阅读',favorites:'我的收藏',recent:'最近阅读',done:'已经读完'};
const formatLabels = {'.txt':'TXT','.md':'MD','.text':'TXT','.epub':'EPUB','.eqpub':'EPUB','.pdf':'PDF','folder':'相册','.zip':'ZIP','.rar':'RAR','.7z':'7Z','.cbz':'CBZ','.cbr':'CBR','.docx':'DOCX','.doc':'DOC','.mobi':'MOBI','.azw3':'AZW3'};
const colors = [['#e4e8dd','#607153'],['#ede2d7','#8a7160'],['#e0e7e5','#617675'],['#eae4d1','#8a7f55'],['#e4dfe5','#7c6d7e'],['#e1e4d4','#6d7951'],['#e6dcd4','#936f58'],['#dce4e9','#5f7886']];
const archiveExts = ['.zip','.rar','.7z','.cbz','.cbr'];
const imageExts = ['.jpg','.jpeg','.png','.webp','.gif','.avif','.bmp'];
const textExts = ['.txt','.text','.md','.epub','.eqpub','.docx','.html','.htm','.fb2'];
function colorFor(book) { let hash=0; for(const c of book.id) hash=(hash*31+c.charCodeAt(0))|0; return colors[Math.abs(hash)%colors.length]; }
function sizeText(n) { return n>=1073741824 ? `${(n/1073741824).toFixed(1)} GB` : n>=1048576 ? `${(n/1048576).toFixed(1)} MB` : n>=1024 ? `${Math.round(n/1024)} KB` : `${n} B`; }
function number(n) { return new Intl.NumberFormat('zh-CN').format(n || 0); }
function formatDate(n) { return n ? new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '尚未扫描'; }
function rawURL(id,entry='') { return `/api/raw?${new URLSearchParams({id,entry})}`; }
async function api(path,data,options={}) {
  const response = await fetch(path,data===undefined ? options : {...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || '操作未完成，请稍后重试');
  return result;
}
let toastTimer;
function toast(message,error=false) { const el=$('#toast'); el.textContent=message; el.classList.toggle('error',error); el.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.hidden=true,error?6500:3000); }
function onError(error) { console.error(error); toast(error.message || '连接暂时中断，请确认书屿服务正在运行',true); }
function coverHTML(book) {
  const [bg,ink]=colorFor(book);
  return `<div class="book-cover" style="--cover:${bg};--cover-ink:${ink}"><span class="format-badge">${icon(book.kind==='archive'?'archive':book.kind==='comic'?'image':'file')}${escapeHTML(formatLabels[book.ext] || book.ext.slice(1).toUpperCase())}</span><div class="cover-inner"><div class="cover-decoration">SHUYU · LIBRARY</div><div class="cover-title">${escapeHTML(book.title)}</div>${book.author?`<div class="cover-author">${escapeHTML(book.author)} 著</div>`:'<div class="cover-line"></div>'}</div><div class="cover-seal">私 藏 故 事</div>${book.status==='reading'?'<div class="status-badge">正在读</div>':book.status==='done'?'<div class="status-badge">已读完</div>':''}${book.progress>0?`<div class="card-progress"><i style="width:${Math.round(book.progress*100)}%"></i></div>`:''}</div>`;
}
function cardHTML(book) {
  const tags=book.tags.split(/[,，;；\n]/).map(t=>t.trim()).filter(Boolean).slice(0,3);
  return `<article class="book-card" data-id="${book.id}"><button class="cover-button" data-detail="${book.id}" aria-label="查看 ${escapeHTML(book.title)}">${coverHTML(book)}</button><button class="favorite-button ${book.favorite?'on':''}" data-favorite="${book.id}" aria-label="${book.favorite?'取消收藏':'收藏'} ${escapeHTML(book.title)}" aria-pressed="${!!book.favorite}">${icon('heart')}</button><div class="card-info"><button class="card-title" data-detail="${book.id}" title="${escapeHTML(book.name)}">${escapeHTML(book.title)}</button><div class="card-meta"><span class="author">${escapeHTML(book.author || (book.kind==='comic'?`${book.image_count} 张图片`:book.rel.includes('/')?book.rel.split('/')[0]:'书库根目录'))}</span><span>·</span><span>${sizeText(book.size)}</span></div>${tags.length?`<div class="card-tags">${tags.map(t=>`<span>${escapeHTML(t)}</span>`).join('')}</div>`:''}</div></article>`;
}
function skeletons() { return Array.from({length:12},()=>'<div class="book-card skeleton"><div class="book-cover"></div><div class="skeleton-line"></div><div class="skeleton-line"></div></div>').join(''); }
function renderNavigation() {
  $$('#main-nav [data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===state.view&&!state.folder));
  $$('#folder-nav [data-folder]').forEach(el=>el.classList.toggle('active',el.dataset.folder===state.folder));
  $$('#kind-filters [data-kind]').forEach(el=>el.classList.toggle('active',el.dataset.kind===state.kind));
  const label=state.folder || views[state.view];
  $('#shelf-title').textContent=label;
  $('#breadcrumb-current').textContent=label;
  $('#reset-filters').hidden=!(state.kind||state.q||state.folder||state.view!=='all');
  $('#clear-search').hidden=!state.q;
  $$('.view-toggle button').forEach(el=>el.classList.toggle('active',el.dataset.layout===state.layout));
  $('#book-grid').classList.toggle('list-view',state.layout==='list');
}
function renderPagination(data) {
  const el=$('#pagination');
  if(data.pages<=1) { el.innerHTML=''; return; }
  let pages=[1,data.pages,state.page-1,state.page,state.page+1].filter(n=>n>=1&&n<=data.pages);
  pages=[...new Set(pages)].sort((a,b)=>a-b);
  let last=0;
  el.innerHTML=`<button data-page="${state.page-1}" ${state.page===1?'disabled':''} aria-label="上一页">‹</button>`+pages.map(n=>{const gap=last&&n-last>1?'<span>…</span>':'';last=n;return `${gap}<button class="${n===state.page?'active':''}" data-page="${n}" ${n===state.page?'aria-current="page"':''}>${n}</button>`;}).join('')+`<button data-page="${state.page+1}" ${state.page===data.pages?'disabled':''} aria-label="下一页">›</button><span>共 ${data.pages} 页</span>`;
}
async function loadBooks(showLoading=true) {
  const request=++state.request;
  renderNavigation();
  if(showLoading) { $('#book-grid').innerHTML=skeletons(); $('#empty-state').hidden=true; }
  try {
    const query=new URLSearchParams({view:state.view,kind:state.kind,folder:state.folder,q:state.q,sort:state.sort,page:state.page});
    const data=await api('/api/books?'+query);
    if(request!==state.request)return;
    if(data.pages&&state.page>data.pages) { state.page=data.pages; return loadBooks(false); }
    state.items=data.items;
    $('#result-count').textContent=`共 ${number(data.total)} 部故事`;
    $('#book-grid').innerHTML=data.items.map(cardHTML).join('');
    $('#empty-state').hidden=!!data.items.length;
    renderPagination(data);
  } catch(error) { if(request===state.request) { $('#book-grid').innerHTML=''; $('#library-error').textContent='连接不上本地服务，请确认启动窗口仍然开着。'; $('#library-error').hidden=false; } onError(error); }
}
async function refreshStats() {
  const stats=await api('/api/stats'); state.stats=stats;
  for(const field of ['all','reading','favorites','recent','done']) $('#count-'+field).textContent=number(field==='all'?stats.total:stats[field]);
  for(const field of ['total','reading','favorites']) $('#stat-'+field).innerHTML=`${number(stats[field])}<small>部</small>`;
  const rootName=stats.root.replaceAll('\\','/').split('/').filter(Boolean).at(-1);
  $('#source-name').textContent=rootName || stats.root;
  $('#source-name').title=stats.root;
  if(document.activeElement!==$('#root-input')) $('#root-input').value=stats.root;
  $('#scan-dot').classList.toggle('running',stats.scan.running);
  $('#scan-dot').classList.toggle('error',!!stats.scan.error);
  $('#scan-label').textContent=stats.scan.running?`正在扫描 · ${number(stats.scan.seen)} 部`:stats.scan.error?'书库需要留意':'书库已同步';
  $('#scan-time').textContent=stats.scan.running?'新故事正在陆续上架':stats.scan.last_scan?`上次扫描 ${formatDate(stats.scan.last_scan)}`:'新故事会自动出现在这里';
  $('#scan-button').disabled=stats.scan.running;
  $('#scan-button span:last-child').textContent=stats.scan.running?'扫描中…':'扫描书库';
  $('#library-error').textContent=stats.scan.error;
  $('#library-error').hidden=!stats.scan.error;
  const folderSignature=JSON.stringify(stats.folders);
  if($('#folder-nav').dataset.signature!==folderSignature) {
    $('#folder-nav').dataset.signature=folderSignature;
    $('#folder-nav').innerHTML=stats.folders.length?stats.folders.map(([name,count])=>`<button class="nav-item folder-item" data-folder="${escapeHTML(name)}" title="${escapeHTML(name)}">${icon('folder')}<span class="folder-name">${escapeHTML(name)}</span><b>${number(count)}</b></button>`).join(''):'<div class="side-placeholder muted small">子文件夹会显示在这里</div>';
  }
  renderNavigation();
  const latest=stats.latest;
  if(latest) {
    $('#continue-title').textContent=latest.title;
    $('#continue-description').textContent=`${latest.author?latest.author+' · ':''}${latest.status==='done'?'已经读完，也可以重温喜欢的段落。':'接着上次的阅读位置，让故事继续。'}`;
    $('#continue-button').innerHTML=`继续阅读${icon('arrow-right')}`;
    const progress=$('#continue-progress');progress.hidden=latest.kind==='pdf';
    $('i',progress).style.width=Math.round(latest.progress*150)+'px';
    $('span',progress).textContent=`已读 ${Math.round(latest.progress*100)}%`;
  } else {
    $('#continue-title').textContent='给自己留一点，读故事的时间。';
    $('#continue-description').textContent='不必整理好一切，先从喜欢的那一本开始。';
    $('#continue-button').innerHTML=`逛逛我的书架${icon('arrow-right')}`;
    $('#continue-progress').hidden=true;
  }
  return stats;
}
function resetFilters() { Object.assign(state,{view:'all',kind:'',folder:'',q:'',page:1}); $('#search-input').value=''; loadBooks(); }
$('#main-nav').addEventListener('click',event=>{const b=event.target.closest('[data-view]');if(!b)return;Object.assign(state,{view:b.dataset.view,folder:'',page:1});$('#sidebar').classList.remove('open');loadBooks();});
$('#folder-nav').addEventListener('click',event=>{const b=event.target.closest('[data-folder]');if(!b)return;Object.assign(state,{view:'all',folder:state.folder===b.dataset.folder?'':b.dataset.folder,page:1});$('#sidebar').classList.remove('open');loadBooks();});
$('#kind-filters').addEventListener('click',event=>{const b=event.target.closest('[data-kind]');if(!b)return;state.kind=b.dataset.kind;state.page=1;loadBooks();});
let searchTimer;
$('#search-input').addEventListener('input',event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=event.target.value.trim();state.page=1;loadBooks();},240);});
$('#sort-select').addEventListener('change',event=>{state.sort=event.target.value;state.page=1;loadBooks();});
$('#clear-search').onclick=()=>{clearTimeout(searchTimer);state.q='';state.page=1;$('#search-input').value='';loadBooks();};
$('#reset-filters').onclick=resetFilters;
$('#empty-action').onclick=resetFilters;
$('.view-toggle').addEventListener('click',event=>{const b=event.target.closest('[data-layout]');if(!b)return;state.layout=b.dataset.layout;localStorage.setItem('shuyu-layout',state.layout);renderNavigation();});
$('#pagination').addEventListener('click',event=>{const b=event.target.closest('[data-page]');if(!b||b.disabled)return;state.page=Number(b.dataset.page);loadBooks();$('.shelf-section').scrollIntoView({behavior:'smooth',block:'start'});});
$('#scan-button').onclick=async()=>{try{await api('/api/scan',{});await refreshStats();toast('开始扫描，新文件会自动加入书架');}catch(e){onError(e);}};
$('#menu-button').onclick=()=>$('#sidebar').classList.toggle('open');
document.addEventListener('click',event=>{if(!event.target.closest('#sidebar,#menu-button'))$('#sidebar').classList.remove('open');});
$('#book-grid').addEventListener('click',async event=>{
  const favorite=event.target.closest('[data-favorite]');
  if(favorite) { const book=state.items.find(b=>b.id===favorite.dataset.favorite);if(!book)return;try{const result=await api('/api/update',{id:book.id,favorite:!book.favorite});Object.assign(book,result);favorite.classList.toggle('on',!!book.favorite);favorite.setAttribute('aria-pressed',String(!!book.favorite));favorite.setAttribute('aria-label',`${book.favorite?'取消收藏':'收藏'} ${book.title}`);toast(book.favorite?'已放进偏爱的故事里':'已取消收藏');await refreshStats();if(state.view==='favorites')loadBooks(false);}catch(e){onError(e);}return; }
  const detail=event.target.closest('[data-detail]');if(detail)openDetail(detail.dataset.detail);
});

let detailGeneration=0;
async function openDetail(id) {
  const generation=++detailGeneration;
  state.entries=[];state.entriesLoading=false;
  try {
    const book=await api('/api/book?'+new URLSearchParams({id}));
    if(generation!==detailGeneration)return;
    state.detail=book;
    const container=book.kind==='archive'||book.kind==='comic';
    $('#detail-content').innerHTML=`<div class="detail-main"><button class="icon-button detail-close" data-close="detail-dialog" aria-label="关闭详情">×</button><div class="detail-cover">${coverHTML(book)}<div class="metadata-row"><span>${escapeHTML(formatLabels[book.ext]||book.ext.slice(1).toUpperCase())}</span><span>${sizeText(book.size)}</span>${book.kind==='comic'?`<span>${book.image_count} 张</span>`:''}</div></div><div class="detail-info"><div class="detail-topline"><span class="eyebrow">A STORY IN YOUR LIBRARY</span><button id="detail-favorite" class="icon-button" aria-label="${book.favorite?'取消收藏':'收藏'}" aria-pressed="${!!book.favorite}" style="color:${book.favorite?'#ac8166':'#97a587'}">${icon('heart')}</button></div><h2>${escapeHTML(book.title)}</h2><div class="detail-author">${escapeHTML(book.author||'作者未标注')}</div><div class="detail-actions"><button id="read-button" class="button primary">${icon(container?'folder':'book-open')}${book.kind==='archive'?'浏览包内文件':book.kind==='comic'?'开始看图':book.last_read?'继续阅读':'开始阅读'}</button><button id="reveal-button" class="button secondary" title="在文件管理器中显示">${icon('folder')}定位文件</button></div><div class="detail-path">${escapeHTML(book.rel)}</div><label class="field-label">阅读状态</label><div class="status-picker">${[['unread','还没开始'],['reading','正在阅读'],['done','已经读完']].map(([key,label])=>`<button data-status="${key}" class="${book.status===key?'active':''}">${label}</button>`).join('')}</div><label class="field-label" for="detail-tags">我的标签</label><input id="detail-tags" class="full-input" placeholder="例如：待看、喜欢的作者、周末读（逗号分隔）" value="${escapeHTML(book.tags)}"><label class="field-label" for="detail-notes">留一点想法</label><textarea id="detail-notes" rows="3" placeholder="写下简介、读后感，或者下次想读的理由…">${escapeHTML(book.notes)}</textarea><div class="metadata-row"><span>文件更新于 ${formatDate(book.mtime)}</span>${book.last_read?`<span>上次阅读 ${formatDate(book.last_read)}</span>`:''}</div></div></div><div class="detail-toolbar"><button id="system-open" class="button ghost">${icon('arrow-up-right')}系统应用打开</button><button id="save-detail" class="button secondary">保存标签与笔记</button></div>${container?`<section class="entry-section" id="entry-section"><div class="entry-heading"><h3>${book.kind==='comic'?'相册里的画面':'压缩包里的故事'}</h3><span id="entry-count">正在读取…</span></div><input id="entry-search" class="full-input entry-search" placeholder="搜索包内文件…"><div id="entry-list" class="entry-list"><div class="entry-empty">正在查看内容，较大的压缩包可能需要一点时间…</div></div></section>`:''}`;
    const dialog=$('#detail-dialog');if(!dialog.open)dialog.showModal();dialog.scrollTop=0;
    $('#detail-favorite').onclick=async()=>{try{book.favorite=!book.favorite;await api('/api/update',{id:book.id,favorite:book.favorite});$('#detail-favorite').style.color=book.favorite?'#ac8166':'#97a587';$('#detail-favorite').setAttribute('aria-pressed',String(!!book.favorite));$('#detail-favorite').setAttribute('aria-label',book.favorite?'取消收藏':'收藏');refreshStats().catch(onError);loadBooks(false);}catch(e){book.favorite=!book.favorite;onError(e);}};
    $('#reveal-button').onclick=()=>api('/api/reveal',{id:book.id}).catch(onError);
    $('#system-open').onclick=()=>api('/api/open',{id:book.id}).then(()=>toast('已交给系统应用打开')).catch(onError);
    $('#save-detail').onclick=()=>saveDetail(book).then(()=>toast('标签和笔记已保存')).catch(onError);
    $('.status-picker').onclick=async event=>{const b=event.target.closest('[data-status]');if(!b)return;try{await api('/api/update',{id:book.id,status:b.dataset.status});book.status=b.dataset.status;$$('[data-status]',$('.status-picker')).forEach(el=>el.classList.toggle('active',el===b));refreshStats().catch(onError);loadBooks(false);}catch(e){onError(e);}};
    $('#read-button').onclick=async()=>{
      if(book.kind==='archive') { $('#entry-section').scrollIntoView({behavior:'smooth'}); return; }
      if(book.kind==='comic') { if(!state.entries.length){toast('图片列表还在加载，请稍候');return;} const saved=state.entries.find(e=>e.key===book.last_entry);startReading(book,saved||state.entries[0],state.entries); }
      else startReading(book);
    };
    if(container) {
      state.entriesLoading=true;
      try {
        const result=await api('/api/entries?'+new URLSearchParams({id:book.id}));
        if(generation!==detailGeneration||!dialog.open)return;
        state.entries=result.entries;state.entriesLoading=false;
        $('#entry-count').textContent=`共 ${number(result.entries.length)} 个文件`;
        $('#entry-search').oninput=renderEntries;
        renderEntries();
        $('#entry-list').onclick=event=>{const b=event.target.closest('[data-entry-index]');if(!b)return;const entry=state.entries[Number(b.dataset.entryIndex)];startReading(book,entry,state.entries);};
        if(book.kind==='archive'&&book.last_entry) $('#read-button').innerHTML=`${icon('play')}继续包内阅读`;
        if(book.kind==='archive'&&book.last_entry) $('#read-button').onclick=()=>{const entry=state.entries.find(e=>e.key===book.last_entry);if(entry)startReading(book,entry,state.entries);else $('#entry-section').scrollIntoView({behavior:'smooth'});};
      }catch(e){if(generation===detailGeneration){state.entriesLoading=false;$('#entry-count').textContent='读取失败';$('#entry-list').innerHTML=`<div class="entry-empty">${escapeHTML(e.message)}</div>`;}}
    }
  }catch(e){onError(e);}
}
function renderEntries() {
  const query=$('#entry-search').value.trim().toLocaleLowerCase();
  const filtered=state.entries.map((entry,index)=>({entry,index})).filter(({entry})=>entry.name.toLocaleLowerCase().includes(query));
  $('#entry-list').innerHTML=filtered.slice(0,1500).map(({entry,index})=>`<button class="entry-row" data-entry-index="${index}">${icon(imageExts.includes(entry.ext)?'image':archiveExts.includes(entry.ext)?'archive':'file')}<span>${escapeHTML(entry.name)}</span>${entry.reading?`<small>已读 ${Math.round(entry.reading.progress*100)}%</small>`:''}<small>${entry.size?sizeText(entry.size):''}</small><span class="entry-type">${escapeHTML(entry.ext.slice(1).toUpperCase()||'FILE')}</span>${icon('chevron-right')}</button>`).join('')||'<div class="entry-empty">没有匹配的文件。</div>';
  if(filtered.length>1500)$('#entry-list').insertAdjacentHTML('beforeend','<div class="entry-empty">文件较多，请输入关键词缩小范围。</div>');
}
async function saveDetail(book=state.detail) {
  if(!book||!$('#detail-tags'))return;
  const tags=$('#detail-tags').value,notes=$('#detail-notes').value;
  if(book.tags===tags&&book.notes===notes)return;
  const result=await api('/api/update',{id:book.id,tags,notes});Object.assign(book,result);loadBooks(false);
}
$$('[data-close]').forEach(el=>el.onclick=()=>$('#'+el.dataset.close).close());
document.addEventListener('click',event=>{const b=event.target.closest('[data-close]');if(b)$('#'+b.dataset.close).close();});
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}}));
$('#detail-dialog').addEventListener('close',()=>{detailGeneration++;saveDetail().catch(onError);});
$('#settings-button').onclick=()=>{$('#settings-dialog').showModal();$('#sidebar').classList.remove('open');};
$('#settings-form').onsubmit=async event=>{event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;try{await api('/api/settings',{root:$('#root-input').value.trim()});state.folder='';state.page=1;$('#settings-dialog').close();await refreshStats();await loadBooks();toast('已切换书库，正在扫描');}catch(e){onError(e);}finally{button.disabled=false;}};
$('#import-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>1900000)throw new Error('备份文件过大，请直接备份项目的 .data 文件夹');const backup=JSON.parse(await file.text());if(!Array.isArray(backup.books)||!Array.isArray(backup.reading))throw new Error('这不是书屿的阅读记录备份');if(!window.confirm('恢复备份将覆盖匹配书籍的收藏、标签、笔记及进度。确定恢复？'))return;const result=await api('/api/import',{backup});toast(`已恢复 ${number(result.restored)} 本书的记录`);await refreshStats();await loadBooks(false);}catch(e){onError(e);}finally{event.target.value='';}};
$('#continue-button').onclick=async()=>{const book=state.stats?.latest;if(!book){$('.shelf-section').scrollIntoView({behavior:'smooth'});return;}if(book.kind==='archive'||book.kind==='comic')openDetail(book.id);else startReading(book);};

const readerState={book:null,entry:'',mode:'text',start:0,end:0,total:0,chapters:[],history:[],images:[],imageIndex:0,font:Number(localStorage.getItem('shuyu-font')||21),theme:localStorage.getItem('shuyu-theme')||'paper',request:0,busy:false,progressTimer:null};
function setReaderAppearance() { const el=$('#reader');el.classList.toggle('dark',readerState.theme==='dark');el.classList.toggle('white',readerState.theme==='white');$('#reader-text').style.fontSize=readerState.font+'px'; }
setReaderAppearance();
function getPosition() {
  if(readerState.mode==='comic')return readerState.imageIndex;
  const scroll=$('#reader-scroll');
  const ratio=scroll.scrollHeight>0?Math.max(0,scroll.scrollTop)/(scroll.scrollHeight):0;
  return Math.round(readerState.start+(readerState.end-readerState.start)*Math.min(1,ratio));
}
function currentProgress() { return readerState.mode==='comic'?(readerState.imageIndex+1)/Math.max(1,readerState.images.length):readerState.mode==='pdf'?readerState.book.progress||0:Math.min(1,getPosition()/Math.max(1,readerState.total)); }
async function persistProgress(keepalive=false) {
  if(!readerState.book||readerState.busy)return;
  const book=readerState.book;
  const entry=readerState.mode==='comic'?readerState.images[readerState.imageIndex]?.key||'':readerState.entry;
  let position=getPosition(),progress=currentProgress();
  if(readerState.mode==='text') {
    const s=$('#reader-scroll');
    if(s.scrollHeight>0&&s.scrollTop+s.clientHeight>=s.scrollHeight-8) {position=readerState.end;progress=position/Math.max(1,readerState.total);}
    // Reopen the final page instead of showing a single character at EOF.
    if(position>=readerState.total&&readerState.total) position=readerState.start;
  }
  if(readerState.mode==='pdf')position=0;
  await api('/api/progress',{id:book.id,entry,position,progress},{keepalive});
  book.progress=progress;book.last_entry=entry;
}
async function startReading(book,entry=null,entries=[]) {
  const ext=entry?entry.ext:book.ext;
  if(!textExts.includes(ext)&&ext!=='.pdf'&&!imageExts.includes(ext)&&book.kind!=='comic') {
    if(entry) { toast('这个包内格式暂不支持直接阅读，可下载后使用系统应用打开');const a=document.createElement('a');a.href=rawURL(book.id,entry.key);a.download=entry.name.split('/').at(-1);a.click(); }
    else {try{await api('/api/open',{id:book.id});toast('已交给系统应用打开');}catch(e){onError(e);}}
    return;
  }
  try {await saveDetail();}catch(e){onError(e);return;}
  $('#detail-dialog').close();
  Object.assign(readerState,{book,entry:entry?.key||'',mode:imageExts.includes(ext)||book.kind==='comic'?'comic':ext==='.pdf'?'pdf':'text',history:[],start:0,end:0,total:0,chapters:[],request:readerState.request+1,busy:false});
  $('#reader-title').textContent=entry?entry.name.split('/').at(-1):book.title;
  $('#reader-subtitle').textContent=entry?book.title:book.author||'书屿 · 私人阅读时间';
  $('#reader').hidden=false;document.body.style.overflow='hidden';
  $('#reader-text').textContent='';$('#reader-text').hidden=readerState.mode!=='text';
  $('#comic-pages').innerHTML='';$('#comic-pages').hidden=readerState.mode!=='comic';
  $('#pdf-frame').hidden=readerState.mode!=='pdf';$('#pdf-frame').removeAttribute('src');
  $('#reader-end').hidden=true;$('#reader-toc').hidden=true;
  $('#reader-toc-button').hidden=readerState.mode==='pdf';
  $('#reader-loading').hidden=readerState.mode!=='text';
  $('#font-up').hidden=readerState.mode!=='text';$('#font-down').hidden=readerState.mode!=='text';
  $('#reader-scroll').scrollTop=0;
  if(readerState.mode==='pdf') {
    $('#pdf-frame').src=rawURL(book.id,entry?.key||'');
    // Browser PDF viewer owns page navigation; no fictitious page progress.
    persistProgress().catch(onError);
  } else if(readerState.mode==='comic') {
    readerState.images=entries.filter(e=>imageExts.includes(e.ext));
    if(!readerState.images.length)readerState.images=[entry];
    readerState.imageIndex=Math.max(0,readerState.images.findIndex(e=>e.key===entry?.key));
    readerState.total=readerState.images.length;
    $('#toc-items').innerHTML=readerState.images.map((e,i)=>`<button class="toc-item" data-image-index="${i}">${i+1}. ${escapeHTML(e.name.split('/').at(-1))}</button>`).join('');
    await showImage();
  } else await loadText(-1);
}
async function loadText(start) {
  const request=++readerState.request;
  readerState.busy=true;
  $('#reader-loading').textContent='正在翻开故事…';
  $('#reader-loading').hidden=false;$('#reader-text').hidden=true;$('#reader-end').hidden=true;
  $('#prev-page').disabled=true;$('#next-page').disabled=true;
  try {
    const data=await api('/api/content?'+new URLSearchParams({id:readerState.book.id,entry:readerState.entry,start}));
    if(request!==readerState.request||$('#reader').hidden)return;
    Object.assign(readerState,{start:data.start,end:data.end,total:data.total,chapters:data.chapters,busy:false});
    $('#reader-text').textContent=data.text||'这个文件还没有文字。';
    $('#reader-loading').hidden=true;$('#reader-text').hidden=false;
    $('#reader-scroll').scrollTop=0;
    $('#reader-end').hidden=data.end<data.total;
    $('#toc-items').innerHTML=data.chapters.length?data.chapters.map(c=>`<button class="toc-item ${c.position>=data.start&&c.position<data.end?'active':''}" data-position="${c.position}">${escapeHTML(c.title)}</button>`).join(''):'<p class="muted small" style="padding:12px;line-height:1.9">没有识别到章节标题，可以使用下方按钮翻页。</p>';
    updateReaderFooter();
    persistProgress().catch(onError);
  }catch(e){if(request===readerState.request){readerState.busy=false;$('#reader-loading').textContent=e.message+' 可返回书架，用系统应用打开。';$('#reader-text').hidden=true;}onError(e);}
}
async function showImage() {
  const image=readerState.images[readerState.imageIndex];if(!image)return;
  $('#comic-pages').innerHTML=`<img src="${escapeHTML(rawURL(readerState.book.id,image.key))}" alt="${escapeHTML(image.name)}" decoding="async">`;
  const img=$('img',$('#comic-pages'));
  img.onerror=()=>{$('#comic-pages').innerHTML='<div class="image-error">图片加载失败，请检查文件是否可访问，或返回书架定位原文件。</div>';};
  $('#reader-scroll').scrollTop=0;
  $('#reader-subtitle').textContent=`${readerState.book.title} · ${image.name.split('/').at(-1)}`;
  $$('.toc-item',$('#toc-items')).forEach(el=>el.classList.toggle('active',Number(el.dataset.imageIndex)===readerState.imageIndex));
  $('#reader-end').hidden=readerState.imageIndex<readerState.images.length-1;
  updateReaderFooter();
  await persistProgress();
}
function updateReaderFooter() {
  if(readerState.mode==='comic') {
    $('#reader-progress').textContent=`第 ${readerState.imageIndex+1} / ${readerState.images.length} 张`;
    $('#prev-page').disabled=readerState.imageIndex===0;$('#next-page').disabled=readerState.imageIndex>=readerState.images.length-1;
  }else{
    $('#reader-progress').textContent=`${Math.round(currentProgress()*100)}% · ${number(readerState.total)} 字`;
    $('#prev-page').disabled=readerState.start===0;$('#next-page').disabled=readerState.end>=readerState.total;
  }
}
async function turnPage(direction) {
  if(readerState.busy)return;
  if(readerState.mode==='comic') {
    const index=readerState.imageIndex+direction;if(index<0||index>=readerState.images.length)return;
    readerState.imageIndex=index;showImage().catch(onError);
  }else if(readerState.mode==='text') {
    if(direction>0) {
      if(readerState.end>=readerState.total)return;
      readerState.history.push(readerState.start);await loadText(readerState.end);
    }else{
      if(readerState.start===0)return;
      await loadText(readerState.history.pop()??Math.max(0,readerState.start-14000));
    }
  }
}
$('#prev-page').onclick=()=>turnPage(-1);
$('#next-page').onclick=()=>turnPage(1);
$('#reader-scroll').addEventListener('scroll',()=>{
  if(readerState.mode!=='text'||readerState.busy)return;
  updateReaderFooter();clearTimeout(readerState.progressTimer);readerState.progressTimer=setTimeout(()=>persistProgress().catch(onError),900);
},{passive:true});
$('#reader-back').onclick=async()=>{
  clearTimeout(readerState.progressTimer);
  try{await persistProgress();}catch(e){onError(e);}
  readerState.request++;readerState.book=null;
  $('#reader').hidden=true;$('#reader-toc').hidden=true;$('#pdf-frame').removeAttribute('src');document.body.style.overflow='';
  refreshStats().catch(onError);loadBooks(false);
};
$('#reader-toc-button').onclick=()=>{$('#reader-toc').hidden=!$('#reader-toc').hidden;};
$('#toc-items').onclick=event=>{
  const position=event.target.closest('[data-position]'),image=event.target.closest('[data-image-index]');
  if(position&&!readerState.busy){readerState.history.push(readerState.start);loadText(Number(position.dataset.position));}
  if(image){readerState.imageIndex=Number(image.dataset.imageIndex);showImage().catch(onError);}
  if(innerWidth<601)$('#reader-toc').hidden=true;
};
$('#font-up').onclick=()=>{readerState.font=Math.min(36,readerState.font+2);localStorage.setItem('shuyu-font',readerState.font);setReaderAppearance();};
$('#font-down').onclick=()=>{readerState.font=Math.max(14,readerState.font-2);localStorage.setItem('shuyu-font',readerState.font);setReaderAppearance();};
$('#reader-theme').onclick=()=>{readerState.theme={paper:'dark',dark:'white',white:'paper'}[readerState.theme]||'paper';localStorage.setItem('shuyu-theme',readerState.theme);setReaderAppearance();};
$('#mark-finished').onclick=async()=>{if(!readerState.book)return;try{await api('/api/update',{id:readerState.book.id,status:'done'});toast('又读完了一个故事。');$('#reader-back').click();}catch(e){onError(e);}};
document.addEventListener('keydown',event=>{
  if(event.target.matches('input,textarea,select'))return;
  if(!$('#reader').hidden) {
    if(event.key==='Escape') { if(!$('#reader-toc').hidden)$('#reader-toc').hidden=true;else $('#reader-back').click(); }
    if(event.key==='ArrowRight'){event.preventDefault();turnPage(1);}
    if(event.key==='ArrowLeft'){event.preventDefault();turnPage(-1);}
  }else if(event.key==='/'&&!$('dialog[open]')){event.preventDefault();$('#search-input').focus();}
});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')persistProgress(true).catch(()=>{});});
window.addEventListener('pagehide',()=>{if(!readerState.book||readerState.busy)return;const book=readerState.book;const entry=readerState.mode==='comic'?readerState.images[readerState.imageIndex]?.key||'':readerState.entry;fetch('/api/progress',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:book.id,entry,position:getPosition(),progress:currentProgress()}),keepalive:true}).catch(()=>{});});
let pollBusy=false,previousScan=false,previousCount=-1;
async function poll() {
  if(pollBusy)return;pollBusy=true;
  try {const stats=await refreshStats();if((previousScan||stats.scan.running||previousCount!==stats.total)&&$('#reader').hidden&&!$('#detail-dialog').open)await loadBooks(false);previousScan=stats.scan.running;previousCount=stats.total;}catch(e){$('#scan-label').textContent='本地服务未连接';$('#scan-dot').classList.add('error');}finally{pollBusy=false;}
}
renderNavigation();loadBooks();refreshStats().then(stats=>{previousScan=stats.scan.running;previousCount=stats.total;}).catch(onError);
setInterval(poll,2500);
