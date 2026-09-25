const $=id=>document.getElementById(id);let timer=null;let rows=[];let uiLanguage='he';
function ensureSweetModal(){
  if($('sweetOverlay'))return;
  const style=document.createElement('style');
  style.textContent='.sweet-overlay{position:fixed;inset:0;background:#05070fcc;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;z-index:9999;opacity:0;pointer-events:none;transition:opacity .22s ease;padding:20px}.sweet-overlay.show{opacity:1;pointer-events:auto}.sweet-card{width:min(360px,100%);background:linear-gradient(160deg,#161f37,#0e1526);border:1px solid #3d4d6f;border-radius:18px;padding:26px 24px 20px;text-align:center;box-shadow:0 24px 60px #00000066;transform:scale(.92) translateY(6px);transition:transform .22s cubic-bezier(.34,1.56,.64,1)}.sweet-overlay.show .sweet-card{transform:scale(1) translateY(0)}.sweet-icon{width:52px;height:52px;margin:0 auto 14px;border-radius:50%;display:grid;place-items:center;font-size:22px;font-weight:700}.sweet-icon.info{background:#8266ff22;color:#b2a8ff}.sweet-icon.success{background:#35d39c22;color:#5ee0a0}.sweet-icon.error{background:#ff728922;color:#ff9aaa}.sweet-icon.question{background:#f5ca5b22;color:#f5ca5b}.sweet-message{margin:0 0 20px;color:#dbe6fa;font:14px/1.7 Heebo;white-space:pre-line}.sweet-actions{display:flex;gap:10px;justify-content:center}.sweet-btn{flex:1;max-width:140px;border:0;border-radius:10px;padding:10px 14px;font:600 13px Heebo;cursor:pointer;transition:transform .12s ease,opacity .12s ease}.sweet-btn:active{transform:scale(.96)}.sweet-btn:hover{opacity:.92}.sweet-btn.primary{background:linear-gradient(135deg,#8266ff,#5b71e8);color:#fff}.sweet-btn.secondary{background:#1e2a43;color:#c7d2ea;border:1px solid #344360}.sweet-btn.danger{background:linear-gradient(135deg,#ff8a9b,#d0546f);color:#fff}';
  document.head.append(style);
  const overlay=document.createElement('div');
  overlay.id='sweetOverlay';overlay.className='sweet-overlay';
  overlay.innerHTML='<div class="sweet-card"><div class="sweet-icon" id="sweetIcon"></div><p class="sweet-message" id="sweetMessage"></p><div class="sweet-actions" id="sweetActions"></div></div>';
  document.body.append(overlay);
  overlay.addEventListener('click',e=>{if(e.target===overlay&&window.__sweetCancel)window.__sweetCancel()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&overlay.classList.contains('show')&&window.__sweetCancel)window.__sweetCancel()});
}
const SWEET_ICONS={info:'ℹ',success:'✓',error:'!',question:'?'};
function sweetAlert(message,opts={}){
  ensureSweetModal();
  return new Promise(resolve=>{
    const overlay=$('sweetOverlay'),type=opts.type||'info';
    $('sweetIcon').textContent=opts.icon||SWEET_ICONS[type];
    $('sweetIcon').className='sweet-icon '+type;
    $('sweetMessage').textContent=message;
    $('sweetActions').innerHTML='<button id="sweetOk" class="sweet-btn primary" type="button"></button>';
    $('sweetOk').textContent=opts.okText||'הבנתי';
    overlay.classList.add('show');
    const finish=()=>{overlay.classList.remove('show');resolve(true)};
    $('sweetOk').onclick=finish;
    window.__sweetCancel=finish;
    setTimeout(()=>$('sweetOk').focus(),10);
  });
}
function sweetConfirm(message,opts={}){
  ensureSweetModal();
  return new Promise(resolve=>{
    const overlay=$('sweetOverlay'),type=opts.type||'question';
    $('sweetIcon').textContent=opts.icon||SWEET_ICONS[type];
    $('sweetIcon').className='sweet-icon '+type;
    $('sweetMessage').textContent=message;
    $('sweetActions').innerHTML=`<button id="sweetCancel" class="sweet-btn secondary" type="button"></button><button id="sweetConfirmBtn" class="sweet-btn ${opts.danger?'danger':'primary'}" type="button"></button>`;
    $('sweetCancel').textContent=opts.cancelText||'ביטול';
    $('sweetConfirmBtn').textContent=opts.confirmText||'אישור';
    overlay.classList.add('show');
    const finish=val=>{overlay.classList.remove('show');resolve(val)};
    $('sweetCancel').onclick=()=>finish(false);
    $('sweetConfirmBtn').onclick=()=>finish(true);
    window.__sweetCancel=()=>finish(false);
    setTimeout(()=>$('sweetCancel').focus(),10);
  });
}
let __redirecting=false;
function goToLogin(){if(__redirecting)return;__redirecting=true;location.href='/login'}
async function api(url,method='GET',body=null){
  if(__redirecting)return new Promise(()=>{});
  const headers={'Accept':'application/json'};
  if(body)headers['Content-Type']='application/json';
  const response=await fetch(url,{method,headers,body:body?JSON.stringify(body):null});
  if(response.status===401&&!location.pathname.startsWith('/login')){goToLogin();return new Promise(()=>{})}
  let data={};
  try{data=await response.json()}catch(e){}
  return{ok:response.ok,status:response.status,data};
}
function jsonp(kind){if(__redirecting)return new Promise(()=>{});return new Promise((resolve,reject)=>{const callback=`dashboardData${Date.now()}${Math.floor(Math.random()*10000)}`,script=document.createElement('script'),timer=setTimeout(()=>cleanup(new Error('Data request timed out')),8000);const cleanup=error=>{clearTimeout(timer);delete window[callback];script.remove();error?reject(error):null};window[callback]=data=>{cleanup();if(data&&data.__unauthenticated__){goToLogin();return}resolve(data)};script.onerror=()=>cleanup(new Error('Data request failed'));script.src=`/api/dashboard/${kind}?callback=${callback}`;document.head.append(script)})}
async function getStatus(){try{renderStatus(await jsonp('status'));translatePage(uiLanguage)}catch(e){}}
function renderStatus(s){$('panels').textContent=s.panels??0;$('devices').textContent=s.devices??0;$('numbers').textContent=s.numbers??0;$('links').textContent=s.links??0;$('percent').textContent=(s.progress??0)+'%';$('bar').style.width=(s.progress??0)+'%';$('currentStep').textContent=s.step||'ממתין להתחלה';$('device').textContent=s.current_device||'—';$('mobile').textContent=s.current_number||'—';$('logs').textContent=(s.logs||[]).join('\n');$('logs').scrollTop=$('logs').scrollHeight;$('startBtn').disabled=!!s.running;$('stopBtn').disabled=!s.running;$('live').textContent=s.running?'פעיל':'לא פעיל';$('live').classList.toggle('on',!!s.running);const order=['Loading Panels','Scanning Messages','OTP / Activation','Completed'];document.querySelectorAll('.step').forEach((el,i)=>{el.classList.remove('active','done');const x=order[i];if(s.step===x)el.classList.add('active');if(order.indexOf(s.step)>i||s.step==='Completed')el.classList.add('done');el.querySelector('em').textContent=el.classList.contains('done')?'הושלם':el.classList.contains('active')?'פעיל':'ממתין';});}
async function loadResults(){try{rows=await jsonp('results');renderResults()}catch(e){}}
function statusClass(x){if(x==='activation_url_found')return'success';if(x==='already_active')return'warn';if(x.includes('failed'))return'error';return'info'}
function renderResults(){const q=($('search').value||'').toLowerCase(),filter=$('statusFilter').value;const filtered=rows.filter(x=>JSON.stringify(x).toLowerCase().includes(q)&&(!filter||(x.status||'').toLowerCase().includes(filter)));$('resultCount').textContent=`${filtered.length} תוצאות`;$('results').innerHTML=filtered.map((r,i)=>{const url=r.activation_url||'';return `<tr><td>${r.serial_number||i+1}</td><td dir="ltr">${esc(r.device_id||'')}</td><td dir="ltr">${mask(r.mobile_number||'')}</td><td><span class="badge ${statusClass(r.status||'')}">${esc(r.status||'')}</span></td><td><div class="url" dir="ltr"><span>${esc(url)}</span>${url?`<button class="copy" onclick="copyUrl('${encodeURIComponent(url)}')">העתק</button>`:''}</div></td></tr>`}).join('')||'<tr><td colspan="5" style="text-align:center;color:#697792">אין תוצאות תואמות</td></tr>'}
function mask(x){const d=x.replace(/\D/g,'');return d.length>4?'••••••'+d.slice(-4):x}function esc(x){return String(x).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}window.copyUrl=async x=>{await navigator.clipboard.writeText(decodeURIComponent(x))};
$('startBtn').onclick=async()=>{const r=await api('/api/start','POST',{});const d=r.data;if(!d.ok)await sweetAlert(d.error||'שגיאה',{type:'error'});else{await getStatus();if(timer)clearInterval(timer);timer=setInterval(getStatus,1000)}};
$('stopBtn').onclick=async()=>{await api('/api/stop','POST');await getStatus()};$('exportBtn').onclick=()=>location.href='/api/export';$('refreshBtn').onclick=async()=>{await Promise.all([getStatus(),loadResults()])};$('clearLogsBtn').onclick=()=>{$('logs').textContent=''};$('search').oninput=renderResults;$('statusFilter').onchange=renderResults;loadResults();getStatus();setInterval(loadResults,2500);setInterval(getStatus,1000);


