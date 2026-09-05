"""호흡(쉼표 구간) 검사 — ★쉼표 자동 삽입은 2026-08-20부터 기본 중단.

★2026-08-20 개정 — 자동 삽입을 끈 이유 (05편 실측)
    05편 대본의 쉼표 817개 중 **348개(42.6%)가 조사 바로 뒤**에 찍혀 있었다.
    아래 BREAK_TAIL/STOP_NEXT 가드가 목적어+동사 짝을 못 막기 때문이다.
        "왜 남의 것을, 가져왔느냐고 물었습니다"   "아무것도 묻지 않은, 까닭을"
    문법적으로 틀린 자리이고 Vrew 낭독이 그 자리에서 숨을 끊는다.

    그리고 애초에 **삽입의 근거였던 "자막 한 줄 20자"가 파이프라인과 맞지 않는다** —
    자막은 `split_long_cues.py`가 `settings.subtitle.max_chars`(현재 **13자**) 기준으로
    어절 경계에서 다시 자른다. 대본에 쉼표를 박아도 자막 분할은 그 쉼표를 따르지 않고,
    남는 것은 잘못된 낭독 호흡뿐이다.

    → 이 도구는 **긴 호흡을 찾아 주는 검사기**로 쓴다. 걸린 문장은 쉼표가 아니라
      **문장을 둘로 나누어** 고친다(script-guide §5). 기본 상한도 20 → 26자로 올렸다.
      옛 동작이 꼭 필요하면 `--legacy-commas --apply`.
      이미 박힌 쉼표를 걷어내려면 `--undo-commas --apply`.

원칙 (세 모드 공통)
    - **문구를 절대 바꾸지 않는다.** 넣든 빼든 쉼표 하나뿐이다.
    - 따옴표(대사) 줄은 건드리지 않는다. 낭독 톤이 흔들린다.
    - `--undo-commas`는 ①앞 어절이 조사로 끝나고 ②지워도 그 줄 최장 호흡이 limit 이하일 때만
      지운다. 조건 ②가 없으면 자막이 안 들어가는 긴 호흡이 생긴다.

과거 이력 (--legacy-commas 로 남아 있는 옛 동작의 설계)
    - 넣는 자리는 조사/어미 뒤 어절 경계 중 문장 한가운데에 가장 가까운 곳.
    - 넣어도 상한을 못 맞추면 건드리지 않고 경고로 남긴다.
    - **한계**: BREAK_TAIL이 "을/를"을 후보로 두는데 STOP_NEXT가 일반 용언을 못 막아
      `남의 것을, 가져왔느냐고` 같은 목적어-동사 분리가 생긴다. 이것이 중단 사유다.

사용법
    python3 scripts/script/fix_breath.py {S}/chapters                       # 검사(기본)
    python3 scripts/script/fix_breath.py {S}/chapters --undo-commas --apply # 조사 뒤 쉼표 제거
    python3 scripts/script/fix_breath.py {S}/chapters --legacy-commas --apply  # 옛 동작(권장 안 함)
"""
import argparse
import pathlib
import re
import sys

BREATH_SPLIT_RE = re.compile(r'[,.!?…:;"“”]')

# 조사 바로 뒤에 찍힌 쉼표 — 호흡을 맞추려고 구(句)를 끊어 놓은 자리다.
# 긴 조사를 먼저 적어야 짧은 것에 먼저 걸리지 않는다(에서 → 에).
COMMA_AFTER_JOSA = re.compile(
    r'[가-힣](?:에서|에게|부터|까지|으로|보다|처럼|한테|라고|이나|밖에|마다|조차'
    r'|을|를|이|가|은|는|의|에|로|와|과|도|만)(?P<c>,)(?=\s)')

