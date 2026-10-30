'use strict';
const $ = id => document.getElementById(id);
const paths = {
  search:'m20 20-5-5m2-6a6 6 0 1 1-12 0 6 6 0 0 1 12 0',
  settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
  refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2',
  more:'M5 12h.01M12 12h.01M19 12h.01',
  table:'M3 4h18v16H3zM3 9h18M8 4v16M14 9v11M3 14h18',
  image:'M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M16 7h.01',
  save:'M5 3h12l4 4v14H3V3h2M7 3v6h10V3M7 21v-8h10v8',
  restore:'M3 11a9 9 0 1 1 3 8M3 4v7h7M12 7v5l3 2',
  folder:'M3 6h7l2 2h9v12H3z', tag:'M3 3h8l10 10-8 8L3 11zM7 7h.01',
  layers:'m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5',
  info:'M12 11v6M12 7h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  link:'M14 3h7v7M21 3l-10 10M10 5H4v15h15v-6',
  undo:'M3 10h11a7 7 0 0 1 7 7M3 10l5-5M3 10l5 5', redo:'M21 10H10a7 7 0 0 0-7 7M21 10l-5-5M21 10l-5 5',
  download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5', archive:'M3 3h18v5H3zM5 8v13h14V8M9 12h6',
  key:'M14 9a5 5 0 1 1-4-6 5 5 0 0 1 4 6L4 21H1v-3l4-4',
  activity:'M2 12h4l3-9 6 18 3-9h4', trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  sliders:'M4 3v5m0 4v9M12 3v11m0 4v3M20 3v3m0 4v11M1 8h6m2 6h6m2-8h6',
  check:'m5 12 4 4L20 5', x:'m6 6 12 12M6 18 18 6', chevron:'m7 10 5 5 5-5',
  sun:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5',
  moon:'M20 14a9 9 0 0 1-10-11A9 9 0 1 0 20 14', monitor:'M3 3h18v14H3zM8 21h8m-4-4v4'
};
function icon(name) { const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 24 24');svg.classList.add('icon');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[name]||paths.info);svg.append(path);return svg; }
document.querySelectorAll('[data-icon]').forEach(n=>n.prepend(icon(n.dataset.icon)));
function el(tag, cls='', text='') { const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n; }
function field(label, input) {const n=el('div','field');n.append(el('label','',label),input);return n;}
function input(value='', type='text') { const n=el('input');n.type=type;n.value=value;return n; }
function formatBytes(bytes){if(bytes==null)return '未读取';const units=['B','KiB','MiB','GiB'];let n=Number(bytes),i=0;while(n>=1024&&i<3){n/=1024;i++;}return n.toFixed(i?1:0)+' '+units[i];}
function compatibilityText(m){return Object.entries(m.compatibility||{}).filter(([key])=>!key.toLowerCase().includes('mts')).map(([,value])=>typeof value==='object'?JSON.stringify(value):String(value)).join(' / ');}
function note(text) {return el('p','note',text);}
function checkbox(text, checked=false) {const n=el('label','check-line');const box=input('', 'checkbox');box.checked=checked;n.append(box,document.createTextNode(text));return {node:n,box};}
function options(select, values, chosen) {select.replaceChildren(...values.map(v=>{const o=el('option','',v.label??v);o.value=v.value??v;return o;}));if(chosen!==undefined)select.value=chosen;}
const gameViews=new Map();
let state=null, mods=new Map(), selected=new Set(), focusRow='', lastRow='', anchor=-1, flat=[], rowById=new Map(), collapsed=new Set(), nodes=new Map();
let job={version:-1,running:false,kind:'',lines:[]}, busy=0, queue=Promise.resolve(), pollRunning=false, themePending=false;
let toastTimer, searchTimer, scrollFrame=0, dialogResolve=null, currentDialog='', closePending=false;
const media=matchMedia('(prefers-color-scheme: dark)');let currentTheme='system';
function theme(value, notify=false) {currentTheme=value;const dark=value==='dark'||value==='system'&&media.matches;document.documentElement.dataset.colorTheme=dark?'dark':'light';$('theme').value=value;$('themeIcon').replaceChildren(icon(value==='system'?'monitor':dark?'moon':'sun'));if(window.pywebview?.api)window.pywebview.api.request(notify?'theme':'titlebar',notify?{value,dark}:{dark});}
media.addEventListener('change',()=>{if(currentTheme==='system')theme('system');});theme('system');
function toast(text,error=false) {clearTimeout(toastTimer);$('toast').textContent=text;$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?6500:3500);}
function markBusy(value) {busy+=value;$('busyLine').hidden=busy<=0;document.body.classList.toggle('working',busy>0);updateControls();}
function updateControls() {const blocked=busy>0, running=job.running;['scan','smallRefresh','cacheSettings','apiSettings','settings'].forEach(id=>$(id).disabled=blocked||running);document.querySelectorAll('[data-game]').forEach(n=>n.disabled=blocked||running);$('apply').disabled=blocked||running||!state?.ready;$('undo').disabled=blocked||!state?.undo;$('redo').disabled=blocked||!state?.redo;$('master').disabled=blocked||state?.game!=='sts2';['details','workshop','setGroup','manageTags','addTag'].forEach(id=>$(id).disabled=blocked||!selected.size);$('quickTag').closest('.tag-input').hidden=!selected.size;$('restoreProfile').disabled=blocked||!$('profile').value;$('saveProfile').disabled=blocked||!state?.ready;$('releasePacks').disabled=blocked||running;}
function accept(response) {if(response.state&&(!state||response.state.revision>=state.revision))renderState(response.state);if(response.job&&response.job.version>=job.version){job={...job,...response.job};updateJob();}updateControls();}
async function rpc(action,payload={}) {const r=await window.pywebview.api.request(action,payload);if(!r.ok)throw Error(r.error||'操作失败');accept(r);return r.result;}
function act(action,payload={},heavy=false) {const task=async()=>{if(heavy)markBusy(1);try{return await rpc(action,payload);}finally{if(heavy)markBusy(-1);}};const result=queue.then(task);queue=result.catch(()=>{});return result;}
function fire(action,payload={},heavy=false) {return act(action,payload,heavy).catch(e=>toast(e.message,true));}
async function pick(kind) {const r=await window.pywebview.api.file(kind);if(!r.ok)throw Error(r.error);if(r.job){job={...job,...r.job};updateJob();}return r.path;}

function dialog(title, body, buttons=[{label:'关闭',value:'close'}], wide=false, key='') {if($('dialog').open){$('dialog').close();if(dialogResolve)dialogResolve('cancel');}currentDialog=key;$('dialog').classList.toggle('wide',wide);$('dialog').classList.toggle('task-dialog',key==='job');$('dialogTitle').textContent=title;$('dialogBody').replaceChildren(typeof body==='string'?el('pre','text-output',body):body);$('dialogButtons').replaceChildren();const promise=new Promise(resolve=>dialogResolve=resolve);for(const b of buttons){const n=el('button',b.primary?'primary':b.danger?'danger':'',b.label);n.addEventListener('click',()=>finishDialog(b.value));$('dialogButtons').append(n);}$('dialog').showModal();return promise;}
function finishDialog(value='cancel') {if(!$('dialog').open)return;$('dialog').close();currentDialog='';const resolve=dialogResolve;dialogResolve=null;if(resolve)resolve(value);}
$('dialogX').onclick=()=>finishDialog();$('dialog').addEventListener('cancel',e=>{e.preventDefault();finishDialog();});
async function confirm(text,title='请确认') {return await dialog(title,note(text),[{label:'取消',value:false},{label:'确认',value:true,primary:true}])===true;}
async function promptText(title,label,value='') {const box=input(value),body=el('div');body.append(field(label,box));const p=dialog(title,body,[{label:'取消',value:false},{label:'保存',value:true,primary:true}]);setTimeout(()=>{box.focus();box.select();},0);box.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();finishDialog(true);}};return await p===true?box.value:null;}
async function discard() {return !state?.dirty||await confirm('放弃未应用的开关修改？已保存的昵称、标签、分组和开关方案会保留。','有未应用的修改');}

