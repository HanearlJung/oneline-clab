"""Phase 6-b — 보호예수(락업) 실데이터.

주주별 보호예수 물량·해제일은 사내 DB(dunamis)에 정형화돼 있다. 여기에 없는
IPO 주관사는 증권신고서 '5. 인수 등에 관한 사항'에서 직접 읽는다.

이 대시보드에서 '수탁 증권사'는 그 회사의 IPO 대표주관회사다. 상장은 반드시 증권사를
통해서 하므로 주관사가 없는 회사는 없다 — 못 찾았으면 수집이 덜 된 것이다.
공동대표주관이면 주관사가 여러 곳이고, 점유율은 그 회사 물량을 고르게 나눠 센다.

접속 정보는 환경변수 DUNAMIS_DSN 으로만 받는다. 코드에 넣지 않는다.

산출: data/lockup.json
"""
import argparse
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

import psycopg2
import requests

import dart
from dart import ROOT
from step5_build_dashboard import ALIAS, norm_lender

UA = {"User-Agent": dart.UA}
KIND_URL = "https://kind.krx.co.kr/listinvstg/listingcompany.do?method=searchListingTypeMain"

# DB 의 relationship 은 원문 표기 그대로라 수십 종이다. 요구사항의 5분류로 접는다.
# 순서가 곧 우선순위다 — '최대주주의 특수관계인'은 특수관계인이다.
HOLDER_RULES = [
    ("우리사주", re.compile(r"우리사주")),
    ("특수관계인", re.compile(r"특수관계|친인척|배우자|자녀|계열|임원|이사|감사|대표")),
    ("최대주주", re.compile(r"최대주주|본인")),
    ("기관", re.compile(r"벤처금융|전문투자자|의무확약|기관|투자조합|펀드|금융|창투|신기사|재무적투자자|"
                       r"상장주선인|주관|인수")),
]

ROLE_RE = re.compile(r"^(공동\s*대표\s*주관|공동\s*대표|대표\s*주관|대표|공동\s*주관|인수)"
                     r"\s*(회사|사|단|인|기관)?$")
ROLE_KEY = {"대표주관": "lead", "대표": "lead", "공동대표주관": "co_lead", "공동대표": "co_lead",
            "공동주관": "co_mgr", "인수": "uw"}
BROKER_HINT = re.compile(r"증권|투자|금융|Securities|리미티드|은행")


def holder_type(rel: str | None) -> str:
    rel = (rel or "").replace(" ", "")
    for label, pat in HOLDER_RULES:
        if pat.search(rel):
            return label
    return "기타"


def clean_broker(name: str) -> str | None:
    name = re.sub(r"\(주\d*\)|주\d+\)", "", name or "").strip()
    n = norm_lender(name)
    if not n:
        return None
    n = re.sub(r"서울지점$|한국지점$", "", n)
    return ALIAS.get(n, n)


