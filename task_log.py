"""User-facing task rows; detailed transport events remain separate."""
import copy

class TaskLog:
    def __init__(self):
        self.rows={};self.problems={};self.batches={};self.failed_batches={};self.sequence=0
    def row(self,key,text,status='working'):
        if key not in self.rows:
            if key!='stage' and 'stage' in self.rows:self.rows['stage']['status']='success'
            self.sequence+=1;self.rows[key]={'id':key,'order':self.sequence}
        self.rows[key].update(text=text,status=status)
    def title(self,entry):
        item=str(entry.get('id',''));status=entry.get('status');request=entry.get('request_id')
        key='request:'+str(request) if request is not None else 'item:'+item
        size=entry.get('batch_size',1);error=entry.get('error') or '没有取得可用信息'
        if status=='requesting':
            if not entry.get('seconds',0):
                for row in self.rows.values():
                    if row['id'].startswith('wait:') and row['status']=='working':row.update(text='等待结束，继续请求',status='success')
            seconds=entry.get('seconds',0)
            self.row(key,f'正在更新 {size} 项 · 等待网络返回 {seconds} 秒')
            return
        if status=='waiting':
            self.row('wait:'+item,f'等待服务器允许重试 · 剩余约 {entry.get("wait_seconds",0)} 秒' if entry.get('wait_kind')=='server_cooldown' else f'网页查询间隔 · 剩余约 {entry.get("wait_seconds",0)} 秒')
            return
        if status=='success':
            self.problems.pop(('title',item),None)
            if entry.get('resumed'):
                batch=self.batches.setdefault('cached',set());batch.add(item);self.row('cached',f'已读取 {len(batch)} 项现有结果','success');return
            batch=self.batches.setdefault(key,set());batch.add(item)
            failed=self.failed_batches.get(key,set());text=f'本批 {size} 项全部更新成功' if len(batch)>=size else f'本批已更新 {len(batch)}/{size} 项'+(f' · {len(failed)} 项需要关注' if failed else '');self.row(key,text,'warning' if failed else 'success' if len(batch)>=size else 'working')
        elif status in ('retry','failed','deferred','removed','halted'):
            affected=(entry.get('batch_ids') or [item]) if status in ('retry','failed') else [item]
            if status in ('deferred','removed','halted'):
                self.problems[('title',item)]={'id':item,'phase':'标题','reason':error}
            self.failed_batches.setdefault(key,set()).update(affected)
            self.row(key,('请求失败：' if status in ('retry','failed') else '部分项目无法确认：')+error,'info' if status in ('retry','failed') else 'warning')
    def cover(self,done,total,item,text):
        if item=='缓存' or text.startswith('已有 '):
            self.row('covers',f'封面已有 {done}/{total} 项，无需重复下载','success' if done>=total else 'info');return
        if '已发送 API' in text:
            self.row('cover-request:'+str(item),text);return
        if text.startswith('封面信息已返回'):
            key='cover-request:'+str(item)
            if key in self.rows:self.row(key,text,'success')
            return
        if text=='封面已缓存':
            self.problems.pop(('cover',str(item)),None)
            self.row('covers',f'封面已更新 {done}/{total} 项','success' if done>=total else 'working')
        elif '等待' in text:self.row('cover-request',text)
        elif str(item).isdecimal():
            self.problems[('cover',str(item))]={'id':str(item),'phase':'封面','reason':text}
            self.row('covers',f'封面已处理 {done}/{total} 项 · 有项目需要关注','warning')
    def snapshot(self,running=False):
        rows=copy.deepcopy(list(self.rows.values()))
        if not running:
            for row in rows:
                if row['status']=='working':
                    row['status']='stopped'
                    if '等待' in row['text']:row['text']+=' · 已停止等待'
        return {'rows':rows[-150:],'problems':[] if running else copy.deepcopy(list(self.problems.values()))}

def clean_saved_view(view):
    """Correct presentation mistakes in older saved overviews without rewriting raw logs."""
    if not view:return view
    view=copy.deepcopy(view)
    cached=next((p for p in view.get('problems',[]) if p.get('id')=='缓存' and p.get('reason','').startswith('已有 ')),None)
    view['problems']=[p for p in view.get('problems',[]) if str(p.get('id','')).isdecimal()]
    for row in view.get('rows',[]):
        row['text']=row['text'].replace(' · 本次已结束','')
        if row['text'].startswith('请求失败：账号 API'):row['status']='info'
        if cached and row['id']=='covers':row.update(text=cached['reason'],status='success')
    return view