function renderState(s) {const oldGame=state?.game,oldMode=state?.view.mode,switched=oldGame&&oldGame!==s.game;
  if(switched)gameViews.set(oldGame,{selected:[...selected],collapsed:[...collapsed],focusRow,lastRow,anchor,scroll:$('viewport').scrollTop});
  state=s;mods=new Map(s.mods.map(m=>[m.key,m]));
  if(switched){const saved=gameViews.get(s.game);selected=new Set(saved?.selected||[]);collapsed=new Set(saved?.collapsed||[]);focusRow=saved?.focusRow||'';lastRow=saved?.lastRow||'';anchor=saved?.anchor??-1;nodes.clear();$('rows').replaceChildren();}
  selected=new Set([...selected].filter(k=>mods.has(k)));if(!switched&&oldMode!==s.view.mode)collapsed.clear();
  document.querySelectorAll('[data-game]').forEach(n=>{const on=n.dataset.game===s.game;n.title=(s.dirty_games||[]).includes(n.dataset.game)?'有未应用的开关修改':'';n.classList.toggle('active',on);n.setAttribute('aria-selected',String(on));});
  if(document.activeElement!==$('search'))$('search').value=s.query;$('viewMode').value=s.view.mode;options($('groupFilter'),s.groups,s.group);const profile=$('profile').value;options($('profile'),s.profiles.length?s.profiles:[{label:'暂无方案',value:''}],s.profiles.includes(profile)?profile:s.profiles[0]||'');
  $('groupOrder').hidden=s.view.mode==='不分组';$('groupFolds').hidden=s.view.mode==='不分组';$('groupOrder').textContent=s.view.group_orders?.[s.view.mode]?.length?'手动排序 ↕':'组名 '+(s.view.group_descending?'↓':'↑');$('groupOrder').title='拖动分组行左侧把手调整顺序；点击此处恢复组名排序';$('groupOrder').setAttribute('aria-label','分组名称'+(s.view.group_descending?'倒序，点击改为正序':'正序，点击改为倒序'));document.querySelectorAll('[data-layout]').forEach(n=>{const active=n.dataset.layout===(s.view.layout||'table');n.classList.toggle('active',active);n.setAttribute('aria-pressed',String(active));});$('tableHeader').parentElement.classList.toggle('cover-layout',s.view.layout==='covers');$('hideOld').checked=s.view.hide_unavailable;$('showFolder').checked=s.view.show_folder;$('master').checked=s.master;$('master').parentElement.hidden=s.game!=='sts2';$('totalBadge').textContent=s.counts.total;$('totalBadge').title='当前模组包共占用 '+formatBytes(s.counts.disk_bytes);$('shownCaption').title='当前模组包共占用 '+formatBytes(s.counts.disk_bytes);$('shownCaption').textContent=`显示 ${s.counts.shown} / ${s.counts.total} · ${formatBytes(s.counts.disk_bytes)}`;$('libraryCaption').textContent=`Slay the Spire ${s.game==='sts1'?'I':'II'} · 本地模组`;
  $('status').textContent=s.warning?'Steam 工坊状态未同步 · 更多 → 扫描提示':s.dirty?`开启 ${s.counts.enabled} · ${s.counts.changed} 项开关待应用`:s.status+' · 开启 '+s.counts.enabled;$('status').title=s.warning||s.status;$('statusDot').classList.toggle('warning',!!s.warning||s.dirty);$('changeBadge').hidden=!s.counts.changed;$('changeBadge').textContent=s.counts.changed;
  if(!themePending&&currentTheme!==s.theme)theme(s.theme);renderHeader();flatten();if(switched)$('viewport').scrollTop=gameViews.get(s.game)?.scroll||0;renderVirtual(true);selectionChanged();}
const columnDefs=[['enabled','开关'],['nickname','昵称'],['name','模组名称'],['version','版本'],['group','分组'],['tags','标签'],['folder','文件夹归属'],['source','来源'],['loader','状态']];let headerKey='';
function visibleColumns(){return columnDefs.filter(([key])=>key!=='folder'||state.view.show_folder);}
const columnMinimum={enabled:48,nickname:70,name:70,version:54,group:54,tags:60,folder:80,source:44,loader:76};
let columnDrag=null;
function applyColumnWidths(widths){
  const columns=visibleColumns(),card=$('tableHeader').parentElement;
  const fitted=Object.fromEntries(columns.map(([key])=>[key,Math.max(columnMinimum[key],Number(widths[key])||columnMinimum[key])]));
  const flexible=columns.map(([key])=>key).filter(key=>!['enabled','source','loader'].includes(key));
  const minimum=columns.reduce((sum,[key])=>sum+columnMinimum[key],0);
  const available=Math.max(minimum,$('viewport').clientWidth);
  const total=columns.reduce((sum,[key])=>sum+fitted[key],0),difference=available-total;
  if(difference>0){
    const growing=flexible.filter(key=>['nickname','name','tags','folder'].includes(key));
    const basis=growing.reduce((sum,key)=>sum+fitted[key],0);
    for(const key of growing)fitted[key]+=difference*fitted[key]/basis;
  }else if(difference<0){
    let remaining=-difference;
    for(const candidates of [flexible,['enabled','source','loader']]){
      const capacity=candidates.reduce((sum,key)=>sum+Math.max(0,fitted[key]-columnMinimum[key]),0);
      const taken=Math.min(remaining,capacity);
      if(capacity)for(const key of candidates)fitted[key]-=taken*(fitted[key]-columnMinimum[key])/capacity;
      remaining-=taken;
    }
  }
  card.style.setProperty('--columns',columns.map(([key])=>fitted[key]+'px').join(' '));
  card.style.setProperty('--table-min',minimum+'px');
}