def parse_underwriters(text: str) -> dict:
    """'가. 인수방법에 관한 사항' 표에서 역할별 인수인을 읽는다.

    같은 역할이 여러 행이면 역할 칸이 병합(rowspan)되어 둘째 행부터는 비어 온다.
    그때는 바로 앞 행의 역할을 이어받는다.
    """
    head = text.split("인수대가에 관한 사항", 1)[0]
    out = {"lead": [], "co_lead": [], "co_mgr": [], "uw": []}
    role, width = None, None
    for line in head.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            if line:
                role = None
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        m = ROLE_RE.match(c[0].replace(" ", "")) if c else None
        if m and len(c) >= 2:
            role = ROLE_KEY[m.group(1).replace(" ", "")]
            width = len(c)
            name = c[1]
        elif role and width and len(c) == width - 1 and BROKER_HINT.search(c[0]):
            name = c[0]
        else:
            continue
        b = clean_broker(name)
        if b and BROKER_HINT.search(b) and b not in out[role]:
            out[role].append(b)
    if any(out.values()):
        return out

    # 역할 칸이 없는 서식 — '| 명칭 | 주소 |' 아래로 인수인 이름만 나열된다.
    # 누가 대표주관인지는 이 표로 알 수 없다. 1곳뿐이면 단독 주관이다.
    names, raw_of, in_tbl = [], {}, False
    for line in head.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            if line and in_tbl and names:
                break
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if c and c[0].replace(" ", "") in ("명칭", "인수인"):
            in_tbl = True
            continue
        if in_tbl and len(c) >= 3 and BROKER_HINT.search(c[0]) and not c[0].startswith("주"):
            b = clean_broker(c[0])
            if b and b not in names:
                names.append(b)
                raw_of[b] = re.sub(r"\s+", "", c[0])
    if len(names) == 1:
        out["lead"] = names
        return out

    # 표에 역할이 없으면 주석 문장에서 읽는다.
    #   '대표주관회사인 삼성증권㈜이 전체 공모주식의 …'
    #   '공동대표주관회사인 케이비증권 주식회사 및 엔에이치투자증권 주식회사가 …'
    prose = {"공동대표주관회사": "co_lead", "대표주관회사": "lead", "공동주관회사": "co_mgr",
             "인수회사": "uw"}
    found = defaultdict(list)
    flat = re.sub(r"\s+", "", text)
    for m in re.finditer(r"(공동대표주관회사|대표주관회사|공동주관회사|인수회사)인", flat):
        span = flat[m.end(): m.end() + 90]
        span = re.split(r"전체|자기계산|발행회사|총액인수|[.]|공동대표주관회사|대표주관회사|"
                        r"공동주관회사|인수회사", span)[0]
        for b in names:
            key = raw_of[b][:4]
            if (key in span or b in span) and not any(b in v for v in found.values()):
                found[prose[m.group(1)]].append(b)
    if found.get("lead") or found.get("co_lead"):
        for k, v in found.items():
            out[k] = v
        placed = {b for v in found.values() for b in v}
        out["uw"] = [n for n in names if n not in placed]
    else:
        out["uw"] = names
    return out


def custody(uw: dict) -> list[str]:
    """IPO 주관사. 대표주관 → 공동대표주관 → (둘 다 못 읽었으면) 공동주관·인수인 순."""
    for keys in (("lead", "co_lead"), ("co_mgr",), ("uw",)):
        got = [b for k in keys for b in uw[k]]
        if got:
            return list(dict.fromkeys(got))
    return []