# 이 조사·어미로 끝나는 어절 뒤가 끊어 읽기 좋은 자리다.
# ★"은/는/을/를"도 넣는다 — 관형형 어미일 때가 문제인데(“말을 끊은, 것은”),
#   그 경우는 아래 STOP_NEXT가 막는다(뒤에 의존명사가 오면 후보에서 뺀다).
#   두 장치를 같이 써야 “것은,” 뒤에서 끊는 자연스러운 자리를 잃지 않는다(2026-08-17).
BREAK_TAIL = (
    "에서", "에게", "으로", "까지", "부터", "보다", "처럼", "라고", "하고", "한테",
    "이나", "이며", "이고", "지만", "는데", "아서", "어서", "니까", "면서", "다가",
    "고는", "고도", "에는", "에도", "이는", "이가", "은데", "것은", "것도", "것이",
    "은", "는", "이", "가", "을", "를", "에", "도", "만", "와", "과",
    "고", "며", "면", "자", "서", "니",
)

# 이런 말 앞에서는 끊지 않는다 — 앞말이 그것을 꾸미는 관형어라 붙여 읽어야 한다.
STOP_NEXT = (
    "것", "일", "때", "수", "줄", "데", "바", "뿐", "만큼", "듯", "적", "채", "뒤",
    "김", "탓", "덕", "무렵", "자리", "사람", "이", "게", "지",
    "하게", "하고", "하는", "한", "할", "하지", "하니",
)


def nospace(s: str) -> int:
    return len(re.sub(r"\s", "", s))


def worst_segment(line: str) -> int:
    return max((nospace(seg) for seg in BREATH_SPLIT_RE.split(line)), default=0)


def insert_comma(line: str, limit: int) -> str | None:
    """가장 긴 호흡 구간을 어절 경계에서 쪼갠다. 못 쪼개면 None."""
    segments = BREATH_SPLIT_RE.split(line)
    target = max(segments, key=lambda s: nospace(s)) if segments else ""
    if nospace(target) <= limit:
        return None

    start = line.find(target)
    words = target.split()
    if len(words) < 2:
        return None

    # 어절 경계 후보 — 조건을 다 만족하는 자리만 쓴다. 억지로 넣지 않는다.
    #   ① 앞뒤 두 조각이 모두 limit 이하   ② 앞 어절이 조사·연결어미로 끝남
    #   ③ 뒤 어절이 의존명사·보조용언이 아님(관형어와 떼어 놓으면 어색하다)
    best, best_score = None, None
    pos = 0
    for i, w in enumerate(words[:-1]):
        pos = target.find(w, pos) + len(w)
        left, right = target[:pos], target[pos:]
        if nospace(left) > limit or nospace(right) > limit:
            continue
        if not any(w.endswith(t) for t in BREAK_TAIL):
            continue
        nxt = words[i + 1]
        if any(nxt.startswith(s) for s in STOP_NEXT):
            continue
        score = abs(nospace(left) - nospace(right))   # 가운데에 가까울수록 좋다
        if best_score is None or score < best_score:
            best, best_score = pos, score

    if best is None:
        return None
    new_target = target[:best] + "," + target[best:]
    return line[:start] + new_target + line[start + len(target):]


def process(path: pathlib.Path, limit: int, apply: bool) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    fixed = unfixable = 0
    out = []
    for ln, line in enumerate(lines, 1):
        stripped = line.strip()
        if worst_segment(line) <= limit or stripped.startswith(('"', '“')):
            out.append(line)
            continue
        new = insert_comma(line, limit)
        if new is None:
            unfixable += 1
            print(f"  ✗ L{ln} 자동 분할 불가 ({worst_segment(line)}자) — 문구를 고치세요")
            print(f"      {stripped}")
            out.append(line)
        else:
            fixed += 1
            if not apply:
                print(f"  · L{ln}  {stripped}")
                print(f"      → {new.strip()}")
            out.append(new)
    if apply and fixed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return fixed, unfixable