// Sidebar navigation
function initSidebar(){
  const links = document.querySelectorAll('.sidebar nav a[data-target]');
  links.forEach(link => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      const target = document.getElementById(link.dataset.target);
      if (!target) return;
      links.forEach(x => x.classList.remove('active'));
      link.classList.add('active');
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  const sections = [...document.querySelectorAll('[id$="Section"]')];
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting)
      .sort((a,b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    const link = document.querySelector(`.sidebar nav a[data-target="${visible.target.id}"]`);
    if (link) {
      links.forEach(x => x.classList.remove('active'));
      link.classList.add('active');
    }
  }, { rootMargin: '-20% 0px -65% 0px', threshold: [0.1,0.25,0.5] });
  sections.forEach(section => observer.observe(section));
}

async function loadPanelManager(){
  try{
    const panels=await jsonp('panels');
    $('panelList').innerHTML=panels.map(panel=>`<li><span title="${esc(panel.value)}">${esc(panel.value)}</span><button aria-label="הסר Panel" onclick="removePanel(${panel.id})">×</button></li>`).join('')||'<li class="empty-panels">אין Panels עדיין</li>';
    $('panelListCount').textContent=panels.length;
    const inactive=await jsonp('inactive-panels');
    $('inactivePanelList').innerHTML=inactive.map(panel=>`<li><span title="${esc(panel.value)}">${esc(panel.value)}</span><small>${panel.reason==='duplicate'?'Duplicate':'Connection error'}</small></li>`).join('')||'<li class="empty-panels">אין Panels לא פעילים</li>';
    $('inactivePanelListCount').textContent=inactive.length;
    const review=await jsonp('review-panels');
    $('reviewPanelList').innerHTML=review.map(panel=>`<li><span title="${esc(panel.value)}">${esc(panel.value)}</span><button aria-label="הסר Panel" onclick="removePanel(${panel.id})">×</button></li>`).join('')||'<li class="empty-panels">אין Panels לבדיקה</li>';
    $('reviewPanelListCount').textContent=review.length;
    $('panelValid').textContent=panels.length;$('panelInvalid').textContent=inactive.length;$('panelTotal').textContent=panels.length+inactive.length+review.length;
  }catch(e){}
}
window.removePanel=async line=>{
  if(!await sweetConfirm('להסיר את ה-Panel הזה מהרשימה?',{type:'question',danger:true,confirmText:'הסר'}))return;
  const form=document.createElement('form');form.method='post';form.action=`/api/panels/${line}/remove`;form.target='panelActionFrame';document.body.append(form);form.submit();form.remove();setTimeout(loadPanelManager,350);
};
function initPanelManager(){
  const style=document.createElement('style');
  style.textContent='.panel-manager{margin:0 0 18px;padding:0;border:1px solid #3d4d6f;border-radius:16px;background:#111a2d;overflow:hidden}.panel-manager-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:15px 18px;color:#e8eeff;font-size:14px;font-weight:700;border-bottom:1px solid #2a3854}.panel-manager-head b{color:#bdb3ff;background:#8266ff22;border-radius:9px;padding:2px 8px}.panel-manager ul{list-style:none;margin:0;padding:0;max-height:260px;overflow:auto}.panel-manager li{display:flex;gap:12px;align-items:center;padding:10px 18px;color:#aebbd2;font:12px Consolas,monospace;border-bottom:1px solid #26334b}.panel-manager li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;flex:1}.panel-manager li button{border:0;border-radius:6px;background:#ff728917;color:#ff9aaa;font-size:18px;line-height:20px;cursor:pointer}.empty-panels{font-family:Heebo!important;color:#71809b!important}#addPanelForm{display:flex;gap:8px;padding:13px 18px;background:#0b1222}#addPanelForm input{min-width:0;flex:1;border:1px solid #344360;border-radius:8px;padding:9px 10px;color:#dbe6fa;background:#0a1222;font:12px Heebo;outline:0;direction:ltr}#addPanelForm input:focus{border-color:#8266ff}#addPanelForm button{width:38px;border:0;border-radius:8px;color:white;background:#705dde;font-size:21px;cursor:pointer}';
  style.textContent+=' html,body{max-width:100%;overflow-x:hidden}.app{padding-right:256px}.main{margin:0 auto}.sidebar{position:fixed;right:0;top:0;bottom:0;height:100vh;overflow:hidden}.app,.main,.grid,.panel,.results-panel{min-width:0;max-width:100%}.grid{display:flex;flex-direction:column}.pipeline{order:-1}.results-panel{width:100%}.panel-manager li span{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;word-break:break-word}.panel-manager.panels-page{max-width:100%;min-height:520px;margin-top:0}@media(max-width:1100px){.app{padding-right:78px}.main{min-width:0}.panel-manager.panels-page{min-width:0}}@media(max-width:680px){.app{padding-right:0;padding-top:105px}.sidebar{position:fixed;right:0;left:0;top:0;bottom:auto;width:100%;height:105px;z-index:50;overflow:hidden}.sidebar nav{overflow-x:auto;overflow-y:hidden;scrollbar-width:none}.sidebar nav::-webkit-scrollbar{display:none}.panel-manager{margin-bottom:14px;border-radius:12px}.panel-manager.panels-page{min-height:calc(100vh - 155px)}.panel-manager-head{padding:13px 14px}.panel-manager li{padding:10px 14px;gap:8px}.panel-manager li span{max-width:calc(100vw - 115px)}#addPanelForm{padding:11px 14px}.panel-manager ul{max-height:45vh}.table-wrap{max-width:100%;overflow-x:auto}.actions{max-width:100%}.button{min-width:0}}';
  style.textContent+=' .panel-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:16px 18px;border-bottom:1px solid #2a3854}.panel-stats div{min-width:0;background:#0b1324;border:1px solid #2a3854;border-radius:12px;padding:12px 14px}.panel-stats span{display:block;color:#7e8ba5;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.panel-stats b{display:block;margin-top:6px;font-size:26px;color:#e8eeff}.panel-stats .ok b{color:#5ee0a0}.panel-stats .bad b{color:#ff9aaa}@media(max-width:420px){.panel-stats{gap:8px;padding:12px}.panel-stats div{padding:9px 8px}.panel-stats span{font-size:10px}.panel-stats b{font-size:20px;margin-top:4px}}';
  style.textContent+=' #auditPanelsBtn,#auditInactiveBtn,#auditExtractBtn{margin-left:8px;border:1px solid #514b9a;border-radius:7px;padding:5px 9px;color:#d8d3ff;background:#8266ff20;font:11px Heebo;cursor:pointer}.inactive-panel-head,.review-panel-head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;padding:14px 18px;border-top:1px solid #2a3854;border-bottom:1px solid #2a3854;color:#f0b2ba;font-size:13px;font-weight:700}.review-panel-head{color:#e8d187}.inactive-panel-head b{background:#ff728922;color:#ffabb9;border-radius:9px;padding:2px 8px}.review-panel-head b{background:#f5ca5b22;color:#f5ca5b;border-radius:9px;padding:2px 8px}.inactive-panel-head>div,.review-panel-head>div{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.review-panel-note{margin:0;padding:10px 18px;color:#7e8ba5;font-size:11px;border-bottom:1px solid #2a3854}.panel-manager #inactivePanelList li{color:#8190a9}.panel-manager #inactivePanelList small{color:#f0a4b2;font:10px Heebo;white-space:nowrap}@media(max-width:680px){#auditPanelsBtn,#auditInactiveBtn,#auditExtractBtn{padding:4px 6px}.inactive-panel-head,.review-panel-head{padding:12px 14px}.review-panel-note{padding:9px 14px}}';
  style.textContent+=' #bulkAddPanel{padding:0 18px 13px;background:#0b1222}#bulkAddPanel textarea{width:100%;min-height:70px;resize:vertical;border:1px solid #344360;border-radius:8px;padding:9px 10px;color:#dbe6fa;background:#0a1222;font:12px Consolas,monospace;outline:0;direction:ltr}#bulkAddPanel textarea:focus{border-color:#8266ff}#bulkAddPanel button{margin-top:8px;border:1px solid #514b9a;border-radius:8px;padding:7px 12px;color:#d8d3ff;background:#8266ff20;font:12px Heebo;cursor:pointer}#bulkAddPanel button:disabled{opacity:.5;cursor:default}@media(max-width:680px){#bulkAddPanel{padding:0 14px 11px}}';
  document.head.append(style);
  const manager=document.createElement('section');
  manager.className='panel-manager';manager.id='panelsManageSection';
  manager.innerHTML='<div class="panel-stats"><div><span>סה״כ Panels</span><b id="panelTotal">0</b></div><div class="ok"><span>תקינים</span><b id="panelValid">0</b></div><div class="bad"><span>לא תקינים</span><b id="panelInvalid">0</b></div></div><div class="panel-manager-head"><span>ה־Panels שלי</span><div><button id="auditPanelsBtn" type="button">בדוק Panels</button><b id="panelListCount">0</b></div></div><ul id="panelList"></ul><form id="addPanelForm"><input id="newPanel" placeholder="כתובת Panel חדשה" aria-label="כתובת Panel חדשה"><button title="הוסף Panel" type="submit">+</button></form><div id="bulkAddPanel"><textarea id="bulkPanelText" placeholder="הדבק כאן טקסט או מסמך שמכיל לינקים בתוך טקסט רגיל - יחולצו ויתווספו רק הלינקים" aria-label="הדבקת טקסט עם לינקים"></textarea><button id="bulkAddBtn" type="button">חלץ והוסף לינקים</button></div><div class="inactive-panel-head"><span>Panels לא פעילים</span><div><button id="auditInactiveBtn" type="button">בדוק לא תקינים</button><b id="inactivePanelListCount">0</b></div></div><ul id="inactivePanelList"></ul><div class="review-panel-head"><span>Panels לבדיקה</span><div><button id="auditExtractBtn" type="button">בדוק חילוץ לינקים</button><b id="reviewPanelListCount">0</b></div></div><p class="review-panel-note">לינקים שלא הצלחנו לחלץ מהם כתובת Firebase בכלל - כתובות דף בית, לינקים לטלגרם, קיצורי URL וכו׳. הם לא נכנסים לסריקה.</p><ul id="reviewPanelList"></ul>';
  document.querySelector('.main').insertBefore(manager,document.querySelector('.cards'));
  const link=document.createElement('a');link.href='#panelsManageSection';link.innerHTML='<i>▤</i><span>ניהול Panels</span>';document.querySelector('.sidebar nav').append(link);
  const dashboard=document.querySelectorAll('.main > :not(.panel-manager)');
  const showPanels=show=>{manager.classList.toggle('panels-page',show);dashboard.forEach(element=>element.hidden=show);link.classList.toggle('active',show);document.querySelector('.sidebar nav a[data-target="dashboardSection"]').classList.toggle('active',!show);if(show)manager.scrollIntoView({block:'start'})};
  link.addEventListener('click',event=>{event.preventDefault();showPanels(true)});
  document.querySelector('.sidebar nav a[data-target="dashboardSection"]').addEventListener('click',()=>showPanels(false));
  document.querySelectorAll('.sidebar nav a[data-target]').forEach(navLink=>navLink.addEventListener('click',()=>showPanels(false)));
  showPanels(false);
const form=$('addPanelForm'),input=$('newPanel'),frame=document.createElement('iframe');frame.name='panelActionFrame';frame.hidden=true;document.body.append(frame);input.name='value';
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const value=input.value.trim();
    if(!value)return;
    const submitBtn=form.querySelector('button');submitBtn.disabled=true;
    try{
      const r=await api('/api/panels','POST',{value});
      if(r.data.ok){input.value='';loadPanelManager()}
      else await sweetAlert(r.data.error||'לא ניתן להוסיף את ה-Panel',{type:'error'})
    }catch(e){await sweetAlert('שגיאה בהוספת ה-Panel',{type:'error'})}
    submitBtn.disabled=false;
  });
  const runAudit=async(btn,url,idleLabel,onResult)=>{
    if(btn.disabled)return;
    btn.disabled=true;btn.textContent='בודק...';
    try{
      const r=await api(url,'POST',{});
      if(r.data.ok)await sweetAlert(onResult(r.data),{type:'success'});
      else await sweetAlert(r.data.error||'הבדיקה נכשלה',{type:'error'})
    }catch(e){await sweetAlert('שגיאה בביצוע הבדיקה',{type:'error'})}
    btn.disabled=false;btn.textContent=idleLabel;
    loadPanelManager();
  };
  $('auditPanelsBtn').onclick=()=>runAudit($('auditPanelsBtn'),'/api/panels/audit','בדוק Panels',d=>d.moved?`הועברו ${d.moved} Panels ל"לא פעילים" (לא הגיבו לבדיקת החיבור).`:'כל ה-Panels הפעילים הגיבו לבדיקת החיבור, שום דבר לא הועבר.');
  $('auditInactiveBtn').onclick=()=>runAudit($('auditInactiveBtn'),'/api/panels/audit-inactive','בדוק לא תקינים',d=>d.restored?`${d.restored} Panels חזרו להיות זמינים והוחזרו לרשימה הפעילה.`:'אף Panel לא הגיב מחדש - הרשימה הלא פעילה נשארה ללא שינוי.');
  $('auditExtractBtn').onclick=()=>runAudit($('auditExtractBtn'),'/api/panels/audit-extract','בדוק חילוץ לינקים',d=>{
    const parts=[];
    if(d.flagged)parts.push(`${d.flagged} Panels הועברו ל"לבדיקה" (לא ניתן לחלץ מהם כתובת Firebase)`);
    if(d.restored)parts.push(`${d.restored} Panels חזרו לרשימה הפעילה (עכשיו ניתן לחלץ מהם כתובת)`);
    return parts.length?parts.join('. ')+'.':'לא נמצאו שינויים - כל ה-Panels הפעילים תקינים.';
  });
  $('bulkAddBtn').onclick=async()=>{
    const btn=$('bulkAddBtn'),area=$('bulkPanelText'),text=area.value;
    if(!text.trim())return;
    if(btn.disabled)return;
    btn.disabled=true;btn.textContent='מוסיף...';
    try{
      const r=await api('/api/panels/bulk','POST',{text});
      if(r.data.ok){area.value='';await sweetAlert(`נמצאו ${r.data.found} לינקים, נוספו ${r.data.added} חדשים (${r.data.skipped} כבר היו ברשימה)`,{type:'success'});loadPanelManager()}
      else await sweetAlert(r.data.error||'לא נמצאו לינקים בטקסט',{type:'error'})
    }catch(e){await sweetAlert('שגיאה בהוספת הלינקים',{type:'error'})}
    btn.disabled=false;btn.textContent='חלץ והוסף לינקים';
  };
  loadPanelManager();
}

