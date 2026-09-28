"""Phase 6-c — 한국예탁결제원(SEIBro) 의무보유 등록·반환 내역.

증권신고서에는 신규상장(IPO) 의무보유만 있다. 제3자배정 유상증자·합병 등으로 생긴
의무보유는 예탁결제원에 등록된 내역으로만 전 시장을 볼 수 있다.

  - 등록 내역(DT_TYPE=0): 예탁일·사유·수량. 아직 반환되지 않은 건에는 반환일이 없다.
  - 반환 내역(DT_TYPE=1): 예탁일과 반환일이 함께 있다. 사유별 실제 보유 기간을 여기서 잰다.

산출: data/seibro_deposit.json, data/seibro_return.json
"""
import argparse
import re
import time
from datetime import date

import requests

from dart import ROOT, UA, load_json, save_json

URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
REF = ("https://seibro.or.kr/websquare/control.jsp"
       "?w2xPath=/IPORTAL/user/company/BIP_CNTS01045V.xml&menuNo=284")
TASK = "ksd.safe.bip.cnts.Company.process.DutySafedpAdepoPTask"
PAUSE = 0.6
PAGE = 100

_s = requests.Session()
_s.headers.update({"User-Agent": UA})


def call(action: str, dt_type: str, d1: str, d2: str, sp: int = 1, ep: int = PAGE) -> str:
    a = ("FIRST_SAFEDP_DT1", "FIRST_SAFEDP_DT2") if dt_type == "0" else ("RETURN_DT1", "RETURN_DT2")
    body = (
        f'<reqParam action="{action}" task="{TASK}"><MENU_NO value="284"/>'
        f'<W2XPATH value="/IPORTAL/user/company/BIP_CNTS01045V.xml"/>'
        f'<ISSUCO_CUSTNO value=""/><CALTOT_MART_TPCD value=""/>'
        f'<DT_TYPE value="{dt_type}"/><ISSU_TYPE value=""/>'
        f'<DT_TYPE_0 value="{"1" if dt_type == "0" else ""}"/>'
        f'<DT_TYPE_1 value="{"1" if dt_type == "1" else ""}"/>'
        f'<{a[0]} value="{d1}"/><{a[1]} value="{d2}"/>'
        f'<START_PAGE value="{sp}"/><END_PAGE value="{ep}"/></reqParam>')
    for attempt in range(4):
        try:
            r = _s.post(URL, data=body.encode("utf-8"), timeout=60, headers={
                "Content-Type": 'application/xml; charset="UTF-8"', "Referer": REF,
                "submissionid": "submission_" + action})
            r.raise_for_status()
            time.sleep(PAUSE)
            return r.text
        except Exception as exc:  # noqa: BLE001
            print(f"    · 재시도 {attempt + 1}: {exc}", flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("SEIBro 요청 실패")


def parse(xml: str) -> list[dict]:
    out = []
    for block in re.findall(r"<result>(.*?)</result>", xml, re.S):
        out.append({k: v for k, v in re.findall(r'<(\w+) value="([^"]*)"', block)})
    return out


def months(start: str, end: str):
    y, m = int(start[:4]), int(start[4:6])
    while f"{y}{m:02d}" <= end[:6]:
        last = 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30 if m != 2 else (29 if y % 4 == 0 else 28)
        yield max(start, f"{y}{m:02d}01"), min(end, f"{y}{m:02d}{last}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def collect(dt_type: str, start: str, end: str, tag: str) -> list[dict]:
    cache = ROOT / "data" / "cache_seibro"
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for d1, d2 in months(start, end):
        f = cache / f"{tag}_{d1}_{d2}.json"
        # 이번 달은 계속 늘어난다. 캐시하지 않는다.
        if f.exists() and d2 < date.today().strftime("%Y%m%d"):
            rows.extend(load_json(f))
            continue
        cnt = re.search(r'LIST_CNT value="(\d+)"', call("dutySafeBuySkedulListCntEL1", dt_type, d1, d2))
        total = int(cnt.group(1)) if cnt else 0
        got, sp = [], 1
        while sp <= total:
            got.extend(parse(call("dutySafeBuySkedulPListEL1", dt_type, d1, d2, sp, sp + PAGE - 1)))
            sp += PAGE
        save_json(f, got)
        rows.extend(got)
        print(f"  {tag} {d1[:6]} — {len(got)}/{total}건", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20210901")
    ap.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    args = ap.parse_args()
    _s.get(REF, timeout=30)
    dep = collect("0", args.start, args.end, "deposit")
    save_json(ROOT / "data" / "seibro_deposit.json", dep)
    ret = collect("1", args.start, args.end, "return")
    save_json(ROOT / "data" / "seibro_return.json", ret)
    print(f"등록 {len(dep):,}건 · 반환 {len(ret):,}건")


if __name__ == "__main__":
    main()
