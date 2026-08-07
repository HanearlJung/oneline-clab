#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SNAPSHOT_DATE = "2026-08-06"
SNAPSHOT_DATE_DISPLAY = "2026.08.06"
PURPOSE_KEYS = [
    "investment",
    "pension",
    "blockdeal",
    "pledge",
    "employee",
    "wealth",
]

NAMES = [
    "삼성전자", "SK하이닉스", "현대자동차", "기아", "NAVER", "카카오", "LG에너지솔루션", "삼성바이오로직스", "셀트리온", "POSCO홀딩스",
    "KB금융", "신한지주", "하나금융지주", "우리금융지주", "삼성생명", "삼성화재", "LG화학", "삼성SDI", "LG전자", "현대모비스",
    "한화에어로스페이스", "두산에너빌리티", "한국전력", "HMM", "KT", "SK텔레콤", "크래프톤", "넷마블", "엔씨소프트", "카카오게임즈",
    "하이브", "JYP Ent.", "에스엠", "CJ ENM", "CJ제일제당", "오리온", "농심", "오뚜기", "이마트", "신세계",
    "롯데쇼핑", "아모레퍼시픽", "LG생활건강", "코스맥스", "한국콜마", "유한양행", "한미약품", "대웅제약", "녹십자", "SK바이오팜",
    "삼성E&A", "HD현대중공업", "HD한국조선해양", "한화오션", "현대로템", "한국항공우주", "대한항공", "아시아나항공", "제주항공", "호텔신라",
    "강원랜드", "파라다이스", "GS리테일", "BGF리테일", "현대백화점", "F&F", "휠라홀딩스", "한섬", "영원무역", "LS ELECTRIC",
    "효성중공업", "대한전선", "일진전기", "두산밥캣", "현대건설", "DL이앤씨", "GS건설", "대우건설", "삼성물산", "SK이노베이션",
    "S-Oil", "GS", "롯데케미칼", "금호석유", "코오롱인더", "한화솔루션", "OCI홀딩스", "풍산", "고려아연", "삼성증권",
    "미래에셋증권", "NH투자증권", "한국금융지주", "키움증권", "DB손해보험", "현대해상", "메리츠금융지주", "카카오뱅크", "기업은행", "에코프로비엠",
]

INDUSTRIES = [
    "전기전자", "반도체", "자동차", "인터넷", "바이오", "철강", "금융", "화학", "통신", "게임", "미디어",
    "식품", "유통", "화장품", "제약", "조선", "항공", "호텔·레저", "패션", "전력기기", "건설", "에너지",
]


@dataclass
class Company:
    company_id: str
    name: str
    market: str
    industry: str
    seed_rank: int

    def to_dict(self) -> dict:
        return {
            "companyId": self.company_id,
            "name": self.name,
            "market": self.market,
            "industry": self.industry,
            "seedRank": self.seed_rank,
            "corpCode": None,
            "stockCode": None,
            "snapshotDate": SNAPSHOT_DATE,
        }


def hash_value(text: str) -> int:
    h = 2166136261
    for char in text:
        h ^= ord(char)
        h = (h * 16777619) & 0xFFFFFFFF
    return abs(h)


def format_date(year: int, month: int, day: int) -> str:
    return f"{year}.{month:02d}.{day:02d}"


def d_day(day_offset: int) -> str:
    if day_offset <= 0:
        return "D-DAY"
    return f"D-{day_offset}"


def event_date(months_after_snapshot: int, seed: int) -> tuple[int, int, int, int]:
    base_month = 8
    month = base_month + months_after_snapshot
    year = 2026
    while month > 12:
        month -= 12
        year += 1
    day = 7 + (seed % 18)
    day_offset = months_after_snapshot * 30 + (seed % 18) + 1
    return year, month, day, day_offset


def historic_event(months_ago: int, seed: int) -> str:
    month = 8 - months_ago
    year = 2026
    while month <= 0:
        month += 12
        year -= 1
    day = 5 + (seed % 20)
    return format_date(year, month, day)


def to_eok(value: int) -> str:
    if value >= 10000:
        jo = value / 10000
        return f"{jo:.0f}조원" if jo.is_integer() else f"{jo:.1f}조원"
    return f"{value:,}억원"


def build_companies() -> list[Company]:
    companies: list[Company] = []
    for index, name in enumerate(NAMES):
        companies.append(
            Company(
                company_id=f"seed-{index + 1:03d}",
                name=name,
                market="KOSDAQ" if index % 5 == 0 else "KOSPI",
                industry=INDUSTRIES[index % len(INDUSTRIES)],
                seed_rank=index + 1,
            )
        )
    return companies


