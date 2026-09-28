"""Phase 8 — IPO 주관 실적의 모집단을 한국거래소(KIND) 공식 신규상장 목록으로 잡는다.

사내 DB 의 보호예수 자료는 회사가 빠져 있다(스팩 다수, 2022년 초 상장분, 최근 상장분).
거기서 센 주관 건수는 실제보다 적다. 점유율은 KIND '신규상장기업현황' 전체를 모집단으로
하고, 주관사는 아래 순서로 정한다.

  1) 증권신고서 '인수 등에 관한 사항'의 대표주관·공동대표주관 (step6b 가 읽어 둔 것)
  2) KIND 상장주선인이 한 곳이면 그곳
  3) KIND 상장주선인이 여러 곳이면 증권신고서를 새로 받아 역할을 읽는다
  4) 그래도 못 정하면 KIND 상장주선인 전부 (근거를 'KIND(역할 미확인)'으로 남긴다)

금액은 증권사별 **실제 인수금액**이다. 증권신고서 인수인 표의 비율에 거래소가 공시한
확정 공모금액을 곱한다. 대표주관뿐 아니라 공동주관·인수회사도 각자 인수한 만큼 잡힌다.
스팩은 주관사가 전량 인수한다(신고서를 읽은 52건 전부 그랬다).

산출: data/ipo_deals.json
"""
import argparse
import re
from collections import Counter
from datetime import date, timedelta

import dart
from dart import ROOT, load_json, save_json
from ipo_alloc import parse_allocations, underwriting_text
from step6b_build_lockup import clean_broker, custody, parse_underwriters, search_prospectus

MARKET = {"코스닥": "KOSDAQ", "유가증권": "KOSPI"}
SPAC_RE = re.compile(r"스팩|기업인수목적")
REIT_SECU = ("부동산투자회사", "사회간접자본투융자회사")


