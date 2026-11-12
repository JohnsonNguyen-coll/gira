from decimal import Decimal, InvalidOperation

BPS = 10_000
MIN_LAUNCH_AGE_SECONDS = 5


def decimal_units(text, decimals):
    try:
        value = Decimal(text)
        scaled = value * 10**decimals
        if not value.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError("Too many decimal places")
        return int(scaled)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("Invalid decimal number") from exc


def ticker_key(ticker):
    # Exact symbol match, with case folded. Do not strip '$' from a real symbol.
    ticker = ticker.strip()
    if not ticker or len(ticker) > 64:
        raise ValueError("Ticker must contain 1–64 characters")
    return ticker.casefold()


def first_buy_rejection(tokens_out, supply, minimum_bps, maximum_bps):
    if supply <= 0:
        return 'invalid_supply'
    if not 0 < minimum_bps <= maximum_bps <= BPS:
        return 'invalid_range'
    # Keep the exact integer comparison and the explanation in one decision.
    scaled_output = tokens_out * BPS
    if scaled_output < minimum_bps * supply:
        return 'below_minimum'
    if scaled_output > maximum_bps * supply:
        return 'above_maximum'
    return None


def first_buy_matches(tokens_out, supply, minimum_bps, maximum_bps):
    return first_buy_rejection(tokens_out, supply, minimum_bps, maximum_bps) is None


def quote_buy(quote_in, quote_reserve, token_reserve, sellable, fee_bps,
              creator_tax_bps, raw_snipe_bps):
    if min(quote_in, quote_reserve, token_reserve, sellable) <= 0:
        raise ValueError("Curve is empty or closed")
    if not 0 <= fee_bps + creator_tax_bps <= BPS - 100:
        raise ValueError("Invalid curve fees")
    snipe = min(raw_snipe_bps, BPS - fee_bps - creator_tax_bps - 100)
    net = quote_in - sum(quote_in * rate // BPS for rate in
                         (fee_bps, creator_tax_bps, snipe))
    output = net * token_reserve // (quote_reserve + net)
    # A partial fill weakens PONS's minTokensOut quantity guarantee; skip it.
    if output >= sellable:
        raise ValueError("Buy would reach graduation / partial fill; skipped")
    if output <= 0:
        raise ValueError("Zero token output")
    return output, snipe
