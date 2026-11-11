"""Bulk persistence via COPY into temp tables + change-only upserts (TD §6.1 steps 3–5)."""
from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from datetime import date

import pandas as pd

from app.core.db import get_engine


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _int(v):
    f = _num(v)
    return None if f is None else int(round(f))


def normalise_lseg_rows(rows: Iterable[dict]) -> list[tuple]:
    """LSEG Contents rows → (ric, date, last, settle, bid, ask, volume, oi)."""
    out = []
    for r in rows:
        ric, td = r.get("RIC"), r.get("Trade Date")
        if not ric or not td:
            continue  # legacy note: one NaT-dated row breaks date slicing — drop at ingest
        try:
            d = date.fromisoformat(str(td)[:10])
        except ValueError:
            continue
        out.append((ric, d, _num(r.get("Last")), _num(r.get("Settlement Price")), _num(r.get("Bid")),
                    _num(r.get("Ask")), _int(r.get("Volume")), _int(r.get("Open Interest"))))
    return out


UPSERT_PRICES = """
INSERT INTO market.price_daily AS p (ric, trade_date, last_price, settlement_price, bid, ask, price,
                                     volume, open_interest, source, run_id, updated_at)
SELECT DISTINCT ON (t.ric, t.trade_date)
       t.ric, t.trade_date, t.last_price, t.settlement_price, t.bid, t.ask,
       CASE WHEN i.price_basis = 'mid' THEN (t.bid + t.ask) / 2
            ELSE coalesce(t.last_price, t.settlement_price) END,
       t.volume, t.open_interest, %(source)s, %(run_id)s, now()
FROM tmp_prices t JOIN market.instruments i ON i.ric = t.ric
ORDER BY t.ric, t.trade_date, t.seq DESC
ON CONFLICT (ric, trade_date) DO UPDATE SET
  last_price = EXCLUDED.last_price, settlement_price = EXCLUDED.settlement_price,
  bid = EXCLUDED.bid, ask = EXCLUDED.ask, price = EXCLUDED.price, volume = EXCLUDED.volume,
  open_interest = EXCLUDED.open_interest, source = EXCLUDED.source, run_id = EXCLUDED.run_id,
  updated_at = now()
WHERE (p.last_price, p.settlement_price, p.bid, p.ask, p.volume, p.open_interest)
      IS DISTINCT FROM
      (EXCLUDED.last_price, EXCLUDED.settlement_price, EXCLUDED.bid, EXCLUDED.ask, EXCLUDED.volume,
       EXCLUDED.open_interest)
"""


def upsert_prices(rows: list[tuple], source: str, run_id: uuid.UUID) -> int:
    """Newest row wins for duplicate (ric, date) — legacy drop_duplicates(keep='last')."""
    if not rows:
        return 0
    raw = get_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("CREATE TEMP TABLE tmp_prices (seq bigint, ric varchar(32), trade_date date, "
                        "last_price numeric, settlement_price numeric, bid numeric, ask numeric, volume bigint, "
                        "open_interest bigint) ON COMMIT DROP")
            with cur.copy("COPY tmp_prices FROM STDIN") as cp:
                for i, r in enumerate(rows):
                    cp.write_row((i, *r))
            cur.execute(UPSERT_PRICES, {"source": source, "run_id": run_id})
            n = cur.rowcount
        raw.commit()
        return n
    finally:
        raw.close()


