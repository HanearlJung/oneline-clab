"""Phase 3 — 추출 결과를 화면 데이터로 정규화 + 커버리지 집계.

extracted/{corp_code}.json  ->  data/site_data.json

원칙
  - 값이 없어 레코드를 만들 수 없는 기업은 해당 탭에서 제외한다.
  - 제외분은 커버리지로 집계되어 화면에 정직하게 표기된다.
  - 날짜가 없는 항목에 가짜 날짜를 만들지 않는다. timeColumn.mode 로 표현을 바꾼다.
"""
import argparse
from datetime import date, datetime

import dart
from dart import ROOT

# 원문 단위 -> 억원
UNIT_TO_EOK = {
    "원": 1e-8, "천원": 1e-5, "백만원": 1e-2, "십억원": 10.0, "억원": 1.0, "조원": 10000.0,
}


def to_eok(value, unit: str):
    """원문 단위 금액을 억원으로 환산. 단위를 모르면 None (추측하지 않는다)."""
    if value is None:
        return None
    factor = UNIT_TO_EOK.get((unit or "").strip())
    if factor is None:
        return None
    return round(float(value) * factor, 1)


def eok_text(v):
    if v is None:
        return None
    if abs(v) >= 10000:
        j = v / 10000
        return (f"{j:.0f}" if float(j).is_integer() else f"{j:.1f}") + "조원"
    return f"{round(v):,}억원"


def src(rec: dict, rcp_no: str = None) -> dict:
    s = rec.get("source") or {}
    no = rcp_no or s.get("rcp_no")
    return {"rcpNo": no, "sourceUrl": dart.dart_url(no) if no else None,
            "asOf": s.get("as_of")}


MARKET_META: dict[str, dict] = {}   # corp_code -> 시총·주가·상장주식수·업종
BASE_DATE = date.today().isoformat()  # main()에서 --base-date 로 덮어씀


def is_future(d) -> bool:
    return bool(d) and d >= BASE_DATE


def meta(rec: dict) -> dict:
    return MARKET_META.get(rec["corp_code"], {})


def base(rec: dict) -> dict:
    m = meta(rec)
    return {"name": rec["name"],
            "code": m.get("stock_code") or rec.get("stock_code") or rec["corp_code"],
            "market": rec.get("market") or m.get("market"),
            "industry": rec.get("industry") or m.get("industry")}


def shares_to_eok(rec: dict, shares) -> float | None:
    """주식수 -> 평가액(억원). 주가가 없으면 None (추측하지 않는다).

    담보주식·매각지분·주식보상은 '주식수'로는 영업에 못 쓴다.
    얼마짜리인지 알아야 제안 규모가 나온다.
    """
    price = meta(rec).get("price")
    if not price or not shares:
        return None
    return round(float(shares) * float(price) / 1e8, 1)


# ------------------------------------------------------------- 탭별 행 생성