async function loadLinks(){
  try{
    const links=await jsonp('links');
    $('linksList').innerHTML=links.map(l=>`<li><div><span class="link-url" dir="ltr" title="${esc(l.activation_url)}">${esc(l.activation_url)}</span><small dir="ltr">${esc(l.device_id||'')} · ${mask(l.mobile_number||'')}</small></div><button class="copy" onclick="copyUrl('${encodeURIComponent(l.activation_url)}')">העתק</button></li>`).join('')||'<li class="empty-panels">אין לינקים תקינים עדיין</li>';
    $('linksListCount').textContent=links.length;
  }catch(e){}
}
function initLinksManager(){
  const style=document.createElement('style');
  style.textContent='.link-manager{margin:0 0 18px;padding:0;border:1px solid #3d4d6f;border-radius:16px;background:#111a2d;overflow:hidden}.link-manager-head{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;color:#e8eeff;font-size:14px;font-weight:700;border-bottom:1px solid #2a3854}.link-manager-head b{color:#bdb3ff;background:#8266ff22;border-radius:9px;padding:2px 8px}.link-manager ul{list-style:none;margin:0;padding:0;max-height:70vh;overflow:auto}.link-manager li{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 18px;color:#aebbd2;font:12px Consolas,monospace;border-bottom:1px solid #26334b}.link-manager li>div{min-width:0;flex:1;display:flex;flex-direction:column;gap:4px}.link-manager .link-url{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;color:#dbe6fa}.link-manager li small{color:#7e8ba5;font:11px Heebo}.link-manager.panels-page{max-width:100%;min-height:520px;margin-top:0}@media(max-width:1100px){.link-manager.panels-page{min-width:0}}@media(max-width:680px){.link-manager{margin-bottom:14px;border-radius:12px}.link-manager.panels-page{min-height:calc(100vh - 155px)}.link-manager-head{padding:13px 14px}.link-manager li{padding:10px 14px;gap:8px}.link-manager .link-url{max-width:calc(100vw - 130px)}}';
  document.head.append(style);
  const manager=document.createElement('section');
  manager.className='link-manager';manager.id='linksManageSection';manager.hidden=true;
  manager.innerHTML='<div class="link-manager-head"><span>לינקים תקינים</span><b id="linksListCount">0</b></div><ul id="linksList"></ul>';
  document.querySelector('.main').insertBefore(manager,document.querySelector('.cards'));
  const link=document.createElement('a');link.href='#linksManageSection';link.innerHTML='<i>⇗</i><span>לינקים תקינים</span>';document.querySelector('.sidebar nav').append(link);
  loadLinks();
  setInterval(loadLinks,3000);
}

