"""Read public Workshop collections; never subscribe or download game files."""
import copy
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from core import APP_IDS, Mod


def collection_id(value):
    value=str(value).strip()
    if value.isdecimal() and 0<int(value)<2**64:return value
    url=urllib.parse.urlparse(value)
    if url.scheme!='https' or url.hostname not in ('steamcommunity.com','www.steamcommunity.com'):raise ValueError('请输入工坊合集编号或 Steam 创意工坊 HTTPS 链接。')
    return collection_id(urllib.parse.parse_qs(url.query).get('id',[''])[0]) if urllib.parse.parse_qs(url.query).get('id',[''])[0].isdecimal() else invalid()

def invalid():raise ValueError('链接中没有有效的工坊编号。')

def post(method, ids, key=""):
    count='collectioncount' if method=='GetCollectionDetails' else 'itemcount'
    params={count:len(ids),**{f'publishedfileids[{i}]':v for i,v in enumerate(ids)}}
    if key:
        params={'language':6,**{f'publishedfileids[{i}]':v for i,v in enumerate(ids)}}
        if method=='GetCollectionDetails':params['includechildren']='true'
        request=urllib.request.Request('https://api.steampowered.com/IPublishedFileService/GetDetails/v1/?'+urllib.parse.urlencode(params),headers={'x-webapi-key':key,'User-Agent':'SpireModManager/1.10'})
    else:
        request=urllib.request.Request('https://api.steampowered.com/ISteamRemoteStorage/'+method+'/v1/',data=urllib.parse.urlencode(params).encode(),headers={'User-Agent':'SpireModManager/1.10'})
    stage='读取合集成员' if method=='GetCollectionDetails' else '获取模组标题'
    try:
        from steam_account import api_open
        with (api_open(request) if key else urllib.request.urlopen(request,timeout=20)) as response:
            data=response.read(16*1024*1024+1)
    except urllib.error.HTTPError as exc:
        raise ValueError(stage+f'失败（HTTP {exc.code}）；请检查账号权限或稍后重试，本次未导入。') from None
    except (TimeoutError,urllib.error.URLError) as exc:
        reason=getattr(exc,'reason',exc)
        if isinstance(reason,TimeoutError):raise ValueError(stage+f'超时（20 秒，{len(ids)} 项）；本次未导入，请稍后重试。') from None
        raise ValueError(stage+'连接失败（'+type(reason).__name__+'）；本次未导入。') from None
    if len(data)>16*1024*1024:raise ValueError('合集返回内容过大。')
    result=json.loads(data)['response']
    if key:
        rows=result.get('publishedfiledetails',[])
        for row in rows:row['consumer_app_id']=row.get('consumer_appid',row.get('consumer_app_id',0))
        if method=='GetCollectionDetails':
            for row in rows:
                if row.get('result')==1 and row.get('file_type')!=2:raise ValueError('这个编号是普通工坊项目，不是合集。')
                if row.get('result')==1 and row.get('num_children',0)>len(row.get('children',[])):raise ValueError('账号接口没有返回完整的合集成员，请稍后重试。')
            return {'collectiondetails':rows}
    return result

def fetch(value,game,request=post):
    root=collection_id(value);pending=[root];seen=set();members=[];warnings=[]
    while pending:
        batch=[i for i in pending if i not in seen];pending=[]
        if not batch:break
        if len(seen)+len(batch)>100:raise ValueError('嵌套合集超过 100 个，请分开导入。')
        seen.update(batch)
        rows=request('GetCollectionDetails',batch).get('collectiondetails',[])
        for row in rows:
            if row.get('result')!=1:
                if str(row.get('publishedfileid'))==root:raise ValueError('无法读取合集（Steam result='+str(row.get('result'))+'）。它可能私密、不可见或已移除；私密合集请配置所属账号的 API Key，或自行调整可见性后重试。')
                warnings.append('无法读取子合集 '+str(row.get('publishedfileid')));continue
            for child in row.get('children',[]):
                ident=str(child.get('publishedfileid',''))
                if not ident.isdecimal():continue
                if int(child.get('filetype',child.get('file_type',0)))==2:pending.append(ident)
                elif ident not in members:members.append(ident)
        if len(members)>10000:raise ValueError('合集成员超过 10000 项。')
    details={}
    for offset in range(0,len(members)+1,50):
        ids=([root]+members)[offset:offset+50]
        if not ids:continue
        for row in request('GetPublishedFileDetails',ids).get('publishedfiledetails',[]):details[str(row.get('publishedfileid'))]=row
    parent=details.get(root,{})
    if parent.get('result')!=1:raise ValueError('无法确认合集标题和所属游戏，请稍后重试。')
    app=str(parent.get('consumer_app_id',parent.get('consumer_appid','0')))
    if app not in ('0',APP_IDS[game]):raise ValueError('这个合集属于其他游戏，请切换游戏后重试。')
    items={}
    for ident in members:
        row=details.get(ident,{})
        if str(row.get('consumer_app_id',row.get('consumer_appid',app))) not in ('0',APP_IDS[game]):warnings.append('跳过其他游戏项目 '+ident);continue
        title = str(row.get('title') or '').strip() if row.get('result') == 1 else ''
        if not title:
            warnings.append(f"未取得标题 {ident}（Steam 结果 {row.get('result', '无详情')}）")
        items[ident] = {'title': title or '工坊 '+ident, 'updated': row.get('time_updated',0), 'confirmed': bool(title)}
    return {'id':root,'title':str(parent.get('title') or '合集 '+root),'items':items,'warnings':warnings}

