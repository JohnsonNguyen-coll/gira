import asyncio

from web3 import AsyncWeb3, Web3

from .abi import FACTORY_ABI, TOKEN_ABI, CURVE_ABI
from .rules import quote_buy, MIN_LAUNCH_AGE_SECONDS
from .rpc import FailoverProvider, rpc_urls

ZERO = "0x" + "00" * 20


class Chain:
    def __init__(self, config):
        self.config = config
        self.web3 = AsyncWeb3(FailoverProvider(rpc_urls(config.rpc, config.rpc_list),
                                              config.rpc_timeout, config.rpc_cooldown))
        self.factory = self.web3.eth.contract(address=Web3.to_checksum_address(config.factory),
                                               abi=FACTORY_ABI)

    async def validate(self):
        if await self.web3.eth.chain_id != 4663:
            raise ValueError("RPC must be Robinhood Chain, chain ID 4663")
        if not await self.web3.eth.get_code(self.factory.address):
            raise ValueError("No factory bytecode at configured address")

    async def head(self):
        return await self.web3.eth.get_block("latest")

    def curve(self, address):
        return self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=CURVE_ABI)

    def token(self, address):
        return self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=TOKEN_ABI)

    async def launches(self, start, end):
        return await self.factory.events.TokenLaunched().get_logs(from_block=start, to_block=end)

    async def first_buy(self, curve_address, start, end):
        logs = await self.curve(curve_address).events.CurveBuy().get_logs(
            from_block=start, to_block=end)
        return min(logs, key=lambda x: (x['blockNumber'], x['transactionIndex'], x['logIndex'])) if logs else None

    async def plan_buy(self, payload, watch, account):
        head = await self.head()
        # Use chain timestamps, not five seconds after RPC detection. Poll again
        # without blocking other watches until the launch is old enough.
        if head['timestamp'] - payload['timestamp'] < MIN_LAUNCH_AGE_SECONDS:
            return None
        canonical = await self.web3.eth.get_block(payload['block'])
        if canonical['hash'].hex() != payload['block_hash']:
            raise ValueError("Launch removed by chain reorganisation")
        if head['timestamp'] - payload['timestamp'] > self.config.max_age:
            raise ValueError("Launch expired")
        curve = self.curve(payload['curve'])
        b = head['number']

        async def read(name, *args):
            return await getattr(curve.functions, name)(*args).call(block_identifier=b)

        native, reserves, sellable, base, creator, snipe = await asyncio.gather(
            read("isNativeQuote"), read("getReserves"), read("sellableTokens"),
            read("feeBps"), read("creatorTaxBps"), read("currentSnipeTaxBps", account.address))
        if not native:
            raise ValueError("Only native ETH-quoted curves are supported")
        amount = int(watch['amount'])
        expected, actual_snipe = quote_buy(amount, *reserves, sellable, base, creator, snipe)
        if actual_snipe > watch['tax_limit']:
            return None  # Wait for the first eligible tax level, within max launch age.
        minimum = expected * (10000 - self.config.slippage) // 10000
        if minimum <= 0:
            raise ValueError("Minimum output would be zero")
        fn = curve.functions.buy(amount, minimum, account.address)
        tx_base = {"from": account.address, "value": amount}
        # Simulation verifies the actual bytecode path before signing.
        simulated = await fn.call(tx_base, block_identifier=b)
        if simulated < minimum:
            raise ValueError("Simulation returned less than minimum output")
        gas_price, gas_estimate, nonce, balance = await asyncio.gather(
            self.web3.eth.gas_price, fn.estimate_gas(tx_base),
            self.web3.eth.get_transaction_count(account.address, "pending"),
            self.web3.eth.get_balance(account.address, "pending"))
        gas = (gas_estimate * 120 + 99) // 100
        if gas > self.config.max_gas:
            raise ValueError("Gas limit exceeds MAX_GAS_LIMIT")
        if gas_price > self.config.max_gas_price:
            raise ValueError("Gas price exceeds MAX_GAS_PRICE_GWEI")
        if balance < amount + gas * gas_price:
            raise ValueError("Insufficient ETH for buy plus gas")
        tx = {**tx_base, "to": curve.address, "data": fn._encode_transaction_data(),
              "chainId": 4663, "nonce": nonce, "gas": gas, "gasPrice": gas_price}
        return tx, expected, actual_snipe

    async def close(self):
        await self.web3.provider.disconnect()
