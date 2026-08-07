#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


BASE_URL = "https://opendart.fss.or.kr/api"
DEFAULT_BEGIN = "20260101"
DEFAULT_END = "20260806"


class OpenDartClient:
    def __init__(self, api_key: str, throttle_seconds: float = 0.2) -> None:
        self.api_key = api_key
        self.throttle_seconds = throttle_seconds

    def _get(self, endpoint: str, params: dict, expect_binary: bool = False):
        query = urllib.parse.urlencode({"crtfc_key": self.api_key, **params})
        url = f"{BASE_URL}/{endpoint}?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
        time.sleep(self.throttle_seconds)
        if expect_binary:
            return payload
        data = json.loads(payload.decode("utf-8"))
        status = data.get("status")
        if status not in (None, "000", "013"):
            raise RuntimeError(f"{endpoint} failed: {status} {data.get('message', '')}".strip())
        return data

    def fetch_corp_codes(self) -> list[dict]:
        payload = self._get("corpCode.xml", {}, expect_binary=True)
        xml_bytes = self._extract_corp_code_xml(payload)

        root = ET.fromstring(xml_bytes)
        items: list[dict] = []
        for node in root.findall(".//list"):
            item = {}
            for child in node:
                item[child.tag] = (child.text or "").strip()
            items.append(item)
        return items

    def _extract_corp_code_xml(self, payload: bytes) -> bytes:
        if payload.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = archive.namelist()
                if not names:
                    raise RuntimeError("corpCode.xml response zip is empty")
                return archive.read(names[0])

        if payload.lstrip().startswith(b"<?xml") or payload.lstrip().startswith(b"<result"):
            root = ET.fromstring(payload)
            status = (root.findtext("status") or "").strip()
            message = (root.findtext("message") or "").strip()
            if status and status != "000":
                raise RuntimeError(f"corpCode.xml failed: {status} {message}".strip())
            return payload

        raise RuntimeError("corpCode.xml returned an unknown payload format")

    def search_disclosures(
        self,
        corp_code: str,
        begin: str,
        end: str,
        *,
        pblntf_ty: str | None = None,
        pblntf_detail_ty: str | None = None,
        last_report_only: bool = True,
        page_count: int = 100,
    ) -> list[dict]:
        params = {
            "corp_code": corp_code,
            "bgn_de": begin,
            "end_de": end,
            "sort": "date",
            "sort_mth": "desc",
            "page_count": str(page_count),
            "last_reprt_at": "Y" if last_report_only else "N",
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        if pblntf_detail_ty:
            params["pblntf_detail_ty"] = pblntf_detail_ty
        data = self._get("list.json", params)
        return data.get("list", [])

    def fetch_company_overview(self, corp_code: str) -> dict:
        return self._get("company.json", {"corp_code": corp_code})


def normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def load_seed_companies(root: Path) -> list[dict]:
    path = root / "docs" / "data" / "companies.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_company_index(root: Path, client: OpenDartClient) -> list[dict]:
    seed_companies = load_seed_companies(root)
    corp_codes = client.fetch_corp_codes()

    by_name = {normalize_name(item.get("corp_name", "")): item for item in corp_codes}
    enriched: list[dict] = []
    unmatched: list[dict] = []

    for company in seed_companies:
        match = by_name.get(normalize_name(company["name"]))
        record = dict(company)
        if not match:
            record["matchStatus"] = "unmatched"
            unmatched.append({"name": company["name"]})
            enriched.append(record)
            continue

        record.update(
            {
                "corpCode": match.get("corp_code"),
                "stockCode": match.get("stock_code") or None,
                "modifyDate": match.get("modify_date"),
                "matchStatus": "matched",
            }
        )
        try:
            overview = client.fetch_company_overview(match["corp_code"])
        except Exception as exc:  # noqa: BLE001
            record["overviewError"] = str(exc)
        else:
            record["corpNameEn"] = overview.get("corp_name_eng")
            record["ceoName"] = overview.get("ceo_nm")
            record["corpCls"] = overview.get("corp_cls")
            record["industryCode"] = overview.get("induty_code")
            record["homepage"] = overview.get("hm_url")
        enriched.append(record)

    save_json(root / "working" / "raw" / "opendart_company_master.json", enriched)
    save_json(root / "working" / "review" / "opendart_company_match_gaps.json", unmatched)
    return enriched


def scan_filings(root: Path, client: OpenDartClient, begin: str, end: str) -> dict:
    companies = json.loads((root / "working" / "raw" / "opendart_company_master.json").read_text(encoding="utf-8"))
    scopes = {
        "periodic": {"pblntf_ty": "A"},
        "major": {"pblntf_ty": "B"},
        "equity": {"pblntf_ty": "D"},
        "other_events": {"pblntf_ty": "E"},
        "securities": {"pblntf_ty": "C"},
    }
    result: dict[str, dict] = {}

    for company in companies:
        corp_code = company.get("corpCode")
        if not corp_code:
            continue

        company_result = {
            "companyId": company["companyId"],
            "name": company["name"],
            "corpCode": corp_code,
            "stockCode": company.get("stockCode"),
            "ranges": {},
        }
        for scope, params in scopes.items():
            try:
                filings = client.search_disclosures(corp_code, begin, end, **params)
            except Exception as exc:  # noqa: BLE001
                company_result["ranges"][scope] = {"error": str(exc), "items": []}
            else:
                company_result["ranges"][scope] = {"count": len(filings), "items": filings[:100]}
        result[company["companyId"]] = company_result

    save_json(root / "working" / "raw" / "opendart_filing_index.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare OpenDART company mappings and filing indexes for the corporate sales targeting demo. "
            "This script requires the OPENDART_API_KEY environment variable."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync-company-map", help="Match the 100-company seed list against OpenDART corpCode and company overview APIs.")

    filings_parser = subparsers.add_parser("scan-filings", help="Scan latest filings by company and store a local filing index.")
    filings_parser.add_argument("--begin", default=DEFAULT_BEGIN, help=f"Start date in YYYYMMDD format. Default: {DEFAULT_BEGIN}")
    filings_parser.add_argument("--end", default=DEFAULT_END, help=f"End date in YYYYMMDD format. Default: {DEFAULT_END}")

    args = parser.parse_args()

    api_key = os.environ.get("OPENDART_API_KEY")
    if not api_key:
        raise SystemExit("OPENDART_API_KEY is required")

    root = Path(__file__).resolve().parents[1]
    client = OpenDartClient(api_key)

    if args.command == "sync-company-map":
        build_company_index(root, client)
        return

    if not (root / "working" / "raw" / "opendart_company_master.json").exists():
        raise SystemExit("Run `sync-company-map` first to create working/raw/opendart_company_master.json")

    scan_filings(root, client, args.begin, args.end)


if __name__ == "__main__":
    main()
