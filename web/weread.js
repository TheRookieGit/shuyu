'use strict';
// WeRead (微信读书), shared by the full page and Lite: the Windows backend signs in once with WeChat, then sends novels to the shelf.
// The sign-in lives on the backend, so both pages show the same account. Uses the page's own $, api, toast and onError.
const weread={pending:null,timer:null,status:null};
const LOGIN_BUSY=['waiting','scanned','checking','verify'];
document.body.insertAdjacentHTML('beforeend',`<dialog id="weread-dialog" class="settings-dialog weread-dialog">
  <h2>微信读书</h2>
  <p id="weread-account"></p>
  <div id="weread-login" hidden>
    <div id="weread-qr" class="weread-qr" hidden></div>
    <p id="weread-login-status" class="field-help hint" role="status"></p>
    <p id="weread-link-row" class="field-help hint" hidden>在手机上没法扫这个码时：<button id="weread-copy" class="text-button" type="button">复制登录链接</button>，发到微信里自己点开确认。</p>
    <form id="weread-verify" hidden><label class="field-label label" for="weread-otp">验证码</label><input id="weread-otp" class="full-input" inputmode="numeric" autocomplete="one-time-code" maxlength="10" required><div class="weread-actions"><button class="button primary" type="submit">确认登录</button></div></form>
    <button id="weread-retry" class="button primary" type="button" hidden>扫码登录</button>
  </div>
  <p class="field-help hint">登录保存在 Windows 电脑上，完整版书屿和书屿 Lite 共用，只用来把书传到你的书架。这是照着微信读书网页版的上传方式做的，不是官方接口；微信读书改版后，可能要等书屿更新才能继续使用。</p>
  <div class="weread-actions"><button id="weread-switch" class="button secondary" type="button" hidden>换一个账号</button><button id="weread-logout" class="button secondary" type="button" hidden>退出微信读书</button><button id="weread-close" class="text-button" type="button">关闭</button></div>
</dialog>`);
const qrSVG=rows=>`<svg viewBox="-4 -4 ${rows.length+8} ${rows.length+8}" role="img" aria-label="微信读书登录二维码" shape-rendering="crispEdges"><rect x="-4" y="-4" width="100%" height="100%" fill="#fff"/><path fill="#000" d="${rows.map((row,y)=>[...row].map((c,x)=>c==='1'?`M${x} ${y}h1v1h-1z`:'').join('')).join('')}"/></svg>`;
function renderWeRead(status){
  weread.status=status;const login=status.login||{},chip=$('#weread-status');
  // The header shows which account is signed in, so it is clear where books will go.
  if(chip){chip.hidden=false;chip.classList.toggle('signed-in',status.signed_in);$('#weread-status-text').textContent=status.signed_in?status.name||'已登录':'未登录';
    chip.title=status.signed_in?`微信读书已登录${status.name?'：'+status.name:''}，点击查看或退出`:'微信读书未登录，点击扫码登录';}
  $('#weread-account').textContent=status.signed_in?`已登录微信读书${status.name?'：'+status.name:''}。可以把书直接传到这个账号的书架。`:'还没有登录微信读书。用微信扫码登录一次，之后就能把书直接传到你的书架。';
  $('#weread-login').hidden=status.signed_in;$('#weread-logout').hidden=$('#weread-switch').hidden=!status.signed_in;
  if(status.signed_in)return;
  const shown=['waiting','scanned'].includes(login.state)&&!!login.qr;
  $('#weread-qr').hidden=$('#weread-link-row').hidden=!shown;
  if(shown&&$('#weread-qr').dataset.link!==login.link){$('#weread-qr').innerHTML=qrSVG(login.qr);$('#weread-qr').dataset.link=login.link;}
  $('#weread-verify').hidden=login.state!=='verify';$('#weread-retry').hidden=LOGIN_BUSY.includes(login.state);
  $('#weread-retry').textContent=login.state==='failed'?'重新获取二维码':'扫码登录';
  $('#weread-login-status').textContent={waiting:'用微信扫一扫这个二维码，然后在手机上确认登录。',scanned:'已扫码，请在手机上确认登录。',checking:'正在登录微信读书…',
    verify:login.error||'微信读书要求输入验证码，请填写微信里收到的验证码。',failed:login.error||'登录没有完成，请重新获取二维码。'}[login.state]||'';
}
async function refreshWeRead(){try{renderWeRead(await api('/api/weread'));}catch{}}
async function pollWeRead(){
  clearTimeout(weread.timer);if(!$('#weread-dialog').open)return;
  try{
    const status=await api('/api/weread');renderWeRead(status);
    if(status.signed_in){if(weread.pending){const pending=weread.pending;weread.pending=null;$('#weread-dialog').close();toast('微信读书已登录');sendToWeRead(...pending);}return;}
    if(LOGIN_BUSY.includes(status.login.state))weread.timer=setTimeout(pollWeRead,2000);
  }catch(error){onError(error);}
}
// Opening the dialog while signed out goes straight to a QR code; signed in, it shows the account.
async function openWeRead(){
  try{
    let status=await api('/api/weread');
    if(!status.signed_in&&!LOGIN_BUSY.includes(status.login.state))status=await api('/api/weread/login',{});
    renderWeRead(status);if(!$('#weread-dialog').open)$('#weread-dialog').showModal();
    clearTimeout(weread.timer);weread.timer=setTimeout(pollWeRead,2000);
  }catch(error){onError(error);}
}
async function wereadAction(...steps){
  try{let status;for(const [path,data] of steps)status=await api(path,data);renderWeRead(status);clearTimeout(weread.timer);weread.timer=setTimeout(pollWeRead,1000);}catch(error){onError(error);}
}
$('#weread-close').onclick=()=>$('#weread-dialog').close();
$('#weread-dialog').addEventListener('close',()=>{clearTimeout(weread.timer);weread.pending=null;});
$('#weread-retry').onclick=()=>wereadAction(['/api/weread/login',{}]);
$('#weread-verify').onsubmit=event=>{event.preventDefault();wereadAction(['/api/weread/login',{otp:$('#weread-otp').value.trim()}]);$('#weread-otp').value='';};
$('#weread-logout').onclick=async()=>{await wereadAction(['/api/weread/logout',{}]);toast('已退出微信读书');};
$('#weread-switch').onclick=()=>wereadAction(['/api/weread/logout',{}],['/api/weread/login',{}]);
$('#weread-copy').onclick=async()=>{
  try{await navigator.clipboard.writeText($('#weread-qr').dataset.link);toast('登录链接已复制，发到微信里点开确认');}catch{toast('没能复制，请改用另一台设备扫码',true);}
};
if($('#weread-status'))$('#weread-status').onclick=openWeRead;
// Signing in or out on the other page shows up here when the reader comes back.
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&weread.status)refreshWeRead();});
authReady.then(refreshWeRead);
async function sendToWeRead(bookId,versionId,button){
  const label=button.innerHTML;button.disabled=true;button.textContent='正在上传到微信读书…';
  const signIn=()=>{weread.pending=[bookId,versionId,button];openWeRead();};
  try{
    let task=await api('/api/weread/upload',{id:bookId,version_id:versionId});
    while(['uploading','importing'].includes(task.state)){
      button.textContent=task.state==='importing'?'微信读书正在导入…':'正在上传到微信读书…';
      await new Promise(resolve=>setTimeout(resolve,2000));
      task=await api('/api/weread/upload?'+new URLSearchParams({task:task.id}));
    }
    if(task.state==='done')toast(`《${task.title}》已传到微信读书书架${weread.status?.name?'（'+weread.status.name+'）':''}`);
    else{toast(task.error||'没有传到微信读书',true);if(task.login){refreshWeRead();signIn();}}
  }catch(error){if(error.wereadLogin)signIn();else onError(error);}
  finally{button.disabled=false;button.innerHTML=label;}
}
