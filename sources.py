import time, re
BLOCKED=re.compile(r'抽奖|代练|卖号|买号|外挂|推广|充值|赌博')
from .services import service
from astrbot.api import logger
def valid(item, max_age, now):
    if not item.get('title') or not item.get('cover') or BLOCKED.search(item['title']):
        return False
    stamp = item.get('published_at')
    return not stamp or -300 <= now-stamp <= max_age*86400

def matches(text, words):
    text = text.casefold()
    for word in words:
        aliases = [word]
        if word.lower() in ['apex','apex英雄']: aliases += ['Apex英雄','Apex Legends']
        if word.lower() in ['lol','英雄联盟']: aliases += ['LOL','英雄联盟']
        if word in ['游戏趣事','有趣日常','二游']: aliases += {'游戏趣事':['游戏'], '有趣日常':['趣事','搞笑','日常'], '二游':['原神','鸣潮','崩坏','绝区零','明日方舟']}[word]
        if any(w.casefold() in text for w in aliases): return True
    return False

class Reader:
    def __init__(self, root, config, context):self.root,self.config,self.context=root,config,context
    async def collect(self,p,round_=0):
        limit=max(1,min(3,int(self.config.get('keywords_per_refresh',2))))
        words=[p['keywords'][(round_*limit+i)%len(p['keywords'])] for i in range(min(limit,len(p['keywords'])))]
        pools={};statuses={}
        for source in p['sources']:
            try:
                items=await service(self.context,source).search(words,p.get('max_age_days',7))
                pools[source]=[x for x in items if valid(x,p.get('max_age_days',7),time.time())][:8]
                statuses[source]='ok:'+str(len(pools[source]))
            except Exception as e:
                statuses[source]=type(e).__name__;logger.warning('[platform consumer] %s unavailable: %s',source,type(e).__name__)
        out={}
        for i in range(8):
            for batch in pools.values():
                if i<len(batch):
                    item=batch[i]
                    if item['id'] in out:out[item['id']]['queries']=list(set(out[item['id']].get('queries',[])+item.get('queries',[])))
                    else:out[item['id']]=item
        return list(out.values())[:24],statuses
    async def enrich(self,item):
        base={**item,'evidence':item.get('description',''),'evidence_kind':'标题与简介','hot_comment':''}
        try:
            result=await service(self.context,item['source']).read(item['url'],images=False)
            # Preserve canonical candidate identity used by historical sent records.
            return {**base,**result,'id':item['id'],'url':item['url']}
        except Exception as e:
            logger.warning('[platform consumer] detail unavailable: %s',type(e).__name__)
            base['read_error']=type(e).__name__;return base
