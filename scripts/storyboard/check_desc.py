#!/usr/bin/env python3
"""씬 서술 감사 — 그림을 뽑기 전에 visual_desc 가 갖춰야 할 것을 확인한다.

★왜 필요한가 (2026-09-02, 12편 157씬 전수 검수에서 신설)
    12편은 전 씬이 `check_scenes.py`(흰 액자·레터박스·이중 패널)를 통과했는데도
    사용자가 눈으로 보고 "눈동자가 뿌옇다 / 표정이 생동감이 없다"고 지적했다.
    세어 보니 **구도 지시가 없는 씬 92% · 표정 지시가 없는 씬 87%** 였다.

    두 가지는 style.json 으로 못 고친다:
      · 구도 — 프리셋이 "인물은 크게"라고 해도, 씬이 마당 전경을 요구하면 얼굴이
        화면 높이의 1/10이 된다. 그 크기에는 홍채를 그릴 화소가 없어 눈이 회색 얼룩이 된다.
        프리셋의 `VERY DARK BROWN iris` 는 그릴 자리가 있을 때만 지켜진다.
      · 표정 — 인물 lock 에 쓰면 편 전체가 그 얼굴로 굳는다(CLAUDE.md 08편 실측).
        그래서 씬 visual_desc 가 유일한 자리인데, 아무도 안 쓰면 무표정 미인으로 수렴한다.
    프리셋의 `FACES MUST ACT` 는 **그 순간에 표정이 적혀 있을 때만** 작동한다.

    build.py 도 같은 경고를 찍지만, 그때는 이미 생성이 시작된 뒤다. 이 스크립트는
    **뽑기 전에** 편 전체를 한 장으로 보여 준다.

검사 항목
    구도   화면에서 얼굴이 얼마나 크게 잡히는지 (cast 가 있는 씬만)
    표정   그 순간의 표정이 적혀 있는지 (cast 가 있는 씬만)
    캐스팅 visual_desc 의 {id} 토큰이 cast 에 다 들어 있는지 (CLAUDE.md 11편 실측)
    길이   프롬프트가 너무 길어 화풍 지시를 밀어내지 않는지

Usage:
    python3 scripts/storyboard/check_desc.py <project_dir> [--verbose]
    python3 scripts/storyboard/check_desc.py <편 폴더 여러 개>
종료 코드: 위반이 있으면 1
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build as _build          # 검사 기준을 build.py 와 공유한다

_RE_FRAMING = re.compile(
    r"waist-?up|waist up|chest[- ]up|close medium|close shot|close-?up|medium shot|two shot|"
    r"large in frame|fills? the frame|extreme close|face(?:s)? (?:are |is )?(?:big|large)",
    re.I)
_RE_EXPRESSION = re.compile(
    r"\b(?:brows?|eyes?|eyelids?|eyelashes|lashes|gaze|stare\w*|staring|blink\w*|"
    r"mouth|lips?|jaw|chin|cheeks?|face is|her face|his face|expression|"
    r"smil\w*|grin\w*|laugh\w*|frown\w*|scowl|glare|weep\w*|cry|cries|crying|tears?|"
    r"stunned|startled|astonish\w*|delight\w*|relief|relieved|weary|weariness|"
    r"anxious|worried|worry|ashamed|shame|resolve|determined|tender|"
    r"blush\w*|flush\w*|hesitat\w*|puzzl\w*|dismay|bewilder\w*|"
    r"breath|breathing|trembl\w*|flinch\w*|wince\w*)\b",
    re.I)
# 프롬프트가 이보다 길면 채널 화풍 지시가 뒤로 밀린다(CLAUDE.md 03편 실측 5,700자 사고)
DESC_MAX = 1500


def _expr_phrases(v):
    """표정을 묘사한 짧은 구절들 — 반복 검사에 쓴다."""
    out = []
    # ★소유격이 앞에 있을 때만 표정으로 본다 — 없으면 "fire mouth glowing orange",
    #   "the mouth of the market" 같은 사물 표현까지 표정으로 세어 버린다(2026-09-02 실측).
    for m in re.finditer(
            r"\b(?:his|her|their)\s+(?:brows?|eyes?|eyelids?|mouth|lips?|jaw|chin|gaze)"
            r"\s+(?:is|are|has|have|had)?\s*[a-z' -]{3,28}", v, re.I):
        out.append(" ".join(m.group(0).lower().split()))
    return out


def repeated_expressions(scenes, min_share=3):
    """여러 씬에 똑같이 쓰인 표정 구절 → [(구절, 씬 수)] 내림차순."""
    from collections import Counter
    c = Counter()
    for s in scenes:
        if not s.get("cast"):
            continue
        for ph in set(_expr_phrases(s.get("visual_desc", "") or "")):
            c[ph] += 1
    return [(p, n) for p, n in c.most_common() if n >= min_share]


def audit(project: pathlib.Path, verbose=False):
    sb = project / "storyboard.json"
    if not sb.exists():
        print(f"  storyboard.json 없음: {project}", file=sys.stderr)
        return None
    scenes = json.loads(sb.read_text(encoding="utf-8"))["scenes"]
    chars = {}
    cj = project / "characters.json"
    if cj.exists():
        chars = {k: v for k, v in json.loads(cj.read_text(encoding="utf-8")).items()
                 if not k.startswith("_")}

    # ★검사 기준은 build.py 의 게이트와 같은 함수를 쓴다 — 두 곳이 어긋나면
    #   여기서는 통과하고 생성에서 막히는(또는 그 반대의) 일이 생긴다.
    style = {}
    try:
        style = json.loads(_build.find_channel_config(project, "style.json").read_text(
            encoding="utf-8"))
    except Exception:
        pass

    rows = []
    for s in scenes:
        v = s.get("visual_desc", "") or ""
        bad = list(_build.scene_problems(s, chars, style, project))
        if len(v) > DESC_MAX:
            bad.append("길이%d" % len(v))
        if bad:
            rows.append((s.get("id"), bad, v))
    return scenes, rows



# ★[배경 고정 2026-09-04 사용자 지적 — "배경이 계속 고정되고 인물들만 위에 올라가는 느낌"]
#   같은 location 을 쓰는 씬이 많은데 씬마다 '장소 안 어디서 보는가'를 안 적으면,
#   모델이 배경 시트의 구도를 그대로 재현하고 인물만 그 위에 올린다.
#   13편 실측: 장소가 지정된 117씬이 전부 같은 각도로 나왔다.
#   시트는 '이 장소가 무엇으로 되어 있는가'를 정의할 뿐 카메라 위치가 아니다.
VANTAGE = re.compile(
    r"from inside|looking out|looking in|looking down|looking back|from behind|over the shoulder|"
    r"from above|from below|low angle|high angle|from the water|from the gate|from outside|"
    r"in the doorway|through the (door|gap|opening)|against the \w+ wall|tight on|extreme close|"
    r"from the far side|from beside|from within|seen from", re.I)


def fixed_background(scenes, min_scenes=8, warn_share=0.6):
    """location 별로 '시점 지시가 없는 씬' 비율. 높으면 배경이 한 장으로 고정된다."""
    from collections import defaultdict
    by = defaultdict(list)
    for s in scenes:
        loc = s.get("location")
        if loc:
            by[loc].append(s)
    out = []
    for loc, ss in sorted(by.items()):
        if len(ss) < min_scenes:
            continue
        flat = [s.get("id") for s in ss if not VANTAGE.search(s.get("visual_desc") or "")]
        if len(flat) / len(ss) >= warn_share:
            out.append((loc, len(ss), flat))
    return out


def main():
    ap = argparse.ArgumentParser(description="씬 visual_desc 감사 (그림 뽑기 전)")
    ap.add_argument("project_dirs", nargs="+", type=pathlib.Path)
    ap.add_argument("--verbose", "-v", action="store_true", help="위반 씬의 서술을 함께 출력")
    a = ap.parse_args()

    rc = 0
    tot_n = tot_bad = 0
    for p in a.project_dirs:
        r = audit(p, a.verbose)
        if r is None:
            rc = 1
            continue
        scenes, rows = r
        n_cast = sum(1 for s in scenes if s.get("cast"))
        has = lambda b, k: any(x.startswith(k) for x in b)
        nf = sum(1 for _, b, _ in rows if has(b, "구도"))
        ne = sum(1 for _, b, _ in rows if has(b, "표정"))
        nc = sum(1 for _, b, _ in rows if has(b, "캐스팅"))
        nl = sum(1 for _, b, _ in rows if has(b, "길이"))
        nsh = sum(1 for _, b, _ in rows if has(b, "인물축소"))
        nsheet = sum(1 for _, b, _ in rows if has(b, "시트없음"))
        pct = lambda x: f"{x*100//n_cast}%" if n_cast else "-"
        print(f"\n■ {p.name}  씬 {len(scenes)}개 (인물 등장 {n_cast})")
        print(f"    구도 없음 {nf:3d} ({pct(nf)})   표정 없음 {ne:3d} ({pct(ne)})"
              f"   인물축소 {nsh}   캐스팅 누락 {nc}   시트없음 {nsheet}   서술 과장 {nl}")
        fb = fixed_background(scenes)
        if fb:
            print("    ★배경이 한 장으로 고정될 위험 — 씬마다 '장소 안 어디서 보는가'를 적어 주세요:")
            for loc, n, flat in fb:
                print(f"        {loc}: {n}씬 중 {len(flat)}씬에 시점 지시 없음 — {flat[:20]}")
                print("        예) 'Seen FROM INSIDE THE ROOM looking out through the open door' / "
                      "'EXTREME CLOSE on the drying board' / 'LOW ANGLE from the packed earth'")

        rep = repeated_expressions(scenes)
        if rep:
            print("    ★같은 표정이 여러 씬에 반복됩니다 — 표정을 씬마다 갈라 주세요:")
            for ph, n in rep[:6]:
                print(f"        {n}개 씬: {ph}")
        if rows:
            print("    " + ", ".join(f"{i}[{'/'.join(b)}]" for i, b, _ in rows[:40]))
            if len(rows) > 40:
                print(f"    … 외 {len(rows)-40}개")
        if a.verbose:
            for i, b, v in rows:
                print(f"\n    씬{i} [{'/'.join(b)}]\n      {v[:300]}")
        tot_n += len(scenes)
        tot_bad += len(rows)
        if rows:
            rc = 1

    print(f"\n합계: 씬 {tot_n}개 중 {tot_bad}개에 지적")
    if rc:
        print("고치는 법 — 그 씬 visual_desc 에 두 줄을 넣는다:")
        print("  구도: 'CLOSE MEDIUM SHOT - her face LARGE in frame, cut at the chest'")
        print("  표정: 그 순간의 표정. 'her brows drawn faintly together, her mouth pressed thin'")
        print("  ★영구 특징(눈가 주름)이 아니라 그 장면의 표정을 쓴다. lock 에는 절대 쓰지 않는다.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
