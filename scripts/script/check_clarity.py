"""귀로 한 번 들어서 뜻이 잡히지 않는 문장을 찾는다.

왜 필요한가 (2026-08-18 사용자 지적)
    "이야기를 빠르게 훑었는데 머리속으로 바로바로 이해가 되지 않아."
    "무슨 소리인지 모르겠는 이야기들이 좀 많아. 쉽게 풀어써줘야 해."

    guide §5-2는 **낱말**이 어려운 것을 막는다(사초·봉분·조리돌림). 그런데 낱말이 다 쉬워도
    문장이 안 잡히는 경우가 따로 있다. 자다 듣는 시청자에게는 되감기가 없으므로,
    **한 문장 안에서 '누가 무엇을 했는지'가 끝나야** 한다.

무엇을 잡는가
    1. **격언 문장** — 인물이 없고 일반론으로 끝나는 문장("사람은 …하는 법입니다").
       한둘은 여운이지만, 연달아 나오면 이야기가 멈추고 훈계가 된다.
    2. **지시어만 있는 문장** — 주어가 '그것/그 말/그 일'뿐이라 앞 문장을 기억해야만 풀린다.
    3. **되짚어야 풀리는 문장** — 부정이 겹치거나('안 …한 것이 아니라'),
       한 문장에 절이 셋 이상 들어간 것.
    4. **격언 연속** — 위 1번이 두 문장 이상 붙어 있는 구간(제일 위험하다).

    잡힌 문장이 다 틀린 것은 아니다. **고칠 자리를 보여 주는 것**이 이 도구의 목적이다.

사용법
    python3 scripts/script/check_clarity.py {S}/chapters
    python3 scripts/script/check_clarity.py {S}/chapters/01.md --show
"""
import argparse
import pathlib
import re
import sys

RE_QUOTE = re.compile(r'^\s*["“]')
# 인물이 있다고 보는 표지 — 고유명사는 알 수 없으므로 일반 지칭까지 센다
PERSON = re.compile(r"(는|은|이|가|을|를|에게|한테)\s*$|[가-힣]{2,3}(은|는|이|가)\s"
                    r"|사내|노인|처녀|아이|여자|사람들|어미|아비|아들|딸|손녀|주모|객주|마님")
APHORISM = re.compile(r"(법입니다|법이지요|법이니까요|법이랍니다|것입니다|것이지요|것이니까요|"
                      r"것이랍니다|셈입니다|셈이지요)$")
GENERAL = re.compile(r"^(사람은|사람이|말은|일은|셈은|그것은|이것은|누구나|다들|원래)")
DEICTIC = re.compile(r"^(그것|그 말|그 일|그 자리|그 뒤|그 돈|그 표|그 종이|그 사람)")
DOUBLE_NEG = re.compile(r"(안|않|못)[^.]{0,12}(것이 아니|게 아니|리가 없|수 없)")
# "A가 아니라 B다" — 듣는 사람이 A를 붙들고 B를 기다려야 해서 한 박자 늦게 잡힌다
CONTRAST = re.compile(r"(것이|이|가|은|는)\s*아니라")


def clauses(s: str) -> int:
    return len(re.findall(r"[,]|(?:고|며|면서|는데|지만|어서|아서|니까)\s", s))


def analyse(path: pathlib.Path):
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    flags = []
    for i, s in enumerate(lines):
        if RE_QUOTE.match(s):
            flags.append(None)
            continue
        why = []
        core = s.rstrip('."\'?! ')
        if APHORISM.search(core) or GENERAL.match(s):
            why.append("격언")
        if CONTRAST.search(s):
            why.append("A아니라B")
        if DEICTIC.match(s):
            why.append("지시어시작")
        if DOUBLE_NEG.search(s):
            why.append("이중부정")
        if clauses(s) >= 3:
            why.append("절3개+")
        flags.append(why or None)

    runs = []
    start = None
    for i, f in enumerate(flags):
        if f and "격언" in f:
            start = i if start is None else start
        else:
            if start is not None and i - start >= 2:
                runs.append((start, i - 1))
            start = None
    return lines, flags, runs


def main() -> int:
    ap = argparse.ArgumentParser(description="귀로 안 잡히는 문장 찾기")
    ap.add_argument("path", help="대본 파일 또는 chapters 폴더")
    ap.add_argument("--show", action="store_true", help="걸린 문장을 전부 출력")
    ap.add_argument("--max-aphorism", type=float, default=8.0, help="격언 문장 비율 상한 %%")
    args = ap.parse_args()

    p = pathlib.Path(args.path)
    targets = sorted(p.glob("*.md")) if p.is_dir() else [p]
    bad = 0
    for t in targets:
        lines, flags, runs = analyse(t)
        n = len(lines)
        cnt = {}
        for f in flags:
            for w in (f or []):
                cnt[w] = cnt.get(w, 0) + 1
        pct = cnt.get("격언", 0) * 100 / n
        mark = "✗" if pct > args.max_aphorism or runs else "✓"
        if mark == "✗":
            bad += 1
        detail = " · ".join(f"{k} {v}" for k, v in sorted(cnt.items()))
        print(f"{mark} {t.stem}  문장 {n}  격언 {pct:.1f}%  |  {detail}")
        for a, b in runs:
            print(f"    ★격언 {b - a + 1}연속 @L{a + 1}")
            for j in range(a, b + 1):
                print(f"        {lines[j]}")
        if args.show:
            for j, f in enumerate(flags):
                if f and "격언" not in f:
                    print(f"    [{'/'.join(f)}] {lines[j]}")
    print(f"\n{'위반 ' + str(bad) + '편' if bad else '통과 ✓'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
