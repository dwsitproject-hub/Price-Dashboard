import sys
import subprocess
import logging
from datetime import datetime
from pathlib import Path

ROOT    = Path(__file__).parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "update_log.txt"

LOG_DIR.mkdir(exist_ok=True)

# Configure logging so gsheet_data warnings go to the same log file
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.WARNING,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def run(script: str) -> None:
    log(f"Running {script}...")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log(f"  {line}")
    if result.returncode != 0:
        err = result.stderr.strip() if result.stderr else "(no stderr)"
        raise RuntimeError(f"{script} failed (exit {result.returncode}):\n{err}")
    log(f"{script} completed successfully.")


def main() -> None:
    log("=" * 60)
    log("Update started")
    try:
        run("fetch.py")
        run("process.py")
        run("oi_analyze.py")
        run("oi_summary.py")
        run("oi_cumulative.py")

        # Fetch India local prices from Google Sheet (non-critical)
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from gsheet_data import fetch_india_data
            india = fetch_india_data()
            if india:
                log(f"India local data fetched: {len(india[0])} outrights, {len(india[2])} spreads.")
            else:
                log("WARNING: India local prices unavailable — dashboard will show N/A.")
        except Exception as exc:
            log(f"WARNING: India local price fetch error: {exc}")

        # Fetch Tradable Premium data from the Tradeable Prices Pull sheet (non-critical)
        try:
            from tradable_prem_data import fetch_tradable_prem
            tp = fetch_tradable_prem()
            if tp is not None:
                n = tp.groupby(["region", "product"]).ngroups
                log(f"Tradable Prem fetched: {len(tp)} rows across {n} series.")
            else:
                log("WARNING: Tradable Prem unavailable (no service-account key or unreadable sheet).")
        except Exception as exc:
            log(f"WARNING: Tradable Prem fetch error: {exc}")

        # Fetch Argus biofuels reference prices from the Argus Google Sheet (non-critical)
        try:
            from argus_data import fetch_argus
            argus = fetch_argus()
            if argus is not None:
                n = argus["series_key"].nunique()
                log(f"Argus fetched: {len(argus)} rows across {n} series.")
            else:
                log("WARNING: Argus data unavailable (no service-account key or unreadable sheet).")
        except Exception as exc:
            log(f"WARNING: Argus fetch error: {exc}")

        # Push DCP settlement curve (USD/MT) to a Google Sheet (non-critical)
        try:
            from dcp_gsheet_push import push_dcp_prices
            n = push_dcp_prices()
            if n is not None:
                log(f"DCP price push: wrote {n} rows to Google Sheet.")
            else:
                log("WARNING: DCP price push skipped (no key/libs or DCP_GSHEET_ID blank).")
        except Exception as exc:
            log(f"WARNING: DCP price push error: {exc}")

        # Push cash prices (BO Arg upriver, MY local CPO, Black Sea sunflower) to Google Sheet (non-critical)
        try:
            from cash_gsheet_push import push_cash_prices
            n = push_cash_prices()
            if n is not None:
                log(f"Cash price push: wrote {n} rows to Google Sheet.")
            else:
                log("WARNING: Cash price push skipped (no key/libs or sheet ID blank).")
        except Exception as exc:
            log(f"WARNING: Cash price push error: {exc}")

        # Extract document intelligence (non-critical)
        try:
            run("doc_extract.py")
        except Exception as exc:
            log(f"WARNING: Document extraction error: {exc}")

        # Refresh the commodity/energy event calendar (non-critical)
        try:
            from calendar_data import fetch_calendar
            n = fetch_calendar()
            if n:
                log(f"Calendar: fetched {n} upcoming events.")
            else:
                log("WARNING: Calendar unavailable — keeping previous cache.")
        except Exception as exc:
            log(f"WARNING: Calendar fetch error: {exc}")

        log("Update finished successfully.")
    except Exception as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
    log("=" * 60)


if __name__ == "__main__":
    main()