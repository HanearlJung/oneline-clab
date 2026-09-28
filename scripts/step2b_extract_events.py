"""Phase 2-a — 이벤트 공시에서 값 추출.

이벤트 공시(자기주식 처분결정·대량보유상황보고·최대주주변경)는 금융위 서식이
고정이라 항목명이 모든 회사에서 같다. 그래서 항목명으로 집는다.

주석(금융자산·퇴직연금)은 회사마다 표 구조가 달라 이렇게 못 한다. 그쪽은 사람이 읽는다.

산출: extracted/{corp_code}.json 의 treasury / pledge / shareholders 를 갱신
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from dart import ROOT, load_json, save_json


def num(s):
    """'4,026,318,000' -> 4026318000. 값이 없으면 None."""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "0원", "해당사항없음"):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def find_value(text: str, label: str, idx: int = 0):
    """'| 1. 처분예정주식(주) | 보통주식 | 22,812 |' 같은 줄에서 값을 집는다."""
    for line in text.split("\n"):
        if label in line and "|" in line:
            c = cells(line)
            for i, v in enumerate(c):
                if label in v:
                    rest = c[i + 1:]
                    vals = [x for x in rest if x not in ("", "-")]
                    if idx < len(vals):
                        return vals[idx]
    return None


def find_number(text: str, label: str):
    """라벨 뒤 첫 '숫자' 값. 서식상 '보통주식' 같은 구분값이 앞에 끼어든다."""
    for line in text.split("\n"):
        if label in line and "|" in line:
            c = cells(line)
            for i, v in enumerate(c):
                if label in v:
                    for x in c[i + 1:]:
                        n = num(x)
                        if n is not None:
                            return n
    return None


def parse_date(s):
    """'2026년 02월 25일' / '2026-02-25' -> '2026-02-25'."""
    if not s:
        return None
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(s))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


# ------------------------------------------------------------------ 자사주

def parse_treasury(text: str) -> dict | None:
    etype = "처분" if "자기주식 처분 결정" in text or "처분예정주식" in text else "취득"
    key = "처분" if etype == "처분" else "취득"

    shares = find_number(text, f"{key}예정주식(주)")
    amount = find_number(text, f"{key}예정금액(원)")
    if shares is None and amount is None:
        return None

    period = None
    for line in text.split("\n"):
        if f"{key}예정기간" in line:
            period = cells(line)
            break
    start = parse_date(period[-1]) if period else None
    end = None
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if f"{key}예정기간" in line and i + 1 < len(lines) and "종료일" in lines[i + 1]:
            end = parse_date(cells(lines[i + 1])[-1])

    method = None
    for label in ("시간외대량매매", "장외처분", "시장을 통한 매도", "기 타"):
        if find_number(text, label):
            method = label.replace(" ", "")
            break

    # 보유 자사주 = 향후 처분 가능 물량. 개별 처분건보다 큰 영업 단서다.
    held = find_number(text, f"{key} 전 자기주식 보유현황")

    return {
        "event_type": etype,
        "unit": "원",
        "shares": int(shares) if shares else None,
        "amount": amount,
        "period_start": start,
        "period_end": end,
        "purpose": (find_value(text, f"{key}목적") or "").strip() or None,
        "method": method,
        "held_shares": int(held) if held else None,
    }


# -------------------------------------------------------------------- 담보

CONTRACT_HEAD = "계약의 종류"


_DATE_RE = re.compile(r"(?<!\d)(\d{4}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_REL_RE = re.compile(r"^(?:계약\s*체결일로부터\s*)?(?:(\d{1,2})\s*년)?\s*(?:(\d{1,3})\s*개월)?$")


def _iso(y: str, m: str, d: str) -> str | None:
    y = int(y) + 2000 if len(y) == 2 else int(y)
    m, d = int(m), int(d)
    if not (1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y}-{m:02d}-{d:02d}"


def parse_period(period: str, signed: str | None) -> tuple[str | None, str | None]:
    """'계약 기간' 칸에서 만기를 읽는다. 반환: (만기, 근거)

    근거는 '기재'(칸에 날짜가 적혀 있음) 또는 '계산'(체결일 + 기재된 기간).
    '자동연장', '해지시까지'처럼 끝이 정해지지 않은 계약은 만기를 만들지 않는다.

      19.07.08~26.08.26 / 2026.04.27 ~ 2027.04.27   → 뒤 날짜
      2027.02.25 / 2026.08.31까지 / ~2027.07.14      → 그 날짜
      2026.07.15 ~ 상환시까지 / 2025.09.30 ~         → 없음 (앞 날짜는 시작일)
      1년 / 60개월                                    → 체결일 + 기간
    """
    p = (period or "").strip()
    if not p:
        return None, None
    found = [(m.start(), _iso(*m.groups())) for m in _DATE_RE.finditer(p)]
    found = [(pos, d) for pos, d in found if d]
    if len(found) >= 2:
        return found[-1][1], "기재"
    if len(found) == 1:
        pos, d = found[0]
        tail = _DATE_RE.sub("", p, count=1)[pos:].strip()
        if pos == 0 and tail.startswith("~"):      # 시작일만 있고 끝은 열려 있다
            return None, None
        if signed and d <= signed:                  # 체결일과 같거나 앞서면 만기가 아니다
            return None, None
        return d, "기재"
    m = _REL_RE.match(p.replace(" ", ""))
    if m and signed and (m.group(1) or m.group(2)):
        months = int(m.group(1) or 0) * 12 + int(m.group(2) or 0)
        if 0 < months <= 120:
            y, mo, d = (int(x) for x in signed.split("-"))
            mo += months
            y, mo = y + (mo - 1) // 12, (mo - 1) % 12 + 1
            d = min(d, 28 if mo == 2 else 30 if mo in (4, 6, 9, 11) else 31)
            return f"{y}-{mo:02d}-{d:02d}", "계산"
    return None, None


def signed(s) -> float | None:
    """부호 있는 수. 해지 행은 '- 1,069,112', '△1,069,112', '(1,069,112)' 로 적힌다."""
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group())
    neg = bool(re.match(r"^\s*(-|△|▲|\()", t))
    return -v if neg else v


_UNIT = {"억원": 1e8, "억": 1e8, "백만원": 1e6, "백만": 1e6, "천원": 1e3, "원": 1}


def money(cell: str, table_unit: float = 1) -> float | None:
    """대출금액 칸. '30억원', '3,000백만원'처럼 칸 안에 단위가 붙는 경우가 있다.

    외화 표기는 원화로 바꿀 근거가 없으므로 읽지 않는다.
    """
    c = (cell or "").replace(",", "").strip()
    if not c or re.search(r"USD|US\$|\$|달러|엔|EUR|JPY|CNY|위안", c, re.I):
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(억원|억|백만원|백만|천원|원)?", c)
    if not m:
        return None
    v = float(m.group(1))
    if v <= 0:
        return None
    return v * (_UNIT[m.group(2)] if m.group(2) else table_unit)


def parse_pledge(text: str) -> list[dict]:
    """'나. 계약 내용' 표에서 담보성 계약만, '다.' 표에서 대출금액을 붙인다.

    서식은 고정이지만 기재 관행이 갈린다. 실제 공시에서 확인한 것들:
      - 연번이 '1-1', '1-2' 처럼 가지번호다. 한 담보에 대출이 여러 건이면 둘째부터
        주식수가 0 이다 (추가 담보 없이 같은 주식으로 빌린 것).
      - 해지·보고자 변경은 음수 주식수 행으로 적는다. 앞의 양수 행과 상쇄해야 한다.
      - '다.' 표의 연번은 '나.' 표의 연번을 가리킨다. 순서(위치)로 맞추면 담보가 아닌
        계약이 섞인 문서에서 어긋난다.
    """
    rows, header = [], None
    for line in text.split("\n"):
        if not line.strip().startswith("|"):
            continue
        c = cells(line)
        if CONTRACT_HEAD in c:
            header = c
            continue
        if header and len(c) >= len(header) - 2:
            row = dict(zip(header, c))
            kind = row.get(CONTRACT_HEAD, "")
            # 양수도·공동보유 계약은 담보가 아니다. 담보성만 남긴다.
            if not any(k in kind for k in ("담보", "질권", "대차", "신탁")):
                continue
            # 세무서 납세담보(연부연납 공탁)는 대출이 아니다
            if re.search(r"공탁|납세", kind):
                continue
            period = row.get("계약 기간", "")
            signed_on = parse_date(row.get("계약체결 (변경)일"))
            maturity, maturity_src = parse_period(period, signed_on)
            ratio = signed(row.get("비율"))
            rows.append({
                "_no": (row.get("연번") or "").strip(),
                "holder": row.get("성명 (명칭)"),
                "relation": row.get("보고자와의 관계"),
                "contract_type": kind,
                "counterparty": row.get("계약 상대방"),
                "shares": int(signed(row.get("주식등의 수")) or 0) or None,
                "contract_date": signed_on,
                "maturity": maturity,
                "maturity_src": maturity_src,
                "period_raw": period.strip() or None,
                "ratio": ratio if ratio else None,
                "loan_amount": None,
                "interest_rate": None,
                "maintenance_ratio": None,
                "unit": "원",
                "note": (row.get("비고") or "").strip() or None,
            })

    # 음수 행 = 해지·이관. 같은 사람·같은 기관의 양수 행에서 덜어낸다.
    for neg in [r for r in rows if r["shares"] and r["shares"] < 0]:
        same = [r for r in rows if r["shares"] and r["shares"] > 0
                and r["holder"] == neg["holder"] and r["counterparty"] == neg["counterparty"]]
        exact = [r for r in same if r["shares"] == -neg["shares"]]
        if exact:
            exact[0]["shares"] = 0
        elif same:
            big = max(same, key=lambda r: r["shares"])
            big["shares"] = max(big["shares"] + neg["shares"], 0)
            if big["ratio"] and neg["ratio"]:
                big["ratio"] = round(max(big["ratio"] + neg["ratio"], 0), 2) or None
        neg["shares"] = 0
        neg["_void"] = True
    for r in rows:
        if r["shares"] == 0 and r.get("_void") is None and r["_no"] and "-" not in r["_no"]:
            r["_void"] = True          # 상쇄되어 0 이 된 계약
    rows = [r for r in rows if not r.get("_void")]
    for r in rows:
        r["shares"] = r["shares"] or None

    # '다. 주요계약이 담보계약인 경우 추가 기재사항' — 대출금액·이자율·담보유지비율
    tail = text.split("담보계약인 경우 추가 기재사항", 1)
    if len(tail) == 2 and rows:
        body = tail[1].split("\n## ", 1)[0]
        # '(단위 : 주, 백만원, %)' 처럼 여러 단위가 한 줄에 섞여 온다. 금액 단위만 고른다.
        tunit = 1
        um = re.search(r"단위[^\n|]{0,40}", body[:500])
        if um:
            mu = re.search(r"억원|백만원|천원", um.group())
            if mu:
                tunit = _UNIT[mu.group()]
        by_no = {r["_no"]: r for r in rows if r["_no"]}
        for line in body.split("\n"):
            if not line.strip().startswith("|"):
                continue
            c = cells(line)
            if len(c) < 4 or not re.match(r"^\d+(-\d+)?$", c[0]):
                continue
            loan = money(c[2], tunit)
            if not loan:
                continue
            # 이 표의 연번은 '나.' 표의 연번일 때도, 담보계약만 다시 센 순번일 때도 있다.
            # 주식수가 적혀 있으면 주식수로 맞추는 것이 확실하다. 연번은 그다음이다.
            sh = int(num(c[1]) or 0)
            cand = [r for r in rows if r["loan_amount"] is None and sh and r["shares"] == sh]
            if len(cand) > 1:
                cand = [r for r in cand if r["_no"] == c[0]] or cand
            target = cand[0] if cand else None
            if target is None and not sh:
                target = by_no.get(c[0])
                if target is not None and target["loan_amount"] is not None:
                    target = None
            if target is None:
                continue
            target["loan_amount"] = loan
            target["debtor"] = c[3] if c[3] not in ("", "-", "상동", "동일") else None
            if len(c) > 4:
                target["interest_rate"] = num(c[4])
            if len(c) > 5:
                target["maintenance_ratio"] = num(c[5])

    # 주식수도 대출금액도 없는 행은 계약으로 볼 근거가 없다
    rows = [r for r in rows if r["shares"] or r["loan_amount"]]
    for r in rows:
        if r["ratio"] is not None and r["ratio"] <= 0:
            r["ratio"] = None
    for r in rows:
        r.pop("_no", None)
    return rows


def parse_pledge_exchange(text: str) -> list[dict]:
    """거래소 공시 '최대주주 변경을 수반하는 주식 담보제공 계약 체결'.

    대량보유보고서와 서식이 다르다. '[개별 담보제공 계약에 관한 사항]' 표에
    채권자·담보설정금액·담보제공기간(시작·종료)이 계약 단위로 들어 있다.

    이 표의 금액은 '담보설정금액'이지 대출금액이 아니다(통상 대출의 110~130%).
    대출금액은 문서 전체의 '채무(차입)금액 총액'으로만 공시되므로, 계약이 1건일 때만
    대출금액을 확정한다. 여러 건이면 나누어 추정하지 않고 비워 둔다.
    """
    holder = find_value(text, "명칭(성명, 법인명, 조합명, 단체명)")
    total_debt = find_number(text, "채무(차입)금액 총액")
    part = text.split("[개별 담보제공 계약에 관한 사항]", 1)
    if len(part) < 2:
        return []
    body = part[1].split("[개별 담보제공 계약의 담보권 실행 조건]", 1)[0]
    rows = []
    for line in body.split("\n"):
        if not line.strip().startswith("|"):
            continue
        c = cells(line)
        if len(c) < 10 or not c[0].isdigit():
            continue
        if re.search(r"공탁|납세", c[5] + c[3]) or "세무서" in c[1]:
            continue                    # 납세담보는 대출이 아니다
        dates = [parse_date(x) for x in c[-3:]]
        start, end, signed = dates
        rows.append({
            "holder": holder,
            "relation": "최대주주",
            "contract_type": c[5] or None,
            "purpose": c[3] or None,
            "counterparty": c[1] or None,
            "shares": int(num(c[6]) or 0) or None,
            "contract_date": signed or start,
            "maturity": end,
            "ratio": None,
            "loan_amount": None,
            "collateral_set_amount": num(c[4]),
            "unit": "원",
            "debtor": c[2] or None,
        })
    n_all = sum(1 for line in body.split("\n")
                if line.strip().startswith("|") and cells(line)[0].isdigit())
    if len(rows) == 1 and n_all == 1 and total_debt:
        rows[0]["loan_amount"] = total_debt
    for r in rows:
        r["maturity_src"] = "기재" if r["maturity"] else None
    return rows


# ---------------------------------------------------------------- 최대주주

def parse_owner_change(text: str) -> dict | None:
    """'최대주주 변경' 공시 서식.

        | 1. 변경내용 | 변경전 | 최대주주등 | (주)SNT홀딩스 외 1인 |
        |   소유비율(%) | 21.26 |
        | 변경후 | 최대주주등 | SNT모티브(주) 외 1인 |
        | -인수자금 조달방법 | 자기자금(원) | 60,587,840,880 |

    구 대주주에게 실제로 간 돈은 '인수자금'이다. 지분율 x 시총 추정보다 정확하다.
    """
    # --- 서식 B: '최대주주 변경을 수반하는 주식양수도 계약 체결'
    #     양도인/양수인으로 적히고 '양수도 대금'이 곧 매각대금이다.
    if "양수도 대금" in text or "-양도인" in text:
        seller = buyer = None
        for line in text.split("\n"):
            c = cells(line)
            for i, v in enumerate(c):
                if v.strip() in ("-양도인", "양도인") and i + 1 < len(c):
                    seller = seller or c[i + 1].strip()
                if v.strip() in ("-양수인", "양수인") and i + 1 < len(c):
                    buyer = buyer or c[i + 1].strip()
        buyer = buyer or find_value(text, "3. 변경예정 최대주주")
        deal = find_number(text, "양수도 대금(원)")
        ratio = find_number(text, "예정 소유비율")
        date = (parse_date(find_value(text, "-변경 예정일자"))
                or parse_date(find_value(text, "변경 예정일자"))
                or parse_date(find_value(text, "4. 계약일자")))
        if any([seller, buyer, deal, date]):
            return {
                "largest": buyer, "prev_largest": seller,
                "largest_ratio": ratio, "prev_ratio": None,
                "change_date": date,
                "deal_amount": deal, "deal_own": None, "deal_debt": None,
                "unit": "원",
                "reason": "최대주주 변경을 수반하는 주식양수도 계약 체결",
            }

    # --- 서식 A: '최대주주변경'
    before = after = None
    before_ratio = after_ratio = None
    state = None
    for line in text.split("\n"):
        if not line.strip().startswith("|"):
            continue
        c = cells(line)
        joined = " ".join(c)
        if "변경전" in joined:
            state = "before"
        elif "변경후" in joined:
            state = "after"

        if "최대주주등" in joined:
            for i, v in enumerate(c):
                if "최대주주등" in v and i + 1 < len(c):
                    name = c[i + 1].strip()
                    if name and name != "-":
                        if state == "before":
                            before = name
                        elif state == "after":
                            after = name
        if "소유비율" in joined:
            r = find_number(" | ".join(c), "소유비율")
            if r is not None:
                if state == "before" and before_ratio is None:
                    before_ratio = r
                elif state == "after":
                    after_ratio = r

    date = (parse_date(find_value(text, "4. 변경일자"))
            or parse_date(find_value(text, "변경일자"))
            or parse_date(find_value(text, "변경 예정일자")))
    own = find_number(text, "자기자금(원)") or 0
    debt = find_number(text, "차입금(원)") or 0
    deal = (own + debt) or None

    if not any([after, before, after_ratio, date]):
        return None
    return {
        "largest": after, "prev_largest": before,
        "largest_ratio": after_ratio, "prev_ratio": before_ratio,
        "change_date": date,
        "deal_amount": deal, "deal_own": own or None, "deal_debt": debt or None,
        "unit": "원",
        "reason": (find_value(text, "2. 변경사유") or "").strip() or None,
    }


# -------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="특정 corp_code 만")
    args = ap.parse_args()

    by_corp = defaultdict(lambda: {"treasury": [], "pledge": [], "owner": []})
    for f in sorted(Path(ROOT / "slices").glob("*/events/*.md")):
        cc = f.parts[-3]
        if args.only and cc != args.only:
            continue
        text = f.read_text(encoding="utf-8")
        rcp = re.search(r"접수번호: (\d+)", text)
        rcp = rcp.group(1) if rcp else None
        try:
            if f.name.startswith("treasury_"):
                r = parse_treasury(text)
                if r:
                    by_corp[cc]["treasury"].append({**r, "rcp_no": rcp})
            elif f.name.startswith("pledge_"):
                for r in parse_pledge(text):
                    by_corp[cc]["pledge"].append({**r, "rcp_no": rcp})
            elif f.name.startswith("owner_change_"):
                r = parse_owner_change(text)
                if r:
                    by_corp[cc]["owner"].append({**r, "rcp_no": rcp})
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.name}: {type(exc).__name__} {exc}")

    n_t = n_p = n_o = 0
    for cc, data in by_corp.items():
        path = ROOT / "extracted" / f"{cc}.json"
        rec = load_json(path) if path.exists() else {"corp_code": cc}
        if data["treasury"]:
            rec["treasury"] = data["treasury"]
            n_t += 1
        if data["pledge"]:
            rec["pledge"] = data["pledge"]
            n_p += 1
        if data["owner"]:
            latest = sorted(data["owner"],
                            key=lambda r: r.get("change_date") or "")[-1]
            sh = rec.get("shareholders") or {}
            sh.update({k: v for k, v in latest.items() if v is not None})
            rec["shareholders"] = sh
            n_o += 1
        if path.exists():
            save_json(path, rec)

    print(f"이벤트 추출 — 대상 {len(by_corp)}개사")
    print(f"  자사주 {n_t}개사 · 담보 {n_p}개사 · 최대주주변경 {n_o}개사")
    print("  (extracted/*.json 이 이미 있는 회사만 반영됨)")


if __name__ == "__main__":
    main()
