"""Phase 8-a — 한국거래소(KIND) 신규상장기업현황을 받는다.

IPO 주관 실적의 모집단이다. 연 단위로 나눠 받아 하나로 합친다.
올해 분은 매번 새로 받는다 (상장이 계속 추가된다).

산출: data/verify/kind_listing_2022_{올해}.json  → step8 이 읽는 고정 이름으로도 저장
"""
import re
import time
from datetime import date

import requests

from dart import ROOT, UA, load_json, save_json

REF = "https://kind.krx.co.kr/listinvstg/listingcompany.do?method=searchListingTypeMain"
URL = "https://kind.krx.co.kr/listinvstg/listingcompany.do"
OUT = ROOT / "data" / "verify" / "kind_listing_2022_2026.json"


def fetch_year(s: requests.Session, y: int, until: str) -> list[dict]:
    data = [("method", "searchListingTypeSub"), ("currentPageSize", "3000"), ("pageIndex", "1"),
            ("orderMode", "1"), ("orderStat", "D"), ("repIsuSrtCd", ""), ("isurCd", ""),
            ("forward", "listingtype_sub"), ("listTypeArrStr", "01|02|03|04|05|"),
            ("choicTypeArrStr", "02|03|"), ("searchCodeType", ""), ("searchCorpName", ""),
            ("secuGrpArrStr", "0|ST|FS|MF|SC|RT|IF|DR|"), ("marketType", ""),
            ("searchCorpNameTmp", ""), ("country", ""), ("industry", ""),
            ("repMajAgntDesignAdvserComp", ""), ("repMajAgntComp", ""), ("designAdvserComp", ""),
            ("secuGrpArr", "0"), ("secuGrpArr", "ST|FS"), ("secuGrpArr", "MF|SC|RT|IF"),
            ("secuGrpArr", "DR"), ("listTypeArr", "01"), ("listTypeArr", "02"),
            ("listTypeArr", "03"), ("listTypeArr", "04"), ("listTypeArr", "05"),
            ("fromDate", f"{y}-01-01"), ("toDate", min(f"{y}-12-31", until)),
            ("choicTypeArr", "02"), ("choicTypeArr", "03")]
    r = s.post(URL, data=data, timeout=90,
               headers={"Referer": REF, "X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    t = r.content.decode("utf-8", "replace")
    body = re.search(r"<tbody>(.*?)</tbody>", t, re.S)
    total = re.search(r"전체\s*<em>\s*([\d,]+)", t)
    out = []
    for m in re.finditer(r"<tr([^>]*)>(.*?)</tr>", body.group(1) if body else "", re.S):
        attr, tr = m.groups()
        td = [" ".join(re.sub(r"<[^>]+>|&nbsp;", " ", c).split())
              for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(td) < 8:
            continue
        mk = re.search(r"alt='(코스닥|유가증권|코넥스)'", tr)
        cd = re.search(r"fnDetailView\('(\w+)','(\d+)'\)", attr)
        out.append({"name": td[0], "listing_date": td[1], "type": td[2], "secu": td[3],
                    "industry": td[4], "country": td[5], "sponsor": td[6], "price": td[7],
                    "amount_k": td[8] if len(td) > 8 else None,
                    "market": mk.group(1) if mk else None,
                    "code": (cd.group(1) + "0") if cd else None,
                    "biz_no": cd.group(2) if cd else None})
    want = int(total.group(1).replace(",", "")) if total else None
    if want is not None and want != len(out):
        raise RuntimeError(f"{y}년: 거래소 집계 {want}건, 읽은 행 {len(out)}건 — 서식이 바뀌었는지 확인")
    return out


def main() -> None:
    today = date.today()
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.get(REF, timeout=30)
    old = load_json(OUT) if OUT.exists() else []
    rows = []
    for y in range(2022, today.year + 1):
        keep = [r for r in old if r["listing_date"][:4] == str(y)]
        if keep and y < today.year:
            rows += keep                      # 지난 해는 바뀌지 않는다
            continue
        got = fetch_year(s, y, today.isoformat())
        print(f"  {y}년 {len(got)}건" + (f" (직전 {len(keep)}건)" if keep else ""))
        rows += got
        time.sleep(1)
    save_json(OUT, rows)
    new = {r["name"] for r in rows} - {r["name"] for r in old}
    print(f"KIND 신규상장기업현황 {len(rows)}건 · 새로 들어온 회사 {len(new)}곳 {sorted(new)}")


if __name__ == "__main__":
    main()