function beginColumnResize(event,index){
  if(event.button!==0)return;event.preventDefault();event.stopPropagation();
  const columns=visibleColumns(),widths=Object.fromEntries(columns.map(([key],i)=>[key,$('headerInner').children[i].getBoundingClientRect().width]));
  columnDrag={x:event.clientX,index,widths,columns,moved:false};
  const handle=event.currentTarget;handle.setPointerCapture(event.pointerId);document.body.classList.add('resizing-columns');
  handle.onpointermove=e=>{
    if(!columnDrag)return;const drag=columnDrag,key=columns[index][0],next=columns[index+1][0];
    const delta=Math.max(columnMinimum[key]-widths[key],Math.min(e.clientX-drag.x,widths[next]-columnMinimum[next]));
    if(Math.abs(delta)>.5)drag.moved=true;
    drag.result={...state.view.column_widths,...widths,[key]:widths[key]+delta,[next]:widths[next]-delta};
    applyColumnWidths(drag.result);
  };
  const finish=()=>{const drag=columnDrag;if(!drag)return;columnDrag=null;document.body.classList.remove('resizing-columns');if(drag.moved)fire('view',{column_widths:drag.result});};
  handle.onpointerup=finish;handle.onlostpointercapture=finish;
  handle.onpointercancel=()=>{if(columnDrag)applyColumnWidths(columnDrag.widths);columnDrag=null;document.body.classList.remove('resizing-columns');};
}
function renderHeader() {
  const key=JSON.stringify([state.game,state.view.show_folder,state.view.sort_column,state.view.descending,state.view.column_widths]);if(key===headerKey)return;headerKey=key;
  const columns=visibleColumns();
  $('headerInner').replaceChildren(...columns.map(([key,label],index)=>{
    const on=state.view.sort_column===key,n=el('button',on?'sorted':'',label+(on?(state.view.descending?' ↓':' ↑'):''));n.dataset.column=key;
    n.onclick=e=>{if(!e.target.closest('.column-resizer'))fire('view',{sort_column:key,descending:on?!state.view.descending:false});};
    if(index<columns.length-1){const handle=el('span','column-resizer');handle.title='拖动调整列宽';handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');handle.setAttribute('aria-label',label+'列宽');handle.onpointerdown=e=>beginColumnResize(e,index);handle.onclick=e=>e.stopPropagation();n.append(handle);}
    return n;
  }));
  $('headerInner').style.gridTemplateColumns='';
  const card=$('tableHeader').parentElement;
  if(state.view.column_widths&&Object.keys(state.view.column_widths).length)applyColumnWidths(state.view.column_widths);
  else{
    const template=state.view.show_folder?'52px minmax(85px,1.15fr) minmax(85px,1.1fr) 66px 65px minmax(75px,1fr) minmax(90px,1fr) 46px 82px':'52px minmax(95px,1.2fr) minmax(95px,1.1fr) 70px 72px minmax(88px,1.1fr) 46px 82px';
    card.style.setProperty('--columns',template);card.style.setProperty('--table-min',state.view.show_folder?'740px':'620px');
  }
}
function shortStatus(mod){if(mod.loader_status.startsWith('预计采用'))return '预计采用';if(mod.loadable)return '可配置';return mod.superseded?'已遮蔽':'需核对';}

function flatten() {flat=[];const drag=listGesture?.kind==='group'&&listGesture.active?listGesture:null;const sections=drag?.previewSections||state.sections;if(sections.length){for(const section of sections){const id='g:'+state.view.mode+':'+section.key;if(drag&&section.key===drag.key){flat.push({id:'placeholder:'+id,type:'placeholder',keys:[],groupId:'',heightOverride:drag.sourceHeight});continue;}flat.push({id,type:'group',section,keys:section.members,groupId:id});if(!collapsed.has(id))for(const key of section.shown)flat.push({id:id+'|'+key,type:'mod',key,keys:[key],groupId:id});}}else for(const key of state.visible)flat.push({id:'m:'+key,type:'mod',key,keys:[key],groupId:''});rowById=new Map(flat.map((r,i)=>[r.id,{...r,index:i}]));flat=flat.map(r=>rowById.get(r.id));let top=0;for(const row of flat){row.top=top;row.height=row.heightOverride||(row.type==='group'?34:(state.view.layout==='covers'?128:42));top+=row.height;}$('spacer').style.height=top+'px';$('empty').hidden=!!flat.length;$('viewport').setAttribute('aria-rowcount',flat.length);}
function controlState(keys) {const targets=keys.map(k=>mods.get(k)).filter(Boolean),available=targets.filter(m=>m.loadable),list=available.length?available:targets;const count=list.filter(m=>m.enabled).length;return count===list.length&&list.length?'on':count?'mixed':'off';}
function switchButton(row) {const n=el('button','switch');n.dataset.action='toggle';n.dataset.state=controlState(row.keys);n.setAttribute('role','switch');n.setAttribute('aria-checked',n.dataset.state==='mixed'?'mixed':String(n.dataset.state==='on'));n.setAttribute('aria-label',row.type==='group'?'切换整个分组':'切换模组');n.title=row.type==='group'?'切换整个分组（含筛选隐藏的组员）':'切换模组';return n;}
function fillRow(n,row) {n.dataset.row=row.id;n.setAttribute('role','row');if(row.type==='placeholder'){n.className='group-slot';n.style.height=row.height+'px';n.replaceChildren(el('span','group-slot-label','拖动分组至此'));return;}if(row.type==='group'){n.className='group-row'+(collapsed.has(row.id)?' collapsed':'');const expander=el('button','expander');expander.dataset.action='collapse';expander.append(icon('chevron'));expander.setAttribute('aria-label','展开或折叠分组');const grip=el('span','group-grip','⠿');grip.title='按住上下拖动分组';grip.setAttribute('aria-label','拖动分组排序');n.replaceChildren(grip,expander,switchButton(row),el('strong','',row.section.label),el('span','group-count',`${row.keys.length} 项 · 开启 ${row.keys.filter(k=>mods.get(k)?.enabled).length}`));}else if(state.view.layout==='covers'){fillCoverRow(n,row);}else{const m=mods.get(row.key);n.className='grid-row mod-row'+(m.changed?' changed':'')+(!m.loadable?' unavailable':'');const editing=n.querySelector('.alias-edit');const cells=[];const control=el('div','switch-cell');control.append(switchButton(row));cells.push(control);for(const [key] of columnDefs.slice(1)){if(key==='folder'&&!state.view.show_folder)continue;const text=key==='name'?m.name:key==='folder'?m.folder_label:key==='source'?(m.source==='steam_workshop'?'工坊':'本地'):key==='loader'?shortStatus(m):key==='nickname'?m.nickname:key==='tags'?'':m[key]||(key==='group'?'未分组':'—');const cell=el('div','cell '+(key==='name'?'modname':key==='loader'?'loader':key));cell.dataset.column=key;if(['version','group','tags'].includes(key))cell.dataset.manageable='true';cell.title=key==='tags'?m.tags.join('，'):key==='loader'?m.loader_status:text;if(key==='tags')for(const tag of m.tags)cell.append(el('span','tag-chip',tag));else if(key==='nickname'&&editing)cell.append(editing);else {cell.textContent=text;if(key==='name'||key==='version'){const sub=key==='name'?(m.game==='sts1'||state.game==='sts1'?'文件 ':'目录共 ')+formatBytes(m.disk_bytes):compatibilityText(m)?'适用 '+compatibilityText(m):'';if(sub){const main=el('span','cell-main',text);cell.replaceChildren(main,el('small','cell-secondary',sub));cell.title=text+'\n'+sub;}}}cells.push(cell);}for(const cell of cells)if(cell.dataset.manageable)cell.title+='\n双击管理';n.replaceChildren(...cells);}n.classList.toggle('selected',row.type==='mod'?selected.has(row.key):row.keys.length>0&&row.keys.every(k=>selected.has(k)));}
const coverImages=new Map();
function coverError(message,code){return Object.assign(Error(message),{code});}
function cachedCover(game,id){
  const key=game+':'+id;
  if(!coverImages.has(key)){
    const request=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(coverError('本地读取超时','read_timeout')),7000);
      Promise.resolve().then(()=>window.pywebview.api.cover(game,id)).then(result=>{
        clearTimeout(timer);
        const value=typeof result==='string'?result:result?.data;
        if(value)resolve(value);else reject(coverError(result?.reason||'本地读取返回空结果',result?.code||'empty_result'));
      },()=>{clearTimeout(timer);reject(coverError('本地读取接口异常','read_error'));});
    }).catch(error=>{if(coverImages.get(key)===request)coverImages.delete(key);throw error;});
    coverImages.set(key,request);
  }
  return coverImages.get(key);
}
function showCoverState(art,status,reason=''){
  art.dataset.state=status;art.dataset.reason=reason;
  const item=art._cover;
  if(item){
    item.label.textContent=status==='loaded'?'':status==='loading'?'封面加载中':status==='retry'?'正在重试封面\n'+reason:'封面加载失败\n'+reason;
    art.classList.toggle('has-image',status==='loaded');
    art.title=reason?reason+'（尝试 '+item.attempts+'/3）':'';
  }
  const row=art.closest('.cover-row'),failed=status==='failed'||status==='retry';
  if(row){row.classList.toggle('cover-error',failed);const badge=row.querySelector('.cover-failure-badge');if(badge){badge.hidden=!failed;badge.textContent=status==='retry'?'! 封面重试':'! 封面异常';badge.title=reason;}}
}
function loadCover(art){
  const item=art?._cover;if(!item||!art.isConnected)return;
  const image=item.image;
  if(image.complete&&image.naturalWidth>0){showCoverState(art,'loaded');return;}
  if(item.pending||item.attempts>=3)return;
  item.pending=true;item.attempts++;
  showCoverState(art,'loading');
  cachedCover(item.game,item.id).then(src=>{
    if(image.src!==src)image.src=src;
    return Promise.race([
      image.decode().catch(()=>{throw coverError('图片解码失败','decode_failed');}),
      new Promise((_,reject)=>{item.decodeTimer=setTimeout(()=>reject(coverError('图片解码超时','decode_timeout')),5000);})
    ]);
  }).then(()=>{if(image.naturalWidth>0)showCoverState(art,'loaded');else throw coverError('图片内容无效','invalid_image');}).catch(error=>{
    coverImages.delete(item.game+':'+item.id);
    art.dataset.errorCode=error.code||'unknown';
    showCoverState(art,item.attempts<3?'retry':'failed',error.message||'封面加载异常');
    if(item.attempts<3)setTimeout(()=>loadCover(art),300*item.attempts);
  }).finally(()=>{clearTimeout(item.decodeTimer);item.pending=false;});
}
function fillCoverRow(n,row){
  const mod=mods.get(row.key);n.className='mod-row cover-row'+(mod.changed?' changed':'')+(!mod.loadable?' unavailable':'');
  const control=el('div','switch-cell');control.append(switchButton(row));
  let art=n.querySelector('.cover-art');
  const available=mod.workshop_id&&state.cover_ids.includes(mod.workshop_id),coverKey=state.game+':'+mod.workshop_id;
  if(!art||art.dataset.coverKey!==coverKey||Boolean(art._cover)!==Boolean(available)){
    art=el('div','cover-art');art.dataset.coverKey=coverKey;
    const label=el('span','cover-placeholder',available?'封面加载中':mod.workshop_id?'暂无封面':'本地模组');art.append(label);
    if(available){
      const image=el('img');image.alt='';image.decoding='async';image.onload=()=>showCoverState(art,'loaded');art.append(image);
      art._cover={game:state.game,id:mod.workshop_id,image,label,pending:false,attempts:0};
    }
  }
  const info=el('div','cover-info'),name=el('div','cell nickname',mod.nickname);name.dataset.column='nickname';name.title=mod.nickname;
  const version=el('div','cover-version','版本 '+(mod.version||'未知')),badge=el('span','cover-failure-badge');badge.hidden=true;badge.setAttribute('role','status');version.dataset.column='version';version.dataset.manageable='true';version.title='双击查看可取回的历史版本';version.append(el('span','cover-storage',(state.game==='sts1'?'文件 ':'目录共 ')+formatBytes(mod.disk_bytes)));const compatibility=compatibilityText(mod);if(compatibility){const fit=el('span','cover-compatibility','适用 '+compatibility);fit.title=compatibility;version.append(fit);}version.append(badge);info.append(name,version);
  if(mod.tags.length){const tags=el('div','cover-tags');tags.dataset.column='tags';tags.dataset.manageable='true';tags.title='双击管理标签';for(const tag of mod.tags)tags.append(el('span','tag-chip',tag));info.append(tags);}
  const status=el('span','cover-status',shortStatus(mod));status.title=mod.loader_status;
  n.replaceChildren(control,art,info,status);
  showCoverState(art,art.dataset.state||'missing',art.dataset.reason||'');
}
let renderedStart=-1,renderedEnd=-1,rowRenderVersion=0;
function renderVirtual(force=false) {
  scrollFrame=0;if(!state)return;syncScrollRail();if(force)rowRenderVersion++;
  const viewport=$('viewport');let first=0;while(first<flat.length&&flat[first].top+flat[first].height<=viewport.scrollTop)first++;let last=first;while(last<flat.length&&flat[last].top<viewport.scrollTop+viewport.clientHeight)last++;const start=Math.max(0,first-7),end=Math.min(flat.length,last+7);
  const width=viewport.clientWidth+'px';if($('headerInner').style.width!==width){$('headerInner').style.width=width;if(!columnDrag&&state.view.column_widths&&Object.keys(state.view.column_widths).length)applyColumnWidths(state.view.column_widths);}
  $('tableHeader').scrollLeft=viewport.scrollLeft;
  // Pixel wheel movement within the same range needs no DOM mutations.
  if(!force&&start===renderedStart&&end===renderedEnd)return;
  const items=flat.slice(start,end),wanted=new Set(items.map(r=>r.id));
  for(const child of [...$('rows').children])if(!wanted.has(child.dataset.row))child.remove();
  for(let i=0;i<items.length;i++){
    const row=items[i];let n=nodes.get(row.id);
    if(!n){n=el('div');nodes.set(row.id,n);}
    // Offscreen cached nodes must catch up after layout or record changes too.
    if(n.dataset.renderVersion!==String(rowRenderVersion)){fillRow(n,row);n.dataset.renderVersion=String(rowRenderVersion);}
    // Alternating backgrounds follow logical indices, not the recycled DOM window.
    n.classList.toggle('stripe',row.index%2===1);n.classList.toggle('focus-item',row.type==='mod'&&row.id===lastRow);
    n.classList.toggle('selected',row.type==='mod'?selected.has(row.key):row.keys.length>0&&row.keys.every(k=>selected.has(k)));
    if($('rows').children[i]!==n)$('rows').insertBefore(n,$('rows').children[i]||null);
    if(row.type==='mod'&&state.view.layout==='covers')loadCover(n.querySelector('.cover-art'));
  }
  $('rows').style.transform=`translate3d(0,${flat[start]?.top||0}px,0)`;
  renderedStart=start;renderedEnd=end;
  if(nodes.size>250)for(const key of nodes.keys())if(!wanted.has(key))nodes.delete(key);
}
new ResizeObserver(()=>{if(!scrollFrame)scrollFrame=requestAnimationFrame(()=>renderVirtual());}).observe($('viewport'));
$('viewport').addEventListener('scroll',()=>{if(!scrollFrame)scrollFrame=requestAnimationFrame(()=>renderVirtual());},{passive:true});
function selectionChanged() {$('selectedCount').textContent=selected.size+' 项';for(const n of $('rows').children){const row=rowById.get(n.dataset.row);if(row){n.classList.toggle('selected',row.type==='mod'?selected.has(row.key):row.keys.length>0&&row.keys.every(k=>selected.has(k)));n.classList.toggle('focus-item',row.type==='mod'&&row.id===lastRow);}}updateControls();}
function choose(row,event) {if(event.shiftKey&&anchor>=0){if(!event.ctrlKey)selected.clear();for(const r of flat.slice(Math.min(anchor,row.index),Math.max(anchor,row.index)+1))if(r.type==='mod')selected.add(r.key);}else if(event.ctrlKey||event.metaKey){const remove=row.keys.every(k=>selected.has(k));for(const key of row.keys)remove?selected.delete(key):selected.add(key);anchor=row.index;}else{selected=new Set(row.keys);anchor=row.index;}focusRow=row.id;lastRow=row.id;selectionChanged();$('viewport').focus({preventScroll:true});}
$('rows').addEventListener('click',event=>{if(suppressGroupClick)return;const n=event.target.closest('[data-row]');if(!n||event.target.closest('input'))return;const row=rowById.get(n.dataset.row);if(!row)return;const action=event.target.closest('[data-action]')?.dataset.action;if(action==='collapse'){collapsed.has(row.id)?collapsed.delete(row.id):collapsed.add(row.id);flatten();renderVirtual(true);return;}choose(row,event);if(action==='toggle')fire('toggle',{keys:row.keys});});
$('rows').addEventListener('dblclick',event=>{const groupNode=event.target.closest('.group-row');if(groupNode&&state.view.mode==='自定义分组'&&!event.target.closest('button,.group-grip')){const group=rowById.get(groupNode.dataset.row);if(group?.section.key){renameCustomGroup(group.section.key);}return;}const property=event.target.closest('[data-column]'),owner=event.target.closest('[data-row]'),item=owner&&rowById.get(owner.dataset.row);if(item?.type==='mod'&&['version','group','tags'].includes(property?.dataset.column)){choose(item,{});if(property.dataset.column==='version')fire('details',{key:item.key}).then(r=>{if(r)showVersions(r.versions);});else $(property.dataset.column==='group'?'setGroup':'manageTags').click();return;}const cell=event.target.closest('[data-column="nickname"]'),n=event.target.closest('[data-row]');if(!cell||!n||cell.querySelector('input'))return;const row=rowById.get(n.dataset.row);if(!row||row.type!=='mod')return;const m=mods.get(row.key),box=input(m.nickname);box.className='alias-edit';let finished=false;const finish=save=>{if(finished)return;finished=true;document.removeEventListener('pointerdown',outside,true);const value=box.value;const owner=box.parentElement;if(owner)owner.textContent=save?(value.trim()||m.name):m.nickname;if(save)fire('alias',{keys:[row.key],value});};const outside=e=>{if(!box.contains(e.target))finish(true);};document.addEventListener('pointerdown',outside,true);cell.replaceChildren(box);box.focus();box.select();box.addEventListener('click',e=>e.stopPropagation());box.onkeydown=e=>{if(e.isComposing||e.keyCode===229)return;if(e.key==='Enter'){e.preventDefault();finish(true);}if(e.key==='Escape'){e.preventDefault();finish(false);}};// Save only when a pointer press starts outside, never on a selection drag release.
});

