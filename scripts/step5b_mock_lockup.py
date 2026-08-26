"""보호예수(락업) 샘플 데이터 생성 — 화면 검증용.

실데이터 연결 전까지 화면을 완성하기 위한 mock 이다.
`is_mock: true` 를 데이터와 화면에 명시해 실측치와 섞이지 않게 한다.

스키마는 2026-08-14 데이터요구사항의 `lockup` 테이블을 따른다.
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
random.seed(20260813)

BROKERS = [
    ("미래에셋증권", 0.14), ("NH투자증권", 0.13), ("한국투자증권", 0.12),
    ("KB증권", 0.11), ("삼성증권", 0.10), ("신한투자증권", 0.09),
    ("대신증권", 0.07), ("키움증권", 0.06), ("하나증권", 0.05),
    ("메리츠증권", 0.05), ("유진투자증권", 0.04), ("교보증권", 0.04),
]
HOLDER = [("최대주주", 0.34), ("특수관계인", 0.30), ("기관", 0.22),
          ("우리사주", 0.10), ("기타", 0.04)]
PERIODS = [(6, "상장 후 6개월"), (12, "상장 후 1년"),
           (18, "상장 후 1년 6개월"), (24, "상장 후 2년"), (36, "상장 후 3년")]

NAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
GIVEN = ["성한", "동현", "지훈", "민서", "예준", "서연", "도윤", "하윤", "시우", "주원"]


def pick(pairs):
    r, acc = random.random(), 0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def main():
    comps = json.loads((ROOT / "data" / "companies.json").read_text(encoding="utf-8"))["companies"]
    master = json.loads((ROOT / "data" / "market_master.json").read_text(encoding="utf-8"))
    base = date.today()

    # 락업이 성립하려면 최근 상장이어야 한다. 표본에서 절반을 '2023년 이후 상장'으로 가정한다.
    pool = [c for c in comps if c.get("market_cap")]
    random.shuffle(pool)
    targets = pool[: max(30, len(pool) // 2)]

    rows = []
    for c in targets:
        m = master.get(c["name"], {})
        price = m.get("price") or 10000
        listing = base - timedelta(days=random.randint(120, 1080))
        lead = pick(BROKERS)
        # 대표주관사가 보호예수를 전담한다(3~4년 전 제도 변경). 수탁 = 대표주관사가 기본.
        n = random.randint(2, 6)
        for _ in range(n):
            months, label = random.choice(PERIODS)
            rel = listing + timedelta(days=int(months * 30.44))
            if rel < base - timedelta(days=200):
                continue
            qty = random.randint(30_000, 4_000_000)
            # 공시에 수탁기관이 안 적힌 건이 섞인다 — 미상/추정 구분을 화면에서 보여주기 위함
            r = random.random()
            if r < 0.62:
                broker, src = lead, "기재"
            elif r < 0.88:
                broker, src = lead, "추정"
            else:
                broker, src = None, "미상"
            rows.append({
                "corp_name": c["name"],
                "stock_code": m.get("stock_code") or c.get("stock_code"),
                "corp_code": c["corp_code"],
                "industry": m.get("industry"),
                "market_cap": m.get("market_cap"),
                "listing_date": listing.isoformat(),
                "holder_name": (random.choice(NAMES) + random.choice(GIVEN)
                                if random.random() < .55 else
                                random.choice(["국민연금공단", "우리사주조합",
                                               "한국투자파트너스", "스틱인베스트먼트",
                                               "IMM인베스트먼트"])),
                "holder_type": pick(HOLDER),
                "lockup_qty": qty,
                "lockup_value": int(qty * price),
                "value_base_date": base.isoformat(),
                "release_date": rel.isoformat(),
                "lockup_period": label,
                "custody_broker": broker,
                "custody_broker_src": src,
                "lead_manager": lead,
                "rcept_no": f"2026{random.randint(10,12)}{random.randint(10,28):02d}{random.randint(100000,999999)}",
                "is_mock": True,
            })

    for r in rows:
        r["source_url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"

    out = ROOT / "data" / "lockup_mock.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    fut = [r for r in rows if r["release_date"] >= base.isoformat()]
    print(f"lockup_mock.json — {len(rows)}건 / {len({r['corp_name'] for r in rows})}개사")
    print(f"  해제 예정(미래) {len(fut)}건 · 수탁 기재 "
          f"{sum(1 for r in rows if r['custody_broker_src']=='기재')} · "
          f"추정 {sum(1 for r in rows if r['custody_broker_src']=='추정')} · "
          f"미상 {sum(1 for r in rows if r['custody_broker_src']=='미상')}")


if __name__ == "__main__":
    main()
