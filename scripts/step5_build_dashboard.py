"""Phase 5 — 락업·담보대출 대시보드 데이터 생성.

8/13 현업 미팅으로 스코프가 2종(보호예수·주식담보대출)으로 압축됐다.
화면은 임원용/실무용으로 나눈다. 이 스크립트는 두 화면이 공유하는 데이터를 만든다.

대상은 KOSPI·KOSDAQ 전체 상장사다.
  입력  data/pledge_all.json   (step7 — 담보계약)
        data/lockup.json       (step6b — 보호예수)
        data/market_master.json (step0c — 업종·시세)

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


# 같은 기관이 한글 음차·영문 약칭으로 갈려 적힌다. M/S 가 쪼개지지 않게 하나로 모은다.
ALIAS = {
    "엔에이치투자증권": "NH투자증권", "농협투자증권": "NH투자증권",
    "케이비증권": "KB증권", "케이비국민은행": "KB국민은행", "국민은행": "KB국민은행",
    "아이비케이투자증권": "IBK투자증권", "아이비케이기업은행": "IBK기업은행",
    "중소기업은행": "IBK기업은행", "기업은행": "IBK기업은행",
    "디비증권": "DB증권",
    "에스케이증권": "SK증권", "비엔케이투자증권": "BNK투자증권",
    "엘에스증권": "LS증권", "아이엠증권": "iM증권", "아이엠뱅크": "iM뱅크",
    "케이디비산업은행": "한국산업은행", "산업은행": "한국산업은행", "KDB산업은행": "한국산업은행",
    "엔에이치농협은행": "NH농협은행", "농협은행": "NH농협은행",
    "한국증권금융주": "한국증권금융", "증권금융": "한국증권금융",
    "하나금융투자": "하나증권", "신한금융투자": "신한투자증권",
    "이베스트투자증권": "LS증권", "하이투자증권": "iM증권",
    "대신": "대신증권", "미래에셋대우": "미래에셋증권",
    "디비금융투자": "DB증권", "DB금융투자": "DB증권",      # 2025년 사명 변경
    "유비에스증권리미티드": "UBS증권", "유비에스증권": "UBS증권",
    "제이피모간증권회사": "JP모간증권", "제이피모간증권": "JP모간증권",
    "메릴린치인터내셔날엘엘씨증권": "메릴린치증권",
    "모간스탠리인터내셔날증권회사": "모건스탠리증권",
    "골드만삭스증권회사": "골드만삭스증권",
    "씨티그룹글로벌마켓증권": "씨티증권",
    "크레디트스위스증권": "CS증권",
    "KEB하나은행": "하나은행", "케이이비하나은행": "하나은행", "하나투자증권": "하나증권",
    "IM뱅크": "iM뱅크", "대구은행": "iM뱅크", "아이엠뱅크": "iM뱅크",   # 2024년 사명 변경
    "BNK증권": "BNK투자증권", "한화증권": "한화투자증권", "NH증권": "NH투자증권",
    "케이프증권": "케이프투자증권", "푸른상호저축은행": "푸른저축은행",
    "아이비케이캐피탈": "IBK캐피탈", "케이비캐피탈": "KB캐피탈",
    "제이비우리캐피탈": "JB우리캐피탈", "에스비아이저축은행": "SBI저축은행",
    "비엔케이저축은행": "BNK저축은행", "엔에이치농협캐피탈": "NH농협캐피탈",
}
# 세무서·법원에 맡긴 주식은 납세담보·공탁이다. 대출이 아니다.
NOT_LENDER_RE = re.compile(r"세무서|지방법원|국세청|법원$")


def split_lenders(s):
    """한 셀에 복수 기관이 들어간 건이 있다(공동 담보).
    건수는 각 기관에 세고, 금액은 균등 분할해 중복 계상을 막는다."""
    if not s:
        return []
    s = re.sub(r"\([^()]*\)", " ", s)            # 괄호 안 영문명·각주 표시는 기관명이 아니다
    s = re.sub(r"\([^()]*$", " ", s)
    m = re.search(r"근질권자\s*[:：]\s*(.+)$", s)   # '주선금융기관: A 근질권자: B' → 담보권자 B
    if m:
        s = m.group(1)
    s = re.sub(r"(^|\s)-\s*", ", ", s)          # '- A - B - C' 나열
    parts = [norm_lender(x) for x in SPLIT_RE.split(s)]
    out = []
    for x in parts:
        if x and x not in out:
            out.append(x)
    return out


_FOOT_RE = re.compile(r"^\(?주\d*\)?$")
_ETC_RE = re.compile(r"(외\d*(인|개사|개|곳|개기관|사)?|외대주단|등)$")


def norm_lender(s):
    if not s:
        return None
    s = s.strip()
    if _FOOT_RE.match(s.replace(" ", "")):     # '주1)' — 각주 참조이지 기관명이 아니다
        return None
    s = CORP_RE.sub("", s).strip()
    s = BRANCH_RE.sub("", s).strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[㈜()]+$|^[㈜()]+", "", s)
    s = re.sub(r"(은행|증권|금고|조합)[가-힣A-Za-z0-9]{1,12}(지점|점|센터|영업부|금융센터)$", r"\1", s)
    s = _ETC_RE.sub("", s)                      # 'NH투자증권외42' → 대표 기관
    if s in ("", "-", "없음", "해당사항없음"):
        return None
    return ALIAS.get(s, s)


def norm_name(s):
    """사람·법인 이름 비교용. 법인격 표기와 공백 차이를 없앤다."""
    return re.sub(r"\s+", "", CORP_RE.sub("", s or ""))


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
    by_code = {v["stock_code"]: {**v, "name": k} for k, v in master.items()}
    listed = {c: v for c, v in by_code.items() if v.get("market") in ("KOSPI", "KOSDAQ")}
    recs = json.loads((ROOT / "data" / "pledge_all.json").read_text(encoding="utf-8"))

    loans, dropped = [], defaultdict(int)
    for p in recs:
        m = listed.get(p.get("stock_code"))
        if not m:                       # 상장폐지·코넥스·종목코드 없는 법인
            dropped["비상장/코넥스"] += 1
            continue
        names = split_lenders(p.get("counterparty"))
        if names and all(NOT_LENDER_RE.search(n) for n in names):
            dropped["납세담보·공탁"] += 1
            continue
        lender = names[0] if names else None
        amount = to_won(p.get("loan_amount"), p.get("unit"))
        shares = p.get("shares")
        price = m.get("price")
        collateral = int(shares * price) if (shares and price) else None
        # 담보 평가액이 시가총액을 넘으면 공시 이후 감자·병합으로 주식수가 바뀐 것이다.
        # 옛 주식수에 지금 주가를 곱한 값은 쓸 수 없다.
        stale_qty = bool(collateral and m.get("market_cap")
                         and collateral > m["market_cap"] * 1e8)
        if stale_qty:
            collateral = None
        ratio = p.get("ratio")
        if ratio is not None and not (0 < ratio <= 100):
            ratio = None                # 지분율 칸에 담보유지비율을 적은 공시
        maturity = p.get("maturity")
        loans.append({
            "corp_name": m["name"],
            "stock_code": m["stock_code"],
            "corp_code": p["corp_code"],
            "market": m.get("market"),
            "industry": m.get("industry"),
            "market_cap": m.get("market_cap"),
            "borrower_name": p.get("holder"),
            "borrower_type": p.get("relation"),
            "lender": lender,
            "lenders": names,
            "lender_type": lender_type(lender),
            "contract_type": p.get("contract_type"),
            "pledged_qty": shares,
            "pledged_ratio": ratio,
            "stale_qty": stale_qty or None,
            "loan_amount": amount,
            "interest_rate": p.get("interest_rate"),
            "maintenance_ratio": p.get("maintenance_ratio"),
            "collateral_set_amount": (int(p["collateral_set_amount"])
                                      if p.get("collateral_set_amount") else None),
            "collateral_amount": collateral,
            "contract_date": p.get("contract_date"),
            "maturity_date": maturity,
            "maturity_src": p.get("maturity_src") or ("기재" if maturity else None),
            "period_raw": p.get("period_raw"),
            "is_active": bool(maturity and maturity >= base_s),
            "debtor": p.get("debtor"),
            "rcept_no": p.get("rcp_no"),
            "rcept_dt": p.get("rcept_dt"),
            "source_kind": p.get("source_kind"),
            "source_section": p.get("source_section"),
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={p.get('rcp_no')}",
        })

    # 단위를 적지 않고 백만원·천원으로 쓴 공시가 있다 ('49,500' = 495억원).
    # 1천만원 미만 대출은 있을 수 없으므로 단위 누락으로 본다. 담보 평가액 대비 비율이
    # 말이 되는 단위가 하나뿐일 때만 환산하고, 아니면 금액을 비운다. 환산한 건은 표시한다.
    unit_fixed = unit_dropped = 0
    for x in loans:
        a = x["loan_amount"]
        if not a or a >= 10_000_000:
            continue
        col = x["collateral_amount"]
        ok = [u for u in (1_000, 1_000_000, 100_000_000)
              if col and 0.03 <= a * u / col <= 1.2]
        if len(ok) == 1:
            x["loan_amount"] = int(a * ok[0])
            x["amount_src"] = "단위추정"
            unit_fixed += 1
        else:
            x["loan_amount"] = None
            x["amount_src"] = "단위불명"
            unit_dropped += 1

    n_before = len(loans)
    # 중복 제거 — 같은 계약이 대량보유보고서와 거래소 공시에 함께 실린다.
    # 계약 실체(회사·차주·기관·주식수)로 묶고, 금액이 있는 쪽 → 최신 접수분 순으로 남긴다.
    # 버린 쪽에만 있는 만기·체결일은 남긴 쪽에 채운다.
    dedup = {}
    for x in loans:
        # 한 담보로 여러 번 빌린 건은 주식수가 비어 있다. 체결일·만기까지 봐야 구분된다.
        k = (x["stock_code"], x["borrower_name"], x["lender"], x["pledged_qty"],
             x["contract_date"], x["maturity_date"])
        cur = dedup.get(k)
        if cur is None:
            dedup[k] = x
            continue
        rank = lambda r: (r["loan_amount"] is not None, r["rcept_no"] or "")
        keep, drop = (x, cur) if rank(x) > rank(cur) else (cur, x)
        for f in ("maturity_date", "maturity_src", "contract_date", "collateral_set_amount"):
            if keep.get(f) is None and drop.get(f) is not None:
                keep[f] = drop[f]
        keep["is_active"] = bool(keep["maturity_date"] and keep["maturity_date"] >= base_s)
        dedup[k] = keep
    loans = list(dedup.values())

    # 같은 담보(회사·담보제공자·주식수)가 대량보유보고서와 거래소 공시 양쪽에 있으면 한 건이다.
    # 기관 표기와 만기 기재가 서로 달라 위 키로는 안 묶인다. 최신 접수분을 남기고,
    # 거기 비어 있는 값(금액·만기·금리)만 다른 쪽에서 채운다.
    blocks = defaultdict(list)
    for x in loans:
        if x["pledged_qty"]:
            blocks[(x["stock_code"], norm_name(x["borrower_name"]), x["pledged_qty"])].append(x)
    drop_ids = set()
    for g in blocks.values():
        if len({r["source_kind"] for r in g}) < 2:
            continue
        g.sort(key=lambda r: r["rcept_no"] or "", reverse=True)
        keep = g[0]
        for other in g[1:]:
            if other["source_kind"] == keep["source_kind"]:
                continue
            for f in ("loan_amount", "maturity_date", "maturity_src", "contract_date",
                      "interest_rate", "maintenance_ratio", "pledged_ratio",
                      "collateral_set_amount", "period_raw", "debtor"):
                if keep.get(f) is None and other.get(f) is not None:
                    keep[f] = other[f]
            # 대량보유보고서는 상대방을 '주4)' 처럼 주석으로 돌리는 경우가 있다.
            # 남기는 쪽에 기관이 없으면 거래소 공시의 채권자를 가져온다.
            if not keep.get("lender") and other.get("lender"):
                for f in ("lender", "lenders", "lender_type"):
                    if other.get(f):
                        keep[f] = other[f]
            keep["also_in"] = other["rcept_no"]
            drop_ids.add(id(other))
        keep["is_active"] = bool(keep["maturity_date"] and keep["maturity_date"] >= base_s)
    loans = [x for x in loans if id(x) not in drop_ids]
    removed = n_before - len(loans)

    # ---- 금액 집계 기준. 목록에는 공시된 금액을 그대로 두고, 합계에만 아래를 적용한다.
    #  (1) 공동담보: 한 대출에 여러 사람이 주식을 담보로 넣으면 같은 대출금액이 사람 수만큼
    #      반복 기재된다. 그대로 더하면 잔액이 몇 배로 부푼다. 한 번만 센다.
    #  (2) 복합여신: 대출금액이 담보 주식 평가액의 3배를 넘으면 주식 외 자산이 함께 담보된
    #      여신이다(인수금융·신용보강 등). 주식담보대출 잔액으로 세지 않는다.
    groups = defaultdict(list)
    for x in loans:
        if x["loan_amount"]:
            # 접수번호·회사로 나누지 않는다. 한 대출에 여러 회사 주식이 담보로 들어가기도 한다.
            groups[(x["lender"], x["loan_amount"], x["maturity_date"],
                    x["debtor"] or x["borrower_name"])].append(x)
    for g in groups.values():
        g.sort(key=lambda r: -(r["pledged_qty"] or 0))
        tot_col = sum(r["collateral_amount"] or 0 for r in g)
        facility = bool(tot_col and g[0]["loan_amount"] > tot_col * 3)
        for i, r in enumerate(g):
            r["shared_cnt"] = len(g) if len(g) > 1 else None
            r["facility"] = facility or None
            r["amount_counted"] = 0 if (i > 0 or facility) else r["loan_amount"]
    for x in loans:
        x.setdefault("amount_counted", 0)
        # 제3자 채무를 위해 담보를 넣은 경우에만 채무자를 따로 보여준다
        if not x.get("debtor") or norm_name(x["debtor"]) == norm_name(x["borrower_name"]):
            x.pop("debtor", None)
    # 만기가 지난 계약은 현재 잔액으로 보지 않는다 (만기 미상·자동연장은 유효로 둔다)
    live = [x for x in loans if not x["maturity_date"] or x["maturity_date"] >= base_s]

    # ---- ms_loan. 미상은 분모에서 빼고 별도 표기한다(현업 명시 요구)
    def build_ms(rows):
        agg = defaultdict(lambda: {"cnt": 0, "balance": 0, "collateral": 0})
        unknown = {"cnt": 0, "balance": 0}
        multi = 0
        for x in rows:
            names = x["lenders"] or []
            if not names:
                unknown["cnt"] += 1
                unknown["balance"] += x["amount_counted"]
                continue
            if len(names) > 1:
                multi += 1
            share = 1 / len(names)
            for nm in names:
                a = agg[nm]
                a["cnt"] += 1
                a["balance"] += x["amount_counted"] * share
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
                "total": {"cnt": tot_c, "balance": tot_b, "collateral": tot_l,
                          "companies": len({x["stock_code"] for x in rows}),
                          "contracts": len(rows)}}

    # 체결 연도별 M/S. 과거 시점의 잔액이 아니다 — 지금 남아 있는 계약을 체결(연장)한 해로 나눈 것이다.
    LOAN_YEARS = ("2026",)
    ms_loan_by_year = {"all": build_ms(live)}
    for y in LOAN_YEARS:
        ms_loan_by_year[y] = build_ms([x for x in live if (x["contract_date"] or "")[:4] == y])

    active = [x for x in loans if x["is_active"]]

    def within(days):
        rows = [x for x in active
                if (datetime.fromisoformat(x["maturity_date"]).date() - base).days <= days]
        return {"cnt": len(rows), "amount": sum(x["amount_counted"] for x in rows)}

    callable_co = len({x["corp_name"] for x in active})
    unknown_mat = [x for x in loans if not x["maturity_date"]]

    # ---- 보호예수 (step6b)
    lk_path = ROOT / "data" / "lockup.json"
    lockups = json.loads(lk_path.read_text(encoding="utf-8")) if lk_path.exists() else []
    for x in lockups:
        x.setdefault("source", "IPO")          # 증권신고서 — 주주 단위
    ksd_path = ROOT / "data" / "lockup_seibro.json"
    if ksd_path.exists():                       # 예탁결제원 — 사유 단위 (유상증자·합병 등)
        lockups += json.loads(ksd_path.read_text(encoding="utf-8"))
    lockups = [x for x in lockups if x.get("stock_code") in listed]
    # 주관사는 KIND 공식 신규상장 목록으로 만든 IPO 모집단(step8)의 확정값을 쓴다.
    # 목록과 점유율이 서로 다른 주관사를 말하면 안 된다.
    deals_path = ROOT / "data" / "ipo_deals.json"
    deals = json.loads(deals_path.read_text(encoding="utf-8")) if deals_path.exists() else []
    deal_by_code = {d["stock_code"]: d for d in deals if d.get("stock_code")}
    deal_by_name = {re.sub(r"\s+", "", d["name"]): d for d in deals}
    for x in lockups:
        d = deal_by_code.get(x["stock_code"]) or deal_by_name.get(re.sub(r"\s+", "", x["corp_name"]))
        if not d:
            continue
        x["custody_brokers"] = d["lead_managers"]
        x["custody_broker"] = d["lead_managers"][0] if d["lead_managers"] else None
        x["custody_broker_src"] = "주관사"
        x["lead_manager"] = ", ".join(d["lead_managers"]) or None
        x["co_manager"] = ", ".join(d["co_managers"]) or None
        x["manager_src"] = d["lead_src"]
        x["listing_date"] = d["listing_date"]          # 거래소 확정 상장일
        x["is_spac"] = d["is_spac"]
    # 있을 수 없는 값은 화면에 내지 않는다. 한 건이 틀리면 전체를 못 믿게 된다.
    #   - 평가금액이 시가총액보다 큼: 주식수 칸에 금액(전환사채 권면 등)이 들어간 행
    #   - 해제일이 상장일보다 앞섬: 기산일이 잘못 잡힌 행
    excluded = []
    def bad(x):
        cap = (x.get("market_cap") or 0) * 1e8
        if cap and x.get("lockup_value") and x["lockup_value"] > cap:
            return "락업금액>시총"
        if x.get("listing_date") and x["release_date"] and x["release_date"] < x["listing_date"]:
            return "해제일<상장일"
        return None
    kept = []
    for x in lockups:
        why = bad(x)
        if why:
            excluded.append({"rule": why, "excluded": True, "corp_name": x["corp_name"],
                             "stock_code": x["stock_code"], "rcept_no": x.get("rcept_no"),
                             "detail": f"{x.get('holder_name')} {x['lockup_qty']:,}주 {x['release_date']}"})
        else:
            kept.append(x)
    lockups = kept
    is_mock = bool(lockups) and lockups[0].get("is_mock")
    for x in lockups:
        m = listed[x["stock_code"]]
        x["corp_name"] = m["name"]
        x["market"] = m.get("market")
        # 해제일을 모르는 건(예탁결제원 미반환 잔량)은 아직 묶여 있는 물량이다
        x["is_active"] = (x["release_date"] is None) or x["release_date"] >= base_s
    lk_active = [x for x in lockups if x["is_active"]]
    # 수탁 M/S = IPO 주관사 점유율
    lk_ms = [x for x in lk_active if x.get("source") == "IPO"]

    def lk_within(days):
        rows = [x for x in lockups if x["is_active"] and x["release_date"]
                and (datetime.fromisoformat(x["release_date"]).date() - base).days <= days]
        return {"cnt": len(rows), "amount": sum(x["lockup_value"] or 0 for x in rows)}

    def build_ms_lockup(rows):
        """IPO 주관사별 점유율.

        건수는 회사 단위다 — 한 회사의 IPO 는 주주가 몇 명이든 1건이다.
        공동대표주관이면 그 1건을 각 주관사에 세고, 금액은 고르게 나눈다.
        """
        agg = defaultdict(lambda: {"cos": set(), "amt": 0, "rows": 0})
        unknown = {"cos": set(), "amt": 0}
        joint = set()
        for x in rows:
            bs = x.get("custody_brokers") or ([x["custody_broker"]] if x.get("custody_broker") else [])
            if not bs:
                unknown["cos"].add(x["stock_code"])
                unknown["amt"] += x["lockup_value"] or 0
                continue
            if len(bs) > 1:
                joint.add(x["stock_code"])
            for b in bs:
                a = agg[b]
                a["cos"].add(x["stock_code"])
                a["rows"] += 1
                a["amt"] += (x["lockup_value"] or 0) / len(bs)
        tc = sum(len(a["cos"]) for a in agg.values()) or 1
        ta = sum(a["amt"] for a in agg.values()) or 1
        out = [{"broker": k, "cnt": len(v["cos"]), "amt": int(v["amt"]),
                "co_cnt": len(v["cos"]), "row_cnt": v["rows"], "est_cnt": 0,
                "cnt_ms": round(len(v["cos"]) / tc * 100, 1),
                "amt_ms": round(v["amt"] / ta * 100, 1)} for k, v in agg.items()]
        out.sort(key=lambda x: (-x["cnt"], -x["amt"]))
        return {"rows": out,
                "unknown": {"cnt": len(unknown["cos"]), "amt": unknown["amt"]},
                "joint_cnt": len(joint), "unit": "개사",
                "total": {"cnt": tc, "amt": int(ta), "rows": len(rows),
                          "companies": len({x["stock_code"] for x in rows})}}

    def build_ms_ipo(rows):
        """IPO 주관 실적.

        건수 — 대표주관(공동대표주관 포함) 기준. 회사 1곳 = 1건.
        금액 — 증권사별 실제 인수금액. 증권신고서 인수인 표의 비율 × 확정 공모금액.
               공동주관·인수회사도 각자 인수한 만큼 잡힌다.
        """
        agg = defaultdict(lambda: {"cnt": 0, "amt": 0.0, "spac": 0, "uw_cnt": 0})
        joint = 0
        for d in rows:
            bs = d["lead_managers"]
            if len(bs) > 1:
                joint += 1
            for b in bs:
                agg[b]["cnt"] += 1
                agg[b]["spac"] += 1 if d["is_spac"] else 0
            for al in d.get("allocations") or []:
                agg[al["broker"]]["amt"] += al["amount"] or 0
                agg[al["broker"]]["uw_cnt"] += 1
        tc = sum(a["cnt"] for a in agg.values()) or 1
        ta = sum(a["amt"] for a in agg.values()) or 1
        out = [{"broker": k, "cnt": v["cnt"], "amt": int(v["amt"]), "co_cnt": v["cnt"],
                "uw_cnt": v["uw_cnt"], "spac_cnt": v["spac"], "est_cnt": 0,
                "cnt_ms": round(v["cnt"] / tc * 100, 1),
                "amt_ms": round(v["amt"] / ta * 100, 1)} for k, v in agg.items()]
        out.sort(key=lambda x: (-x["cnt"], -x["amt"]))
        codes = {d["stock_code"] for d in rows}
        with_lock = {x["stock_code"] for x in lockups} & codes
        unverified = sum(1 for d in rows if "미확인" in (d.get("alloc_src") or ""))
        return {"rows": out, "unknown": {"cnt": 0, "amt": 0}, "joint_cnt": joint, "unit": "개사",
                "total": {"cnt": tc, "amt": int(sum(d["offer_amount"] or 0 for d in rows)),
                          "companies": len(rows),
                          "kospi": sum(1 for d in rows if d["market"] == "KOSPI"),
                          "kosdaq": sum(1 for d in rows if d["market"] == "KOSDAQ"),
                          "spac": sum(1 for d in rows if d["is_spac"]),
                          "reit": sum(1 for d in rows if d["is_reit"]),
                          "alloc_unverified": unverified,
                          "with_lockup": len(with_lock),
                          "rows": sum(1 for x in lockups if x["stock_code"] in codes)}}

    # 'core' = 스팩·리츠·인프라펀드 제외 (증권업계 리그테이블의 통상 기준), 'all' = 전부 포함
    scopes = {"core": [d for d in deals if not d["is_spac"] and not d["is_reit"]], "all": deals}
    years = sorted({d["listing_date"][:4] for d in deals})
    ms_ipo = {}
    for sc, rows in scopes.items():
        ms_ipo[sc] = {"all": build_ms_ipo(rows)}
        for y in years:
            ms_ipo[sc][y] = build_ms_ipo([d for d in rows if d["listing_date"][:4] == y])
    ms_by_year = ms_ipo["core"]
    no_date = 0
    ld = [d["listing_date"] for d in deals]
    ms_range = [min(ld), max(ld)] if ld else None

    basis = {"주관사": sum(1 for x in lk_ms if x.get("custody_brokers")),
             "미상": sum(1 for x in lk_ms if not x.get("custody_brokers")),
             "기재": 0, "추정": 0}

    # ---- 전체 상장사 마스터. 공시가 없는 회사도 검색되어야 한다.
    n_loan, n_lock = defaultdict(int), defaultdict(int)
    for x in loans:
        n_loan[x["stock_code"]] += 1
    for x in lockups:
        n_lock[x["stock_code"]] += 1
    companies = [{"n": v["name"], "c": c, "m": v.get("market"), "i": v.get("industry"),
                  "cap": v.get("market_cap"), "ln": n_loan.get(c, 0), "lk": n_lock.get(c, 0)}
                 for c, v in sorted(listed.items(), key=lambda kv: -(kv[1].get("market_cap") or 0))]

    manifest = {}
    for kind in ("majorstock", "exchange"):
        f = ROOT / "data" / f"pledge_manifest_{kind}.json"
        if not f.exists() and kind == "majorstock":
            f = ROOT / "data" / "pledge_manifest.json"
        if f.exists():
            mf = json.loads(f.read_text(encoding="utf-8"))
            manifest[kind] = {"period": mf["period"], "report": mf["report"],
                              "list_rows": mf["list_rows"], "targets": len(mf["targets"]),
                              "companies": len({t["corp_code"] for t in mf["targets"]})}
    meta_path = ROOT / "data" / "market_master_meta.json"
    price_date = (json.loads(meta_path.read_text(encoding="utf-8"))["price_date"]
                  if meta_path.exists() else None)
    _pm = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    _fa = _pm.get("fetched_at") or ""
    if price_date and _fa[:10] == price_date and _fa[11:16] < "15:30":
        _h, _mi = int(_fa[11:13]), _fa[14:16]
        price_label = f"{price_date.replace('-', '.')} {'오후' if _h >= 12 else '오전'} {_h - 12 if _h > 12 else _h}시 {_mi}분 장중 가격"
    else:
        price_label = f"{price_date.replace('-', '.')} 종가"

    data = {
        "meta": {
            "baseDate": base_s,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "금융감독원 전자공시시스템(DART)",
            "priceDate": price_date,
            # 15:30 이전에 받은 당일 시세는 종가가 아니라 장중 가격이다
            "priceLabel": price_label,
            "universe": {"listed": len(listed),
                         "kospi": sum(1 for v in listed.values() if v["market"] == "KOSPI"),
                         "kosdaq": sum(1 for v in listed.values() if v["market"] == "KOSDAQ"),
                         "with_loan": len(n_loan), "with_lockup": len(n_lock)},
            "collection": manifest,
            "ipoRange": ms_range,
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
                "balance": sum(x["amount_counted"] for x in active),
                "live_cnt": len(live),
                "facility_cnt": sum(1 for x in loans if x.get("facility")),
                "shared_cnt": sum(1 for x in loans if x.get("shared_cnt")),
            },
            "lockup": {
                "total_cnt": len(lockups),
                "total_co": len({x["corp_name"] for x in lockups}),
                "active_cnt": sum(1 for x in lockups if x["is_active"]),
                "active_co": len({x["corp_name"] for x in lockups if x["is_active"]}),
                "ms_cnt": len(lk_ms), "ms_co": len({x["stock_code"] for x in lk_ms}),
                "ipo_co": len({x["stock_code"] for x in lockups if x.get("source") == "IPO"}),
                "ksd_co": len({x["stock_code"] for x in lockups if x.get("source") == "KSD"}),
                "unknown_release_cnt": sum(1 for x in lockups if not x["release_date"]),
                "d1": lk_within(1), "d7": lk_within(7), "d30": lk_within(30),
                "amount": sum(x["lockup_value"] or 0 for x in lockups if x["is_active"]),
            } if lockups else None,
        },
        "ms_loan": ms_loan_by_year["all"],
        "ms_loan_by_year": ms_loan_by_year,
        # 수탁 M/S 는 아직 풀리지 않은 물량 기준이다. 이미 해제된 건은 수탁 잔고가 아니다.
        # 수탁 M/S 는 상장 연도로 본다 — 그 해 IPO 를 누가 주관했는가.
        # 'all' 은 2022년부터 지금까지 전체, 'active' 는 보호예수가 아직 남은 회사만.
        "ms_ipo": ms_ipo,
        # 점유율 화면에서 증권사를 누르면 그 증권사의 IPO 목록을 보여준다
        "ipo_deals": [{
            "n": d["name"], "c": d["stock_code"], "m": d["market"], "d": d["listing_date"],
            "spac": d["is_spac"], "reit": d["is_reit"], "t": d["listing_type"],
            "amt": d["offer_amount"], "px": d["offer_price"],
            "lead": d["lead_managers"], "co": d["co_managers"],
            "al": [[a["broker"], a["amount"], a["ratio"]] for a in d.get("allocations") or []],
            "rcp": d.get("prospectus_rcept_no"),
        } for d in sorted(deals, key=lambda d: d["listing_date"], reverse=True)],
        "ms_lockup_by_year": ms_by_year,
        "ms_lockup": ms_by_year["all"] if lockups else None,
        "ms_lockup_active": build_ms_lockup(lk_ms) if lockups else None,
        "lockup_basis": basis,
        "companies": companies,
        "loans": sorted(loans, key=lambda x: (x["maturity_date"] or "9999", -(x["loan_amount"] or 0))),
        "lockups": sorted(lockups, key=lambda x: (x["release_date"] or "9999",
                                                  -(x["lockup_value"] or 0))),
        "lockup_status": {
            "state": "mock" if is_mock else ("ok" if lockups else "pending"),
            "note": ("화면 검증용 샘플 데이터입니다. 실공시 연결 시 교체됩니다."
                     if is_mock else "증권신고서·투자설명서 '의무보유' 섹션. "
                     "수탁 증권사는 IPO 대표주관회사"),
        },
    }
    # ---- 검증 (데이터요구사항 6절). 위반 건은 지우지 않고 기록한다 — 사람이 원문과 대조한다.
    issues = list(excluded)
    def flag(rule, x, detail):
        issues.append({"rule": rule, "corp_name": x["corp_name"], "stock_code": x["stock_code"],
                       "rcept_no": x.get("rcept_no"), "detail": detail})
    for x in loans:
        cap = (x["market_cap"] or 0) * 1e8
        if cap and x["collateral_amount"] and x["collateral_amount"] > cap:
            flag("담보금액>시총", x, f"{x['collateral_amount']:,} > {int(cap):,}")
        if cap and x["loan_amount"] and x["loan_amount"] > cap:
            flag("대출금액>시총", x, f"{x['loan_amount']:,} > {int(cap):,}")
        r = x["pledged_ratio"]
        if r is not None and not (0 < r <= 100):
            flag("지분율 범위", x, str(r))
        if x["maturity_date"] and x["contract_date"] and x["maturity_date"] < x["contract_date"]:
            flag("만기<체결일", x, f"{x['maturity_date']} < {x['contract_date']}")
        if not x["borrower_name"]:
            flag("필수 누락", x, "차주")
    for name, ms, key in (("ms_loan", data["ms_loan"], "cnt_ms"), ("ms_lockup", data["ms_lockup"], "cnt_ms")):
        if ms and ms["rows"]:
            tot = sum(r[key] for r in ms["rows"])
            # 기관이 수백 곳이면 0.1 단위 반올림 오차가 쌓인다. 허용폭을 행 수에 비례해 잡는다.
            if abs(tot - 100) > 0.05 * len(ms["rows"]) + 0.1:
                issues.append({"rule": "M/S 합", "detail": f"{name} {tot:.1f}%"})
    (ROOT / "data" / "validation.json").write_text(
        json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
    by_rule = defaultdict(int)
    for i in issues:
        by_rule[i["rule"]] += 1
    data["meta"]["validation"] = dict(by_rule)

    out = ROOT / "data" / "dashboard.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    lk = data["kpi"]["lockup"]
    if lk:
        print(f"  보호예수 {lk['total_cnt']}건 / {lk['total_co']}개사"
              f" (해제 예정 {lk['active_cnt']}건) — {data['lockup_status']['state']}")
        print(f"  수탁기관 {len(data['ms_lockup']['rows'])}곳 · 미상 {data['ms_lockup']['unknown']['cnt']}건")
    k = data["kpi"]["loan"]
    print(f"dashboard.json 저장 — {out.stat().st_size//1024}KB")
    print(f"  중복 제거 {removed}건 (같은 계약의 반복 보고) · 제외 {dict(dropped)}")
    print("  수탁 M/S(상장 연도) — " + " · ".join(
        f"{k} {v['total']['companies']}개사" for k, v in ms_by_year.items())
        + (f" · 상장일 없음 {no_date}개사" if no_date else ""))
    print(f"  단위 누락 — 환산 {unit_fixed}건 · 금액 비움 {unit_dropped}건")
    print(f"  상장사 {len(listed):,}개사 중 담보대출 {len(n_loan):,} · 보호예수 {len(n_lock):,}개사")
    print(f"  검증 위반 {len(issues)}건 {dict(by_rule)} → data/validation.json")
    print(f"  담보대출 {k['total_cnt']}건 / {k['total_co']}개사")
    print(f"  콜 가능(만기 확보) {k['callable_cnt']}건 / {k['callable_co']}개사")
    print(f"  만기 미상 {k['unknown_maturity_cnt']}건 / {k['unknown_maturity_co']}개사")
    print(f"  7일내 {k['d7']['cnt']}건 · 30일내 {k['d30']['cnt']}건 · 90일내 {k['d90']['cnt']}건")
    print(f"  대출기관 {len(data['ms_loan']['rows'])}곳 · 기관미상 {data['ms_loan']['unknown']['cnt']}건"
          f" · 복수기관 계약 {data['ms_loan']['multi_lender_cnt']}건")


if __name__ == "__main__":
    main()
