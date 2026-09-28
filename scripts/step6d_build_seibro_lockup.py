"""Phase 6-d — 예탁결제원 의무보유 내역을 보호예수 행으로 만든다.  [보류 — 대시보드에 쓰지 않음]

2026-09-28 검증 결과 이 산출물은 화면에 올리지 않기로 했다.
  - 등록 내역과 반환 내역의 예탁일·수량이 서로 맞지 않는다 (같은 건인데 예탁일이 며칠
    어긋나거나 수량이 다르다). 그래서 '아직 묶여 있는 잔량'을 믿을 수 있게 계산할 수 없다.
  - 해제일도 사유로 정해지지 않는다. 5년치 반환 6,433건을 재 보니 한 기간에 85% 이상
    몰리는 사유는 '상장주선인(국내기업)' 하나뿐이다. 모집(전매제한)도 12개월이 72%다.
step5 는 data/lockup_seibro.json 이 있을 때만 합친다. 이 스크립트를 돌리면 그 파일이
생기므로, 위 두 문제를 풀기 전에는 돌린 뒤 파일을 지울 것.

예탁결제원 자료에는 '언제 풀리는지'가 없다. 예탁일·사유·수량과, 반환된 건의 반환일만 있다.
그래서 해제일을 이렇게 정한다.

  1) 지난 5년 반환 내역에서 사유별로 예탁일→반환일 간격을 잰다.
  2) 한 사유의 반환이 한 기간(예: 12개월)에 몰려 있으면 그 기간을 그 사유의 의무보유
     기간으로 쓴다. 몰려 있지 않으면(사유 하나에 1·3·6개월이 섞임) 해제일을 만들지 않는다.
  3) 아직 반환되지 않은 잔량 = 예탁 수량 − 같은 예탁 건의 반환 수량 합.

해제일은 공시 기재값이 아니라 계산값이므로 release_src 에 '실측기간'으로 남긴다.
신규상장 건은 증권신고서 자료(step6b)가 주주 단위로 더 자세하다. 그 종목의 상장 관련
사유는 여기서 넣지 않는다 (같은 물량을 두 번 세게 된다).

산출: data/lockup_seibro.json
"""
from collections import Counter, defaultdict
from datetime import date

from dart import ROOT, load_json, save_json

MARKET = {"유가증권시장": "KOSPI", "코스닥시장": "KOSDAQ"}
# 신규상장 때 생기는 의무보유. 증권신고서 자료가 있는 종목에서는 건너뛴다.
IPO_REASON = ("최대주주(코스닥)", "최대주주(상장)", "최대주주(기술성장기업)", "벤처금융", "기관투자가",
              "상장주선인", "기타 보호예수 필요 주주", "SPAC", "우리사주", "전문투자자", "주식매수선택권")
MIN_N, MIN_SHARE = 30, 0.85