async function loadAutoRuns(){
  try{
    const d=await jsonp('auto-runs'),runs=d.runs||[],en=uiLanguage==='en';
    const label=$('autoRunsIntervalLabel');
    if(label)label.textContent=en?(d.interval_minutes?`Runs automatically every ${d.interval_minutes} minutes`:'Automatic runs are disabled'):(d.interval_minutes?`רץ אוטומטית כל ${d.interval_minutes} דקות`:'ההרצה האוטומטית כבויה');
    const statusCls={running:'info',completed:'success',stopped:'warn',skipped:'warn',error:'error'};
    const statusLabels=en?{running:'Running',completed:'Completed',stopped:'Stopped',skipped:'Skipped',error:'Error'}:{running:'רץ כרגע',completed:'הושלם',stopped:'נעצר',skipped:'דולג',error:'שגיאה'};
    const statNames=en?{panels:'Panels',devices:'Devices',numbers:'Numbers',links:'Links'}:{panels:'פאנלים',devices:'מכשירים',numbers:'מספרים',links:'לינקים'};
    const fmtTime=t=>t?esc(new Date(t).toLocaleString(en?'en-GB':'he-IL')):'—';
    $('autoRunsList').innerHTML=runs.map(r=>{
      const stats=['panels','devices','numbers','links']
        .filter(key=>r[key]!==null&&r[key]!==undefined)
        .map(key=>`<span>${statNames[key]}: <b>${r[key]}</b></span>`).join('');
      return `<li><div><div class="autorun-times" dir="ltr"><b>${fmtTime(r.started_at)}</b><i>→</i><b>${fmtTime(r.finished_at)}</b></div>${stats?`<div class="autorun-stats">${stats}</div>`:''}${r.message?`<small>${esc(r.message)}</small>`:''}</div><span class="badge ${statusCls[r.status]||'info'}">${statusLabels[r.status]||esc(r.status||'')}</span></li>`;
    }).join('')||`<li class="empty-panels">${en?'No automatic runs yet':'עדיין לא בוצעו הרצות אוטומטיות'}</li>`;
    $('autoRunsListCount').textContent=runs.length;
  }catch(e){}
}
function initAutoRunsManager(){
  const style=document.createElement('style');
  style.textContent='.autorun-manager{margin:0 0 18px;padding:0;border:1px solid #3d4d6f;border-radius:16px;background:#111a2d;overflow:hidden}.autorun-manager-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:15px 18px;color:#e8eeff;font-size:14px;font-weight:700;border-bottom:1px solid #2a3854}.autorun-manager-head>div{display:flex;flex-direction:column;gap:3px}.autorun-manager-head small{color:#7e8ba5;font-size:11px;font-weight:400}.autorun-manager-head b{color:#bdb3ff;background:#8266ff22;border-radius:9px;padding:2px 8px}.autorun-manager ul{list-style:none;margin:0;padding:0;max-height:70vh;overflow:auto}.autorun-manager li{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 18px;color:#aebbd2;font:12px Consolas,monospace;border-bottom:1px solid #26334b}.autorun-manager li>div{min-width:0;display:flex;flex-direction:column;gap:5px}.autorun-manager li b{color:#dbe6fa;font-weight:600}.autorun-manager li small{color:#7e8ba5;font:11px Heebo;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.autorun-manager .badge{flex:0 0 auto}.autorun-times{display:flex;align-items:center;gap:7px;font-size:12px}.autorun-times i{color:#556180;font-style:normal}.autorun-stats{display:flex;flex-wrap:wrap;gap:10px;color:#8492ac;font:11px Heebo}.autorun-stats b{color:#c6d0e2;font-weight:600}.autorun-manager.panels-page{max-width:100%;min-height:520px;margin-top:0}@media(max-width:1100px){.autorun-manager.panels-page{min-width:0}}@media(max-width:680px){.autorun-manager{margin-bottom:14px;border-radius:12px}.autorun-manager.panels-page{min-height:calc(100vh - 155px)}.autorun-manager-head{padding:13px 14px}.autorun-manager li{padding:10px 14px;gap:8px}.autorun-times{flex-wrap:wrap}}';
  document.head.append(style);
  const manager=document.createElement('section');
  manager.className='autorun-manager';manager.id='autoRunsManageSection';manager.hidden=true;
  manager.innerHTML='<div class="autorun-manager-head"><div><span>הרצות אוטומטיות</span><small id="autoRunsIntervalLabel"></small></div><b id="autoRunsListCount">0</b></div><ul id="autoRunsList"></ul>';
  document.querySelector('.main').insertBefore(manager,document.querySelector('.cards'));
  const link=document.createElement('a');link.href='#autoRunsManageSection';link.innerHTML='<i>⏱</i><span>הרצות אוטומטיות</span>';document.querySelector('.sidebar nav').append(link);
  loadAutoRuns();
  setInterval(()=>{if(!$('autoRunsManageSection').hidden)loadAutoRuns()},15000);
}