def rows_investment(rec):
    """금융상품 영업의 타깃은 금융자산 총액이 아니다.

    FVOCI 는 대개 전략적 지분(계열사 지분 등)이라 건드릴 수 없다.
    실제로 갈아탈 수 있는 건 단기금융상품·예치금 같은 저수익 파킹 자금과
    수익증권류(FVTPL)다. 영업사원이 쓰는 숫자는 그것이다.
    """
    fa = rec.get("financial_assets")
    if not fa:
        return None
    unit = fa.get("unit")
    total = to_eok(fa.get("total"), unit)
    fvoci = to_eok(fa.get("fvoci"), unit)
    fvtpl = to_eok(fa.get("fvtpl"), unit)
    short = to_eok(fa.get("short_term_deposits"), unit)

    # 영업 가능 물량 = 저수익 파킹 자금 + 이미 굴리는 수익증권류
    target = sum(x for x in (short, fvtpl) if x is not None) or None
    if target is None:
        target = total
    if target is None:
        return None

    if short and target and short / target >= 0.6:
        situation = "예치자금 편중"
        action = f"단기 예치자금 {eok_text(short)} 채권형·ELS 수익형 전환 제안"
    elif fvtpl and target and fvtpl / target >= 0.5:
        situation = "운용상품 보유"
        action = f"기보유 운용상품 {eok_text(fvtpl)} 운용사 교체·추가 제안"
    else:
        situation = "운용 여력"
        action = f"여유자금 {eok_text(target)} 채권형·유동성 상품 제안"

    parts = [f"{n} {eok_text(v)}" for n, v in
             (("단기예치", short), ("수익증권류", fvtpl), ("전략지분(FVOCI)", fvoci)) if v]
    buckets = fa.get("maturity_buckets") or []

    return {
        **base(rec), **src(rec),
        "reason": f"운용 가능 자금 {eok_text(target)} 보유",
        "detail": " · ".join(parts) or f"금융자산 총계 {eok_text(total)}",
        "amount": target,
        "metricText": eok_text(target),
        "ratio": None,
        "situation": situation,
        "dateValue": None,
        "timeText": (" · ".join(b["label"] for b in buckets[:2]) if buckets
                     else situation),
        "action": action,
        "chips": ["금융자산", situation],
    }


def rows_pension(rec):
    """퇴직연금 영업의 대상은 부채(DBO)가 아니라 '운용 중인 적립금'이다.

    특히 원금보장형에 묶인 금액이 실적배당형 전환 제안의 실제 타깃 물량이고,
    당기 사용자 기여금은 올해 새로 들어온 운용 자금이다.
    이 둘이 영업사원이 쓰는 숫자다.
    """
    p = rec.get("pension")
    if not p:
        return None
    unit = p.get("unit")
    dbo = to_eok(p.get("dbo"), unit)
    assets = to_eok(p.get("plan_assets"), unit)
    guaranteed = to_eok(p.get("principal_guaranteed"), unit)   # 원금보장형
    indirect = to_eok(p.get("indirect_investment"), unit)      # 간접투자상품
    contrib = to_eok(p.get("employer_contribution"), unit)     # 당기 사용자 기여금

    if assets is None and dbo is None:
        return None

    ratio = p.get("funding_ratio")
    if ratio is None and assets is not None and dbo:
        ratio = round(assets / dbo * 100, 1)

    # 영업 가능 물량: 원금보장형이 있으면 그것, 없으면 적립금 전체
    target = guaranteed if guaranteed is not None else assets
    gpct = round(guaranteed / assets * 100, 1) if (guaranteed and assets) else None

    # 영업 상황 판정 — 문구와 데이터가 어긋나지 않도록 여기서 갈라준다.
    # 100% 언저리(95~100%)는 사실상 완전적립이라 '부족'으로 부르면 과장이 된다.
    if ratio is not None and ratio < 95:
        situation = "추가적립 필요"
        action = f"적립 부족분 해소 + 신규 적립금 {eok_text(target)} 운용기관 제안"
    elif gpct is not None and gpct >= 60:
        situation = "원금보장형 편중"
        action = f"원금보장형 {eok_text(guaranteed)} 실적배당형 전환 제안"
    else:
        situation = "운용기관 경쟁"
        action = f"적립금 {eok_text(target)} 운용기관 교체·추가 제안"

    expected = to_eok(p.get("expected_contribution"), unit)   # 차기 예상 기여금
    providers = p.get("providers") or []                      # 현 수탁기관 = 경쟁사

    detail_parts = []
    if gpct is not None:
        # 간접투자가 0이면 "0억원"을 적지 않는다. 없는 게 곧 영업 포인트다.
        seg = f"원금보장형 {eok_text(guaranteed)}({gpct}%)"
        seg += f" · 간접투자 {eok_text(indirect)}" if indirect else " · 실적배당형 없음"
        detail_parts.append(seg)
    if expected:
        detail_parts.append(f"차기 기여금 {eok_text(expected)} 유입 예정")
    elif contrib:
        detail_parts.append(f"당기 기여금 {eok_text(contrib)} 신규 유입")
    if providers:
        detail_parts.append(f"현 수탁 {', '.join(providers)}")
    if not detail_parts:
        detail_parts.append(f"확정급여채무 {eok_text(dbo)} · 적립률 {ratio}%"
                            if ratio is not None else f"확정급여채무 {eok_text(dbo)}")

    return {
        **base(rec), **src(rec),
        "reason": f"퇴직연금 적립금 {eok_text(assets)} 운용 중" if assets
                  else f"확정급여채무 {eok_text(dbo)}",
        "detail": " · ".join(detail_parts),
        "amount": target,
        "metricText": eok_text(target),
        "ratio": ratio,
        "situation": situation,
        "dateValue": None,
        "timeText": situation + (f" · 적립률 {ratio}%" if ratio is not None else ""),
        "action": action,
        "chips": ["퇴직연금", situation],
    }


