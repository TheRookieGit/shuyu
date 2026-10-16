const {JSDOM,VirtualConsole}=require('jsdom');
const {spawn}=require('node:child_process');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const child=spawn('python',['-u',path.join(__dirname,'fixture_server.py')],{cwd:root,stdio:['pipe','pipe','pipe'],windowsHide:true});
let dom;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(fn,label){const deadline=Date.now()+6000;while(Date.now()<deadline){if(fn())return;await delay(20);}throw new Error('Timed out: '+label);}
async function run(){
  const base=await new Promise((resolve,reject)=>{let output='';child.stdout.on('data',chunk=>{output+=chunk;const m=output.match(/READY (http:\/\/[^\s]+)/);if(m)resolve(m[1]);});child.on('error',reject);child.on('exit',code=>reject(new Error('Fixture server exited: '+code)));setTimeout(()=>reject(new Error('Fixture startup timed out')),8000).unref();});
  const errors=[],requests=[];
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
  dom=new JSDOM(readFileSync(path.join(root,'web/index.html'),'utf8'),{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  const w=dom.window,d=w.document,$=selector=>d.querySelector(selector);
  w.fetch=(url,options)=>{
    requests.push({url:String(url),options});
    // HTTP and extraction are verified in Python. Avoid launching desktop applications here.
    if(['/api/open','/api/reveal'].includes(String(url)))return Promise.resolve({ok:true,json:async()=>({ok:true,extracted:true})});
    return fetch(new URL(url,base),options);
  };
  w.console.error=(...args)=>errors.push(args.map(String).join(' '));
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){if(!this.open)return;this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
  w.eval(readFileSync(path.join(root,'web/app.js'),'utf8'));
  await waitFor(()=>d.querySelectorAll('.book-card:not(.skeleton)').length===8,'flat library');
  await waitFor(()=>$('#stat-total').textContent.includes('8'),'statistics');
  for(const selector of ['#sidebar','#reader','#continue-banner','.favorite-button','#main-nav','#import-file'])assert.equal($(selector),null,selector+' removed');
  assert.ok(![...d.querySelectorAll('.card-title')].some(el=>el.textContent.includes('损坏')),'no broken archive masquerading as book');
  console.log('PASS catalog only: no reading, history, favorites, annotations or archive placeholders');
  $('#search-input').value='小舟';$('#search-input').dispatchEvent(new w.Event('input',{bubbles:true}));
  await waitFor(()=>d.querySelectorAll('.book-card').length===1&&$('.card-title').textContent==='夜航','deep archive search');
  $('.cover-button').click();await waitFor(()=>$('#detail-dialog').open,'details');
  for(const selector of ['#detail-tags','#detail-notes','.status-picker','#detail-favorite','#save-detail'])assert.equal($(selector),null,selector+' removed');
  $('#reveal-button').click();await waitFor(()=>requests.some(r=>r.url==='/api/reveal'),'locate extracted novel');
  assert.ok(!JSON.parse(requests.find(r=>r.url==='/api/reveal').options.body).original);
  $('#reveal-original').click();await waitFor(()=>requests.filter(r=>r.url==='/api/reveal').length===2,'locate original archive');
  assert.equal(JSON.parse(requests.filter(r=>r.url==='/api/reveal')[1].options.body).original,true);
  $('#system-open').click();await waitFor(()=>requests.some(r=>r.url==='/api/open'),'system open');
  $('[data-close="detail-dialog"]').click();
  console.log('PASS nested novel discovery, open, exact novel location and separate source-archive location');
  $('#reset-filters').click();await waitFor(()=>d.querySelectorAll('.book-card').length===8,'reset search');
  $('[data-kind="comic"]').click();await waitFor(()=>d.querySelectorAll('.book-card').length===2,'comic grouping');
  $('[data-layout="list"]').click();assert.ok($('#book-grid').classList.contains('list-view'));
  $('#issues-button').click();await waitFor(()=>$('#issues-dialog').open,'archive failure details');
  assert.equal(d.querySelectorAll('.archive-issue').length,1);assert.match($('.archive-issue').textContent,/损坏/);
  $('[data-close="issues-dialog"]').click();
  $('#settings-button').click();assert.ok($('#settings-dialog').open);assert.ok($('#root-input').value);assert.ok($('#password-input'));
  $('[data-close="settings-dialog"]').click();
  $('#reset-filters').click();await waitFor(()=>d.querySelectorAll('.book-card').length===8,'all books');
  $('#search-input').value='不存在的书';$('#search-input').dispatchEvent(new w.Event('input',{bubbles:true}));
  await waitFor(()=>!$('#empty-state').hidden,'empty result');$('#empty-action').click();
  await waitFor(()=>d.querySelectorAll('.book-card').length===8,'reset from empty result');
  console.log('PASS filters, display preferences, concrete archive errors, password settings and empty states');
  assert.ok(!requests.some(r=>/\/api\/(update|progress|export|import)/.test(r.url)),'no activity or annotation writes');
  assert.deepEqual(errors,[],'no frontend runtime errors');
  console.log('All catalog integration checks passed (DOM; visual rendering not covered).');
}
run().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  await delay(180);dom?.window.close();
  if(child.exitCode===null){child.stdin.end('stop\n');await Promise.race([new Promise(resolve=>child.once('exit',resolve)),delay(2000)]);if(child.exitCode===null)child.kill();}
});
