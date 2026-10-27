"""
Money safety module for deterministic monetary calculations.
Guarantees Rule 4: All authoritative financial values are computed using integer cents or Decimal.
Rejects floats, NaN, non-finite input, scientific notation, and fractional sub-cents.
"""

from decimal import Decimal, InvalidOperation
from typing import Union

def parse_to_cents(value: Union[str, int, Decimal]) -> int:
    """
    Parses a monetary amount into authoritative integer cents.

    Accepted types:
      - str: Formatted decimal string (e.g., '1,234.50', '-0.01', '$10.00', '10')
      - int: Whole currency units (e.g., 10 -> 1000 cents)
      - Decimal: High-precision decimal amount in currency units (e.g., Decimal('1234.50') -> 123450)

    Strictly rejected inputs:
      - float: Binary floating-point values (e.g. 0.1 + 0.2, 1.23) to prevent IEEE 754 precision loss
      - bool: Booleans are not numbers
      - Non-finite numbers (NaN, Infinity, -Infinity)
      - Scientific notation strings or Decimals ('1e3', '1E-2')
      - Fractional sub-cents beyond 2 decimal places (e.g. '12.345', '0.30000000000000004')
      - Malformed comma groupings (e.g. '1,,234', '1,2345')

    Returns:
      int: Amount in integer cents.
    """
    if isinstance(value, bool):
        raise TypeError(f"Boolean values cannot be parsed as monetary amounts: {value!r}")

    if isinstance(value, float):
        raise TypeError(
            f"Float input is strictly forbidden for monetary operations to prevent precision loss: {value!r}. "
            f"Pass a string, whole integer, or Decimal."
        )

    if not isinstance(value, (str, int, Decimal)):
        raise TypeError(f"Unsupported type for monetary amount: {type(value).__name__}")

    if isinstance(value, str):
        val_str = value.strip()
        if not val_str:
            raise ValueError("Empty string cannot be parsed as monetary amount.")

        # Reject scientific notation
        if "e" in val_str.lower():
            raise ValueError(f"Scientific notation is not allowed for monetary amounts: {value!r}")

        s = val_str
        is_negative = False

        # Handle optional signs and currency symbols, e.g. "-$10.50", "$-10.50", "$10.50", "-10.50"
        if s.startswith("-"):
            is_negative = True
            s = s[1:].strip()
        elif s.startswith("+"):
            s = s[1:].strip()

        if s.startswith("$"):
            s = s[1:].strip()
            if not is_negative and s.startswith("-"):
                is_negative = True
                s = s[1:].strip()

        if not s:
            raise ValueError(f"Invalid monetary format: {value!r}")

        # Validate thousands grouping if commas exist
        if "," in s:
            parts = s.split(".")
            if len(parts) > 2:
                raise ValueError(f"Invalid monetary format with multiple decimals: {value!r}")
            int_part = parts[0]
            int_subparts = int_part.split(",")
            # First group must have 1-3 digits, subsequent groups must have exactly 3 digits
            if not (1 <= len(int_subparts[0]) <= 3 and all(len(sp) == 3 and sp.isdigit() for sp in int_subparts[1:])):
                raise ValueError(f"Invalid comma grouping in monetary string: {value!r}")
            s = s.replace(",", "")

        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ValueError(f"Invalid monetary amount: {value!r}")

        if is_negative:
            d = -d

    elif isinstance(value, int):
        d = Decimal(value)

    elif isinstance(value, Decimal):
        if "e" in str(value).lower():
            raise ValueError(f"Scientific notation is not allowed for monetary amounts: {value!r}")
        d = value

    # Check finite
    if not d.is_finite():
        raise ValueError(f"Non-finite value cannot be parsed as money: {value!r}")

    # Validate precision: reject fractional sub-cents
    cents_decimal = d * 100
    if cents_decimal != cents_decimal.to_integral_value():
        raise ValueError(f"Monetary amount has fractional cents beyond 2 decimal places: {value!r}")

    return int(cents_decimal)

def format_cents(cents: int) -> str:
    """
    Formats an integer count of cents into a canonical two-decimal-place currency string.

    Example:
      format_cents(123450) -> '1234.50'
      format_cents(-1)     -> '-0.01'
      format_cents(0)      -> '0.00'
      format_cents(5)      -> '0.05'

    Rejects:
      Non-integer values (floats, strings, Decimals, booleans).
    """
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise TypeError(f"cents must be an integer, got {type(cents).__name__}")

    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    dollars = abs_cents // 100
    remainder = abs_cents % 100
    return f"{sign}{dollars}.{remainder:02d}"
