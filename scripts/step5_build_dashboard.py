"""Phase 5 — 락업·담보대출 대시보드 데이터 생성.

8/13 현업 미팅으로 스코프가 2종(보호예수·주식담보대출)으로 압축됐다.
화면은 임원용/실무용으로 나눈다. 이 스크립트는 두 화면이 공유하는 데이터를 만든다.

산출: data/dashboard.json
"""
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 대출기관명 정규화 — 안 하면 M/S 가 틀린다.
# 실측: '한국증권금융' 이 4가지 표기로 갈라져 137건으로 잡혔으나 실제 180건.
BRANCH_RE = re.compile(r"\s*(본점|본사|[가-힣]+지점|[가-힣]+지사|[가-힣]+센터)\s*$")
CORP_RE = re.compile(r"주식회사|㈜|\(주\)|\(유\)|\(재\)")

BANK_KW = ("은행", "농협", "수협", "신협", "새마을금고", "저축은행")
INSUR_KW = ("생명", "화재", "해상", "보험")
SEC_KW = ("증권", "투자증권", "금융투자")


SPLIT_RE = re.compile(r"\s*[,/·]\s*|\s+및\s+")


def split_lenders(s):
    """한 셀에 복수 기관이 들어간 건이 있다(공동 담보).
    건수는 각 기관에 세고, 금액은 균등 분할해 중복 계상을 막는다."""
    if not s:
        return []
    parts = [norm_lender(x) for x in SPLIT_RE.split(s)]
    return [x for x in parts if x]


def norm_lender(s):
    if not s:
        return None
    s = CORP_RE.sub("", s).strip()
    s = BRANCH_RE.sub("", s).strip()
    s = re.sub(r"\s+", "", s)
    return s or None


def lender_type(s):
    if not s:
        return None
    if "한국증권금융" in s:
        return "증권금융"
    if any(k in s for k in BANK_KW):
        return "은행"
    if any(k in s for k in INSUR_KW):
        return "보험"
    if any(k in s for k in SEC_KW):
        return "증권"
    return "기타"


def to_won(v, unit):
    if v is None:
        return None
    f = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}.get(unit or "원")
    return int(v * f) if f else None


