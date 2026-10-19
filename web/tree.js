'use strict';
// Folder tree: how many books each folder and archive of the library holds, loaded one level at a time.
function treeCountHTML(counts){
  return `<strong>${number(counts.total)}</strong> 本${counts.unique<counts.total?`<small>去重后 ${number(counts.unique)}</small>`:''}${counts.comics?`<small>漫画 ${number(counts.comics)}</small>`:''}`;
}
function treeLevelHTML(data,depth){
  const largest=Math.max(1,...data.children.map(c=>c.total));
  const folders=data.children.map(child=>{
    const path=JSON.stringify([...data.path,child.name]);
    return `<li class="tree-node" role="treeitem" aria-expanded="false" style="--depth:${depth}"><button class="tree-row" type="button" data-tree-path="${escapeHTML(path)}" ${child.has_children?'':'disabled'}><span class="tree-caret" aria-hidden="true">${child.has_children?'▸':''}</span><span class="tree-icon ${child.kind}">${icon(child.kind==='archive'?'archive':'folder')}</span><span class="tree-name" title="${escapeHTML(child.name)}">${escapeHTML(child.name)}</span><span class="tree-count">${treeCountHTML(child)}</span><span class="tree-bar" style="--share:${(child.total/largest*100).toFixed(1)}%"></span></button><ul class="tree-children" role="group" hidden></ul></li>`;
  }).join('');
  const books=data.books.map(book=>`<li class="tree-book" role="treeitem" style="--depth:${depth}"><button type="button" data-tree-book="${book.canonical}" title="${escapeHTML(book.name)}"><span class="tree-caret"></span><span class="tree-icon">${icon(book.kind==='comic'?'image':'file')}</span><span class="tree-name">${escapeHTML(book.name)}</span></button></li>`).join('');
  const more=data.direct_total>data.books.length?`<li class="tree-more" style="--depth:${depth}">这一层还有 ${number(data.direct_total-data.books.length)} 本没有列出，可以用搜索查找。</li>`:'';
  return folders+books+more||`<li class="tree-more" style="--depth:${depth}">这里没有书。</li>`;
}
async function treeLevel(path){return api('/api/tree?'+new URLSearchParams({path:JSON.stringify(path)}));}
async function openTree(){
  $('#tree-root').innerHTML='<li class="tree-more">正在统计书库文件夹…</li>';$('#tree-summary').textContent='';
  if(!$('#tree-dialog').open)$('#tree-dialog').showModal();
  try{const data=await treeLevel([]);const c=data.counts;
    $('#tree-summary').innerHTML=`<span class="remote-addresses">${escapeHTML(data.root)}</span><br>共 ${number(c.total)} 个小说来源文件，去重后 ${number(c.unique)} 本${c.comics?`，其中漫画 ${number(c.comics)} 部`:''}。数量包含所有子文件夹和压缩包，点击可以展开下一层。`;
    $('#tree-root').innerHTML=treeLevelHTML(data,0);
  }catch(error){$('#tree-root').innerHTML='';onError(error);}
}
$('#tree-button').onclick=openTree;
$('#tree-root').onclick=async event=>{
  const book=event.target.closest('[data-tree-book]');
  if(book){openDetail(book.dataset.treeBook);return;}
  const row=event.target.closest('[data-tree-path]');if(!row||row.disabled)return;
  const node=row.parentElement,children=$('.tree-children',node),open=node.getAttribute('aria-expanded')==='true';
  node.setAttribute('aria-expanded',String(!open));children.hidden=open;
  if(open||children.dataset.loaded)return;
  children.innerHTML=`<li class="tree-more" style="--depth:${Number(node.style.getPropertyValue('--depth'))+1}">正在读取…</li>`;
  try{const data=await treeLevel(JSON.parse(row.dataset.treePath));children.innerHTML=treeLevelHTML(data,data.path.length);children.dataset.loaded='1';}
  catch(error){children.innerHTML='';node.setAttribute('aria-expanded','false');children.hidden=true;onError(error);}
};
