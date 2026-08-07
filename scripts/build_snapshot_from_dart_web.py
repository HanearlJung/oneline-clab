#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import http.client
import http.cookiejar
import json
import random
import re
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path


BASE_URL = "https://dart.fss.or.kr"
DEFAULT_BEGIN = "20260101"
DEFAULT_END = "20260806"
DEFAULT_MAX_RESULTS = 100

COMPANY_NAME_ALIASES = {
    "삼성화재": "삼성화재해상보험",
    "한국전력": "한국전력공사",
    "금호석유": "금호석유화학",
}

PURPOSE_REPORT_KEYWORDS = {
    "investment": ["사업보고서", "반기보고서", "분기보고서"],
    "pension": ["사업보고서", "반기보고서", "분기보고서"],
    "blockdeal": ["자기주식처분", "자기주식 처분", "증권신고서", "투자설명서", "보호예수"],
    "pledge": ["주식등의대량보유상황보고서", "임원ㆍ주요주주특정증권등소유상황보고서", "특정증권등소유상황보고서"],
    "employee": ["주식매수선택권", "주식기준보상", "스톡옵션", "RSU", "RSA", "PSU"],
    "wealth": ["최대주주", "대표이사변경", "사외이사의선임", "임원ㆍ주요주주특정증권등소유상황보고서"],
}

PURPOSE_FALLBACK_REPORTS = {
    "blockdeal": ["자기주식 처분결정", "자기주식 처분결과보고서", "증권신고서", "투자설명서"],
    "pledge": ["주식등의 대량보유상황보고서", "임원ㆍ주요주주특정증권등소유상황보고서"],
    "employee": ["주식매수선택권부여에관한신고", "주식매수선택권 행사", "주식매수선택권"],
    "wealth": ["최대주주", "대표이사변경", "사외이사의선임", "임원ㆍ주요주주특정증권등소유상황보고서"],
}

PURPOSE_FALLBACK_PUBLIC_TYPES = {
    "investment": ["A001", "A002", "A003"],
    "pension": ["A001", "A002", "A003"],
}


def normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def sort_filings(filings: list[dict]) -> list[dict]:
    return sorted(
        filings,
        key=lambda filing: (
            filing.get("rceptDate", ""),
            -int(filing.get("rowNumber", 9999)),
        ),
        reverse=True,
    )


