# Telegram bot canh launch PONS

Bot Python dành cho **một chủ sở hữu**, chạy trên **Robinhood Chain (chain ID 4663)**.
Theo dõi factory **PONS v2**, so sánh ticker không phân biệt hoa thường, kiểm tra
**lệnh `CurveBuy` đầu tiên của bất kỳ ví nào**, rồi tự mua bằng số ETH đã nhập.

## Cách chạy trên Windows

```powershell
cd D:\PythonTool\gira
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Mở `.env` và điền:

- `TELEGRAM_BOT_TOKEN`: token bot tạo bằng BotFather.
- `TELEGRAM_OWNER_ID`: Telegram user ID dạng số của bạn. Bot chỉ nhận lệnh từ ID
  này trong chat riêng; không hỗ trợ group hay nhiều người dùng.
- `RPC_URL`: RPC Robinhood Chain có hỗ trợ `eth_getLogs`, `eth_call`,
  `eth_estimateGas` và `eth_sendRawTransaction`. Endpoint mặc định lấy từ docs PONS;
  trong môi trường phát triển hiện tại endpoint này bị lỗi DNS, chưa xác minh live.
- Ban đầu giữ `DRY_RUN=true` để kiểm tra toàn bộ điều kiện, quote và simulation
  mà không ký hoặc gửi giao dịch. Ví vẫn cần có ETH để simulation/balance check thành công.

```powershell
.\.venv\Scripts\python.exe -m ponsbot
```

Bot kiểm tra chain ID và bytecode factory khi khởi động; nếu sai hoặc không truy cập
được RPC thì dừng, không chuyển sang mạng khác. Chạy một tiến trình duy nhất cho
mỗi bot và database. Không dùng cùng ví cho các bot hoặc chương trình gửi giao dịch
khác cùng lúc, vì nonce có thể xung đột.

## Lệnh Telegram

Gửi `/start`, sau đó gửi **trong chat riêng với bot của bạn**:

```text
/key YOUR_DEDICATED_WALLET_PRIVATE_KEY
/wallet
/watch TICKER MIN_PERCENT ETH MAX_SNIPE_PERCENT [DEPLOYER] [MAX_PERCENT]
```

Ví dụ:

```text
/watch DOG 50 0.01 3
```

Nghĩa là:

- Chỉ xét token **mới ra mắt sau khi lệnh canh được tạo**, ticker chính xác `DOG`
  nhưng chấp nhận `dog`, `Dog`, ... Không tìm theo tên token, không khớp chuỗi con.
- Lệnh mua đầu tiên phải nhận **ít nhất 50% tổng cung**. Đây là `tokensOut` trong
  event đầu tiên, chia cho `totalSupply`, không phải tổng các lệnh hay số dư ví.
- Người thực hiện lệnh đầu có thể là creator hoặc bất kỳ ví nào. Nếu lệnh đầu mua
  10%, lệnh sau mua 50%, token **không đạt** điều kiện.
- Bot dùng **0.01 ETH** cho lệnh mua, cộng phí gas riêng; không có nghĩa bot mua 50%.
- Khi lệnh đầu đạt điều kiện, bot mua sớm nhất mà nó quan sát được thuế snipe của ví
  mình **không quá 3%**. Nhập `99` nếu muốn chấp nhận mọi mức thuế snipe hiện có;
  nhập `0` để đợi hết thuế hoặc khi ví được miễn. Mức này **không bao gồm** phí
  giao dịch cơ bản và creator tax. Các khoản phí đều đã được trừ khi tính quote.
- Mỗi watch chỉ thực hiện tối đa **một lần thử mua**. Nếu gửi lỗi không rõ kết quả,
  bị revert hoặc đang pending, bot không tự đặt lại lệnh để tránh mua hai lần.

Muốn điều kiện **đúng 50%** thay vì ít nhất 50%:

```text
/watch DOG 50 0.01 3 * 50
```

Điều kiện bằng tuyệt đối dùng số nguyên on-chain, không làm tròn. Nếu creator mua
49.999999%, nó không bằng 50%. Để cho phép khoảng 49.90–50.10%:

```text
/watch DOG 49.90 0.01 3 * 50.10
```

Để lọc thêm địa chỉ creator, thay `*` bằng địa chỉ creator. Ticker có thể bị người
khác tạo trùng; điều kiện tỷ lệ mua đầu không xác thực danh tính dự án.

Các lệnh khác:

```text
/list
/cancel 1
/forget
```

`/cancel` hủy watch còn `armed`, kể cả khi đang lấy quote. Khi watch đã chuyển sang
`executing`/`submitted`, lệnh hủy không rút được giao dịch đã ký/gửi.
`/forget` bỏ ví khỏi bộ nhớ của ứng dụng và hủy tất cả watch còn `armed`.

## Bật mua thật

Sau khi cấu hình, nạp ví và kiểm tra chế độ mô phỏng:

1. Dừng bot.
2. Đổi `DRY_RUN=false` trong `.env`.
3. Khởi động lại, nạp lại cùng ví qua `/key`, tạo watch mới nếu watch trước đã
   `simulated`. Watch còn `armed` được giữ trong database.

Bot không có API đổi chế độ giao dịch qua Telegram. Mặc định không có key và không
có lệnh canh. Repo không chứa token Telegram hay private key thực.

## Key và quyền truy cập

- Telegram bot chat **không có mã hóa đầu cuối**. Việc xóa tin nhắn không chứng minh
  Telegram chưa xử lý hoặc không còn bản sao. Chỉ dùng ví riêng với số dư dành cho bot;
  không dùng seed phrase hoặc ví tài sản chính. Không gửi key vào chat Codex này.
- Bot xóa tin nhắn `/key` trước khi nạp ví; nếu xóa không thành công thì không nạp.
- Private key chỉ nằm trong RAM của tiến trình, không lưu database hay `.env` và
  không in log. Python không bảo đảm xóa vật lý mọi bản sao key trong RAM/pagefile.
- `.env` chứa token Telegram; không commit hoặc chia sẻ file này. Database lưu địa
  chỉ ví, điều kiện canh, token và hash giao dịch, không lưu private key.
- Sau restart, watch bị ràng buộc vào địa chỉ ví ban đầu. Bot không tự dùng ví khác
  để thực hiện watch cũ. Dùng `/cancel` trước nếu muốn đổi ví.
- Startup bỏ qua backlog Telegram để tránh chạy lại lệnh cũ hoặc nạp key cũ.

## Giới hạn tốc độ và giao thức

- Chỉ hỗ trợ **PONS v2, native ETH quote**. Không mua token v1, token ghép ERC-20,
  hoặc token đã chuyển sang pool Uniswap v4.
- Theo dõi log on-chain qua HTTP polling, không phải mempool hay bundle. Bot phải
  chờ giao dịch launch/lệnh mua đầu xuất hiện trong block. Không thể chen vào trước
  lượt mua creator nằm cùng giao dịch tạo token, cũng không bảo đảm là lượt mua thứ hai.
- `POLL_SECONDS` là thời gian nghỉ giữa các vòng; độ trễ thực còn có thời gian RPC,
  quote, simulation, ký và inclusion. RPC công khai có thể chậm hoặc giới hạn tốc độ.
- `MAX_LAUNCH_AGE_SECONDS` mặc định 60 giây tính từ timestamp block launch. Sau thời
  gian này ứng viên hết hạn, kể cả đang đợi lệnh mua đầu hoặc đợi thuế giảm; watch
  vẫn canh token mới khác cùng ticker.
- Lịch sử log lấy từ block launch để xác định chính xác lệnh mua đầu. Nếu RPC lỗi,
  bot thử lại trong thời hạn; không dùng số dư hiện tại để đoán lệnh mua đầu.
- `SLIPPAGE_BPS=300` là 3% trượt giá so với quote hiện tại; quote đã tính price impact,
  phí cơ bản, creator tax và snipe tax. Có kiểm tra gas price, gas limit và số dư.
- Không chủ động mua tới ranh giới graduation. Nếu người khác giao dịch trước khi
  lệnh bot thực thi, PONS vẫn có thể partial-fill ở graduation: `minTokensOut` của
  PONS kiểm tra tỷ giá trong trường hợp đó, không bảo đảm tổng số token nhận được.
- Kiểm tra hash block trước khi mua và quét lại hai block gần nhất, nhưng gửi ngay
  sau inclusion vẫn chịu rủi ro reorg. Trạng thái `confirmed` chỉ nghĩa receipt thành
  công khi quan sát được, chưa phải cam kết finality.
- Hash giao dịch được lưu trước broadcast. Nếu trạng thái `submitted` chưa có receipt,
  tự kiểm tra hash trên explorer trước khi tạo lệnh thay thế. Không tự retry giao dịch
  có kết quả chưa rõ. `executing` sau crash cũng cần kiểm tra ví/nonce thủ công.

## Kiểm thử

```powershell
python -m unittest discover -s tests -v
```

Các test chạy offline với dữ liệu giả: tỷ lệ tổng cung, quote/rounding, case-insensitive
ticker, kiểm tra quyền Telegram, xóa key, cancel trong lúc quote, one-shot execution,
và broadcast timeout không gây mua lặp. Chưa có kiểm thử giao dịch live vì chưa cấu
hình bot Telegram/ví và RPC mặc định không truy cập được từ môi trường này.

## Nguồn giao thức

- [PONS v2 docs](https://docs.ponsfamily.com/v2): địa chỉ factory, `TokenLaunched`,
  `CurveBuy`, curve quote và buy ABI.
- [PONS v1 docs](https://docs.ponsfamily.com/): phiên bản khác, không dùng trong bot này.

Contract có thể được thay bằng deployment mới. Xác minh `PONS_FACTORY` với docs
chính thức trước khi bật mua thật; bot chỉ theo dõi địa chỉ factory được cấu hình.
