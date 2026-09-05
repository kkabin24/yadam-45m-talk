#!/usr/bin/env python3
"""이탈률·조회수 관점 대본 검사 (2026-09-06 신설 · 사용자 지시).

    python3 scripts/script/check_retention.py {S}/chapters [--cpm 284] [--map]

문체 검사(validate_script --style)와 무엇이 다른가 — **저것은 문장을 재고 이것은 시간을 잰다.**
시청자는 문장을 세지 않고 **분 단위로 지루해진다.** 그래서 대본을 낭독 시간 축에 펼쳐 놓고
2분짜리 창을 밀어 가며 "이 창에서 귀가 붙잡힐 이유가 하나라도 있는가"를 묻는다.

검사 항목 (근거는 prompts/review-retention.md)

  H1 훅          첫 문장 안에 직접대사가 있는가                      (§3-0)
  H2 첫 사건     ②우연한 사건이 일곱 번째 문장 전에 시작되는가       (§3-1)
  H3 첫 3분      도입 3분 안에 대사 덩어리가 셋 이상인가             ★이탈이 여기 몰린다
  R1 최장 공백   대사와 대사 사이 최장 구간(분)                      (§5-7, 대사클립 모드 2.0)
  R2 죽은 창     2분 창 중 '대사 0 + 장면전환 0'인 창의 개수와 위치   ★이 검사의 핵심
  R3 설명 연속   인물도 대사도 없는 문단이 연달아 몇 개인가          (§3-4)
  R4 전환 밀도   십 분당 시간·장소 전환 표지 수                      (§3-5 하한)
  T1 뒷심        마지막 오분의 일 구간의 대사 밀도 / 전체 평균        ★후반 급락 탐지

--map 을 주면 분 단위 이탈 위험 지도를 그린다.
"""
import argparse
import pathlib
import re
import sys

DEFAULT_CPM = 284          # script-guide §1 현행 승계값
WINDOW_MIN = 2.0           # 창 크기(분) — 대사클립 간격과 같다
OPEN_MIN = 3.0             # 도입 방어 구간(분)

# 시간·장소가 바뀌는 신호. 이게 없으면 같은 자리에 머무는 서술이다.
TURN = re.compile(
    r"(그러던|그날|그 뒤|그 이듬해|이듬해|하루는|어느 날|그때|바로 그때|며칠|사흘|열흘|한 달|"
    r"봄이|여름이|가을이|겨울이|해가 바뀌|새벽|아침에|낮에|저녁|밤에|이튿날|다음 날|넷째 날|"
    r"셋째 날|둘째 날|첫날|그해|장터|강가|산에|마당|부엌|방으로|문을 나서|길을 나서)")

# 사람이 무언가 하는 문장인지 — 인물 지시어·행위 동사
# ★행위 동사를 열거하지 않는다 — 한국어 동사는 끝이 없어 반드시 빠지고, 빠지면 오탐이 난다.
#   대신 **정태 서술의 부재**로 잰다: 문장이 '이다/있다/없다'로 닫히면 상태 묘사,
#   그 밖의 동사로 닫히면 누군가 무언가를 한 것이다.
#   "막실 노인이 팔을 휘저었습니다" → 행동 / "움막은 강 언덕 위에 있었습니다" → 묘사.
STATIVE = re.compile(
    r"(이었|였|이지요|입니다|이에요|이었지요|이었어요|"
    r"있었|있지요|있습니다|있어요|없었|없습니다|없지요|없어요)"
    r"\s*[.!?…]*\s*$")

QUOTE = re.compile(r'["“”]')


def no_space(s):
    return len(re.sub(r"\s", "", s))


def sentences(t):
    return [x.strip() for x in re.split(r"(?<=[.!?…])\s+", t) if x.strip()]


def paragraphs(t):
    return [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]


def timeline(text, cpm):
    """문장마다 (누적 시작분, 누적 끝분, 문장) — 낭독 시간 축."""
    out, acc = [], 0.0
    for s in sentences(text):
        d = no_space(s) / cpm
        out.append((acc, acc + d, s))
        acc += d
    return out, acc


def dialogue_blocks(text):
    """연속된 대사 문장을 하나로 묶어 덩어리 수를 센다."""
    n, run = 0, False
    for s in sentences(text):
        has = bool(QUOTE.search(s))
        if has and not run:
            n += 1
        run = has
    return n


