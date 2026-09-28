"""상장사 마스터의 시세를 사내 DB(gold.fct_marketcap) 최신 거래일로 갱신.

업종·결산월은 KIND 에서 받은 기존 값을 유지한다. DB 에만 있는 신규 상장사는
업종 없이 추가한다. 단위는 기존 마스터와 맞춘다 (시총 억원, 주식수 천주).

산출: data/market_master.json (+ meta 는 data/market_master_meta.json)
"""
import os

import psycopg2

from dart import ROOT, load_json, save_json

MARKET = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KONEX": "KONEX",
          "유가": "KOSPI", "유가증권": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}


def main() -> None:
    master = load_json(ROOT / "data" / "market_master.json")
    by_code = {v["stock_code"]: k for k, v in master.items()}
    with psycopg2.connect(os.environ["DUNAMIS_DSN"]) as conn, conn.cursor() as cur:
        conn.set_session(readonly=True)
        cur.execute("""
            select distinct on (isu_srt_cd) isu_srt_cd, company_name, trade_date,
                   market_cap, close_price, list_shares, market_type
            from gold.fct_marketcap
            where trade_date >= (select max(trade_date) from gold.fct_marketcap) - 7
            order by isu_srt_cd, trade_date desc""")
        rows = cur.fetchall()
        cur.execute("""
            select company_name, stock_code, listing_expected_date, confirmed_offering_price,
                   shares_after_offering
            from silver.vw_ipo_info
            where listing_expected_date >= current_date - 120
              and stock_code ~ '^[0-9A-Z]{6}$'""")
        ipos = cur.fetchall()
    upd = new = 0
    base = max(r[2] for r in rows)
    for code, name, _dt, cap, px, shares, mkt in rows:
        code = str(code).zfill(6)
        key = by_code.get(code)
        if key is None:
            key = name if name not in master else f"{name}({code})"
            master[key] = {"stock_code": code, "industry": None, "fiscal_month": None}
            by_code[code] = key
            new += 1
        else:
            upd += 1
        m = master[key]
        m["market"] = MARKET.get(mkt, m.get("market") or mkt)
        m["market_cap"] = round(cap / 1e8, 1) if cap else None
        m["price"] = float(px) if px else None
        m["shares"] = round(shares / 1e3, 1) if shares else None
    # 최근 상장·상장 예정 종목. 시세 테이블에 아직 없으면 공모가로 평가한다(근거를 남긴다).
    kind_path = ROOT / "data" / "cache_uw" / "_kind_ipo.json"
    kind = load_json(kind_path) if kind_path.exists() else {}
    ipo_added = 0
    for name, code, listed_on, offer, shares in ipos:
        if code in by_code:
            continue
        k = kind.get(name) or {}
        if not k.get("market"):
            continue                    # 시장을 확인하지 못한 종목은 넣지 않는다
        master[name] = {
            "stock_code": code, "market": k["market"], "industry": None, "fiscal_month": None,
            "market_cap": round(offer * shares / 1e8, 1) if offer and shares else None,
            "price": float(offer) if offer else None,
            "shares": round(shares / 1e3, 1) if shares else None,
            "price_basis": "공모가", "listing_date": listed_on.isoformat(),
        }
        by_code[code] = name
        ipo_added += 1
    for v in master.values():
        v["market"] = MARKET.get(v.get("market"), v.get("market"))
    save_json(ROOT / "data" / "market_master.json", master)
    save_json(ROOT / "data" / "market_master_meta.json",
              {"price_date": base.isoformat(), "updated": upd, "added": new,
               "ipo_added": ipo_added})
    listed = sum(1 for v in master.values() if v["market"] in ("KOSPI", "KOSDAQ"))
    priced = sum(1 for v in master.values() if v["market"] in ("KOSPI", "KOSDAQ") and v.get("price"))
    print(f"시세 기준일 {base} · 갱신 {upd:,} · 신규 {new:,} · 최근 상장(공모가 평가) {ipo_added}")
    print(f"KOSPI+KOSDAQ {listed:,}개사 중 시세 보유 {priced:,}개사")


if __name__ == "__main__":
    main()
