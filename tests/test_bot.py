import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from eth_account import Account
from hexbytes import HexBytes

from ponsbot.bot import Bot
from ponsbot.chain import Chain
from ponsbot.config import Config

WALLET = '0x1111111111111111111111111111111111111111'
TOKEN = '0x2222222222222222222222222222222222222222'
CURVE = '0x3333333333333333333333333333333333333333'
FACTORY = '0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e'


class BotTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        config = Config('dummy', 123, 'http://localhost:8545', FACTORY, True,
                        .5, 60, 300, 20*10**9, 1_500_000, Path(self.tmp.name))
        self.bot = Bot(config)
        self.bot.notify = AsyncMock()
        self.bot.account = NS(address=WALLET)
        self.wid = self.bot.store.add_watch('pons', 5000, 10000, 10**16, 300, None, WALLET, 10)
        self.payload = {'token': TOKEN, 'curve': CURVE, 'block': 10, 'timestamp': 100,
                        'block_hash': 'aa', 'symbol': 'PoNs'}
        self.head = {'number': 11, 'timestamp': 105}
        first = {'blockNumber': 10, 'blockHash': HexBytes('0xaa'),
                 'args': {'tokensOut': 500}}
        supply_call = NS(call=AsyncMock(return_value=1000))
        self.bot.chain = NS(first_buy=AsyncMock(return_value=first),
            token=Mock(return_value=NS(functions=NS(totalSupply=Mock(return_value=supply_call)))),
            web3=NS(eth=NS(get_block=AsyncMock(return_value={'hash': HexBytes('0xaa')}),
                          send_raw_transaction=AsyncMock())),
            plan_buy=AsyncMock(return_value=({}, 100, 0)))

    def tearDown(self):
        self.bot.store.db.close()
        self.tmp.cleanup()

    async def test_dry_run_does_not_send_and_is_one_shot(self):
        await self.bot.evaluate(self.wid, self.payload, self.head)
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'simulated')
        await self.bot.evaluate(self.wid, self.payload, self.head)
        self.bot.chain.plan_buy.assert_awaited_once()
        self.bot.chain.web3.eth.send_raw_transaction.assert_not_awaited()

    async def test_no_buy_before_five_seconds_even_when_condition_matches(self):
        for timestamp in (100, 101, 104):
            await self.bot.evaluate(self.wid, self.payload, {'number': 11, 'timestamp': timestamp})
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'armed')
        self.bot.chain.first_buy.assert_not_awaited()
        self.bot.chain.plan_buy.assert_not_awaited()
        self.bot.chain.web3.eth.send_raw_transaction.assert_not_awaited()
        # Same candidate becomes eligible exactly at launch timestamp + 5.
        await self.bot.evaluate(self.wid, self.payload, {'number': 12, 'timestamp': 105})
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'simulated')

    async def test_chain_plan_also_enforces_five_second_minimum(self):
        chain = Chain.__new__(Chain)
        chain.head = AsyncMock(return_value={'number': 11, 'timestamp': 104})
        chain.web3 = NS(eth=NS(get_block=AsyncMock()))
        result = await chain.plan_buy(self.payload, self.bot.store.watch(self.wid), self.bot.account)
        self.assertIsNone(result)
        chain.web3.eth.get_block.assert_not_awaited()

    async def test_first_buy_not_cumulative(self):
        self.bot.chain.first_buy.return_value['args']['tokensOut'] = 499
        await self.bot.evaluate(self.wid, self.payload, self.head)
        self.bot.chain.plan_buy.assert_not_awaited()
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'armed')

    async def test_wait_for_tax(self):
        self.bot.chain.plan_buy.return_value = None
        await self.bot.evaluate(self.wid, self.payload, self.head)
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'armed')

    async def test_expired_launch_never_buys(self):
        await self.bot.evaluate(self.wid, self.payload, {'number': 99, 'timestamp': 161})
        self.bot.chain.first_buy.assert_not_awaited()

    async def test_cancel_and_claim_cannot_both_win(self):
        self.assertTrue(self.bot.store.cancel(self.wid))
        self.assertFalse(self.bot.store.claim(self.wid))
        await self.bot.evaluate(self.wid, self.payload, self.head)
        self.bot.chain.plan_buy.assert_not_awaited()

    async def test_cancel_while_quote_is_awaiting_prevents_submission(self):
        async def cancel_during_quote(*args):
            update = NS(effective_user=NS(id=123), effective_chat=NS(id=123, type='private'))
            await self.bot.cancel(update, NS(args=[str(self.wid)]))
            return {}, 100, 0
        self.bot.chain.plan_buy.side_effect = cancel_during_quote
        await asyncio.wait_for(self.bot.evaluate(self.wid, self.payload, self.head), timeout=1)
        self.assertEqual(self.bot.store.watch(self.wid)['status'], 'cancelled')
        self.bot.chain.web3.eth.send_raw_transaction.assert_not_awaited()

    async def test_key_command_only_owner_private_chat(self):
        update = NS(effective_user=NS(id=999), effective_chat=NS(id=999, type='private'),
                    message=NS(delete=AsyncMock()))
        await self.bot.key(update, NS(args=['invalid']))
        update.message.delete.assert_not_awaited()
        self.bot.notify.assert_not_awaited()

    async def test_key_deleted_and_never_written_to_database(self):
        self.bot.store.cancel(self.wid)
        # Ephemeral fixture, not a funded key or a production secret.
        key = Account.create().key.hex()
        update = NS(effective_user=NS(id=123), effective_chat=NS(id=123, type='private'),
                    message=NS(delete=AsyncMock()))
        await self.bot.key(update, NS(args=[key]))
        update.message.delete.assert_awaited_once()
        self.assertEqual(self.bot.account.address, Account.from_key(key).address)
        for call in self.bot.notify.call_args_list:
            self.assertNotIn(key, str(call))
        dump = '\n'.join(self.bot.store.db.iterdump())
        self.assertNotIn(key, dump)

    async def test_key_rejected_if_delete_fails(self):
        update = NS(effective_user=NS(id=123), effective_chat=NS(id=123, type='private'),
                    message=NS(delete=AsyncMock(side_effect=RuntimeError())))
        previous = self.bot.account
        await self.bot.key(update, NS(args=[Account.create().key.hex()]))
        self.assertIs(self.bot.account, previous)

    async def test_restart_does_not_bind_watch_to_new_wallet(self):
        self.bot.account = None
        update = NS(effective_user=NS(id=123), effective_chat=NS(id=123, type='private'),
                    message=NS(delete=AsyncMock()))
        await self.bot.key(update, NS(args=[Account.create().key.hex()]))
        self.assertIsNone(self.bot.account)

    async def test_live_send_failure_does_not_rebuy(self):
        object.__setattr__(self.bot.config, 'dry_run', False)
        self.bot.account = NS(address=WALLET, sign_transaction=Mock(
            return_value=NS(hash=HexBytes('0x' + 'ab'*32), raw_transaction=b'fixture')))
        self.bot.chain.web3.eth.send_raw_transaction.side_effect = TimeoutError()
        await self.bot.evaluate(self.wid, self.payload, self.head)
        state = self.bot.store.watch(self.wid)
        self.assertEqual(state['status'], 'submitted')
        self.assertEqual(state['tx_hash'], '0x' + 'ab'*32)
        await self.bot.evaluate(self.wid, self.payload, self.head)
        self.bot.chain.web3.eth.send_raw_transaction.assert_awaited_once()
