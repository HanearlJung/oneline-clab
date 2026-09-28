"""상장사 마스터 — 종목코드·업종·시가총액.

DART 공시에는 업종도 시가총액도 없다. 화면 필터(업종)와 기업 선정(규모)에
둘 다 필요하므로 공개 소스에서 따로 모은다.

  - KIND 상장법인목록 : 회사명, 종목코드, 시장, 업종, 결산월
  - 네이버 금융       : 시가총액

'공시 건수'를 규모 프록시로 쓰면 안 된다. 공시가 잦은 건 규모가 아니라
자본조달·지분변동이 빈번하다는 뜻이라 한계기업이 상위로 올라온다.

산출: data/market_master.json
"""
import io
import re
import time

import pandas as pd
import requests

from dart import ROOT, save_json

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
NAVER_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
PAUSE = 0.8


def fetch_kind() -> pd.DataFrame:
    r = requests.get(KIND_URL, headers={"User-Agent": UA}, timeout=90)
    r.raise_for_status()
    df = pd.read_html(io.BytesIO(r.content), encoding="euc-kr", flavor="lxml")[0]
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    return df[["회사명", "시장구분", "종목코드", "업종", "결산월"]]


NAVER_API = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
_traded = []          # 시세 시각 — 기준일을 정하는 데 쓴다


def _n(v):
    v = str(v or "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def fetch_naver_caps() -> dict[str, dict]:
    """시장별 전 종목의 종가·시가총액.

    담보주식 평가액, 보호예수 평가금액을 '금액'으로 환산하려면 종가가 필요하다.
    PC 시세 페이지는 자동 조회를 막는다. 모바일 시세 API 를 쓴다.
    """
    caps: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        page = 1
        while True:
            r = requests.get(NAVER_API.format(market=market), params={"page": page, "pageSize": 100},
                             headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            j = r.json()
            stocks = j.get("stocks") or []
            for x in stocks:
                price = _n(x.get("closePrice"))
                cap = _n(x.get("marketValue"))              # 억원
                caps[x["itemCode"]] = {
                    "market_cap": cap, "price": price,
                    "shares": round(cap * 1e8 / price / 1e3, 1) if cap and price else None,   # 천주
                }
                if x.get("localTradedAt"):
                    _traded.append(x["localTradedAt"][:10])
            print(f"  {market} {page}p — 누적 {len(caps):,}종목", flush=True)
            if len(stocks) < 100 or page * 100 >= (j.get("totalCount") or 0):
                break
            page += 1
            time.sleep(PAUSE)
    return caps


def price_date() -> str | None:
    """시세 기준일 — 받은 시세의 체결일 중 가장 많은 날짜 (거래정지 종목의 옛 날짜는 버린다)."""
    from collections import Counter
    return Counter(_traded).most_common(1)[0][0] if _traded else None


def _month(value) -> int | None:
    if pd.isna(value):
        return None
    m = re.search(r"\d{1,2}", str(value))
    return int(m.group()) if m else None


def main() -> None:
    print("[1/2] KIND 상장법인목록")
    kind = fetch_kind()
    print(f"      {len(kind):,}개사")

    print("[2/2] 네이버 금융 시가총액")
    caps = fetch_naver_caps()
    print(f"      {len(caps):,}종목")

    master = {}
    for _, row in kind.iterrows():
        code = row["종목코드"]
        q = caps.get(code) or {}
        master[row["회사명"]] = {
            "stock_code": code,
            "market": {"유가증권": "KOSPI", "코스닥": "KOSDAQ",
                       "코넥스": "KONEX"}.get(row["시장구분"], row["시장구분"]),
            "industry": row["업종"],
            "fiscal_month": _month(row["결산월"]),   # "12월" 형태로 온다
            "market_cap": q.get("market_cap"),   # 억원
            "price": q.get("price"),             # 원
            "shares": q.get("shares"),           # 천주 (네이버 표기 단위)
        }

    matched = sum(1 for v in master.values() if v["market_cap"])
    save_json(ROOT / "data" / "market_master.json", master)
    save_json(ROOT / "data" / "market_master_meta.json",
              {"price_date": price_date(), "price_src": "네이버 금융", "companies": len(master)})
    print(f"\ndata/market_master.json — {len(master):,}개사 "
          f"(시총 매칭 {matched:,}개사)")


if __name__ == "__main__":
    main()
