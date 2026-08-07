"""DART 공개 웹 접근 — 다운로드 전용. API 키가 필요 없다.

OpenDART API 키는 등록 IP에서만 쓸 수 있어 사용하지 않는다.
대신 공개 공시검색/뷰어를 그대로 쓴다. 셋 다 인증 없이 열린다.

  - 공시검색  dsab007/detailSearch.ax   공시유형·기간·회사명으로 목록
  - 목차      dsaf001/main.do?rcpNo=    문서 트리(섹션별 offset/length)
  - 본문      report/viewer.do          목차가 가리키는 구간만 정확히 수신

이 모듈은 '가져오기'와 '기계적 텍스트화'까지만 한다. 값 해석은 하지 않는다.
"""
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://dart.fss.or.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 90
RETRY = 5
# 공개 서비스다. 짧은 간격으로 수백 페이지를 훑으면 IP가 차단된다(경험).
# 처리량보다 차단 회피가 우선이다.
PAUSE = 1.5
BACKOFF = (5, 15, 45, 120, 300)  # 실패 시 대기(초). 차단은 시간이 지나야 풀린다

# 공시유형 코드 (상세검색 publicType)
PT_ANNUAL = "A001"       # 사업보고서
PT_HALF = "A002"         # 반기보고서
PT_QUARTER = "A003"      # 분기보고서
PT_MAJOR = "B001"        # 주요사항보고서
PT_MAJORSTOCK = "D001"   # 주식등의 대량보유상황보고서
PT_ELESTOCK = "D002"     # 임원·주요주주 특정증권등 소유상황보고서
PT_EXCHANGE = "I001"     # 거래소 수시공시 — 최대주주변경·주식담보제공계약 체결
PT_OWNERSHIP_CHG = "I004"  # 거래소 지분공시 — 최대주주등 소유주식 변동신고서

MARKET = {"유": "KOSPI", "코": "KOSDAQ", "넥": "KONEX", "기": "기타"}
# 시장구분은 글자보다 CSS class 로 판별하는 편이 안정적이다
MARKET_CLASS = {
    "tagCom_kospi": "KOSPI", "tagCom_kosdaq": "KOSDAQ",
    "tagCom_konex": "KONEX", "tagCom_etc": "기타",
}
_MARKET_CLS_RE = re.compile(r"^tagCom_(kospi|kosdaq|konex|etc)$")
_CORPINFO_TITLE_RE = re.compile("기업개황")
_CORP_CODE_RE = re.compile(r"openCorpInfoNew\(\s*'(\d{8})'")

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Referer": BASE})


def _reset_session() -> None:
    """연결이 끊기면 세션을 새로 만든다(끊긴 keep-alive 재사용 방지)."""
    global _session
    _session = requests.Session()
    _session.headers.update({"User-Agent": UA, "Referer": BASE})


def _request(method: str, url: str, **kw) -> requests.Response:
    last = None
    for attempt in range(RETRY):
        try:
            r = _session.request(method, url, timeout=TIMEOUT, **kw)
            r.raise_for_status()
            time.sleep(PAUSE)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            print(f"    · 요청 실패({type(e).__name__}) {wait}s 후 재시도 "
                  f"[{attempt + 1}/{RETRY}]", flush=True)
            _reset_session()
            time.sleep(wait)
    raise RuntimeError(f"{url} 실패: {last}")


# ---------------------------------------------------------------- 공시검색

_ROW_RE = re.compile(
    r"<tr>(.*?)</tr>", re.S)
_RCP_RE = re.compile(r"rcpNo=(\d{14})")


