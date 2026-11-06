"""Opt-in checks against the actual WebView2 renderer; smoke data is isolated by the entry point."""
import json
from pathlib import Path
import threading
import time
import traceback


def wait_js(window, expression, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(expression)
        if value:
            return value
        time.sleep(.08)
    raise AssertionError("Browser condition did not become true: " + expression)


def capture(window, path):
    from System import Action
    from System.IO import FileMode, FileStream
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    tasks = []
    streams = []
    def start():
        stream = FileStream(str(path), FileMode.Create)
        streams.append(stream)
        tasks.append(window.native.webview.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream))
    window.native.Invoke(Action(start))
    try:
        tasks[0].GetAwaiter().GetResult()
    finally:
        streams[0].Close()


def emulate_color(window, value):
    from System import Action
    tasks = []
    payload = {"features": [{"name": "prefers-color-scheme", "value": value}]} if value else {"features": []}
    def start():
        tasks.append(window.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setEmulatedMedia", json.dumps(payload)))
    window.native.Invoke(Action(start))
    tasks[0].GetAwaiter().GetResult()


def devtools(window, method, payload):
    """Send browser input, including its native wheel scrolling/default actions."""
    from System import Action
    tasks = []
    def start():
        tasks.append(window.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.dumps(payload)))
    window.native.Invoke(Action(start))
    return json.loads(str(tasks[0].GetAwaiter().GetResult()))


def wheel_probe(window, anchor):
    window.evaluate_js(f"document.getElementById('viewport').style.overflowAnchor='none';document.getElementById('viewport').scrollTop=0")
    time.sleep(.06)
    window.evaluate_js(f"document.getElementById('viewport').style.overflowAnchor='{anchor}';document.getElementById('viewport').scrollTop=420")
    time.sleep(.15)
    settled_start = window.evaluate_js("document.getElementById('viewport').scrollTop")
    point = window.evaluate_js("(()=>{const r=document.getElementById('viewport').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()")
    results = []
    for delta in (42, 42, 42, 42, 3, 1, -42, -42, -42, -42, -3, -1):
        window.evaluate_js("window.wheelSamples=[];window.wheelRecord=true;window.wheelToken=(window.wheelToken||0)+1;{const token=window.wheelToken;requestAnimationFrame(function sample(){if(token!==window.wheelToken)return;window.wheelSamples.push(document.getElementById('viewport').scrollTop);if(window.wheelRecord)requestAnimationFrame(sample)})}")
        devtools(window, 'Input.dispatchMouseEvent', {'type': 'mouseWheel', **point, 'deltaX': 0, 'deltaY': delta})
        time.sleep(.28)
        window.evaluate_js('window.wheelRecord=false')
        samples = window.evaluate_js('window.wheelSamples')
        reverse = sum((b-a) * (1 if delta > 0 else -1) < -.6 for a,b in zip(samples, samples[1:]))
        results.append({'delta':delta, 'reverse_frames':reverse, 'samples':samples})
    window.evaluate_js("document.getElementById('viewport').style.overflowAnchor='none';document.getElementById('viewport').scrollTop=0")
    time.sleep(.06)
    window.evaluate_js("document.getElementById('viewport').style.overflowAnchor=''")
    return {'requested_start':420, 'settled_start':settled_start, 'steps':results}


def inventory_check(window, api, output):
    report={'ok':False}
    try:
        wait_js(window,'Boolean(window.spire?.state?.ready)')
        if not window.evaluate_js('window.spire.state.api_intro_seen'):
            window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
            wait_js(window,"!document.getElementById('dialog').open")
        window.resize(1000,610)
        expected=window.evaluate_js('({total:state.counts.total,local:state.inventory.local,available:state.inventory.available,hidden:state.counts.hidden})')
        widths=window.evaluate_js("[parseFloat(document.querySelector('.inventory-local').style.width),parseFloat(document.querySelector('.inventory-available').style.width)]")
        assert abs(widths[0]-expected['local']/expected['total']*100)<.01
        assert abs(widths[1]-expected['available']/expected['total']*100)<.01
        window.evaluate_js("document.querySelector('.inventory-meter').click()")
        wait_js(window,"document.getElementById('dialogTitle').textContent==='清单与本地资源'")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('本地文件')")
        if expected['hidden']:
            assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('旧副本')")
            window.evaluate_js("window.hiddenProbe=state.mods.find(m=>m.superseded&&!m.enabled);document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"!state.view.hide_unavailable&&state.visible.includes(window.hiddenProbe.key)")
            window.evaluate_js("{const row=window.spire.rows.find(r=>r.key===window.hiddenProbe.key);document.getElementById('viewport').scrollTop=row.top;window.spire.renderVirtual()}")
            assert window.evaluate_js("[...document.querySelectorAll('.mod-row')].some(n=>n.textContent.includes(window.hiddenProbe.nickname)&&n.textContent.includes('已遮蔽'))")
        else:window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        report.update(ok=True,counts=expected,checks=['layer widths match total/local/available','hidden-copy reason dialog and one-click reveal'])
        window.evaluate_js("document.querySelector('.mod-row [data-column=version]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent.includes('历史版本')")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('没有备份')&&Boolean(document.querySelector('.version-local'))")
        assert window.evaluate_js("!document.getElementById('dialogBody').textContent.includes('回滚到此版本')")
        capture(window,Path(output).parent/'local-history-preview.png')
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click();document.querySelector('.mod-row [data-column=name]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='详细信息'")
        report['checks'].append('version double-click opens per-mod history with physical copies and record-only state; name double-click opens details')
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click();{const row=window.spire.rows.find(r=>r.type==='mod'&&state.mods.some(m=>m.key===r.key&&m.enabled));if(row){document.getElementById('viewport').scrollTop=row.top;window.spire.renderVirtual();document.querySelector(`[data-row=\"${row.id}\"] [data-column=version]`).dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));}}")
        wait_js(window,"document.getElementById('dialog').open&&Boolean(document.querySelector('.version-entry.version-active'))")
        assert window.evaluate_js("document.querySelector('.version-entry.version-active').textContent.includes('最新记录')&&document.querySelector('.version-entry.version-active').textContent.includes('启用时预计采用')")
        report['checks'].append('latest record label and green border on selected current record-only version')
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click();{const r=window.spire.rows.find(r=>r.type==='mod'&&state.mods.some(m=>m.key===r.key&&m.enabled));window.predictedProbe=r.key;window.spire.act('toggle',{keys:[r.key],value:false});}")
        wait_js(window,"!state.mods.find(m=>m.key===window.predictedProbe).enabled")
        window.evaluate_js("{const r=window.spire.rows.find(r=>r.key===window.predictedProbe);document.getElementById('viewport').scrollTop=r.top;window.spire.renderVirtual();document.querySelector(`[data-row=\"${r.id}\"] [data-column=version]`).dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));}")
        wait_js(window,"document.getElementById('dialog').open&&Boolean(document.querySelector('.version-entry.version-active'))")
        assert window.evaluate_js("document.querySelector('.version-entry.version-active').textContent.includes('启用时预计采用')")
        window.evaluate_js("{const probe=document.createElement('div');renderTaskLog(probe,{rows:[{id:'completion',status:'success',text:'模组列表更新完成'}],problems:[],changes:{added:[{name:'新增测试',version:'1',workshop_id:'123'}],updated:[],missing:[{name:'消失测试',version:'0'}]}});window.changeProbe=probe.textContent;}")
        assert window.evaluate_js("window.changeProbe.includes('新增测试')&&window.changeProbe.includes('消失测试')")
        report['checks'].append('disabled mod retains predicted green border; finished change details render inline')

        capture(window,Path(output).parent/'local-history-selection-preview.png')
        capture(window,Path(output).parent/'local-inventory-preview.png')
    except Exception:report['error']=traceback.format_exc()
    finally:
        Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        api._allow_close=True;window.destroy()


