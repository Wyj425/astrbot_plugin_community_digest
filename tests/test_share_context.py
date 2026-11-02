import importlib.util
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('share_context_test', ROOT/'share_context.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SCOPE = 'qq:FriendMessage:123'


class ShareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE group_history(gid TEXT,id TEXT,at REAL,state TEXT,card_id TEXT,comment_id TEXT,comment TEXT,snapshot TEXT)')
        self.db.execute('CREATE TABLE evidence(id TEXT PRIMARY KEY,data TEXT,at REAL)')
        self.p = module.ShareContextMixin()
        self.p.config = {'platform_id': 'qq'}
        self.p.store = SimpleNamespace(db=self.db)
        self.p.initialize_share_context()
        self.now = time.time()
        self.item = {'id': 'heybox:apex', 'source': '小黑盒', 'title': 'Apex 密客无人机',
                     'url': 'https://api.xiaoheihe.cn/share?link_id=apex', 'body': '无人机扫描后大招碎甲',
                     'images': ['data:image/jpeg;base64,private-image'], 'images_loaded': 1}

    def tearDown(self): self.db.close()

    def legacy(self, state='accepted', card='-77', comment='88', scope='private:123', at=None):
        self.db.execute('INSERT INTO group_history VALUES (?,?,?,?,?,?,?,?)',
            (scope, self.item['id'], self.now-30 if at is None else at, state, card, comment,
             '这个有点意思', json.dumps(self.item, ensure_ascii=False)))
        self.db.execute('INSERT OR REPLACE INTO evidence VALUES (?,?,?)',
                        (self.item['id'], json.dumps(self.item), self.now))
        self.db.commit()

    def test_legacy_accepted_delivery_survives_upgrade_and_has_cached_body(self):
        self.legacy()
        rows = self.p.shared_content_context(SCOPE, self.now)
        self.assertEqual(rows[0]['origin'], 'bot')
        self.assertEqual(rows[0]['title'], self.item['title'])
        self.assertEqual(self.p.shared_content_evidence(SCOPE, '-77', self.now)['body'], self.item['body'])

    def test_user_and_group_and_platform_scopes_never_mix(self):
        self.legacy()
        for scope in ['qq:FriendMessage:456', 'qq:GroupMessage:123', 'other:FriendMessage:123']:
            self.assertEqual(self.p.shared_content_context(scope, self.now), [])
            self.assertIsNone(self.p.shared_content_evidence(scope, '-77', self.now))

    def test_unaccepted_or_failed_drafts_are_not_sent_facts(self):
        for state, card in [('reserved', ''), ('failed', ''), ('failed', '77')]:
            with self.subTest(state=state):
                self.db.execute('DELETE FROM group_history')
                self.legacy(state=state, card=card)
                self.assertEqual(self.p.shared_content_context(SCOPE, self.now), [])

    def test_card_accepted_without_comment_does_not_claim_comment_was_sent(self):
        self.legacy(state='card_accepted', comment='')
        self.assertEqual(self.p.shared_content_context(SCOPE, self.now)[0]['comment'], '')

    def test_legacy_card_only_still_resolves_its_delivered_card(self):
        self.legacy(state='card_only', comment='')
        refs = self.p.shared_content_context(SCOPE, self.now, message_id='-77')
        self.assertEqual(refs[0]['title'], self.item['title'])
        self.assertEqual(refs[0]['comment'], '')

    def test_future_delivery_is_not_visible_to_earlier_queued_message(self):
        self.legacy(at=self.now+1)
        self.assertEqual(self.p.shared_content_context(SCOPE, self.now), [])

    def test_negative_card_id_and_comment_reply_both_resolve(self):
        self.legacy()
        for mid in ['-77', '88']:
            self.assertEqual(self.p.shared_content_context(SCOPE, self.now, message_id=mid)[0]['card_id'], '-77')

    def test_recording_comment_keeps_original_card_time_and_strips_binaries(self):
        self.p.record_shared_content(SCOPE, self.item, '-77', sent_at=self.now-20)
        self.p.record_shared_content(SCOPE, self.item, '-77', '88', '短评', sent_at=self.now)
        row = self.p.shared_content_context(SCOPE, self.now)[0]
        self.assertEqual(row['at'], self.now-20)
        self.assertEqual(row['comment'], '短评')
        raw = self.db.execute('SELECT data FROM shared_content_context').fetchone()[0]
        self.assertNotIn('private-image', raw)

    def test_new_journal_overrides_legacy_metadata_once(self):
        self.legacy()
        self.p.record_shared_content(SCOPE, self.item, '-77', '88', '短评', sent_at=self.now-20)
        self.assertEqual(len(self.p.shared_content_context(SCOPE, self.now)), 1)

    def test_same_numeric_group_and_friend_can_each_have_their_own_share(self):
        self.legacy()
        self.p.record_shared_content('qq:GroupMessage:123', {**self.item, 'title': '群内独立帖子'}, '99', sent_at=self.now-2)
        self.assertEqual(self.p.shared_content_context(SCOPE, self.now)[0]['title'], self.item['title'])
        self.assertEqual(self.p.shared_content_context('qq:GroupMessage:123', self.now)[0]['title'], '群内独立帖子')

    async def test_platform_history_uses_full_scope_and_is_idempotent_without_sending(self):
        manager = SimpleNamespace(get=AsyncMock(return_value=[]), insert=AsyncMock())
        self.p.context = SimpleNamespace(message_history_manager=manager)
        await self.p.sync_shared_history(SCOPE, self.item, '-77', '88', '短评')
        self.assertEqual(manager.insert.await_count, 2)
        kwargs = manager.insert.await_args_list[0].kwargs
        self.assertEqual(kwargs['user_id'], SCOPE)
        self.assertEqual(kwargs['sender_name'], 'bot')
        manager.get.return_value = [SimpleNamespace(content=x.kwargs['content']) for x in manager.insert.await_args_list]
        await self.p.sync_shared_history(SCOPE, self.item, '-77', '88', '短评')
        self.assertEqual(manager.insert.await_count, 2)


if __name__ == '__main__': unittest.main()
