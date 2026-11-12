import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from eth_account import Account

from ponsbot.config import Config
from ponsbot.store import Store
from test_bot import BotFixture, WALLET, TOKEN


def update(user_id):
    return NS(effective_user=NS(id=user_id), effective_chat=NS(id=user_id, type='private'),
              message=NS(delete=AsyncMock(), reply_text=AsyncMock()))


class MultiUserTests(BotFixture, IsolatedAsyncioTestCase):
    def second_watch(self):
        wallet = '0x4444444444444444444444444444444444444444'
        self.bot.accounts[999] = NS(address=wallet)
        wid = self.bot.store.add_watch('other', 1000, 10000, 10**15, 0, None,
                                       wallet, 10, user_id=999)
        return wid

    async def test_other_user_can_start_without_owner_permission(self):
        u = update(999)
        await self.bot.start(u, NS())
        u.message.reply_text.assert_awaited_once()
        self.assertIn(999, self.bot.store.users())

    async def test_other_user_can_load_independent_key(self):
        previous = self.bot.accounts[123]
        key = Account.create().key.hex()
        await self.bot.key(update(999), NS(args=[key]))
        self.assertEqual(self.bot.accounts[999].address, Account.from_key(key).address)
        self.assertIs(self.bot.accounts[123], previous)
        self.assertEqual(self.bot.notify.call_args.kwargs, {'user_id': 999})

    async def test_user_cannot_cancel_guessed_watch_id(self):
        await self.bot.cancel(update(999), NS(args=[str(self.wid)]))
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'armed')
        self.assertIsNone(self.bot.store.watch(self.wid, user_id=999))
        self.assertFalse(self.bot.store.cancel(self.wid, user_id=999))
        self.assertEqual(self.bot.notify.call_args.kwargs, {'user_id': 999})

    async def test_list_only_contains_own_watches(self):
        own_id = self.second_watch()
        await self.bot.list_watches(update(999), NS())
        text = self.bot.notify.call_args.args[0]
        self.assertIn(f'#{own_id} other', text)
        self.assertNotIn(f'#{self.wid} pons', text)
        self.assertEqual(self.bot.notify.call_args.kwargs, {'user_id': 999})

    async def test_forget_does_not_remove_other_users_key_or_watch(self):
        own_id = self.second_watch()
        previous = self.bot.accounts[123]
        await self.bot.forget(update(999), NS())
        self.assertNotIn(999, self.bot.accounts)
        self.assertIs(self.bot.accounts[123], previous)
        self.assertEqual(self.bot.store.watch(own_id)['status'], 'cancelled')
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'armed')

    async def test_wallet_never_falls_back_to_admin_wallet(self):
        await self.bot.wallet(update(999), NS())
        text = self.bot.notify.call_args.args[0]
        self.assertNotIn(WALLET, text)
        self.assertEqual(self.bot.notify.call_args.kwargs, {'user_id': 999})

    async def test_watch_is_bound_to_calling_user_and_their_wallet(self):
        self.second_watch()
        self.bot.chain.head = AsyncMock(return_value={'number': 50})
        await self.bot.watch(update(999), NS(args=['DOG', '50', '0.025', '3']))
        row = self.bot.store.watches(user_id=999)[-1]
        self.assertEqual(row['user_id'], 999)
        self.assertEqual(row['wallet'], self.bot.accounts[999].address)
        self.assertEqual(int(row['amount']), 25 * 10**15)

    async def test_exact_reported_command_accepts_50_point_963222_percent(self):
        self.bot.chain.head = AsyncMock(return_value={'number': 50})
        await self.bot.watch(update(123), NS(args=['DOG', '50', '0.025', '3']))
        row = self.bot.store.watches(user_id=123)[-1]
        self.assertEqual((row['min_bps'], row['max_bps']), (5000, 10000))
        self.assertEqual(row['tax_limit'], 300)
        first = self.bot.chain.first_buy.return_value
        first['args']['tokensOut'] = 509_632_220
        self.bot.chain.token.return_value.functions.totalSupply.return_value.call.return_value = 1_000_000_000
        payload = {**self.payload, 'symbol': 'DOG'}
        self.bot.notify.reset_mock()
        await self.bot.evaluate(row['id'], payload, self.head)
        self.assertEqual(self.bot.store.watch(row['id'])['status'], 'simulated')
        self.bot.chain.plan_buy.assert_awaited_once()
        self.assertNotIn('bỏ qua', self.bot.notify.call_args.args[0])

    async def test_exact_reported_command_rejects_lower_percentages_with_saved_range(self):
        self.bot.chain.head = AsyncMock(return_value={'number': 50})
        for amount in (93_264_250, 46_256_560):
            with self.subTest(tokens_out=amount):
                await self.bot.watch(update(123), NS(args=['DOG', '50', '0.025', '3']))
                row = self.bot.store.watches(user_id=123)[-1]
                self.bot.chain.first_buy.return_value['args']['tokensOut'] = amount
                self.bot.chain.token.return_value.functions.totalSupply.return_value.call.return_value = 1_000_000_000
                await self.bot.evaluate(row['id'], {**self.payload, 'symbol': 'DOG'}, self.head)
                self.assertEqual(self.bot.store.watch(row['id'])['status'], 'armed')
                self.assertIn('Điều kiện đã lưu: 50–100%', self.bot.notify.call_args.args[0])
                self.assertIn('thấp hơn mức tối thiểu 50%', self.bot.notify.call_args.args[0])
                self.assertIn(self.bot.version, self.bot.notify.call_args.args[0])
                self.assertEqual(self.bot.store.watch(row['id'])['detail'], self.bot.notify.call_args.args[0])
        self.bot.chain.plan_buy.assert_not_awaited()

    async def test_version_is_available_to_other_users(self):
        await self.bot.version_info(update(999), NS())
        self.bot.notify.assert_awaited_once_with(self.bot.version, user_id=999)

    async def test_diagnostic_record_cannot_rearm_cancelled_watch(self):
        self.bot.store.cancel(self.wid, user_id=123)
        self.bot.store.record_check(self.wid, 'late diagnostic')
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'cancelled')

    async def test_execution_selects_owner_key_and_routes_result_to_owner(self):
        wid = self.second_watch()
        await self.bot.evaluate(wid, self.payload, self.head)
        self.assertIs(self.bot.chain.plan_buy.call_args.args[2], self.bot.accounts[999])
        self.assertEqual(self.bot.notify.call_args.kwargs, {'user_id': 999})
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'armed')

    async def test_missing_owner_key_does_not_use_other_users_key(self):
        wid = self.second_watch()
        self.bot.accounts.pop(999)
        await self.bot.evaluate(wid, self.payload, self.head)
        self.bot.chain.plan_buy.assert_not_awaited()
        self.assertEqual(self.bot.store.watch(wid)['status'], 'armed')

    async def test_receipt_notification_goes_only_to_watch_owner(self):
        wid = self.second_watch()
        self.bot.store.status(wid, 'submitted', TOKEN, '0x' + 'ab' * 32)
        self.bot.chain.web3.eth.get_transaction_receipt = AsyncMock(return_value={'status': 1})
        await self.bot.reconcile()
        self.bot.notify.assert_awaited_once()
        self.assertEqual(self.bot.notify.call_args.kwargs, {'user_id': 999})


