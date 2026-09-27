"""Money representation: integer amounts in a currency's minor unit (e.g.
cents), never floats, with an ISO 4217 code. This matches the legacy app's
``Space.price`` (integer cents).

Only the currencies below are accepted. ``exponent`` is the number of minor
units per major unit as a power of ten (USD 2 -> 1234 = $12.34; JPY 0 ->
1234 = ¥1,234). Clients get the exponent from the API rather than assuming
two decimal places.
"""

CURRENCY_EXPONENTS = {
    "USD": 2,
    "CAD": 2,
    "MXN": 2,
    "EUR": 2,
    "GBP": 2,
    "AUD": 2,
    "NZD": 2,
    "JPY": 0,
    "KRW": 0,
}
SUPPORTED_CURRENCIES = tuple(CURRENCY_EXPONENTS)
# Upper bound for one stall's price, in minor units (10 million major units
# at exponent 2). Keeps values well inside JavaScript's safe integer range.
MAX_PRICE_MINOR = 1_000_000_000


def exponent(currency: str) -> int:
    return CURRENCY_EXPONENTS[currency]
