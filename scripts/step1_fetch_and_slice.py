"""Phase 1 — 공시 원문 수집 + 주석 섹션 잘라내기 (API 키 불필요).

DART 원문은 `<A name='tocN'>제목</A>` 앵커로 주석 섹션 경계를 명시한다.
회사마다 주석 번호·명칭·분량이 제각각이므로 고정 길이 윈도우로 자르지 않고
이 경계를 그대로 따라간다.

  1) 공시검색으로 회사별 최신 정기보고서 rcp_no
  2) 목차에서 '연결재무제표 주석' 노드 → 그 구간만 수신
  3) 앵커로 섹션 분할 → 주제별로 해당 섹션만 골라 slices/ 에 저장

값을 해석하거나 뽑지 않는다. 추출은 에이전트가 slices/ 를 직접 읽어서 한다.

산출: raw/{stock_code}/, slices/{stock_code}/{topic}.md
"""
import argparse
import sys

import dart
from dart import ROOT

# 주제별 섹션 선택 규칙.
#   title — 주석 제목에 이 중 하나가 있으면 그 섹션 채택 (주 경로)
#   body  — 제목으로 못 찾을 때만 쓰는 본문 키워드 (예: 삼성전자는 주식기준보상
#           별도 주석이 없고 '기타자본항목' 안에 들어 있다)
#   source "notes" — 재무제표 주석 안에서 섹션을 고른다
#   source "toc"   — 보고서 목차 노드를 직접 가져온다 (주석 밖에 있는 항목)
TOPICS = {
    "financial_assets": {
        # 영업에 필요한 값(상각후원가/FVOCI/FVTPL/단기금융상품 금액)은
        # '범주별 금융상품' 표 하나에 다 있다. 재무위험관리·공정가치 측정까지
        # 끌어오면 유동성·신용위험 표가 딸려와 분량만 몇 배가 된다.
        "source": "notes",
        "title": ["범주별 금융상품", "금융상품의 범주", "금융자산의 범주",
                  "금융상품 공정가치", "금융상품", "금융자산"],
        # '금융상품의 범주별 순손익', '금융자산의 신용건전성' 같은 섹션에는
        # 정작 범주별 금액표가 없다. 제목만 보고 집으면 엉뚱한 표를 가져온다.
        "exclude": ["순손익", "손익", "위험", "신용건전성", "사용이 제한", "양도",
                    "회계정책", "작성기준", "회계추정"],
        "body": ["당기손익-공정가치", "상각후원가"],
        "max_sections": 2,
    },
    "pension": {
        "source": "notes",
        "title": ["순확정급여", "확정급여", "종업원급여", "퇴직급여"],
        "body": ["확정급여채무", "사외적립자산"],
    },
    "stock_comp": {
        # 주식매수선택권 부여현황은 주석이 아니라 'VIII. 임원 및 직원 등'에 있다.
        # 주석 쪽 '주식기준보상'은 자본 누계액이라 부여 내역과 다르다.
        "source": "toc",
        "toc": ["임원 및 직원 등의 현황", "임원 및 직원 등에 관한 사항"],
        "require": ["주식매수선택권", "주식기준보상", "양도제한조건부주식"],
    },
    "shareholders": {
        "source": "toc",
        "toc": ["주주에 관한 사항"],
        "require": ["최대주주"],
    },
}

NOTE_PATTERNS = ["연결재무제표 주석", "재무제표 주석", "재무제표에 대한 주석"]
# body fallback 최소 히트 — 낮으면 '주당이익' 같은 무관한 섹션이 딸려온다
MIN_BODY_HITS = 3
TOPIC_MAX = 90_000   # 주제당 총 문자 상한 — 섹션 단위로만 자른다
MIN_SECTION = 300    # 제목만 있고 내용이 없는 껍데기 섹션 제외


