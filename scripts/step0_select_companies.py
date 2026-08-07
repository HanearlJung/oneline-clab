"""Phase 0 — 대상 100개사 선정 + 각사 최신 정기보고서 rcp_no 확보.

요청 수를 최소로 잡는다. 전 IP 차단은 필요도 없는 전수 훑기 때문이었다.

  - 정기보고서 검색 결과에 이미 회사별 rcp_no 가 들어 있다.
    따라서 '회사별 검색'을 100번 반복할 필요가 없다. step1 은 여기서 나온
    rcp_no 를 그대로 쓴다.
  - 이벤트 공시는 보고서명 표적 검색으로만 받는다. 유형 전체를 훑지 않는다.

예상 요청 수: 정기보고서 약 80p + 이벤트 약 30p = 110회 내외.

산출: data/companies.json
"""
import argparse
import re
from collections import defaultdict
from datetime import date, timedelta

import dart
from dart import ROOT

LISTED = {"KOSPI", "KOSDAQ"}
PERIODIC = [dart.PT_ANNUAL, dart.PT_HALF, dart.PT_QUARTER]
PERIODIC_RE = re.compile(r"^(사업보고서|반기보고서|분기보고서)")

# 이벤트 커버리지. 전부 보고서명 표적 검색 — 유형 전수 훑기는 하지 않는다.
#
# 주의: DART 의 reportName 은 '완전일치'다. 부분 문자열("자기주식")로는 0건이 나온다.
#       반드시 공시 화면에 뜨는 보고서명 전체를 그대로 적어야 한다.
#       (정정 접두사 "[기재정정] " 는 알아서 포함되어 검색된다)
EVENT_QUERIES = [
    dict(key="treasury", public_type=dart.PT_MAJOR,
         report_name="주요사항보고서(자기주식처분결정)",
         pattern=re.compile(r"자기주식처분결정")),
    dict(key="treasury", public_type=dart.PT_MAJOR,
         report_name="주요사항보고서(자기주식취득결정)",
         pattern=re.compile(r"자기주식취득결정")),
    dict(key="owner_change", public_type=dart.PT_EXCHANGE,
         report_name="최대주주변경",
         pattern=re.compile(r"최대주주변경")),
    dict(key="owner_change", public_type=dart.PT_EXCHANGE,
         report_name="최대주주변경을수반하는주식양수도계약체결",
         pattern=re.compile(r"최대주주변경을수반하는주식양수도계약체결")),
    dict(key="pledge", public_type=dart.PT_EXCHANGE,
         report_name="최대주주변경을수반하는주식담보제공계약체결",
         pattern=re.compile(r"주식담보제공계약체결")),
    dict(key="pledge", public_type=dart.PT_MAJORSTOCK,
         report_name="주식등의대량보유상황보고서(일반)",
         pattern=re.compile(r"대량보유상황보고서")),
]
EVENT_KEYS = sorted({q["key"] for q in EVENT_QUERIES})


# 보고서 우선순위: 사업 > 반기 > 분기. 같은 종류면 최신 접수분.
REPORT_RANK = {"사업보고서": 3, "반기보고서": 2, "분기보고서": 1}


def _rank(row: dict) -> tuple:
    nm = row["report_nm"].replace("[기재정정] ", "").replace("[첨부정정] ", "")
    kind = next((k for k in REPORT_RANK if nm.startswith(k)), None)
    return (REPORT_RANK.get(kind, 0), row["rcept_dt"])