async function loadDbTable(){
  try{
    const name=$('dbTableSelect').value;
    if(!name)return;
    const t=await jsonp('db-table-'+name);
    $('dbTableHead').innerHTML='<tr>'+t.columns.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>';
    $('dbTableBody').innerHTML=t.rows.map(r=>'<tr>'+r.map(v=>{const x=v===null?'':String(v);return `<td dir="ltr" title="${esc(x)}">${esc(x)}</td>`}).join('')+'</tr>').join('')||`<tr><td colspan="${t.columns.length||1}" style="text-align:center;color:#697792">הטבלה ריקה</td></tr>`;
    $('dbTableCount').textContent=t.total>t.rows.length?`${t.rows.length} מתוך ${t.total}`:t.total;
  }catch(e){}
}
async function loadDbTables(){
  try{
    const tables=await jsonp('db-tables'),select=$('dbTableSelect'),current=select.value;
    select.innerHTML=tables.map(t=>`<option value="${esc(t.name)}">${esc(t.name)} (${t.count})</option>`).join('');
    if(current&&tables.some(t=>t.name===current))select.value=current;
    loadDbTable();
  }catch(e){}
}
async function loadDbBackend(){
  try{
    const b=await jsonp('db-backend');
    $('dbBackendLabel').textContent=`מסד נתונים (${b.backend})`;
  }catch(e){}
}
function initDbViewer(){
  const style=document.createElement('style');
  style.textContent='.db-manager{margin:0 0 18px;border:1px solid #3d4d6f;border-radius:16px;background:#111a2d;overflow:hidden}.db-manager-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;padding:15px 18px;color:#e8eeff;font-size:14px;font-weight:700;border-bottom:1px solid #2a3854}.db-manager-head b{color:#bdb3ff;background:#8266ff22;border-radius:9px;padding:2px 8px}.db-manager-head>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.db-manager-head select{min-width:0;border:1px solid #344360;border-radius:8px;padding:7px 10px;color:#dbe6fa;background:#0a1222;font:12px Consolas,monospace;direction:ltr}.db-scroll{max-height:70vh;overflow:auto}.db-manager table{width:100%;border-collapse:collapse;font:12px Consolas,monospace;color:#aebbd2}.db-manager th{position:sticky;top:0;background:#0b1324;color:#9eaff0;text-align:left;padding:9px 12px;border-bottom:1px solid #2a3854;white-space:nowrap}.db-manager td{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:8px 12px;border-bottom:1px solid #26334b}.db-manager.panels-page{max-width:100%;min-height:520px;margin-top:0}@media(max-width:680px){.db-manager{border-radius:12px}.db-manager.panels-page{min-height:calc(100vh - 155px)}.db-manager-head{padding:13px 14px}.db-manager-head select{flex:1;width:100%}.db-manager td,.db-manager th{max-width:160px;padding:7px 9px;font-size:11px}}';
  document.head.append(style);
  const manager=document.createElement('section');
  manager.className='db-manager';manager.id='dbManageSection';manager.hidden=true;
  manager.innerHTML='<div class="db-manager-head"><span id="dbBackendLabel">מסד נתונים</span><div><select id="dbTableSelect" aria-label="בחירת טבלה"></select> <b id="dbTableCount">0</b></div></div><div class="db-scroll"><table><thead id="dbTableHead"></thead><tbody id="dbTableBody"></tbody></table></div>';
  document.querySelector('.main').insertBefore(manager,document.querySelector('.cards'));
  const link=document.createElement('a');link.href='#dbManageSection';link.innerHTML='<i>▦</i><span>מסד נתונים</span>';document.querySelector('.sidebar nav').append(link);
  $('dbTableSelect').onchange=loadDbTable;
  loadDbBackend();
  loadDbTables();
  setInterval(()=>{if(!$('dbManageSection').hidden)loadDbTables()},4000);
}

