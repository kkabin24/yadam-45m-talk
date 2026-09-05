#!/usr/bin/env python3
"""사건 밀도 검사 — "반복으로 시간을 때웠는가"를 편별로 잰다.

왜 필요한가 (2026-08-27, 사용자가 08~10편 대본에 대해 받아 온 외부 리뷰)
    *"노동/생활 묘사는 40~50% 압축한다. 같은 종류의 노동이 반복되면 삭제 또는 통합한다."*
    *"'계절이 바뀌었다' 대신 사건으로 시간을 넘긴다."*
    *"3개의 장면을 1개의 장면으로 합칠 수 있으면 합친다."*

기존 검사기가 이걸 못 잡는 이유 — 넷 다 보는 축이 달랐다.
    · pacing_check.py 는 "인물이 있느냐"를 본다. 노동 묘사에는 인물이 있으므로 통과한다.
      04편에서 잡은 것은 '인물이 사라진 문단'이었지 '인물이 같은 일을 열 번 하는 것'이 아니다.
    · pacing_check.py 의 9자 반복은 **글자가 똑같아야** 걸린다. 새끼를 꼬았다가 짚신을 삼으면 안 걸린다.
    · check_sameness.py 는 **편과 편 사이**만 본다. 한 편 안에서 같은 일감을 반복하는 것은 안 본다.
      (§7-1 "반복 노동 몽타주는 최대 3편"은 여섯 편 중 셋까지라는 뜻이지 한 편 안의 횟수가 아니다.)
    · validate_script.py 의 대사 공백은 **목소리**가 비는 구간을 잰다. 여긴 **판**이 안 바뀌는 구간이다.

★ 왜 '문단 수'가 아니라 '덩어리'를 세는가 (2026-08-27 05~10편 18개 편 실측으로 갈아탄 것)
    처음에는 모티프가 나오는 문단 수를 셌다. 그랬더니 09-01 논밭일 54문단, 10-02 부엌살림 56문단이
    나와 **18편 전부 위반**으로 찍혔다. 당연하다 — 이 포맷은 주인공의 생업이 편의 배경이라
    일감 낱말이 편 전체에 흩어진다. 그건 지루함이 아니라 **소재**다.
    지루한 것은 흩어진 언급이 아니라 **한자리에 몰린 덩어리**다(= 리뷰가 말한 "합칠 수 있는 세 장면").
    그래서 인접(간격 2문단 이내) 문단이 이어지는 **덩어리의 길이와 개수**를 센다.
    같은 18편을 다시 재니 최장 덩어리 중앙값 3, 꼬리가 6~8(06-01 짚일 7 · 09-03 짐승 8 · 10-02 부엌살림 6)로
    갈렸다. 신호와 소음이 이 축에서 나뉜다.

★ 감정은 왜 점수로 안 재는가 (같은 날 실측 — 넣으려다 뺀 지표)
    리뷰의 *"같은 감정을 반복하지 않는다"*를 감정 낱말 사전으로 재려 했다. 그런데
    **06편 1편은 14,327자에 감정 낱말이 0개**였다(미안·고달·외로·서러·두려·원망·반가·고마 전부 0).
    오탐이 아니라 이 채널이 실제로 그렇게 쓴다 — 감정을 이름 붙이지 않고 행동으로 보이는 것이
    리뷰가 *"유지해야 하는 장점"*으로 꼽은 **담담한 서술** 그 자체다.
    이걸 점수로 걸면 통과하려고 감정 낱말을 집어넣게 되고, 그러면 리뷰가 지키라던 것을 검사기가 깬다.
    그래서 **참고 수치로만 찍고 판정하지 않는다.** 사전(lexicon)은 그대로 두었으니,
    나중에 포맷이 바뀌어 감정을 명시하는 채널이 생기면 thresholds 에 키를 넣는 것만으로 살아난다.
    "감정이 단계적으로 변하는가"는 사람이 본다 — script-guide §7-1 편별 브리프.

★ 상한과 하한을 왜 다르게 다루는가 (개정 규약 3 — "상한을 넣으면 하한도 같이 본다")
    상한만 있는 규칙은 언제나 0으로 수렴한다(05편 실측: 40자 초과 목표 10% → 실제 0.6%).
    그런데 하한까지 **에러**로 만들면 정반대의 사고가 난다 —
    "생활 디테일 네 종류 이상"이 에러면, 리뷰가 빼라던 노동 문단을 **점수를 맞추려고 도로 끼워 넣게** 된다.
        · 상한(cap)   = 에러(종료 1). "이미 지루하다"는 실증이 있는 쪽.
        · 하한(floor) = 경고(△).      소재·자유 편이 정당하게 밑돌 수 있는 쪽.
    하한은 실패로 치지 않되 **매번 화면에 찍힌다.** 코너 솔루션은 눈에 보이면 대개 안 간다.

★ 이 검사기가 일부러 하지 않는 것
    · **자동 수정 없다.** --apply 가 없다. 자리를 보여 주고 고치는 것은 사람이다(개정 규약).
    · **절대 시계표를 박지 않는다.** 리뷰에는 "12~15분 첫 반전 / 25분 최대 갈등 / 35분 감정 반전"이
      있었으나 넣지 않았다. ① 이 채널은 수면 포맷이라 반전·최대갈등이 §6 금지(각성 장치)이고,
      ② 분 단위 비트표를 박으면 세 편이 같은 시계를 따라가 **§7-0 자유 편이 성립하지 않는다.**
      리뷰에서 취한 것은 "일정 간격마다 판이 바뀐다"는 **간격**뿐이고, 그 간격 하나만 잰다.
    · **사전이 코드 안에 없다.** lexicon/density.json 에 있고 채널별로 덮어쓴다 — 소재가 바뀌면
      규칙을 조이는 게 아니라 사전을 늘리는 것이 이 검사기의 확장 방향이다.

세는 것 (편별)
    [1] 반복 일감 — 같은 일감 덩어리의 최장 길이(상한) · 덩어리 개수(상한) · 일감 종수(하한)
    [2] 감정      — 종수·최다 감정 (★참고 수치, 판정하지 않는다)
    [3] 시간 요약 — 시간 점프 만자당 횟수(밴드) · 그중 인물 없는 '맨 점프' 비율(상한)
    [4] 장면 전환 — 판이 안 바뀌고 흐르는 최장 구간(분, 상한) · 장면 평균 길이(밴드)

사용법
    python scripts/script/check_density.py {S}/chapters
    python scripts/script/check_density.py {S}/chapters --free 03      # 자유 편은 상한 면제
    python scripts/script/check_density.py {S}/chapters --cpm 311      # _speed.json 이 없을 때
    python scripts/script/check_density.py {S}/chapters --set motif_run_cap=7
    python scripts/script/check_density.py {S}/chapters --why          # 걸린 자리를 찍는다

종료 코드: 상한 위반 있으면 1, 없으면 0. 하한 위반(△)·참고 수치는 실패로 치지 않는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BASE_LEXICON = HERE / "lexicon" / "density.json"

RE_WS = re.compile(r"\s")
RE_HEADING = re.compile(r"^\s*(#|<!--|//)")
DQUOTE = '"'
LQUOTE = "“"

# 사전 파일이 통째로 없을 때만 쓰는 최후 기본값. 정상 경로에서는 lexicon/density.json 이 이긴다.
FALLBACK_THRESHOLDS = {
    "motif_gap": 2,
    "motif_run_cap": 5,
    "motif_cluster_min": 3,
    "motif_cluster_cap": 4,
    "motif_kinds_floor": 4,
    "timejump_per10k_floor": 2.0,
    "timejump_per10k_cap": 18.0,
    "bare_timejump_share_cap": 45.0,
    "bare_timejump_min_sample": 6,
    "scene_gap_cap_min": 6.5,
    "scene_len_floor": 250,
    "scene_len_cap": 500,
}

GROUPS = ("motifs", "emotions", "time_jumps", "scene_markers", "actors_generic")


# ── 사전 로드 ────────────────────────────────────────────────────────────────
def _merge_group(base: dict, over: dict) -> dict:
    """목록은 '합친다'(확장). 키 앞에 ! 를 붙이면 '대체'."""
    out = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}
    for k, v in over.items():
        if k.startswith("_"):
            continue
        if k.startswith("!"):
            out[k[1:]] = list(v)
        elif k in out and isinstance(out[k], list) and isinstance(v, list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = list(v) if isinstance(v, list) else v
    return out


def load_lexicon(start: pathlib.Path, overrides: dict):
    src = []
    lex = {"thresholds": dict(FALLBACK_THRESHOLDS)}
    for key in GROUPS:
        lex[key] = {}

    if BASE_LEXICON.exists():
        base = json.loads(BASE_LEXICON.read_text(encoding="utf-8"))
        src.append("lexicon/density.json")
        for key in GROUPS:
            lex[key] = _merge_group({}, base.get(key, {}))
        for k, v in (base.get("thresholds") or {}).items():
            if not k.startswith("_") and v:            # 0 = 미지정으로 본다
                lex["thresholds"][k] = v

    # 채널 override — 위로 올라가며 channels/{채널}/config/density.json 을 찾는다
    node = start.resolve()
    for _ in range(8):
        cand = node / "config" / "density.json"
        if cand.exists():
            over = json.loads(cand.read_text(encoding="utf-8"))
            src.append(str(cand))
            for key in GROUPS:
                lex[key] = _merge_group(lex[key], over.get(key, {}))
            for k, v in (over.get("thresholds") or {}).items():
                if not k.startswith("_") and v:
                    lex["thresholds"][k] = v
            break
        if node.parent == node:
            break
        node = node.parent

    lex["thresholds"].update(overrides)
    return lex, src


def flat_terms(group: dict) -> list:
    out = []
    for k, v in group.items():
        if not k.startswith("_") and isinstance(v, list):
            out += v
    return out


def axes(group: dict) -> dict:
    return {k: v for k, v in group.items() if not k.startswith("_") and isinstance(v, list)}


# ── 주변 정보(속도·인물) ──────────────────────────────────────────────────────
def find_cpm(start: pathlib.Path):
    node = start.resolve()
    for _ in range(6):
        f = node / "_speed.json"
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("cpm"):
                    return int(d["cpm"]), str(f)
            except Exception:
                pass
        if node.parent == node:
            break
        node = node.parent
    return None, ""


def collect_names(start: pathlib.Path) -> set:
    names = set()
    node = start.resolve()
    for _ in range(5):
        for f in node.glob("**/characters.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            entries = data.values() if isinstance(data, dict) else data
            for v in entries:
                if isinstance(v, dict) and v.get("name"):
                    names.add(v["name"])
        if names or node.parent == node:
            break
        node = node.parent
    return names


# ── 측정 ─────────────────────────────────────────────────────────────────────
def nlen(s: str) -> int:
    return len(RE_WS.sub("", s))


def paragraphs(text: str) -> list:
    out = []
    for p in re.split(r"\n\s*\n", text):
        p = p.strip()
        if p and not RE_HEADING.match(p):
            out.append(p)
    return out


def hit_axes(para: str, group: dict) -> set:
    return {ax for ax, terms in group.items() if any(t in para for t in terms)}


def clusters(idx: list, gap: int) -> list:
    """인접(간격 gap 이내) 문단끼리 묶는다 — '한자리에 몰린 덩어리'."""
    if not idx:
        return []
    out, cur = [], [idx[0]]
    for a, b in zip(idx, idx[1:]):
        if b - a <= gap:
            cur.append(b)
        else:
            out.append(cur)
            cur = [b]
    out.append(cur)
    return out


def measure(text: str, lex: dict, names: set, cpm: int, th: dict) -> dict:
    paras = paragraphs(text)
    motif_ax = axes(lex["motifs"])
    emo_ax = axes(lex["emotions"])
    tj_terms = flat_terms(lex["time_jumps"])
    sm_terms = flat_terms(lex["scene_markers"])
    generic = flat_terms(lex["actors_generic"])

    total = nlen(text)
    motif_hits = collections.defaultdict(list)
    emo_hits = collections.defaultdict(list)
    tj_count = 0
    bare_tj = []
    scene_at = []          # (분, 문단번호, 이유)
    seen_names = set()

    pos = 0.0              # 누적 분
    for i, p in enumerate(paras):
        for ax in hit_axes(p, motif_ax):
            motif_hits[ax].append(i)
        for ax in hit_axes(p, emo_ax):
            emo_hits[ax].append(i)

        n_tj = sum(p.count(t) for t in tj_terms)
        if n_tj:
            tj_count += n_tj
            has_actor = (DQUOTE in p or LQUOTE in p
                         or any(n in p for n in names) or any(g in p for g in generic))
            if not has_actor:
                bare_tj.append(i)

        # 판이 바뀌는 자리: 시간·장면 표지 또는 그 편에서 처음 불리는 이름
        why = next((t for t in sm_terms if t in p), "")
        if not why:
            fresh = next((n for n in sorted(names) if n in p and n not in seen_names), "")
            if fresh:
                why = fresh + "(첫 등장)"
        for n in names:
            if n in p:
                seen_names.add(n)
        if why:
            scene_at.append((pos, i, why))
        pos += nlen(p) / cpm

    # 일감 덩어리
    gap = int(th["motif_gap"])
    cmin = int(th["motif_cluster_min"])
    runs = []              # (길이, 축, 시작문단)
    n_clusters = 0
    for ax, idx in motif_hits.items():
        for c in clusters(idx, gap):
            if len(c) >= cmin:
                n_clusters += 1
            runs.append((len(c), ax, c[0]))
    runs.sort(reverse=True)
    top_run = runs[0] if runs else (0, "-", 0)

    runtime = total / cpm
    marks = [0.0] + [t for t, _, _ in scene_at] + [runtime]
    gaps = [(marks[k + 1] - marks[k], k) for k in range(len(marks) - 1)]
    worst_gap, worst_k = max(gaps) if gaps else (0.0, 0)
    gap_after = scene_at[worst_k - 1] if 0 < worst_k <= len(scene_at) else None

    emo_total = sum(len(v) for v in emo_hits.values())
    top_emo = max(emo_hits.items(), key=lambda kv: len(kv[1])) if emo_hits else ("", [])

    return {
        "paras": len(paras), "chars": total, "runtime": runtime,
        "motif_kinds": len(motif_hits), "top_run": top_run, "n_clusters": n_clusters,
        "runs": runs,
        "emo_kinds": len(emo_hits), "top_emo": top_emo[0], "emo_total": emo_total,
        "emo_share": (len(top_emo[1]) * 100.0 / emo_total) if emo_total else 0.0,
        "tj_per10k": tj_count * 10000.0 / max(1, total), "tj_count": tj_count,
        "bare_tj": bare_tj,
        "bare_share": (len(bare_tj) * 100.0 / tj_count) if tj_count else 0.0,
        "scene_n": len(scene_at), "scene_at": scene_at,
        "scene_len": total / max(1, len(scene_at)),
        "worst_gap": worst_gap, "gap_after": gap_after,
        "paras_text": paras,
    }


# ── 보고 ─────────────────────────────────────────────────────────────────────
def report(name: str, m: dict, th: dict, free: bool, why: bool) -> int:
    caps, floors = [], []

    def cap(ok, label, val, limit, unit, detail):
        if not ok:
            caps.append((label, val, limit, unit, detail))

    def floor(ok, label, val, limit, unit, detail):
        if not ok:
            floors.append((label, val, limit, unit, detail))

    rlen, rax, _rstart = m["top_run"]
    cap(rlen <= th["motif_run_cap"], "일감 덩어리(%s)" % rax, rlen, th["motif_run_cap"], "문단",
        "한자리에 몰린 같은 일감 — 세 문단을 한 문단으로 합치고 남는 자리에 사건을 넣는다(§5-1)")
    cap(m["n_clusters"] <= th["motif_cluster_cap"], "일감 덩어리 개수", m["n_clusters"],
        th["motif_cluster_cap"], "개",
        "%d문단 이상 몰린 자리가 여럿 — 노동은 성격을 보이는 장치이지 이야기의 중심이 아니다"
        % int(th["motif_cluster_min"]))
    floor(m["motif_kinds"] >= th["motif_kinds_floor"], "생활 디테일 종수", m["motif_kinds"],
          th["motif_kinds_floor"], "종", "조선 살림 디테일은 이 채널의 칭찬 축이다(§5) — 다 빼지 말 것")

    cap(m["tj_per10k"] <= th["timejump_per10k_cap"], "시간 점프", round(m["tj_per10k"], 1),
        th["timejump_per10k_cap"], "회/만자", "세월로 넘긴 자리 — 사건으로 넘길 수 있는지 본다")
    floor(m["tj_per10k"] >= th["timejump_per10k_floor"], "시간 점프", round(m["tj_per10k"], 1),
          th["timejump_per10k_floor"], "회/만자", "45분에 세월이 하나도 안 흐르면 이야기가 제자리다")
    if m["tj_count"] >= th["bare_timejump_min_sample"]:
        cap(m["bare_share"] <= th["bare_timejump_share_cap"], "맨 점프 비율",
            round(m["bare_share"], 1), th["bare_timejump_share_cap"], "%",
            "인물 없이 '세월이 흘렀습니다'로만 넘긴 자리")

    cap(m["worst_gap"] <= th["scene_gap_cap_min"], "최장 무전환", round(m["worst_gap"], 1),
        th["scene_gap_cap_min"], "분", "판이 안 바뀌고 흐른 구간 — 여기에 사건이나 사람을 하나 들인다")
    floor(m["scene_len"] >= th["scene_len_floor"], "장면 평균 길이", int(m["scene_len"]),
          th["scene_len_floor"], "자", "장면이 너무 짧으면 사건이 아니라 나열이다(§2: 250~350자)")
    cap(m["scene_len"] <= th["scene_len_cap"], "장면 평균 길이", int(m["scene_len"]),
        th["scene_len_cap"], "자", "한 장면이 길다 — 특정한 하루·특정한 자리로 끊는다(§2)")

    mark = "✓" if not caps else ("~" if free else "✗")
    print("\n%s %s — %s자 / %.1f분 · 문단 %d · 장면 %d개(평균 %d자)" % (
        mark, name, format(m["chars"], ","), m["runtime"], m["paras"],
        m["scene_n"], int(m["scene_len"])))
    print("    일감 %d종 · 최장 덩어리 %s %d문단 · 덩어리 %d개 · "
          "시간점프 %d회(%.1f/만자, 맨 %.0f%%) · 최장 무전환 %.1f분" % (
              m["motif_kinds"], rax, rlen, m["n_clusters"],
              m["tj_count"], m["tj_per10k"], m["bare_share"], m["worst_gap"]))
    print("    (참고·무판정) 감정 낱말 %d종 %d회%s" % (
        m["emo_kinds"], m["emo_total"],
        " 최다 %s %.0f%%" % (m["top_emo"], m["emo_share"]) if m["emo_total"] else
        " — 감정을 이름 붙이지 않고 행동으로 보인 것이면 정상이다"))

    for label, val, lim, unit, detail in caps:
        print("    %s %s %s%s > %s%s — %s" % (
            "~" if free else "✗", label, val, unit, lim, unit, detail))
    for label, val, lim, unit, detail in floors:
        print("    △ %s %s%s < %s%s — %s" % (label, val, unit, lim, unit, detail))
    if free and caps:
        print("    ~ 자유 편(§7-0)이라 상한 위반을 실패로 치지 않는다. 다만 위 자리는 눈으로 볼 것.")

    if why:
        for rl, ax, st in m["runs"][:3]:
            if rl >= th["motif_cluster_min"]:
                print("      · '%s' 덩어리 %d문단 — 문단 %d부터" % (ax, rl, st))
        if m["bare_tj"]:
            print("      · 맨 점프 문단 번호: %s" % m["bare_tj"][:12])
        if m["gap_after"]:
            t, i, w = m["gap_after"]
            print("      · 무전환 시작: %.1f분(문단 %d, 직전 표지 \"%s\") → 여기부터 %.1f분간 판이 안 바뀐다"
                  % (t, i, w, m["worst_gap"]))
            head = m["paras_text"][i + 1][:40] if i + 1 < len(m["paras_text"]) else ""
            if head:
                print("        다음 문단: \"%s…\"" % head)

    return 0 if (free or not caps) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="사건 밀도 검사 — 반복으로 시간을 때웠는지 편별로 잰다")
    ap.add_argument("path", help="chapters 폴더 또는 단일 파일")
    ap.add_argument("--cpm", type=int, default=None,
                    help="낭독 속도(공백 제외 자/분). 생략하면 _speed.json 을 찾고, 없으면 265")
    ap.add_argument("--free", default="",
                    help="자유 편 번호(§7-0) — 쉼표로 여럿. 상한 위반을 실패로 치지 않는다 (예: --free 03)")
    ap.add_argument("--set", action="append", default=[], metavar="키=값",
                    help="임계 덮어쓰기 (예: --set motif_run_cap=7 --set scene_gap_cap_min=8)")
    ap.add_argument("--why", action="store_true", help="걸린 자리의 문단 번호·본문을 찍는다")
    a = ap.parse_args()

    p = pathlib.Path(a.path)
    if not p.exists():
        print("경로 없음: %s" % p)
        return 2
    start = p if p.is_dir() else p.parent

    overrides = {}
    for kv in a.set:
        k, _, v = kv.partition("=")
        try:
            overrides[k.strip()] = float(v) if "." in v else int(v)
        except ValueError:
            print("--set 값이 숫자가 아니다: %s" % kv)
            return 2

    lex, src = load_lexicon(start, overrides)
    th = lex["thresholds"]

    if a.cpm:
        cpm, cpm_src = a.cpm, "--cpm"
    else:
        cpm, cpm_src = find_cpm(start)
    if not cpm:
        cpm, cpm_src = 265, "기본값 — ★_speed.json 이 없다. 실측 속도로 다시 재세요"
    names = collect_names(start)

    if p.is_dir():
        files = sorted(x for x in p.glob("*.md") if not x.name.startswith(("_", ".")))
    else:
        files = [p]
    if not files:
        print("검사할 .md 가 없다: %s" % p)
        return 2

    free = {x.strip() for x in a.free.split(",") if x.strip()}
    free_norm = {x.lstrip("0") for x in free}

    print("사건 밀도 검사 — 상한(✗)은 실패, 하한(△)은 경고, 감정은 참고 수치다. 자동 수정은 없다.")
    print("  사전: %s   임계는 --set 으로 덮어쓴다" % (" + ".join(src) or "(내장 기본값)"))
    print("  속도: %d자/분 (%s)   인물 이름 %d개%s" % (
        cpm, cpm_src, len(names),
        "" if names else " — ★못 찾았다. 장면 전환이 시간 표지만으로 잡힌다"))

    bad = 0
    for f in files:
        m = measure(f.read_text(encoding="utf-8"), lex, names, cpm, th)
        is_free = f.stem in free or f.stem.lstrip("0") in free_norm
        bad += report(f.stem, m, th, is_free, a.why)

    print()
    if bad:
        print("상한 위반 %d편 — 고치는 법: 표현을 동의어로 바꾸지 말고 **장면을 합치거나 장치를 바꾼다.**" % bad)
        print("  · 일감 덩어리: 세 장면을 한 장면으로 합치고, 비는 자리에 사건을 하나 넣는다(§5-1).")
        print("  · 맨 점프: '두 해가 지났습니다'를 지우고, 두 해가 지났음을 보여 주는 장면 하나로 바꾼다.")
        print("  · 무전환 구간: 사람을 하나 들이거나, 자리를 옮기거나, 날을 바꾼다(§2 '특정한 하루, 특정한 자리').")
        print("  ※ 소재상 정당한 위반이면 --set 으로 그 편의 임계를 올리고, 왜 올렸는지 OUTLINE 브리프에 적는다.")
    else:
        print("통과 ✓")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
