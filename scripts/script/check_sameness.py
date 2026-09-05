"""옴니버스 6편이 '한 편을 여섯 번 들은 것'처럼 들리지 않는지 기계로 잰다.

왜 필요한가 (2026-08-18 사용자 지적)
    "6개 내에서는 좀 같은 느낌이 들지 않도록 다양하게 이야기가 진행되면 좋겠어."
    script-guide §7의 4축 대조(주인공·결핍·촉발·해결)는 **소재 층위**만 본다. 그런데
    시청자가 "돌려쓰기 하는 건가요?"라고 느끼는 지점은 소재가 아니라 **문장 층위**다 —
    편이 달라도 같은 상투구로 서술하면 같은 이야기로 들린다. 벤치 ①에 실제로 그 댓글이
    달렸다(§7). 소재를 갈라 놓고도 문장이 같으면 소용이 없으므로 여기서 따로 잰다.

무엇을 재는가
    1. **공통 상투구** — 여러 편에 그대로 나오는 구절.
       ★임계는 편 수에 비례한다 (2026-08-20 개정) — 위반 = 편 수의 3분의 2 이상,
       경고 = 절반 이상. 6편이면 4편/3편, **3편이면 2편/2편**이다.
       종전에는 "4편 이상"으로 고정돼 있어 **3편 포맷에서는 세 편이 전부 공유해도
       위반이 나올 수 없었다** — 05편 실측에서 `아무 말도 하지`·`오래 앉아 있었습니다`가
       세 편에 다 있는데도 통과로 찍혔다.
    2. **문장 첫머리 쏠림** — `그런데`·`그러고는`으로 시작하는 문장의 비율.
       한 편에서 15%를 넘으면 리듬이 단조롭다.
    3. **편별 어휘 겹침률** — 두 편이 쓰는 명사·용언이 얼마나 겹치는가(자카드).
       35%를 넘는 쌍은 소재가 달라도 서술 질감이 같다는 뜻이다.

사용법
    python3 scripts/script/check_sameness.py {S}/chapters
    python3 scripts/script/check_sameness.py {S}/chapters --min-len 7 --top 40
"""
import argparse
import collections
import itertools
import math
import pathlib
import re
import sys

# 상투구 판정에서 뺀다 — 이 포맷이 의도적으로 공유하는 것들
ALLOW = (
    "이야기는 오늘날까지",      # 닫는 정형구 (§4 — 로테이션이 따로 관리한다)
    "오래도록 행복하게",
    "평생 행복하게",
    "남은 날을 보냈다고",
)

RE_WORD = re.compile(r"[가-힣]{2,}")
RE_QUOTE = re.compile(r'^\s*["“]')
OPENERS = ("그런데", "그러고는", "그러자", "그래서", "다만", "그러다")


def sentences(path: pathlib.Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not RE_QUOTE.match(l)]


def phrases(sents: list[str], n: int) -> set[str]:
    """어절 n개짜리 구절 집합. 조사까지 같아야 상투구다."""
    out = set()
    for s in sents:
        w = re.sub(r"[.,!?…]", "", s).split()
        for i in range(len(w) - n + 1):
            out.add(" ".join(w[i:i + n]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="6편이 서로 같은 느낌인지 기계 검사")
    ap.add_argument("path", help="chapters 폴더")
    ap.add_argument("--min-len", type=int, default=None,
                    help="상투구로 볼 어절 수 (기본: 5편 이상이면 3, 4편 이하면 4)")
    ap.add_argument("--top", type=int, default=25, help="출력할 상투구 개수")
    ap.add_argument("--overlap-limit", type=float, default=35.0, help="어휘 겹침 상한 %%")
    args = ap.parse_args()

    files = sorted(pathlib.Path(args.path).glob("*.md"))
    if len(files) < 2:
        print("비교할 편이 2개 미만입니다")
        return 1

    sents = {f.stem: sentences(f) for f in files}
    bad = 0

    # 1. 공통 상투구 — 임계를 편 수에 비례시킨다 (3편 포맷 대응, 2026-08-20)
    n_ep = len(files)
    bad_at = max(2, math.ceil(n_ep * 2 / 3))    # 6편→4 · 3편→2
    warn_at = max(2, math.ceil(n_ep / 2))       # 6편→3 · 3편→2
    # ★편이 적으면 창을 넓힌다 — 3어절은 편이 셋뿐일 때 "그 말을 듣고" 같은
    #   평범한 연결 어구까지 걸려 신호가 묻힌다(05편 실측 200건 이상).
    min_len = args.min_len if args.min_len else (3 if n_ep >= 5 else 4)
    owner = collections.defaultdict(set)
    for name, ss in sents.items():
        for p in phrases(ss, min_len):
            owner[p].add(name)
    shared = {p: v for p, v in owner.items()
              if len(v) >= warn_at and not any(a in p for a in ALLOW)}
    print(f"\n[1] 공통 상투구 — {warn_at}편 이상에 그대로 나오는 {min_len}어절 구절"
          f"  (총 {n_ep}편 · 위반 {bad_at}편 이상)")
    if not shared:
        print("  없음 ✓")
    for p, v in sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:args.top]:
        mark = "✗" if len(v) >= bad_at else "△"
        if len(v) >= bad_at:
            bad += 1
        print(f"  {mark} {len(v)}편  \"{p}\"   ({', '.join(sorted(v))})")

    # 2. 문장 첫머리 쏠림
    # ★2026-08-29부터 참고값 — 통과/실패에 넣지 않는다.
    #   05~10편 실측이 2.6~3.9%로 15% 상한에 한 번도 닿지 않았다. 붙잡지 못하는 상한을 들고 있으면
    #   "통과"가 안심을 준다. §7-1 뼈대 8축 확대의 one-out으로 내렸다.
    print("\n[2] 문장 첫머리 쏠림 — 접속 부사로 여는 문장 비율 (참고값)")
    for name, ss in sents.items():
        c = sum(1 for s in ss if s.startswith(OPENERS))
        pct = c * 100 / len(ss)
        mark = "△" if pct > 15 else "·"
        top = collections.Counter(s.split()[0] for s in ss if s.startswith(OPENERS))
        detail = " · ".join(f"{w} {n}" for w, n in top.most_common(3))
        print(f"  {mark} {name}  {pct:4.1f}%  ({c}/{len(ss)})   {detail}")

    # 3. 편별 어휘 겹침
    # ★2026-08-29부터 참고값 — 실측 17~21%로 35% 상한에 닿은 적이 없다.
    print(f"\n[3] 편 사이 어휘 겹침률 (참고값 · 종전 상한 {args.overlap_limit:.0f}%)")
    vocab = {n: set(RE_WORD.findall(" ".join(ss))) for n, ss in sents.items()}
    rows = []
    for a, b in itertools.combinations(sorted(vocab), 2):
        inter = len(vocab[a] & vocab[b])
        pct = inter * 100 / len(vocab[a] | vocab[b])
        rows.append((pct, a, b, inter))
    for pct, a, b, inter in sorted(rows, reverse=True)[:8]:
        mark = "△" if pct > args.overlap_limit else "·"
        print(f"  {mark} {a} ↔ {b}   {pct:4.1f}%  (공통 낱말 {inter})")

    print(f"\n{'위반 ' + str(bad) + '건 — 고치고 다시 재세요' if bad else '통과 ✓'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
