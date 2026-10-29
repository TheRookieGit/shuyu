'use strict';
const wishKinds={novel:'小说文件',folder:'文件夹',zip:'ZIP 压缩包'};
const wishIcons={novel:'file',folder:'folder',zip:'archive'};
let wishItems=[],editingWish=null,wishHub=false,wishShots=[];
function safeURL(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:'';}catch{return '';}}
function linkHTML(value,label='下载地址'){const url=safeURL(value);return url?`<a class="wish-link" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${icon('link')}${label} ↗</a>`:'';}
function setView(view){
  if(state.view===view){loadBooks();return;}
  state.view=view;state.page=1;
  $$('.shelf-tab').forEach(b=>{const on=b.dataset.view===view;b.classList.toggle('active',on);b.setAttribute('aria-selected',String(on));});
  $$('[data-mobile]').forEach(b=>b.classList.toggle('active',b.dataset.mobile===view));
  $('.catalog-layout').classList.toggle('wish-mode',view==='wish');$('#wish-add').hidden=view!=='wish';
  $('#shelf-title').textContent=view==='wish'?'想看清单':'全部小说';
  $('#search-input').placeholder=view==='wish'?'搜索想看清单的书名、作者、备注…':'搜索书名、作者，找到下一个故事…';
  setFilterSheet(false);loadBooks();
}
function wishCardHTML(item){
  const kind=wishKinds[item.kind]||item.kind;
  if(item.downloaded)return `<article class="book-card wish-card downloaded"><button class="cover-button" data-detail="${item.book_id}" aria-label="查看已下载的 ${escapeHTML(item.title)}"><div class="book-cover wish-cover"><span class="format-badge">${icon(wishIcons[item.kind])}${escapeHTML(kind)}</span><div class="cover-inner"><div class="cover-decoration">ON YOUR SHELF</div><div class="cover-title">${escapeHTML(item.title)}</div></div><div class="cover-seal">已 下 载</div></div></button><div class="card-info"><button class="card-title" data-detail="${item.book_id}">${escapeHTML(item.title)}</button><div class="card-meta"><span class="author">${escapeHTML(item.author||'作者未标注')}</span><span>·</span><span>已到书架</span></div><button class="text-button wish-edit" data-wish="${item.id}">编辑记录</button></div></article>`;
  return `<article class="book-card wish-card"><button class="cover-button" data-wish="${item.id}" aria-label="编辑想看的 ${escapeHTML(item.title)}"><div class="book-cover wish-cover"><span class="format-badge">${icon(wishIcons[item.kind])}${escapeHTML(kind)}</span><div class="cover-inner"><div class="cover-decoration">WISHLIST · 想看</div><div class="cover-title">${escapeHTML(item.title)}</div>${item.author?`<div class="cover-author">${escapeHTML(item.author)} 著</div>`:'<div class="cover-line"></div>'}</div><div class="cover-seal">尚 未 下 载</div></div></button><div class="card-info"><button class="card-title" data-wish="${item.id}" title="${escapeHTML(item.title)}">${escapeHTML(item.title)}</button><div class="card-meta"><span class="author">${escapeHTML(item.author||'作者未标注')}</span><span>·</span><span>${escapeHTML(kind)}</span></div>${item.note?`<p class="wish-note">${escapeHTML(item.note)}</p>`:''}${linkHTML(item.url)}</div></article>`;
}
function updateWishCount(pending){$('#wish-count').textContent=pending?` ${number(pending)}`:'';}
async function refreshWishCount(){try{updateWishCount((await api('/api/wishlist')).pending);}catch{}}
async function loadWishlist(){
  const request=++state.request;renderFilters();$('#empty-state').hidden=true;
  try{const data=await api('/api/wishlist?'+new URLSearchParams({q:state.q}));if(request!==state.request||state.view!=='wish')return;
    wishItems=data.items;wishHub=data.hub;updateWishCount(data.pending);
    $('#book-grid').innerHTML=data.items.map(wishCardHTML).join('');$('#pagination').innerHTML='';
    const arrived=data.items.length-data.pending;
    $('#result-count').textContent=data.items.length?`${number(data.pending)} 本还没下载${arrived?` · ${number(arrived)} 本已到书架`:''}`:'';
    $('#wish-empty').hidden=!!data.items.length;$('#wish-empty h3').textContent=state.q?'想看清单里没有匹配的书':'还没有想看的书';
  }catch(error){if(request===state.request)$('#book-grid').innerHTML='';onError(error);}
}
function openWish(id){
  editingWish=wishItems.find(item=>item.id===id)||null;const item=editingWish||{title:'',author:'',kind:'novel',url:'',note:''};
  $('#wish-dialog-title').textContent=editingWish?'编辑想看':'添加想看';
  $('#wish-name').value=item.title;$('#wish-author').value=item.author;$('#wish-url').value=item.url;$('#wish-note').value=item.note;
  $$('input[name="wish-kind"]').forEach(el=>el.checked=el.value===item.kind);
  const url=safeURL(item.url);$('#wish-open-url').hidden=!url;if(url)$('#wish-open-url').href=url;
  wishShots=[...(item.shots||[])];$('#wish-shots-field').hidden=!wishHub;renderWishShots();
  $('#wish-delete').hidden=!editingWish;$('#wish-delete').textContent='删除';delete $('#wish-delete').dataset.confirm;$('#wish-error').hidden=true;
  $('#wish-dialog').showModal();
}
$('.shelf-tabs').addEventListener('click',event=>{const tab=event.target.closest('[data-view]');if(tab)setView(tab.dataset.view);});
$('#book-grid').addEventListener('click',event=>{const button=event.target.closest('[data-wish]');if(button)openWish(button.dataset.wish);});
$('#wish-add').onclick=()=>openWish(null);$('#wish-empty-add').onclick=()=>openWish(null);
$('#wish-form').onsubmit=async event=>{
  event.preventDefault();const button=$('button[type="submit"]',event.target);button.disabled=true;$('#wish-error').hidden=true;
  try{const saved=await api('/api/wishlist/save',{id:editingWish?.id||'',title:$('#wish-name').value,author:$('#wish-author').value,...(wishHub?{shots:wishShots}:{}),kind:$('input[name="wish-kind"]:checked').value,url:$('#wish-url').value,note:$('#wish-note').value});
    $('#wish-dialog').close();toast(saved.downloaded?`《${saved.title}》已经在书架上了`:`已加入想看：${saved.title}`);
    if(state.view==='wish')await loadWishlist();else refreshWishCount();
  }catch(error){$('#wish-error').textContent=error.message;$('#wish-error').hidden=false;}finally{button.disabled=false;}
};
// Original screenshots, shared with the wish hub: shown from the hub, added here go up to it when the wish is saved.
const shotCache=new Map();
function renderWishShots(){
  $('#wish-shots').innerHTML=wishShots.map(id=>`<span class="wish-shot"><img data-shot="${escapeHTML(id)}" alt="原截图"><button type="button" class="wish-shot-remove" data-remove-shot="${escapeHTML(id)}" aria-label="删掉这张截图">×</button></span>`).join('')+'<label class="wish-shot-add"><input id="wish-shot-input" type="file" accept="image/*" multiple hidden><span>＋ 添加截图</span></label>';
  $$('#wish-shots [data-shot]').forEach(async img=>{const id=img.dataset.shot;
    try{if(!shotCache.has(id))shotCache.set(id,(await api('/api/wishlist/shot?'+new URLSearchParams({id}))).data);img.src=shotCache.get(id);img.onclick=()=>window.open().document.write(`<img src="${shotCache.get(id)}" style="max-width:100%">`);}
    catch(error){img.closest('.wish-shot').classList.add('missing');img.title=error.message;}});
  $('#wish-shot-input').onchange=event=>{addWishShots([...event.target.files]);event.target.value='';};
}
// The same size the hub keeps: the long side at most 1600px, as JPEG.
async function shotDataURL(file){
  const bitmap=await createImageBitmap(file);const scale=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));
  const canvas=document.createElement('canvas');canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);
  canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
  let data=canvas.toDataURL('image/jpeg',.72);if(data.length>900000)data=canvas.toDataURL('image/jpeg',.5);return data;
}
async function addWishShots(files){
  for(const file of files.filter(f=>f.type.startsWith('image/'))){
    try{const data=await shotDataURL(file);const {id}=await api('/api/wishlist/shot',{data});shotCache.set(id,data);wishShots.push(id);}catch(error){onError(error);}
  }
  renderWishShots();
}
$('#wish-shots').addEventListener('click',event=>{const button=event.target.closest('[data-remove-shot]');if(!button)return;
  wishShots=wishShots.filter(id=>id!==button.dataset.removeShot);renderWishShots();});