def load_prices_long(rics: list[str] | None = None, with_oi: bool = False) -> pd.DataFrame:
    cols = "trade_date, ric, price::float8"
    if with_oi:
        cols += ", open_interest::float8, volume::float8"
    sql = f"COPY (SELECT {cols} FROM market.price_daily"
    params = None
    if rics is not None:
        sql_rics = ",".join("'" + r.replace("'", "''") + "'" for r in rics)
        sql += f" WHERE ric IN ({sql_rics})"
    sql += ") TO STDOUT"
    raw = get_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            records = []
            with cur.copy(sql, params) as cp:
                cp.set_types(["date", "text", "float8"] + (["float8", "float8"] if with_oi else []))
                for row in cp.rows():
                    records.append(row)
        raw.commit()
    finally:
        raw.close()
    names = ["date", "ric", "price"] + (["oi", "volume"] if with_oi else [])
    df = pd.DataFrame.from_records(records, columns=names)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def write_series(series: dict[str, pd.DataFrame], scope: list[str]) -> tuple[int, int]:
    """Stage all series points, upsert changed rows, delete orphans among the `scope` keys (the series this writer
    owns — the LSEG pipeline and each Google Sheet source never delete each other's points).
    Returns (upserted, deleted)."""
    raw = get_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("CREATE TEMP TABLE tmp_points (series_key varchar(64), obs_date date, value float8, "
                        "value_local float8) ON COMMIT DROP")
            with cur.copy("COPY tmp_points FROM STDIN") as cp:
                for key, df in series.items():
                    vals = df["value"].to_numpy()
                    locs = df["value_local"].to_numpy() if "value_local" in df else [None] * len(df)
                    for d, v, lv in zip(df.index, vals, locs):
                        if v is None or (isinstance(v, float) and math.isnan(v)):
                            continue
                        lv = None if lv is None or (isinstance(lv, float) and math.isnan(lv)) else float(lv)
                        cp.write_row((key, pd.Timestamp(d).date(), float(v), lv))
            cur.execute("CREATE INDEX ON tmp_points (series_key, obs_date)")
            cur.execute("""
                INSERT INTO market.series_points AS sp (series_key, obs_date, value, value_local)
                SELECT t.series_key, t.obs_date, t.value, t.value_local FROM tmp_points t
                JOIN market.series s ON s.key = t.series_key
                ON CONFLICT (series_key, obs_date) DO UPDATE SET value = EXCLUDED.value,
                       value_local = EXCLUDED.value_local
                WHERE (sp.value, sp.value_local) IS DISTINCT FROM (EXCLUDED.value, EXCLUDED.value_local)""")
            upserted = cur.rowcount
            cur.execute("""
                DELETE FROM market.series_points sp WHERE sp.series_key = ANY(%(scope)s) AND NOT EXISTS (
                  SELECT 1 FROM tmp_points t WHERE t.series_key = sp.series_key AND t.obs_date = sp.obs_date)""",
                        {"scope": list(scope)})
            deleted = cur.rowcount
        raw.commit()
        return upserted, deleted
    finally:
        raw.close()


def write_oi(cum: pd.DataFrame, thresholds: pd.DataFrame) -> int:
    raw = get_engine().raw_connection()

    def n(v):
        return None if v is None or pd.isna(v) else v
    try:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM market.oi_cumulative")
            with cur.copy("COPY market.oi_cumulative (product_code, obs_date, total_oi, oi_chg, total_volume, "
                          "volume_chg, oi_vol_pct, avg_price, price_chg_pct, contracts_reporting) FROM STDIN") as cp:
                for r in cum.itertuples(index=False):
                    cp.write_row((r.product_code, pd.Timestamp(r.date).date(), int(r.total_oi),
                                  None if pd.isna(r.oi_chg) else int(r.oi_chg),
                                  None if pd.isna(r.total_volume) else int(r.total_volume),
                                  None if pd.isna(r.volume_chg) else int(r.volume_chg),
                                  n(r.oi_vol_pct), n(r.avg_price), n(r.price_chg_pct),
                                  int(r.contracts_reporting)))
            cur.execute("DELETE FROM market.oi_arrow_thresholds")
            for r in thresholds.itertuples(index=False):
                cur.execute("INSERT INTO market.oi_arrow_thresholds (product_code, median_pct, p75_pct, n_obs, "
                            "window_start) VALUES (%s, %s, %s, %s, %s)",
                            (r.product_code, r.median_pct, r.p75_pct, r.n_obs, r.window_start))
        raw.commit()
        return len(cum)
    finally:
        raw.close()