def nospace(s):
    return re.sub(r"\s+", "", s or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()

    kind = load_json(ROOT / "data" / "verify" / "kind_listing_2022_2026.json")
    idx = load_json(ROOT / "data" / "cache_uw" / "_index.json")
    codes = load_json(ROOT / "data" / "corp_codes.json")            # corp_code -> {stock_code}
    corp_of = {v["stock_code"]: k for k, v in codes.items()}
    lock = load_json(ROOT / "data" / "lockup.json")
    lock_name = {x["stock_code"]: x["corp_name"] for x in lock if x.get("stock_code")}
    idx_by_name = {nospace(k): v for k, v in idx.items()}
    cache = ROOT / "data" / "cache_uw"
    overrides = load_json(ROOT / "data" / "verify" / "lead_overrides.json")
    bn = ROOT / "data" / "verify" / "prospectus_by_name.json"
    by_name = load_json(bn) if bn.exists() else {}

    deals, stat, stat2 = [], Counter(), Counter()
    for r in kind:
        if r["market"] not in MARKET or r["type"] not in ("신규상장", "이전상장"):
            continue
        has_offer = r["price"] not in ("", "-", None)
        if r["type"] == "이전상장" and not has_offer:
            continue                      # 공모 없는 시장 이전은 주관 실적이 아니다
        sponsors = [clean_broker(x) for x in re.split(r"[,/]", r["sponsor"] or "")]
        sponsors = [x for x in dict.fromkeys(sponsors) if x]
        name = r["name"]
        meta = idx_by_name.get(nospace(name)) or idx_by_name.get(nospace(lock_name.get(r["code"], "")))
        uw = (meta or {}).get("uw")
        rcp = (meta or {}).get("rcp_no")
        leads, src = [], None
        if uw and (uw["lead"] or uw["co_lead"]):
            leads = list(dict.fromkeys(uw["lead"] + uw["co_lead"]))
            src = "증권신고서"
            if sponsors and not set(leads) <= set(sponsors):
                # 신고서의 주관사가 거래소 상장주선인에 없다 — 다른 공모의 신고서를 읽은 것
                leads, src = [], None
        if not leads and len(sponsors) == 1:
            leads, src = sponsors, "KIND 상장주선인(단독)"
        if not leads and len(sponsors) > 1 and not args.no_fetch:
            cc = corp_of.get(r["code"])
            try:
                ld = date.fromisoformat(r["listing_date"])
                rcp2 = search_prospectus(cc, ld) if cc else None
                if rcp2:
                    f = cache / f"{rcp2}.md"
                    if not f.exists() or f.stat().st_size < 50:
                        toc = dart.get_toc(rcp2)
                        node = dart.find_toc_node(toc, ["인수 등에 관한 사항"])
                        text = dart.html_to_text(dart.get_section(node)) if node else ""
                        if len(text) < 50:
                            node = dart.find_toc_node(toc, ["모집 또는 매출에 관한 일반사항"])
                            whole = dart.html_to_text(dart.get_section(node)) if node else ""
                            i = whole.rfind("인수 등에 관한 사항")
                            text = whole[i:] if i >= 0 else ""
                        f.write_text(text, encoding="utf-8")
                    u2 = parse_underwriters(f.read_text(encoding="utf-8"))
                    got = list(dict.fromkeys(u2["lead"] + u2["co_lead"]))
                    if got and set(got) <= set(sponsors):
                        leads, src, rcp, uw = got, "증권신고서", rcp2, u2
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {name}: {exc}")
        ov = overrides.get(name)
        if ov:
            leads, src, rcp = ov["lead"], "증권신고서(본문 확인)", ov.get("rcept_no")
            uw = {"co_mgr": ov.get("co_mgr") or []}
        if not leads and sponsors:
            leads, src = sponsors, "KIND 상장주선인(역할 미확인)"
        amt = re.sub(r"[^\d]", "", r.get("amount_k") or "")
        offer = int(amt) * 1000 if amt else None

        # ---- 증권사별 인수 몫
        def fetch_section(no):
            f = cache / f"{no}.md"
            stale = f.exists() and not parse_allocations(f.read_text(encoding="utf-8"))
            if (not f.exists() or stale) and not args.no_fetch:
                text = underwriting_text(dart, no)
                if text:
                    f.write_text(text, encoding="utf-8")
            return f.read_text(encoding="utf-8") if f.exists() else ""

        alloc, alloc_src = [], None
        is_spac = bool(SPAC_RE.search(name))
        doc = rcp or by_name.get(name)
        if not doc and not is_spac and not args.no_fetch:
            cc = corp_of.get(r["code"])
            try:
                doc = search_prospectus(cc, date.fromisoformat(r["listing_date"])) if cc else None
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 신고서 검색 {name}: {exc}")
        if doc:
            try:
                got = parse_allocations(fetch_section(doc))
            except Exception as exc:  # noqa: BLE001
                got = []
                print(f"  ! 인수표 {name}: {exc}")
            if got and set(leads) <= {g["broker"] for g in got}:
                alloc = [{"broker": g["broker"], "ratio": round(g["ratio"], 6)} for g in got]
                alloc_src = f"증권신고서 인수인 표({got[0]['ratio_basis']})"
                rcp = rcp or doc
        if not alloc and len(leads) == 1:
            alloc = [{"broker": leads[0], "ratio": 1.0}]
            alloc_src = "주관사 단독(전량)" if is_spac else "주관사 단독(인수인 표 미확인)"
        if not alloc and leads:
            alloc = [{"broker": b, "ratio": round(1 / len(leads), 6)} for b in leads]
            alloc_src = "균등 분할(인수인 표 미확인)"
        for a in alloc:
            a["amount"] = int(offer * a["ratio"]) if offer else None
        stat2[alloc_src] += 1
        deals.append({
            "name": name, "stock_code": r["code"], "market": MARKET[r["market"]],
            "listing_date": r["listing_date"], "listing_type": r["type"],
            "is_spac": bool(SPAC_RE.search(name)),
            # 이름으로 가리면 '바이오인프라', '메리츠스팩'이 리츠로 잡힌다. 거래소 증권구분을 쓴다.
            "is_reit": r["secu"] in REIT_SECU, "secu": r["secu"],
            "offer_price": int(re.sub(r"[^\d]", "", r["price"])) if has_offer else None,
            "offer_amount": offer,                                     # 원
            "allocations": alloc, "alloc_src": alloc_src,
            "lead_managers": leads, "lead_src": src,
            "kind_sponsors": sponsors,
            "co_managers": (uw or {}).get("co_mgr") or [],
            "prospectus_rcept_no": rcp,
            "in_lockup_db": r["code"] in lock_name or nospace(name) in {nospace(v) for v in lock_name.values()},
        })
        stat[src or "주관사 없음"] += 1
    save_json(ROOT / "data" / "ipo_deals.json", deals)
    print(f"ipo_deals.json — {len(deals)}건", dict(stat))
    print("  인수 몫 근거:", dict(stat2))
    print("  인수인 표 미확인:", [d["name"] for d in deals if "미확인" in (d["alloc_src"] or "")])
    print("  주관사 없음:", [d["name"] for d in deals if not d["lead_managers"]])
    print("  역할 미확인:", [(d["name"], d["kind_sponsors"]) for d in deals
                          if d["lead_src"] == "KIND 상장주선인(역할 미확인)"])


if __name__ == "__main__":
    main()
