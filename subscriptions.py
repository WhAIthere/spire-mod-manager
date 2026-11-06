"""Explicit subscription previews; metadata validation precedes any client write."""
from pathlib import Path
import time
from core import running_game_processes
from steam_workshop import check_account
from workshop_collections import post

def preview(model,keys,key=''):
    with model.lock:
        if getattr(model,'demo_mode',False):raise ValueError('空白清单体验禁止真实订阅。')
        if model.game_id!='sts2':raise ValueError('辅助订阅目前仅支持尖塔 II；尖塔 I 请打开工坊页面订阅。')
        if not model.game_dir or not Path(model.game_dir).is_dir():raise ValueError('请先设置正确的塔二游戏目录。')
        mods=model.targets(keys)
        if not mods or any(m.source!='steam_workshop' or not m.workshop_id.isdecimal() for m in mods):raise ValueError('请选择创意工坊项目。')
        names = {m.workshop_id: m.nickname for m in mods}
        ids = list(names)
        if len(ids)>50:raise ValueError('每次最多辅助订阅 50 项，请分批选择。')
        plan = {'game':'sts2','game_dir':model.game_dir,'config_path':model.config_path,
                'revision':model.revision,'created':time.monotonic()}
        steam = model.steam
    if running_game_processes('sts2'):raise ValueError('请先关闭游戏和加载器，再订阅缺失模组。')
    snapshot=steam(plan['game_dir'])
    check_account(snapshot,plan['config_path'])
    details=post('GetPublishedFileDetails',ids,key).get('publishedfiledetails',[])
    byid={str(m.get('publishedfileid')):m for m in details}
    for ident in ids:
        row=byid.get(ident,{})
        if row.get('result')!=1 or str(row.get('consumer_app_id',row.get('consumer_appid','')))!="2868840" or row.get('file_type')==2:
            raise ValueError('无法确认项目属于塔二普通模组，未生成订阅计划：'+ident)
    return {**plan, 'account':snapshot['account'],
            'items':[{'id':i,'name':names[i],'already':i in snapshot['items']} for i in ids]}


def validate(model,plan):
    if getattr(model,'demo_mode',False) or model.game_id!=plan['game'] or model.revision!=plan['revision'] or model.game_dir!=plan['game_dir'] or model.config_path!=plan['config_path'] or time.monotonic()-plan['created']>300:
        raise ValueError('订阅预览已变化或超过五分钟，请重新确认。')


def validate_account(steam,plan):
    if running_game_processes('sts2'):raise ValueError('请先关闭游戏和加载器。')
    snapshot=steam(plan['game_dir'])
    check_account(snapshot,plan['config_path'])
    if snapshot['account']!=plan['account']:raise ValueError('Steam 账号已变化，请重新确认。')
