"""Portable mod lists: allowlisted metadata, never game files or machine paths."""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from core import Mod

FORMAT = 'spire-mod-manager-list'
MISSING = '缺失'


def identity(row):
    return hashlib.sha256(json.dumps([row['source'], row['workshop_id'], row['id'], row['file']], ensure_ascii=False).encode()).hexdigest()[:24]


def normalize(data, game):
    if not isinstance(data, dict) or data.get('format') != FORMAT or data.get('schema') != 1:
        raise ValueError('不是受支持的模组清单（需要新版清单 JSON）。')
    if data.get('game') != game:
        raise ValueError('清单所属游戏不同，请先切换到对应的尖塔 I / II。')
    rows = data.get('mods')
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('清单项目数量无效。')
    result = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict): raise ValueError('清单项目格式无效。')
        row = {}
        for key in ('id', 'name', 'version', 'alias', 'group', 'package_title', 'workshop_id', 'source', 'file'):
            value = raw.get(key, '')
            if not isinstance(value, str) or len(value) > 2048: raise ValueError('清单文本字段无效。')
            row[key] = value
        if row['source'] not in ('steam_workshop', 'mods_directory') or not row['id']:
            raise ValueError('模组来源或 ID 无效。')
        if row['source']=='steam_workshop' and (not row['workshop_id'].isdecimal() or not 0<int(row['workshop_id'])<2**64):
            raise ValueError('工坊编号无效。')
        file = PurePosixPath(row['file'])
        if not row['file'] or file.is_absolute() or any(x in row['file'] for x in ('\\', ':')) or '..' in file.parts:
            raise ValueError('清单只能包含包内相对文件名。')
        tags = raw.get('tags', [])
        if not isinstance(tags, list) or len(tags)>200 or any(not isinstance(t,str) or len(t)>100 for t in tags):
            raise ValueError('标签格式无效。')
        row['tags'] = sorted({t.strip() for t in tags if t.strip() and t.strip()!=MISSING})
        if not isinstance(raw.get('enabled', False), bool): raise ValueError('开关状态无效。')
        row['enabled'] = raw.get('enabled', False)
        ident = identity(row)
        if ident in seen: raise ValueError('清单存在重复项目。')
        seen.add(ident); result.append(row)
    orders = data.get('group_orders', {})
    if not isinstance(orders,dict): raise ValueError('分组顺序无效。')
    orders = {k:list(v) for k,v in orders.items() if k in ('自定义分组','标签分组') and isinstance(v,list) and len(v)<10000 and all(isinstance(x,str) and len(x)<2048 for x in v)}
    profiles = data.get('profiles', {})
    if not isinstance(profiles,dict) or len(profiles)>200: raise ValueError('开关方案格式无效。')
    clean_profiles={}
    for name,profile in profiles.items():
        if not isinstance(name,str) or len(name)>200 or not isinstance(profile,dict): raise ValueError('开关方案格式无效。')
        enabled=profile.get('enabled',[])
        if not isinstance(enabled,list) or len(enabled)>10000 or any(not isinstance(x,str) or x not in seen for x in enabled): raise ValueError('开关方案引用无效。')
        clean_profiles[name]={'enabled':enabled,'master':bool(profile.get('master',True))}
    return {'format':FORMAT,'schema':1,'game':game,'mods':result,'group_orders':orders,'profiles':clean_profiles}


def record(mod):
    return {'id':mod.id,'name':mod.name,'version':mod.version,'alias':mod.alias,'group':mod.group,
            'package_title':mod.package_title,'workshop_id':mod.workshop_id,'source':mod.source,
            'file':mod.path.relative_to(mod.package_root).as_posix() if mod.package_root else mod.path.name,
            'tags':[t for t in mod.tags if t!=MISSING], 'enabled':mod.wanted_enabled if mod.missing else mod.enabled}


def match(row, mods):
    candidates=[m for m in mods if not m.missing and (m.source,m.workshop_id,m.id)==(row['source'],row['workshop_id'],row['id'])]
    exact=[m for m in candidates if record(m)['file']==row['file']]
    if len(exact)==1:return exact[0]
    return candidates[0] if len(candidates)==1 else None


