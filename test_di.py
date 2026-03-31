import sys
from datetime import date
import traceback
import pyield as yd

print(f"pyield version: {yd.__version__}")

test_date = date(2026, 3, 27)
print(f"\nFetching {test_date}...")
try:
    import logging
    logging.basicConfig(level=logging.WARNING)
    df = yd.futures(date=test_date, contract_code="DI1")
    print(f"Rows: {len(df)}")
    if not df.is_empty():
        print(df.select(["TradeDate", "TickerSymbol", "SettlementRate"]).head(3))
    else:
        print("EMPTY - no data returned")
except Exception as e:
    print(f"EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