def rows_blockdeal(rec):
    """처분 '방식'이 영업 기회를 가른다.

    시간외대량매매·장외처분이면 주관 기회가 직접 열리고,
    장내매도면 매도 대행·유동성 공급 쪽 제안이 된다.
    금액이 미공시여도 수량 x 현재가로 규모를 가늠할 수 있다.
    """
    events = rec.get("treasury") or []
    if not events:
        return None

    def ev_date(e):
        return e.get("period_end") or e.get("period_start") or e.get("resolved_on")

    def ev_amount(e):
        return (to_eok(e.get("amount"), e.get("unit") or "백만원")
                or shares_to_eok(rec, e.get("shares")))

    # 처분예정기간은 대개 결정 후 3개월 이내라 이미 끝난 건이 훨씬 많다.
    # 끝난 건은 영업 기회가 아니다. 미래 예정 건이 있으면 그게 접촉 시점이고,
    # 없으면 '보유 자사주'가 향후 처분 여지를 나타내는 단서가 된다.
    upcoming = sorted((e for e in events if is_future(ev_date(e))),
                      key=ev_date)
    held = max((e.get("held_shares") or 0) for e in events) or None
    held_value = shares_to_eok(rec, held)

    if upcoming:
        e = upcoming[0]
        amount = ev_amount(e)
        etype = e.get("event_type") or "처분"
        method = e.get("method") or ""
        if etype == "취득":
            action = "취득 물량 자기주식 거래 주관·신탁계약 제안"
        elif any(k in method for k in ("시간외", "장외", "블록")):
            action = f"{eok_text(amount) or '처분 물량'} 블록딜 주관 제안"
        elif "장내" in method:
            action = "장내 처분 대행 및 매도 타이밍 자문 제안"
        else:
            action = "처분 방식 검토 및 블록딜 주관 제안"
        return {
            **base(rec), **src(rec, e.get("rcp_no")),
            "reason": (f"자사주 {etype} {eok_text(amount)} 예정" if amount
                       else f"자사주 {etype} 예정"),
            "detail": " · ".join(x for x in [
                f"{e['shares']:,}주" if e.get("shares") else None, method,
                f"보유 자사주 {held:,}주" if held else None,
                e.get("purpose")] if x),
            "amount": amount, "metricText": eok_text(amount) or "미공시",
            "ratio": None, "situation": "처분 예정",
            "dateValue": ev_date(e), "timeText": None,
            "eventType": etype, "action": action, "chips": ["자사주"],
        }

    # 미래 예정 건이 없는 회사 — 보유 자사주가 향후 물량이다
    if not held_value:
        return None
    recent = max(events, key=lambda e: ev_date(e) or "")
    return {
        **base(rec), **src(rec, recent.get("rcp_no")),
        "reason": f"보유 자사주 {eok_text(held_value)} 처분 여력",
        "detail": " · ".join(x for x in [
            f"{held:,}주 보유",
            f"최근 {recent.get('event_type') or '처분'} {ev_date(recent)}"
            if ev_date(recent) else None] if x),
        "amount": held_value, "metricText": eok_text(held_value),
        "ratio": None, "situation": "보유 물량",
        "dateValue": None, "timeText": "예정 공시 없음",
        "eventType": recent.get("event_type") or "처분",
        "action": f"보유 자사주 {eok_text(held_value)} 처분·소각 방안 및 블록딜 주관 제안",
        "chips": ["자사주"],
    }