document.querySelectorAll('[data-game]').forEach(n=>n.onclick=async()=>{if(n.dataset.game===state?.game)return;fire('game',{game:n.dataset.game},true);});
async function cacheSettings(first=false){const body=el('div'),enabled=checkbox('更新列表时为新版本保存文件缓存（额外占用大量磁盘）',!!state.backup_cache_enabled);body.append(note('更新只增量补充或更新模组信息，不会主动删除已下架、取消订阅的模组文件，也不会替你取消订阅。Steam 自己已经移除的文件，需要从备份取回。'),enabled.node,note('缓存可导出成备份包上传网盘，之后仅下载所需的一包即可取回文件。此选择会记住；以后可在右上角“更新与备份设置”修改。'));if(await dialog(first?'首次更新：选择是否缓存旧版':'更新与备份设置',body,[{label:'取消',value:false},{label:first?'记住并更新':'保存设置',value:true,primary:true}],true)!==true)return false;await fire('cache_options',{enabled:enabled.box.checked});return true;}
$('cacheSettings').onclick=()=>cacheSettings();
$('smallRefresh').onclick=async()=>{await fire('refresh_small');showProgress();};
$('scan').onclick=async()=>{if(!state.backup_cache_configured&&!await cacheSettings(true))return;if(await discard()){await fire('scan',{discard:true},true);showProgress();}};
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{$('viewport').scrollTop=0;fire('view',{query:$('search').value});},100);});
document.querySelectorAll('[data-layout]').forEach(n=>n.onclick=()=>{if(n.dataset.layout===(state?.view.layout||'table'))return;$('viewport').scrollTop=0;fire('view',{layout:n.dataset.layout});});
async function coverOptions(){const refresh=checkbox('重新获取已缓存的封面（默认只补齐缺失项）'),body=el('div');body.append(note('按当前游戏的工坊编号下载封面，同目录模组共用图片。图片保存在本地，停止后保留已完成部分。'),refresh.node);if(await dialog('下载工坊封面',body,[{label:'取消',value:false},{label:'开始获取',value:true,primary:true}])===true){await fire('covers_start',{refresh:refresh.box.checked});showProgress();}};
$('groupOrder').onclick=()=>fire('view',{group_order:[],group_descending:state.view.group_orders?.[state.view.mode]?.length?false:!state.view.group_descending});
$('viewMode').onchange=()=>{$('viewport').scrollTop=0;fire('view',{mode:$('viewMode').value});};$('groupFilter').onchange=()=>fire('view',{group:$('groupFilter').value});$('hideOld').onchange=()=>fire('view',{hide_unavailable:$('hideOld').checked});$('showFolder').onchange=()=>fire('view',{show_folder:$('showFolder').checked});
$('filterOn').onclick=()=>fire('toggle',{keys:state.visible,value:true});$('filterOff').onclick=()=>fire('toggle',{keys:state.visible,value:false});$('master').onchange=()=>fire('master',{value:$('master').checked});
$('theme').onchange=async()=>{themePending=true;const value=$('theme').value;theme(value);try{await act('theme',{value,dark:document.documentElement.dataset.colorTheme==='dark'});}catch(e){toast(e.message,true);}finally{themePending=false;}};
$('addTag').onclick=()=>{if(selected.size&&$('quickTag').value.trim())fire('tags_add',{keys:[...selected],value:$('quickTag').value});};$('quickTag').onkeydown=e=>{if(e.key==='Enter')$('addTag').click();};
$('setGroup').onclick=async()=>{const keys=[...selected],box=input(mods.get(keys[0])?.group||''),existing=el('select'),body=el('div');options(existing,[{label:'选择已有分组…',value:''},...[...new Set(state.mods.map(m=>m.group).filter(Boolean))].sort().map(g=>({label:g,value:g}))],'');existing.onchange=()=>{if(existing.value)box.value=existing.value;};body.append(field('已有分组',existing),field('分组名称（也可输入新名称，留空清除）',box));box.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();finishDialog(true);}};const yes=await dialog('设置分组',body,[{label:'取消',value:false},{label:'保存',value:true,primary:true}]);if(yes===true)fire('group',{keys,value:box.value});};
$('saveProfile').onclick=async()=>{const name=await promptText('保存开关方案','方案名称',$('profile').value);if(!name?.trim())return;const overwrite=state.profiles.includes(name.trim());if(overwrite&&!await confirm('覆盖同名方案“'+name.trim()+'”？'))return;await fire('profile_save',{name:name.trim(),overwrite});$('profile').value=name.trim();};
$('restoreProfile').onclick=()=>fire('profile_restore',{name:$('profile').value});$('deleteProfile').onclick=async()=>{if($('profile').value&&await confirm('删除方案“'+$('profile').value+'”？'))fire('profile_delete',{name:$('profile').value});};
$('profile').onchange=updateControls;$('undo').onclick=()=>fire('undo');$('redo').onclick=()=>fire('redo');
$('dependencies').onclick=async()=>{const r=await fire('dependencies');if(!r)return;dialog('本地依赖检查',`已勾选 ${r.added} 个本地依赖，点击应用后生效。\n\n`+(r.missing.length?'缺少以下依赖，请自行下载：\n'+r.missing.join('\n')+'\n\n':'')+(r.ambiguous.length?'无法自动选择副本：\n'+r.ambiguous.join('\n')+'\n\n':'')+r.issues.join('\n'));};
$('details').onclick=async()=>{if(!selected.size)return;const r=await fire('details',{key:[...selected][0]});if(!r)return;const m=r.mod,body=el('div'),dl=el('dl','details-grid');const fields=[['昵称',m.nickname],['模组名称',m.name],['版本',m.version],['本地占用',formatBytes(m.disk_bytes)+(state.game==='sts1'?'（JAR 文件）':'（清单所在目录共用）')],['完整包占用',formatBytes(m.package_bytes)],['适用版本（作者声明）',Object.keys(m.compatibility||{}).length?JSON.stringify(m.compatibility):'未声明，不能据此保证兼容'],['作者',m.authors],['工坊状态',r.workshop_health?.status==='存疑'?'存疑（已下架为约定标签，并非确认；下次扫描仅复查一次）':r.workshop_health?.status||'未记录'],['模组 ID',m.id],['文件名',m.path.split(/[\\/]/).pop()],['完整路径',m.path],['文件夹归属',m.folder_label],['自定义分组',m.group||'未分组'],['标签',m.tags.join('，')||'无'],['加载状态',m.loader_status],['依赖',JSON.stringify(m.dependencies,null,2)]];for(const [label,value] of fields)dl.append(el('dt','',label),el('dd','',value));body.append(dl);const copies=el('details'),summary=el('summary','',`同 ID 的所有文件（${r.copies.length} 份，含隐藏副本）`);copies.append(summary,...r.copies.map(c=>el('pre','',`${c.version} · ${c.enabled?'开启':'关闭'} · ${c.folder_label}\n${c.path}\n${c.loader_status}`)));body.append(copies);const historyButton=el('button','',`版本与备份记录（${r.versions.length} 条）`);historyButton.onclick=()=>showVersions(r.versions);body.append(historyButton);dialog('详细信息',body,[{label:'关闭',value:'close'}],true);};
$('workshop').onclick=()=>selected.size&&fire('open_workshop',{key:[...selected][0]});

