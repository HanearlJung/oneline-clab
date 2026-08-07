# 데이터 계약

두 단계에서 쓰는 스키마. Phase 2(에이전트가 직접 추출)와 Phase 3(화면 데이터)이 여기에 맞춘다.

---

## 1. `extracted/{corp_code}.json` — Phase 2 산출

에이전트가 `slices/{corp_code}/*.md` 를 **직접 읽어서** 작성한다.

> **원칙: 원문에 없으면 `null`. 추측·보간 금지.**
> 결측은 Phase 3에서 커버리지로 집계되어 화면에 정직하게 표기된다.

```jsonc
{
  "corp_code": "00126380",
  "name": "삼성전자",
  "market": "KOSPI",
  "source": {                    // 이 회사 데이터의 출처. 링크가 실제로 열려야 한다
    "rcp_no": "20260515002181",
    "report_nm": "분기보고서 (2026.03)",
    "rcept_dt": "20260515",
    "as_of": "2026-03-31"        // 보고서 기준일 (회사마다 다름 — 화면에 표기)
  },

  "financial_assets": {          // 없으면 null
    "unit": "백만원",            // 원문 단위를 그대로 적는다. 환산은 Phase 3에서
    "total": 276102125,
    "amortized_cost": 252334454,
    "fvoci": 22090048,
    "fvtpl": 1626577,
    "short_term_deposits": 74048085,
    "products": ["채무증권", "수익증권"],   // 원문에 있으면
    "maturity_buckets": [        // 개별 만기일은 대개 없고 구간으로만 공시된다
      {"label": "1년 이내", "amount": 12345}
    ]
  },

  "pension": {
    "unit": "백만원",
    "dbo": 17658229,             // 확정급여채무 현재가치
    "plan_assets": 20353960,     // 사외적립자산 공정가치
    "net_liability": -2695731,   // 순확정급여부채. 음수면 초과적립(자산)
    "funding_ratio": 115.3,      // plan_assets/dbo*100. 계산값
    "service_cost": 396422,
    "plan_type": "DB"
  },

  "stock_comp": {                // 제도가 없는 회사가 많다 → 대부분 null
    "unit": "주",
    "types": ["스톡옵션"],       // 스톡옵션/RSU/RSA/PSU
    "granted": 120000,
    "outstanding": 80000,
    "exercise_price": 45000,
    "exercise_start": "2027-03-01",
    "exercise_end": "2032-02-28"
  },

  "shareholders": {
    "largest": "이재용",
    "largest_ratio": 18.2,
    "change_date": null,
    "prev_largest": null,
    "reason": null
  },

  "treasury": [                  // 자사주 처분/취득 결정. 이벤트 배열
    {
      "event_type": "처분",      // 처분/취득
      "rcp_no": "20260610000123",
      "resolved_on": "2026-06-10",
      "shares": 1000000,
      "amount": 85000,           // 단위 백만원
      "period_start": "2026-06-15",
      "period_end": "2026-09-14",
      "purpose": "임직원 상여 지급",
      "method": "장내매도"
    }
  ],

  "pledge": [                    // 대주주·특수관계인 주식담보
    {
      "rcp_no": "20260520000456",
      "holder": "홍길동",
      "relation": "최대주주",
      "contract_type": "주식담보제공계약",
      "counterparty": "OO은행",
      "shares": 500000,
      "loan_amount": 30000,      // 공시 의무가 아니라 결측이 흔하다
      "contract_start": "2026-05-20",
      "maturity": "2027-05-19"   // 없는 경우 많음 → null
    }
  ]
}
```

---

## 2. `data/site_data.json` — Phase 3 산출 (화면이 직접 읽음)

```jsonc
{
  "meta": {
    "baseDate": "2026-08-07",
    "companyCount": 100,
    "generated": "2026-08-07T05:30:00"
  },
  "tabs": [
    {
      "key": "pension",
      "title": "퇴직연금",
      "desc": "DB 부채·적립금 규모 기반 운용 영업",
      "upcoming": "적립률이 낮아 우선 접촉할 퇴직연금 영업 건입니다.",
      "metricLabel": "확정급여채무",
      "timeColumn": {"label": "적립률", "mode": "none"},
      "sortOptions": [["ratio","적립률 낮은 순"],["amount","규모 큰 순"],["company","기업명 순"]],
      "sortDefault": "ratio",
      "filters": [
        {"label":"확정급여채무","field":"amount","type":"min",
         "options":[["","전체"],["1000","1,000억 이상"]]},
        {"label":"적립률","field":"ratio","type":"max",
         "options":[["","전체"],["80","80% 미만"]]}
      ],
      "coverage": {"covered": 87, "total": 100},
      "rows": [ /* 아래 */ ]
    }
  ]
}
```

### row

| 필드 | 설명 |
|---|---|
| `name` `code` `market` `industry` | 기업 식별. `industry`는 없으면 `null` |
| `reason` | "왜 대상인가" — 표 2열 굵은 줄 |
| `detail` | 그 아래 근거 한 줄 |
| `amount` | 정렬·필터용 숫자. **억원 단위로 통일** |
| `metricText` | "관련 규모" 열에 그대로 출력할 문자열 |
| `ratio` | 적립률 등 비율. 없으면 `null` |
| `dateValue` | `YYYY-MM-DD` 또는 `null` |
| `timeText` | 시간 열에 출력할 문자열 ("2026.09.30 · D-54", "만기 미공시") |
| `action` | 제안 방향 |
| `chips` | 활용 데이터 배지 |
| `rcpNo` `sourceUrl` | 원문 링크. **반드시 실제로 열려야 한다** |
| `asOf` | 이 값의 기준일 (보고서마다 달라 화면에 표기) |

### `timeColumn.mode`

항목마다 날짜의 성격이 달라서 열 라벨과 정렬을 다르게 둔다. **가짜 날짜를 만들지 않는다.**

| mode | 쓰는 탭 | 열 라벨 | 의미 |
|---|---|---|---|
| `dday` | 자사주, 주식보상, 담보(만기 공시분) | 권장 영업일 / D-day | 미래 이벤트까지 남은 일수 |
| `elapsed` | 대주주 자금운용 | 발생 후 경과 | 과거 이벤트로부터 지난 일수 |
| `range` | 금융상품 운용 | 만기구간 | 개별 만기일이 없어 구간으로만 공시됨 |
| `none` | 퇴직연금 | 적립률 | 날짜 개념 자체가 없음 |

### `filters[].type`

| type | 판정 |
|---|---|
| `min` | `row[field] >= Number(value)` |
| `max` | `row[field] < Number(value)` |
| `eq` | `row[field] === value` |
| `withinMonths` | `row.dateValue` 가 기준일로부터 N개월 이내 |