def rows_pledge(rec):
    """대출금액은 공시 의무가 아니라 결측이 흔하다.

    그럴 때 담보주식 평가액(주식수 x 현재가)이 영업 규모의 대용이 된다.
    담보 여력·리파이낸싱 규모를 가늠할 수 있어야 제안이 성립한다.
    """
    items = rec.get("pledge") or []
    if not items:
        return None

    # 한 회사에 계약이 수십 건씩 있다(같은 대주주가 증권사별로 쪼개 실행).
    # 개별 계약을 나열하면 영업에 못 쓴다. 회사 단위로 합산하고
    # 가장 먼저 도래하는 만기를 접촉 시점으로 삼는다.
    unit = items[0].get("unit") or "원"
    loans = [to_eok(p.get("loan_amount"), p.get("unit") or unit) for p in items]
    loans = [x for x in loans if x]
    total_loan = round(sum(loans), 1) if loans else None
    total_shares = sum(p.get("shares") or 0 for p in items) or None
    collateral = shares_to_eok(rec, total_shares)

    # 만기가 지난 계약은 이미 상환·연장됐다. 접촉 시점은 '앞으로 도래하는' 만기다.
    maturities = sorted(p["maturity"] for p in items if is_future(p.get("maturity")))
    expired = sum(1 for p in items
                  if p.get("maturity") and not is_future(p["maturity"]))
    nearest = maturities[0] if maturities else None
    lenders = sorted({p.get("counterparty") for p in items if p.get("counterparty")})
    holders = sorted({p.get("holder") for p in items if p.get("holder")})
    amount = total_loan or collateral

    if total_loan:
        metric, situation = eok_text(total_loan), "대출금액 공시"
    elif collateral:
        metric, situation = f"담보가치 {eok_text(collateral)}", "대출금액 미공시"
    else:
        metric, situation = "미공시", "규모 미공시"

    return {
        **base(rec), **src(rec, items[0].get("rcp_no")),
        "reason": (f"대주주 주식담보대출 {eok_text(total_loan)}" if total_loan
                   else f"대주주 주식담보 계약 {len(items)}건"),
        "detail": " · ".join(x for x in [
            f"계약 {len(items)}건",
            f"담보주식 {total_shares:,}주" if total_shares else None,
            f"평가액 {eok_text(collateral)}" if collateral else None,
            f"차입처 {', '.join(lenders[:2])}{' 외' if len(lenders) > 2 else ''}"
            if lenders else None,
            f"보유자 {', '.join(holders[:2])}{' 외' if len(holders) > 2 else ''}"
            if holders else None] if x),
        "amount": amount,
        "metricText": metric,
        "ratio": None,
        "situation": situation,
        "contractCount": len(items),
        "dateValue": nearest,
        "timeText": (None if nearest
                     else ("만기 경과 · 연장 여부 확인" if expired else "만기 미공시")),
        "action": (f"만기 도래 담보대출 {eok_text(total_loan) or ''} 리파이낸싱 제안".replace("  ", " ")
                   if nearest else "담보대출 신규·증액 또는 연장 조건 제안"),
        "chips": ["주식담보"],
    }