$('apply').onclick=async()=>{const p=await fire('apply_preview',{},true);if(!p)return;const yes=await dialog('应用前预览',p.text,[{label:'取消',value:false},{label:'确认应用',value:true,primary:true}],true);if(yes===true){const r=await fire('apply_commit',{token:p.token},true);if(r)dialog('已应用',`下次启动生效。\n\n配置回退文件（仅开关设置）：\n${r.backup}`+(r.receipt?'\n\n工坊开关记录：\n'+r.receipt:''));}};

async function manageTags() {let scope=selected.size?'selected':'all';for(;;){const keys=scope==='all'?state.mods.map(m=>m.key):[...selected],members=keys.map(k=>mods.get(k)).filter(Boolean),tags=[...new Set(members.flatMap(m=>m.tags))].sort(),body=el('div'),sel=el('select');options(sel,[...(selected.size?[{label:`选中模组（${selected.size} 项）`,value:'selected'}]:[]),{label:'全部模组',value:'all'}],scope);body.append(field('管理范围',sel),note('移除或重命名标签不会删除模组。'));const listing=el('div','tag-list'),boxes=[];for(const tag of tags){const c=checkbox(tag),count=el('small','',members.filter(m=>m.tags.includes(tag)).length+' 项');c.node.append(count);listing.append(c.node);boxes.push([tag,c.box]);}if(!tags.length)listing.append(note('此范围内暂无标签。'));body.append(listing);sel.onchange=()=>finishDialog('scope:'+sel.value);const result=await dialog('管理标签',body,[{label:'关闭',value:'close'},{label:'重命名',value:'rename'},{label:'移除所选标签',value:'remove',primary:true}]);if(String(result).startsWith('scope:')){scope=result.slice(6);continue;}if(result==='close'||result==='cancel')break;const chosen=boxes.filter(([,box])=>box.checked).map(([tag])=>tag);if(!chosen.length){toast('先选择标签');continue;}if(result==='remove')await fire('tags_remove',{keys,tags:chosen});else if(chosen.length===1){const value=await promptText('重命名标签','新标签名称',chosen[0]);if(value?.trim())await fire('tags_rename',{keys,old:chosen[0],value});}else toast('重命名时请选择一个标签');}}
$('manageTags').onclick=manageTags;
async function settings() {if(!await discard())return;const body=el('div'),config=el('select');options(config,state.configs,state.config_path);const configRow=el('div','inline-field'),configPick=el('button','','选择文件');configRow.append(config,configPick);configPick.onclick=async()=>{try{const path=await pick('config');if(path){const values=[...config.options].map(o=>o.value);if(!values.includes(path))values.push(path);options(config,values,path);}}catch(e){toast(e.message,true);}};const install=input(state.game_dir);install.readOnly=true;const installRow=el('div','inline-field'),installPick=el('button','','更改目录');installRow.append(install,installPick);installPick.onclick=async()=>{try{const path=await pick('install');if(path)install.value=path;}catch(e){toast(e.message,true);}};const roots=[...state.workshop_roots],rootList=el('pre','text-output',roots.join('\n')),rootPick=el('button','','添加工坊目录');rootPick.onclick=async()=>{try{const path=await pick('workshop');if(path&&!roots.includes(path)){roots.push(path);rootList.textContent=roots.join('\n');}}catch(e){toast(e.message,true);}};const extras=el('div','settings-extras');for(const [text,action] of [['名称校准（高级）',titleOptions],['封面重新下载（高级）',coverOptions]]){const b=el('button','',text);b.onclick=action;extras.append(b);}body.append(extras,note('塔二的配置账号必须与 Steam 当前登录账号一致。'),field('游戏配置文件',configRow),field('游戏安装目录',installRow),field('工坊根目录',rootList),rootPick);if(await dialog('路径与账户',body,[{label:'取消',value:false},{label:'保存并重新扫描',value:true,primary:true}],true)===true)fire('settings',{config_path:config.value,game_dir:install.value,workshop_roots:roots,discard:true},true);}
$('settings').onclick=settings;
async function account() {const r=await fire('account_info');if(!r)return;const body=el('div'),method=el('select');options(method,[{label:'账号 API（每批最多 50 项，无固定等待）',value:'account_api'},{label:'匿名简体中文网页',value:'web_schinese'}],r.method);const key=input('','password'),remember=checkbox('在本机加密保存 Key',r.saved),replace=el('button','','输入 / 替换 Key'),guide=el('button','','查看使用与安全说明'),forget=el('button','danger','清除本机及本次会话的 Key');key.autocomplete='new-password';key.disabled=!!(r.saved||r.session);key.placeholder=key.disabled?'已设置；不会显示原 Key':'粘贴新的 32 位 Key';replace.onclick=()=>{key.disabled=false;key.value='';key.placeholder='输入新的 Key，原 Key 不会回填';key.focus();};guide.onclick=()=>apiIntro(true);forget.onclick=async()=>{const result=await act('account_forget').catch(e=>{toast(e.message,true);return false;});if(result===false)return;key.value='';finishDialog();toast('本机文件与本次会话 Key 已清除；如需撤销 Key，请到 Steam 官方页面操作。');};body.append(note(r.saved?'当前状态：本机已有加密 Key。界面不提供查看或复制原 Key。':r.session?'当前状态：仅本次会话已设置 Key，退出后不保留。':'当前状态：尚未设置 Key，可稍后配置。'),field('信息获取方式',method),field('新的 Steam Web API Key',key),replace,remember.node,note('Key 不写入日志或备份包。清除本机记录不等于撤销 Steam 官方 Key；需要撤销时请查看说明中的官方入口。'),guide,forget);const yes=await dialog('Steam API 管理',body,[{label:'取消',value:false},{label:'保存设置',value:true,primary:true}],true);if(yes===true){const value=key.value;key.value='';await act('account_save',{method:method.value,key:value,remember:remember.box.checked}).then(()=>toast('API 设置已保存')).catch(e=>toast(e.message,true));}key.value='';}
$('apiSettings').onclick=account;
async function titleOptions(){const body=el('div'),resume=checkbox('从断点继续（取消则全部重查）',true),overwrite=checkbox('全部替换，包括手动修改的昵称');body.append(note('逐项保存标题和昵称。超时或限流时等待重试；停止后保留已完成部分。未勾选“全部替换”时保留手动昵称。'),resume.node,overwrite.node);if(await dialog('批量更新昵称',body,[{label:'取消',value:false},{label:'开始更新',value:true,primary:true}])===true){await fire('titles_start',{resume:resume.box.checked,overwrite:overwrite.box.checked});showProgress();}};
function updateJob() {if(job.kind){$('status').textContent=job.summary;$('status').title=job.summary;}if(currentDialog==='job'){$('jobSummary').textContent=job.summary;$('jobProgress').max=Math.max(1,job.total||1);$('jobProgress').value=job.done||0;const log=$('jobLog'),pinned=log.scrollHeight-log.scrollTop-log.clientHeight<35;if(log.dataset.history!=='true'&&log.dataset.version!==String(job.version)){renderTaskLog(log,job.user_log,(job.lines||[]).join('\n'));if($('jobRawLog'))$('jobRawLog').textContent=(job.lines||[]).join('\n');log.dataset.version=String(job.version);if(pinned)log.scrollTop=log.scrollHeight;}if(job.next==='versions'&&!job.running)$('dialogButtons').lastElementChild.textContent='查看版本记录';const stop=$('dialogButtons').querySelector('[data-stop]');if(stop)stop.disabled=!job.running||!['titles','covers','scan'].includes(job.kind);}updateControls();}
async function showProgress() {const r=await fire('logs');if(!r)return;const body=el('div'),summary=el('div','job-summary'),bar=el('progress'),log=el('div','job-log'),source=el('select'),issues=el('details'),head=el('summary','',`检查提示 · ${r.issues.length} 项`);summary.id='jobSummary';bar.id='jobProgress';log.id='jobLog';summary.textContent=job.kind?job.summary:r.user_log?.rows?.length?'最近任务记录':'暂无后台任务';log.dataset.history=String(!job.kind);renderTaskLog(log,r.user_log,r.text);issues.hidden=!r.issues.length;issues.append(head,el('pre','',r.issues.join('\n\n')||'没有发现问题。'));options(source,[{label:'当前 / 最近任务',value:''},...r.files.map(f=>({label:f.date+' · '+f.name,value:f.name}))],'');source.onchange=async()=>{log.dataset.history=String(!!source.value);if(source.value){const data=await fire('log_read',{name:source.value});if(data){renderTaskLog(log,data.user_log,data.text);$('jobRawLog').textContent=data.text;}}else{log.dataset.history=String(!job.kind);renderTaskLog(log,job.kind?job.user_log:r.user_log,(job.lines||[]).join('\n')||r.text);log.dataset.version='';updateJob();}};const raw=el('details'),rawHead=el('summary','','详细记录（排查用）'),rawText=el('pre','job-log',r.text);rawText.id='jobRawLog';raw.append(rawHead,rawText);body.append(summary,bar,log,field('日志记录',source),issues,raw);const promise=dialog('日志与检查',body,[{label:'导出当前任务日志',value:'export'},{label:'停止任务',value:'stop'},{label:job.running?'收起，后台继续':'关闭',value:'close',primary:true}],true,'job');$('dialogButtons').children[1].dataset.stop='true';updateJob();const result=await promise;if(result==='close'&&job.next==='versions'&&!job.running){allVersions();return;}if(result==='stop'){await fire('job_stop');showProgress();}if(result==='export'){await exportFile('log');showProgress();}}
$('progress').onclick=showProgress;
async function exportFile(kind) {try{const path=await pick(kind);if(path){if(['backup','release_export','release_import','release_import_folder'].includes(kind))showProgress();else toast('已保存：'+path);}}catch(e){toast(e.message,true);}}
$('export').onclick=()=>exportFile('export');