def main():
    base = date.today()
    base_s = base.isoformat()
    master = json.loads((ROOT / "data" / "market_master.json").read_text(encoding="utf-8"))
    recs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((ROOT / "extracted").glob("*.json"))]

    loans, seen = [], set()
    for r in recs:
        m = master.get(r["name"], {})
        price = m.get("price")
        for p in (r.get("pledge") or []):
            names = split_lenders(p.get("counterparty"))
            lender = names[0] if names else None
            amount = to_won(p.get("loan_amount"), p.get("unit"))
            shares = p.get("shares")
            collateral = int(shares * price) if (shares and price) else None
            maturity = p.get("maturity")
            # 같은 계약이 여러 보고서에 반복 기재된다(정정·후속 보고).
            # rcept_no 를 키에 넣으면 같은 계약이 여러 행으로 남아 화면에 중복이 보인다.
            # 계약 실체로 키를 잡고 최신 접수분만 남긴다.
            key = (r["name"], p.get("holder"), lender, p.get("contract_date"),
                   maturity, amount, shares)
            loans.append({
                "_key": key,
                "corp_name": r["name"],
                "stock_code": m.get("stock_code") or r.get("stock_code"),
                "corp_code": r["corp_code"],
                "industry": m.get("industry"),
                "market_cap": m.get("market_cap"),
                "borrower_name": p.get("holder"),
                "borrower_type": p.get("relation"),
                "lender": lender,
                "lenders": names,
                "lender_type": lender_type(lender),
                "contract_type": p.get("contract_type"),
                "pledged_qty": shares,
                "pledged_ratio": p.get("ratio"),
                "loan_amount": amount,
                "collateral_amount": collateral,
                "contract_date": p.get("contract_date"),
                "maturity_date": maturity,
                "is_active": bool(maturity and maturity >= base_s),
                "rcept_no": p.get("rcp_no"),
                "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={p.get('rcp_no')}",
            })

    # 중복 제거 — 계약 실체가 같으면 최신 접수분만 남긴다
    dedup = {}
    for x in loans:
        k = x.pop("_key")
        cur = dedup.get(k)
        if cur is None or (x["rcept_no"] or "") > (cur["rcept_no"] or ""):
            dedup[k] = x
    removed = len(loans) - len(dedup)
    loans = list(dedup.values())

    # ---- ms_loan. 미상은 분모에서 빼고 별도 표기한다(현업 명시 요구)
    def build_ms(rows):
        agg = defaultdict(lambda: {"cnt": 0, "balance": 0, "collateral": 0})
        unknown = {"cnt": 0, "balance": 0}
        multi = 0
        for x in rows:
            names = x["lenders"] or []
            if not names:
                unknown["cnt"] += 1
                unknown["balance"] += x["loan_amount"] or 0
                continue
            if len(names) > 1:
                multi += 1
            share = 1 / len(names)
            for nm in names:
                a = agg[nm]
                a["cnt"] += 1
                a["balance"] += (x["loan_amount"] or 0) * share
                a["collateral"] += (x["collateral_amount"] or 0) * share
        tot_c = sum(a["cnt"] for a in agg.values()) or 1
        tot_b = sum(a["balance"] for a in agg.values()) or 1
        tot_l = sum(a["collateral"] for a in agg.values()) or 1
        out = [{"lender": k, "lender_type": lender_type(k),
                "cnt": v["cnt"], "balance": int(v["balance"]),
                "collateral": int(v["collateral"]),
                "cnt_ms": round(v["cnt"] / tot_c * 100, 1),
                "balance_ms": round(v["balance"] / tot_b * 100, 1),
                "collateral_ms": round(v["collateral"] / tot_l * 100, 1)}
               for k, v in agg.items()]
        out.sort(key=lambda x: -x["cnt"])
        return {"rows": out, "unknown": unknown, "multi_lender_cnt": multi,
                "total": {"cnt": tot_c, "balance": tot_b, "collateral": tot_l}}

    active = [x for x in loans if x["is_active"]]

    def within(days):
        rows = [x for x in active
                if (datetime.fromisoformat(x["maturity_date"]).date() - base).days <= days]
        return {"cnt": len(rows), "amount": sum(x["loan_amount"] or 0 for x in rows)}

    callable_co = len({x["corp_name"] for x in active})
    unknown_mat = [x for x in loans if not x["maturity_date"]]

    # ---- 보호예수. 실데이터 연결 전까지 mock 을 쓴다(화면에 명시)
    lk_path = ROOT / "data" / "lockup_mock.json"
    lockups = json.loads(lk_path.read_text(encoding="utf-8")) if lk_path.exists() else []
    is_mock = bool(lockups) and lockups[0].get("is_mock")
    for x in lockups:
        x["is_active"] = x["release_date"] >= base_s

    def lk_within(days):
        rows = [x for x in lockups if x["is_active"]
                and (datetime.fromisoformat(x["release_date"]).date() - base).days <= days]
        return {"cnt": len(rows), "amount": sum(x["lockup_value"] or 0 for x in rows)}

    def build_ms_lockup(rows, include_est=True):
        agg = defaultdict(lambda: {"cnt": 0, "amt": 0, "est": 0})
        unknown = {"cnt": 0, "amt": 0}
        for x in rows:
            b, src = x.get("custody_broker"), x.get("custody_broker_src")
            if not b or (src == "추정" and not include_est):
                unknown["cnt"] += 1
                unknown["amt"] += x["lockup_value"] or 0
                continue
            a = agg[b]
            a["cnt"] += 1
            a["amt"] += x["lockup_value"] or 0
            if src == "추정":
                a["est"] += 1
        tc = sum(a["cnt"] for a in agg.values()) or 1
        ta = sum(a["amt"] for a in agg.values()) or 1
        out = [{"broker": k, "cnt": v["cnt"], "amt": v["amt"], "est_cnt": v["est"],
                "cnt_ms": round(v["cnt"] / tc * 100, 1),
                "amt_ms": round(v["amt"] / ta * 100, 1)} for k, v in agg.items()]
        out.sort(key=lambda x: -x["cnt"])
        return {"rows": out, "unknown": unknown, "total": {"cnt": tc, "amt": ta}}

    basis = {k: sum(1 for x in lockups if x.get("custody_broker_src") == k)
             for k in ("기재", "추정", "미상")}

    data = {
        "meta": {
            "baseDate": base_s,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "금융감독원 전자공시시스템(DART)",
        },
        "kpi": {
            "loan": {
                "total_cnt": len(loans),
                "total_co": len({x["corp_name"] for x in loans}),
                "callable_cnt": len(active),
                "callable_co": callable_co,
                "unknown_maturity_cnt": len(unknown_mat),
                "unknown_maturity_co": len({x["corp_name"] for x in unknown_mat}),
                "d7": within(7), "d30": within(30), "d90": within(90),
                "balance": sum(x["loan_amount"] or 0 for x in active),
            },
            "lockup": {
                "total_cnt": len(lockups),
                "total_co": len({x["corp_name"] for x in lockups}),
                "active_cnt": sum(1 for x in lockups if x["is_active"]),
                "active_co": len({x["corp_name"] for x in lockups if x["is_active"]}),
                "d1": lk_within(1), "d7": lk_within(7), "d30": lk_within(30),
                "amount": sum(x["lockup_value"] or 0 for x in lockups if x["is_active"]),
            } if lockups else None,
        },
        "ms_loan": build_ms(loans),
        "ms_lockup": build_ms_lockup(lockups) if lockups else None,
        "ms_lockup_strict": build_ms_lockup(lockups, include_est=False) if lockups else None,
        "lockup_basis": basis,
        "loans": sorted(loans, key=lambda x: (x["maturity_date"] or "9999", -(x["loan_amount"] or 0))),
        "lockups": sorted(lockups, key=lambda x: (x["release_date"], -(x["lockup_value"] or 0))),
        "lockup_status": {
            "state": "mock" if is_mock else ("ok" if lockups else "pending"),
            "note": ("화면 검증용 샘플 데이터입니다. 실공시 연결 시 교체됩니다."
                     if is_mock else "증권신고서·투자설명서 '의무보유' 섹션"),
        },
    }
    out = ROOT / "data" / "dashboard.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    lk = data["kpi"]["lockup"]
    if lk:
        print(f"  보호예수 {lk['total_cnt']}건 / {lk['total_co']}개사"
              f" (해제 예정 {lk['active_cnt']}건) — {data['lockup_status']['state']}")
        print(f"  수탁기관 {len(data['ms_lockup']['rows'])}곳 · 미상 {data['ms_lockup']['unknown']['cnt']}건")
    k = data["kpi"]["loan"]
    print(f"dashboard.json 저장 — {out.stat().st_size//1024}KB")
    print(f"  중복 제거 {removed}건 (같은 계약의 반복 보고)")
    print(f"  담보대출 {k['total_cnt']}건 / {k['total_co']}개사")
    print(f"  콜 가능(만기 확보) {k['callable_cnt']}건 / {k['callable_co']}개사")
    print(f"  만기 미상 {k['unknown_maturity_cnt']}건 / {k['unknown_maturity_co']}개사")
    print(f"  7일내 {k['d7']['cnt']}건 · 30일내 {k['d30']['cnt']}건 · 90일내 {k['d90']['cnt']}건")
    print(f"  대출기관 {len(data['ms_loan']['rows'])}곳 · 기관미상 {data['ms_loan']['unknown']['cnt']}건"
          f" · 복수기관 계약 {data['ms_loan']['multi_lender_cnt']}건")


if __name__ == "__main__":
    main()
