"""Phase 1-b — 이벤트 공시 원문 수집 (자사주·담보·최대주주변경).

정기보고서 주석과 달리 이벤트 공시는 문서 자체가 작아서 통째로 텍스트화한다.

검색을 다시 하지 않는다. step0 이 남긴 data/cache/ 의 공시목록을 재사용하고,
선정된 100개사에 해당하는 원문만 받는다. (요청 수를 줄여 차단을 피하기 위함)

산출: slices/{corp_code}/events/{type}_{rcp_no}.md
"""
import argparse
import re
from collections import defaultdict

import dart
from dart import ROOT

# 캐시 폴더명 접두사 -> (이벤트 타입, 보고서명 확인 패턴)
EVENT_TYPES = {
    "treasury": re.compile(r"자기주식(취득|처분)결정"),
    "owner_change": re.compile(r"최대주주변경|최대주주등소유주식변동"),
    "pledge": re.compile(r"대량보유상황보고서|주식담보제공계약"),
}

# 회사·유형당 최신 N건까지만. 문서 1건에 요청 2회(목차+본문)가 드니
# 상한을 두지 않으면 수천 요청이 된다. 영업에는 최신 건이면 충분하다.
MAX_PER_TYPE = {"treasury": 2, "owner_change": 1, "pledge": 2}
DEFAULT_MAX = 1

# 유형별로 가져올 목차 노드.
# 대량보유보고서는 제1~3부로 나뉘어 있고 담보 계약은 '2. 보유주식등에 관한 계약'에
# 들어 있다. 가장 큰 노드(제3부 세부변동내역)를 집으면 담보 정보를 통째로 놓친다.
NODE_PREFS = {
    "pledge": ["보유주식등에 관한 계약", "보유주식등의 수 및 보유비율",
               "대량보유자에 관한 사항"],
}


def load_cached_rows() -> list[dict]:
    """step0 이 저장한 검색 결과 페이지를 전부 읽어들인다."""
    cache_root = ROOT / "data" / "cache"
    if not cache_root.exists():
        raise SystemExit("data/cache 가 없습니다. step0_select_companies.py 를 먼저 실행하세요.")
    rows = []
    for page_file in sorted(cache_root.rglob("*.json")):
        rows.extend(dart.load_json(page_file).get("rows", []))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="앞에서 N개사만")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    companies = dart.load_json(ROOT / "data" / "companies.json")["companies"]
    if args.limit:
        companies = companies[: args.limit]
    wanted = {c["corp_code"]: c for c in companies}
    print(f"대상 {len(wanted)}개사")

    # 회사 x 이벤트유형 별로 최신순 정리
    bucket: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in load_cached_rows():
        cc = r.get("corp_code")
        if cc not in wanted:
            continue
        for etype, pattern in EVENT_TYPES.items():
            if pattern.search(r.get("report_nm", "")):
                bucket[(cc, etype)].append(r)

    for key, rows in bucket.items():
        rows.sort(key=lambda r: r["rcept_dt"], reverse=True)
        # 같은 접수번호 중복 제거
        seen, uniq = set(), []
        for r in rows:
            if r["rcp_no"] in seen:
                continue
            seen.add(r["rcp_no"])
            uniq.append(r)
        bucket[key] = uniq[: MAX_PER_TYPE.get(key[1], DEFAULT_MAX)]

    total = sum(len(v) for v in bucket.values())
    print(f"받을 원문 {total:,}건 ({len({k[0] for k in bucket})}개사) "
          f"· 예상 요청 {total * 2:,}회")

    done, failed = 0, 0
    for (cc, etype), rows in sorted(bucket.items()):
        name = wanted[cc]["name"]
        out_dir = ROOT / "slices" / cc / "events"
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in rows:
            dest = out_dir / f"{etype}_{r['rcp_no']}.md"
            if dest.exists() and not args.force:
                done += 1
                continue
            try:
                toc = dart.get_toc(r["rcp_no"])
                if not toc:
                    raise RuntimeError("목차 없음")

                prefs = NODE_PREFS.get(etype)
                picked = []
                if prefs:
                    for pat in prefs:
                        for n in toc:
                            if pat in n["title"] and n["length"] and n not in picked:
                                picked.append(n)
                                break
                if not picked:
                    # 단일 문서 공시는 가장 큰 노드가 곧 본문이다
                    picked = [max(toc, key=lambda n: n["length"])]

                text = "\n\n".join(
                    f"## {n['title']}\n\n{dart.html_to_text(dart.get_section(n))}"
                    for n in picked)
                header = (
                    f"# {name} ({cc}) — {etype}\n"
                    f"- 보고서: {r['report_nm']}\n"
                    f"- 접수일: {r['rcept_dt']}\n"
                    f"- 접수번호: {r['rcp_no']}\n"
                    f"- 원문: {dart.dart_url(r['rcp_no'])}\n\n---\n\n")
                dest.write_text(header + text, encoding="utf-8")
                done += 1
                print(f"  [{done + failed}/{total}] {name} {etype} "
                      f"{r['rcept_dt']} — {len(text):,}자", flush=True)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  [{done + failed}/{total}] {name} {etype} "
                      f"— ERROR {type(exc).__name__}: {exc}", flush=True)

    print(f"\n완료 {done:,}건 · 실패 {failed:,}건")


if __name__ == "__main__":
    main()