def rows_employee(rec):
    sc = rec.get("stock_comp")
    if not sc:
        return None
    qty = sc.get("outstanding") or sc.get("granted")
    if qty is None:
        return None
    types = sc.get("types") or []
    value = shares_to_eok(rec, qty)   # 행사 시 임직원에게 생기는 자산 규모
    return {
        **base(rec), **src(rec),
        "reason": (f"{'/'.join(types) if types else '주식보상'} 미행사분 "
                   f"{eok_text(value)} 규모" if value else
                   f"{'/'.join(types) if types else '주식보상'} 미행사 {qty:,}주"),
        "detail": " · ".join(x for x in [
            f"{qty:,}주",
            f"행사가격 {sc['exercise_price']:,}원" if sc.get("exercise_price") else None,
            f"행사기간 {sc.get('exercise_start','')}~{sc.get('exercise_end','')}"
            if sc.get("exercise_start") else None] if x),
        "amount": value,
        "metricText": eok_text(value) or f"{qty:,}주",
        "ratio": None,
        "dateValue": sc.get("exercise_start"),
        "timeText": None,
        "compType": types[0] if types else None,
        "action": "임직원 계좌 개설·주식 매매·세무·자산관리 제안",
        "chips": ["주식보상"],
    }


def rows_wealth(rec):
    """구 대주주에게 얼마가 들어왔는지가 영업 규모다.

    지분율만으로는 제안이 안 된다. 이전 지분율 x 시가총액으로 매각대금을
    환산해야 '얼마짜리 운용 영업인지'가 나온다.
    """
    sh = rec.get("shareholders")
    if not sh or not sh.get("change_date"):
        return None  # 변경 이벤트가 있어야 자금 유입 가능성이 성립한다

    # 국민연금 등 기관의 지분율 변동으로 최대주주가 바뀐 건은 오너의 지분 매각이
    # 아니다. 개인 자금이 유입되지 않으므로 이 탭의 영업 대상이 아니다.
    prev = sh.get("prev_largest") or ""
    if any(k in prev for k in ("국민연금", "연기금", "자산운용", "투자신탁",
                               "은행", "보험", "공제회")):
        return None

    # 공시에 인수자금이 있으면 그게 곧 구 대주주에게 간 돈이다. 추정보다 정확하다.
    proceeds = to_eok(sh.get("deal_amount"), sh.get("unit") or "원")
    prev_ratio = sh.get("prev_ratio") or sh.get("largest_ratio")
    if proceeds is None:
        cap = meta(rec).get("market_cap")       # 억원
        proceeds = (round(cap * prev_ratio / 100, 1)
                    if cap and prev_ratio else None)
    debt = to_eok(sh.get("deal_debt"), sh.get("unit") or "원")

    return {
        **base(rec), **src(rec),
        "reason": (f"최대주주 변경 — 매각대금 {eok_text(proceeds)} 유입 추정"
                   if proceeds else
                   f"최대주주 변경 — {sh.get('prev_largest') or '구 대주주'} → "
                   f"{sh.get('largest') or ''}".strip()),
        "detail": " · ".join(x for x in [
            f"{sh.get('prev_largest') or '구 대주주'} → {sh.get('largest') or ''}".strip(" →"),
            f"지분 {prev_ratio}%" if prev_ratio else None,
            # 차입으로 인수했다면 신 대주주 쪽 담보대출 영업 기회이기도 하다
            f"차입 {eok_text(debt)}" if debt else None,
            sh.get("reason")] if x),
        "amount": proceeds,
        "metricText": eok_text(proceeds) or (f"지분 {prev_ratio}%" if prev_ratio else "-"),
        "ratio": prev_ratio,
        "dateValue": sh.get("change_date"),
        "timeText": None,
        "action": (f"매각대금 {eok_text(proceeds)} 랩·신탁·채권형 운용 제안"
                   if proceeds else "지분 매각대금 랩·신탁·채권형 운용 제안"),
        "chips": ["대주주변경"],
    }


# ----------------------------------------------------------------- 탭 정의

MONTH_OPTS = [["", "전체"], ["3", "3개월 이내"], ["6", "6개월 이내"], ["12", "12개월 이내"]]
AMOUNT_OPTS = [["", "전체"], ["1000", "1,000억 이상"], ["5000", "5,000억 이상"],
               ["10000", "1조 이상"]]