def source_stub(purpose: str) -> list[dict]:
    return [
        {
            "reportName": "데모 시드",
            "rceptNo": None,
            "rceptDate": SNAPSHOT_DATE.replace("-", ""),
            "viewerUrl": "https://dart.fss.or.kr/",
            "purpose": purpose,
            "note": "실공시 전환 전 구조 검증용 시드 데이터",
        }
    ]


def make_row(company: Company, purpose: str) -> dict:
    seed = hash_value(company.name + purpose)

    if purpose == "investment":
        amount = 500 + (seed % 19500)
        months = 1 + (seed % 14)
        maturity = 100 + (seed % 2800)
        year, month, day, day_offset = event_date(months, seed)
        asset_type = ["회사채", "채권형 펀드", "해외채권", "단기금융상품"][seed % 4]
        return {
            "id": f"{purpose}-{company.company_id}",
            "purpose": purpose,
            "companyId": company.company_id,
            "name": company.name,
            "market": company.market,
            "industry": company.industry,
            "amount": amount,
            "months": months,
            "dateTs": day_offset,
            "priorityDate": format_date(year, month, day),
            "timing": f"{format_date(year, month, day)} · {d_day(day_offset)}",
            "reason": f"{asset_type} {to_eok(amount)} 보유",
            "detail": f"{format_date(year, month, day)} {to_eok(maturity)} 만기·처분 예정",
            "action": f"만기·처분 자금에 맞는 {asset_type} 재투자 상품 제안",
            "chips": ["금융자산", "투자이력"],
            "confidence": "seed",
            "viewerUrl": "https://dart.fss.or.kr/",
            "sourceReports": source_stub(purpose),
        }

    if purpose == "pension":
        amount = 700 + (seed % 14500)
        rate = 68 + (seed % 43)
        assets = round(amount * rate / 100)
        months = 0 if rate < 80 else 1 if rate < 90 else 3
        year, month, day, day_offset = event_date(months, seed)
        return {
            "id": f"{purpose}-{company.company_id}",
            "purpose": purpose,
            "companyId": company.company_id,
            "name": company.name,
            "market": company.market,
            "industry": company.industry,
            "amount": amount,
            "rate": rate,
            "months": months,
            "dateTs": day_offset,
            "priorityDate": format_date(year, month, day),
            "timing": f"{format_date(year, month, day)} · {d_day(day_offset)}",
            "reason": f"확정급여채무 {to_eok(amount)} · 적립률 {rate}%",
            "detail": f"사외적립자산 {to_eok(assets)} 보유",
            "action": "DB 추가 적립 및 적립금 운용 제안" if rate < 90 else "퇴직연금 적립금 운용기관 다변화 제안",
            "chips": ["퇴직연금"],
            "confidence": "seed",
            "viewerUrl": "https://dart.fss.or.kr/",
            "sourceReports": source_stub(purpose),
        }

    if purpose == "blockdeal":
        event_type = ["자사주 처분", "보호예수 해제"][seed % 2]
        amount = 80 + (seed % 1900)
        quantity = 100000 + (seed % 4900000)
        months = 1 + (seed % 12)
        year, month, day, day_offset = event_date(months, seed)
        chip = "자사주" if event_type == "자사주 처분" else "보호예수"
        action = "처분 방식 검토 및 블록딜 주관 제안" if event_type == "자사주 처분" else "해제 주주 대상 블록딜·매각 실행 제안"
        return {
            "id": f"{purpose}-{company.company_id}",
            "purpose": purpose,
            "companyId": company.company_id,
            "name": company.name,
            "market": company.market,
            "industry": company.industry,
            "amount": amount,
            "eventType": event_type,
            "months": months,
            "dateTs": day_offset,
            "priorityDate": format_date(year, month, day),
            "timing": f"{format_date(year, month, day)} · {d_day(day_offset)}",
            "reason": f"{event_type} {quantity:,}주 예정",
            "detail": f"예상 규모 {to_eok(amount)} · {format_date(year, month, day)} 예정",
            "action": action,
            "chips": [chip],
            "confidence": "seed",
            "viewerUrl": "https://dart.fss.or.kr/",
            "sourceReports": source_stub(purpose),
        }

    if purpose == "pledge":
        amount = 100 + (seed % 1900)
        months = 1 + (seed % 12)
        year, month, day, day_offset = event_date(months, seed)
        holder = ["최대주주", "특수관계인", "등기임원"][seed % 3]
        pledged = 200000 + (seed % 4800000)
        lender = ["은행권", "증권사", "저축은행"][seed % 3]
        return {
            "id": f"{purpose}-{company.company_id}",
            "purpose": purpose,
            "companyId": company.company_id,
            "name": company.name,
            "market": company.market,
            "industry": company.industry,
            "amount": amount,
            "months": months,
            "dateTs": day_offset,
            "priorityDate": format_date(year, month, day),
            "timing": f"{format_date(year, month, day)} · {d_day(day_offset)}",
            "reason": f"{holder} 주식담보대출 {to_eok(amount)}",
            "detail": f"담보주식 {pledged:,}주 · {lender} · {format_date(year, month, day)} 만기",
            "action": "담보대출 신규·증액 또는 만기 리파이낸싱 제안",
            "chips": ["주식담보"],
            "confidence": "seed",
            "viewerUrl": "https://dart.fss.or.kr/",
            "sourceReports": source_stub(purpose),
        }

    if purpose == "employee":
        comp_type = ["RSU", "RSA", "스톡옵션", "PSU"][seed % 4]
        qty = 50000 + (seed % 2400000)
        months = 1 + (seed % 12)
        year, month, day, day_offset = event_date(months, seed)
        return {
            "id": f"{purpose}-{company.company_id}",
            "purpose": purpose,
            "companyId": company.company_id,
            "name": company.name,
            "market": company.market,
            "industry": company.industry,
            "qty": qty,
            "amount": qty,
            "compType": comp_type,
            "months": months,
            "dateTs": day_offset,
            "priorityDate": format_date(year, month, day),
            "timing": f"{format_date(year, month, day)} · {d_day(day_offset)}",
            "reason": f"{comp_type} 지급·행사 예정",
            "detail": f"예정 수량 {qty:,}주 · {format_date(year, month, day)} 예정",
            "action": "임직원 계좌 개설·주식 매매·세무·자산관리 제안",
            "chips": ["주식보상"],
            "confidence": "seed",
            "viewerUrl": "https://dart.fss.or.kr/",
            "sourceReports": source_stub(purpose),
        }

    change_type = ["대주주 변경", "임원 퇴임"][seed % 2]
    months_ago = 1 + (seed % 12)
    amount = 300 + (seed % 4700) if change_type == "대주주 변경" else 50 + (seed % 950)
    contact_year, contact_month, contact_day, day_offset = event_date(0, seed)
    action = "지분 매각대금의 랩·신탁·채권형 상품 운용 제안" if change_type == "대주주 변경" else "퇴직금의 IRP 이전 및 개인 자산관리 제안"
    reason = f"구 대주주 지분 매각대금 {to_eok(amount)} 유입 가능" if change_type == "대주주 변경" else f"퇴임 임원 퇴직금·보유주식 매각대금 {to_eok(amount)} 운용 가능"
    return {
        "id": f"{purpose}-{company.company_id}",
        "purpose": purpose,
        "companyId": company.company_id,
        "name": company.name,
        "market": company.market,
        "industry": company.industry,
        "amount": amount,
        "changeType": change_type,
        "months": months_ago,
        "dateTs": day_offset,
        "priorityDate": format_date(contact_year, contact_month, contact_day),
        "timing": f"{format_date(contact_year, contact_month, contact_day)} · {d_day(day_offset)}",
        "reason": reason,
        "detail": f"{historic_event(months_ago, seed)} {change_type} 공시",
        "action": action,
        "chips": ["대주주변경" if change_type == "대주주 변경" else "임원퇴임"],
        "confidence": "seed",
        "viewerUrl": "https://dart.fss.or.kr/",
        "sourceReports": source_stub(purpose),
    }