def analyse(text, cpm):
    tl, total = timeline(text, cpm)
    sents = [s for _, _, s in tl]
    r = {"minutes": total, "sentences": len(sents)}

    # H1 훅 — 첫 문장에 대사
    r["H1"] = bool(sents and QUOTE.search(sents[0]))

    # H2 첫 사건 착수 문장 번호.
    # ★사건 개시의 신호는 둘이다 — 전환 표지("그러던 어느 날")로 들어가거나,
    #   아예 사건 한복판에서 열어 인물이 말을 하거나(§3-2 "첫 문단이 곧 사건이다").
    #   후자에는 전환 표지가 없으므로 표지만 찾으면 잘 쓴 도입을 오히려 걸러 낸다.
    idx = next((i + 1 for i, s in enumerate(sents[:20])
                if TURN.search(s) or QUOTE.search(s)), None)
    r["H2"] = idx

    # H3 도입 3분 대사 덩어리
    head = " ".join(s for st, _, s in tl if st < OPEN_MIN)
    r["H3"] = dialogue_blocks(head)

    # R1 대사 최장 공백
    gap = best = 0.0
    for st, en, s in tl:
        if QUOTE.search(s):
            best, gap = max(best, gap), 0.0
        else:
            gap += en - st
    r["R1"] = max(best, gap)

    # R2 죽은 창 — 대사 0 + 전환 0
    # ★마지막 창은 세지 않는다. 편의 끝은 ⑨주제 봉인 + 닫는 정형구 자리이고(§2·§4),
    #   그 자리는 대사도 장면 전환도 없이 조용히 닫는 것이 규칙이다.
    #   여기를 걸면 규칙대로 쓴 대본이 늘 위반으로 뜬다 — 검사가 규칙과 싸우게 된다.
    dead, w = [], 0.0
    last_start = max(0.0, (int(total / WINDOW_MIN)) * WINDOW_MIN)
    while w < total:
        if w >= last_start:
            break
        seg = [s for st, en, s in tl if st < w + WINDOW_MIN and en > w]
        body = " ".join(seg)
        if seg and not QUOTE.search(body) and not TURN.search(body):
            dead.append(round(w, 1))
        w += WINDOW_MIN
    r["R2"] = dead

    # R3 설명 문단 연속 — 대사도 없고 모든 문장이 정태 서술로 닫히는 문단이 몇 개 이어지는가
    run = worst = 0
    r["R3_where"] = []
    for i, p in enumerate(paragraphs(text)):
        ss = sentences(p)
        descriptive = (not QUOTE.search(p)) and bool(ss) and all(STATIVE.search(x) for x in ss)
        if not descriptive:
            run = 0
        else:
            run += 1
            if run > worst:
                worst, r["R3_where"] = run, [i - run + 1, i]
    r["R3"] = worst

    # R4 십 분당 전환 표지
    r["R4"] = len(TURN.findall(text)) / max(1e-9, total) * 10

    # T1 뒷심 — 마지막 20% 대사 밀도 / 전체 평균
    cut = total * 0.8
    tail = [s for st, _, s in tl if st >= cut]
    dens = lambda ss: (sum(1 for s in ss if QUOTE.search(s)) / len(ss)) if ss else 0.0
    whole = dens(sents)
    r["T1"] = (dens(tail) / whole) if whole else 0.0
    return r


LIMITS = {
    "H1": ("훅 — 첫 문장에 대사",        lambda v: v is True,        "있음/없음"),
    "H2": ("첫 사건 착수 문장",          lambda v: v is not None and v <= 7, "≤7번째"),
    "H3": ("도입 3분 대사 덩어리",       lambda v: v >= 3,           "≥3개"),
    "R1": ("대사 최장 공백(분)",         lambda v: v <= 2.0,         "≤2.0분"),
    "R2": ("죽은 창(대사0+전환0)",       lambda v: len(v) == 0,      "0개"),
    "R3": ("설명 문단 최장 연속",        lambda v: v <= 2,           "≤2개"),
    "R4": ("십 분당 전환 표지",          lambda v: v >= 8,           "≥8회"),
    "T1": ("뒷심(후반 대사밀도 비)",     lambda v: v >= 0.6,         "≥0.60"),
}


def report(name, r, show_map=False, cpm=DEFAULT_CPM):
    print(f"\n[{name}]  {r['minutes']:.1f}분 · 문장 {r['sentences']}개")
    bad = []
    for k, (label, ok, want) in LIMITS.items():
        v = r[k]
        good = ok(v)
        shown = (f"{len(v)}개 {v[:8]}{'…' if len(v) > 8 else ''}" if k == "R2"
                 else f"{v:.2f}" if isinstance(v, float)
                 else ("있음" if v is True else "없음" if v is False else str(v)))
        print(f"  {'✓' if good else '✗'} {label:22s} {shown:34s} (목표 {want})")
        if not good:
            bad.append(k)
    if show_map and r["R2"]:
        print("  이탈 위험 구간(분):", ", ".join(f"{m:.0f}~{m+WINDOW_MIN:.0f}" for m in r["R2"]))
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=pathlib.Path, help="대본 파일 또는 chapters 폴더")
    ap.add_argument("--cpm", type=float, default=DEFAULT_CPM)
    ap.add_argument("--map", action="store_true", help="이탈 위험 구간 좌표 출력")
    a = ap.parse_args()

    files = sorted(a.path.glob("*.md")) if a.path.is_dir() else [a.path]
    if not files:
        print("대본 파일이 없습니다.", file=sys.stderr)
        return 2
    fail = 0
    for f in files:
        bad = report(f.name, analyse(f.read_text(encoding="utf-8"), a.cpm), a.map, a.cpm)
        fail += len(bad)
    print(f"\n{'통과' if fail == 0 else f'위반 {fail}건 — 고칠 것'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
