"""대본 제작 제약 기계 검증 (script-constraints.md의 검증 도구).

사용법:
    python3 scripts/script/validate_script.py {P}/script.txt --target 36000,38000 --cpm 265
    python3 scripts/script/validate_script.py {P}/_script/chapters --chapter-target 5300,6400 --cpm 265
    python3 scripts/script/validate_script.py {P}/script.txt --style     # v3.0 문체 수치 검사

검사 항목:
    - 공백 제외 글자수 (목표 범위)
    - 아라비아 숫자 / 로마자 (0건이어야 함 — tts_map 생략 조건)
    - 20자 초과 호흡 (쉼표·구두점 구간, 공백 제외 — vrew 자막 줄 기준)
    - 문어체 어미 (였다/하였다/이었다/거늘/허나)
    - ★수면 포맷 금지 표현 (후렴·시점교차·패턴인터럽트·CTA — script-guide §6)
    - 경어체 이탈 의심 (~다. 종결 — 대사 밖, 경고만)
    - 문단 수
    - --style: 문장 길이 분포 / 종결어미 분포 / 직접대사 비율 (script-guide §5 목표치 대조)

종료 코드: 위반(에러) 있으면 1, 없으면 0. 경고는 실패로 치지 않는다.
"""

import argparse
import os
import re
import sys
from collections import Counter

DEFAULT_CPM = 265  # 공백 제외 글자/분 — script-guide v3.0 §1

QUOTE_RE = re.compile(r'["“][^"“”]*["”]')
BREATH_SPLIT_RE = re.compile(r'[,.!?…:;"“”]')
MUNEOCHE_RE = re.compile(r'(하였다|이었다|거늘|허나)(?=[\s".,!?…”)]|$)|(?<![니습])였다(?=[\s".,!?…”)]|$)')
PLAIN_END_RE = re.compile(r'([가-힣])다[.!?…]')

# script-guide.md §6 "쓰지 않는 것 목록" — 벤치 2편에서 0회였던 각성 장치
SLEEP_BANNED = [
    ("후렴", re.compile(r"아직\s*(알지|알\s지)\s*못")),
    ("시점교차", re.compile(r"(^|[.\s”\"])한편[,\s]")),
    ("패턴인터럽트", re.compile(r"숨\s*고르|쉬어\s*가실까요|뒤로\s*갈수록")),
    ("CTA", re.compile(r"구독|좋아요|알림\s*설정|눌러\s*주")),
]

# --style 목표치 (script-guide §5) — (라벨, 정규식, 하한%, 상한%)
# ★2026-08-23 재도출 — §5-6(같은 어미 연속 금지)이 상한을 구조적으로 정한다.
# 어미가 셋인데 이웃끼리 겹치면 안 되므로, 어느 하나도 절반을 넘을 수 없다.
# 종전 값(습니다 45~55, 단일 나레이션 55~65)은 연속을 세지 않던 시절의 값이라 새 규칙과 모순된다.
STYLE_ENDINGS = [
    ("~습니다", re.compile(r"(습니다|ㅂ니다)$"), 38, 50),
    ("~죠/~지요", re.compile(r"(죠|지요)$"), 15, 30),
    ("~어요/~네요", re.compile(r"(어요|아요|예요|이에요|네요|세요|워요|해요)$"), 15, 30),
]

# ★2026-08-20 신설 — 문체 '하한' 지표.
# 상한만 있는 규칙은 언제나 0 쪽으로 수렴한다(05편 실측: 40자 초과 0.6% / 대사 1.1%).
# 아래 둘은 "덜 쓰면 걸리는" 항목이라 코너 솔루션을 막는다.
MIMETIC_RE = re.compile(
    r"(반짝|번쩍|훌쩍|덜컥|철썩|바스락|사부작|터덜|성큼|우두커니|물끄러미|힐끗|슬쩍|덥석|"
    r"와락|불쑥|후드득|사르르|스르르|조곤조곤|또박또박|툭툭|툭|쓱|끄덕|절레|콱|푹|"
    r"휘청|비틀|주춤|움찔|꾸벅|살며시|가만히|천천히)")
WORD_RE = re.compile(r"[가-힣]{2,}")
TTR_WINDOW = 3000          # 어절 — 45분 편 하나가 약 3,900어절이라 편 단위로도 잰다