def dedupe_filings(filings: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for filing in sort_filings(filings):
        rcept_no = filing.get("rceptNo")
        if not rcept_no or rcept_no in seen:
            continue
        seen.add(rcept_no)
        unique.append(filing)
    return unique


class DartWebClient:
    popup_row_pattern = re.compile(
        r"<tr>\s*"
        r"<td[^>]*>.*?<input type='hidden' name='hiddenCikCD\d+' value='([^']+)'>\s*"
        r"<input type='hidden' name='hiddenCikNM\d+' value='([^']+)'>.*?</td>\s*"
        r"<td class=\"tL ellipsis\"[^>]*>(.*?)</td>\s*"
        r"<td class=\"tL ellipsis\"[^>]*>(.*?)</td>\s*"
        r"<td>(.*?)</td>\s*"
        r"<td class=\"tL ellipsis\"[^>]*>(.*?)</td>\s*"
        r"</tr>",
        re.S,
    )

    filing_row_pattern = re.compile(
        r"<tr>\s*"
        r"<td[^>]*>\s*(\d+)\s*</td>\s*"
        r"<td class=\"tL\">(.*?)</td>\s*"
        r"<td class=\"tL\">(.*?)</td>\s*"
        r"<td class=\"tL ellipsis\"[^>]*>(.*?)</td>\s*"
        r"<td>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"</tr>",
        re.S,
    )

    report_link_pattern = re.compile(r'href="(/dsaf001/main\.do\?rcpNo=(\d+)[^"]*)"')

    def __init__(
        self,
        *,
        request_delay: float = 2.2,
        request_jitter: float = 0.8,
        company_delay: float = 4.0,
        max_attempts: int = 6,
    ) -> None:
        self.request_delay = request_delay
        self.request_jitter = request_jitter
        self.company_delay = company_delay
        self.max_attempts = max_attempts
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.last_request_at = 0.0
        self.warmed_up = False

    def sleep_between_companies(self) -> None:
        delay = max(0.0, self.company_delay + random.uniform(0.0, self.request_jitter))
        if delay:
            time.sleep(delay)

    def warmup(self) -> None:
        if self.warmed_up:
            return
        self.fetch_text("/main.do", referer_path="/main.do", warmup=False)
        self.fetch_text("/dsab001/main.do", referer_path="/main.do", warmup=False)
        self.warmed_up = True

    def throttle(self) -> None:
        wait_for = self.request_delay + random.uniform(0.0, self.request_jitter)
        elapsed = time.time() - self.last_request_at if self.last_request_at else None
        if elapsed is not None and elapsed < wait_for:
            time.sleep(wait_for - elapsed)

    def fetch_text(
        self,
        path: str,
        *,
        params: list[tuple[str, str]] | None = None,
        method: str = "GET",
        referer_path: str = "/dsab001/main.do",
        warmup: bool = True,
    ) -> str:
        if warmup:
            self.warmup()

        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        data = None
        if params:
            encoded = urllib.parse.urlencode(params).encode()
            if method == "GET":
                url = f"{url}?{encoded.decode()}"
            else:
                data = encoded

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "close",
            "Referer": f"{BASE_URL}{referer_path}",
        }
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            headers["Origin"] = BASE_URL
            headers["X-Requested-With"] = "XMLHttpRequest"

        request = urllib.request.Request(url, data=data, method=method, headers=headers)

        for attempt in range(1, self.max_attempts + 1):
            self.throttle()
            try:
                with self.opener.open(request, timeout=60) as response:
                    text = response.read().decode("utf-8", "ignore")
                self.last_request_at = time.time()
                return text
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                http.client.HTTPException,
                http.client.RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                ConnectionError,
                OSError,
            ):
                self.last_request_at = time.time()
                if attempt == self.max_attempts:
                    raise
                backoff = max(self.request_delay * attempt * 3, 5.0) + random.uniform(0.0, self.request_jitter)
                time.sleep(backoff)

        raise RuntimeError(f"Failed to fetch {url}")

    def resolve_company(self, company_name: str) -> dict | None:
        search_name = COMPANY_NAME_ALIASES.get(company_name, company_name)
        html_text = self.fetch_text(
            "/corp/searchCorp.ax",
            params=[("textCrpNm", search_name), ("histYn", "Y")],
            referer_path="/dsab001/main.do",
        )
        matches = []
        for cik, hidden_name, name_cell, ceo_cell, stock_code, industry_cell in self.popup_row_pattern.findall(html_text):
            label = strip_tags(name_cell).replace("유", "", 1).replace("코", "", 1).replace("넥", "", 1).replace("기", "", 1).strip()
            matches.append(
                {
                    "corpId": cik,
                    "companyName": hidden_name.strip() or label,
                    "displayName": label,
                    "ceoName": strip_tags(ceo_cell),
                    "stockCode": strip_tags(stock_code),
                    "industry": strip_tags(industry_cell),
                }
            )

        if not matches:
            return None

        wanted = normalize_name(company_name)
        alias_wanted = normalize_name(search_name)
        for match in matches:
            if normalize_name(match["companyName"]) == wanted or normalize_name(match["displayName"]) == wanted:
                return match
        for match in matches:
            if normalize_name(match["companyName"]) == alias_wanted or normalize_name(match["displayName"]) == alias_wanted:
                return match

        return matches[0]

    def search_company_filings(
        self,
        corp_id: str,
        company_name: str,
        start_date: str,
        end_date: str,
        *,
        max_results: int = DEFAULT_MAX_RESULTS,
        report_name: str | None = None,
        public_types: list[str] | None = None,
    ) -> list[dict]:
        params: list[tuple[str, str]] = [
            ("pageGubun", "corp"),
            ("currentPage", "1"),
            ("maxResults", str(max_results)),
            ("maxLinks", "10"),
            ("textCrpCik", corp_id),
            ("textCrpNm", company_name),
            ("startDate", start_date),
            ("endDate", end_date),
            ("finalReport", "recent"),
        ]
        if report_name:
            params.append(("reportName", report_name))
        for public_type in public_types or []:
            params.append(("publicType", public_type))

        html_text = self.fetch_text(
            "/dsab001/searchCorp.ax",
            params=params,
            method="POST",
            referer_path="/dsab001/main.do",
        )
        if "조회 결과가 없습니다" in html_text:
            return []

        rows: list[dict] = []
        for row_number, company_cell, report_cell, submitter_cell, date_cell, note_cell in self.filing_row_pattern.findall(html_text):
            link_match = self.report_link_pattern.search(report_cell)
            if not link_match:
                continue

            viewer_path, rcp_no = link_match.groups()
            rows.append(
                {
                    "rowNumber": int(row_number),
                    "companyName": company_name,
                    "reportName": strip_tags(report_cell),
                    "submitter": strip_tags(submitter_cell),
                    "rceptDate": strip_tags(date_cell).replace(".", ""),
                    "rceptDateDisplay": strip_tags(date_cell),
                    "rceptNo": rcp_no,
                    "viewerUrl": f"{BASE_URL}{viewer_path}",
                    "note": strip_tags(note_cell),
                    "companyCellText": strip_tags(company_cell),
                }
            )
        return dedupe_filings(rows)


