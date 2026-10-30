"""Incremental, independent ZIP release packs. No credentials or game config included."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile
from datetime import datetime
from contextlib import contextmanager
from core import atomic_json


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def pending(store):
    return [(key,p,v) for key,p in store.index['packages'].items() for v in p['versions'] if not v.get('export')]


def info(store):
    return {'pending':len(pending(store)), 'releases':list(store.index.get('releases',{}).values())}


def export_release(store, destination, limit=512*1024**2, progress=lambda text:None, remove_loose=True):
    items=pending(store)
    if not items:return {'versions':0,'parts':0,'message':'没有尚未导出的模组版本。'}
    if limit<1:raise ValueError('分包大小必须大于零')
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    sequence=store.index.get('release_sequence',len(store.index.get('releases',{})))+1
    batch=f'V{sequence}'
    final=destination/batch;stage=Path(tempfile.mkdtemp(prefix='.'+batch+'-',dir=destination))
    groups=[];current=[];size=0
    for item in items:
        path=store.archive_path(item[1],item[2])
        if not path or not path.is_file():raise ValueError('未导出版本的本地备份缺失，请重新扫描或找回文件：'+str(path))
        if current and size+path.stat().st_size>limit:groups.append(current);current=[];size=0
        current.append(item);size+=path.stat().st_size
    if current:groups.append(current)
    manifest={'schema':1,'kind':'spire-mod-release','batch':batch,'created':datetime.now().isoformat(timespec='seconds'),'parts':[]}
    assignments=[]
    try:
        for n,group in enumerate(groups,1):
            name=f'{batch}.{n}.zip';path=stage/name;entries=[]
            with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as outer:
                for identity,package,version in group:
                    loose=store.archive_path(package,version)
                    store._verify_archive(loose,version['files'])
                    entry={'package':identity,'game':package['game'],'source':package['source'],
                           'version':{k:v for k,v in version.items() if k!='export'},'entry':version['archive'],'sha256':file_hash(loose)}
                    entries.append(entry);outer.write(loose,entry['entry'])
                part={'schema':1,'kind':'spire-mod-release','batch':batch,'part':name,'entries':entries}
                outer.writestr('manifest.json',json.dumps(part,ensure_ascii=False,indent=2))
            # Read every nested ZIP back before recording it as exported.
            with zipfile.ZipFile(path) as outer:
                for entry in entries:
                    with outer.open(entry['entry']) as stream:
                        if hashlib.file_digest(stream,'sha256').hexdigest()!=entry['sha256']:raise ValueError('导出包校验失败')
            part_record={'file':name,'bytes':path.stat().st_size,'sha256':file_hash(path),'entries':entries}
            manifest['parts'].append(part_record)
            for (_,_,version),entry in zip(group,entries):
                assignments.append((version,{'batch':batch,'part':name,'entry':entry['entry'],'sha256':entry['sha256']}))
            progress(f'[{n}/{len(groups)}] 已校验 {name} · {len(entries)} 个版本 · {path.stat().st_size/1024**2:.1f} MiB')
        stage.rename(final)
        # Only published, fully verified parts enter the durable export ledger.
        record={'batch':batch,'sequence':sequence,'created':manifest['created'],'directory':str(final),
                'parts':[{'file':p['file'],'bytes':p['bytes'],'sha256':p['sha256'],'versions':len(p['entries'])} for p in manifest['parts']]}
        store.index.setdefault('releases',{})[batch]=record
        store.index['release_sequence']=sequence
        for version,ref in assignments:version['export']=ref
        for part in manifest['parts']:store.index.setdefault('release_locations',{})[part['file']]=str(final/part['file'])
        store.save()
        atomic_json(store.root/'releases'/f'{batch}.json',manifest)
        atomic_json(store.root/'releases'/'backup-catalog.json',store.index)
        if remove_loose:
            for _,package,version in items:
                path=store.archive_path(package,version)
                if path and path.is_file():
                    if not path.resolve().is_relative_to(store.root.resolve()):raise ValueError('备份清理路径超出历史库')
                    path.unlink()
        return {'versions':len(items),'parts':len(groups),'batch':batch,'directory':str(final)}
    finally:
        if stage.exists():shutil.rmtree(stage)


@contextmanager
def open_export(store, version):
    ref=version['export'];location=store.index.get('release_locations',{}).get(ref['part'],'')
    path=Path(location) if location else None
    if not path or not path.is_file():
        raise FileNotFoundError(f"请找回模组包 {ref['part']}（导出批次 {ref['batch']}），通过“导出备份包 → 导入备份包”定位后再恢复。")
    with zipfile.ZipFile(path) as outer, tempfile.TemporaryFile() as temp:
        h=hashlib.sha256()
        with outer.open(ref['entry']) as stream:
            while block:=stream.read(1024*1024):h.update(block);temp.write(block)
        if h.hexdigest()!=ref['sha256']:raise ValueError('导出包中的模组备份校验失败')
        temp.seek(0)
        with zipfile.ZipFile(temp) as nested:yield lambda rel:nested.open(rel)


def register_part(store, path, progress=lambda text:None):
    """Rebuild records from this pack alone. Never trust an imported restore destination."""
    import copy
    import re
    from versions import safe_relative
    path=Path(path).resolve();validated=[]
    with zipfile.ZipFile(path) as outer:
        manifest=json.loads(outer.read('manifest.json'))
        if manifest.get('schema')!=1 or manifest.get('kind')!='spire-mod-release':raise ValueError('不是本管理器的备份包')
        part_name=store.index.get('part_aliases',{}).get(manifest['part'],manifest['part'])
        if not re.fullmatch(r'V\d+\.\d+\.zip',part_name) and re.fullmatch(r'V\d+\.\d+\.zip',path.name):part_name=path.name
        incoming={(e['entry'],e['sha256']) for e in manifest['entries']}
        existing={(v['export']['entry'],v['export']['sha256']) for p in store.index['packages'].values() for v in p['versions'] if v.get('export',{}).get('part')==part_name}
        if existing-incoming:
            suffix=hashlib.sha256(json.dumps(sorted(incoming)).encode()).hexdigest()[:8]
            part_name=Path(part_name).stem+'-'+suffix+'.zip'
        safe_relative(part_name)
        if '/' in part_name:raise ValueError('无效备份包名称')
        for e in manifest['entries']:
            if e['game'] not in ('sts1','sts2'):raise ValueError('未知游戏')
            safe_relative(e['entry']);v=e['version'];files=v['files']
            for rel,digest in files.items():
                safe_relative(rel)
                if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('无效文件校验值')
            expected=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
            if expected!=v['digest']:raise ValueError('版本清单校验失败')
            with tempfile.TemporaryFile() as temp:
                h=hashlib.sha256()
                with outer.open(e['entry']) as stream:
                    while block:=stream.read(1024*1024):h.update(block);temp.write(block)
                if h.hexdigest()!=e['sha256']:raise ValueError('导入包校验失败')
                temp.seek(0);store._verify_archive(temp,files)
            validated.append(copy.deepcopy(e));progress('已校验 '+e['entry'])
    if not validated:raise ValueError('备份包没有模组版本')
    # Nothing is committed until the entire part has passed validation.
    original=copy.deepcopy(store.index);added=0
    try:
        for e in validated:
            v=e['version'];folder=PurePosixPath(e['entry']).parts[1]
            package=store.index['packages'].get(e['package'])
            version=next((x for x in package['versions'] if x['digest']==v['digest']),None) if package else None
            if not version:
                identity='backup:'+e['game']+':'+folder
                package=store.index['packages'].setdefault(identity,{'game':e['game'],'source':folder,'external':True,'versions':[]})
                version=next((x for x in package['versions'] if x['digest']==v['digest']),None)
                if not version:
                    records=[]
                    for m in v.get('mods',[]):
                        if not isinstance(m,dict) or not isinstance(m.get('id'),str):raise ValueError('无效模组信息')
                        records.append({k:m[k] for k in ('id','name','nickname','version','compatibility','workshop_id') if k in m})
                    if not records:records=[{'id':folder,'name':'模组 '+folder,'version':'未声明','workshop_id':folder if folder.isdigit() else ''}]
                    for m in records:m.setdefault('name',m['id'])
                    version={'digest':v['digest'],'files':v['files'],'mods':records,'created':v.get('created',''),'origin':'import','archive':e['entry']}
                    package['versions'].append(version);added+=1
            if version['files']!=v['files']:raise ValueError('已有版本与包内记录不一致')
            version['export']={'batch':manifest['batch'],'part':part_name,'entry':e['entry'],'sha256':e['sha256']}
        store.index.setdefault('release_locations',{})[part_name]=str(path)
        if part_name!=manifest['part'] and not re.fullmatch(r'V\d+\.\d+\.zip',manifest['part']) and manifest['part'] not in store.index.get('part_aliases',{}):store.index.setdefault('part_aliases',{})[manifest['part']]=part_name
        # Reserve imported sequence numbers to avoid confusing a later export with this batch.
        import re
        match=re.fullmatch(r'V(\d+)\.(\d+)\.zip',part_name)
        if match:store.index['release_sequence']=max(store.index.get('release_sequence',0),int(match[1]))
        store.save()
    except Exception:
        store.index=original;raise
    return {'versions':len(validated),'added':added,'part':part_name}


def register_many(store, paths, progress=lambda text:None):
    results=[];errors=[];seen=set()
    for path in paths:
        path=Path(path).resolve()
        if path in seen:continue
        seen.add(path)
        try:results.append(register_part(store,path,progress))
        except Exception as exc:errors.append({'file':path.name,'error':str(exc)});progress('跳过 '+path.name+'：'+str(exc))
    return {'packs':len(results),'versions':sum(r['versions'] for r in results),'added':sum(r['added'] for r in results),'errors':errors,'results':results}