function closeMenus() {$('moreMenu').hidden=true;$('displayMenu').hidden=true;}
$('more').onclick=e=>{e.stopPropagation();const show=$('moreMenu').hidden;closeMenus();$('moreMenu').hidden=!show;};$('display').onclick=e=>{e.stopPropagation();const show=$('displayMenu').hidden;closeMenus();$('displayMenu').hidden=!show;};$('moreMenu').addEventListener('click',e=>{if(e.target.closest('button'))closeMenus();});document.addEventListener('click',e=>{if(!e.target.closest('.popover'))closeMenus();});
document.addEventListener('keydown',event=>{const editing=event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable=true]');if(editing)return;if(event.key==='Escape'){closeMenus();return;}if($('dialog').open)return;const mod=event.ctrlKey||event.metaKey;if((event.key==='ArrowDown'||event.key==='ArrowUp')&&(event.target===$('viewport')||lastRow&&!event.target.closest('button,a,[role=button]'))){event.preventDefault();const items=flat.filter(r=>r.type==='mod'),index=items.findIndex(r=>r.id===(lastRow||focusRow)),step=event.key==='ArrowDown'?1:-1;const row=items[Math.max(0,Math.min(items.length-1,index<0?(step>0?0:items.length-1):index+step))];if(row){choose(row,event);const v=$('viewport');if(row.top<v.scrollTop)v.scrollTop=row.top;else if(row.top+row.height>v.scrollTop+v.clientHeight)v.scrollTop=row.top+row.height-v.clientHeight;renderVirtual();}return;}if(mod&&event.key.toLowerCase()==='a'){event.preventDefault();const row=rowById.get(focusRow),group=row?.groupId?rowById.get(row.groupId):row?.type==='group'?row:null;selected=new Set(group?.section?.shown||state?.visible||[]);selectionChanged();}else if(mod&&event.key.toLowerCase()==='z'){event.preventDefault();fire(event.shiftKey?'redo':'undo');}else if(event.code==='Space'&&event.target.tagName!=='BUTTON'&&selected.size){event.preventDefault();fire('toggle',{keys:[...selected]});}else if(event.key==='/'){event.preventDefault();$('search').focus();}});
window.requestClose=async()=>{if(closePending)return;closePending=true;try{if(job.running&&job.kind==='backup'){toast('备份进行中，请等待完成后退出。');return;}if(job.running&&!await confirm('停止后台获取任务并退出？已完成的结果会保留。','后台任务进行中'))return;if(state?.dirty_games?.length&&!await confirm('退出将放弃'+state.dirty_games.map(g=>g==='sts1'?'塔一':'塔二').join('、')+'未应用的开关修改。已保存的昵称、标签和分组会保留。','有未应用的修改'))return;let r=await window.pywebview.api.close(true);if(r.waiting){markBusy(1);while(r.waiting){await new Promise(resolve=>setTimeout(resolve,300));r=await window.pywebview.api.close(true);}markBusy(-1);}if(!r.ok)toast(r.error,true);}finally{closePending=false;}};
async function poll() {if(pollRunning||!window.pywebview?.api||!state)return;pollRunning=true;try{const r=await window.pywebview.api.request('poll',{revision:state.revision,job_version:job.version});if(r.ok)accept(r);}finally{pollRunning=false;}}
window.addEventListener('pywebviewready',async()=>{markBusy(1);try{await rpc('init');theme(state.theme);if(!state.api_intro_seen)await apiIntro();}catch(e){toast(e.message,true);$('status').textContent=e.message;}finally{markBusy(-1);}setInterval(poll,500);});
window.spire={get state(){return state;},get selected(){return [...selected];},get rows(){return flat;},get job(){return job;},act,theme,renderVirtual,showProgress};

async function showVersions(versions){
 const body=el('div'),search=input('');search.placeholder='搜索模组名称、版本或备份包号…';body.append(search);search.oninput=()=>{for(const row of body.querySelectorAll('.version-entry'))row.hidden=!row.textContent.toLowerCase().includes(search.value.trim().toLowerCase());};body.append(note('可从所选版本取出对应模组目录，或安装到本地 mods。版本相同也可能内容不同。适用版本由作者声明；未声明不等于兼容。安装后仍需更新模组列表、选择开关并预览应用。Steam 后续更新可能覆盖工坊目录。'));
 if(!versions.length)body.append(note('尚无版本记录。如需保留旧版，在更新与备份设置中开启缓存，再更新模组列表。'));
 for(const v of versions){const row=el('div','version-entry'),label=el('pre','',v.mods.map(m=>`${m.nickname||m.name} · ${m.version||'版本未声明'}`).join('\n')+`\n${v.created} · ${v.available?'可以取回':'需要找回备份包'}${v.export?' · '+v.export.part:''}`),button=el('button','','取出模组文件'),details=el('details'),summary=el('summary','','文件信息');details.append(summary,el('pre','',`${v.files} 个文件 · ${v.digest}\n${v.source}\n${v.export?v.export.entry:v.archive||''}`));row.append(label,button,details);body.append(row);button.onclick=()=>extractVersion(v);}

 dialog('版本与备份记录',body,[{label:'关闭',value:'close'}],true);
}
async function allVersions(){const r=await fire('versions');if(r)showVersions(r);};
// Pointer gestures use logical row bounds so virtualization never loses hits.
let listGesture=null, suppressListContext=false, suppressGroupClick=false;
const gestureViewport=$('viewport');
function gesturePoint(e){const r=gestureViewport.getBoundingClientRect();return {x:Math.max(0,Math.min(gestureViewport.clientWidth,e.clientX-r.left)),y:Math.max(0,Math.min(gestureViewport.clientHeight,e.clientY-r.top))+gestureViewport.scrollTop};}
function updateListGesture(){const g=listGesture;if(!g)return;const p=gesturePoint(g.last),r=gestureViewport.getBoundingClientRect();
 if(g.kind==='select'){
  const left=Math.min(g.start.x,p.x),right=Math.max(g.start.x,p.x),top=Math.min(g.start.y,p.y),bottom=Math.max(g.start.y,p.y);
  g.overlay.style.cssText=`left:${r.left+left}px;top:${r.top+Math.max(0,top-gestureViewport.scrollTop)}px;width:${right-left}px;height:${Math.max(0,Math.min(gestureViewport.clientHeight,bottom-gestureViewport.scrollTop)-Math.max(0,top-gestureViewport.scrollTop))}px`;
  selected=new Set(g.base);for(const row of flat)if(row.type==='mod'&&row.top<bottom&&row.top+row.height>top)selected.add(row.key);
  selectionChanged();
 }else{
  if(!g.previewSections)liftGroup(g);
  const dragTop=g.originTop+p.y-g.start.y;
  g.ghost.style.top=(g.last.clientY-g.grabOffset)+'px';
  let moved=false;
  for(let pass=0;pass<g.previewSections.length;pass++){
   let top=0;const bounds=g.previewSections.map(section=>{const h=g.heights.get(section.key),b={key:section.key,top,height:h};top+=h;return b;});
   const index=bounds.findIndex(b=>b.key===g.key),previous=bounds[index-1],next=bounds[index+1];
   if(next&&dragTop+g.sourceHeight>next.top+next.height/2+2){const [source]=g.previewSections.splice(index,1);g.previewSections.splice(index+1,0,source);moved=true;}
   else if(previous&&dragTop<previous.top+previous.height/2-2){const [source]=g.previewSections.splice(index,1);g.previewSections.splice(index-1,0,source);moved=true;}
   else break;
  }
  if(moved)animateGroupLayout();

 }
}
function gestureTick(){const g=listGesture;if(!g)return;if(g.active){const r=gestureViewport.getBoundingClientRect(),y=g.last.clientY-r.top;const speed=y<30?-Math.ceil((30-y)/3):y>gestureViewport.clientHeight-30?Math.ceil((y-gestureViewport.clientHeight+30)/3):0;if(speed){gestureViewport.scrollTop+=Math.max(-20,Math.min(20,speed));renderVirtual();updateListGesture();}}g.frame=requestAnimationFrame(gestureTick);}
gestureViewport.addEventListener('pointerdown',e=>{
 if(listGesture||e.target.closest('input,textarea')||e.clientX>=gestureViewport.getBoundingClientRect().left+gestureViewport.clientWidth)return;
 const grip=e.target.closest('.group-grip');if(e.button!==2&&!(e.button===0&&grip))return;
 const row=rowById.get(e.target.closest('[data-row]')?.dataset.row);if(grip&&row?.type!=='group')return;
 const overlay=el('div',grip?'group-drop-marker':'selection-marquee');overlay.hidden=true;document.body.append(overlay);
 listGesture={kind:grip?'group':'select',id:e.pointerId,key:row?.section?.key,start:gesturePoint(e),press:{x:e.clientX,y:e.clientY},last:e,active:false,base:new Set(e.ctrlKey||e.shiftKey?selected:[]),overlay};
 gestureViewport.setPointerCapture(e.pointerId);e.preventDefault();gestureTick();
});
gestureViewport.addEventListener('pointermove',e=>{const g=listGesture;if(!g||g.id!==e.pointerId)return;g.last=e;if(!g.active&&Math.hypot(e.clientX-g.press.x,e.clientY-g.press.y)>4){g.active=true;g.overlay.hidden=false;document.body.classList.add('list-dragging');if(g.kind==='select')suppressListContext=true;else g.overlay.hidden=true;}if(g.active)updateListGesture();});
function endListGesture(e,cancel=false){const g=listGesture;if(!g||g.id!==e.pointerId)return;listGesture=null;cancelAnimationFrame(g.frame);g.overlay.remove();document.body.classList.remove('list-dragging');if(gestureViewport.hasPointerCapture(e.pointerId))gestureViewport.releasePointerCapture(e.pointerId);
 if(cancel&&g.kind==='select'){selected=g.base;selectionChanged();}
 if(g.active&&g.kind==='group'){
  suppressGroupClick=true;setTimeout(()=>suppressGroupClick=false,100);
  if(!cancel&&g.previewSections){
   const mode=state.view.mode,saved=state.view.group_orders?.[mode]||[],visible=g.previewSections.map(section=>section.key);let cursor=0;
   const order=saved.length?saved.map(key=>visible.includes(key)?visible[cursor++]:key):[];
   for(const key of visible)if(!order.includes(key))order.push(key);
   state.sections=g.previewSections;
   flatten();renderVirtual(true);selectionChanged();
   act('view',{group_order:order}).catch(e=>{state.sections=g.originalSections;flatten();renderVirtual(true);toast(e.message,true);});
  }else animateGroupLayout();
  settleGroupGhost(g);
 }
 setTimeout(()=>suppressListContext=false,100);
}
function animateGroupLayout(){
 const before=new Map([...$('rows').children].map(n=>[n.dataset.row,n.getBoundingClientRect().top]));
 for(const n of $('rows').children)n._groupShift?.cancel();
 flatten();renderVirtual(true);
 if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 for(const n of $('rows').children){if(n.classList.contains('group-slot'))continue;const old=before.get(n.dataset.row),now=n.getBoundingClientRect().top;if(old!==undefined&&Math.abs(old-now)>.5)n._groupShift=n.animate([{transform:`translateY(${old-now}px)`},{transform:'translateY(0)'}],{duration:210,easing:'cubic-bezier(.2,.8,.2,1)'});}
}
function liftGroup(g){
 g.originalSections=state.sections;g.previewSections=[...state.sections];g.heights=new Map();const headers=flat.filter(row=>row.type==='group');
 for(let i=0;i<headers.length;i++)g.heights.set(headers[i].section.key,(headers[i+1]?.top||parseFloat($('spacer').style.height))-headers[i].top);
 const source=headers.find(row=>row.section.key===g.key);g.originTop=source.top;g.sourceHeight=g.heights.get(g.key);
 const viewport=gestureViewport.getBoundingClientRect(),node=nodes.get(source.id),rect=node.getBoundingClientRect();g.grabOffset=g.press.y-rect.top;
 const ghost=el('div','lifted-group'+(state.view.layout==='covers'?' cover-layout':''));
 // Keep preview bounded even when a group contains hundreds of expanded mods.
 ghost.style.cssText=`left:${viewport.left+5}px;top:${rect.top}px;width:${gestureViewport.clientWidth-10}px;max-height:${Math.max(34,Math.min(g.sourceHeight,gestureViewport.clientHeight*.65))}px;--columns:${getComputedStyle($('tableHeader').parentElement).getPropertyValue('--columns')};--table-min:0px`;
 const block=flat.filter(row=>row.id===source.id||row.groupId===source.id);
 for(const row of block.slice(0,5)){const existing=nodes.get(row.id);if(existing){const clone=existing.cloneNode(true);clone.removeAttribute('data-row');clone.classList.remove('selected');for(const child of clone.querySelectorAll('input'))child.replaceWith(document.createTextNode(child.value));ghost.append(clone);}}
 if(block.length>5)ghost.append(el('div','lifted-summary',`共 ${source.keys.length} 个模组 · 保持当前展开状态`));
 g.ghost=ghost;document.body.append(ghost);flatten();renderVirtual(true);
}
function settleGroupGhost(g){if(!g.ghost)return;const target=nodes.get('g:'+state.view.mode+':'+g.key);const top=target?.isConnected?target.getBoundingClientRect().top:g.ghost.getBoundingClientRect().top;
 const animation=g.ghost.animate([{transform:'translateY(0) scale(1.015)',opacity:1},{transform:`translateY(${top-parseFloat(g.ghost.style.top)}px) scale(1)`,opacity:0}],{duration:matchMedia('(prefers-reduced-motion: reduce)').matches?0:170,easing:'ease-out',fill:'forwards'});
 animation.finished.catch(()=>{}).finally(()=>g.ghost.remove());
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&listGesture){e.preventDefault();endListGesture({pointerId:listGesture.id},true);}});
gestureViewport.addEventListener('pointerup',e=>endListGesture(e));gestureViewport.addEventListener('pointercancel',e=>endListGesture(e,true));gestureViewport.addEventListener('lostpointercapture',e=>endListGesture(e,true));
gestureViewport.addEventListener('contextmenu',e=>{if(suppressListContext||listGesture?.active)e.preventDefault();});