function translatePage(lang){
  uiLanguage=lang;
  const en=lang==='en';
  const set=(selector,values)=>document.querySelectorAll(selector).forEach((element,index)=>{if(values[index]!==undefined)element.textContent=values[index]});
  set('.sidebar nav span',en?['Dashboard','Activation results','Manage panels','Valid links','Database','Automatic runs']:['לוח בקרה','תוצאות הפעלה','ניהול Panels','לינקים תקינים','מסד נתונים','הרצות אוטומטיות']);
  set('.card-head span',['Panels','Online Devices','Unique Numbers','Activation Links']);
  set('.card small',en?['Panels loaded for scanning','Available devices found','Unique candidates to check','Activation links found']:['פאנלים שנטענו לסריקה','מכשירים זמינים שנמצאו','מועמדים ייחודיים לבדיקה','קישורי הפעלה שהתגלו']);
  set('#resultsSection .panel-head p',[en?'Scan history is saved in the CSV file.':'היסטוריית תוצאות הסריקה נשמרת בקובץ ה־CSV.']);
  set('#resultsSection th',en?['#','Device ID','Mobile','Status','Activation URL']:['#','Device ID','Mobile','Status','Activation URL']);
  set('#statusFilter option',en?['All statuses','Link found','Already active','Failed']:['כל הסטטוסים','קישור נמצא','כבר פעיל','נכשל']);
  const count=$('resultCount');if(count)count.textContent=en?count.textContent.replace(/ תוצאות$/,' results'):count.textContent.replace(/ results$/,' תוצאות');
  document.querySelectorAll('.copy').forEach(button=>button.textContent=en?'Copy':'העתק');
  const empty=document.querySelector('#results td[colspan]');if(empty)empty.textContent=en?'No matching results':'אין תוצאות תואמות';
  set('#numbersSection h2',[en?'Scan pipeline':'תהליך הסריקה']);
  set('.step strong',en?['Load panels','Scan devices','Check numbers','Finish and save']:['טעינת Panels','סריקת מכשירים','בדיקת מספרים','סיום ושמירה']);
  set('.step small',en?['Collect available panels','Find online devices','Verify OTP and activate','Export results to file']:['איסוף פאנלים זמינים','איתור מכשירים פעילים','אימות OTP והפעלה','ייצוא התוצאות לקובץ']);
  set('.current-box span',[en?'Current device':'מכשיר נוכחי']);
  set('#logsSection .panel-head p',[en?'Scanner output updates automatically.':'פלט הסורק מתעדכן אוטומטית.']);
  const clear=$('clearLogsBtn');if(clear)clear.textContent=en?'Clear view':'נקה תצוגה';
  const manager=document.querySelector('.panel-manager-head span');if(manager)manager.textContent=en?'My panels':'ה־Panels שלי';
  const autoHead=document.querySelector('.autorun-manager-head>div>span');if(autoHead)autoHead.textContent=en?'Automatic runs':'הרצות אוטומטיות';
  const panelInput=$('newPanel');if(panelInput){panelInput.placeholder=en?'New panel address':'כתובת Panel חדשה';panelInput.setAttribute('aria-label',panelInput.placeholder)}
  const addButton=document.querySelector('#addPanelForm button');if(addButton){addButton.title=en?'Add panel':'הוסף Panel';addButton.setAttribute('aria-label',addButton.title)}
  const side=document.querySelector('.side-note div');if(side&&side.firstChild)side.firstChild.nodeValue=en?'Local scanner':'סורק מקומי';
  document.querySelectorAll('.step em').forEach(element=>{if(en)element.textContent=element.classList.contains('done')?'Done':element.classList.contains('active')?'Active':'Waiting'});
  if($('autoRunsManageSection'))loadAutoRuns();
}