def resolve(value, request=post):
    ident = collection_id(value)
    rows = request('GetPublishedFileDetails', [ident]).get('publishedfiledetails', [])
    row = next((item for item in rows if str(item.get('publishedfileid')) == ident), {})
    if row.get('result') != 1:
        raise ValueError('无法读取此工坊项目，请检查编号、可见性或账号 API Key。')
    app = str(row.get('consumer_appid', row.get('consumer_app_id', '')))
    game = next((game for game, app_id in APP_IDS.items() if app_id == app), None)
    if game is None:
        raise ValueError('这不是杀戮尖塔 I 或 II 的工坊项目。')
    file_type = row.get('file_type', row.get('filetype'))
    if file_type is None:
        collections = request('GetCollectionDetails', [ident]).get('collectiondetails', [])
        file_type = 2 if any(str(c.get('publishedfileid')) == ident and c.get('result') == 1 for c in collections) else 0
    if int(file_type) == 2:
        return game, fetch(ident, game, request)
    title = row.get('title') or '工坊 ' + ident
    return game, {'id': ident, 'title': title, 'single': True, 'items': {
        ident: {'title': title, 'updated': row.get('time_updated', 0), 'confirmed': True}
    }}


def attach(game,mods,collections,metadata=None):
    mods=[m for m in mods if not m.collection_placeholder]
    for m in mods:m.collections=[]
    inherited = set()
    for ident,collection in collections.items():
        for wid,item in collection['items'].items():
            matches=[m for m in mods if m.source=='steam_workshop' and m.workshop_id==wid]
            if not matches:
                m=Mod(game,'workshop:'+wid,item['title'],'',Path('__missing__')/wid/'__pending__','steam_workshop',wid,
                    package_root=Path('__missing__')/wid,package_title=item['title'],missing=True,loadable=False,
                    tags=['缺失'],loader_status='缺失 · 下载并扫描后识别包内模组',collection_placeholder=True)
                values=(metadata or {}).get(m.key,{})
                m.alias=values.get('alias','');m.group=values.get('group','');m.tags=list(values.get('tags',['缺失']))
                matches=[m];mods.append(m)
            for m in matches:
                if not m.missing and m.key not in inherited and m.key not in (metadata or {}):
                    pending_key = f'missing:steam_workshop:workshop:{wid}@{wid}/__pending__'
                    values = (metadata or {}).get(pending_key, {})
                    m.alias = m.alias or values.get('alias', '')
                    m.group = m.group or values.get('group', '')
                    m.tags = list(dict.fromkeys(m.tags + [tag for tag in values.get('tags', []) if tag != '缺失']))
                    inherited.add(m.key)
                if not collection.get('single'):
                    m.collections.append({'id':ident,'title':collection['title']})
                m.remote_updated=max(m.remote_updated,int(item.get('updated',0) or 0))
    return mods

def merge(model,collection):
    existing=model.state.setdefault('collections',{})
    old=existing.get(collection['id'],{})
    collection=copy.deepcopy(collection)
    items = dict(old.get('items', {}))
    for wid, item in collection['items'].items():
        if not item.get('confirmed'):
            cached = model.state.get('titles', {}).get(wid)
            if cached:
                item = {**item, 'title': cached}
            elif items.get(wid, {}).get('confirmed'):
                continue
        items[wid] = item
    collection['items'] = items
    existing[collection['id']]=collection
    model.mods=attach(model.game_id,model.mods,existing,model.state.get('metadata',{}))
    model.persist_metadata()
    return len(collection['items'])
