import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    owner: int
    rpc: str
    factory: str
    dry_run: bool
    poll: float
    max_age: int
    slippage: int
    max_gas_price: int
    max_gas: int
    data_dir: Path

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        owner = int(os.environ.get("TELEGRAM_OWNER_ID", "0"))
        if not token or owner <= 0:
            raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID in .env")
        c = cls(token, owner, os.environ.get("RPC_URL", "https://rpc.mainnet.chain.robinhood.com"),
                os.environ.get("PONS_FACTORY", "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"),
                os.environ.get("DRY_RUN", "true").lower() != "false",
                float(os.environ.get("POLL_SECONDS", "0.5")),
                int(os.environ.get("MAX_LAUNCH_AGE_SECONDS", "60")),
                int(os.environ.get("SLIPPAGE_BPS", "300")),
                int(Decimal(os.environ.get("MAX_GAS_PRICE_GWEI", "20")) * 10**9),
                int(os.environ.get("MAX_GAS_LIMIT", "1500000")),
                Path(os.environ.get("DATA_DIR", "data")))
        if not (0 <= c.slippage < 10000 and c.poll >= 0.1 and c.max_age > 0
                and c.max_gas >= 21000 and c.max_gas_price > 0):
            raise ValueError("Invalid execution limits in .env")
        return c