function initLanguageToggle(){
  const translations={
    he:{title:'בקרה וניהול סריקה',subtitle:'מעקב אחר פאנלים, מכשירים ותוצאות הפעלה בזמן אמת.',refresh:'רענן',export:'ייצוא קישורי הפעלה',start:'התחל סריקה',stop:'עצור',results:'תוצאות אחרונות',logs:'יומן פעילות חי',search:'חיפוש מספר, מכשיר או סטטוס...',panelPlaceholder:'כתובת Panel חדשה',language:'English'},
    en:{title:'Scanner control center',subtitle:'Monitor panels, devices, and activation results in real time.',refresh:'Refresh',export:'Export activation links',start:'Start scan',stop:'Stop',results:'Latest results',logs:'Live activity log',search:'Search number, device, or status...',panelPlaceholder:'New panel address',language:'עברית'}
  };
  const button=document.createElement('button');button.className='button secondary language-toggle';document.querySelector('.actions').prepend(button);
  const apply=lang=>{const t=translations[lang];document.documentElement.lang=lang;document.documentElement.dir=lang==='he'?'rtl':'ltr';document.querySelector('.header h1').textContent=t.title;document.querySelector('.header p').textContent=t.subtitle;$('refreshBtn').lastElementChild.textContent=t.refresh;$('exportBtn').lastElementChild.textContent=t.export;$('startBtn').lastElementChild.textContent=t.start;$('stopBtn').lastElementChild.textContent=t.stop;document.querySelector('#resultsSection h2').textContent=t.results;document.querySelector('#logsSection h2').textContent=t.logs;$('search').placeholder=t.search;$('newPanel').placeholder=t.panelPlaceholder;button.textContent='◉ '+t.language;button.onclick=()=>{const next=lang==='he'?'en':'he';localStorage.setItem('scanner-language',next);apply(next)};};
  new MutationObserver(()=>translatePage(document.documentElement.lang==='en'?'en':'he')).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  const initialLanguage=localStorage.getItem('scanner-language')||'he';apply(initialLanguage);translatePage(initialLanguage);
}

