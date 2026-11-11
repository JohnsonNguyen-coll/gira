"""Bounded RPC failover; retries broadcasts with the SAME signed bytes only."""
import asyncio
import time
from urllib.parse import urlsplit

from aiohttp import ClientError
from web3 import AsyncHTTPProvider, Web3
from web3.providers.async_base import AsyncJSONBaseProvider

PUBLIC_RPCS = (
    'https://rpc.mainnet.chain.robinhood.com',
    'https://robinhood-rpc.publicnode.com',
    'https://api.blockvectra.com/v1/robinhood_mainnet/public',
)


class RpcUnavailable(ConnectionError):
    pass


def rpc_urls(primary, configured=()):
    urls = tuple(dict.fromkeys(u.strip() for u in (configured or (primary, *PUBLIC_RPCS)) if u.strip()))
    if not urls or any(urlsplit(u).scheme not in ('http', 'https') or not urlsplit(u).hostname for u in urls):
        raise ValueError('RPC_URLS must contain HTTP/HTTPS endpoints')
    return urls


def retryable(error):
    message = str(error.get('message', '')).lower()
    # A contract revert / invalid transaction is not an availability problem.
    if any(s in message for s in ('revert', 'insufficient funds', 'nonce too low',
                                  'replacement transaction underpriced', 'invalid sender')):
        return False
    return (error.get('code') in (-32005, -32016, -32002, -32603, -32601, 429, 502, 503, 504)
            or any(s in message for s in ('rate limit', 'too many requests', 'timeout',
                                          'timed out', 'temporarily unavailable', 'overloaded',
                                          'header not found', 'unknown block', 'missing trie node',
                                          'method not allowed', 'not supported', 'quota exceeded')))


class FailoverProvider(AsyncJSONBaseProvider):
    def __init__(self, urls, timeout=3.0, cooldown=15.0):
        super().__init__()
        self.urls = tuple(urls)
        self.providers = [AsyncHTTPProvider(url, request_kwargs={'timeout': timeout},
                                           exception_retry_configuration=None) for url in urls]
        self.active = 0
        self.cooldown = cooldown
        self.unhealthy_until = [0.0] * len(urls)
        self.disabled = set()
        self.validated = set()
        self.validation_locks = [asyncio.Lock() for _ in urls]
        self.switches = 0
        self.highest_block = 0

    async def _check_height(self, index):
        if not self.highest_block:
            return
        response = await self.providers[index].make_request('eth_blockNumber', [])
        if 'result' not in response or int(response['result'], 16) < self.highest_block:
            raise RpcUnavailable('Fallback RPC is behind the last observed block')

    async def _validate(self, index):
        async with self.validation_locks[index]:
            if index in self.validated:
                return
            response = await self.providers[index].make_request('eth_chainId', [])
            if 'result' not in response:
                raise RpcUnavailable('RPC chain identity check failed')
            if int(response['result'], 16) != 4663:
                self.disabled.add(index)
                raise RpcUnavailable('RPC has wrong chain ID')
            self.validated.add(index)

    def _succeeded(self, index):
        self.unhealthy_until[index] = 0
        if self.active != index:
            self.active = index
            self.switches += 1

    async def make_request(self, method, params):
        # Do not include URLs, params, raw transactions or upstream error messages
        # in terminal errors: provider URLs can embed API keys.
        now = time.monotonic()
        order = [(self.active + offset) % len(self.providers) for offset in range(len(self.providers))]
        order = [i for i in order if i not in self.disabled and self.unhealthy_until[i] <= now]
        if not order:
            raise RpcUnavailable('All RPC endpoints are unavailable; retry after cooldown')
        for index in order:
            try:
                await self._validate(index)
                if index != self.active:
                    # A lagging fallback must not return empty logs for a range
                    # it has not indexed yet and cause the scanner to skip launches.
                    await self._check_height(index)
                response = await self.providers[index].make_request(method, params)
                if not isinstance(response, dict) or not ('result' in response or 'error' in response):
                    raise RpcUnavailable('Invalid JSON-RPC response')
                error = response.get('error')
                message = str(error.get('message', '')).lower() if error else ''
                if error and method == 'eth_sendRawTransaction' and (
                        'already known' in message or 'already imported' in message
                        or message.startswith('known transaction')):
                    self._succeeded(index)
                    return {'jsonrpc': '2.0', 'id': response.get('id', 1),
                            'result': Web3.to_hex(Web3.keccak(hexstr=params[0]))}
                if error and retryable(error):
                    raise RpcUnavailable('Temporary RPC error')
                result = response.get('result')
                if not error and method == 'eth_blockNumber' and result is not None:
                    self.highest_block = max(self.highest_block, int(result, 16))
                if (not error and method == 'eth_getBlockByNumber' and params[0] == 'latest'
                        and isinstance(result, dict) and result.get('number')):
                    number = int(result['number'], 16)
                    if number < self.highest_block:
                        raise RpcUnavailable('RPC returned a stale head')
                    self.highest_block = max(self.highest_block, number)
                # Hard errors are returned normally for web3 to interpret. In
                # particular, never retry a reverted call to make it look successful.
                self._succeeded(index)
                return response
            except (ClientError, OSError, asyncio.TimeoutError, ValueError):
                self.unhealthy_until[index] = time.monotonic() + self.cooldown
        raise RpcUnavailable('RPC request failed on all available endpoints')

    async def is_connected(self, show_traceback=False):
        try:
            response = await self.make_request('eth_chainId', [])
            return response.get('result') == hex(4663)
        except RpcUnavailable:
            return False

    async def disconnect(self):
        await asyncio.gather(*(p.disconnect() for p in self.providers), return_exceptions=True)