def pick_sections(sections: list[dict], rule: dict) -> list[dict]:
    """주제에 해당하는 섹션들을 고른다. 길이를 추정해 자르지 않는다."""
    picked, seen = [], set()

    # 1) 제목 매칭 — 규칙에 적힌 순서가 우선순위
    excl = rule.get("exclude", [])
    for pat in rule["title"]:
        for i, s in enumerate(sections):
            if i in seen or len(s["text"]) < MIN_SECTION:
                continue
            if any(x in s["title"] for x in excl):
                continue
            if pat in s["title"]:
                seen.add(i)
                picked.append(s)

    # 2) 제목으로 하나도 못 찾을 때만 본문 키워드 fallback.
    #    임계치를 두지 않으면 키워드가 한두 번 스친 무관한 섹션이 딸려온다.
    if not picked:
        scored = []
        for i, s in enumerate(sections):
            if len(s["text"]) < MIN_SECTION:
                continue
            if any(x in s["title"] for x in excl):
                continue    # fallback 에도 제외 규칙을 적용해야 회계정책 서술이 안 걸린다
            n = sum(s["text"].count(kw) for kw in rule.get("body", []))
            if n >= MIN_BODY_HITS:
                scored.append((n, i, s))
        scored.sort(key=lambda x: -x[0])
        picked = [s for _, _, s in scored[:1]]

    # 상한을 넘으면 뒤쪽 섹션부터 버린다 (앞쪽이 우선순위가 높음)
    limit = rule.get("max_sections")
    out, total = [], 0
    for s in picked:
        if limit and len(out) >= limit:
            break
        if total + len(s["text"]) > TOPIC_MAX and out:
            break
        out.append(s)
        total += len(s["text"])
    return out


def focus_text(text: str, keywords: list[str], pad: int = 6_000) -> str:
    """내부 앵커가 없는 대형 노드(예: 임원현황 687KB) 전용 축약.

    섹션 경계가 없을 때만 쓰는 마지막 수단이다. 키워드 주변 구간만 남기고
    잘라낸 자리는 표시해서, 읽는 쪽이 생략을 알 수 있게 한다.
    """
    spans = []
    for kw in keywords:
        pos = 0
        while True:
            i = text.find(kw, pos)
            if i < 0:
                break
            spans.append((max(0, i - pad), min(len(text), i + pad)))
            pos = i + len(kw)  # 반드시 전진
    if not spans:
        return text[:TOPIC_MAX]

    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    parts, total = [], 0
    for s, e in merged:
        chunk = text[s:e]
        if total + len(chunk) > TOPIC_MAX:
            chunk = chunk[: max(0, TOPIC_MAX - total)]
        if chunk:
            parts.append(chunk)
            total += len(chunk)
        if total >= TOPIC_MAX:
            break
    return "\n\n[... 중략 ...]\n\n".join(parts)