def progress(label):
    def fn(page, total, n):
        if page % 10 == 0 or page == total:
            print(f"      [{label}] {page}/{total}p  누적 {n:,}건", flush=True)
    return fn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--periodic-months", type=int, default=15,
                    help="정기보고서 조회 범위. 사업보고서 1회가 반드시 포함되도록")
    ap.add_argument("--event-months", type=int, default=12)
    ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args()

    today = date.today()
    p_start = (today - timedelta(days=args.periodic_months * 31)).strftime("%Y%m%d")
    e_start = (today - timedelta(days=args.event_months * 31)).strftime("%Y%m%d")
    end_s = today.strftime("%Y%m%d")

    # ---------------------------------------------- 1) 정기보고서 = 상장사 유니버스
    print(f"[1/3] 정기보고서 {p_start}~{end_s}")
    latest: dict[str, dict] = {}   # corp_code -> 최신 정기보고서 행
    for pt in PERIODIC:
        rows = dart.search_all(pt, p_start, end_s, on_page=progress(pt),
                               cache_key=f"{pt}_{p_start}_{end_s}")
        print(f"      {pt} {len(rows):,}건")
        for r in rows:
            cc = r["corp_code"]
            if not cc or r["market"] not in LISTED:
                continue
            if not PERIODIC_RE.match(r["report_nm"].replace("[기재정정] ", "")
                                     .replace("[첨부정정] ", "")):
                continue
            # '가장 최근'이 아니라 '사업보고서 우선'이다.
            # 분기·반기보고서는 주석이 축약돼 영업에 필요한 항목이 빠진다.
            # (예: 사외적립자산 운용 구성·사용자 기여금은 사업보고서에만 있다)
            prev = latest.get(cc)
            if prev is None or _rank(r) > _rank(prev):
                latest[cc] = r
    annual = sum(1 for r in latest.values() if r["report_nm"].startswith("사업보고서"))
    print(f"      상장사 {len(latest):,}개사 · 사업보고서 {annual:,}개사 "
          f"/ 반기·분기 {len(latest) - annual:,}개사")

    # ------------------------------------------------------ 2) 이벤트 커버리지
    print(f"[2/3] 이벤트 공시 {e_start}~{end_s} (보고서명 표적 검색)")
    events: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for q in EVENT_QUERIES:
        label = f"{q['public_type']}/{q['report_name']}"
        rows = dart.search_all(
            q["public_type"], e_start, end_s, on_page=progress(label),
            report_name=q["report_name"],
            cache_key=f"{q['public_type']}_{q['report_name']}_{e_start}_{end_s}")
        hit = 0
        for r in rows:
            cc = r["corp_code"]
            if cc not in latest:
                continue
            counts[cc] += 1
            if q["pattern"].search(r["report_nm"]):
                events[q["key"]].add(cc)
                hit += 1
        print(f"      {label}: {len(rows):,}건 수신 · 상장사 일치 {hit:,}건")

    # ------------------------------------------------------------ 3) 선정
    print("[3/3] 선정")
    master_file = ROOT / "data" / "market_master.json"
    if not master_file.exists():
        raise SystemExit("data/market_master.json 이 없습니다. "
                         "step0b_market_master.py 를 먼저 실행하세요.")
    master = dart.load_json(master_file)

    scored, unmatched = [], 0
    for cc, rpt in latest.items():
        flags = {k: (cc in events[k]) for k in EVENT_KEYS}
        m = master.get(rpt["corp_name"])
        if m is None:
            unmatched += 1
        scored.append({
            "corp_code": cc,
            "name": rpt["corp_name"],
            "market": rpt["market"],
            "stock_code": (m or {}).get("stock_code"),
            "industry": (m or {}).get("industry"),
            "market_cap": (m or {}).get("market_cap"),   # 억원
            "report": {"rcp_no": rpt["rcp_no"], "report_nm": rpt["report_nm"],
                       "rcept_dt": rpt["rcept_dt"]},
            "events": flags,
            "event_count": sum(flags.values()),
            "disclosure_count": counts[cc],
        })
    print(f"      상장사 마스터 미매칭 {unmatched:,}개사 (시총·업종 없음)")

    # 이벤트 보유 종류 우선(화면 6탭이 고르게 채워짐), 동수면 시가총액 순.
    # 공시 건수는 규모 프록시로 쓰지 않는다 — 자본조달이 잦은 한계기업이 올라온다.
    scored.sort(key=lambda x: (-x["event_count"], -(x["market_cap"] or 0), x["name"]))
    selected = scored[: args.top]

    print(f"\n유니버스 {len(scored):,}개사 → 선정 {len(selected)}개사")
    for key in EVENT_KEYS:
        n = sum(1 for c in selected if c["events"][key])
        print(f"  {key:14s} {n:3d}/{len(selected)}개사")
    for mk in sorted(LISTED):
        print(f"  {mk:14s} {sum(1 for c in selected if c['market'] == mk):3d}개사")

    dart.save_json(ROOT / "data" / "companies.json", {
        "meta": {
            "generated": today.isoformat(),
            "periodic_window": {"start": p_start, "end": end_s},
            "event_window": {"start": e_start, "end": end_s},
            "universe": len(scored),
            "selected": len(selected),
        },
        "companies": selected,
    })
    print("\ndata/companies.json 저장")


if __name__ == "__main__":
    main()
