"""증권신고서 '가. 인수방법에 관한 사항' 표에서 증권사별 인수 몫을 읽는다.

표의 인수금액은 신고서 버전에 따라 희망공모가 하단 기준일 때도, 확정공모가 기준일 때도 있다.
그래서 금액 자체를 쓰지 않고 **비율**만 쓴다 (증권사별 인수금액 ÷ 표의 합계). 실제 인수금액은
이 비율에 거래소가 공시한 확정 공모금액을 곱해 구한다.
"""
import re

from step6b_build_lockup import BROKER_HINT, ROLE_RE, ROLE_KEY, clean_broker

_AMT_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3}){2,})(?![\d,])\s*(원)?")
_SHR_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\s*주(?!\w)")
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


_HEAD_RE = re.compile(r"(?m)^\s*(?:\d+\.|[가-힣]\.)?\s*인수\s*등에\s*관한\s*사항\s*$")


def underwriting_text(dart, rcp_no: str) -> str:
    """신고서에서 '인수 등에 관한 사항' 절을 가져온다.

    목차에 그 절이 따로 있으면 그것을, 없으면(2022년 상반기까지의 서식) 상위 절을 받아
    제목 줄에서부터 잘라낸다. 본문 중의 참조 문구('…인수 등에 관한 사항 참조')가 아니라
    제목 줄을 찾아야 표가 잘리지 않는다.
    """
    toc = dart.get_toc(rcp_no)
    node = dart.find_toc_node(toc, ["인수 등에 관한 사항"])
    if node:
        text = dart.html_to_text(dart.get_section(node))
        if len(text) > 200:
            return text
    cands = [dart.find_toc_node(toc, [pat]) for pat in
             ("모집 또는 매출에 관한 일반사항", "모집 또는 매출에 관한 사항")]
    if len(toc) <= 2:                       # '증권발행조건확정'처럼 목차 없이 통째로 된 문서
        cands += sorted(toc, key=lambda n: -n["length"])[:1]
    for node in cands:
        if not node:
            continue
        whole = dart.html_to_text(dart.get_section(node))
        m = list(_HEAD_RE.finditer(whole))
        if m:
            return whole[m[-1].start():]
        i = whole.find("인수방법에 관한 사항")
        if i >= 0:
            return whole[i:]
    return ""


def _num(s):
    return int(s.replace(",", ""))


def parse_allocations(text: str) -> list[dict]:
    """인수인 표는 '인수대가에 관한 사항' 앞에 있다. 그 문구가 절 첫머리의 안내문에도
    나오는 신고서가 있어, 앞에서부터 차례로 끊어 보며 표가 읽히는 첫 구간을 쓴다."""
    cuts = [m.start() for m in re.finditer(r"인수\s*대가에\s*관한\s*사항", text)]
    for cut in cuts or [len(text)]:
        got = _parse_head(text[:cut])
        if got:
            return got
    return []


def _parse_head(head: str) -> list[dict]:
    rows, role = [], None
    for line in head.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) < 3 or re.match(r"^\(?주\s*\d*\)?", c[0]):
            continue
        name_i = None
        m = ROLE_RE.match(c[0].replace(" ", ""))
        if m and BROKER_HINT.search(c[1]):
            role, name_i = ROLE_KEY[m.group(1).replace(" ", "")], 1
        elif BROKER_HINT.search(c[0]) and len(c[0]) <= 30 and not re.search(r"\d{3}", c[0]):
            name_i = 0
        if name_i is None:
            continue
        b = clean_broker(c[name_i])
        if not b or not BROKER_HINT.search(b):
            continue
        rest = " | ".join(c[name_i + 1:])
        amts = [_num(x[0]) for x in _AMT_RE.findall(rest) if x[1] or _num(x[0]) >= 100_000_000]
        amts = [a for a in amts if a >= 10_000_000]
        shares = [_num(x) for x in _SHR_RE.findall(rest)]
        pct = [float(x) for x in _PCT_RE.findall(rest) if 0 < float(x) <= 100]
        # 금액 칸에 '원'이 붙은 값을 우선한다. 주소의 숫자나 주식수가 섞이지 않게.
        won = [_num(x[0]) for x in _AMT_RE.findall(rest) if x[1]]
        amount = max(won) if won else (max(amts) if amts else None)
        rows.append({"broker": b, "role": role, "amount": amount,
                     "shares": max(shares) if shares else None,
                     "pct": pct[0] if pct else None})
    # 같은 증권사가 두 번 나오면(표가 반복 인용됨) 첫 값만
    seen, out = set(), []
    for r in rows:
        if r["broker"] in seen:
            continue
        seen.add(r["broker"])
        out.append(r)
    for key in ("amount", "shares", "pct"):
        if out and all(r[key] for r in out):
            tot = sum(r[key] for r in out)
            for r in out:
                r["ratio"] = r[key] / tot
                r["ratio_basis"] = {"amount": "인수금액", "shares": "인수수량", "pct": "인수비율"}[key]
            return out
    return []