# ★2026-08-29 v3.7 — 「어휘 다양도(TTR)」를 「반복 어휘 비율」로 교체 (proposal_20260829.md 항목 A)
# 재는 것은 같다(같은 낱말을 얼마나 되풀이하나). 바꾼 이유는 TTR이 표기 흔들림에
# 민감해 벤치와의 비교가 불안정하기 때문이다 — 벤치 대본은 전부 자동자막 유래라
# 오인식 하나하나가 새 타입이 되어 TTR을 부풀린다. 반복 토큰 비율은 그 노이즈를
# 반대 방향으로만 받으므로(오인식은 반복을 깨뜨린다) 과대평가가 아니라 과소평가로 간다.
#
# 실측 (3천 어절 창 3구간 평균, 창을 채운 대본만)
#   벤치 001(97만, 정리본) 0.308 · 002(67만, 정리본) 0.353
#   벤치 003-1 0.325 · 003-2 0.305 · 004 0.305   (달이 채널, 자동자막)
#   자사 05~10편 18개 편      0.412 ~ 0.539
#   → 자사 최선(0.412)이 벤치 최악(0.353)에 못 미친다. 두 집단이 겹치지 않는다.
#
# ★01편·04편(6편 편성)은 편당 1,500~2,200어절이라 창을 못 채운다. 그 값(0.28~0.43)은
#   짧아서 낮게 나온 것이므로 3편 편성 값과 섞어 읽지 않는다 — 아래에서 창 부족을 표시한다.
#
# 목표는 벤치의 0.31~0.35지만 한 번에 못 간다. MIMETIC_FLOOR·check_density와 같은 방식으로
# 꼬리부터 걸리게 잡았다 — 0.47이면 자사 18편 중 8편이 걸린다(check_density와 같은 적발률).
REPEAT_CEIL = 0.47         # 3회 이상 나온 낱말이 차지하는 어절 비율. 낮을수록 어휘가 넓다
MIMETIC_FLOOR = 6.0        # 만자당. 자사 실측 1.8~3.2 · 벤치② 6.1 · 벤치① 26.6
                           # → 약한 쪽 벤치에 맞춘 하한. 목표는 ①의 26이지만 한 번에 못 간다

# 2026-08-23 신설 - 종결어미 연속 (사용자 지시)
# "습니다. 습니다" 처럼 같은 종결이 붙어 나오면 낭독이 한 음으로 눌린다.
# 비율(45~55%)만 재면 이 붙음을 못 잡는다 - 08편 초고는 습니다 58%인데
# 2연속이 329곳, 최장 7연속이었다. 그래서 분포가 아니라 이웃을 따로 센다.
# 귀에 걸리는 것은 낱말이 아니라 종결의 낙차다. 같은 낙차가 이어지면 한 음으로 눌린다.
# 그래서 '니다'만이 아니라 '지요'·'어요'도 각각 제 연속을 따로 센다.
END_KINDS = [
    ("니다", re.compile(r"니다$")),
    ("지요", re.compile(r"(지요|죠)$")),
    ("어요", re.compile(r"(어요|아요|여요|예요|에요|네요|해요|워요|워요)$")),
]
SEUB_RUN_ERROR = 3         # 3연속 이상 = 에러
SEUB_PAIR_CEIL = 5.0       # 2연속 시작 지점이 문장 수의 5% 이하

# ★2026-08-27 신설 — 대사 공백 (사용자가 가져온 외부 리뷰)
# "갈등 없는 구간이 8~12분 존재한다. 3~5분마다 질문·반전·선택·감정 폭발이 하나는 있어야 한다."
# 대사 '비율'은 이미 재고 있었는데(2.5~4%) 08편은 3.1%로 밴드 안이면서
# 2편에 11.5분짜리 무대사 구간이 있었다. 어미 연속과 같은 사각지대 —
# 비율은 맞고 분포가 무너진 경우다. 그래서 '몇 개'가 아니라 '얼마나 비었나'를 잰다.
DIALOGUE_GAP_CEIL = 5.0    # 분. 대사와 대사 사이가 이보다 길면 걸린다 (단일 나레이션)

# ★2026-09-06 — 대사클립 모드(voice="clip"). 2분마다 대사 구간 하나를 PJN i2v 클립으로 만든다.
# 그래서 공백 상한이 문체 기준이 아니라 **렌더 파이프라인의 입력 요구사항**이 된다:
# 대본이 4분을 비워 놓으면 그 자리에 꽂을 클립이 없다. script-guide §5-7 참조.
DIALOGUE_GAP_CEIL_CLIP = 2.0
CLIP_INTERVAL_MIN = 2.0    # 클립 간격(분) — 편당 클립 자리 수 = 러닝타임 / 이 값


