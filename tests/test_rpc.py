import asyncio
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from web3 import AsyncWeb3, Web3

from ponsbot.rpc import FailoverProvider, RpcUnavailable, retryable, rpc_urls


def node(handler=None, chain=4663, height=100):
    async def request(method, params):
        if method == 'eth_chainId':
            return {'jsonrpc': '2.0', 'id': 1, 'result': hex(chain)}
        if method == 'eth_blockNumber':
            return {'jsonrpc': '2.0', 'id': 1, 'result': hex(height)}
        if handler:
            return await handler(method, params)
        return {'jsonrpc': '2.0', 'id': 1, 'result': '0x42'}
    result = AsyncMock()
    result.make_request.side_effect = request
    return result


class RpcTests(IsolatedAsyncioTestCase):
    def pool(self, *nodes):
        provider = FailoverProvider(['http://localhost:8545'] * len(nodes))
        provider.providers = list(nodes)
        return provider

    async def test_timeout_switches_and_new_endpoint_stays_active(self):
        async def failure(method, params):
            raise asyncio.TimeoutError()
        first, second = node(failure), node()
        p = self.pool(first, second)
        response = await p.make_request('eth_getBalance', ['0xabc', 'latest'])
        self.assertEqual(response['result'], '0x42')
        self.assertEqual(p.active, 1)
        self.assertEqual(p.switches, 1)
        first_count = first.make_request.await_count
        await p.make_request('eth_getBalance', ['0xabc', 'latest'])
        self.assertEqual(first.make_request.await_count, first_count)

    async def test_json_rpc_rate_limit_switches(self):
        async def limited(method, params):
            return {'error': {'code': -32005, 'message': 'rate limit'}}
        p = self.pool(node(limited), node())
        await p.make_request('eth_getLogs', [{}])
        self.assertEqual(p.active, 1)

    async def test_wrong_chain_never_receives_operation(self):
        bad = node(chain=46630)
        p = self.pool(bad, node())
        await p.make_request('eth_sendRawTransaction', ['0x1234'])
        self.assertIn(0, p.disabled)
        self.assertEqual([c.args[0] for c in bad.make_request.call_args_list], ['eth_chainId'])

    async def test_contract_revert_is_not_retried(self):
        async def reverted(method, params):
            return {'error': {'code': -32000, 'message': 'execution reverted', 'data': '0x1234'}}
        second = node()
        p = self.pool(node(reverted), second)
        response = await p.make_request('eth_call', [{}, 'latest'])
        self.assertEqual(response['error']['data'], '0x1234')
        second.make_request.assert_not_awaited()

    async def test_ambiguous_broadcast_reuses_identical_raw_transaction(self):
        sent = []
        async def timeout_after_accepting(method, params):
            sent.append(tuple(params))
            raise TimeoutError()
        async def known(method, params):
            sent.append(tuple(params))
            return {'error': {'code': -32000, 'message': 'already known'}}
        p = self.pool(node(timeout_after_accepting), node(known))
        response = await p.make_request('eth_sendRawTransaction', ['0x1234'])
        self.assertEqual(sent, [('0x1234',), ('0x1234',)])
        self.assertEqual(response['result'], Web3.to_hex(Web3.keccak(hexstr='0x1234')))

    async def test_all_failures_bounded_and_cooldown_does_not_spin(self):
        async def failure(method, params):
            raise ConnectionError('private-provider-url-must-not-leak')
        nodes = [node(failure), node(failure)]
        p = self.pool(*nodes)
        with self.assertRaises(RpcUnavailable) as exc:
            await p.make_request('eth_getLogs', [{}])
        self.assertNotIn('private-provider', str(exc.exception))
        counts = [n.make_request.await_count for n in nodes]
        with self.assertRaises(RpcUnavailable):
            await p.make_request('eth_getLogs', [{}])
        self.assertEqual(counts, [n.make_request.await_count for n in nodes])

    async def test_unknown_transaction_is_not_misread_as_known_transaction(self):
        async def unknown(method, params):
            return {'error': {'code': -32000, 'message': 'unknown transaction'}}
        p = self.pool(node(unknown))
        response = await p.make_request('eth_sendRawTransaction', ['0x1234'])
        self.assertIn('error', response)
        self.assertNotIn('result', response)

    async def test_lagging_fallback_cannot_return_empty_logs(self):
        async def failure(method, params):
            raise TimeoutError()
        lagging = node(height=99)
        p = self.pool(node(failure), lagging, node(height=100))
        p.highest_block = 100
        await p.make_request('eth_getLogs', [{'fromBlock': '0x64', 'toBlock': '0x64'}])
        self.assertEqual(p.active, 2)
        self.assertNotIn('eth_getLogs', [c.args[0] for c in lagging.make_request.call_args_list])

    async def test_web3_middleware_works_with_failover_provider(self):
        p = self.pool(node())
        w3 = AsyncWeb3(p)
        self.assertEqual(await w3.eth.chain_id, 4663)
        self.assertEqual(await w3.eth.block_number, 100)

    async def test_disconnect_closes_all_sessions(self):
        nodes = [node(), node()]
        await self.pool(*nodes).disconnect()
        for n in nodes:
            n.disconnect.assert_awaited_once()


class ConfigurationTests(TestCase):
    def test_pool_deduplicates_and_custom_list_replaces_defaults(self):
        self.assertEqual(rpc_urls('https://unused', ('https://a', 'https://a', 'https://b')),
                         ('https://a', 'https://b'))
        self.assertGreaterEqual(len(rpc_urls('https://custom')), 3)

    def test_invalid_url_rejected(self):
        with self.assertRaises(ValueError):
            rpc_urls('https://a', ('wss://b',))

    def test_transaction_errors_not_classified_as_availability(self):
        for message in ['execution reverted', 'insufficient funds', 'nonce too low']:
            self.assertFalse(retryable({'code': -32603, 'message': message}))
