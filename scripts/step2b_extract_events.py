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


def parse_pledge(text: str) -> list[dict]:
    """'나. 계약 내용' 표에서 담보성 계약만, '다.' 표에서 대출금액을 붙인다."""
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
            period = row.get("계약 기간", "")
            dates = re.findall(r"(\d{4})[.\-년]\s*(\d{1,2})[.\-월]\s*(\d{1,2})", period)
            maturity = (f"{dates[-1][0]}-{int(dates[-1][1]):02d}-{int(dates[-1][2]):02d}"
                        if len(dates) >= 2 else None)
            rows.append({
                "holder": row.get("성명 (명칭)"),
                "relation": row.get("보고자와의 관계"),
                "contract_type": kind,
                "counterparty": row.get("계약 상대방"),
                "shares": int(num(row.get("주식등의 수")) or 0) or None,
                "contract_date": parse_date(row.get("계약체결 (변경)일")),
                "maturity": maturity,
                "ratio": num(row.get("비율")),
                "loan_amount": None,
                "unit": "원",
            })

    # '다. 주요계약이 담보계약인 경우 추가 기재사항' — 대출금액·이자율
    tail = text.split("담보계약인 경우 추가 기재사항", 1)
    if len(tail) == 2 and rows:
        for line in tail[1].split("\n"):
            if not line.strip().startswith("|"):
                continue
            c = cells(line)
            if len(c) >= 4 and c[0].isdigit():
                loan = num(c[2])
                if loan:
                    i = int(c[0]) - 1
                    if 0 <= i < len(rows):
                        rows[i]["loan_amount"] = loan
                        rows[i]["debtor"] = c[3] if len(c) > 3 else None
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
