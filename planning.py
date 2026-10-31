"""Persistent shared candidates and independent conversation delivery plans."""
import hashlib
import html
import json
import random
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TZ = ZoneInfo('Asia/Shanghai')
SOURCES = ['B站', '小红书', '小黑盒']
DEFAULT_KEYWORDS = ['Apex', '英雄联盟', '游戏趣事']


def report_url(item):
    url=urlsplit(item['url'])
    query=[(k,v) for k,v in parse_qsl(url.query,keep_blank_values=True) if k not in ['xsec_token','xsec_source']]
    return urlunsplit((url.scheme,url.netloc,url.path,urlencode(query),''))


def clean(value, limit=250):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', '', str(value))))[:limit].strip()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def minute(value, end=False):
    if end and value == '24:00':
        return 1440
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(value)):
        raise ValueError('时间应为 HH:MM；结束时间可用24:00')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def profile(config, gid, name='', kind='group'):
    from .subscriptions import resolve_tags, tag_profile
    if kind not in ['group', 'private'] or not str(gid).isdigit():
        raise ValueError('会话类型或QQ号码无效')
    field = 'group_id' if kind == 'group' else 'user_id'
    rules = [r for r in config.get('group_rules' if kind == 'group' else 'private_rules', []) if str(r.get(field)) == str(gid)]
    if len(rules) > 1:
        raise ValueError('同一会话只能配置一次')
    rule = rules[0] if rules else {}
    words = rule.get('tags') or rule.get('keywords') or config.get('default_tags') or config.get('default_keywords') or DEFAULT_KEYWORDS
    tags = resolve_tags(config, words)
    words = [t['name'] for t in tags]
    if not words:
        raise ValueError('至少订阅一个标签')
    sources = rule.get('sources') or SOURCES
    if not isinstance(sources, list) or not set(sources) <= set(SOURCES):
        raise ValueError('未知平台')
    sources = [s for s in SOURCES if s in sources and
               (s == 'B站' or config.get({'小红书':'xhs_enabled', '小黑盒':'heybox_enabled'}[s], False))]
    mode = rule.get('schedule_mode', '跟随全局')
    if mode == '跟随全局':
        mode = config.get('schedule_mode', '随机时间')
    count = int(rule.get('daily_count') or config.get('daily_count', 2))
    if not 1 <= count <= 6:
        raise ValueError('每天推送次数应在1至6之间')
    score = int(rule.get('min_score') or config.get('min_score', 80))
    ttl = float(config.get('cache_ttl_days', 3))
    max_age = float(config.get('published_max_age_days', 7))
    if not 0 <= score <= 100 or not 0 < ttl <= 30 or not 0 < max_age <= 90:
        raise ValueError('评分或有效期配置无效')
    target_key = str(gid) if kind == 'group' else 'private:' + str(gid)
    p = dict(group_id=target_key, group_name=clean(rule.get('group_name' if kind == 'group' else 'user_name') or name or str(gid), 80),
             target_id=str(gid), target_type=kind, target_key=target_key, tags=tags,
             umo=f"{config.get('platform_id','')}:{'GroupMessage' if kind == 'group' else 'FriendMessage'}:{gid}",
             enabled=rule.get('enabled', True) if kind == 'group' else bool(rules) and rule.get('enabled', True), keywords=words, sources=sources,
             mode=mode, count=count, min_score=score, ttl_days=ttl, max_age_days=max_age,
             fixed_times=rule.get('fixed_times') or config.get('fixed_times', ['12:30','20:30']),
             start=config.get('random_start', '09:00'), end=config.get('random_end', '24:00'),
             gap=int(config.get('min_gap_minutes', 90)),
             persona_id=rule.get('persona_id', ''),
             selection=config.get('selection_mode', '最高分'),
             comment_mode=config.get('comment_mode', '人设短评'))
    p['key'] = digest({'keywords':sorted(words), 'sources':sources, 'max_age':max_age,
                       'provider':config.get('provider_id'), 'rubric':2})
    for tag in tags:
        tag['rating_key'] = tag_profile(config,p,tag)['key']
    return p