def first_run_check(window, api, output):
    """Blank records, no game paths or credentials, and no real Steam/network calls."""
    report = {'ok': False, 'checks': []}
    try:
        wait_js(window, "Boolean(window.spire?.state)")
        wait_js(window, "document.querySelector('.brand img')?.complete&&document.querySelector('.brand img').naturalWidth===128")
        report['checks'].append('numbered dual-game sidebar icon decoded')
        wait_js(window, "document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent.includes('Steam API')")
        assert not api._model.mods and not api._model.store.get('backup_cache_enabled', False)
        assert not (api._model.directory / 'credentials').exists()
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        wait_js(window, "window.spire.state.api_intro_seen&&!document.getElementById('dialog').open")
        assert window.evaluate_js("!window.spire.state.ready&&document.getElementById('apply').disabled")
        assert window.evaluate_js("document.getElementById('status').textContent.includes('路径与高级设置')")
        report['checks'].append('empty library: optional API introduction, no key, backups off, apply disabled with setup guidance')
        window.evaluate_js("document.getElementById('more').click();document.getElementById('settings').click()")
        wait_js(window, "document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent.includes('路径')")
        assert window.evaluate_js("document.querySelectorAll('#dialogBody input').length>0")
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        window.evaluate_js("document.querySelector('[data-game=sts2]').click()")
        wait_js(window, "window.spire.state.game==='sts2'")
        assert not api._model.mods
        assert not (api._model.directory / 'credentials').exists()
        report['checks'].append('paths dialog available and second game handles missing installation without crashing')
        from unittest.mock import patch
        from core import atomic_json
        fixture=api._model.directory/'example-list.json'
        atomic_json(fixture,{'format':'spire-mod-manager-list','schema':1,'game':'sts2','mods':[{
            'id':'ui-missing','name':'清单中的待获取模组','version':'1.0','source':'steam_workshop','workshop_id':'123',
            'file':'example.json','alias':'清单验证','group':'导入的分组','tags':['推荐'],'enabled':True}]})
        window.resize(1000,610)
        with patch.object(api._window,'create_file_dialog',return_value=(str(fixture),)):
            window.evaluate_js("document.getElementById('export').click()")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='模组清单'")
            window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='导入清单预览'")
            assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('本地匹配 0')")
            window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"window.spire.state.inventory.missing===1&&!document.getElementById('dialog').open")
        assert window.evaluate_js("document.querySelector('.system-missing')?.textContent==='缺失'&&document.querySelector('.mod-row .switch').disabled")
        assert window.evaluate_js("document.querySelector('.inventory-meter').getAttribute('aria-label').includes('列表 1 项 · 本地 0 项 · 可用 0 项')")
        assert window.evaluate_js("document.getElementById('scanStatus').getBoundingClientRect().right<document.getElementById('scan').getBoundingClientRect().left")
        assert window.evaluate_js("document.querySelector('.inventory-local').style.width==='0%'&&document.querySelector('.inventory-available').style.width==='0%'")
        window.evaluate_js("document.querySelector('.inventory-meter').click()")
        wait_js(window,"document.getElementById('dialogTitle').textContent==='清单与本地资源'")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('缺失 1 项')")
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        report['checks'].append('native-picker list import, missing badge, layered inventory meter and clickable count explanation')
        api._model.collection_fixture={'id':'900000001','title':'模拟合集','items':{'900000002':{'title':'模拟角色','updated':1},'900000003':{'title':'模拟美化','updated':1}},'warnings':[]}
        window.evaluate_js("showCollectionImport()")
        wait_js(window,"document.getElementById('dialogTitle').textContent==='从工坊合集导入'")
        window.evaluate_js("document.querySelector('#dialogBody input').value='900000001';document.getElementById('dialogButtons').lastElementChild.click()")
        wait_js(window,"document.getElementById('dialogTitle').textContent==='合集导入预览'")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('本地已有 0')")
        window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
        wait_js(window,"state.inventory.missing===3&&!document.getElementById('dialog').open")
        window.evaluate_js("document.getElementById('viewMode').value='合集分组';document.getElementById('viewMode').dispatchEvent(new Event('change'))")
        wait_js(window,"state.view.mode==='合集分组'&&document.getElementById('rows').textContent.includes('模拟合集')")
        window.evaluate_js("document.getElementById('localFilter').value='local';document.getElementById('localFilter').dispatchEvent(new Event('change'))")
        wait_js(window,"state.view.local_filter==='local'&&state.visible.length===0")
        window.evaluate_js("document.getElementById('localFilter').value='missing';document.getElementById('localFilter').dispatchEvent(new Event('change'))")
        wait_js(window,"state.visible.length===3")
        report['checks'].append('blank-user collection preview/import, source grouping and local/missing filters with simulated API and no subscription')
        from unittest.mock import patch
        from core import atomic_json
        import copy
        import workshop_collections
        model=api._model
        model.change_game('sts2',True)
        model.game_dir=str(model.directory/'subscription-test-game');Path(model.game_dir).mkdir()
        model.config_path=str(model.directory/'steam/00000000000000000/settings.save')
        atomic_json(Path(model.config_path),{'mod_settings':{'mods_enabled':True,'mod_list':[]}})
        fake_snapshot={'appid':'2868840','account':'00000000000000000','items':{}}
        calls=[]
        def fake_steam(game,command='snapshot',**options):
            if command=='subscribe':calls.append(options['item']);return {'status':'subscribed','item':options['item']}
            return copy.deepcopy(fake_snapshot)
        model.steam=fake_steam
        workshop_collections.merge(model,{'id':'888','title':'订阅测试','items':{'123':{'title':'模拟待订阅模组','updated':0}}})
        window.evaluate_js("window.spire.act('poll',{revision:-1})")
        wait_js(window,"state.game==='sts2'&&state.mods.some(m=>m.workshop_id==='123')")
        window.evaluate_js("document.getElementById('subscriptionMode').click()")
        wait_js(window,"state.subscription_mode&&Boolean(document.querySelector('.subscribe-pick'))")
        assert window.evaluate_js("document.getElementById('headerInner').children[0].textContent.includes('订阅')")
        window.evaluate_js("{const r=window.spire.rows.find(r=>r.type==='mod'&&state.mods.some(m=>m.key===r.key&&m.workshop_id==='123'));document.getElementById('viewport').scrollTop=r.top;window.spire.renderVirtual();document.querySelector(`[data-row=\"${r.id}\"] .subscribe-pick`).click();}")
        assert window.evaluate_js("subscriptionPicked.size===1&&!state.mods.find(m=>m.workshop_id==='123').enabled"), window.evaluate_js("({picked:[...subscriptionPicked],allowed:state.subscription_keys,visible:state.visible,button:document.querySelector('.subscribe-pick')?.outerHTML,toast:document.getElementById('toast').textContent})")
        window.evaluate_js("document.getElementById('layoutCovers').click()")
        wait_js(window,"state.view.layout==='covers'&&Boolean(document.querySelector('.subscribe-pick'))")
        capture(window,Path(output).parent/'subscription-mode-preview.png')
        for m in model.mods:
            if m.workshop_id:fake_snapshot['items'][m.workshop_id]={'disabled':False,'installed':False,'state':1,'path':''}
        window.evaluate_js("document.getElementById('refreshSubscriptions').click()")
        wait_js(window,"state.subscription_mode&&state.visible.length===0")
        fake_snapshot['items'].clear()
        window.evaluate_js("document.getElementById('refreshSubscriptions').click()")
        wait_js(window,"state.visible.length===3")
        window.evaluate_js("document.getElementById('subscriptionMode').click()")
        wait_js(window,"!state.subscription_mode&&subscriptionPicked.size===0")
        report['checks'].append('top subscription mode, pick without game toggle, cover layout, hide subscribed-but-not-installed, exit clears picks')
        with patch('subscriptions.post',return_value={'publishedfiledetails':[{'publishedfileid':'123','result':1,'consumer_app_id':2868840,'file_type':0}]}),patch('subscriptions.running_game_processes',return_value=[]):
            window.evaluate_js("showSubscribe([state.mods.find(m=>m.workshop_id==='123').key])")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='订阅缺失模组'")
            assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('Steam 已运行并登录')")
            window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='确认订阅并下载'")
            assert calls==[]
            window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
            assert calls==[]
            window.evaluate_js("showSubscribe([state.mods.find(m=>m.workshop_id==='123').key])")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='订阅缺失模组'")
            window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='确认订阅并下载'")
            window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"window.spire.job.kind==='subscribe'&&!window.spire.job.running")
            assert calls==['123']
        report['checks'].append('subscription warning and exact preview, cancel performs no write, confirm executes only mocked Steam bridge')


        from web_app import VERSION
        report.update(ok=True, version=VERSION)
        capture(window, Path(output).parent / 'public-first-run-preview.png')
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        api._allow_close = True
        window.destroy()


