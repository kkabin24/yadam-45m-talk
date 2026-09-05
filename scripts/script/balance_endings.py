"""종결어미 3박자(습니다 / 죠·지요 / 어요·네요) 비율을 목표 범위로 맞춘다.

왜 필요한가 (2026-08-17 실측)
    script-guide §5는 종결어미 비율을 수치로 못 박는다 — `~습니다` 45~55%,
    `~죠/~지요` 10~15%, `~어요/~네요` 8~12%. 이 세 어미를 번갈아 쓰는 것이 채널 목소리다.
    그런데 집필 중에는 이 비율을 세면서 쓸 수가 없다. 한 편을 쓰고 나면 매번
    한쪽으로 쏠린다(03편 실측: 3편은 어요 22%로 넘치고, 4편은 습니다 81%로 쏠렸다).
    **뜻을 바꾸지 않고 어미만 바꾸는 일**이므로 기계가 한다.

★2026-08-20 개정 — 자동 교체는 기본 중단(검사 전용)
    교체 위치를 **의미가 아니라 난수로 고른다**(아래 `rng.shuffle(pool)`). 어느 문장이
    `~지요`로 정서를 얹을 자리인지는 문맥이 정하는 것이지 시드 7번이 정할 일이 아니다.
    그리고 §5의 목표 비율 자체가 **직접대사 22.3%인 벤치 ②를 그대로 옮긴 값**이라
    단일 나레이션인 우리 대본에는 전제가 맞지 않는다(§5 주석이 이미 그렇게 적고 있다).
    → **비율은 목표가 아니라 관측치**로 쓴다. 이 도구는 쏠림을 보여 주고, 고치는 것은
      사람이 문맥을 보고 한다. 옛 동작이 꼭 필요하면 `--legacy-rewrite --apply`.

원칙
    - 문장의 뜻·어순·낱말을 바꾸지 않는다. 어미만 교체한다.
    - `았/었/했 + 습니다`처럼 **교체해도 반드시 자연스러운 형태만** 건드린다.
      (`~입니다`·`~합니다`처럼 현재형·지정사는 손대지 않는다 — 어색해지기 쉽다)
    - 대사(따옴표 줄)와 편의 **첫 문장·마지막 문장**은 건드리지 않는다.
    - 목표 비율에 닿으면 멈춘다. 필요한 만큼만 바꾼다.

사용법
    python3 scripts/script/balance_endings.py {S}/chapters/04.md           # 미리보기
    python3 scripts/script/balance_endings.py {S}/chapters/04.md --apply
    python3 scripts/script/balance_endings.py {S}/chapters --apply         # 폴더 일괄
"""
import argparse
import pathlib
import random
import re
import sys

# script-guide §5 목표 (%)
TARGET = {"seumnida": (45, 55), "jyo": (10, 15), "eoyo": (8, 12)}

RE_SEUMNIDA = re.compile(r"(습니다|ㅂ니다)$")
RE_JYO = re.compile(r"(죠|지요)$")
RE_EOYO = re.compile(r"(어요|아요|예요|이에요|네요|세요|워요|해요)$")

# 습니다 → 지요 / 어요 : 과거형 어간만 안전하게 교체한다
TO_JYO = [(re.compile(r"았습니다\.$"), "았지요."), (re.compile(r"었습니다\.$"), "었지요."),
          (re.compile(r"했습니다\.$"), "했지요."), (re.compile(r"였습니다\.$"), "였지요.")]
TO_EOYO = [(re.compile(r"았습니다\.$"), "았어요."), (re.compile(r"었습니다\.$"), "었어요."),
           (re.compile(r"했습니다\.$"), "했어요."), (re.compile(r"였습니다\.$"), "였어요.")]
# 그래도 습니다가 남으면 전언체(~답니다)로 보낸다 — 야담 나레이션에 자연스럽고
# 정규식 분류상 '기타'로 잡혀 세 어미의 비율을 눌러 준다.
TO_OTHER = [(re.compile(r"았습니다\.$"), "았답니다."), (re.compile(r"었습니다\.$"), "었답니다."),
            (re.compile(r"했습니다\.$"), "했답니다.")]