$('#wish-dialog').addEventListener('paste',event=>{if(!wishHub)return;const files=[...(event.clipboardData?.files||[])].filter(f=>f.type.startsWith('image/'));
  if(files.length){event.preventDefault();addWishShots(files);}});
$('#wish-delete').onclick=async()=>{
  const button=$('#wish-delete');
  // A second tap confirms; browser confirm dialogs are avoided on phones.
  if(!button.dataset.confirm){button.dataset.confirm='1';button.textContent='再点一次确认删除';return;}
  try{await api('/api/wishlist/delete',{id:editingWish.id});$('#wish-dialog').close();toast('已从想看清单删除');await loadWishlist();}catch(error){onError(error);}
};
function renderDownloadLink(book){
  $('#download-link-panel')?.remove();
  $('.source-details',$('#detail-content')).insertAdjacentHTML('beforebegin',`<div id="download-link-panel" class="download-link-panel"><label class="field-label" for="book-link">下载地址</label><div class="link-row"><input id="book-link" class="full-input" type="url" placeholder="记下这本书的下载页，https://" value="${escapeHTML(book.download_url||'')}" autocomplete="off" spellcheck="false"><button id="book-link-save" class="button secondary" type="button">保存</button></div><div id="book-link-open">${linkHTML(book.download_url,'打开下载地址')}</div></div>`);
  $('#book-link-save').onclick=async()=>{const button=$('#book-link-save');button.disabled=true;
    try{const result=await api('/api/book/link',{id:book.id,url:$('#book-link').value});book.download_url=result.url;$('#book-link-open').innerHTML=linkHTML(result.url,'打开下载地址');toast(result.url?'下载地址已保存':'已清除下载地址');}catch(error){onError(error);}finally{button.disabled=false;}};
}
$('#mobile-nav').onclick=event=>{
  const button=event.target.closest('[data-mobile]');if(!button)return;const action=button.dataset.mobile;
  if(action==='filter'){if(state.view!=='shelf')setView('shelf');setFilterSheet(!$('#filter-panel').classList.contains('open'));}
  else if(action==='settings'){setFilterSheet(false);settings();}
  else{setView(action);$('.catalog-shell').scrollIntoView({behavior:'smooth'});}
};
// The wish hub (心愿中枢): connect with a pairing code made in the hub; the wishlist then follows the hub's novels.
async function renderHub(){
  try{const hub=await api('/api/hub');
    $('#hub-status').textContent=hub.paired?`已连接 · 想看清单来自心愿中枢（${number(hub.wishes)} 本）${hub.queued?` · ${number(hub.queued)} 项等联网后发送`:''}${hub.error?' · '+hub.error:''}`:'还没有连接。';
    $('#hub-pair').hidden=hub.paired;$('#hub-disconnect').hidden=!hub.paired;
  }catch(error){$('#hub-status').textContent=error.message;}
}
$('#hub-connect').onclick=async()=>{const button=$('#hub-connect');button.disabled=true;
  try{await api('/api/hub/pair',{token:$('#hub-token').value});$('#hub-token').value='';toast('已连接心愿中枢');await renderHub();if(state.view==='wish')await loadWishlist();else refreshWishCount();}
  catch(error){$('#hub-status').textContent=error.message;}finally{button.disabled=false;}};
$('#hub-disconnect').onclick=async()=>{
  const button=$('#hub-disconnect');
  if(!button.dataset.confirm){button.dataset.confirm='1';button.textContent='再点一次确认断开';return;}
  delete button.dataset.confirm;button.textContent='断开连接';
  try{await api('/api/hub/unpair',{});toast('已断开心愿中枢');await renderHub();if(state.view==='wish')await loadWishlist();else refreshWishCount();}catch(error){onError(error);}};
// The hub's 「查看」 opens a matched book here as #book=<id>.
function openHashBook(){const match=location.hash.match(/^#book=(.+)$/);if(!match)return;history.replaceState(null,'',location.pathname+location.search);
  try{openDetail(decodeURIComponent(match[1]));}catch{}}
window.addEventListener('hashchange',openHashBook);
refreshWishCount();openHashBook();
