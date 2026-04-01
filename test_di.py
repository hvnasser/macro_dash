import sys
from datetime import date
import traceback
import pyield as yd

sys.path.insert(0, ".")

print(f"pyield version: {yd.__version__}")

test_date = date(2026, 3, 31)
print(f"\n--- 1. Fetching {test_date} from B3 ---")
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
    sys.exit(1)

print("\n--- 2. Testing Supabase connection ---")
try:
    from utils.db import get_client
    client = get_client()
    resp = client.table("di_futures_raw").select("ref_date", count="exact").limit(1).execute()
    print(f"Connection OK — di_futures_raw has {resp.count} rows")
except Exception as e:
    print(f"EXCEPTION connecting to Supabase: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n--- 3. Checking for today's data in di_futures_raw ---")
resp2 = client.table("di_futures_raw").select("*").eq("ref_date", test_date.isoformat()).limit(3).execute()
print(f"Rows for {test_date}: {len(resp2.data)}")
if resp2.data:
    print("Sample:", resp2.data[0])

print("\n--- 4. Saving one test row to Supabase ---")
try:
    from data_pipeline.di_curve import fetch_di_settlements, save_raw_settlements
    df_raw = fetch_di_settlements(test_date)
    if df_raw.empty:
        print("No data to save (empty fetch)")
    else:
        print(f"Fetched {len(df_raw)} rows, saving...")
        save_raw_settlements(df_raw)
        # Verify it landed
        resp3 = client.table("di_futures_raw").select("ref_date", count="exact").eq("ref_date", test_date.isoformat()).execute()
        print(f"Rows in DB for {test_date} after save: {resp3.count}")
except Exception as e:
    print(f"EXCEPTION during save: {type(e).__name__}: {e}")
    traceback.print_exc()