def quick_check(window, api, output):
    """Short opt-in UI smoke for small visual changes; uses isolated manager records."""
    report = {'ok':False, 'checks':[]}
    try:
        wait_js(window, 'Boolean(window.spire?.state?.ready)')
        if not window.evaluate_js("window.spire.state.api_intro_seen"):
            wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent.includes('Steam API')")
            assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('不要分享')")
            window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
            wait_js(window,"window.spire.state.api_intro_seen&&!document.getElementById('dialog').open")
            report['checks'].append('optional first-run Steam API introduction and persisted dismissal')
        if window.evaluate_js("window.spire.state.view.layout==='covers'"):
            window.evaluate_js("document.getElementById('layoutTable').click()")
            wait_js(window,"window.spire.state.view.layout==='table'")
        window.resize(1000, 610)
        time.sleep(.15)
        assert window.evaluate_js("document.querySelectorAll('.mod-row').length>0")
        assert window.evaluate_js("['apply','theme'].every(id=>document.getElementById(id).getBoundingClientRect().bottom<=innerHeight)")
        report['checks'].append('launch, visible rows and bottom controls')
        for width in (860, 1000):
            window.resize(width,610)
            time.sleep(.1)
            assert window.evaluate_js("['scan','progress','layoutTable','layoutCovers','display','search'].every(id=>{const r=document.getElementById(id).getBoundingClientRect();return r.width>0&&r.right<=innerWidth&&r.left>=0&&r.bottom<innerHeight})"), 'Toolbar clips at '+str(width)
            assert window.evaluate_js("document.querySelector('.list-shell').getBoundingClientRect().top<145"), 'List starts too low'
        window.evaluate_js("document.getElementById('more').click()")
        assert window.evaluate_js("!document.getElementById('moreMenu').hidden&&document.querySelectorAll('#moreMenu button').length===8&&document.getElementById('smallRefresh').getBoundingClientRect().width>0&&!document.querySelector('.backup-bar')")
        window.evaluate_js("document.getElementById('more').click();document.getElementById('display').click()")
        assert window.evaluate_js("!document.getElementById('displayMenu').hidden&&['filterOn','filterOff','hideOld','showFolder','groupOrder'].every(id=>document.getElementById(id).closest('#displayMenu'))")
        window.evaluate_js("document.getElementById('display').click()")
        report['checks'].append('compact toolbar at 860/1000px, visible icon-labelled view switch and retained secondary actions')
        window.evaluate_js("document.getElementById('cacheSettings').click()")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='更新与备份设置'")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('不会主动删除')&&document.querySelector('#dialogBody input[type=checkbox]')")
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        window.evaluate_js("document.getElementById('progress').click()")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='日志与检查'")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('检查提示')")
        assert window.evaluate_js("document.getElementById('jobLog').getBoundingClientRect().height>innerHeight*.45"), 'Progress content area is too small'
        assert window.evaluate_js("(()=>{const n=document.createElement('div');renderTaskLog(n,{rows:[{id:'r',text:'完成',status:'success'}],problems:[{id:'缓存',phase:'封面',reason:'已有61项'}]});return !n.querySelector('.task-problems').textContent&&n.querySelector('.task-rows').nextElementSibling.classList.contains('task-problems')})()")
        capture(window,Path(output).parent/'web-task-log-preview.png')

        window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
        assert window.evaluate_js("(()=>{const r=document.querySelector('.sidebar').getBoundingClientRect();return ['saveProfile','restoreProfile'].every(id=>{const b=document.getElementById(id).getBoundingClientRect();return b.left>=r.left&&b.right<=r.right})})()")
        report['checks'].append('optional cache warning, grouped more menu, unified log and checks')
        assert window.evaluate_js("(()=>{const n=document.createElement('div');renderTaskLog(n,{rows:[{id:'r1',text:'等待 1 秒',status:'working'}],problems:[]});const first=n.querySelector('.task-row');renderTaskLog(n,{rows:[{id:'r1',text:'等待 2 秒',status:'working'}],problems:[{id:'123',phase:'标题',reason:'超时'}]});return n.querySelectorAll('.task-row').length===1&&first===n.querySelector('.task-row')&&first.textContent==='等待 2 秒'&&n.querySelector('.task-problems').textContent.includes('123')})()")
        report['checks'].append('waiting replaces existing DOM row; unresolved Workshop IDs remain visible')

        window.evaluate_js("document.getElementById('apiSettings').click()")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='Steam API 管理'")
        assert window.evaluate_js("document.querySelector('#dialogBody input[type=password]').value===''&&document.getElementById('dialogBody').textContent.includes('清除本机')")
        window.evaluate_js("[...document.querySelectorAll('#dialogBody button')].find(b=>b.textContent==='查看使用与安全说明').click()")
        wait_js(window,"document.getElementById('dialogTitle').textContent.includes('用 Steam API')")
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        wait_js(window,"document.getElementById('dialogTitle').textContent==='Steam API 管理'")
        window.evaluate_js("document.getElementById('dialogX').click()")
        report['checks'].append('API management in More, empty write-only key field and repeatable instructions')


        window.evaluate_js("document.getElementById('releasePacks').click()")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='导出备份包'")
        assert window.evaluate_js("document.getElementById('dialogBody').textContent.includes('待导出缓存')&&document.getElementById('dialogButtons').textContent.includes('导入备份包')")
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        report['checks'].append('incremental release menu and export/import choices')
        for column,title in [('version','版本与备份记录'),('group','设置分组'),('tags','管理标签')]:
            window.evaluate_js("document.querySelector('.mod-row [data-column="+column+"]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
            wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==="+json.dumps(title))
            window.evaluate_js("document.getElementById('dialogX').click()")
        window.evaluate_js("chooseBackupPacks()")
        wait_js(window,"document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='添加备份包'")
        assert window.evaluate_js("document.getElementById('dialogButtons').textContent.includes('可多选')&&document.getElementById('dialogButtons').textContent.includes('选择文件夹')")
        window.evaluate_js("document.getElementById('dialogX').click()")
        report['checks'].append('double-click version/group/tag management and multi-file/folder backup chooser')


        assert window.evaluate_js("['layoutTable','layoutCovers'].every(id=>{const n=document.getElementById(id),r=n.getBoundingClientRect();return !n.closest('#displayMenu')&&r.width>0&&r.left>=0&&r.right<=innerWidth})")
        report['checks'].append('always visible layout buttons in the title area')
        before = window.evaluate_js("({sort:window.spire.state.view.sort_column,width:document.getElementById('headerInner').children[1].getBoundingClientRect().width})")
        point = window.evaluate_js("(()=>{const r=document.getElementById('headerInner').children[1].querySelector('.column-resizer').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()")
        devtools(window,'Input.dispatchMouseEvent',{'type':'mousePressed',**point,'button':'left','buttons':1,'clickCount':1})
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':point['x']-24,'y':point['y'],'button':'left','buttons':1})
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':point['x']-24,'y':point['y'],'button':'left','clickCount':1})
        wait_js(window,'document.getElementById("headerInner").children[1].getBoundingClientRect().width<'+str(before['width']-1))
        after = window.evaluate_js("({sort:window.spire.state.view.sort_column,width:document.getElementById('headerInner').children[1].getBoundingClientRect().width})")
        assert after['width'] < before['width'] - 1, (before, after)
        assert after['sort'] == before['sort'], (before, after)
        assert api._model.state['view']['column_widths']['nickname'] < before['width']
        report['checks'].append('native pointer column drag, saved widths, no accidental sort')
        for width in (1450,1000):
            window.resize(width,610)
            time.sleep(.15)
            assert window.evaluate_js("(()=>{const v=document.getElementById('viewport'),right=v.getBoundingClientRect().left+v.clientWidth;return [...document.querySelectorAll('.mod-row .loader')].every(n=>Math.abs(n.getBoundingClientRect().right-right)<2)})()"), 'Status column did not follow the right edge'
        report['checks'].append('saved column widths adapt on resize and status stays at the right edge')
        assert window.evaluate_js("document.getElementById('scrollRail').getBoundingClientRect().left>document.querySelector('.table-card').getBoundingClientRect().right&&getComputedStyle(document.getElementById('viewport')).scrollbarWidth==='none'")
        window.evaluate_js("document.getElementById('scrollRail').scrollTop=150")
        wait_js(window,"Math.abs(document.getElementById('viewport').scrollTop-150)<1")
        window.evaluate_js("document.getElementById('viewport').scrollTop=0")
        wait_js(window,"document.getElementById('scrollRail').scrollTop===0")
        report['checks'].append('scroll rail outside entire table/header, two-way synchronized scrolling')
        assert window.evaluate_js("(()=>{const v=document.getElementById('viewport'),rail=document.getElementById('scrollRail');v.scrollTop=350;window.spire.renderVirtual();v.scrollTop=650;rail.dispatchEvent(new Event('scroll'));return Math.abs(v.scrollTop-650)<1})()"), 'Delayed mirror scroll rewound the list'
        window.evaluate_js("window.wheelProbe=[];window.wheelRecord=()=>window.wheelProbe.push(document.getElementById('viewport').scrollTop);document.getElementById('viewport').addEventListener('scroll',window.wheelRecord);document.getElementById('viewport').scrollTop=0")
        time.sleep(.15)
        point=window.evaluate_js("(()=>{const r=document.getElementById('viewport').getBoundingClientRect();return {x:r.left+100,y:r.top+60}})()")
        for direction in (1,-1):
            window.evaluate_js("window.wheelProbe=[]")
            for i in range(6):
                devtools(window,'Input.dispatchMouseEvent',{'type':'mouseWheel',**point,'deltaX':0,'deltaY':direction*100})
                time.sleep(.025)
            time.sleep(.2)
            samples=window.evaluate_js("window.wheelProbe")
            assert len(samples)>1 and all(direction*(b-a)>=-1 for a,b in zip(samples,samples[1:])), ('Wheel reversed direction',samples)
        window.evaluate_js("document.getElementById('viewport').removeEventListener('scroll',window.wheelRecord);document.getElementById('viewport').scrollTop=0")
        time.sleep(.1)
        report['checks'].append('delayed scroll mirror does not rewind list; rapid native wheel down/up stays monotonic')


        for event in ('enter','outside','escape'):
            window.evaluate_js("document.querySelector('.mod-row [data-column=nickname]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));")
            assert window.evaluate_js("!!document.querySelector('.alias-edit')")
            window.evaluate_js("(()=>{const b=document.querySelector('.alias-edit');b.value='昵称关闭检查';" + ("b.blur();document.getElementById('viewport').dispatchEvent(new PointerEvent('pointerup',{bubbles:true}));if(!document.querySelector('.alias-edit'))throw Error('release outside saved editor');document.getElementById('tableHeader').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));" if event=='outside' else "b.dispatchEvent(new KeyboardEvent('keydown',{key:'"+('Enter' if event=='enter' else 'Escape')+"',bubbles:true}));") + "})()")
            wait_js(window,"document.querySelectorAll('.alias-edit').length===0")
            time.sleep(.15)
            assert window.evaluate_js("document.querySelectorAll('.alias-edit').length===0")
        report['checks'].append('nickname editor saves on outside press, preserves outside release and closes on Enter/Escape')

        window.evaluate_js("window.spire.act('view',{mode:'不分组',query:'',group:'全部分组'})")
        wait_js(window,"window.spire.state.view.mode==='不分组'&&document.querySelectorAll('.mod-row').length>2")
        points=window.evaluate_js("(()=>{const rows=[...document.querySelectorAll('.mod-row')].slice(0,2).map(n=>n.getBoundingClientRect());return {x:rows[0].left+140,y:rows[0].top+4,x2:rows[0].left+250,y2:rows[1].bottom-3}})()")
        devtools(window,'Input.dispatchMouseEvent',{'type':'mousePressed','x':points['x'],'y':points['y'],'button':'right','buttons':2,'clickCount':1})
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':points['x2'],'y':points['y2'],'button':'right','buttons':2})
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':points['x2'],'y':points['y2'],'button':'right','buttons':0,'clickCount':1})
        wait_js(window,"document.getElementById('selectedCount').textContent==='2 项'")
        report['checks'].append('native right drag selects intersected mod rows')
        window.evaluate_js("window.spire.act('view',{mode:'文件夹分组'})")
        wait_js(window,"document.querySelector('.group-grip')")
        window.evaluate_js("document.getElementById('collapseAll').click()")
        wait_js(window,"document.querySelectorAll('.group-grip').length>1")
        original=window.evaluate_js("window.spire.state.sections.slice(0,2).map(s=>s.key)")
        points=window.evaluate_js("(()=>{const g=[...document.querySelectorAll('.group-grip')].slice(0,2).map(n=>n.getBoundingClientRect());return {x:g[0].left+8,y:g[0].top+10,x2:g[1].left+8,y2:g[1].bottom-1}})()")
        devtools(window,'Input.dispatchMouseEvent',{'type':'mousePressed','x':points['x'],'y':points['y'],'button':'left','buttons':1,'clickCount':1})
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':points['x2'],'y':points['y2'],'button':'left','buttons':1})
        assert window.evaluate_js("!!document.querySelector('.lifted-group')&&document.querySelector('.group-slot').offsetHeight===34")
        assert window.evaluate_js("window.spire.rows.filter(r=>r.type==='group')[0].section.key===window.spire.state.sections[1].key")
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':points['x2'],'y':points['y2'],'button':'left','buttons':0,'clickCount':1})
        wait_js(window,"window.spire.state.sections[0].key==="+json.dumps(original[1]))
        assert api._model.view['group_orders']['文件夹分组'][0]==original[1]
        assert window.evaluate_js("document.querySelector('.group-row').offsetHeight===34")
        wait_js(window,"!document.querySelector('.lifted-group')")
        window.evaluate_js("document.querySelector('.group-row .expander').click()")
        points=window.evaluate_js("(()=>{const r=document.querySelector('.group-grip').getBoundingClientRect();return {x:r.left+8,y:r.top+10}})()")
        devtools(window,'Input.dispatchMouseEvent',{'type':'mousePressed','x':points['x'],'y':points['y'],'button':'left','buttons':1,'clickCount':1})
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':points['x']+10,'y':points['y']+6,'button':'left','buttons':1})
        assert window.evaluate_js("document.querySelector('.group-slot').offsetHeight>34&&!!document.querySelector('.lifted-group')")
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        devtools(window,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':points['x']+10,'y':points['y']+6,'button':'left','buttons':0,'clickCount':1})
        wait_js(window,"!document.querySelector('.lifted-group')&&!document.querySelector('.group-slot')")
        assert window.evaluate_js("window.spire.state.sections[0].key==="+json.dumps(original[1]))
        report['checks'].append('native group lift, full-height placeholder, animated reorder persistence and Escape cancellation')
        window.evaluate_js("window.spire.act('view',{group_order:[],mode:'不分组'})")
        wait_js(window,"window.spire.state.view.mode==='不分组'")

        # Recycle selected rows, then select a single visible mod and revisit cached rows.
        window.evaluate_js("document.querySelector('.workspace').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}));document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'a',ctrlKey:true,bubbles:true}));")
        wait_js(window,"parseInt(document.getElementById('selectedCount').textContent)===window.spire.state.visible.length")
        window.evaluate_js("document.getElementById('viewport').scrollTop=document.getElementById('viewport').scrollHeight")
        time.sleep(.12)
        window.evaluate_js("document.getElementById('viewport').scrollTop=0")
        time.sleep(.12)
        window.evaluate_js("document.querySelector('.mod-row .modname').click()")
        wait_js(window,"document.getElementById('selectedCount').textContent==='1 项'")
        window.evaluate_js("document.getElementById('viewport').scrollTop=document.getElementById('viewport').scrollHeight")
        time.sleep(.12)
        assert window.evaluate_js("document.querySelectorAll('.mod-row.selected').length===0")
        window.evaluate_js("document.querySelector('.workspace').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}))")
        assert window.evaluate_js("document.getElementById('selectedCount').textContent==='0 项'")
        window.evaluate_js("document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'a',ctrlKey:true,bubbles:true}))")
        assert window.evaluate_js("parseInt(document.getElementById('selectedCount').textContent)===window.spire.state.visible.length")
        window.evaluate_js("document.getElementById('viewport').scrollTop=0")
        time.sleep(.12)
        assert window.evaluate_js("document.querySelector('.mod-row .modname .cell-secondary').textContent.includes('iB')&&!document.querySelector('.mod-row .nickname .cell-secondary')")
        report['checks'].append('offscreen selection refresh, margin clears selection/focus, global Ctrl+A, compact file sizes')
        window.evaluate_js("document.querySelector('.mod-row .modname').click();document.querySelector('.workspace').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}))")
        assert window.evaluate_js("window.spire.selected.length===0&&document.querySelectorAll('.mod-row.focus-item').length===1")
        assert window.evaluate_js("(()=>{const r=[...document.querySelectorAll('.mod-row')].filter(n=>!n.matches(':hover'));return getComputedStyle(r[0]).backgroundColor===getComputedStyle(r[1]).backgroundColor&&getComputedStyle(document.getElementById('viewport')).paddingRight==='0px'})()")
        window.evaluate_js("document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))")
        assert window.evaluate_js("window.spire.selected.length===1&&document.querySelector('.mod-row.focus-item').classList.contains('selected')")
        window.evaluate_js("window.dispatchEvent(new Event('blur'))")
        assert window.evaluate_js("document.body.classList.contains('window-inactive')&&window.spire.selected.length===1")
        window.evaluate_js("window.dispatchEvent(new Event('focus'));document.querySelector('.workspace').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}));window.dispatchEvent(new Event('blur'))")
        assert window.evaluate_js("!document.querySelector('.mod-row.focus-item')&&window.spire.selected.length===0")
        window.evaluate_js("window.dispatchEvent(new Event('focus'))")
        report['checks'].append('Explorer selection outline, blank-click position cue, arrow navigation and inactive selection')


        window.evaluate_js("window.spire.act('view',{mode:'文件夹分组'})")
        wait_js(window,"!document.getElementById('groupFolds').hidden")
        window.evaluate_js("document.getElementById('collapseAll').click()")
        assert window.evaluate_js("document.querySelectorAll('.mod-row').length===0")
        assert window.evaluate_js("getComputedStyle(document.querySelector('.group-row strong'),'::before').content==='none'")
        assert window.evaluate_js("getComputedStyle(document.querySelector('.group-row')).borderTopWidth==='2px'")
        window.evaluate_js("document.getElementById('expandAll').click()")
        assert window.evaluate_js("document.querySelectorAll('.mod-row').length>0")
        window.evaluate_js("window.spire.act('view',{mode:'不分组'})")
        wait_js(window,"document.getElementById('groupFolds').hidden")
        report['checks'].append('always visible grouped fold/unfold controls, no folder glyph, stronger separators, storage under mod name')

        report['nickname_font'] = window.evaluate_js("getComputedStyle(document.querySelector('.mod-row .nickname')).fontSize")
        report['statuses'] = window.evaluate_js("[...document.querySelectorAll('.mod-row .loader')].map(n=>n.textContent)")
        for fraction in (.5,1,0):
            window.evaluate_js(f"document.getElementById('viewport').scrollTop=document.getElementById('viewport').scrollHeight*{fraction}")
            time.sleep(.08)
        window.evaluate_js("window.spire.act('view',{mode:'文件夹分组'}).then(()=>document.getElementById('layoutCovers').click())")
        wait_js(window,"window.spire.state.view.layout==='covers'&&document.querySelector('.cover-row')")
        window.evaluate_js("document.getElementById('viewport').scrollTop=380")
        time.sleep(.15)
        assert window.evaluate_js("[...document.querySelectorAll('.cover-row')].every(n=>n.offsetHeight===128)")
        assert window.evaluate_js("[...document.querySelectorAll('#rows .switch')].every(n=>{const r=n.getBoundingClientRect(),p=n.closest('[data-row]').getBoundingClientRect();return r.top>=p.top&&r.bottom<=p.bottom})")
        report['checks'].append('cover layout, original folder groups and variable row heights')
        # Exercise the same row IDs in table and cover layouts, including cached offscreen rows.
        window.evaluate_js("document.getElementById('layoutTable').click()")
        wait_js(window,"window.spire.state.view.layout==='table'")
        for fraction in (.5,1,0):
            window.evaluate_js(f"document.getElementById('viewport').scrollTop=document.getElementById('viewport').scrollHeight*{fraction}")
            time.sleep(.08)
        window.evaluate_js("document.getElementById('layoutCovers').click()")
        wait_js(window,"window.spire.state.view.layout==='covers'")
        for fraction in (.5,1,0):
            window.evaluate_js(f"document.getElementById('viewport').scrollTop=document.getElementById('viewport').scrollHeight*{fraction}")
            time.sleep(.08)
            assert window.evaluate_js("[...document.querySelectorAll('.mod-row')].every(n=>n.classList.contains('cover-row')&&n.querySelector('.cover-art')&&n.offsetHeight===128)")
        report['checks'].append('offscreen cached table rows convert to cover rows when recycled')
        if api._model.covers.available('sts1'):
            window.evaluate_js("document.getElementById('viewport').scrollTop=0")
            wait_js(window,"[...document.querySelectorAll('.cover-art img')].some(n=>n.complete&&n.naturalWidth>0)")
            report['checks'].append('local cached cover decoded in WebView2')
            window.evaluate_js("""(()=>{
              const art=document.querySelector('.cover-art img')?.parentElement;
              const row=art?.closest('[data-row]');
              if(!art||!row)throw Error('No cached cover row for retry check');
              const item=art._cover,original=window.pywebview.api.cover;
              window.coverReadOriginal=original;
              window.coverRetryProbe={game:item.game,id:item.id,calls:0,row:row.dataset.row};
              window.pywebview.api.cover=(game,id)=>{
                const p=window.coverRetryProbe;
                if(game===p.game&&id===p.id){p.calls++;if(p.calls===1)return Promise.reject(Error('simulated local read failure'));if(p.calls===2)return Promise.resolve('');}
                return original(game,id);
              };
              coverImages.delete(item.game+':'+item.id);art.remove();window.spire.renderVirtual(true);
            })()""")
            wait_js(window,"window.coverRetryProbe.calls>=3&&[...document.querySelectorAll('[data-row]')].find(n=>n.dataset.row===window.coverRetryProbe.row)?.querySelector('.cover-art img')?.naturalWidth>0")
            report['checks'].append('failed then empty cover read recovers automatically without user actions')
            window.evaluate_js("""(()=>{
              const p=window.coverRetryProbe,row=[...document.querySelectorAll('[data-row]')].find(n=>n.dataset.row===p.row);
              window.coverSavedIds=[...window.spire.state.cover_ids];
              window.spire.state.cover_ids=window.coverSavedIds.filter(id=>id!==p.id);
              row.querySelector('.cover-art').remove();window.spire.renderVirtual(true);
            })()""")
            assert window.evaluate_js("(()=>{const row=[...document.querySelectorAll('[data-row]')].find(n=>n.dataset.row===window.coverRetryProbe.row);return !row.classList.contains('cover-error')&&row.querySelector('.cover-failure-badge').hidden&&row.querySelector('.cover-placeholder').textContent==='暂无封面'})()")
            report['checks'].append('missing local cover uses a plain placeholder without warning')
            window.evaluate_js("""(()=>{
              const p=window.coverRetryProbe,row=[...document.querySelectorAll('[data-row]')].find(n=>n.dataset.row===p.row);
              window.spire.state.cover_ids=window.coverSavedIds;
              window.pywebview.api.cover=(game,id)=>game===p.game&&id===p.id?Promise.resolve({data:'',code:'empty_result',reason:'本地读取返回空结果'}):window.coverReadOriginal(game,id);
              coverImages.delete(p.game+':'+p.id);row.querySelector('.cover-art').remove();window.spire.renderVirtual(true);
            })()""")
            wait_js(window,"(()=>{const row=[...document.querySelectorAll('[data-row]')].find(n=>n.dataset.row===window.coverRetryProbe.row);return row.classList.contains('cover-error')&&row.querySelector('.cover-art').dataset.state==='failed'&&!row.querySelector('.cover-failure-badge').hidden&&row.querySelector('.cover-placeholder').textContent.includes('本地读取返回空结果')})()")
            report['checks'].append('persistent failure shows the reason in the frame and a row warning')
        report['ok'] = True
        from web_app import VERSION
        report['version'] = VERSION
        capture(window,Path(output).parent/'web-quick-preview.png')
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        Path(output).parent.mkdir(parents=True,exist_ok=True)
        Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        api._allow_close = True
        window.destroy()


def check(window, api, output):
    report = {"ok": False, "checks": []}
    try:
        wait_js(window, "Boolean(window.spire && window.spire.state && window.spire.state.ready)")
        report["user_agent"] = window.evaluate_js("navigator.userAgent")
        assert "Chrome" in report["user_agent"]
        report["initial_count"] = len(api._model.mods)
        # Controls and list must remain in bounds, including short screens.
        for width, height in ((1000, 610), (900, 500), (1200, 680)):
            window.resize(width, height)
            time.sleep(.12)
            result = window.evaluate_js("['apply','theme','workshop'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight+1&&r.right<=innerWidth+1}).every(Boolean)")
            assert result, (width, height, window.evaluate_js("({w:innerWidth,h:innerHeight})"))
            assert window.evaluate_js("document.getElementById('viewport').scrollWidth<=document.getElementById('viewport').clientWidth+1"), 'Unnecessary horizontal scrollbar'
            window.evaluate_js("document.getElementById('more').click()")
            assert window.evaluate_js("(()=>{const r=document.getElementById('moreMenu').getBoundingClientRect();return r.left>=0&&r.bottom<=innerHeight})()"), 'Menu clipped'
            window.evaluate_js("document.getElementById('more').click()")
        report["checks"].append("window bounds and short layout")
        report['wheel_anchor_comparison'] = {anchor: wheel_probe(window, anchor) for anchor in ('auto', 'none')}
        corrected = report['wheel_anchor_comparison']['none']
        assert abs(corrected['settled_start'] - corrected['requested_start']) <= .6
        for step in corrected['steps']:
            assert step['reverse_frames'] == 0, step
            assert abs(step['samples'][-1] - step['samples'][0] - step['delta']) <= .6, step
        assert window.evaluate_js("getComputedStyle(document.getElementById('viewport')).overflowAnchor==='none'")
        report['checks'].append('native small wheel input stays monotonic without anchoring compensation')
        for value, expected in (("dark", "dark"), ("light", "light"), ("system", "light")):
            if value == "system":
                emulate_color(window, "light")
            window.evaluate_js(f"document.getElementById('theme').value='{value}';document.getElementById('theme').dispatchEvent(new Event('change'))")
            wait_js(window, f"window.spire.state.theme==='{value}'&&document.documentElement.dataset.colorTheme==='{expected}'")
        emulate_color(window, "dark")
        wait_js(window, "document.documentElement.dataset.colorTheme==='dark'")
        assert api._model.store["web_ui"]["theme"] == "system"
        report["checks"].append("light/dark settings and live system-theme change")
        shot_dir = Path(output).parent
        capture(window, shot_dir / "web-dark.png")
        emulate_color(window, "light")
        wait_js(window, "document.documentElement.dataset.colorTheme==='light'")
        capture(window, shot_dir / "web-light.png")
        # Invoke actual row controls through DOM events, not backend editing shortcuts.
        # Parent and child sorting are independent, so the first mounted child need
        # not be the first item of the globally sorted visible inventory.
        first = window.evaluate_js("window.spire.rows.find(r=>r.id===document.querySelector('.mod-row').dataset.row).key")
        before = window.evaluate_js(f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).enabled")
        revision = api._model.revision
        window.evaluate_js("document.querySelector('.mod-row .switch').click()")
        wait_js(window, f"window.spire.state.revision>{revision}")
        assert window.evaluate_js(f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).enabled") != before
        window.evaluate_js("document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'z',ctrlKey:true,bubbles:true}))")
        wait_js(window, f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).enabled==={str(before).lower()}")
        window.evaluate_js("document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'z',ctrlKey:true,shiftKey:true,bubbles:true}))")
        wait_js(window, f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).enabled!=={str(before).lower()}")
        window.evaluate_js("document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'z',ctrlKey:true,bubbles:true}))")
        wait_js(window, f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).enabled==={str(before).lower()}")
        report["checks"].append("native DOM switch click, Ctrl+Z and Ctrl+Shift+Z")
        window.evaluate_js("document.querySelector('.mod-row').click();document.getElementById('quickTag').value='界面验证';document.getElementById('addTag').click()")
        wait_js(window, f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).tags.includes('界面验证')")
        assert window.evaluate_js("document.getElementById('quickTag').value") == "界面验证"
        report["checks"].append("quick tags retain input")
        window.evaluate_js("document.querySelector('.mod-row [data-column=nickname]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));document.querySelector('.alias-edit').value='界面昵称验证';document.querySelector('.alias-edit').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        wait_js(window, f"window.spire.state.mods.find(m=>m.key==={json.dumps(first)}).alias==='界面昵称验证'")
        report["checks"].append("inline nickname editor")
        for mode in ("自定义分组", "标签分组", "文件夹分组", "不分组"):
            window.evaluate_js(f"document.getElementById('viewMode').value={json.dumps(mode)};document.getElementById('viewMode').dispatchEvent(new Event('change'))")
            wait_js(window, f"window.spire.state.view.mode==={json.dumps(mode)}")
            if mode != '不分组':
                parent_keys = window.evaluate_js("window.spire.state.sections.map(s=>s.key)")
                previous = api._model.view['group_descending']
                window.evaluate_js("document.getElementById('groupOrder').click()")
                wait_js(window, f"window.spire.state.view.group_descending!=={str(previous).lower()}")
                self_keys = window.evaluate_js("window.spire.state.sections.map(s=>s.key)")
                assert self_keys == list(reversed(parent_keys))
                revision = api._model.revision
                window.evaluate_js("document.getElementById('headerInner').children[2].click()")
                wait_js(window, f"window.spire.state.revision>{revision}")
                assert window.evaluate_js("window.spire.state.sections.map(s=>s.key)") == self_keys
            window.evaluate_js("document.getElementById('viewport').scrollTop=0")
            time.sleep(.05)
            window.evaluate_js("document.querySelector('.mod-row').click();document.getElementById('viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'a',ctrlKey:true,bubbles:true}))")
            assert window.evaluate_js("(()=>{const r=window.spire.rows.find(r=>r.type==='mod'),g=window.spire.rows.find(g=>g.id===r.groupId),expected=g?g.section.shown:window.spire.state.visible;return expected.length===window.spire.selected.length&&expected.every(k=>window.spire.selected.includes(k))})()")
            for fraction in (1, .2, .8, 0):
                window.evaluate_js(f"document.getElementById('viewport').scrollTop=document.getElementById('viewport').scrollHeight*{fraction}")
                time.sleep(.03)
            assert window.evaluate_js("document.querySelectorAll('#rows>[data-row]').length<100")
            assert window.evaluate_js("document.querySelectorAll('canvas').length===0")
            assert window.evaluate_js("[...document.querySelectorAll('#rows .switch')].every(n=>{const p=n.closest('[data-row]').getBoundingClientRect(),r=n.getBoundingClientRect();return r.top>=p.top&&r.bottom<=p.bottom})")
        for _ in range(3):
            window.maximize()
            time.sleep(.05)
            window.restore()
            time.sleep(.05)
        window.resize(1200, 850)
        time.sleep(.15)
        assert window.evaluate_js("(()=>{const v=document.getElementById('viewport'),r=document.getElementById('rows').getBoundingClientRect();return r.bottom>=v.getBoundingClientRect().bottom-1||document.getElementById('spacer').offsetHeight<=v.clientHeight})()"), 'Resize left an unrendered area'
        report["checks"].append("all group modes, group Ctrl+A, bounded DOM, rapid scroll/maximize/restore, resize fills rows")
        report['checks'].append('independent parent name order and child column sorting')
        # Check tower II with the same real read-only Steam adapter as the packaged app.
        window.evaluate_js("window.spire.act('game',{game:'sts2',discard:true},true)")
        wait_js(window, "window.spire.state.game==='sts2'&&window.spire.state.ready")
        assert api._model.workshop_snapshot, api._model.warning
        report["sts2_count"] = len(api._model.mods)
        report["checks"].append("STS2 scan and Steam bridge")
        # Both games retain their own editor objects and DOM view state on warm switches.
        warm_times = []
        window.evaluate_js("document.querySelector('.mod-row').click();document.getElementById('viewport').scrollTop=100")
        time.sleep(.1)
        selected = window.evaluate_js('window.spire.selected')
        scroll = window.evaluate_js("document.getElementById('viewport').scrollTop")
        for game in ('sts1', 'sts2', 'sts1', 'sts2'):
            window.evaluate_js(f"window.smokeSwitchTime=0;window.smokeSwitchStart=performance.now();window.spire.act('game',{{game:'{game}'}}).then(()=>{{window.smokeSwitchTime=performance.now()-window.smokeSwitchStart}})")
            warm_times.append(wait_js(window, 'window.smokeSwitchTime'))
            wait_js(window, f"window.spire.state.game==='{game}'")
        assert window.evaluate_js('window.spire.selected') == selected
        assert abs(window.evaluate_js("document.getElementById('viewport').scrollTop") - scroll) <= 1
        report['warm_switch_ms'] = warm_times
        report['checks'].append('warm game switches retain selections, scroll and editor state')
        # Create a concrete apply preview, but never commit real game settings.
        window.evaluate_js("document.getElementById('apply').click()")
        wait_js(window, "document.getElementById('dialog').open&&document.getElementById('dialogTitle').textContent==='应用前预览'")
        assert "自动同步 Steam" in window.evaluate_js("document.getElementById('dialogBody').textContent")
        window.evaluate_js("document.getElementById('dialogButtons').children[0].click()")
        report["checks"].append("two-layer apply preview without writing game config")
        emulate_color(window, None)
        from web_app import VERSION
        report.update(ok=True, version=VERSION, renderer="WebView2", mounted_rows=window.evaluate_js("document.getElementById('rows').children.length"))
    except Exception:
        report["error"] = traceback.format_exc()
        try:
            capture(window, Path(output).parent / 'web-check-failure.png')
            report["bounds"] = window.evaluate_js("['apply','theme','workshop','viewport'].map(id=>({id,rect:document.getElementById(id).getBoundingClientRect().toJSON(),w:innerWidth,h:innerHeight}))")
            report["browser"] = window.evaluate_js("({title:document.title,toast:document.getElementById('toast')?.textContent,status:document.getElementById('status')?.textContent})")
        except Exception:
            pass
    finally:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        api._allow_close = True
        window.destroy()


def demo_check(window,api,output):
    """Exercise the online-demo route with transport replaced, then close normally."""
    from unittest.mock import patch
    report={'ok':False}
    def transport(method,ids,key=''):
        if method=='GetCollectionDetails':return {'collectiondetails':[{'publishedfileid':'100','result':1,'children':[{'publishedfileid':'123','filetype':0}]}]}
        return {'publishedfiledetails':[{'publishedfileid':i,'result':1,'consumer_app_id':646570,'title':'演示测试'} for i in ids]}
    try:
        wait_js(window,'Boolean(window.spire?.state?.demo_mode)')
        assert window.evaluate_js('state.counts.total===0')
        with patch('workshop_collections.post',side_effect=transport):
            window.evaluate_js('showCollectionImport()')
            wait_js(window,"document.getElementById('dialogTitle').textContent==='从工坊合集导入'")
            window.evaluate_js("document.querySelector('#dialogBody input').value='100';document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,"document.getElementById('dialogTitle').textContent==='合集导入预览'")
            window.evaluate_js("document.getElementById('dialogButtons').lastElementChild.click()")
            wait_js(window,'state.inventory.missing===1')
        report={'ok':True,'checks':['empty isolated demo','normal collection fetch path with replaced network transport','import remains list-only','normal close without cache cleanup error']}
    except Exception:report['error']=traceback.format_exc()
    finally:
        Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        api._allow_close=True;window.destroy()
