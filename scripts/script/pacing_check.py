#!/usr/bin/env python3
"""대본 지루함 검사 — 설명 정체·반복 문구·주어 반복을 센다.

왜 필요한가 (사용자 지시 2026-08-17):
    *"비슷한 상황묘사에 너무 오래 하지 말고, 반복 문구 자제하고,
      스토리 진도 천천히 진행하지 말고, 다만 개연성 없이 막 진행하는 건 안 되고"*

분량을 늘리라고 하면 손일 공정·살림 설명을 덧붙이게 되는데, 그것이 길게 이어지면
**인물이 사라진 구간**이 생긴다. 귀로만 듣는 시청자에게는 그 구간이 곧 지루함이다.
반대로 설명을 다 걷어내면 §5의 "현실적"이라는 칭찬 축을 잃는다 — 그래서 비율로 관리한다.

세는 것:
    ① 설명 문단 비율 — 인물 이름도 대사도 없는 문단의 비율 (목표 50% 이하)
    ② 정체 구간 — 그런 문단이 몇 개나 연달아 붙어 있는지 (목표 3개 이하)
    ③ 반복 문구 — 같은 9자 이상 어구가 4회 이상 (목표 0)
    ④ 주어 반복 — 한 인물 이름으로 시작하는 문단 비율 (문단수의 18% 이하, 최소 20)
       ★2026-08-27부터 기본 꺼짐. `--subject` 를 줘야 판정한다.
         이유: 이 규칙은 **고치는 방법이 없다.** 이름을 빼서 맞추면 그 문단이 ①의 '설명 문단'으로
         넘어가 정체 지표가 도리어 나빠진다는 것이 2026-08-17에 실측됐다. 남는 처방은
         "이름은 두고 문장 구조를 바꿔라"뿐인데, 그건 ②(접속 부사 쏠림, check_sameness.py)가
         이미 재는 것이라 사실상 중복이다. 못 고치는 위반이 매번 뜨면 나머지 세 지표까지 무시된다.
         `check_density.py` 를 새로 들이면서 개정 규약(one-in-one-out)에 따라 이것을 내렸다.
         켜서 보고 싶으면 --subject. 판정 없이 수치만 보고 싶으면 그냥 두면 된다(참고로 찍힌다).

Usage:
    python3 scripts/script/pacing_check.py <파일 또는 폴더> [--names 곱단,봉수,...] [--subject]
    이름을 안 주면 같은 폴더의 characters.json 들에서 자동 수집한다.
"""
import argparse, collections, glob, io, json, pathlib, re, sys

DEF_MAX_EXPL_RATIO = 50
DEF_MAX_RUN = 3
DEF_MAX_REPEAT = 3
DEF_MAX_SUBJ_RATIO = 18  # 문단수 대비 % — 주인공은 자주 나올 수밖에 없다


