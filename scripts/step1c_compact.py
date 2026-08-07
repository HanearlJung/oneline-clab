"""Phase 1-c — 조각에서 표만 남긴다.

주석 조각의 대부분은 회계정책 서술이라 숫자가 없다. 값은 전부 표에 있다.
표 블록과 그 직전 캡션만 남기면 분량이 크게 줄고, 읽을 때 놓치는 값은 없다.

해석은 하지 않는다. 표를 고르거나 값을 뽑지 않고, 표가 아닌 문단만 버린다.

산출: compact/{corp_code}/{topic}.md
"""
import argparse

from dart import ROOT

LEAD_LINES = 2      # 표 앞 캡션·단위 표기를 살린다 ("(단위 : 백만원)" 등)
MIN_CELLS = 2       # '|' 가 2개 미만이면 표로 보지 않는다


def compact(text: str) -> str:
    lines = text.split("\n")
    keep = [False] * len(lines)

    for i, line in enumerate(lines):
        if line.count("|") >= MIN_CELLS:
            keep[i] = True
            for j in range(max(0, i - LEAD_LINES), i):
                # 직전 빈 줄이 아닌 설명 줄만 캡션으로 살린다
                if lines[j].strip():
                    keep[j] = True

    out, gap = [], False
    for i, line in enumerate(lines):
        if keep[i]:
            out.append(line)
            gap = False
        elif not gap:
            out.append("…")   # 생략 표시 — 읽는 쪽이 잘렸음을 알 수 있게
            gap = True
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    src_root, dst_root = ROOT / "slices", ROOT / "compact"
    before = after = 0
    n = 0
    for src in sorted(src_root.rglob("*.md")):
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        if dst.exists() and not args.force:
            continue
        text = src.read_text(encoding="utf-8")
        # 헤더(--- 위쪽)는 출처 정보라 그대로 유지한다
        head, sep, body = text.partition("\n---\n\n")
        result = head + sep + compact(body) if sep else compact(text)

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(result, encoding="utf-8")
        before += len(text)
        after += len(result)
        n += 1

    if n:
        print(f"압축 {n}개 파일  {before/1024/1024:.2f}MB -> {after/1024/1024:.2f}MB "
              f"({after/before*100:.0f}%)")
    else:
        print("새로 압축할 파일 없음")


if __name__ == "__main__":
    main()
