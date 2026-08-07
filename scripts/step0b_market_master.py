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
    df = pd.read_html(io.BytesIO(r.content), encoding="euc-kr")[0]
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    return df[["회사명", "시장구분", "종목코드", "업종", "결산월"]]


def fetch_naver_caps() -> dict[str, dict]:
    """시장별 전 페이지 순회.

    시가총액(억원)뿐 아니라 현재가·상장주식수도 같이 받는다.
    담보주식 평가액, 지분 매각대금, 주식보상 행사가치를 '금액'으로 환산하려면
    이 둘이 필요하다. 지분율·주식수만으로는 영업에 쓸 수 없다.
    """
    caps: dict[str, dict] = {}
    for sosok, label in ((0, "KOSPI"), (1, "KOSDAQ")):
        page = 1
        while True:
            r = requests.get(NAVER_URL, params={"sosok": sosok, "page": page},
                             headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            html = r.content.decode("euc-kr", errors="replace")

            tables = [t for t in pd.read_html(io.StringIO(html)) if "종목명" in t.columns]
            if not tables:
                break
            table = tables[0].dropna(subset=["종목명"])
            if table.empty:
                break

            # 표에는 코드가 없다. 링크에서 순서대로 뽑아 짝지운다.
            codes = []
            for c in re.findall(r"code=(\d{6})", html):
                if not codes or codes[-1] != c:
                    codes.append(c)
            def col(name):
                return table[name].tolist() if name in table.columns else [None] * len(table)

            caps_col = col("시가총액")
            price_col = col("현재가")
            shares_col = col("상장주식수")
            for i in range(len(table)):
                if i >= len(codes):
                    break
                caps[codes[i]] = {
                    "market_cap": float(caps_col[i]) if pd.notna(caps_col[i]) else None,
                    "price": float(price_col[i]) if pd.notna(price_col[i]) else None,
                    "shares": float(shares_col[i]) if pd.notna(shares_col[i]) else None,
                }

            print(f"  {label} {page}p — 누적 {len(caps):,}종목", flush=True)
            if len(table) < 50:
                break
            page += 1
            time.sleep(PAUSE)
    return caps


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
    print(f"\ndata/market_master.json — {len(master):,}개사 "
          f"(시총 매칭 {matched:,}개사)")


if __name__ == "__main__":
    main()