def collect_names(path):
    names = set()
    root = pathlib.Path(path).resolve()
    for _ in range(5):
        for f in root.glob("**/characters.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # ★턴어라운드가 없는 사람 (2026-08-29). 훈장·의원·도가·아전처럼 **직함으로만 불리는**
                # 사람은 레지스트리에 올리지 않는다(턴어라운드 상한 때문). 그런데 대본에는 실제로
                # 나오므로, 그 문단이 '인물 없는 설명 문단'으로 세어져 정체가 허위로 잡힌다.
                # characters.json 맨 위에 "_extra_names": ["도가", ...] 로 적어 두면 함께 센다.
                for nm in (data.get("_extra_names") or []):
                    if nm:
                        names.add(nm)
                for k, v in data.items():
                    if not isinstance(v, dict):
                        continue
                    if v.get("name"):
                        names.add(v["name"])
                    # ★별칭 (2026-08-29). 대본은 인물을 늘 등록명 그대로 부르지 않는다 —
                    # 「한 사공」을 「사공」으로, 「방앗간 도가」를 「도가」로 부른다.
                    # 그러면 인물이 있는 문단이 '설명 문단'으로 세어져 정체가 허위로 잡힌다
                    # (08편 2편 실측: 정체 5문단 → 별칭을 주니 3문단으로 정상).
                    for al in (v.get("aliases") or []):
                        if al:
                            names.add(al)
            except Exception:
                pass
        if names or root.parent == root:
            break
        root = root.parent
    return names


def check(path, names, judge_subject=False):
    text = io.open(path, encoding="utf-8").read()
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        return None

    # 사람이 무대에 있느냐를 본다 — 고유명사만이 아니라 일반 지칭도 인물로 센다.
    # (도입부나 절정에서 이름을 일부러 감추는 것은 연출이지 정체가 아니다 — 2026-08-17)
    GENERIC = ("아이", "사내", "사람", "주인", "노파", "아낙", "영감", "어른", "어미",
               "아비", "손님", "며느리", "총각", "새댁", "아들", "형", "아우")

    def has_actor(p):
        return ('"' in p) or any(n in p for n in names) or any(g in p for g in GENERIC)

    expl = [i for i, p in enumerate(paras) if not has_actor(p)]
    ratio = len(expl) * 100 // len(paras)

    runs, run, start = [], 0, 0
    for i, p in enumerate(paras):
        if not has_actor(p):
            if run == 0:
                start = i
            run += 1
        else:
            if run:
                runs.append((run, start))
            run = 0
    if run:
        runs.append((run, start))
    runs.sort(reverse=True)

    flat = re.sub(r"\s", "", text)
    grams = collections.Counter(flat[i:i + 9] for i in range(len(flat) - 9))
    repeats = [(g, c) for g, c in grams.most_common(40) if c > DEF_MAX_REPEAT]
    # 겹치는 n-gram은 대표 하나만
    seen, dedup = [], []
    for g, c in repeats:
        if any(g in s or s in g for s in seen):
            continue
        seen.append(g)
        dedup.append((g, c))

    subj = collections.Counter()
    for p in paras:
        for n in names:
            if p.startswith(n):
                subj[n] += 1
                break

    bad = []
    if ratio > DEF_MAX_EXPL_RATIO:
        bad.append(f"설명비율 {ratio}%>{DEF_MAX_EXPL_RATIO}")
    long_runs = [r for r in runs if r[0] > DEF_MAX_RUN]
    if long_runs:
        bad.append(f"정체구간 {len(long_runs)}개(최장 {long_runs[0][0]}문단@{long_runs[0][1]})")
    if dedup:
        bad.append(f"반복어구 {len(dedup)}종")
    subj_cap = max(20, len(paras) * DEF_MAX_SUBJ_RATIO // 100)
    over_subj = [(n, c) for n, c in subj.most_common(3) if c > subj_cap]
    if over_subj and judge_subject:
        bad.append("주어반복 " + ",".join(f"{n}×{c}" for n, c in over_subj))

    name = pathlib.Path(path).name
    print(f"{'✗' if bad else '✓'} {name} — 문단 {len(paras)} · 설명 {ratio}% · "
          f"최장정체 {runs[0][0] if runs else 0}문단")
    if long_runs:
        print("    정체:", ", ".join(f"{r}문단@{s}" for r, s in long_runs[:5]))
    for g, c in dedup[:5]:
        print(f"    반복: '{g}' ×{c}")
    if over_subj:
        tag = "주어:" if judge_subject else "주어(참고·무판정):"
        print("   ", tag, ", ".join(f"'{n}…' {c}문단" for n, c in over_subj))
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--names", default="")
    ap.add_argument("--subject", action="store_true",
                    help="주어 반복을 '위반'으로 판정한다 (기본 꺼짐 — 머리말의 이유 참고)")
    a = ap.parse_args()
    names = ({x.strip() for x in a.names.split(",") if x.strip()}
             or collect_names(a.target))
    if not names:
        print("경고: 인물 이름을 못 찾았다 — --names 로 넘겨라", file=sys.stderr)
    p = pathlib.Path(a.target)
    files = sorted(glob.glob(str(p / "*.md"))) if p.is_dir() else [str(p)]
    ok = all([check(f, names, a.subject) for f in files])
    return 0 if ok else 1


sys.exit(main())