TABS = [
    {
        "key": "investment", "title": "금융상품 운용",
        "desc": "저수익 예치자금·운용상품 유치 영업",
        "upcoming": "갈아탈 수 있는 자금 규모가 가장 큰 영업 건입니다.",
        "upcomingBadge": "운용 자금 큰 순",
        "metricLabel": "영업 가능 자금",
        "timeColumn": {"label": "자금 성격", "mode": "range"},
        "sortOptions": [["amount", "자금 큰 순"], ["company", "기업명 순"]],
        "sortDefault": "amount",
        "filters": [
            {"label": "영업 가능 자금", "field": "amount", "type": "min",
             "options": AMOUNT_OPTS},
            {"label": "자금 성격", "field": "situation", "type": "eq",
             "options": [["", "전체"], ["예치자금 편중", "예치자금 편중"],
                         ["운용상품 보유", "운용상품 보유"], ["운용 여력", "운용 여력"]]},
        ],
        "builder": rows_investment,
    },
    {
        "key": "pension", "title": "퇴직연금",
        "desc": "적립금 운용 물량 기반 연금 영업",
        "upcoming": "가져올 수 있는 적립금 물량이 가장 큰 영업 건입니다.",
        "upcomingBadge": "운용 물량 큰 순",
        "metricLabel": "영업 가능 물량",
        "timeColumn": {"label": "영업 상황", "mode": "none"},
        "sortOptions": [["amount", "물량 큰 순"], ["ratio", "적립률 낮은 순"],
                        ["company", "기업명 순"]],
        "sortDefault": "amount",
        "filters": [
            {"label": "영업 가능 물량", "field": "amount", "type": "min", "options": AMOUNT_OPTS},
            {"label": "영업 상황", "field": "situation", "type": "eq",
             "options": [["", "전체"], ["추가적립 필요", "추가적립 필요"],
                         ["원금보장형 편중", "원금보장형 편중"],
                         ["운용기관 경쟁", "운용기관 경쟁"]]},
        ],
        "builder": rows_pension,
    },
    {
        "key": "blockdeal", "title": "자사주 처분",
        "desc": "자사주 처분 물량 블록딜 영업",
        "upcoming": "처분 예정일이 가장 가까운 자사주 영업 건입니다.",
        "upcomingBadge": "예정일 빠른 순",
        "metricLabel": "처분 규모",
        "timeColumn": {"label": "처분 예정일 / D-day", "mode": "dday"},
        "sortOptions": [["date", "예정일 빠른 순"], ["amount", "규모 큰 순"],
                        ["company", "기업명 순"]],
        "sortDefault": "date",
        "filters": [
            {"label": "이벤트 유형", "field": "eventType", "type": "eq",
             "options": [["", "전체"], ["처분", "처분"], ["취득", "취득"]]},
            {"label": "예정 시점", "field": "dateValue", "type": "withinMonths",
             "options": MONTH_OPTS},
        ],
        "builder": rows_blockdeal,
    },
    {
        "key": "pledge", "title": "주식담보대출",
        "desc": "대주주·특수관계인 담보대출 영업",
        "upcoming": "담보계약 만기가 가장 가까운 영업 건입니다.",
        "upcomingBadge": "만기 빠른 순",
        "metricLabel": "대출금액",
        "timeColumn": {"label": "계약 만기 / D-day", "mode": "dday"},
        "sortOptions": [["date", "만기 빠른 순"], ["amount", "금액 큰 순"],
                        ["company", "기업명 순"]],
        "sortDefault": "date",
        "filters": [
            {"label": "대출금액", "field": "amount", "type": "min",
             "options": [["", "전체"], ["100", "100억 이상"], ["500", "500억 이상"],
                         ["1000", "1,000억 이상"]]},
            {"label": "만기 시점", "field": "dateValue", "type": "withinMonths",
             "options": MONTH_OPTS},
        ],
        "builder": rows_pledge,
    },
    {
        "key": "employee", "title": "임직원 주식보상",
        "desc": "주식 지급·행사 시점 기반 임직원 자산관리",
        "upcoming": "행사 시작이 가장 가까운 임직원 자산관리 건입니다.",
        "upcomingBadge": "행사일 빠른 순",
        "metricLabel": "미행사 수량",
        "timeColumn": {"label": "행사 시작일 / D-day", "mode": "dday"},
        "sortOptions": [["date", "행사일 빠른 순"], ["company", "기업명 순"]],
        "sortDefault": "date",
        "filters": [{"label": "보상 유형", "field": "compType", "type": "eq",
                     "options": [["", "전체"], ["스톡옵션", "스톡옵션"], ["RSU", "RSU"],
                                 ["RSA", "RSA"], ["PSU", "PSU"]]}],
        "builder": rows_employee,
    },
    {
        "key": "wealth", "title": "대주주 자금운용",
        "desc": "지분 매각대금 운용 영업",
        "upcoming": "최대주주 변경으로 현금 유입 가능성이 최근 발생한 건입니다.",
        "upcomingBadge": "최근 발생 순",
        "metricLabel": "변경 후 지분",
        "timeColumn": {"label": "발생 후 경과", "mode": "elapsed"},
        "sortOptions": [["date", "최근 발생 순"], ["company", "기업명 순"]],
        "sortDefault": "date",
        "filters": [{"label": "발생 시점", "field": "dateValue", "type": "withinMonths",
                     "options": [["", "전체"], ["3", "최근 3개월"], ["6", "최근 6개월"],
                                 ["12", "최근 12개월"]]}],
        "builder": rows_wealth,
    },
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-date", default=date.today().isoformat())
    args = ap.parse_args()

    # 시총·주가·상장주식수·업종을 붙인다. 주식수를 금액으로 환산하는 데 필요하다.
    companies_file = ROOT / "data" / "companies.json"
    master_file = ROOT / "data" / "market_master.json"
    if companies_file.exists():
        master = dart.load_json(master_file) if master_file.exists() else {}
        for c in dart.load_json(companies_file)["companies"]:
            m = master.get(c["name"], {})
            MARKET_META[c["corp_code"]] = {
                "stock_code": c.get("stock_code") or m.get("stock_code"),
                "market": c.get("market") or m.get("market"),
                "industry": c.get("industry") or m.get("industry"),
                "market_cap": c.get("market_cap") or m.get("market_cap"),
                "price": m.get("price"),
                "shares": m.get("shares"),
            }
        priced = sum(1 for v in MARKET_META.values() if v.get("price"))
        print(f"시세 메타 {len(MARKET_META):,}개사 (주가 보유 {priced:,}개사)")

    files = sorted((ROOT / "extracted").glob("*.json"))
    if not files:
        raise SystemExit("extracted/*.json 이 없습니다. Phase 2를 먼저 수행하세요.")
    records = [dart.load_json(f) for f in files]
    total = len(records)
    print(f"추출 결과 {total}개사")

    tabs = []
    for spec in TABS:
        rows = []
        covered = 0
        for rec in records:
            built = spec["builder"](rec)
            if not built:
                continue
            covered += 1
            rows.extend(built if isinstance(built, list) else [built])

        tab = {k: v for k, v in spec.items() if k != "builder"}
        tab["coverage"] = {"covered": covered, "total": total}
        tab["rows"] = rows
        tabs.append(tab)
        print(f"  {spec['key']:11s} {covered:3d}/{total}개사  행 {len(rows):4d}건")

    dart.save_json(ROOT / "data" / "site_data.json", {
        "meta": {
            "baseDate": args.base_date,
            "companyCount": total,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "tabs": tabs,
    })
    print("\ndata/site_data.json 저장")


if __name__ == "__main__":
    main()