def search(public_type: str, start: str, end: str, corp_name: str = None,
           page: int = 1, per_page: int = 100,
           report_name: str = None) -> tuple[list[dict], int]:
    """공시 목록 한 페이지. 반환: (행 목록, 총 페이지 수)

    report_name 을 주면 보고서명으로 좁힌다. 전체 페이지를 훑는 것보다
    요청 수가 훨씬 적어 차단 위험이 낮다.
    """
    data = {
        "currentPage": page, "maxResults": per_page,
        "startDate": start, "endDate": end,
        "publicType": public_type, "sort": "date", "series": "desc",
    }
    if corp_name:
        data["textCrpNm"] = corp_name
    if report_name:
        data["reportName"] = report_name
        data["reportNamePopYn"] = "Y"
    html = _request("POST", f"{BASE}/dsab007/detailSearch.ax", data=data).text

    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.select("table.tbList tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        link = tr.find("a", href=_RCP_RE)
        m = _RCP_RE.search(link["href"]) if link else None
        if not m:
            continue
        # td[1] 구조:
        #   <span class="tagCom_kospi">유</span>
        #   <a onclick="openCorpInfoNew('00258801',...)" title="카카오 기업개황 새창">카카오</a>
        #   <span class="tagCom_ir">IR</span>   <- 배지. 회사명에 섞이면 안 된다
        cell = tds[1]
        mk = "기타"
        tag = cell.find("span", class_=_MARKET_CLS_RE)
        if tag:
            for cls in tag.get("class", []):
                if cls in MARKET_CLASS:
                    mk = MARKET_CLASS[cls]
                    break

        link = cell.find("a", title=_CORPINFO_TITLE_RE)
        corp = (link.get_text(" ", strip=True) if link
                else cell.get_text(" ", strip=True))
        corp = " ".join(corp.split())

        corp_code = ""
        cm = _CORP_CODE_RE.search(str(cell))
        if cm:
            corp_code = cm.group(1)

        rows.append({
            "market": mk,
            "corp_name": corp,
            "corp_code": corp_code,
            "report_nm": " ".join(tds[2].get_text(" ", strip=True).split()),
            "filer": tds[3].get_text(strip=True),
            "rcept_dt": tds[4].get_text(strip=True).replace(".", ""),
            "rcp_no": m.group(1),
        })

    total_pages = 1
    pages = re.findall(r"\[(\d+)/(\d+)\]", soup.get_text())
    if pages:
        total_pages = int(pages[0][1])
    return rows, total_pages


def search_all(public_type: str, start: str, end: str, corp_name: str = None,
               max_pages: int = 500, on_page=None, report_name: str = None,
               cache_key: str = None) -> list[dict]:
    """페이징 전체 수집. 커서는 단조증가만 한다.

    cache_key 를 주면 페이지 단위로 디스크에 적재한다. 중단·차단 후 다시 돌려도
    받은 페이지는 건너뛴다 (수백 페이지를 처음부터 다시 받지 않기 위함).
    """
    cache_dir = None
    if cache_key:
        cache_dir = ROOT / "data" / "cache" / cache_key
        cache_dir.mkdir(parents=True, exist_ok=True)

    out, page = [], 1
    while page <= max_pages:
        cache_file = cache_dir / f"{page:04d}.json" if cache_dir else None
        if cache_file and cache_file.exists():
            payload = load_json(cache_file)
            rows, total = payload["rows"], payload["total"]
        else:
            rows, total = search(public_type, start, end, corp_name, page,
                                 report_name=report_name)
            if cache_file:
                save_json(cache_file, {"rows": rows, "total": total})

        out.extend(rows)
        if on_page:
            on_page(page, total, len(out))
        if page >= total or not rows:
            break
        page += 1
    return out


# ------------------------------------------------------------------- 목차

_NODE_RE = re.compile(
    r"node\d+\['text'\]\s*=\s*\"(?P<text>.*?)\";.*?"
    r"node\d+\['rcpNo'\]\s*=\s*\"(?P<rcpNo>\d+)\";.*?"
    r"node\d+\['dcmNo'\]\s*=\s*\"(?P<dcmNo>\d+)\";.*?"
    r"node\d+\['eleId'\]\s*=\s*\"(?P<eleId>\d*)\";.*?"
    r"node\d+\['offset'\]\s*=\s*\"(?P<offset>\d*)\";.*?"
    r"node\d+\['length'\]\s*=\s*\"(?P<length>\d*)\";.*?"
    r"node\d+\['dtd'\]\s*=\s*\"(?P<dtd>[^\"]*)\";",
    re.S)


# 단일 문서 공시(거래소 수시공시 등)는 목차 트리가 없고 viewDoc 호출만 있다.
# 인자는 (rcpNo, dcmNo, eleId, offset, length, dtd, tocNo) 순.
_VIEWDOC_RE = re.compile(
    r'viewDoc\(\s*"(\d+)"\s*,\s*"(\d+)"\s*,\s*"(\d*)"\s*,\s*"(\d*)"\s*,'
    r'\s*"(\d*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)')


def get_toc(rcp_no: str) -> list[dict]:
    """문서 목차. 각 노드가 섹션의 정확한 offset/length 를 갖는다.

    정기보고서처럼 여러 섹션으로 나뉜 문서는 트리가 있고,
    거래소 수시공시처럼 단일 HTML 문서는 트리 없이 viewDoc 호출만 있다.
    """
    html = _request("GET", f"{BASE}/dsaf001/main.do", params={"rcpNo": rcp_no}).text
    nodes = []
    for m in _NODE_RE.finditer(html):
        d = m.groupdict()
        if not d["offset"] or not d["length"]:
            continue
        nodes.append({
            "title": " ".join(d["text"].split()),
            "rcp_no": d["rcpNo"], "dcm_no": d["dcmNo"], "ele_id": d["eleId"],
            "offset": int(d["offset"]), "length": int(d["length"]), "dtd": d["dtd"],
        })
    if nodes:
        return nodes

    # fallback: 단일 문서
    for m in _VIEWDOC_RE.finditer(html):
        rcp, dcm, ele, off, ln, dtd, _ = m.groups()
        if rcp != rcp_no:
            continue
        return [{
            "title": "전체 문서", "rcp_no": rcp, "dcm_no": dcm,
            "ele_id": ele or "0", "offset": int(off or 0),
            "length": int(ln or 0), "dtd": dtd or "HTML",
        }]
    return []


def find_toc_node(toc: list[dict], patterns: list[str]) -> dict | None:
    """목차에서 패턴에 맞는 노드. 앞선 패턴일수록 우선."""
    for pat in patterns:
        for node in toc:
            if pat in node["title"]:
                return node
    return None


def get_section(node: dict) -> str:
    """목차 노드가 가리키는 구간만 수신."""
    r = _request("GET", f"{BASE}/report/viewer.do", params={
        "rcpNo": node["rcp_no"], "dcmNo": node["dcm_no"], "eleId": node["ele_id"],
        "offset": node["offset"], "length": node["length"], "dtd": node["dtd"],
    })
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


# ------------------------------------------------------- 섹션 분할·텍스트화

_ANCHOR_RE = re.compile(r"<A name='(toc\d+)'>(.*?)</A>", re.S | re.I)
# 텍스트 기반 fallback 용 최상위 주석 제목: "24. 금융상품" 같은 줄.
# 하위 항목(2.6.1)은 잡지 않는다 — 그건 섹션이 아니라 문단이다.
# (?!\d) 가 없으면 "3.1. 법인세" 의 "3." 만 걸려 하위 항목이 섹션이 된다
_TEXT_HEAD_RE = re.compile(r"^[ \t]*(\d{1,2})\.(?!\d)[ \t]*(\S[^\n]{0,44})$", re.M)


def split_sections(html: str) -> list[dict]:
    """주석을 섹션 단위로 나눈다. 각 섹션은 {'title', 'text'}.

    1) XBRL 기반 문서는 `<A name='tocN'>제목</A>` 앵커가 경계를 명시한다.
    2) 수기 작성 문서에는 앵커가 없다. 그때는 텍스트의 최상위 제목으로 나눈다.

    어느 쪽이든 길이를 추정해 자르지 않는다. 회사마다 주석 번호·명칭·분량이
    달라도 문서가 가진 구조를 그대로 따라간다.
    """
    anchors = [(m.start(), " ".join(re.sub(r"<[^>]+>", "", m.group(2)).split()))
               for m in _ANCHOR_RE.finditer(html)]

    if len(anchors) >= 3:
        out = []
        for i, (start, title) in enumerate(anchors):
            end = anchors[i + 1][0] if i + 1 < len(anchors) else len(html)
            out.append({"title": title, "text": html_to_text(html[start:end])})
        return out

    # --- fallback: 앵커가 없거나 너무 적은 문서
    text = html_to_text(html)
    heads = [(m.start(), " ".join(m.group().split()))
             for m in _TEXT_HEAD_RE.finditer(text)]
    # 문서 자체 제목("3. 연결재무제표 주석")이 앞에 오므로 첫 "1." 부터 시작한다.
    # 이후로는 번호가 되돌아가면 목차이거나 표 안이라 경계로 보지 않는다.
    start_at = next((i for i, (_, t) in enumerate(heads)
                     if t.split(".", 1)[0].strip() == "1"), 0)
    kept, last_no = [], 0
    for pos, title in heads[start_at:]:
        no = int(title.split(".", 1)[0])
        if no > last_no:           # 단조증가만 인정
            kept.append((pos, title))
            last_no = no
    if len(kept) < 3:
        return [{"title": "전체 주석", "text": text}]

    out = []
    for i, (start, title) in enumerate(kept):
        end = kept[i + 1][0] if i + 1 < len(kept) else len(text)
        out.append({"title": title, "text": text[start:end]})
    return out


def html_to_text(html: str) -> str:
    """표 구조를 파이프 테이블로 보존하며 텍스트화. 해석은 하지 않는다."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    for table in soup.find_all("table"):
        lines = []
        for tr in table.find_all("tr"):
            cells = [" ".join(td.get_text(" ", strip=True).split())
                     for td in tr.find_all(["td", "th"])]
            if any(cells):
                lines.append("| " + " | ".join(cells) + " |")
        table.replace_with("\n" + "\n".join(lines) + "\n" if lines else "\n")

    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ------------------------------------------------------------------ 유틸

def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dart_url(rcp_no: str) -> str:
    return f"{BASE}/dsaf001/main.do?rcpNo={rcp_no}"