def classify(s: str) -> str:
    core = s.rstrip('."\'?!… ”')
    if RE_SEUMNIDA.search(core):
        return "seumnida"
    if RE_JYO.search(core):
        return "jyo"
    if RE_EOYO.search(core):
        return "eoyo"
    return "other"


def convert(line: str, rules) -> str | None:
    for pat, rep in rules:
        if pat.search(line):
            return pat.sub(rep, line)
    return None


def process(path: pathlib.Path, apply: bool, seed: int) -> bool:
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = [i for i, l in enumerate(lines)
           if l.strip() and not l.strip().startswith(('"', '“'))]
    if len(idx) < 20:
        print("  문장이 너무 적어 건너뜁니다")
        return True

    total = len(idx)
    counts = {k: 0 for k in ("seumnida", "jyo", "eoyo", "other")}
    for i in idx:
        counts[classify(lines[i])] += 1

    def pct(k):
        return counts[k] * 100 / total

    print(f"  현재  습니다 {pct('seumnida'):.1f}% · 죠 {pct('jyo'):.1f}% · 어요 {pct('eoyo'):.1f}%")

    # 편 처음·끝 두 문장은 톤의 얼굴이라 건드리지 않는다
    pool = [i for i in idx[2:-2] if classify(lines[i]) == "seumnida"]
    rng = random.Random(seed)
    rng.shuffle(pool)

    changed = 0
    for key, rules in (("jyo", TO_JYO), ("eoyo", TO_EOYO)):
        lo, hi = TARGET[key]
        need = int(total * (lo + hi) / 2 / 100) - counts[key]
        for i in list(pool):
            if need <= 0 or pct("seumnida") <= TARGET["seumnida"][0]:
                break
            new = convert(lines[i], rules)
            if new is None:
                continue
            lines[i] = new
            pool.remove(i)
            counts["seumnida"] -= 1
            counts[key] += 1
            need -= 1
            changed += 1

    # 3차 — 습니다가 여전히 상한을 넘으면 전언체로 덜어낸다
    for i in list(pool):
        if pct("seumnida") <= TARGET["seumnida"][1]:
            break
        new = convert(lines[i], TO_OTHER)
        if new is None:
            continue
        lines[i] = new
        pool.remove(i)
        counts["seumnida"] -= 1
        counts["other"] += 1
        changed += 1

    print(f"  {'수정' if apply else '수정 예정'} {changed}건"
          f"  →  습니다 {pct('seumnida'):.1f}% · 죠 {pct('jyo'):.1f}% · 어요 {pct('eoyo'):.1f}%")
    if apply and changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok = all(TARGET[k][0] <= pct(k) <= TARGET[k][1] for k in TARGET)
    if not ok:
        print("  ⚠ 목표 범위에 못 미칩니다 — 과거형 문장이 부족합니다(현재형·지정사는 안 건드립니다)")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="종결어미 3박자 비율 검사 — 자동 교체는 2026-08-20부터 기본 중단")
    ap.add_argument("path", help="대본 파일 또는 chapters 폴더")
    ap.add_argument("--apply", action="store_true", help="실제로 수정 (--legacy-rewrite 필요)")
    ap.add_argument("--legacy-rewrite", action="store_true",
                    help="★비권장 — 옛 동작(난수로 고른 문장의 어미를 교체)")
    ap.add_argument("--seed", type=int, default=7, help="교체 위치 셔플 시드(재현용)")
    args = ap.parse_args()

    apply = args.apply and args.legacy_rewrite
    if args.apply and not args.legacy_rewrite:
        print("⚠ 어미 자동 교체는 중단됐습니다 (2026-08-20) — 교체 위치를 난수로 고르기 때문입니다.")
        print("  비율은 목표가 아니라 관측치입니다. 쏠린 편만 문맥을 보고 손으로 고치세요.")
        print("  그래도 옛 동작이 필요하면: --legacy-rewrite --apply\n")

    p = pathlib.Path(args.path)
    targets = sorted(p.glob("*.md")) if p.is_dir() else [p]
    all_ok = True
    for t in targets:
        print(f"\n{t.name}")
        all_ok &= process(t, apply, args.seed)
    if not apply:
        print("\n(검사만 했습니다 — 파일은 그대로입니다)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