def _repeat_load(window):
    """한 창에서 3회 이상 나온 낱말이 차지하는 어절 비율."""
    c = Counter(window)
    return sum(v for v in c.values() if v >= 3) / len(window)


def repeat_load(text):
    """반복 어휘 비율 — 고정 표본(3,000어절) 3구간 평균. 길이 편향을 없앤다.

    같은 낱말을 되풀이할수록 올라간다. 05편처럼 어휘가 좁아지면 0.49까지 간다.
    ★고치는 법은 어려운 말로 바꾸는 것이 아니다(§5-2와 충돌한다) —
      같은 동작을 다른 각도에서 쓰면 어휘는 저절로 갈린다.
    """
    w = WORD_RE.findall(text)
    if len(w) < TTR_WINDOW:
        # 창 부족 — 짧아서 낮게 나온 값이라 다른 편과 견주면 안 된다(호출부가 표시한다)
        return _repeat_load(w) if w else 0.0
    starts = (0, (len(w) - TTR_WINDOW) // 2, len(w) - TTR_WINDOW)
    return sum(_repeat_load(w[s:s + TTR_WINDOW]) for s in starts) / 3


def repeat_window_short(text):
    """반복 어휘 비율을 3천 어절 창으로 재지 못했으면 True."""
    return len(WORD_RE.findall(text)) < TTR_WINDOW


def no_space_len(s):
    return len(re.sub(r"\s", "", s))


def seub_runs(text):
    """같은 종결어미가 이어지는 구간. 반환 ([(idx, 길이, 종류, 첫문장)], 총문장수)."""
    sents = []
    for para in re.split(r"\n\s*\n", text):
        for s in re.split(r"(?<=[.!?\u2026])\s+", para.strip()):
            s = s.strip()
            if s:
                sents.append(s)

    def kind_of(s):
        core = s.rstrip(TRIM)
        for name, pat in END_KINDS:
            if pat.search(core):
                return name
        return None

    kinds = [kind_of(s) for s in sents]
    runs, run, start = [], 0, 0
    for i, k in enumerate(kinds):
        if k is None:
            if run >= 2:
                runs.append((start, run, kinds[start], sents[start]))
            run = 0
        elif run and kinds[start] == k:
            run += 1
        else:
            if run >= 2:
                runs.append((start, run, kinds[start], sents[start]))
            start, run = i, 1
    if run >= 2:
        runs.append((start, run, kinds[start], sents[start]))
    return runs, len(sents)


TRIM = '."\'?!\u2026 \u201d'


def check_text(text, max_breath):
    """반환: (errors, warnings) — 각 항목 (줄번호, 코드, 내용)."""
    errors, warnings = [], []
    lines = text.splitlines()

    for ln, line in enumerate(lines, 1):
        for m in re.finditer(r"[0-9]+", line):
            errors.append((ln, "숫자", f"'{m.group()}' → 한글 표기"))
        for m in re.finditer(r"[A-Za-z]+", line):
            errors.append((ln, "영어", f"'{m.group()}' → 한글 표기"))

        for seg in BREATH_SPLIT_RE.split(line):
            seg_len = no_space_len(seg)
            if seg_len > max_breath:
                errors.append((ln, "호흡", f"{seg_len}자 > {max_breath}자: \"{seg.strip()[:30]}…\""))

        for m in MUNEOCHE_RE.finditer(line):
            errors.append((ln, "문어체", f"'{m.group()}' — 경어체 구술로"))

        for label, pat in SLEEP_BANNED:
            for m in pat.finditer(line):
                errors.append((ln, f"금지({label})", f"'{m.group().strip()}' — script-guide §6"))

        # 대사(따옴표 안)는 반말 허용 — 따옴표 밖 평서 종결만 경고
        outside = QUOTE_RE.sub("", line)
        for m in PLAIN_END_RE.finditer(outside):
            if m.group(1) != "니":  # ~습니다/~입니다 계열 제외
                warnings.append((ln, "종결", f"'…{outside[max(0, m.start()-8):m.end()]}' — 경어체 확인"))

    runs, _ = seub_runs(text)
    for _idx, rlen, kind, first in runs:
        if rlen >= SEUB_RUN_ERROR:
            ln = next((k for k, l in enumerate(lines, 1) if first[:18] in l), 0)
            errors.append((ln, "어미연속", "'~%s' %d연속: \"%s...\"" % (kind, rlen, first[:26])))

    return errors, warnings


def dialogue_gap(text, cpm=DEFAULT_CPM):
    """대사와 대사 사이가 가장 오래 비는 구간을 분으로 돌려준다.
    비율(2.5~4%)만 재면 대사가 한곳에 몰려도 통과한다 - 그 사각지대를 메운다."""
    sents = []
    for para in re.split(r"\n\s*\n", text):
        for s in re.split(r"(?<=[.!?\u2026])\s+", para.strip()):
            s = s.strip()
            if s:
                sents.append(s)
    if not sents:
        return 0.0
    worst = run = 0
    for s in sents:
        if s.lstrip()[:1] in (chr(34), chr(8220)):
            worst = max(worst, run)
            run = 0
        else:
            run += no_space_len(s)
    worst = max(worst, run)
    return worst / max(1, cpm)


def style_report(text, voice="single", cpm=DEFAULT_CPM):
    """script-guide §5 문체 수치표 대조. 반환: 목표 범위를 벗어난 항목 리스트(경고용).

    voice="clip"  : ★대사클립 모드(2026-09-06 신설, 야담 현행 기본) — 직접대사 25~30%.
                    대사 구간을 PJN i2v 립싱크 클립으로 만들어 **인물이 제 목소리로** 말한다.
                    단일 나레이션의 2.5~4%는 "한 사람이 다 읽어 톤이 무너진다"가 유일한 근거였고,
                    클립이 읽으면 그 근거가 사라진다. 벤치 ③(kfZGIhXRXK4) 실측 50.7%와
                    단일 나레이션 4% 사이에서, 클립이 흡수하는 몫만큼만 올린 값.
                    공백 상한도 5분 → 2분으로 함께 내려간다.
    voice="single": 단일 나레이션 — 직접대사 상한 3.5% / 편당 여덟아홉 줄
                    (2026-08-15 v3.0c에서 2.5%로 내렸다가, 같은 날 v3.0d에서 되올림 —
                     6줄로는 ④부탁 장면이 간접화법 나열로 무너진다는 것이 실집필에서 확인됨)
    voice="multi" : 대사에 별도 보이스 — 직접대사 20% 안팎 (벤치 ② 실측 22.3%)
    """
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if not sents:
        return []
    lens = sorted(no_space_len(s) for s in sents)
    n = len(lens)
    median = lens[n // 2]
    pct_short = sum(1 for x in lens if x <= 20) / n * 100
    pct_long = sum(1 for x in lens if x > 40) / n * 100
    dialogue = sum(1 for s in sents if s.lstrip().startswith(('"', '“')) or '"' in s or '“' in s) / n * 100

    dlg_lo, dlg_hi = {"clip": (25, 30), "multi": (15, 30)}.get(voice, (2.5, 4))
    gap_ceil = DIALOGUE_GAP_CEIL_CLIP if voice == "clip" else DIALOGUE_GAP_CEIL
    nospace = no_space_len(text)
    mimetic = len(MIMETIC_RE.findall(text)) * 10000 / max(1, nospace)
    rows = [
        ("문장 길이 중앙값", median, 20, 22, "자"),
        ("20자 이하 비율", pct_short, 45, 60, "%"),
        ("40자 초과 비율", pct_long, 3, 8, "%"),
        (f"직접대사 비율({voice})", dialogue, dlg_lo, dlg_hi, "%"),
        ("반복 어휘 비율" + (" ※창부족" if repeat_window_short(text) else ""),
         repeat_load(text), 0.0, REPEAT_CEIL, ""),
        ("의태·의성어(만자당)", mimetic, MIMETIC_FLOOR, 999, "회"),
    ]
    for label, pat, lo, hi in STYLE_ENDINGS:
        hits = sum(1 for s in sents if pat.search(s.rstrip('."\'?!… ”')))
        rows.append((f"{label} 종결", hits / n * 100, lo, hi, "%"))
    _runs, _ns = seub_runs(text)
    rows.append(("같은 어미 2연속", len(_runs) / max(1, _ns) * 100, 0, SEUB_PAIR_CEIL, "%"))
    rows.append(("대사 최장 공백(분)", dialogue_gap(text, cpm), 0, gap_ceil, "분"))
    if voice == "clip":
        # 클립 자리 수 — 러닝타임을 2분으로 나눈 값만큼 대사 '덩어리'가 필요하다.
        runtime = no_space_len(text) / max(1, cpm)
        need = max(1, int(runtime / CLIP_INTERVAL_MIN))
        blocks, run = 0, False
        for s_ in sents:
            has = ('"' in s_ or '“' in s_)
            if has and not run:
                blocks += 1
            run = has
        rows.append((f"대사 덩어리 수(≥{need}개 필요)", blocks, need, 999, "개"))

    long_n, risky = reading_split_risk(sents)
    print(f"\n[낭독 분할] 40자 초과 서술문 {long_n}개 — Vrew가 약 30%를 두 클립으로 나눠 읽는다")
    if risky:
        print(f"  △ 그중 {len(risky)}개는 35~65% 지점에 쉼표가 없어 **구 한복판이 잘릴 수** 있다:")
        for a, b in risky[:8]:
            print(f"     {a}  ▮  {b}")
        if len(risky) > 8:
            print(f"     … 외 {len(risky) - 8}개")
        print("  → 문장을 둘로 나누거나 가운데쯤에 쉼표를 하나 둔다.")
    else:
        print("  ✓ 전부 가운데쯤에 쉼표가 있어 끊길 자리가 자연스럽다")

    off = []
    print(f"\n[문체 수치] 문장 {n:,}개 — script-guide §5 목표 대조")
    for label, val, lo, hi, unit in rows:
        ok = lo <= val <= hi
        if not ok:
            off.append(label)
        fmt = ".3f" if unit == "" else ".1f"
        print(f"  {'✓' if ok else '△'} {label:<18} {val:>7{fmt}}{unit}  (목표 {lo}~{hi}{unit})")
    return off



# ── 낭독 분할 (2026-08-29 신설) ────────────────────────────────────────────
# ★Vrew는 긴 문장을 **두 클립으로 나눠서** 낭독한다. 01~09편 실측(서술문 14,400개):
#     공백 제외  0~39자 → 나눌 확률 0.007% (1/14,200)
#                40자~  → 나눌 확률 30.5%  (43/141)
#   나눌 때는 문장의 40~60% 지점(중앙 48%)을 고르는데,
#     그 자리에 쉼표가 있으면 90%가 자연스럽게 끊기고,
#     없으면 구 한복판을 자른다 — 「장부의 이름 ▮ 석 자를 세는 대신」처럼.
#   이것이 사용자가 들은 "어색한 문장 끊어읽기"의 정체다(2026-08-29 원인 규명).
# ★§5 문체 밴드가 「40자 초과 3~8%」를 요구하므로 이 문장들은 **일부러** 만든 것이다.
#   그래서 통과/실패로 재지 않고 잘릴 자리를 보여 주어 사람이 판단하게 한다
#   (playbook v3.1 「기계 자동 수정 중단」과 같은 취지).
READING_SPLIT_MIN = 40
READING_CUT_FRAC = 0.48
READING_SAFE_LO, READING_SAFE_HI = 0.35, 0.65


def _nospace_pos(s):
    keep = re.compile(r"[가-힣0-9a-zA-Z]")
    bounds, commas, pos = [], [], 0
    for w in s.split():
        for ch in w:
            if ch == ",":
                commas.append(pos)
            elif keep.match(ch):
                pos += 1
        bounds.append(pos)
    return bounds, commas, pos


def reading_split_risk(sents):
    """(40자 초과 서술문 수, [(예상 앞조각, 예상 뒤조각)])"""
    out, long_n = [], 0
    for s in sents:
        if '"' in s or "“" in s:        # 대사는 Vrew가 마침표에서 끊으므로 정상
            continue
        bounds, commas, n = _nospace_pos(s)
        if n <= READING_SPLIT_MIN or len(bounds) < 2:
            continue
        long_n += 1
        if any(READING_SAFE_LO * n <= c <= READING_SAFE_HI * n for c in commas):
            continue
        cut = min(bounds[:-1], key=lambda b: abs(b - READING_CUT_FRAC * n))
        k = next((i for i, b in enumerate(bounds) if b >= cut), 0) + 1
        words = s.split()
        out.append((" ".join(words[:k]), " ".join(words[k:])))
    return long_n, out

def report_one(path, text, max_breath, target, cpm=DEFAULT_CPM):
    total = no_space_len(text)
    paragraphs = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    errors, warnings = check_text(text, max_breath)

    if target:
        lo, hi = target
        if not (lo <= total <= hi):
            errors.append((0, "분량", f"공백 제외 {total:,}자 — 목표 {lo:,}~{hi:,}자 벗어남"))

    status = "✗" if errors else "✓"
    print(f"\n{status} {path} — 공백 제외 {total:,}자 (약 {total/cpm:.0f}분 @{cpm}자/분), 문단 {paragraphs}개, "
          f"에러 {len(errors)} / 경고 {len(warnings)}")

    by_code = {}
    for ln, code, msg in errors:
        by_code.setdefault(code, []).append((ln, msg))
    for code, items in by_code.items():
        print(f"  [{code}] {len(items)}건")
        for ln, msg in items[:8]:
            loc = f"L{ln}" if ln else "-"
            print(f"    {loc}: {msg}")
        if len(items) > 8:
            print(f"    … 외 {len(items) - 8}건")
    if warnings:
        print(f"  [경고] {len(warnings)}건 (실패 아님)")
        for ln, code, msg in warnings[:5]:
            print(f"    L{ln} ({code}): {msg}")
        if len(warnings) > 5:
            print(f"    … 외 {len(warnings) - 5}건")

    return len(errors) == 0, total


def parse_range(s):
    lo, hi = s.split(",")
    return int(lo), int(hi)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="대본 제작 제약 검증")
    parser.add_argument("path", help="script.txt 또는 chapters 폴더")
    parser.add_argument("--target", type=parse_range, default=None, help="전체 분량 min,max (공백 제외)")
    parser.add_argument("--chapter-target", type=parse_range, default=None, help="장별 분량 min,max (폴더 모드)")
    parser.add_argument("--max-breath", type=int, default=26,
                        help="호흡 최대 글자수 (기본 26 — v3.1 2026-08-20 개정, 종전 20)")
    parser.add_argument("--cpm", type=int, default=DEFAULT_CPM,
                        help=f"낭독 속도 (공백 제외 자/분, 기본 {DEFAULT_CPM}) — 분 환산에만 사용")
    parser.add_argument("--style", action="store_true",
                        help="문체 수치 검사 (script-guide §5 목표치 대조, 경고만)")
    parser.add_argument("--voice", choices=("clip", "single", "multi"), default="clip",
                        help="낭독 보이스 정책 — clip(★기본, 대사클립 모드 25~30%%·공백 2분) | "
                             "single(단일 나레이션 2.5~4%%·공백 5분) | multi(20%% 안팎). --style에만 영향")
    args = parser.parse_args()

    ok_all, grand_total = True, 0
    all_text = []

    if os.path.isdir(args.path):
        files = sorted(
            f for f in os.listdir(args.path)
            if f.endswith((".md", ".txt")) and not f.startswith((".", "_"))
        )
        if not files:
            print(f"검사할 파일 없음: {args.path}")
            sys.exit(1)
        for fname in files:
            fpath = os.path.join(args.path, fname)
            with open(fpath, encoding="utf-8") as f:
                text = f.read()
            ok, total = report_one(fname, text, args.max_breath, args.chapter_target, args.cpm)
            ok_all &= ok
            grand_total += total
            # ★문체 지표는 **낭독되는 글**만 잰다 (2026-08-29 수정).
            #   meta.txt(제목·설명문·제작 메모)까지 섞여 들어가 밴드가 조용히 틀어져 있었다 —
            #   10편 기준 meta 4,187자가 script 39,226자에 섞여 약 10%를 오염시켰고,
            #   표·경로·해시태그가 「낭독 분할」 검사에도 잡혔다.
            if fname != "meta.txt":
                all_text.append(text)
        print(f"\n합계: 공백 제외 {grand_total:,}자 (약 {grand_total/args.cpm:.0f}분 @{args.cpm}자/분)")
        if args.target:
            lo, hi = args.target
            if not (lo <= grand_total <= hi):
                print(f"✗ 전체 분량 목표 {lo:,}~{hi:,}자 벗어남")
                ok_all = False
    else:
        with open(args.path, encoding="utf-8") as f:
            text = f.read()
        ok_all, _ = report_one(args.path, text, args.max_breath, args.target, args.cpm)
        all_text.append(text)

    if args.style:
        # ★--cpm 을 넘긴다 (2026-08-27 수정) — 안 넘기면 「대사 최장 공백」이 늘 265자/분으로 환산돼
        #   실측 311자/분 편에서 분이 17% 부풀었다. 밴드 안이라고 나온 편이 실제로는 밖일 수 있었다.
        off = style_report("\n\n".join(all_text), args.voice, args.cpm)
        if off:
            print(f"  △ 목표 범위 밖 {len(off)}항목: {', '.join(off)} (경고 — 실패로 치지 않음)")

    print("\n" + ("통과 — 다음 단계 진행 가능" if ok_all else "위반 있음 — 수정 후 재검증"))
    sys.exit(0 if ok_all else 1)
