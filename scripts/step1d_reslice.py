"""Phase 1-d — 이미 받아둔 원문에서 주석 조각을 다시 만든다 (네트워크 없음).

주제 선택 규칙(TOPICS)을 바꿀 때마다 원문을 다시 받을 이유가 없다.
raw/{corp_code}/*_notes.html 이 남아 있으므로 오프라인으로 다시 자른다.

주석 기반 주제(financial_assets, pension)만 대상이다.
목차 노드 기반 주제(stock_comp, shareholders)는 원문이 raw 에 없어 제외한다.
"""
import argparse

import dart
from dart import ROOT
from step1_fetch_and_slice import TOPICS, pick_sections


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", action="append",
                    help="다시 자를 주제. 생략하면 notes 기반 전체")
    args = ap.parse_args()

    targets = {k: v for k, v in TOPICS.items()
               if v["source"] == "notes" and (not args.topic or k in args.topic)}
    print(f"대상 주제: {', '.join(targets)}")

    companies = {c["corp_code"]: c
                 for c in dart.load_json(ROOT / "data" / "companies.json")["companies"]}

    done = skipped = 0
    before = after = 0
    for raw_dir in sorted((ROOT / "raw").iterdir()):
        if not raw_dir.is_dir():
            continue
        cc = raw_dir.name
        files = list(raw_dir.glob("*_notes.html"))
        if not files:
            skipped += 1
            continue
        c = companies.get(cc)
        if not c:
            skipped += 1
            continue

        html = files[0].read_text(encoding="utf-8", errors="replace")
        sections = dart.split_sections(html)
        slice_dir = ROOT / "slices" / cc
        slice_dir.mkdir(parents=True, exist_ok=True)
        rpt = c["report"]

        for topic, rule in targets.items():
            dest = slice_dir / f"{topic}.md"
            old = dest.stat().st_size if dest.exists() else 0
            picked = pick_sections(sections, rule)
            if not picked:
                if dest.exists():
                    dest.unlink()
                continue
            body = "\n\n".join(f"## {s['title']}\n\n{s['text']}" for s in picked)
            header = (
                f"# {c['name']} ({cc}) — {topic}\n"
                f"- 보고서: {rpt['report_nm']}\n"
                f"- 접수일: {rpt['rcept_dt']}\n"
                f"- 접수번호: {rpt['rcp_no']}\n"
                f"- 원문: {dart.dart_url(rpt['rcp_no'])}\n"
                f"- 출처: {', '.join(s['title'] for s in picked)}\n\n---\n\n")
            dest.write_text(header + body, encoding="utf-8")
            before += old
            after += dest.stat().st_size
        done += 1

    print(f"재분할 {done}개사 (원문 없음 {skipped}개사)")
    if before:
        print(f"  {before/1024/1024:.2f}MB -> {after/1024/1024:.2f}MB "
              f"({after/before*100:.0f}%)")


if __name__ == "__main__":
    main()