function initViews(){
  const header=document.querySelector('.header'),cards=$('panelsSection'),grid=document.querySelector('.grid'),pipeline=$('numbersSection'),results=$('resultsSection'),logs=$('logsSection'),panels=$('panelsManageSection'),links=$('linksManageSection'),db=$('dbManageSection'),autoruns=$('autoRunsManageSection');
  const titles={he:{dashboard:['בקרה וניהול סריקה','מעקב אחר פאנלים, מכשירים ותוצאות הפעלה בזמן אמת.'],results:['תוצאות אחרונות','חיפוש, סינון וייצוא של תוצאות הסריקה.'],logs:['יומן פעילות חי','פלט הסורק בזמן אמת.'],panels:['ניהול Panels','הוספה, צפייה והסרה של Panels לסריקה.'],links:['לינקים תקינים','כל קישורי ההפעלה שנמצאו, עם העתקה מהירה.'],db:['מסד נתונים','צפייה בטבלאות מסד הנתונים של הסורק.'],autoruns:['הרצות אוטומטיות','יומן כל ההרצות האוטומטיות של הסורק.']},en:{dashboard:['Scanner control center','Monitor panels, devices, and activation results in real time.'],results:['Latest results','Search, filter, and export scan results.'],logs:['Live activity log','Scanner output in real time.'],panels:['Manage panels','Add, review, and remove panels for scanning.'],links:['Valid links','All activation links found, with quick copy.'],db:['Database','Browse the scanner SQLite tables.'],autoruns:['Automatic runs','Log of every automatic scan trigger.']}};
  const viewForLink=link=>link.dataset.target==='resultsSection'?'results':link.getAttribute('href')==='#panelsManageSection'?'panels':link.getAttribute('href')==='#linksManageSection'?'links':link.getAttribute('href')==='#dbManageSection'?'db':link.getAttribute('href')==='#autoRunsManageSection'?'autoruns':'dashboard';
  const show=view=>{const isDashboard=view==='dashboard';header.hidden=false;cards.hidden=!isDashboard;grid.hidden=!(isDashboard||view==='results');pipeline.hidden=!isDashboard;results.hidden=view!=='results';logs.hidden=!isDashboard;panels.hidden=view!=='panels';links.hidden=view!=='links';links.classList.toggle('panels-page',view==='links');db.hidden=view!=='db';db.classList.toggle('panels-page',view==='db');if(view==='db')loadDbTables();autoruns.hidden=view!=='autoruns';autoruns.classList.toggle('panels-page',view==='autoruns');if(view==='autoruns')loadAutoRuns();const copy=titles[uiLanguage][view];header.querySelector('h1').textContent=copy[0];header.querySelector('p').textContent=copy[1];document.querySelectorAll('.sidebar nav a').forEach(link=>link.classList.toggle('active',viewForLink(link)===view));window.scrollTo({top:0,behavior:'smooth'});};
  document.querySelectorAll('.sidebar nav a').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();show(viewForLink(link))}));
  show('dashboard');
}

document.addEventListener('DOMContentLoaded',()=>{initSidebar();initPanelManager();initLinksManager();initDbViewer();initAutoRunsManager();initLanguageToggle();initViews()});