def d(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def add_months(dt: date, n: int) -> date:
    y, m = dt.year + (dt.month - 1 + n) // 12, (dt.month - 1 + n) % 12 + 1
    day = min(dt.day, 28 if m == 2 else 30 if m in (4, 6, 9, 11) else 31)
    return date(y, m, day)


def main() -> None:
    dep = load_json(ROOT / "data" / "seibro_deposit.json")
    ret = load_json(ROOT / "data" / "seibro_return.json")
    master = load_json(ROOT / "data" / "market_master.json")
    by_code = {v["stock_code"]: {**v, "name": k} for k, v in master.items()}
    meta = load_json(ROOT / "data" / "market_master_meta.json")
    ipo = load_json(ROOT / "data" / "lockup.json")
    ipo_codes = {x["stock_code"] for x in ipo}

    # 1) 사유별 실제 보유 기간
    gaps = defaultdict(Counter)
    for r in ret:
        if r.get("FIRST_SAFEDP_DT") and r.get("RETURN_DT"):
            days = (d(r["RETURN_DT"]) - d(r["FIRST_SAFEDP_DT"])).days
            gaps[r["DUTY_SAFEDP_RACD"]][round(days / 30.44)] += 1
    period = {}
    print("사유별 보유 기간 (개월: 건수)")
    for reason, c in sorted(gaps.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        mo, top = c.most_common(1)[0]
        ok = n >= MIN_N and top / n >= MIN_SHARE and mo > 0
        if ok:
            period[reason] = mo
        print(f"  {'O' if ok else 'X'} {reason:<28s} n={n:<5d} 최빈 {mo}개월 {top / n:5.1%}  {dict(c.most_common(4))}")

    # 2) 미반환 잔량
    returned = Counter()
    for r in ret:
        k = (r["SHOTN_ISIN"], r.get("FIRST_SAFEDP_DT"), r["DUTY_SAFEDP_RACD"])
        returned[k] += int(r.get("RETURN_QTY") or 0)
    deposited = Counter()
    info = {}
    for x in dep:
        k = (x["SHOTN_ISIN"], x["FIRST_SAFEDP_DT"], x["DUTY_SAFEDP_RACD"])
        deposited[k] += int(x.get("ORG_NRETURN_QTY") or 0)
        info[k] = x

    today = date.today()
    out, stat = [], Counter()
    for k, qty in deposited.items():
        code, first, reason = k
        x = info[k]
        left = qty - returned.get(k, 0)
        if left <= 0:
            stat["반환 완료"] += 1
            continue
        if MARKET.get(x["CALTOT_MART_TPCD"]) is None or x["SECN_KACD"] != "보통주":
            stat["비상장·우선주 등"] += 1
            continue
        m = by_code.get(code)
        if not m or m.get("market") not in ("KOSPI", "KOSDAQ"):
            stat["상장사 마스터에 없음"] += 1
            continue
        if code in ipo_codes and reason.startswith(IPO_REASON):
            stat["증권신고서 자료와 중복"] += 1
            continue
        mo = period.get(reason)
        rel = add_months(d(first), mo) if mo else None
        if rel and rel < today:
            # 계산한 해제일이 지났는데 반환 기록이 없다. 기간이 다른 건이므로 해제일을 비운다.
            rel, mo = None, None
            stat["기간 불일치(해제일 비움)"] += 1
        price = m.get("price")
        out.append({
            "corp_name": m["name"], "stock_code": code, "corp_code": None,
            "industry": m.get("industry"), "market_cap": m.get("market_cap"),
            "listing_date": None,
            "holder_name": reason, "holder_type": "기타", "holder_rel": reason,
            "lockup_qty": left,
            "lockup_value": int(left * price) if price else None,
            "value_base_date": meta["price_date"] if price else None,
            "value_basis": "종가" if price else None,
            "deposit_date": d(first).isoformat(),
            "release_date": rel.isoformat() if rel else None,
            "release_src": "실측기간" if rel else None,
            "lockup_period": f"예탁일로부터 {mo}개월" if mo else None,
            "custody_broker": None, "custody_broker_src": "미상",
            "lead_manager": None, "co_manager": None, "underwriter": None, "manager_src": None,
            "rcept_no": None, "source_section": "한국예탁결제원 의무보유 등록·반환",
            "source_url": "https://seibro.or.kr/websquare/control.jsp"
                          "?w2xPath=/IPORTAL/user/company/BIP_CNTS01045V.xml&menuNo=284",
            "source": "KSD", "is_mock": False,
        })
        stat["채택"] += 1
    save_json(ROOT / "data" / "lockup_seibro.json", out)
    print(f"\n예탁 {len(deposited):,}건 → {dict(stat)}")
    print(f"lockup_seibro.json — {len(out):,}행 · {len({x['stock_code'] for x in out}):,}종목 "
          f"· 해제일 확정 {sum(1 for x in out if x['release_date']):,}행")
    print("  사유 —", Counter(x["holder_name"] for x in out).most_common(8))


if __name__ == "__main__":
    main()
