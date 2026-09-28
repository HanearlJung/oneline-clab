"""Phase 7 — 전체 상장사 담보계약 추출.

step6 이 받은 원문(slices/*/events/pledge_*.md)을 읽어 계약 단위 목록을 만든다.
100개사용 extracted/ 는 건드리지 않는다 (타깃발굴 화면이 그대로 쓰고 있다).

대량보유보고서는 제출인별 '최신 보고서'만 현재 상태다. 같은 제출인의 과거 보고서에
있던 계약은 이미 해지됐을 수 있으므로 읽지 않는다.
거래소 담보제공 공시도 매번 누적 계약 전체를 다시 적는다. 담보제공자별 최신분만 쓴다.

산출: data/pledge_all.json
"""
import os
import re
from collections import defaultdict

import psycopg2

from dart import ROOT, load_json, save_json
from step2b_extract_events import parse_pledge, parse_pledge_exchange

HEAD_RE = re.compile(r"^# (?P<name>.+?) \((?P<cc>\d{8})\) — pledge", re.M)


def field(text: str, key: str) -> str | None:
    m = re.search(rf"^- {key}: (.+)$", text, re.M)
    return m.group(1).strip() if m else None


def load_list_rows() -> dict[str, dict]:
    """검색 캐시 전체에서 접수번호 → 목록 행. 예전 수집분에는 제출인이 파일에 없다."""
    out = {}
    for f in (ROOT / "data" / "cache").rglob("*.json"):
        try:
            for r in load_json(f).get("rows", []):
                out[r["rcp_no"]] = r
        except Exception:  # noqa: BLE001
            continue
    return out


def stock_codes() -> dict[str, str]:
    """DART 고유번호 → 종목코드. 회사명은 표기가 흔들려 조인 키로 쓰지 않는다."""
    path = ROOT / "data" / "corp_codes.json"
    dsn = os.environ.get("DUNAMIS_DSN")
    if dsn:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            conn.set_session(readonly=True)
            cur.execute("""select corp_code, stock_code, corp_name
                           from bronze.kr_dart_disclosure_corp_code
                           where coalesce(trim(stock_code), '') <> ''""")
            m = {cc: {"stock_code": sc.strip(), "corp_name": nm} for cc, sc, nm in cur.fetchall()}
        save_json(path, m)
        return m
    return load_json(path)


def main() -> None:
    rows_by_rcp = load_list_rows()
    codes = stock_codes()
    files = sorted((ROOT / "slices").glob("*/events/pledge_*.md"))

    docs = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        h = HEAD_RE.search(text)
        rcp = field(text, "접수번호")
        if not h or not rcp:
            continue
        lr = rows_by_rcp.get(rcp, {})
        docs.append({
            "path": f, "text": text, "corp_code": h["cc"], "name": h["name"], "rcp_no": rcp,
            "report": field(text, "보고서") or lr.get("report_nm") or "",
            "filer": field(text, "제출인") or lr.get("filer"),
            "rcept_dt": field(text, "접수일") or lr.get("rcept_dt"),
        })

    # 대량보유: (회사, 제출인)별 최신 접수분만
    latest = {}
    for d in docs:
        if "대량보유" not in d["report"]:
            continue
        k = (d["corp_code"], d["filer"] or d["rcp_no"])
        if k not in latest or d["rcp_no"] > latest[k]["rcp_no"]:
            latest[k] = d
    use = list(latest.values()) + [d for d in docs if "담보제공계약" in d["report"]]

    out, stat = [], defaultdict(int)
    for d in use:
        exch = "담보제공계약" in d["report"]
        try:
            recs = parse_pledge_exchange(d["text"]) if exch else parse_pledge(d["text"])
        except Exception as exc:  # noqa: BLE001
            stat["parse_error"] += 1
            print(f"  ! {d['path'].name}: {type(exc).__name__} {exc}")
            continue
        stat["docs"] += 1
        if recs:
            stat["docs_with_pledge"] += 1
        code = codes.get(d["corp_code"], {})
        for r in recs:
            out.append({
                **r,
                "corp_code": d["corp_code"], "name": d["name"],
                "stock_code": code.get("stock_code"),
                "rcp_no": d["rcp_no"], "rcept_dt": d["rcept_dt"], "filer": d["filer"],
                "source_kind": "거래소공시" if exch else "대량보유상황보고서",
                "source_section": ("개별 담보제공 계약에 관한 사항" if exch
                                   else "보유주식등에 관한 계약"),
            })
    # 거래소 공시: (회사, 담보제공자)별 최신 접수분의 계약만 남긴다
    last = {}
    for x in out:
        if x["source_kind"] == "거래소공시":
            k = (x["corp_code"], x.get("holder"))
            last[k] = max(last.get(k, ""), x["rcp_no"])
    before = len(out)
    out = [x for x in out if x["source_kind"] != "거래소공시"
           or x["rcp_no"] == last[(x["corp_code"], x.get("holder"))]]
    stat["stale_exchange"] = before - len(out)

    save_json(ROOT / "data" / "pledge_all.json", out)
    print(f"원문 {len(files):,}건 → 사용 {len(use):,}건 (담보계약 있는 문서 {stat['docs_with_pledge']:,}건)")
    print(f"계약 {len(out):,}건 · {len({x['corp_code'] for x in out}):,}개사 "
          f"· 파싱 오류 {stat['parse_error']}건 · 과거 누적분 제외 {stat['stale_exchange']:,}건")
    print(f"  만기 확보 {sum(1 for x in out if x.get('maturity')):,}건 "
          f"· 금액 확보 {sum(1 for x in out if x.get('loan_amount')):,}건 "
          f"· 종목코드 없음 {sum(1 for x in out if not x['stock_code']):,}건")


if __name__ == "__main__":
    main()