async function renameCustomGroup(old){const value=await promptText('重命名自定义分组','新名称（整个分组，包括筛选隐藏的模组）',old);if(value?.trim()&&value.trim()!==old){const result=await act('group_rename',{old,value}).then(()=>true).catch(e=>{toast(e.message,true);return false;});if(result){const saved=state.view.group_orders?.['自定义分组']||[];if(saved.includes(old))await fire('view',{group_order:saved.map(k=>k===old?value.trim():k)});}}}
document.addEventListener('pointerdown',e=>{if(e.button!==0||!e.target.closest('.app-shell')||e.target.closest('button,input,select,textarea,a,label,[data-row],dialog,.popover,.toolbar,.batchbar,.layout-switch,#scrollRail'))return;selected.clear();focusRow='';anchor=-1;selectionChanged();e.preventDefault();$('viewport').focus({preventScroll:true});});

function foldAllGroups(close){const mode=state.view.mode;if(mode==='不分组')return;const keys=new Set();for(const m of state.mods){if(mode==='自定义分组')keys.add(m.group||'');else if(mode==='文件夹分组')keys.add(m.package_root);else for(const key of m.tags.length?m.tags.map(t=>'tag:'+t):['untagged:'])keys.add(key);}for(const key of keys){const id='g:'+mode+':'+key;if(close)collapsed.add(id);else collapsed.delete(id);}$('viewport').scrollTop=0;flatten();renderVirtual(true);selectionChanged();}
$('collapseAll').onclick=()=>foldAllGroups(true);$('expandAll').onclick=()=>foldAllGroups(false);