def process_company(c: dict, force: bool = False) -> dict:
    code, name = c["corp_code"], c["name"]
    result = {"corp_code": code, "name": name, "topics": {}, "error": None}

    slice_dir = ROOT / "slices" / code
    if slice_dir.exists() and not force and any(slice_dir.glob("*.md")):
        result["skipped"] = True
        return result

    try:
        # step0 이 이미 최신 정기보고서를 찾아뒀다. 회사별 검색을 다시 하지 않는다.
        rpt = c.get("report")
        if not rpt or not rpt.get("rcp_no"):
            result["error"] = "companies.json 에 정기보고서 rcp_no 없음"
            return result
        result.update(rcp_no=rpt["rcp_no"], report_nm=rpt["report_nm"],
                      rcept_dt=rpt["rcept_dt"])

        toc = dart.get_toc(rpt["rcp_no"])
        raw_dir = ROOT / "raw" / code
        raw_dir.mkdir(parents=True, exist_ok=True)
        slice_dir.mkdir(parents=True, exist_ok=True)

        def write_slice(topic: str, body: str, origin: str) -> None:
            header = (
                f"# {name} ({code}) — {topic}\n"
                f"- 보고서: {rpt['report_nm']}\n"
                f"- 접수일: {rpt['rcept_dt']}\n"
                f"- 접수번호: {rpt['rcp_no']}\n"
                f"- 원문: {dart.dart_url(rpt['rcp_no'])}\n"
                f"- 출처: {origin}\n\n---\n\n")
            (slice_dir / f"{topic}.md").write_text(header + body, encoding="utf-8")

        # --- 주석 기반 주제
        node = dart.find_toc_node(toc, NOTE_PATTERNS)
        sections = []
        if node:
            result["note_section"] = node["title"]
            html = dart.get_section(node)
            (raw_dir / f"{rpt['rcp_no']}_notes.html").write_text(html, encoding="utf-8")
            sections = dart.split_sections(html)
            result["section_count"] = len(sections)

        for topic, rule in TOPICS.items():
            if rule["source"] != "notes":
                continue
            picked = pick_sections(sections, rule) if sections else []
            if not picked:
                result["topics"][topic] = None
                continue
            body = "\n\n".join(
                f"## {s['title']}\n\n{s['text']}" for s in picked)
            write_slice(topic, body, ", ".join(s["title"] for s in picked))
            result["topics"][topic] = {
                "sections": [s["title"] for s in picked], "chars": len(body)}

        # --- 목차 노드 기반 주제 (주석 밖에 있는 항목)
        for topic, rule in TOPICS.items():
            if rule["source"] != "toc":
                continue
            tnode = dart.find_toc_node(toc, rule["toc"])
            if not tnode:
                result["topics"][topic] = None
                continue
            thtml = dart.get_section(tnode)
            # 요구 키워드가 아예 없으면 그 회사엔 해당 제도가 없는 것이다.
            # 억지로 채우지 않고 null 로 둔다.
            if not any(kw in thtml for kw in rule["require"]):
                result["topics"][topic] = None
                continue
            text = dart.html_to_text(thtml)
            if len(text) > TOPIC_MAX:
                text = focus_text(text, rule["require"])
            write_slice(topic, f"## {tnode['title']}\n\n{text}", tnode["title"])
            result["topics"][topic] = {
                "sections": [tnode["title"]], "chars": len(text)}
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="앞에서 N개사만 (테스트용)")
    ap.add_argument("--force", action="store_true", help="기존 slices 무시하고 재생성")
    ap.add_argument("--only", help="특정 회사명만")
    args = ap.parse_args()

    f = ROOT / "data" / "companies.json"
    if not f.exists():
        sys.exit("data/companies.json 이 없습니다. step0_select_companies.py 를 먼저 실행하세요.")
    companies = dart.load_json(f)["companies"]
    if args.only:
        companies = [c for c in companies if c["name"] == args.only]
    if args.limit:
        companies = companies[: args.limit]

    print(f"정기보고서 주석 수집 ({len(companies)}개사)")
    log = []
    for i, c in enumerate(companies, 1):
        r = process_company(c, force=args.force)
        log.append(r)
        if r.get("skipped"):
            status = "skip"
        elif r["error"]:
            status = f"ERROR {r['error']}"
        else:
            got = [t for t, v in r["topics"].items() if v]
            status = f"{r.get('report_nm','')} · 섹션{r.get('section_count',0)} · {'+'.join(got) or '주제 없음'}"
        print(f"  {i}/{len(companies)} {c['name']} — {status}", flush=True)

    dart.save_json(ROOT / "data" / "fetch_log.json", log)
    ok = sum(1 for r in log if not r["error"])
    print(f"\n완료: {ok}/{len(log)}개사")
    for topic in TOPICS:
        n = sum(1 for r in log if r.get("topics", {}).get(topic))
        print(f"  {topic:20s} {n:3d}개사")


if __name__ == "__main__":
    main()
