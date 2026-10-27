"""
Unit tests for core/money.py.
Verifies parsing to integer cents, formatting cents, float rejection,
non-finite rejection, scientific notation rejection, and precision boundaries.
"""

import unittest
from decimal import Decimal
from core.money import parse_to_cents, format_cents

class TestMoneySafety(unittest.TestCase):

    def test_parse_valid_strings(self):
        self.assertEqual(parse_to_cents("1,234.50"), 123450)
        self.assertEqual(parse_to_cents("-0.01"), -1)
        self.assertEqual(parse_to_cents("0.00"), 0)
        self.assertEqual(parse_to_cents("0"), 0)
        self.assertEqual(parse_to_cents("10"), 1000)
        self.assertEqual(parse_to_cents("10.00"), 1000)
        self.assertEqual(parse_to_cents("$1,234.50"), 123450)
        self.assertEqual(parse_to_cents("-$0.01"), -1)
        self.assertEqual(parse_to_cents("$-0.01"), -1)
        self.assertEqual(parse_to_cents("+50.25"), 5025)
        self.assertEqual(parse_to_cents("12,345,678.90"), 1234567890)

    def test_parse_valid_integers_and_decimals(self):
        self.assertEqual(parse_to_cents(10), 1000)
        self.assertEqual(parse_to_cents(0), 0)
        self.assertEqual(parse_to_cents(-5), -500)
        self.assertEqual(parse_to_cents(Decimal("1234.50")), 123450)
        self.assertEqual(parse_to_cents(Decimal("-0.01")), -1)
        self.assertEqual(parse_to_cents(Decimal("0.00")), 0)
        self.assertEqual(parse_to_cents(Decimal("10")), 1000)

    def test_format_cents(self):
        self.assertEqual(format_cents(123450), "1234.50")
        self.assertEqual(format_cents(-1), "-0.01")
        self.assertEqual(format_cents(0), "0.00")
        self.assertEqual(format_cents(5), "0.05")
        self.assertEqual(format_cents(100), "1.00")
        self.assertEqual(format_cents(-100), "-1.00")

    def test_round_trip(self):
        for c in [-100000, -12345, -1, 0, 1, 99, 100, 12345, 9999999]:
            formatted = format_cents(c)
            parsed = parse_to_cents(formatted)
            self.assertEqual(parsed, c)

    def test_reject_float_inputs(self):
        # Immediate rejection of binary floats
        with self.assertRaises(TypeError):
            parse_to_cents(0.1 + 0.2)
        with self.assertRaises(TypeError):
            parse_to_cents(12.50)
        with self.assertRaises(TypeError):
            parse_to_cents(0.0)
        with self.assertRaises(TypeError):
            parse_to_cents(-0.01)

    def test_reject_boolean_inputs(self):
        with self.assertRaises(TypeError):
            parse_to_cents(True)
        with self.assertRaises(TypeError):
            parse_to_cents(False)
        with self.assertRaises(TypeError):
            format_cents(True)

    def test_reject_scientific_notation(self):
        with self.assertRaises(ValueError):
            parse_to_cents("1e3")
        with self.assertRaises(ValueError):
            parse_to_cents("1E2")
        with self.assertRaises(ValueError):
            parse_to_cents("1e-2")
        with self.assertRaises(ValueError):
            parse_to_cents(Decimal("1e3"))

    def test_reject_non_finite_inputs(self):
        with self.assertRaises(ValueError):
            parse_to_cents("NaN")
        with self.assertRaises(ValueError):
            parse_to_cents("Infinity")
        with self.assertRaises(ValueError):
            parse_to_cents("-Infinity")
        with self.assertRaises(ValueError):
            parse_to_cents(Decimal("NaN"))
        with self.assertRaises(ValueError):
            parse_to_cents(Decimal("Infinity"))
        with self.assertRaises(ValueError):
            parse_to_cents(Decimal("-Infinity"))

    def test_reject_subcent_fractional_precision(self):
        # Values with precision beyond 2 decimal places must be rejected
        with self.assertRaises(ValueError):
            parse_to_cents("12.345")
        with self.assertRaises(ValueError):
            parse_to_cents("0.30000000000000004")
        with self.assertRaises(ValueError):
            parse_to_cents(Decimal("12.345"))
        with self.assertRaises(ValueError):
            parse_to_cents(Decimal("0.001"))

    def test_reject_malformed_formats(self):
        with self.assertRaises(ValueError):
            parse_to_cents("")
        with self.assertRaises(ValueError):
            parse_to_cents("   ")
        with self.assertRaises(ValueError):
            parse_to_cents("1,,234.50")
        with self.assertRaises(ValueError):
            parse_to_cents("1,2345.50")
        with self.assertRaises(ValueError):
            parse_to_cents("abc")

    def test_format_cents_type_errors(self):
        with self.assertRaises(TypeError):
            format_cents(12.34)  # float rejected
        with self.assertRaises(TypeError):
            format_cents("1234")  # string rejected
        with self.assertRaises(TypeError):
            format_cents(Decimal("1234"))  # Decimal rejected

if __name__ == "__main__":
    unittest.main()