def schedule_minutes(p, day):
    if p['mode'] == '固定时间':
        return sorted({minute(s) for s in p['fixed_times']})[:p['count']]
    if p['mode'] != '随机时间':
        raise ValueError('未知时间模式')
    start, end = minute(p['start']), minute(p['end'], end=True)
    gap = max(1, p['gap'])
    if end <= start or end - start < (p['count'] - 1) * gap + 1:
        raise ValueError('随机时间窗口不足以满足次数与最小间隔')
    rng = random.Random(digest([p['group_id'], day, p['start'], p['end'], p['count'], gap]))
    # Transform ordered samples so all gaps are at least `gap`, without retries.
    width = end - start - (p['count'] - 1) * (gap - 1)
    base = sorted(rng.sample(range(width), p['count']))
    return [start + v + i * (gap - 1) for i, v in enumerate(base)]


def freshness(item, now):
    published = item.get('published_at')
    if not published:
        return 4
    days = max(0, (now - float(published)) / 86400)
    return 15 if days <= 1 else 12 if days <= 3 else 8 if days <= 7 else 2


def total_score(item, rating, now):
    value = sum(rating[k] for k in ['fit', 'quality', 'fun']) + freshness(item, now)
    return min(value, 79) if rating['fit'] < 24 or rating['quality'] < 10 else value


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS inventory(id TEXT PRIMARY KEY,data TEXT, fetched REAL, expires REAL);
          CREATE TABLE IF NOT EXISTS ratings(profile TEXT,id TEXT,rating TEXT,PRIMARY KEY(profile,id));
          CREATE TABLE IF NOT EXISTS searches(profile TEXT PRIMARY KEY,at REAL,round INTEGER,status TEXT);
          CREATE TABLE IF NOT EXISTS push_jobs(day TEXT,gid TEXT,seq INTEGER,due REAL,state TEXT,
            item TEXT,detail TEXT,PRIMARY KEY(day,gid,seq));
          CREATE TABLE IF NOT EXISTS plan_versions(day TEXT,gid TEXT,version TEXT,PRIMARY KEY(day,gid));
          CREATE TABLE IF NOT EXISTS group_history(gid TEXT,id TEXT,at REAL,state TEXT,
            card_id TEXT,comment_id TEXT,comment TEXT,snapshot TEXT,PRIMARY KEY(gid,id));
          CREATE TABLE IF NOT EXISTS model_usage(day TEXT,purpose TEXT,gid TEXT,
            input INTEGER,output INTEGER,state TEXT);
          CREATE TABLE IF NOT EXISTS tag_items(tag TEXT,id TEXT,PRIMARY KEY(tag,id));
          CREATE INDEX IF NOT EXISTS tag_items_id ON tag_items(id);
          CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,data TEXT,at REAL);
          CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY,at REAL);
        ''')
        if 'snapshot' not in {r[1] for r in self.db.execute('PRAGMA table_info(group_history)')}:
            self.db.execute("ALTER TABLE group_history ADD COLUMN snapshot TEXT DEFAULT '{}' ")
        self.db.commit()

    def migrate_shared(self, config, profiles):
        from .subscriptions import resolve_tags, tag_profile, search_key
        if self.db.execute("SELECT 1 FROM migrations WHERE name='shared-tags-v3'").fetchone():
            return
        for row in self.db.execute('SELECT * FROM inventory').fetchall():
            item=json.loads(row['data'])
            for tag in resolve_tags(config,item.get('queries',[])):
                tp=tag_profile(config,{},tag)
                self.db.execute('INSERT OR IGNORE INTO tag_items VALUES (?,?)',(tp['cache_key'],item['id']))
                age=float(config.get('published_max_age_days',7))
                key=search_key(tag,item['source'],age)
                self.db.execute('''INSERT INTO searches VALUES (?,?,?,?) ON CONFLICT(profile)
                    DO UPDATE SET at=MAX(searches.at,excluded.at)''',
                    (key,row['fetched'],0,json.dumps({'state':'reused_existing_results'},ensure_ascii=False)))
        self.db.execute('INSERT INTO migrations VALUES (?,?)',('shared-tags-v3',datetime.now(TZ).timestamp()))
        self.db.commit()

    def purge(self, now, ttl_days):
        self.db.execute('DELETE FROM inventory WHERE expires<=? OR fetched+?<=?', (now,ttl_days*86400,now))
        self.db.execute('DELETE FROM ratings WHERE id NOT IN (SELECT id FROM inventory)')
        self.db.execute('DELETE FROM tag_items WHERE id NOT IN (SELECT id FROM inventory)')
        self.db.execute('DELETE FROM evidence WHERE id NOT IN (SELECT id FROM inventory)')
        self.db.execute('DELETE FROM push_jobs WHERE due<?', (now-45*86400,))
        self.db.commit()

    def ensure_plan(self, p, now):
        day = now.astimezone(TZ).date().isoformat()
        minutes = schedule_minutes(p, day)
        version = digest(minutes)
        old = self.db.execute('SELECT version FROM plan_versions WHERE day=? AND gid=?', (day,p['group_id'])).fetchone()
        if old and old[0] == version:
            return
        # Retain completed/reserved job IDs when settings change; never resend a slot.
        self.db.execute("DELETE FROM push_jobs WHERE day=? AND gid=? AND state='pending'", (day,p['group_id']))
        midnight = now.astimezone(TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        for i, m in enumerate(minutes):
            due = (midnight+timedelta(minutes=m)).timestamp()
            self.db.execute('INSERT OR IGNORE INTO push_jobs VALUES (?,?,?,?,?,?,?)',
                            (day,p['group_id'],i,due,'pending','', ''))
        self.db.execute('INSERT OR REPLACE INTO plan_versions VALUES (?,?,?)',(day,p['group_id'],version))
        self.db.commit()

    def due(self, now, enabled_gids):
        # Reserve all due work before network calls, so slow searches cannot lose other groups' slots.
        self.db.execute("UPDATE push_jobs SET state='missed' WHERE state='pending' AND due<?", (now-180,))
        rows = self.db.execute("SELECT * FROM push_jobs WHERE state='pending' AND due<=? ORDER BY due", (now,)).fetchall()
        result = []
        for row in rows:
            if row['gid'] not in enabled_gids:
                continue
            self.db.execute("UPDATE push_jobs SET state='reserved' WHERE day=? AND gid=? AND seq=?", (row['day'],row['gid'],row['seq']))
            result.append(dict(row))
        self.db.commit()
        return result

    def finish(self, job, state, item='', detail=''):
        self.db.execute('UPDATE push_jobs SET state=?,item=?,detail=? WHERE day=? AND gid=? AND seq=?',
                        (state,item,detail,job['day'],job['gid'],job['seq']))
        self.db.commit()

    def cache(self, p, items, ratings, now):
        for item in items:
            expiry = now + p['ttl_days']*86400
            if item.get('published_at'):
                expiry = min(expiry, item['published_at'] + p['max_age_days']*86400)
            # Rediscovery never extends the age of an existing cached candidate.
            self.db.execute('''INSERT INTO inventory VALUES (?,?,?,?) ON CONFLICT(id)
                DO UPDATE SET data=excluded.data,expires=MIN(inventory.expires,excluded.expires)''',
                (item['id'],json.dumps(item,ensure_ascii=False),now,expiry))
            if p.get('tag_id'):
                self.db.execute('INSERT OR IGNORE INTO tag_items VALUES (?,?)', (p['cache_key'],item['id']))
        for rating in ratings:
            self.db.execute('INSERT OR REPLACE INTO ratings VALUES (?,?,?)',
                            (p['key'],rating['id'],json.dumps(rating,ensure_ascii=False)))
        self.db.commit()

    def candidates(self, p, now):
        keys = [p['key']]
        keys += [t['rating_key'] for t in p.get('tags',[]) if t.get('rating_key')]
        marks = ','.join('?' for _ in keys)
        rows = self.db.execute(f'''SELECT i.*,r.rating,r.profile FROM inventory i JOIN ratings r ON i.id=r.id
            WHERE r.profile IN ({marks}) AND i.expires>? AND i.fetched+?>?''',
            (*keys,now,p['ttl_days']*86400,now)).fetchall()
        # Respect old global history after upgrading from 0.1.
        legacy = set()
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seen'").fetchone():
            legacy = {x[0] for x in self.db.execute('SELECT id FROM seen')}
        result = {}
        for row in rows:
            item, rating = json.loads(row['data']), json.loads(row['rating'])
            if item['source'] not in p['sources']:
                continue
            if item.get('published_at') and now-item['published_at'] > p['max_age_days']*86400:
                continue
            score = total_score(item,rating,now)
            history = self.db.execute('SELECT state FROM group_history WHERE gid=? AND id=?', (p['group_id'],item['id'])).fetchone()
            state = history[0] if history else '旧版已推送' if item['id'] in legacy else '待推送' if score>=p['min_score'] else '低于门槛'
            value = {**item,'score':score,'rating':rating,'state':state,'expires':row['expires'],
                     'matched_tags':[t['name'] for t in p.get('tags',[]) if t.get('rating_key')==row['profile']]}
            if item['id'] not in result or result[item['id']]['score'] < score:
                result[item['id']] = value
        raw_keys=list(dict.fromkeys(t['cache_key'] for t in p.get('tags',[]) if t.get('cache_key')))
        if raw_keys:
            marks=','.join('?' for _ in raw_keys)
            raw=self.db.execute(f'''SELECT DISTINCT i.* FROM tag_items t JOIN inventory i ON t.id=i.id
                WHERE t.tag IN ({marks}) AND i.expires>? AND i.fetched+?>?''',
                (*raw_keys,now,p['ttl_days']*86400,now)).fetchall()
            for row in raw:
                item=json.loads(row['data'])
                if item['id'] in result or item['source'] not in p['sources']:
                    continue
                if item.get('published_at') and now-item['published_at']>p['max_age_days']*86400:
                    continue
                history=self.db.execute('SELECT state FROM group_history WHERE gid=? AND id=?',(p['group_id'],item['id'])).fetchone()
                matched=[t['name'] for t in p.get('tags',[]) if self.db.execute(
                    'SELECT 1 FROM tag_items WHERE tag=? AND id=?',(t.get('cache_key',''),item['id'])).fetchone()]
                result[item['id']]={**item,'score':0,'rating':{'reason':'共享候选，尚未按订阅标签评分'},
                    'state':history[0] if history else '旧版已推送' if item['id'] in legacy else '待评分',
                    'matched_tags':matched,'expires':row['expires']}
        return sorted(result.values(),key=lambda x:(-x['score'],-(x.get('published_at') or 0),x['id']))

    def eligible(self, p, now):
        return [x for x in self.candidates(p,now) if x['state']=='待推送']

    def mark_search(self, key, now, round_, statuses):
        self.db.execute('INSERT OR REPLACE INTO searches VALUES (?,?,?,?)',
                        (key,now,round_,json.dumps(statuses,ensure_ascii=False)))
        self.db.commit()

    def reserve_item(self, gid, item, now):
        snapshot={k:item.get(k) for k in ['id','title','source','score','matched_tags','interest_mode','selection_reason']}
        snapshot['url']=report_url(item)
        cur=self.db.execute('INSERT OR IGNORE INTO group_history VALUES (?,?,?,?,?,?,?,?)',
                            (gid,item['id'],now,'reserved','','','',json.dumps(snapshot,ensure_ascii=False)))
        self.db.commit()
        return bool(cur.rowcount)

    def update_delivery(self, gid, item_id, state, card_id='', comment_id='', comment=''):
        self.db.execute('UPDATE group_history SET state=?,card_id=?,comment_id=?,comment=? WHERE gid=? AND id=?',
                        (state,str(card_id),str(comment_id),comment,gid,item_id))
        self.db.commit()

    def day_count(self, gid, now):
        start=datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        count=self.db.execute('SELECT count(*) FROM group_history WHERE gid=? AND at>=? AND at<?',
                              (gid,start.timestamp(),start.timestamp()+86400)).fetchone()[0]
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deliveries'").fetchone():
            count+=self.db.execute("SELECT count(*) FROM deliveries WHERE group_id=? AND slot LIKE ? AND state IN ('pending','sent','uncertain')",
                                   (gid,start.date().isoformat()+'%')).fetchone()[0]
        return count

    def export(self, root, profiles, now):
        lines=['# 社区精选候选库', '', '更新时间：'+datetime.fromtimestamp(now,TZ).isoformat(), '',
               '评分：相关性40 + 信息质量25 + 趣味性20 + 时效性15；只推送达到门槛的内容。',
               '每个时段每会话只推一条；同标签共享检索和评分，各会话独立去重。保留期限从首次入库计算。','']
        targets = root/'targets'
        targets.mkdir(exist_ok=True)
        for p in profiles:
            start = len(lines)
            lines+=['## '+('私聊' if p.get('target_type')=='private' else '群聊')+' · '+p['group_name']+'（'+p.get('target_id',p['group_id'])+'）','',
                    '关键词：'+'、'.join(p['keywords']),
                    f"门槛：{p['min_score']}分；候选保留{p['ttl_days']:g}天；发布时间上限{p['max_age_days']:g}天。",'']
            if 'recent_tags' in p:
                lines+=['固定兴趣：'+'、'.join(x['name'] for x in p['fixed_tags']),
                        '近期兴趣：'+('、'.join(x['name'] for x in p['recent_tags']) or '暂未识别到'),
                        '已屏蔽：'+('、'.join(p.get('blocked_topics',[])) or '无'),'']
            jobs=self.db.execute('SELECT due,state FROM push_jobs WHERE day=? AND gid=? ORDER BY due',
                (datetime.fromtimestamp(now,TZ).date().isoformat(),p['group_id'])).fetchall()
            lines+=['今日计划：'+'；'.join(datetime.fromtimestamp(j['due'],TZ).strftime('%H:%M')+' '+j['state'] for j in jobs),'']
            lines+=['| 分数 | 平台 | 内容 | 状态 | 过期时间 | 评分理由 |','|---|---|---|---|---|---|']
            for item in self.candidates(p,now)[:100]:
                rating=item['rating'];score=item['score'];state=item['state']
                # Do not publish bearer-style XHS query tokens into the human-readable report.
                url=report_url(item)
                cells=['待评分' if state=='待评分' else str(score),item['source'],f"[{clean(item['title']).replace('|','／')}]({url})",state,
                       datetime.fromtimestamp(item['expires'],TZ).strftime('%m-%d %H:%M'),clean(rating.get('reason','')).replace('|','／')]
                lines.append('| '+' | '.join(cells)+' |')
            lines+=['']
            lines+=['### 最近推送记录','']
            for h in self.db.execute('SELECT * FROM group_history WHERE gid=? ORDER BY at DESC LIMIT 30',(p['group_id'],)):
                snapshot=json.loads(h['snapshot'] or '{}')
                lines.append('- '+datetime.fromtimestamp(h['at'],TZ).strftime('%m-%d %H:%M')+' '+
                    clean(snapshot.get('title') or h['id'])+' · '+h['state']+'；'+h['comment'])
            lines+=['']
            path = targets/(p.get('target_type','group')+'_'+p.get('target_id',p['group_id'])+'.md')
            path.write_text('\n'.join(lines[start:]),encoding='utf-8');path.chmod(0o600)
        target=root/'候选库.md'
        temp=root/'候选库.md.tmp'
        temp.write_text('\n'.join(lines),encoding='utf-8');temp.chmod(0o600);temp.replace(target)