def load_seed_companies(root: Path) -> list[dict]:
    return json.loads((root / "docs" / "data" / "companies.json").read_text(encoding="utf-8"))


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_company_map(
    root: Path,
    client: DartWebClient,
    *,
    limit: int | None = None,
    offset: int = 0,
    retry_pending: bool = False,
) -> list[dict]:
    seed_companies = load_seed_companies(root)
    companies = seed_companies[offset:]
    if limit:
        companies = companies[:limit]

    company_master_path = root / "working" / "raw" / "dart_web_company_master.json"
    existing_records = {}
    if company_master_path.exists():
        loaded = json.loads(company_master_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            existing_records = {item["companyId"]: item for item in loaded}

    records_by_id = dict(existing_records)
    gaps = []
    for company in companies:
        if company["companyId"] in existing_records:
            record = existing_records[company["companyId"]]
            if record.get("matchStatus") == "matched" or not retry_pending:
                if record.get("matchStatus") != "matched":
                    gaps.append({"companyId": company["companyId"], "name": company["name"]})
                continue

        record = dict(company)
        try:
            match = client.resolve_company(company["name"])
            if not match:
                record["matchStatus"] = "unmatched"
                gaps.append({"companyId": company["companyId"], "name": company["name"]})
            else:
                record.update(
                    {
                        "corpId": match["corpId"],
                        "stockCode": match["stockCode"] or company.get("stockCode"),
                        "resolvedName": match["companyName"],
                        "searchName": COMPANY_NAME_ALIASES.get(company["name"], company["name"]),
                        "ceoName": match["ceoName"],
                        "industryResolved": match["industry"],
                        "matchStatus": "matched",
                    }
                )
        except Exception as exc:
            record["matchStatus"] = "error"
            record["error"] = str(exc)
            gaps.append({"companyId": company["companyId"], "name": company["name"]})
        records_by_id[company["companyId"]] = record
        save_json(
            company_master_path,
            [records_by_id.get(company_row["companyId"], dict(company_row)) for company_row in seed_companies],
        )
        save_json(root / "working" / "review" / "dart_web_company_match_gaps.json", gaps)
        client.sleep_between_companies()

    final_records = [records_by_id.get(company_row["companyId"], dict(company_row)) for company_row in seed_companies]
    save_json(company_master_path, final_records)
    save_json(root / "working" / "review" / "dart_web_company_match_gaps.json", gaps)
    return final_records


def scan_latest_filings(
    root: Path,
    client: DartWebClient,
    *,
    start_date: str,
    end_date: str,
    limit: int | None = None,
    offset: int = 0,
    retry_pending: bool = False,
) -> dict[str, dict]:
    company_master_path = root / "working" / "raw" / "dart_web_company_master.json"
    if not company_master_path.exists():
        raise SystemExit("Run `sync-company-map` first.")

    companies = json.loads(company_master_path.read_text(encoding="utf-8"))
    companies = companies[offset:]
    if limit:
        companies = companies[:limit]

    latest_results_path = root / "working" / "raw" / "dart_web_latest_filings.json"
    results: dict[str, dict] = {}
    if latest_results_path.exists():
        loaded = json.loads(latest_results_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            results.update(loaded)
    for company in companies:
        if company.get("matchStatus") != "matched":
            continue
        if company["companyId"] in results and results[company["companyId"]].get("purposeMatches") and not retry_pending:
            continue
        try:
            filings = client.search_company_filings(
                company["corpId"],
                company["resolvedName"],
                start_date,
                end_date,
                max_results=300,
            )
            purpose_matches = {}
            for purpose, keywords in PURPOSE_REPORT_KEYWORDS.items():
                purpose_match = next(
                    (filing for filing in filings if any(keyword in filing["reportName"] for keyword in keywords)),
                    None,
                )
                if purpose_match is None:
                    public_type_hits = []
                    if PURPOSE_FALLBACK_PUBLIC_TYPES.get(purpose):
                        public_type_hits = client.search_company_filings(
                            company["corpId"],
                            company["resolvedName"],
                            start_date,
                            end_date,
                            max_results=30,
                            public_types=PURPOSE_FALLBACK_PUBLIC_TYPES[purpose],
                        )
                    purpose_match = public_type_hits[0] if public_type_hits else None
                if purpose_match is None:
                    fallback_hits = []
                    for report_name in PURPOSE_FALLBACK_REPORTS.get(purpose, []):
                        fallback_hits.extend(
                            client.search_company_filings(
                                company["corpId"],
                                company["resolvedName"],
                                start_date,
                                end_date,
                                max_results=20,
                                report_name=report_name,
                            )
                        )
                    fallback_hits = dedupe_filings(fallback_hits)
                    purpose_match = fallback_hits[0] if fallback_hits else None
                purpose_matches[purpose] = purpose_match

            results[company["companyId"]] = {
                "companyId": company["companyId"],
                "name": company["name"],
                "resolvedName": company["resolvedName"],
                "corpId": company["corpId"],
                "stockCode": company.get("stockCode"),
                "latestOverall": filings[0] if filings else None,
                "purposeMatches": purpose_matches,
                "filings": filings,
            }
        except Exception as exc:
            results[company["companyId"]] = {
                "companyId": company["companyId"],
                "name": company["name"],
                "resolvedName": company["resolvedName"],
                "corpId": company["corpId"],
                "stockCode": company.get("stockCode"),
                "latestOverall": None,
                "purposeMatches": {},
                "filings": [],
                "error": str(exc),
            }
        save_json(latest_results_path, results)
        client.sleep_between_companies()

    save_json(latest_results_path, results)
    return results


def enrich_targets(root: Path) -> None:
    latest_path = root / "working" / "raw" / "dart_web_latest_filings.json"
    if not latest_path.exists():
        raise SystemExit("Run `scan-latest` first.")

    company_master = json.loads((root / "working" / "raw" / "dart_web_company_master.json").read_text(encoding="utf-8"))
    targets_path = root / "docs" / "data" / "targets.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    latest = json.loads(latest_path.read_text(encoding="utf-8"))

    enriched = []
    for row in targets:
        company_index = latest.get(row["companyId"], {})
        matched = company_index.get("purposeMatches", {}).get(row["purpose"]) or company_index.get("latestOverall")
        row_copy = dict(row)
        if matched:
            row_copy["viewerUrl"] = matched["viewerUrl"]
            row_copy["rceptNo"] = matched["rceptNo"]
            row_copy["latestReportName"] = matched["reportName"]
            row_copy["latestReportDate"] = matched["rceptDateDisplay"]
            row_copy["sourceReports"] = [
                {
                    "reportName": matched["reportName"],
                    "rceptNo": matched["rceptNo"],
                    "rceptDate": matched["rceptDate"],
                    "viewerUrl": matched["viewerUrl"],
                    "purpose": row["purpose"],
                    "note": "공개 DART 웹검색 기반 최신 공시 링크",
                }
            ]
            row_copy["confidence"] = "latest_web_filing"
        enriched.append(row_copy)

    save_json(root / "working" / "normalized" / "targets_with_latest_links.json", enriched)
    save_json(root / "docs" / "data" / "targets_enriched.json", enriched)

    matched_companies = sum(1 for item in company_master if item.get("matchStatus") == "matched")
    latest_count = sum(1 for item in latest.values() if item.get("latestOverall"))
    purpose_coverage = {purpose: 0 for purpose in PURPOSE_REPORT_KEYWORDS}
    for item in latest.values():
        for purpose in purpose_coverage:
            if item.get("purposeMatches", {}).get(purpose):
                purpose_coverage[purpose] += 1
    metadata = {
        "snapshotDate": DEFAULT_END[:4] + "-" + DEFAULT_END[4:6] + "-" + DEFAULT_END[6:8],
        "snapshotDateDisplay": f"{DEFAULT_END[:4]}.{DEFAULT_END[4:6]}.{DEFAULT_END[6:8]}",
        "companyCount": len(company_master),
        "rowCount": len(enriched),
        "dataMode": "web_latest_links",
        "dataModeLabel": "웹검색 최신공시 링크 연동",
        "topMeta": f"웹검색 최신공시 링크 연동 100개사 · 기준일 {DEFAULT_END[:4]}.{DEFAULT_END[4:6]}.{DEFAULT_END[6:8]}",
        "heroNote": "※ 영업 근거 문구와 수치는 아직 시드 데이터이며, 각 행의 공시 링크는 2026년 8월 6일 기준 공개 DART 최신 공시로 연결됩니다.",
        "sourceSummary": f"{matched_companies}개사 식별 성공, {latest_count}개사는 최신 공시 링크 확보",
        "nextAction": "다음 단계는 목적별 공시 본문/정기보고서 주석을 열어 실제 수치와 이벤트 날짜를 행 단위로 교체하는 것입니다.",
        "generatedAt": "2026-08-06T00:00:00+09:00",
        "matchCoverage": {
            "companiesResolved": matched_companies,
            "latestOverallAvailable": latest_count,
            "unmatchedCompanies": [item["name"] for item in company_master if item.get("matchStatus") != "matched"],
            "purposeCoverage": purpose_coverage,
        },
    }
    save_json(root / "docs" / "data" / "metadata_enriched.json", metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build latest filing indexes from the public DART web search UI without using the OpenDART API.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-company-map", help="Resolve the seed 100-company list to DART web company identifiers.")
    sync_parser.add_argument("--limit", type=int, default=None, help="Only process the first N companies for testing.")
    sync_parser.add_argument("--offset", type=int, default=0, help="Skip the first N companies before processing.")
    sync_parser.add_argument("--retry-pending", action="store_true", help="Retry existing unmatched/error companies instead of skipping them.")

    scan_parser = subparsers.add_parser("scan-latest", help="Fetch latest public filing lists for the resolved company map.")
    scan_parser.add_argument("--begin", default=DEFAULT_BEGIN, help=f"Start date in YYYYMMDD format. Default: {DEFAULT_BEGIN}")
    scan_parser.add_argument("--end", default=DEFAULT_END, help=f"End date in YYYYMMDD format. Default: {DEFAULT_END}")
    scan_parser.add_argument("--limit", type=int, default=None, help="Only process the first N companies for testing.")
    scan_parser.add_argument("--offset", type=int, default=0, help="Skip the first N resolved companies before processing.")
    scan_parser.add_argument("--retry-pending", action="store_true", help="Retry existing empty/error filing snapshots instead of skipping them.")

    subparsers.add_parser("enrich-targets", help="Attach latest matched filing links to the current target rows.")

    parser.add_argument("--request-delay", type=float, default=2.2, help="Minimum delay in seconds between DART requests.")
    parser.add_argument("--request-jitter", type=float, default=0.8, help="Random extra delay in seconds added per request/company.")
    parser.add_argument("--company-delay", type=float, default=4.0, help="Extra delay in seconds after each company.")
    parser.add_argument("--max-attempts", type=int, default=6, help="Maximum retry attempts per HTTP request.")

    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    client = DartWebClient(
        request_delay=args.request_delay,
        request_jitter=args.request_jitter,
        company_delay=args.company_delay,
        max_attempts=args.max_attempts,
    )

    if args.command == "sync-company-map":
        sync_company_map(
            root,
            client,
            limit=args.limit,
            offset=args.offset,
            retry_pending=args.retry_pending,
        )
        return

    if args.command == "scan-latest":
        scan_latest_filings(
            root,
            client,
            start_date=args.begin,
            end_date=args.end,
            limit=args.limit,
            offset=args.offset,
            retry_pending=args.retry_pending,
        )
        return

    if args.command == "enrich-targets":
        enrich_targets(root)
        return


if __name__ == "__main__":
    main()