def build_metadata(company_count: int, row_count: int) -> dict:
    return {
        "snapshotDate": SNAPSHOT_DATE,
        "snapshotDateDisplay": SNAPSHOT_DATE_DISPLAY,
        "companyCount": company_count,
        "rowCount": row_count,
        "dataMode": "demo_seed",
        "dataModeLabel": "데모 시드",
        "topMeta": f"데모 시드 {company_count}개사 · 기준일 {SNAPSHOT_DATE_DISPLAY}",
        "heroNote": "※ 현재는 실공시 추출 전 단계로, UI와 데이터 스키마를 검증하기 위한 시드 데이터입니다.",
        "sourceSummary": "현재 원문 링크는 공통 DART 홈으로 연결됩니다. 실공시 스냅샷을 만들면 행별 rcept_no 링크로 교체됩니다.",
        "nextAction": "다음 단계는 최신 공시를 목적별로 연결하고, 시드 문구를 실제 수치·이벤트 날짜로 교체하는 것입니다.",
        "generatedAt": "2026-08-06T00:00:00+09:00",
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "docs" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    companies = build_companies()
    rows = [make_row(company, purpose) for purpose in PURPOSE_KEYS for company in companies]

    (data_dir / "companies.json").write_text(
        json.dumps([company.to_dict() for company in companies], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (data_dir / "targets.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (data_dir / "metadata.json").write_text(
        json.dumps(build_metadata(len(companies), len(rows)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
