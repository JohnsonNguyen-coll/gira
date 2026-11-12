import asyncio
import logging
from decimal import Decimal

from eth_account import Account
from telegram import Update
from telegram.ext import Application, CommandHandler
from web3 import Web3
from web3.exceptions import TransactionNotFound

from .chain import Chain, ZERO
from .config import Config
from .rules import ticker_key, decimal_units, first_buy_matches, MIN_LAUNCH_AGE_SECONDS
from .store import Store

HELP = """👋 Chào bạn! Đây là bot canh token PONS v2 trên Robinhood Chain.

Bot theo dõi token mới theo ticker, kiểm tra lệnh mua đầu tiên của creator hoặc bất kỳ ví nào, rồi tự mua khi đạt điều kiện bạn đặt và đã qua ít nhất 5 giây từ lúc launch (theo timestamp block). Ticker không phân biệt hoa thường. Bạn tự nhập tỷ lệ tổng cung và số ETH muốn mua cho từng lệnh.

🚀 BẮT ĐẦU
1. Nạp ví: /key PRIVATE_KEY
2. Kiểm tra địa chỉ và số dư: /wallet
3. Tạo lệnh canh theo mẫu bên dưới.

🎯 TẠO LỆNH CANH
/watch TICKER MIN_PERCENT ETH MAX_SNIPE_PERCENT [DEPLOYER] [MAX_PERCENT]

Ví dụ: /watch DOG 50 0.025 3
• DOG: ticker cần canh, chấp nhận dog/Dog/DOG.
• 50: lệnh mua đầu tiên phải mua ít nhất 50% tổng cung.
• 0.025: số ETH bạn muốn bot mua; thay bằng số tiền của bạn. Gas tính riêng.
• 3: chờ thuế snipe của ví bạn còn tối đa 3% rồi mua. Nhập 0 để chờ hết thuế; 99 để chấp nhận mọi mức thuế snipe. Phí giao dịch và creator tax tính riêng.

Muốn lệnh đầu mua đúng 50%:
/watch DOG 50 0.025 3 * 50
Dấu * chấp nhận bất kỳ creator; có thể thay bằng địa chỉ creator để lọc thêm.

📋 CÁC LỆNH
/start — xem giới thiệu và hướng dẫn
/help — xem lại hướng dẫn
/key PRIVATE_KEY — nạp ví; key chỉ giữ trong RAM, tin nhắn được xóa
/wallet — xem địa chỉ ví và số dư ETH
/watch — tạo lệnh canh với điều kiện và số tiền bạn nhập
/list — xem ID và trạng thái các lệnh
/rpc — xem trạng thái RPC và số lần chuyển dự phòng
/cancel ID — hủy lệnh còn đang canh, ví dụ /cancel 1
/forget — bỏ ví khỏi RAM và hủy các lệnh đang canh

Mỗi lệnh chỉ mua một lần và chỉ xét launch mới sau khi đặt lệnh. Bot không bảo đảm mua kế tiếp ngay sau lệnh đầu. Hiện hỗ trợ PONS v2 ghép ETH.

🔐 Chỉ nạp key của ví riêng cho bot trong chat riêng này. Telegram không mã hóa đầu cuối. Sau khi khởi động lại bot, bạn cần nạp lại ví.
"""


