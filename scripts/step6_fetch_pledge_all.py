"""Phase 6 — 전체 상장사 담보계약 원문 수집.

100개사 PoC(step2)와 달리 대상을 고르지 않는다. 대량보유상황보고서(일반)를
기간 전체로 검색해, (회사, 제출인)별 최신 1건의 '보유주식등에 관한 계약'을 받는다.

  - 같은 계약이 후속 보고서에 반복 기재되므로 제출인별 최신분이 현재 상태다.
  - 시가총액 큰 회사부터 받는다. 중간에 끊겨도 중요한 회사는 들어가 있다.
  - 받은 파일은 건너뛴다. 중단 후 다시 돌리면 이어서 진행한다.

수집 경로는 DART_BASE 환경변수로 바꾼다 (dart.py).

산출: slices/{corp_code}/events/pledge_{rcp_no}.md   (step2 와 같은 형식)
      data/pledge_manifest.json                        (수집 대상·결과)
"""
import argparse
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import dart
from dart import ROOT
from step2_fetch_events import NODE_PREFS

REPORT = "주식등의대량보유상황보고서(일반)"
# 거래소 수시공시. 문서 1건이 곧 이벤트 1건이고 계약별 만기가 표로 들어 있다.
EXCHANGE_REPORT = "최대주주변경을수반하는주식담보제공계약체결"
KINDS = {
    "majorstock": dict(public_type=dart.PT_MAJORSTOCK, report=REPORT, latest_per_filer=True),
    "exchange": dict(public_type=dart.PT_EXCHANGE, report=EXCHANGE_REPORT, latest_per_filer=False),
}
LISTED = {"KOSPI", "KOSDAQ"}
_lock = threading.Lock()


def year_ranges(start: str, end: str) -> list[tuple[str, str]]:
    """검색 기간을 연 단위로 끊는다. 한 번에 길게 잡으면 페이지가 수백 장이 된다."""
    out, y = [], int(start[:4])
    while y <= int(end[:4]):
        s = max(start, f"{y}0101")
        e = min(end, f"{y}1231")
        out.append((s, e))
        y += 1
    return out


def collect_list(start: str, end: str, kind: dict) -> list[dict]:
    rows = []
    for s, e in year_ranges(start, end):
        got = dart.search_all(
            kind["public_type"], s, e, report_name=kind["report"],
            cache_key=f"ALL_{kind['public_type']}_{kind['report']}_{s}_{e}",
            on_page=lambda p, t, n, s=s: print(f"  목록 {s[:4]} {p}/{t}p · 누적 {n:,}", flush=True))
        rows.extend(got)
    return rows


def pick_targets(rows: list[dict], master_by_code: dict, kind: dict) -> list[dict]:
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["market"] not in LISTED or not r.get("corp_code"):
            continue
        if kind["report"] not in r["report_nm"].replace(" ", ""):
            continue
        # 거래소 공시는 건건이 다른 계약이다. 접수번호로 구분해 전부 받는다.
        k = ((r["corp_code"], r["filer"]) if kind["latest_per_filer"]
             else (r["corp_code"], r["rcp_no"]))
        if k not in latest or r["rcp_no"] > latest[k]["rcp_no"]:
            latest[k] = r
    out = list(latest.values())
    cap = lambda r: (master_by_code.get(r["corp_name"]) or {}).get("market_cap") or 0
    out.sort(key=lambda r: (-cap(r), r["corp_name"], r["rcp_no"]))
    return out


def fetch_one(r: dict, force: bool) -> tuple[str, int]:
    out_dir = ROOT / "slices" / r["corp_code"] / "events"
    dest = out_dir / f"pledge_{r['rcp_no']}.md"
    if dest.exists() and not force:
        return "skip", 0
    toc = dart.get_toc(r["rcp_no"])
    if not toc:
        raise RuntimeError("목차 없음")
    picked = []
    for pat in NODE_PREFS["pledge"]:
        for n in toc:
            if pat in n["title"] and n["length"] and n not in picked:
                picked.append(n)
                break
    if not picked:
        picked = [max(toc, key=lambda n: n["length"])]
    # 계약 섹션만 있으면 된다. 나머지 두 노드는 받지 않아 요청 수를 줄인다.
    picked = picked[:1]
    text = "\n\n".join(
        f"## {n['title']}\n\n{dart.html_to_text(dart.get_section(n))}" for n in picked)
    header = (
        f"# {r['corp_name']} ({r['corp_code']}) — pledge\n"
        f"- 보고서: {r['report_nm']}\n"
        f"- 제출인: {r['filer']}\n"
        f"- 접수일: {r['rcept_dt']}\n"
        f"- 접수번호: {r['rcp_no']}\n"
        f"- 원문: {dart.dart_url(r['rcp_no'])}\n\n---\n\n")
    with _lock:
        out_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(header + text, encoding="utf-8")
    return "ok", len(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--kind", choices=list(KINDS), default="majorstock")
    args = ap.parse_args()
    kind = KINDS[args.kind]

    master = dart.load_json(ROOT / "data" / "market_master.json")
    rows = collect_list(args.start, args.end, kind)
    targets = pick_targets(rows, master, kind)
    if args.limit:
        targets = targets[: args.limit]
    cos = {t["corp_code"] for t in targets}
    print(f"목록 {len(rows):,}건 → 수집 대상 {len(targets):,}건 ({len(cos):,}개사)", flush=True)

    dart.save_json(ROOT / "data" / f"pledge_manifest_{args.kind}.json", {
        "period": [args.start, args.end], "report": kind["report"],
        "list_rows": len(rows), "targets": targets})
    if args.list_only:
        return

    stat = defaultdict(int)
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, t, args.force): t for t in targets}
        for i, f in enumerate(as_completed(futs), 1):
            t = futs[f]
            try:
                st, n = f.result()
                stat[st] += 1
            except Exception as exc:  # noqa: BLE001
                stat["fail"] += 1
                failed.append({**t, "error": f"{type(exc).__name__}: {exc}"[:200]})
            if i % 50 == 0 or i == len(targets):
                print(f"  [{i:,}/{len(targets):,}] ok {stat['ok']:,} · skip {stat['skip']:,} "
                      f"· fail {stat['fail']:,} — {t['corp_name']}", flush=True)

    dart.save_json(ROOT / "data" / f"pledge_failed_{args.kind}.json", failed)
    print(f"\n완료 — 수신 {stat['ok']:,} · 기존 {stat['skip']:,} · 실패 {stat['fail']:,}")


if __name__ == "__main__":
    main()