def placeholder(game,row):
    root=Path('__missing__')/(row['workshop_id'] or 'local')
    mod=Mod(game,row['id'],row['name'],row['version'],root/row['file'],row['source'],row['workshop_id'],
            package_root=root,missing=True,wanted_enabled=row['enabled'],loadable=False,
            loader_status='缺失 · 尚未在本地扫描到对应文件')
    apply_metadata(mod,row)
    mod.tags.append(MISSING)
    return mod


def apply_metadata(mod,row):
    mod.group=row['group'];mod.tags=list(row['tags']);mod.alias=row['alias'];mod.package_title=row['package_title']


def export_list(model):
    rows=[record(m) for m in model.mods]
    references={m.key:identity(row) for m,row in zip(model.mods,rows)}
    profiles={name:{'enabled':[references[k] for k in p.get('enabled',[]) if k in references], 'master':p.get('master',True)} for name,p in model.state.get('profiles',{}).items()}
    return normalize({'format':FORMAT,'schema':1,'game':model.game_id,'mods':rows,'profiles':profiles,
                      'group_orders':model.view.get('group_orders',{})},model.game_id)


def preview(model,data):
    data=normalize(data,model.game_id)
    found=sum(match(row,model.mods) is not None for row in data['mods'])
    return {'total':len(data['mods']),'local':found,'missing':len(data['mods'])-found,
            'profiles':len(data['profiles']),'names':[row['alias'] or row['package_title'] or row['name'] for row in data['mods'][:15]]}


def attach(game,local,records,profiles,apply_pending=True):
    records=copy.deepcopy(records);profiles=copy.deepcopy(profiles)
    mods=list(local)
    for row in records.values():
        found=match(row,local)
        missing=placeholder(game,row)
        if found is None:
            mods.append(missing)
        else:
            apply_metadata(found,row)
            if apply_pending and row.get('pending') and found.loadable:
                found.enabled=row['enabled'];row['pending']=False
            for profile in profiles.values():
                profile['enabled']=[found.key if k==missing.key else k for k in profile.get('enabled',[])]
    return mods,records,profiles


def import_list(model,data,overwrite=False,switches=True):
    data=normalize(data,model.game_id)
    rows=copy.deepcopy(model.list_records)
    for row in data['mods']:
        row=copy.deepcopy(row); ident=identity(row)
        found=match(row,model.mods)
        if found:
            if not overwrite:
                row.update(alias=found.alias or row['alias'],group=found.group or row['group'],
                           tags=sorted(set(found.tags)-{MISSING}|set(row['tags'])),package_title=found.package_title or row['package_title'])
            apply_metadata(found,row)
            if switches and found.loadable:found.enabled=row['enabled']
            row['enabled']=found.enabled
        row['pending']=found is None and switches
        rows[ident]=row
    local=[m for m in model.mods if not m.missing]
    model.mods,model.list_records,profiles=attach(model.game_id,local,rows,model.state.get('profiles',{}))
    references={identity(record(m)):m.key for m in model.mods}
    for name,p in data['profiles'].items():
        if overwrite or name not in profiles:
            profiles[name]={'enabled':[references[i] for i in p['enabled'] if i in references], 'master':p['master']}
    model.state['profiles']=profiles
    orders=model.view.setdefault('group_orders',{})
    for mode,order in data['group_orders'].items():
        orders[mode]=list(dict.fromkeys((order+orders.get(mode,[])) if overwrite else (orders.get(mode,[])+order)))
    model.state['view']=copy.deepcopy(model.view)
    model.persist_metadata()
    return preview(model,data)


def forget_missing(model,keys):
    targets=model.targets(keys)
    if any(not m.missing for m in targets):raise ValueError('只能从清单中移除缺失项；本地文件不会被删除。')
    ids={identity(record(m)) for m in targets}
    model.list_records={k:v for k,v in model.list_records.items() if k not in ids}
    model.mods=[m for m in model.mods if m not in targets]
    model.persist_metadata()
    return len(targets)