class Bot:
    def __init__(self, config):
        self.config = config
        self.chain = Chain(config)
        self.store = Store(config.data_dir / "bot.sqlite3")
        self.account = None
        self.lock = asyncio.Lock()
        self.worker = None
        self.app = None
        self.rpc_warning = False
        self.last_rpc_switch = 0

    def authorized(self, update):
        return (update.effective_user is not None and update.effective_chat is not None
                and update.effective_user.id == self.config.owner
                and update.effective_chat.type == "private"
                and update.effective_chat.id == self.config.owner)

    async def notify(self, text):
        # A Telegram outage must never unwind a transaction state change.
        try:
            await self.app.bot.send_message(self.config.owner, text)
        except Exception:
            print("Telegram delivery failed; inspect /list when connected.")

    async def start(self, update, context):
        if self.authorized(update):
            mode = ("🧪 Chế độ hiện tại: MÔ PHỎNG — không gửi giao dịch mua thật."
                    if self.config.dry_run else
                    "🟢 Chế độ hiện tại: MUA THẬT — lệnh đủ điều kiện sẽ tự gửi giao dịch.")
            await update.message.reply_text(HELP + "\n" + mode)

    async def key(self, update, context):
        if not self.authorized(update):
            return
        # Delete even malformed key messages. Never repeat the message or exceptions.
        try:
            await update.message.delete()
        except Exception:
            await self.notify("Không xóa được tin nhắn key; không nạp ví. Xóa tin nhắn thủ công.")
            return
        try:
            if len(context.args) != 1:
                raise ValueError()
            account = Account.from_key(context.args[0])
        except Exception:
            await self.notify("Private key không hợp lệ. Không lưu key.")
            return
        async with self.lock:
            if any(w['wallet'].lower() != account.address.lower() for w in self.store.watches(armed=True)):
                await self.notify("Hủy các lệnh đang canh trước khi đổi ví.")
                return
            self.account = account
        await self.notify(f"Đã nạp ví {account.address}. Key chỉ nằm trong RAM đến khi tắt bot hoặc /forget.")

    async def forget(self, update, context):
        if not self.authorized(update):
            return
        # These synchronous updates can cancel an in-flight quote before claim().
        self.account = None
        for watch in self.store.watches(armed=True):
            self.store.cancel(watch['id'])
        await self.notify("Đã bỏ ví khỏi RAM và hủy các lệnh đang canh. Giao dịch đã gửi vẫn tiếp tục.")

    async def wallet(self, update, context):
        if not self.authorized(update):
            return
        account = self.account
        if not account:
            await self.notify("Chưa nạp ví. Dùng /key trong chat riêng.")
            return
        balance = await self.chain.web3.eth.get_balance(account.address)
        await self.notify(f"{account.address}\n{Web3.from_wei(balance, 'ether')} ETH\nDRY_RUN={self.config.dry_run}")

    async def watch(self, update, context):
        if not self.authorized(update):
            return
        try:
            if not 4 <= len(context.args) <= 6:
                raise ValueError("/watch TICKER MIN_PERCENT ETH MAX_SNIPE_PERCENT [DEPLOYER] [MAX_PERCENT]")
            symbol, min_pct, amount_text, tax_text = context.args[:4]
            ticker = ticker_key(symbol)
            minimum = decimal_units(min_pct, 2)
            maximum = decimal_units(context.args[5], 2) if len(context.args) == 6 else 10000
            amount = decimal_units(amount_text, 18)
            tax = decimal_units(tax_text, 2)
            deployer = context.args[4] if len(context.args) >= 5 else '*'
            deployer = None if deployer == '*' else Web3.to_checksum_address(deployer)
            if not 0 < minimum <= maximum <= 10000 or amount <= 0 or not 0 <= tax <= 9900:
                raise ValueError("Kiểm tra % tổng cung (0–100), ETH >0, thuế snipe 0–99%.")
            head = await self.chain.head()
            async with self.lock:
                if not self.account:
                    raise ValueError("Nạp ví bằng /key trước khi canh.")
                if len(self.store.watches(armed=True)) >= 20:
                    raise ValueError("Tối đa 20 lệnh đang canh.")
                watch_id = self.store.add_watch(ticker, minimum, maximum, amount, tax,
                                                deployer, self.account.address, head['number'] + 1)
            await self.notify(f"Canh #{watch_id}: {symbol}\nLệnh mua đầu: {min_pct}–{maximum / 100:g}% tổng cung.\n"
                              f"Chi {amount_text} ETH + gas; thuế snipe ≤{tax_text}%.\n"
                              f"Chờ ít nhất 5 giây từ lúc launch rồi mới mua.\n"
                              f"Chỉ launch mới từ block {head['number'] + 1}. DRY_RUN={self.config.dry_run}")
        except ValueError as exc:
            # Only input-validation errors here, never key/RPC exceptions.
            await self.notify(str(exc))

    async def list_watches(self, update, context):
        if not self.authorized(update):
            return
        rows = self.store.watches()[-20:]
        text = "\n\n".join(f"#{r['id']} {r['ticker']} · {r['status']}\n"
                            f"{r['min_bps']/100:g}–{r['max_bps']/100:g}% · "
                            f"{Decimal(r['amount'])/10**18} ETH\n"
                            f"{r['tx_hash'] or r['detail'] or ''}" for r in rows)
        await self.notify(text or "Chưa có lệnh.")

    async def cancel(self, update, context):
        if not self.authorized(update):
            return
        try:
            watch_id = int(context.args[0])
        except (ValueError, IndexError):
            await self.notify("/cancel ID")
            return
        cancelled = self.store.cancel(watch_id)
        await self.notify("Đã hủy." if cancelled else "Không có lệnh đang canh với ID này; lệnh đã gửi không thể hủy ở đây.")

    async def rpc_status(self, update, context):
        if not self.authorized(update):
            return
        provider = self.chain.web3.provider
        await self.notify(f"RPC đang chọn: #{provider.active + 1}/{len(provider.urls)}\n"
                          f"Số lần chuyển: {provider.switches}\n"
                          f"RPC sai mạng đã loại: {len(provider.disabled)}\n"
                          "Tự chuyển khi timeout, lỗi kết nối, rate limit hoặc lỗi RPC tạm thời.")

    async def scan(self, head):
        cursor = self.store.cursor()
        if cursor is None:
            self.store.set_cursor(head['number'])
            return
        # Re-read a small overlap to recover launches after shallow reorgs.
        start = max(0, cursor - 2)
        while start <= head['number']:
            end = min(start + 24, head['number'])
            launches = await self.chain.launches(start, end)
            watches = self.store.watches(armed=True)
            for launch in launches:
                eligible = [w for w in watches if launch['blockNumber'] >= w['start_block']]
                if not eligible:
                    continue
                args = launch['args']
                if args['pairToken'].lower() != ZERO:
                    continue
                block = await self.chain.web3.eth.get_block(launch['blockNumber'])
                if head['timestamp'] - block['timestamp'] > self.config.max_age:
                    continue
                symbol = await self.chain.token(args['token']).functions.symbol().call(
                    block_identifier=launch['blockNumber'])
                for watch in eligible:
                    if ticker_key(symbol) != watch['ticker']:
                        continue
                    if watch['deployer'] and watch['deployer'].lower() != args['deployer'].lower():
                        continue
                    self.store.candidate(watch['id'], {
                        'token': args['token'], 'curve': args['curve'], 'deployer': args['deployer'],
                        'symbol': symbol, 'block': launch['blockNumber'],
                        'block_hash': launch['blockHash'].hex(), 'timestamp': block['timestamp']})
            self.store.set_cursor(end)
            start = end + 1

    async def evaluate(self, watch_id, payload, head):
        watch = self.store.watch(watch_id)
        if not watch or watch['status'] != 'armed':
            return
        if head['timestamp'] - payload['timestamp'] < MIN_LAUNCH_AGE_SECONDS:
            return
        if head['timestamp'] - payload['timestamp'] > self.config.max_age:
            self.store.finish_candidate(watch_id, payload['token'])
            await self.notify(f"#{watch_id}: bỏ qua {payload['token']}: hết thời hạn launch.")
            return
        first = await self.chain.first_buy(payload['curve'], payload['block'], head['number'])
        if first is None:
            return
        supply = await self.chain.token(payload['token']).functions.totalSupply().call(
            block_identifier=first['blockNumber'])
        if not first_buy_matches(first['args']['tokensOut'], supply, watch['min_bps'], watch['max_bps']):
            self.store.finish_candidate(watch_id, payload['token'])
            pct = Decimal(first['args']['tokensOut']) * 100 / supply
            await self.notify(f"#{watch_id}: bỏ qua {payload['token']}; lệnh đầu mua {pct:.6f}% tổng cung.")
            return
        async with self.lock:
            watch = self.store.watch(watch_id)
            account = self.account
            if (watch['status'] != 'armed' or not account
                    or watch['wallet'].lower() != account.address.lower()):
                return
            canonical_first = await self.chain.web3.eth.get_block(first['blockNumber'])
            if canonical_first['hash'] != first['blockHash']:
                return
            try:
                plan = await self.chain.plan_buy(payload, watch, account)
            except ValueError as exc:
                # plan_buy's own messages contain no user secrets.
                self.store.finish_candidate(watch_id, payload['token'])
                await self.notify(f"#{watch_id}: bỏ qua {payload['token']}: {exc}")
                return
            if plan is None:
                return
            tx, expected, snipe = plan
            if not self.store.claim(watch_id):
                return
            if self.config.dry_run:
                self.store.status(watch_id, 'simulated', f"token={payload['token']} output={expected}")
                await self.notify(f"DRY_RUN #{watch_id}: khớp {payload['symbol']} {payload['token']}.\n"
                                  f"Mô phỏng thành công; thuế snipe {snipe/100:g}%. Không gửi giao dịch.")
                return
            # Store the deterministic hash BEFORE broadcasting. On timeout, never
            # submit a fresh transaction or choose another token for this watch.
            try:
                signed = account.sign_transaction(tx)
                tx_hash = Web3.to_hex(signed.hash)
                self.store.status(watch_id, 'submitted', payload['token'], tx_hash)
                await self.chain.web3.eth.send_raw_transaction(signed.raw_transaction)
            except Exception:
                await self.notify(f"#{watch_id}: kết quả gửi chưa rõ. Đã khóa lệnh để tránh mua lặp. Xem /list.")
                return
            await self.notify(f"Đã gửi lệnh #{watch_id}: {payload['token']}\nTx: {tx_hash}")

    async def reconcile(self):
        for watch in self.store.watches():
            if watch['status'] != 'submitted' or not watch['tx_hash']:
                continue
            try:
                receipt = await self.chain.web3.eth.get_transaction_receipt(watch['tx_hash'])
            except TransactionNotFound:
                continue
            status = 'confirmed' if receipt['status'] == 1 else 'reverted'
            self.store.status(watch['id'], status, watch['detail'])
            await self.notify(f"Lệnh #{watch['id']}: {status}\nTx: {watch['tx_hash']}")

    async def run_watcher(self):
        while True:
            try:
                head = await self.chain.head()
                await self.scan(head)
                candidate_error = False
                for watch_id, payload in self.store.candidates():
                    # One malformed candidate must not starve all other watches.
                    try:
                        await self.evaluate(watch_id, payload, head)
                    except Exception:
                        candidate_error = True
                        if not self.rpc_warning:
                            await self.notify("Lỗi RPC/contract khi kiểm tra ứng viên; bot sẽ thử lại trong thời hạn launch.")
                            self.rpc_warning = True
                await self.reconcile()
                if not candidate_error:
                    self.rpc_warning = False
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self.rpc_warning:
                    await self.notify("Kết nối RPC tạm lỗi; chưa phát lệnh mới. Bot đang kết nối lại.")
                    self.rpc_warning = True
            provider = self.chain.web3.provider
            if provider.switches != self.last_rpc_switch:
                self.last_rpc_switch = provider.switches
                await self.notify(f"Đã chuyển RPC dự phòng: #{provider.active + 1}/{len(provider.urls)}.")
            await asyncio.sleep(self.config.poll)

    async def post_init(self, app):
        self.app = app
        await self.chain.validate()
        if self.store.cursor() is None:
            self.store.set_cursor((await self.chain.head())['number'])
        self.worker = asyncio.create_task(self.run_watcher())
        print(f"PONS bot started | chain=4663 | dry_run={self.config.dry_run}", flush=True)
        await self.notify(f"Bot đã chạy · PONS v2 · Robinhood 4663 · DRY_RUN={self.config.dry_run}\n"
                          "Dùng /start. Sau restart cần nạp lại ví; các lệnh canh vẫn được lưu.")

    async def post_shutdown(self, app):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.account = None
        await self.chain.close()
        self.store.db.close()

    async def error(self, update, context):
        # Telegram's default exception logging can include the Update containing a key.
        if isinstance(update, Update) and self.authorized(update):
            await self.notify("Không hoàn tất được lệnh; kiểm tra /list. Chi tiết nhạy cảm không được ghi log.")

    def run(self):
        app = (Application.builder().token(self.config.token)
               .post_init(self.post_init).post_shutdown(self.post_shutdown).build())
        for name, handler in [('start', self.start), ('help', self.start), ('key', self.key),
                              ('wallet', self.wallet), ('forget', self.forget), ('watch', self.watch),
                              ('list', self.list_watches), ('cancel', self.cancel), ('rpc', self.rpc_status)]:
            app.add_handler(CommandHandler(name, handler))
        app.add_error_handler(self.error)
        app.run_polling(drop_pending_updates=True, allowed_updates=['message'])


def main():
    # No verbose HTTP logs: Telegram API URLs contain the bot token.
    logging.disable(logging.CRITICAL)
    try:
        Bot(Config.load()).run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Bot could not start ({type(exc).__name__}). Check .env and RPC. Secrets are not logged.")
        raise SystemExit(1) from None
