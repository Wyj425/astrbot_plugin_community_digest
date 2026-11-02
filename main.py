import asyncio
import json
import random
import time
import sys
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter

from .planning import Store, TZ, clean, profile, report_url
from .sources import Reader, valid
from .services import service
from .editorial import Editor
from .cards import build_card, send_card
from .subscriptions import library, tag_profile, search_key, target_umo
from .interests import Interests
from .share_context import ShareContextMixin


@register('community_digest', 'Wyj425', '结合聊天和长期记忆识别近期兴趣，共享候选并按会话推送', '0.5.1')
class CommunityDigest(ShareContextMixin, Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config=config
        self.root=Path('data/plugin_data/community_digest')
        self.root.mkdir(parents=True,exist_ok=True)
        self.store=Store(self.root/'digest.db')
        self.initialize_share_context()
        (self.root/'digest.db').chmod(0o600)
        self.reader=Reader(self.root,config,context)
        self.editor=Editor(context,config,self.store)
        self.interests=Interests(self.store,self.editor,config)
        self.groups=[]
        self.friends=[]
        self.profiles=[]
        self.groups_at=0
        self.task=None
        self.interest_task=None
        self.search_lock=asyncio.Lock()

    def bot(self):
        platform=self.context.get_platform_inst(self.config.get('platform_id',''))
        if not platform: raise RuntimeError('QQ平台不可用')
        return platform.get_client()

    async def initialize(self):
        self.context.register_web_api('/community_digest/overview',self.web_overview,['GET'],'精选候选库与推送记录')
        self.context.register_web_api('/community_digest/report',self.web_report,['GET'],'导出候选库文档')
        self.context.register_web_api('/community_digest/interests/manage',self.web_manage_interest,['POST'],'管理近期兴趣')
        self.task=asyncio.create_task(self.loop())
        self.interest_task=asyncio.create_task(self.interest_loop())

    @filter.on_llm_request(priority=sys.maxsize-9)
    async def share_context(self, event, req):
        await self.attach_shared_content_context(event, req)

    async def terminate(self):
        for task in [self.task,self.interest_task]:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
        self.store.db.close()

    async def interest_loop(self):
        await asyncio.sleep(10)
        while True:
            try:
                if self.config.get('enabled') and self.config.get('interest_enabled'):
                    info=await asyncio.wait_for(self.bot().call_action('get_login_info'),10)
                    if isinstance(info,dict):self.interests.bot_ids.add(str(info.get('user_id','')))
                    def last_success(p):
                        row=self.store.db.execute("SELECT MAX(at) FROM interest_runs WHERE gid=? AND state='ok'",(p['group_id'],)).fetchone()
                        return row[0] or 0
                    for p in sorted(list(self.profiles),key=last_success):await self.interests.refresh(p)
            except asyncio.CancelledError:raise
            except Exception as e:logger.warning('[community_digest] interest refresh error=%s',type(e).__name__)
            await asyncio.sleep(300)

    async def refresh_groups(self,force=False):
        if force or time.time()-self.groups_at>300:
            groups=await asyncio.wait_for(self.bot().call_action('get_group_list'),20)
            if isinstance(groups,dict): groups=groups.get('data',[])
            if not isinstance(groups,list): raise ValueError('群列表格式不正确')
            self.groups=[g for g in groups if str(g.get('group_id','')).isdigit()]
            # Only explicitly subscribed friends are enabled, never the entire address book.
            try:
                friends=await asyncio.wait_for(self.bot().call_action('get_friend_list'),20)
                if isinstance(friends,dict): friends=friends.get('data',[])
                self.friends=friends if isinstance(friends,list) else []
            except Exception as e:
                logger.warning('[community_digest] friend names unavailable error=%s',type(e).__name__)
            self.groups_at=time.time()
        self.profiles=[]
        for g in self.groups:
            try:
                p=profile(self.config,g['group_id'],g.get('group_name',''))
                if p['enabled'] and p['sources']: self.profiles.append(p)
            except Exception as e:
                logger.warning('[community_digest] group config invalid group=%s error=%s',g['group_id'],str(e))
        for rule in self.config.get('private_rules',[]):
            uid=str(rule.get('user_id',''))
            try:
                friend=next((x for x in self.friends if str(x.get('user_id'))==uid),{})
                p=profile(self.config,uid,friend.get('remark') or friend.get('nickname',''),'private')
                if p['enabled'] and p['sources'] and p['group_id'] not in {x['group_id'] for x in self.profiles}:
                    self.profiles.append(p)
            except Exception as e:
                logger.warning('[community_digest] private config invalid user=%s error=%s',uid,str(e))
        self.store.migrate_shared(self.config,self.profiles)
        self.profiles=[self.interests.apply(p) for p in self.profiles]

    async def loop(self):
        await asyncio.sleep(5)
        while True:
            try:
                await self.refresh_groups()
                now=datetime.now(TZ)
                self.store.purge(now.timestamp(),float(self.config.get('cache_ttl_days',3)))
                for p in self.profiles:
                    self.store.ensure_plan(p,now)
                await self.operator_preview()
                if self.config.get('enabled',False):
                    active={p['group_id']:p for p in self.profiles}
                    for job in self.store.due(now.timestamp(),set(active)):
                        try: await self.run_job(job,active[job['gid']])
                        except asyncio.CancelledError: raise
                        except Exception as e:
                            current=self.store.db.execute('SELECT state FROM push_jobs WHERE day=? AND gid=? AND seq=?',
                                (job['day'],job['gid'],job['seq'])).fetchone()
                            if current and current[0]=='reserved':
                                self.store.finish(job,'failed',detail=type(e).__name__)
                            logger.warning('[community_digest] job failed group=%s error=%s',job['gid'],type(e).__name__)
                self.store.export(self.root,self.profiles,time.time())
                topics=['# 共享标签库','','同标签共享检索和评分，各会话分别记录推送。','']
                for tag in self.tag_overview():
                    topics += ['## '+tag['name'],'检索词：'+'、'.join(tag['keywords']),
                               '订阅：'+'；'.join(('私聊 ' if s['type']=='private' else '群聊 ')+s['name'] for s in tag['subscribers']),
                               '共享候选：'+str(tag['candidates']),'']
                dest=self.root/'标签库.md';dest.write_text('\n'.join(topics),encoding='utf-8');dest.chmod(0o600)
                self.status()
            except asyncio.CancelledError: raise
            except Exception as e:
                logger.warning('[community_digest] scheduler failed error=%s',type(e).__name__)
            await asyncio.sleep(30)

    async def pick(self,p):
        if self.config.get('interest_enabled',False):
            p=self.interests.apply(p)
            if self.interests.mode(p)=='recent':
                item=await self.pick_inventory(self.interests.view(p,p['recent_tags']))
                if item:return {**item,'interest_mode':'recent','selection_reason':'匹配近期兴趣：'+'、'.join(item.get('matched_tags',[]))}
            item=await self.pick_inventory(self.interests.view(p,p['fixed_tags']))
            return {**item,'interest_mode':'fixed','selection_reason':'匹配固定兴趣：'+'、'.join(item.get('matched_tags',[]))} if item else None
        return await self.pick_inventory(p)

    async def pick_inventory(self,p):
        if not p['tags']:return None
        # Recheck inventory under the lock: simultaneous recipients must not duplicate searches.
        async with self.search_lock:
            choices=self.store.eligible(p,time.time())
            if not choices:
                limit=max(1,min(3,int(self.config.get('keywords_per_refresh',2))))
                # Each recipient has its own topic rotation; retrieval state is shared per tag/source.
                rotation='rotation:'+p['group_id']
                old=self.store.db.execute('SELECT round FROM searches WHERE profile=?',(rotation,)).fetchone()
                round_=old[0]+1 if old else 0
                tags=p['tags']
                selected=[tags[(round_*limit+i)%len(tags)] for i in range(min(limit,len(tags)))]
                self.store.mark_search(rotation,time.time(),round_,{'state':'topics_selected'})
                for tag in selected:
                    try:
                        await self.refresh_tag(p,tag)
                    except asyncio.CancelledError: raise
                    except Exception as e:
                        logger.warning('[community_digest] topic refresh failed tag=%s error=%s',tag['name'],type(e).__name__)
                choices=self.store.eligible(p,time.time())
            else:
                logger.info('[community_digest] shared cache hit target=%s eligible=%s',p['group_id'],len(choices))
        if not choices: return None
        weights={t['name']:t.get('interest_weight',0) for t in p['tags']}
        choices.sort(key=lambda x:-(x['score']+8*max((weights.get(t,0) for t in x.get('matched_tags',[])),default=0)))
        return random.choice(choices) if p['selection']=='随机合格内容' else choices[0]

    async def refresh_tag(self,p,tag):
        tp=tag_profile(self.config,p,tag)
        # Search the union of subscribed sources now, so another subscriber can consume
        # the same topic later without fetching an overlapping platform again.
        subscribers=[x for x in self.profiles if any(t['id']==tag['id'] for t in x['tags'])]
        sources=list(dict.fromkeys(s for x in subscribers+[p] for s in x['sources']))
        cooldown=max(10,int(self.config.get('refresh_cooldown_minutes',180)))*60
        for source in sources:
            key=search_key(tag,source,p['max_age_days'])
            old=self.store.db.execute('SELECT * FROM searches WHERE profile=?',(key,)).fetchone()
            if old and time.time()-old['at']<cooldown:
                continue
            turn=old['round']+1 if old else 0
            query=tag['keywords'][turn%len(tag['keywords'])]
            self.store.mark_search(key,time.time(),turn,{'state':'started','tag':tag['name']})
            try:
                pool,status=await self.reader.collect({**tp,'keywords':[query],'sources':[source]},0)
                self.store.cache(tp,pool,[],time.time())
                self.store.mark_search(key,time.time(),turn,status)
                logger.info('[community_digest] shared search tag=%s source=%s candidates=%s',tag['name'],source,len(pool))
            except Exception as e:
                self.store.mark_search(key,time.time(),turn,{'error':type(e).__name__})
        # Raw results remain reusable even if the model quota is exhausted; do not re-search.
        rows=self.store.db.execute('''SELECT i.data FROM tag_items t JOIN inventory i ON i.id=t.id
            WHERE t.tag=? AND i.expires>? AND NOT EXISTS(
              SELECT 1 FROM ratings r WHERE r.profile=? AND r.id=i.id)
            ORDER BY i.fetched DESC LIMIT 24''',(tp['cache_key'],time.time(),tp['key'])).fetchall()
        pool=[json.loads(r[0]) for r in rows]
        if pool:
            from .planning import digest
            attempt='rating:'+tp['key']+':'+digest(sorted(x['id'] for x in pool))
            last=self.store.db.execute('SELECT at FROM searches WHERE profile=?',(attempt,)).fetchone()
            if not last or time.time()-last[0]>=cooldown:
                self.store.mark_search(attempt,time.time(),0,{'state':'started'})
                ratings=await self.editor.rate(tp,pool)
                self.store.cache(tp,pool,ratings,time.time())
                self.store.mark_search(attempt,time.time(),0,{'state':'rated'})

    async def prepare(self,p,item):
        cached=self.store.db.execute('SELECT data,at FROM evidence WHERE id=?',(item['id'],)).fetchone()
        if cached and time.time()-cached['at']<1800:
            enriched={**json.loads(cached['data']),**item}
        else:
            enriched=await self.reader.enrich(item)
            self.store.db.execute('INSERT OR REPLACE INTO evidence VALUES (?,?,?)',
                (item['id'],json.dumps(enriched,ensure_ascii=False),time.time()));self.store.db.commit()
        if not valid(enriched,p['max_age_days'],time.time()):
            self.store.db.execute('UPDATE inventory SET expires=? WHERE id=?',(time.time(),item['id']))
            self.store.db.commit()
            raise ValueError('详情显示内容已过期或不可用')
        text=await self.editor.followup(p,enriched)
        card=await build_card(self.bot(),enriched,self.root,
            signer=service(self.context,'card'),
            source_in_title=self.config.get('source_in_title',False))
        return card,text,enriched

    async def send_bundle(self,target,card,text,on_card=None):
        sent=await asyncio.wait_for(send_card(self.bot(),target,card),30)
        if on_card: on_card(sent['message_id'])
        await asyncio.sleep(1)
        parts=target.split(':')
        group=parts[1]=='GroupMessage'
        comment=await asyncio.wait_for(self.bot().call_action('send_group_msg' if group else 'send_private_msg',
            **{'group_id' if group else 'user_id':int(parts[2])},
            message=[{'type':'text','data':{'text':text}}]),25)
        if not isinstance(comment,dict) or comment.get('message_id') is None:
            raise RuntimeError('短评发送状态不确定，不自动重发')
        return sent['message_id'],comment['message_id']

    async def run_job(self,job,p):
        gid=p['group_id']
        current=self.store.db.execute('SELECT state FROM push_jobs WHERE day=? AND gid=? AND seq=?',
            (job['day'],gid,job['seq'])).fetchone()
        if not current or current[0]!='reserved': return
        if self.store.day_count(gid,time.time())>=p['count']:
            self.store.finish(job,'daily_limit');return
        item=await self.pick(p)
        if not item:
            self.store.finish(job,'skipped',detail='没有达到门槛的候选或仍在检索冷却期');return
        card,text,enriched=await self.prepare(p,item)
        if self.config.get('interest_enabled',False):
            current_p=self.interests.apply(p)
            if not set(item.get('matched_tags',[])) & {t['name'] for t in current_p['tags']}:
                self.store.finish(job,'skipped',detail='选稿期间兴趣订阅已调整');return
        if not self.store.reserve_item(gid,item,time.time()):
            self.store.finish(job,'duplicate',item['id']);return
        self.interests.record(p,item,time.time())
        card_id=''
        def on_card(mid):
            nonlocal card_id
            card_id=mid
            self.store.update_delivery(gid,item['id'],'card_accepted',mid,comment=text)
            self.record_shared_content(target_umo(self.config,p),enriched,mid)
        try:
            card_id,comment_id=await self.send_bundle(
                target_umo(self.config,p),card,text,on_card)
            self.store.update_delivery(gid,item['id'],'accepted',card_id,comment_id,text)
            self.record_shared_content(target_umo(self.config,p),enriched,card_id,comment_id,text)
            await self.sync_shared_history(target_umo(self.config,p),enriched,card_id,comment_id,text)
            self.store.finish(job,'accepted',item['id'],json.dumps({'score':item['score'],'source':item['source']},ensure_ascii=False))
            logger.info('[community_digest] pushed group=%s source=%s id=%s score=%s card_id=%s comment_id=%s',
                gid,item['source'],item['id'],item['score'],card_id,comment_id)
        except BaseException:
            self.store.update_delivery(gid,item['id'],'card_only' if card_id else 'uncertain',card_id,comment=text)
            self.store.finish(job,'card_only' if card_id else 'uncertain',item['id'])
            raise

    def status(self):
        data={'enabled':self.config.get('enabled',False),'groups':[
            {k:p[k] for k in ['group_id','group_name','keywords','sources','mode','count','min_score','ttl_days']}
            for p in self.profiles], 'updated':datetime.now(TZ).isoformat(),
            'targets':[dict(id=p['target_id'],type=p['target_type'],name=p['group_name'],tags=p['keywords']) for p in self.profiles],
            'report':'候选库.md','version':'0.5.1'}
        path=self.root/'status.json'
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2));path.chmod(0o600)

    async def web_report(self):
        from astrbot.api.web import request,file_response,error_response
        if not request.username:
            return error_response('需要登录AstrBot管理后台',status_code=401)
        path=self.root/'候选库.md'
        if not path.exists():return error_response('候选库正在初始化',status_code=404)
        return file_response(path,filename='社区精选候选库.md',content_type='text/markdown; charset=utf-8')

    async def web_overview(self):
        from astrbot.api.web import request,json_response,error_response
        if not request.username:
            return error_response('需要登录AstrBot管理后台',status_code=401)
        now=time.time()
        groups=[]
        for p in self.profiles:
            items=[]
            for item in self.store.candidates(p,now):
                items.append(dict(title=item['title'],source=item['source'],url=report_url(item),score=item['score'],
                    tags=item['matched_tags'],reason=item['rating'].get('reason',''),expires=datetime.fromtimestamp(item['expires'],TZ).isoformat(),
                    state=item['state'],selection_reason=('近期兴趣' if set(item['matched_tags']) & {t['name'] for t in p.get('recent_tags',[])} else '固定兴趣')))
            jobs=[dict(x) for x in self.store.db.execute('SELECT due,state FROM push_jobs WHERE day=? AND gid=? ORDER BY due',
                (datetime.now(TZ).date().isoformat(),p['group_id']))]
            history=[]
            for h in self.store.db.execute('SELECT * FROM group_history WHERE gid=? ORDER BY at DESC LIMIT 30',(p['group_id'],)):
                history.append({**json.loads(h['snapshot'] or '{}'),'at':h['at'],'state':h['state'],'comment':h['comment']})
            searches=[]
            for tag in p['tags']:
                for source in p['sources']:
                    search=self.store.db.execute('SELECT at,status FROM searches WHERE profile=?',(search_key(tag,source,p['max_age_days']),)).fetchone()
                    if search: searches.append(dict(tag=tag['name'],source=source,**dict(search)))
            groups.append(dict(id=p['group_id'],type=p['target_type'],target_id=p['target_id'],name=p['group_name'],keywords=p['keywords'],min_score=p['min_score'],
                ttl=p['ttl_days'],items=sorted(items,key=lambda x:-x['score']),jobs=jobs,history=history,
                interests=self.interests.overview(p['group_id']),fixed_tags=[x['name'] for x in p.get('fixed_tags',p['tags'])],
                searches=searches,last_search=max(searches,key=lambda x:x['at']) if searches else None))
        usage=self.store.db.execute('SELECT count(*),sum(input),sum(output) FROM model_usage WHERE day=?',
            (datetime.now(TZ).date().isoformat(),)).fetchone()
        report=self.root/'候选库.md'
        return json_response(dict(enabled=self.config.get('enabled',False),groups=groups,tags=self.tag_overview(),
            shared_items=self.store.db.execute('SELECT count(*) FROM inventory WHERE expires>?',(now,)).fetchone()[0],
            usage=dict(calls=usage[0],input=usage[1] or 0,output=usage[2] or 0),
            report=report.read_text() if report.exists() else '候选库正在初始化'))

    async def web_manage_interest(self):
        from astrbot.api.web import request,json_response,error_response
        if not request.username:return error_response('需要登录AstrBot管理后台',status_code=401)
        body=await request.json(default={})
        if not isinstance(body,dict) or body.get('target') not in {p['group_id'] for p in self.profiles}:
            return error_response('请选择已开启订阅的会话',status_code=400)
        try:self.interests.manage(body['target'],body.get('tag'),body.get('action'))
        except ValueError as e:return error_response(str(e),status_code=400)
        await self.refresh_groups()
        return json_response({'ok':True})

    def tag_overview(self):
        tags,_=library(self.config)
        for p in self.profiles:
            for tag in p['tags']:tags[tag['id']]=tag
        result=[]
        for tag in tags.values():
            tp=tag_profile(self.config,{},tag)
            subscribers=[dict(id=p['group_id'],name=p['group_name'],type=p['target_type']) for p in self.profiles
                         if any(t['id']==tag['id'] for t in p['tags'])]
            count=self.store.db.execute('''SELECT count(*) FROM tag_items t JOIN inventory i ON i.id=t.id
                WHERE t.tag=? AND i.expires>?''',(tp['cache_key'],time.time())).fetchone()[0]
            result.append(dict(name=tag['name'],keywords=tag['keywords'],subscribers=subscribers,candidates=count))
        return result

    async def operator_preview(self):
        # Operator-only local file, no chat/LLM entry point. Default is NO SEND.
        path=self.root/'operator_request.json'
        if not path.exists(): return
        path.replace(self.root/'operator_started.json')
        result={'state':'started','items':[]}
        def save():
            dest=self.root/'operator_result.json'
            dest.write_text(json.dumps(result,ensure_ascii=False,indent=2));dest.chmod(0o600)
        save()
        try:
            request=json.loads((self.root/'operator_started.json').read_text())
            for gid in request.get('groups',[]):
                p=next(x for x in self.profiles if x['group_id']==str(gid))
                item=await self.pick(p)
                if not item:
                    result['items'].append({'group':gid,'state':'no_qualified_candidate'});save();continue
                record={'group':gid,'id':item['id'],'score':item['score'],'title':item['title'],
                        'source':item['source'],'state':'cached'}
                if request.get('prepare',False):
                    card,text,enriched=await self.prepare(p,item)
                    record.update(comment=text,evidence_kind=enriched['evidence_kind'])
                    target=request.get('private_target')
                    if target:
                        prefix=self.config.get('platform_id','')+':FriendMessage:'
                        if not target.startswith(prefix) or not target[len(prefix):].isdigit():
                            raise ValueError('只允许私聊预览')
                        a,b=await self.send_bundle(target,card,text)
                        record.update(state='private_accepted',card_id=a,comment_id=b)
                result['items'].append(record);save()
            result['state']='finished'
        except Exception as e:
            result.update(state='failed',error=type(e).__name__)
        save()