def audit(path: pathlib.Path, limit: int) -> int:
    """긴 호흡을 찾아 보여만 준다 — 고치는 것은 사람(문장 분할)."""
    over = 0
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        w = worst_segment(line)
        if w > limit and not line.strip().startswith(('"', '“')):
            over += 1
            print(f"  · L{ln} ({w}자) {line.strip()}")
    return over


def undo_commas(path: pathlib.Path, limit: int, apply: bool) -> int:
    """조사 뒤에 박힌 호흡용 쉼표를 걷어낸다 (2026-08-20).

    지우는 조건 둘 — ①앞 어절이 조사로 끝난다 ②지워도 그 줄의 최장 호흡이 limit 이하다.
    문구는 절대 바꾸지 않는다. 지우는 것은 쉼표 하나뿐이다.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    removed = 0
    out = []
    for ln, line in enumerate(lines, 1):
        if line.strip().startswith(('"', '“')):
            out.append(line)
            continue
        cur = line
        while True:
            cand = None
            for m in COMMA_AFTER_JOSA.finditer(cur):
                trial = cur[:m.start('c')] + cur[m.end('c'):]      # 쉼표 한 개만 제거
                if worst_segment(trial) <= limit:
                    cand = trial
                    break
            if cand is None:
                break
            cur = cand
            removed += 1
        if cur != line and not apply:
            print(f"  · L{ln} {line.strip()}")
            print(f"      → {cur.strip()}")
        out.append(cur)
    if apply and removed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="호흡(쉼표 구간) 검사 — 쉼표 자동 삽입은 2026-08-20부터 기본 중단")
    ap.add_argument("path", help="대본 파일 또는 chapters 폴더")
    ap.add_argument("--limit", type=int, default=26,
                    help="호흡 최대 글자수 (기본 26 — 자막은 split_long_cues가 13자로 다시 자른다)")
    ap.add_argument("--apply", action="store_true", help="실제로 수정 (없으면 미리보기)")
    ap.add_argument("--legacy-commas", action="store_true",
                    help="★비권장 — 옛 동작(쉼표 자동 삽입). 05편에서 조사 뒤 쉼표 348개를 만든 그 동작이다")
    ap.add_argument("--undo-commas", action="store_true",
                    help="조사 뒤에 박힌 호흡용 쉼표를 걷어낸다")
    args = ap.parse_args()

    p = pathlib.Path(args.path)
    targets = sorted(p.glob("*.md")) if p.is_dir() else [p]
    if not targets:
        print(f"대상 파일 없음: {p}")
        return 1

    if args.undo_commas:
        total = 0
        for t in targets:
            print(f"\n{t.name}")
            n = undo_commas(t, args.limit, args.apply)
            total += n
            print(f"  {'제거' if args.apply else '제거 예정'} {n}건")
        print(f"\n합계 {total}건" + ("" if args.apply else "  — 적용하려면 --apply"))
        return 0

    if args.legacy_commas:
        print("⚠ 쉼표 자동 삽입은 비권장입니다 (2026-08-20) — 걸린 문장은 둘로 나누는 것이 맞습니다\n")
        total_fixed = total_bad = 0
        for t in targets:
            print(f"\n{t.name}")
            fixed, bad = process(t, args.limit, args.apply)
            total_fixed += fixed
            total_bad += bad
            print(f"  {'수정' if args.apply else '수정 예정'} {fixed}건"
                  + (f" · 수동 필요 {bad}건" if bad else ""))
        print(f"\n합계 {total_fixed}건" + (f" · 수동 {total_bad}건" if total_bad else ""))
        if not args.apply:
            print("실제 적용하려면 --apply")
        return 1 if total_bad else 0

    # 기본 = 검사만
    total = 0
    for t in targets:
        print(f"\n{t.name}")
        n = audit(t, args.limit)
        total += n
        print(f"  호흡 {args.limit}자 초과 {n}건" if n else f"  ✓ 호흡 {args.limit}자 이하")
    print(f"\n합계 {total}건 — ★쉼표를 넣지 말고 문장을 둘로 나누세요 (script-guide §5)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