class MigrationTests(TestCase):
    def legacy_db(self, path):
        db = sqlite3.connect(path)
        db.executescript("""
        CREATE TABLE watches (
          id INTEGER PRIMARY KEY, ticker TEXT, min_bps INTEGER, max_bps INTEGER,
          amount TEXT, tax_limit INTEGER, deployer TEXT, wallet TEXT, start_block INTEGER,
          status TEXT, tx_hash TEXT, detail TEXT);
        INSERT INTO watches VALUES (1,'legacy',5000,10000,'100',300,NULL,'wallet',10,'armed',NULL,NULL);
        INSERT INTO watches VALUES (2,'sent',5000,10000,'100',300,NULL,'wallet',10,'submitted','saved-hash',NULL);
        """)
        db.close()

    def test_migration_preserves_owner_and_transaction_state_across_restarts(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.sqlite3'
            self.legacy_db(path)
            store = Store(path, legacy_owner=123)
            self.assertEqual(len(store.watches(user_id=123)), 2)
            self.assertEqual(store.watches(user_id=999), [])
            self.assertEqual(store.watch(2)['tx_hash'], 'saved-hash')
            store.db.close()
            store = Store(path, legacy_owner=999)
            self.assertEqual(store.watch(1)['user_id'], 123)
            self.assertEqual(store.watch(2)['status'], 'submitted')
            store.db.close()

    def test_missing_legacy_owner_keeps_old_orders_quarantined(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bot.sqlite3'
            self.legacy_db(path)
            store = Store(path)
            self.assertEqual(store.watches(), [])
            self.assertEqual(store.watch(1)['user_id'], 0)
            self.assertFalse(store.cancel(1, user_id=999))
            store.db.close()

    def test_admin_id_optional_in_public_mode(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fixture', 'TELEGRAM_OWNER_ID': ''}, clear=True), \
                patch('ponsbot.config.load_dotenv'):
            self.assertEqual(Config.load().owner, 0)
