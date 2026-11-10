import unittest

from ponsbot.rules import decimal_units, ticker_key, first_buy_matches, quote_buy


class RulesTests(unittest.TestCase):
    def test_case_insensitive_exact_ticker(self):
        self.assertEqual(ticker_key('PoNs'), ticker_key('pons'))
        self.assertNotEqual(ticker_key('PONS2'), ticker_key('PONS'))

    def test_first_buy_uses_exact_fraction(self):
        supply = 10**27
        self.assertTrue(first_buy_matches(supply // 2, supply, 5000, 10000))
        self.assertFalse(first_buy_matches(supply // 2 - 1, supply, 5000, 10000))
        self.assertTrue(first_buy_matches(supply // 2, supply, 5000, 5000))
        self.assertFalse(first_buy_matches(supply // 2 + 1, supply, 5000, 5000))

    def test_decimal_amount_without_float(self):
        self.assertEqual(decimal_units('0.000000000000000001', 18), 1)
        self.assertEqual(decimal_units('50.01', 2), 5001)
        for text in ['nan', 'inf', 'abc', '0.0000000000000000001']:
            with self.assertRaises(ValueError):
                decimal_units(text, 18)

    def test_quote_fee_rounding_and_snipe_cap(self):
        # 1% base + 2% creator leaves a maximum 96% snipe, 1% net.
        out, tax = quote_buy(10_000, 100_000, 1_000_000, 800_000, 100, 200, 9900)
        self.assertEqual(tax, 9600)
        self.assertEqual(out, 100 * 1_000_000 // 100_100)

    def test_normal_quote(self):
        out, tax = quote_buy(10_001, 100_000, 1_000_000, 800_000, 100, 200, 300)
        net = 10001 - 100 - 200 - 300
        self.assertEqual(out, net * 1_000_000 // (100_000 + net))
        self.assertEqual(tax, 300)

    def test_skip_partial_and_closed_curve(self):
        with self.assertRaises(ValueError):
            quote_buy(100_000, 100_000, 1_000_000, 100, 100, 0, 0)
        with self.assertRaises(ValueError):
            quote_buy(100, 100, 100, 0, 100, 0, 0)


if __name__ == '__main__':
    unittest.main()
