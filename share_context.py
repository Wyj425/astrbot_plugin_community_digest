"""Delivered shares are conversation facts, exposed through a scoped peer API."""
import copy
import json
import time


def compact_detail(item):
    fields = ('id', 'source', 'title', 'url', 'author', 'description', 'body',
              'evidence', 'evidence_kind', 'body_truncated', 'comment_scope',
              'has_more_comments', 'images_total', 'image_urls')
    value = {k: item.get(k) for k in fields}
    for key in ('body', 'evidence', 'description'):
        value[key] = str(value.get(key) or '')[:18000]
    value['comments'] = copy.deepcopy((item.get('comments') or [])[:20])
    value['images'] = []  # Never copy image binaries into a delivery journal.
    value['images_loaded'] = 0
    return value


def temp_part(req, text):
    from astrbot.core.agent.message import TextPart
    if getattr(req, 'extra_user_content_parts', None) is None:
        req.extra_user_content_parts = []
    req.extra_user_content_parts.append(TextPart(text=text).mark_as_temp())


class ShareContextMixin:
    sharing_context_version = 1

    def initialize_share_context(self):
        self.store.db.execute('''CREATE TABLE IF NOT EXISTS shared_content_context(
            umo TEXT,card_id TEXT,comment_id TEXT,sent_at REAL,item_id TEXT,
            data TEXT,PRIMARY KEY(umo,card_id))''')
        self.store.db.execute('CREATE INDEX IF NOT EXISTS shared_content_by_scope ON shared_content_context(umo,sent_at)')
        self.store.db.commit()
        self._sharing_context_initialized = True

    def _share_gid(self, umo):
        parts = str(umo).split(':', 2)
        if (len(parts) != 3 or parts[0] != self.config.get('platform_id', '')
                or not parts[2].isdigit() or int(parts[2]) <= 0):
            return None
        if parts[1] == 'GroupMessage':
            return parts[2]
        if parts[1] == 'FriendMessage':
            return 'private:' + parts[2]
        return None

    def record_shared_content(self, umo, item, card_id, comment_id='', comment='', sent_at=None):
        if not getattr(self, '_sharing_context_initialized', False):
            self.initialize_share_context()
        if not self._share_gid(umo) or card_id in (None, ''):
            return
        stamp = time.time() if sent_at is None else sent_at
        old = self.store.db.execute('SELECT sent_at FROM shared_content_context WHERE umo=? AND card_id=?',
                                    (umo, str(card_id))).fetchone()
        if old:
            stamp = old[0]
        value = compact_detail(item)
        value['comment'] = str(comment)[:1000] if comment_id not in (None, '') else ''
        self.store.db.execute('INSERT OR REPLACE INTO shared_content_context VALUES (?,?,?,?,?,?)',
            (umo, str(card_id), str(comment_id or ''), stamp, str(item.get('id', '')),
             json.dumps(value, ensure_ascii=False)))
        self.store.db.execute('DELETE FROM shared_content_context WHERE sent_at<?', (time.time()-7*86400,))
        self.store.db.commit()

    def shared_content_context(self, umo, before=None, message_id=None, limit=8):
        """Only accepted card deliveries from this exact platform/session."""
        gid = self._share_gid(umo)
        if gid is None:
            return []
        if not getattr(self, '_sharing_context_initialized', False):
            self.initialize_share_context()
        before = time.time() if before is None else float(before)
        cutoff = before - 7*86400
        limit = max(1, min(int(limit), 16))
        match = ' AND (card_id=? OR comment_id=?)' if message_id is not None else ''
        identity = (str(message_id), str(message_id)) if message_id is not None else ()
        rows = self.store.db.execute('''SELECT card_id,comment_id,sent_at,item_id,data
            FROM shared_content_context WHERE umo=? AND sent_at<=? AND sent_at>=?
            '''+match+' ORDER BY sent_at DESC LIMIT 32', (umo, before, cutoff)+identity).fetchall()
        found = {}
        for row in rows:
            data = json.loads(row['data'])
            found[str(row['card_id'])] = self._share_ref(umo, row['card_id'], row['comment_id'], row['sent_at'], data)
        # Read old accepted deliveries too: an upgrade/restart must not erase the
        # two real shares that prompted this fix. No unaccepted draft is eligible.
        for row in self.store.db.execute('''SELECT * FROM group_history WHERE gid=?
                AND card_id!='' AND at<=? AND at>=?
                AND state IN ('accepted','card_accepted','card_only','uncertain') '''+match+' ORDER BY at DESC LIMIT 32',
                (gid, before, cutoff)+identity):
            card = str(row['card_id'])
            if card in found:
                continue
            data = json.loads(row['snapshot'] or '{}')
            data['comment'] = row['comment'] if row['comment_id'] else ''
            found[card] = self._share_ref(umo, card, row['comment_id'], row['at'], data)
        values = sorted(found.values(), key=lambda x: x['at'], reverse=True)
        if message_id is not None:
            values = [x for x in values if str(message_id) in x['message_ids']]
        return copy.deepcopy(values[:limit])

    @staticmethod
    def _share_ref(umo, card, comment, at, data):
        return {'source': data.get('source'), 'url': data.get('url'), 'id': data.get('id'),
                'title': data.get('title'), 'origin': 'bot', 'umo': umo, 'at': at,
                'card_id': str(card), 'message_ids': [str(x) for x in (card, comment) if x not in (None, '')],
                'comment': data.get('comment', '')}

    def shared_content_evidence(self, umo, card_id, before=None):
        refs = self.shared_content_context(umo, before, message_id=card_id)
        if not refs:
            return None
        ref = refs[0]
        row = self.store.db.execute('SELECT data FROM shared_content_context WHERE umo=? AND card_id=?',
                                    (umo, ref['card_id'])).fetchone()
        if row:
            return compact_detail(json.loads(row[0]))
        # Compatibility for already-delivered cards, scoped via the lookup above.
        row = self.store.db.execute('SELECT data FROM evidence WHERE id=?', (ref['id'],)).fetchone()
        if row:
            return compact_detail(json.loads(row[0]))
        return None

    async def attach_shared_content_context(self, event, req):
        if event.get_extra('_shared_outgoing_context_attached') or getattr(event, '_proactive_plugin', None):
            return
        stamp = getattr(event.message_obj, 'timestamp', None)
        if hasattr(stamp, 'timestamp'):
            stamp = stamp.timestamp()
        refs = self.shared_content_context(event.unified_msg_origin, stamp or time.time(), limit=3)
        if not refs:
            return
        event.set_extra('_shared_outgoing_context_attached', True)
        temp_part(req, '\n【本会话真实发送记录】以下卡片是你已经转发给当前会话的内容，'
            '不是用户发给你的，也不表示你是原帖作者。“你发的”通常指你转发给对方。'
            '这些只是发送事实，不要求把无关聊天拉回帖子；本轮明确链接、引用和指代优先。\n'
            + json.dumps(list(reversed(refs)), ensure_ascii=False))

    async def sync_shared_history(self, umo, item, card_id, comment_id='', comment=''):
        """Store normalized card text without fabricating a user turn or resending."""
        manager = getattr(self.context, 'message_history_manager', None)
        if not manager:
            return
        try:
            for mid, text in [(card_id, '分享卡片：《'+str(item.get('title', ''))+'》\n'+str(item.get('url', ''))),
                              (comment_id, comment)]:
                if mid in (None, '') or not text:
                    continue
                content = {'type': 'bot', 'message': [{'type': 'plain', 'text': text}],
                           'source_message_id': str(mid), 'share_source': 'community_digest'}
                recent = await manager.get(platform_id=umo.split(':', 1)[0], user_id=umo, page=1, page_size=10)
                if any((getattr(r, 'content', None) if not isinstance(r, dict) else r.get('content', {})).get('source_message_id') == str(mid)
                       for r in recent or []):
                    continue
                await manager.insert(platform_id=umo.split(':', 1)[0], user_id=umo,
                                     sender_id='bot', sender_name='bot', content=content)
        except Exception as exc:
            from astrbot.api import logger
            logger.warning('[community_digest] share history sync failed: %s', type(exc).__name__)