def fetch_kind_sponsors(start="2021-06-01") -> dict[str, str]:
    """KIND 공모기업현황의 상장주선인. 증권신고서를 못 읽은 회사의 보완용."""
    s = requests.Session()
    s.headers.update(UA)
    ref = "https://kind.krx.co.kr/listinvstg/pubofrprogcom.do?method=searchPubofrProgComMain"
    s.get(ref, timeout=30)
    out, ipo, page = {}, {}, 1
    while page <= 30:
        r = s.post("https://kind.krx.co.kr/listinvstg/pubofrprogcom.do", data=dict(
            method="searchPubofrProgComSub", currentPageSize="100", pageIndex=str(page),
            orderMode="1", orderStat="D", forward="pubofrprogcom_sub", searchCorpName="",
            fromDate=start, toDate=date.today().isoformat(), marketType="", repMajAgntComp=""),
            headers={"Referer": ref, "X-Requested-With": "XMLHttpRequest"}, timeout=60)
        t = r.content.decode("utf-8", "replace")
        body = re.search(r"<tbody>(.*?)</tbody>", t, re.S)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S) if body else []
        got = 0
        for tr in rows:
            td = [re.sub(r"<[^>]+>|&nbsp;", " ", x).strip()
                  for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(td) >= 9 and td[0]:
                name = " ".join(td[0].split())
                out.setdefault(name, " ".join(td[8].split()))
                mk = re.search(r"alt='(코스닥|유가증권|코넥스)'", tr)
                ipo.setdefault(name, {
                    "market": {"코스닥": "KOSDAQ", "유가증권": "KOSPI", "코넥스": "KONEX"}.get(
                        mk.group(1)) if mk else None,
                    "listing_date": td[7] or None, "sponsor": out[name]})
                got += 1
        if got < 100:
            break
        page += 1
    dart.save_json(ROOT / "data" / "cache_uw" / "_kind_ipo.json", ipo)
    return out


def search_prospectus(corp_code: str, ipo: date) -> str | None:
    """고유번호로 증권신고서(지분증권)를 찾는다. 스팩은 DART 상호가 달라 이름으로는 안 잡힌다."""
    html = dart._request("POST", f"{dart.BASE}/dsab007/detailSearch.ax", data={
        "currentPage": 1, "maxResults": 100, "textCrpCik": corp_code,
        "startDate": (ipo - timedelta(days=400)).strftime("%Y%m%d"),
        "endDate": (ipo + timedelta(days=10)).strftime("%Y%m%d"),
        "publicType": "C001", "sort": "date", "series": "desc"}).text
    best = None
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        m = re.search(r"rcpNo=(\d{14})", tr)
        if m and re.search(r"증권신고서\(지분증권\)|투자설명서", re.sub(r"<[^>]+>|\s+", "", tr)):
            if corp_code in tr and (best is None or m.group(1) > best):
                best = m.group(1)
    return best


BAD_NAMES = {"유통제한물량", "유통가능물량", "소계", "합계"}
LOCKUP_NODES = ("투자위험요소", "모집 또는 매출에 관한 일반사항", "주주에 관한 사항",
                "그 밖에 투자자", "인수인의 의견")


def repair_names(out: list[dict], cache, no_fetch: bool) -> None:
    """DB 에 주주 이름 대신 표 머리글('유통제한물량')이 들어간 행이 있다.

    증권신고서의 의무보유 표에서 같은 수량·같은 기간이 적힌 줄을 찾아 이름을 되살린다.
    같은 수량인 사람이 여럿이면 나온 순서대로 나눠 준다. 못 찾으면 '이름 미상'으로 둔다.
    """
    todo = defaultdict(list)
    for x in out:
        if x["holder_name"] in BAD_NAMES and x.get("prospectus_rcept_no"):
            todo[x["prospectus_rcept_no"]].append(x)
    fixed = 0
    for rcp, rows in todo.items():
        f = cache / f"lockup_{rcp}.txt"
        if not f.exists() and not no_fetch:
            try:
                seen, parts = set(), []
                for n in dart.get_toc(rcp):
                    k = (n["offset"], n["length"])
                    if n["length"] and k not in seen and any(t in n["title"] for t in LOCKUP_NODES):
                        seen.add(k)
                        parts.append(dart.html_to_text(dart.get_section(n)))
                f.write_text("\n".join(parts), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 이름 복구용 원문 실패 {rcp}: {exc}")
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        used = defaultdict(int)
        for x in rows:
            q = f"{x['lockup_qty']:,}"
            per = re.sub(r"\s+", "", x.get("lockup_period") or "")
            names = []
            for line in text.split("\n"):
                if not line.startswith("|") or q not in line:
                    continue
                if per and per not in re.sub(r"\s+", "", line):
                    continue
                c = [v.strip() for v in line.strip("|").split("|")]
                if c and c[0] and not re.search(r"\d{3}|합계|소계|물량", c[0]) and c[0] not in names:
                    names.append(c[0])
            i = used[(q, per)]
            if i < len(names):
                x["holder_name"] = names[i]
                x["holder_name_src"] = "증권신고서 표에서 복구"
                used[(q, per)] += 1
                fixed += 1
            else:
                x["holder_name"] = "이름 미상"
    n = sum(len(v) for v in todo.values())
    if n:
        print(f"주주 이름 복구 {fixed}/{n}건")


def rcp_of(link: str | None) -> str | None:
    m = re.search(r"rcpNo=(\d{14})", link or "")
    return m.group(1) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="원문 수신 없이 캐시만 사용")
    args = ap.parse_args()

    dsn = os.environ.get("DUNAMIS_DSN")
    if not dsn:
        raise SystemExit("DUNAMIS_DSN 환경변수가 필요합니다.")
    master = dart.load_json(ROOT / "data" / "market_master.json")
    by_code = {v["stock_code"]: {**v, "name": k} for k, v in master.items()}
    meta_path = ROOT / "data" / "market_master_meta.json"
    px_base = dart.load_json(meta_path)["price_date"] if meta_path.exists() else None

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        conn.set_session(readonly=True)
        cur.execute("""
            select v.id, v.stock_code, v.company_name, v.shareholder_name, v.relationship,
                   v.restricted_shares, v.lock_up_release_date, v.close_price, v.market_cap,
                   g.lock_up_period, g.ipo_date, g.lock_up_date
            from gold.vw_shareholder_marketcap_sot v
            join gold.shareholder g on g.id = v.id
            where v.restricted_shares > 0""")
        rows = cur.fetchall()
        cur.execute("select max(trade_date) from gold.fct_marketcap")
        px_date = cur.fetchone()[0]
        cur.execute("select stock_code, corp_code from silver.disclosure_company_info "
                    "where corp_code is not null")
        corp_of = dict(cur.fetchall())
        cur.execute("""
            select distinct company_name, substring(source_link from 'rcpNo=(\\d{14})')
            from silver.kr_dart_disclosure_shareholder_data
            where deleted_at is null""")
        docs_of = defaultdict(list)
        for nm, r in cur.fetchall():
            if r:
                docs_of[nm].append(r)
        cur.execute("""
            select rcept_no, link from silver.kr_dart_disclosure_metadata_toc_html
            where title like '%%인수 등에 관한 사항' and title not like '%%본 문%%'""")
        uw_link = dict(cur.fetchall())
    print(f"DB 락업 {len(rows):,}행 · {len({r[1] for r in rows}):,}종목 · 종가 기준일 {px_date}")

    # ---- 회사별 증권신고서 접수번호. DB 에 없으면 공시검색으로 찾는다.
    companies = {}
    for r in rows:
        companies.setdefault(r[2], {"stock_code": r[1], "ipo": r[10]})
    cache = ROOT / "data" / "cache_uw"
    cache.mkdir(parents=True, exist_ok=True)
    idx_path = cache / "_index.json"
    idx = dart.load_json(idx_path) if idx_path.exists() else {}

    def ipo_doc(name, ipo):
        """상장 전에 낸 신고서 중 가장 늦은 것. 상장 뒤의 신고서는 유상증자 등 다른 공모다 —
        그 주관사를 IPO 주관사로 읽으면 안 된다."""
        if not ipo:
            return None
        limit = (ipo + timedelta(days=7)).strftime("%Y%m%d")
        ok = [r for r in docs_of.get(name, []) if r[:8] <= limit]
        return max(ok) if ok else None

    for i, (name, c) in enumerate(sorted(companies.items()), 1):
        rcp = ipo_doc(name, c["ipo"])
        prev = (idx.get(name) or {}).get("rcp_no")
        if prev and (rcp is None or rcp == prev):
            if rcp is None and c["ipo"] and prev[:8] > (c["ipo"] + timedelta(days=7)).strftime("%Y%m%d"):
                pass            # 예전에 잡은 문서가 상장 뒤 것이다. 아래에서 다시 찾는다
            else:
                continue
        cc = corp_of.get(c["stock_code"])
        if not rcp and cc and not args.no_fetch:
            try:
                rcp = search_prospectus(cc, c["ipo"] or date.today())
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 검색 실패 {name}: {exc}")
        idx[name] = {"rcp_no": rcp}
        if i % 25 == 0:
            dart.save_json(idx_path, idx)
            print(f"  접수번호 {i}/{len(companies)}", flush=True)
    dart.save_json(idx_path, idx)

    # ---- 인수 섹션 수신 → 주관사 위계
    n_doc = n_parsed = 0
    for i, (name, meta) in enumerate(sorted(idx.items()), 1):
        rcp = meta.get("rcp_no")
        if not rcp or name not in companies:
            continue
        n_doc += 1
        f = cache / f"{rcp}.md"
        retry = f.exists() and f.stat().st_size < 50
        if (not f.exists() or retry) and not args.no_fetch:
            try:
                if rcp in uw_link and not retry:
                    # DB 에 저장된 링크의 호스트는 수집 당시 경로다. 지금 쓰는 경로로 바꾼다.
                    link = re.sub(r"^https?://[^/]+", dart.BASE, uw_link[rcp])
                    r = dart._request("GET", link)
                    r.encoding = r.apparent_encoding or "utf-8"
                    html = r.text
                else:
                    toc = dart.get_toc(rcp)
                    node = dart.find_toc_node(toc, ["인수 등에 관한 사항"])
                    html = dart.get_section(node) if node else ""
                text = dart.html_to_text(html)
                if len(text) < 50:
                    # 2022년 상반기까지의 서식은 목차가 한 단계 얕다. 상위 절을 받아 잘라낸다.
                    toc = dart.get_toc(rcp)
                    node = dart.find_toc_node(toc, ["모집 또는 매출에 관한 일반사항"])
                    whole = dart.html_to_text(dart.get_section(node)) if node else ""
                    i = whole.rfind("인수 등에 관한 사항")
                    text = whole[i:] if i >= 0 else ""
                f.write_text(text, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 인수섹션 실패 {name} {rcp}: {exc}")
        if f.exists():
            meta["uw"] = parse_underwriters(f.read_text(encoding="utf-8"))
            if any(meta["uw"].values()):
                n_parsed += 1
        if i % 50 == 0:
            print(f"  인수섹션 {i}/{len(idx)}", flush=True)
    dart.save_json(idx_path, idx)
    print(f"증권신고서 확보 {n_doc}개사 · 주관사 파싱 {n_parsed}개사")

    sponsors = {}
    if not args.no_fetch:
        try:
            sponsors = fetch_kind_sponsors()
            dart.save_json(cache / "_kind_sponsors.json", sponsors)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! KIND 실패: {exc}")
    elif (cache / "_kind_sponsors.json").exists():
        sponsors = dart.load_json(cache / "_kind_sponsors.json")
    print(f"KIND 상장주선인 {len(sponsors)}개사")

    # ---- 조립
    out, stat = [], defaultdict(int)
    mismatch = []
    for (_id, code, name, holder, rel, qty, rel_dt, px, mcap, period, ipo, _lk) in rows:
        meta = idx.get(name) or {}
        uw = meta.get("uw") or {"lead": [], "co_lead": [], "co_mgr": [], "uw": []}
        src_doc = "증권신고서"
        kind_names = [clean_broker(x) for x in re.split(r"[,/]|\s{2,}", sponsors.get(name) or "")]
        kind_names = [x for x in kind_names if x]
        leads = uw["lead"] + uw["co_lead"]
        if leads and kind_names and set(kind_names) < set(leads):
            # 신고서 문장이 '대표주관회사인 A와 B는 각각 95%, 5%를 인수' 처럼 모호할 때가 있다.
            # 거래소(KIND)가 상장주선인으로 올린 곳만 주관사로 둔다.
            extra = [b for b in leads if b not in kind_names]
            uw = {"lead": [b for b in uw["lead"] if b in kind_names],
                  "co_lead": [b for b in uw["co_lead"] if b in kind_names],
                  "co_mgr": uw["co_mgr"], "uw": uw["uw"] + extra}
            leads = uw["lead"] + uw["co_lead"]
        if leads and kind_names and not set(leads) <= set(kind_names):
            mismatch.append((name, leads, kind_names))
        if not (uw["lead"] or uw["co_lead"]) and sponsors.get(name):
            names = [clean_broker(x) for x in re.split(r"[,/]|\s{2,}", sponsors[name])]
            names = [x for x in names if x]
            uw = {**uw, "lead": names, "uw": [x for x in uw["uw"] if x not in names]}
            src_doc = "KIND 상장주선인"
        brokers = custody(uw)
        stat["주관사 있음" if brokers else "주관사 없음"] += 1
        m = by_code.get(code) or master.get(name) or {}
        code = m.get("stock_code") or code
        price = m.get("price") or (float(px) if px else None)
        cap = m.get("market_cap") or (round(float(mcap) / 1e8, 1) if mcap else None)
        rcp = meta.get("rcp_no")
        # 주주 이름이 없는 행은 증권신고서가 아니라 KIND '신규상장기업 유통가능주식수 현황'의
        # 분류 단위 물량이다 (기관 의무보유확약·우리사주·자발적 의무보유). 출처를 구분한다.
        from_kind = holder is None
        out.append({
            "corp_name": name, "stock_code": code, "corp_code": corp_of.get(code),
            "industry": m.get("industry"), "market_cap": cap,
            "listing_date": ipo.isoformat() if ipo else None,
            "holder_name": holder or ("기관 의무보유확약" if rel == "의무확약" else (rel or None)),
            # DB 에 관계 대신 취득일이 들어간 행이 있다. 날짜는 관계가 아니다.
            "holder_type": holder_type(rel) if not re.match(r"^\d{4}[.\-]", rel or "") else "기타",
            "holder_rel": rel if not re.match(r"^\d{4}[.\-]", rel or "") else None,
            "lockup_qty": int(qty),
            "lockup_value": int(qty * price) if price else None,
            "value_base_date": (m.get("listing_date") if m.get("price_basis") else px_base)
                               if price else None,
            "value_basis": m.get("price_basis") or ("종가" if price else None),
            "release_date": rel_dt.isoformat(), "lockup_period": period,
            "custody_broker": brokers[0] if brokers else None,
            "custody_brokers": brokers,
            "custody_broker_src": "주관사" if brokers else "미상",
            "lead_manager": ", ".join(uw["lead"] + uw["co_lead"]) or None,
            "co_manager": ", ".join(uw["co_mgr"]) or None,
            "underwriter": ", ".join(uw["uw"]) or None,
            "manager_src": src_doc if any(uw.values()) else None,
            "rcept_no": None if from_kind else rcp,
            "prospectus_rcept_no": rcp,
            "source_section": ("KIND 신규상장기업 유통가능주식수 현황" if from_kind
                               else "증권신고서 의무보유 · 보호예수"),
            "source_url": KIND_URL if from_kind else (dart.dart_url(rcp) if rcp else None),
            "row_source": "KIND" if from_kind else "증권신고서",
            "is_mock": False,
        })

    # KIND 의 '자발적의무보유' 합계는 증권신고서의 주주별 행과 같은 물량일 수 있다.
    # 같은 회사에 해제일이 사흘 안쪽으로 붙어 있고 수량이 그 안에 들어가면 같은 물량으로 보고
    # KIND 행을 뺀다 (두 번 세지 않는다). 기관 확약·우리사주는 증권신고서에 없는 물량이라 둔다.
    named = defaultdict(list)
    for x in out:
        if x["row_source"] == "증권신고서":
            named[x["stock_code"]].append(x)
    def overlaps(k):
        if (k["holder_rel"] or "").replace(" ", "") != "자발적의무보유":
            return False
        kd = date.fromisoformat(k["release_date"])
        near = [n for n in named.get(k["stock_code"], [])
                if abs((date.fromisoformat(n["release_date"]) - kd).days) <= 3]
        return bool(near) and k["lockup_qty"] <= sum(n["lockup_qty"] for n in near) * 1.05
    n0 = len(out)
    out = [x for x in out if not (x["row_source"] == "KIND" and overlaps(x))]
    repair_names(out, cache, args.no_fetch)
    print(f"KIND 자발적의무보유 중 주주 행과 겹쳐 제외 {n0 - len(out)}건")
    dart.save_json(ROOT / "data" / "lockup.json", out)
    today = date.today().isoformat()
    act = [x for x in out if x["release_date"] >= today]
    print(f"\nlockup.json — {len(out):,}행 · {len({x['stock_code'] for x in out}):,}종목 "
          f"(해제 예정 {len(act):,}행 · {len({x['stock_code'] for x in act}):,}종목)")
    print(f"  IPO 주관사 — 확보 {stat['주관사 있음']:,}행 · 없음 {stat['주관사 없음']:,}행")
    seen = set()
    mm = [m for m in mismatch if not (m[0] in seen or seen.add(m[0]))]
    print(f"  증권신고서 대표주관이 KIND 상장주선인에 없는 회사 {len(mm)}곳")
    dart.save_json(ROOT / "data" / "manager_mismatch.json",
                   [{"corp_name": a, "prospectus": b, "kind": c} for a, b, c in mm])
    missing = sorted({x["corp_name"] for x in out if not x["custody_brokers"]})
    if missing:
        print(f"  !! 주관사를 못 찾은 회사 {len(missing)}곳: {missing}")
    print(f"  평가금액 없음 {sum(1 for x in out if x['lockup_value'] is None):,}행 · "
          f"원문 없음 {sum(1 for x in out if not x['rcept_no']):,}행")
    ht = defaultdict(int)
    for x in out:
        ht[x["holder_type"]] += 1
    print("  대상자 유형 —", dict(ht))


if __name__ == "__main__":
    main()