// Selection survives loss of window activation; the empty focus cue does not.
function setWindowActive(active){document.body.classList.toggle('window-inactive',!active);if(!active&&!selected.size){lastRow='';selectionChanged();}}
window.addEventListener('blur',()=>setWindowActive(false));
window.addEventListener('focus',()=>setWindowActive(true));

// Ignore delayed scroll events generated by mirroring the list. They must never rewind newer wheel input.
let mirroredRailTop=NaN,railPointerActive=false,railInputUntil=0;
function syncScrollRail(){const v=$('viewport'),rail=$('scrollRail');rail.style.marginTop=$('tableHeader').offsetHeight+'px';rail.style.height=v.clientHeight+'px';$('scrollRailSpace').style.height=v.scrollHeight+'px';rail.style.visibility=v.scrollHeight>v.clientHeight?'visible':'hidden';if(railPointerActive||performance.now()<railInputUntil)return;if(Math.abs(rail.scrollTop-v.scrollTop)>.5){rail.scrollTop=v.scrollTop;mirroredRailTop=rail.scrollTop;}}
$('scrollRail').addEventListener('scroll',()=>{const v=$('viewport'),rail=$('scrollRail');if(Math.abs(rail.scrollTop-mirroredRailTop)<.5)return;mirroredRailTop=rail.scrollTop;if(Math.abs(v.scrollTop-rail.scrollTop)>.5)v.scrollTop=rail.scrollTop;},{passive:true});
$('scrollRail').addEventListener('pointerdown',()=>{railPointerActive=true;},{passive:true});
window.addEventListener('pointerup',()=>{if(railPointerActive){railPointerActive=false;requestAnimationFrame(syncScrollRail);}},{passive:true});
window.addEventListener('pointercancel',()=>{railPointerActive=false;requestAnimationFrame(syncScrollRail);},{passive:true});
$('scrollRail').addEventListener('wheel',()=>{railInputUntil=performance.now()+200;},{passive:true});
$('viewport').addEventListener('wheel',()=>{railInputUntil=0;},{passive:true});


$('releasePacks').onclick=async()=>{const r=await fire('release_info');if(!r)return;const body=el('div');body.append(note(`待导出缓存：${r.pending} 个模组版本。已导出的记录永久保留，删除或移走导出包不会使它重新进入缓存。`),note('导出文件按 V次数.包号 命名，例如 V1.1.zip。每包约 512 MiB，单个大版本会独立成包。导入时选择下载好的 ZIP，位置不限；仅记录位置和校验，不自动安装所有模组。导出完成后清理本地缓存；你可以随意上传、移动或删除这些包。日期和对照记录留在管理器内部，恢复时导入对应一包即可。'));
 for(const batch of [...r.releases].reverse()){const lines=batch.parts.map(p=>`${p.file} · ${formatBytes(p.bytes)} · ${p.versions} 个版本`);body.append(el('pre','',batch.batch+' · '+batch.created+' · 已导出\n'+lines.join('\n')));}
 const action=await dialog('导出备份包',body,[{label:'关闭',value:'close'},{label:'版本与备份记录',value:'history'},{label:'导入备份包',value:'import'},{label:'导出新缓存',value:'export',primary:true}],true);
 if(action==='history')allVersions();else if(action==='export')exportFile('release_export');else if(action==='import')chooseBackupPacks();
};

async function extractVersion(v){const body=el('div');body.append(note('只从备份包中取出所选模组目录及其配套文件，不解压包里的其他模组，不覆盖原工坊文件。'),note('选择文件夹：仅取出文件，不改游戏。安装到 mods：放入当前游戏 mods 目录，不覆盖同名内容；随后更新模组列表、选择开启并应用。若订阅版仍启用，请先关闭重复版本；适配与依赖以模组声明为准。'),note(v.export?'需要的备份包：'+v.export.part+'。若已移走，请先在“备份包 → 导入备份包”选择下载好的 ZIP，位置不限。':'使用本地缓存中的这份版本。'));const action=await dialog('取出模组文件',body,[{label:'取消',value:'cancel'},...(!v.available&&v.export?[{label:'选择所需备份包',value:'locate'}]:[]),{label:'安装到游戏 mods',value:'install'},{label:'选择文件夹取出',value:'extract',primary:true}],true);if(action==='locate'){chooseBackupPacks();return;}if(!['install','extract'].includes(action))return;markBusy(1);try{const result=await window.pywebview.api.file(action==='install'?'version_install':'version_extract',{package:v.package,digest:v.digest});if(!result.ok)throw Error(result.error);if(result.path)dialog('模组文件已取出',result.path+(action==='install'?'\n\n请更新模组列表，选择本地版本开启后预览并应用。':'\n\n文件已保存，游戏配置未修改。'));}catch(e){toast(e.message,true);}finally{markBusy(-1);}}

async function chooseBackupPacks(){const result=await dialog('添加备份包','选择一个或多个 ZIP，或选择保存它们的文件夹。管理器会校验并识别其中的模组与版本，即使原来的本地记录已经丢失也能使用。识别后可在版本记录中取出文件，不会自动安装。',[{label:'取消',value:'cancel'},{label:'选择文件夹',value:'folder'},{label:'选择 ZIP（可多选）',value:'files',primary:true}],true);if(result==='files')exportFile('release_import');else if(result==='folder')exportFile('release_import_folder');}

async function apiIntro(fromManagement=false){const body=el('div');body.append(note('可选：使用自己的 Steam Web API Key，可以批量获取模组标题和封面，比匿名网页查询更快、更稳定。它不负责下载模组；不配置也能管理开关。'),note('获取：打开 Steam 官方 Key 页面 → 登录自己的账号 → 按页面要求填写关联域名并同意条款 → 将 32 位 Key 粘贴到本管理器的账号设置。'),note('请保护好 Key：不要分享、截图或上传 GitHub。默认仅在本次会话使用；选择本机保存时由 Windows 加密。泄露后请在 Steam 官方页面撤销并重新申请。'));const link=el('button','','打开 Steam 官方 Key 页面');link.onclick=()=>fire('api_page');body.append(link);const result=await dialog('可选：用 Steam API 加快信息更新',body,fromManagement?[{label:'返回 API 管理',value:'back',primary:true}]:[{label:'稍后设置',value:'later'},{label:'设置 API',value:'setup',primary:true}],true);await fire('api_intro_seen');if(fromManagement||result==='setup')await account();}

function renderTaskLog(container,view,fallback=''){if(!view?.rows?.length){container.textContent=fallback?'这份记录没有进度概览，可展开下方详细记录查看。':'暂无任务记录';return;}if(!container.querySelector('.task-rows'))container.replaceChildren(el('div','task-rows'),el('div','task-problems'));const rows=container.querySelector('.task-rows'),wanted=new Set();for(const row of view.rows){wanted.add(row.id);let node=[...rows.children].find(n=>n.dataset.id===row.id);if(!node){node=el('div','task-row');node.dataset.id=row.id;rows.append(node);}node.dataset.status=row.status;if(node.textContent!==row.text)node.textContent=row.text;}for(const node of [...rows.children])if(!wanted.has(node.dataset.id))node.remove();const problems=container.querySelector('.task-problems'),pending=(view.problems||[]).filter(p=>/^\d+$/.test(String(p.id))),signature=JSON.stringify(pending);if(problems.dataset.signature!==signature){problems.dataset.signature=signature;problems.replaceChildren();if(pending.length){problems.append(el('strong','',`未完成项目 · ${pending.length} 项`));for(const problem of pending){const mod=state?.mods.find(m=>m.workshop_id===problem.id);problems.append(el('div','task-problem',`${mod?.nickname||'工坊模组'} [${problem.id}] · ${problem.phase}：${problem.reason}`));}}}}
