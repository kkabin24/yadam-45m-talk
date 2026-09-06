#!/usr/bin/env python3
"""대사 클립 — 편 전체의 대사 구간을 PJN i2v 립싱크 클립으로 만든다 (2026-09-06 신설).

    python3 scripts/render/dialogue_clips.py {P} --chapter 01 --plan
    python3 scripts/render/dialogue_clips.py {P} --chapter 01 --generate [--only 1,4,9] [--force]

veo_hook.py와 무엇이 다른가 — **저것은 편 맨 앞 훅 하나, 이것은 편 전체에 흩어진 스물몇 개다.**
그래서 이 도구가 따로 풀어야 하는 문제가 둘 있다.

  ① 어디를 클립으로 만들 것인가
     대사를 전부 만들 수는 없다(45분 편에 대사가 백 줄 넘는다).
     낭독 시간 축에 펼쳐 놓고 **2분 간격 목표 지점마다 가장 가까운 대사 한 줄**을 고른다.
     ★덩어리가 아니라 한 줄이다 — 8초 클립에 주고받기 네 줄을 넣으면 화자가 섞인다.
     script-guide §5-7이 "대사 공백 2분 이하"를 요구하는 이유가 이것이다 —
     대본이 4분을 비워 놓으면 그 자리에 꽂을 클립이 없다.

  ② 같은 인물이 계속 같은 목소리로 말하게 하는가  ★이게 핵심이다
     2026-09-06 실측 결과, 목소리를 결정하는 것은 셋이었다:
       같은 시작 프레임 계열 이미지 · 같은 영문 화자 묘사 · 같은 seed
     **API의 ref_audios(음성 레퍼런스)는 작동하지 않는다.** 정반대 목소리(젊은 여성)를
     레퍼런스로 강제해도 출력이 그대로 노인이었고, i2v·r2v 양쪽 다 같았다.
     그리고 r2v는 first_frame을 버려서(앵커 대비 픽셀차 8.5 → 51) 스틸→클립 연결이 깨진다.
     그래서 **i2v로만 가고**, 인물별 seed·영문 묘사를 _video/voice_registry.json에 박아 둔다.

화자 추정은 어림짐작이다 — 대사 바로 뒤/앞 서술에 발화 동사와 등록된 이름이 **하나만** 있을 때만 받는다.
확신이 없으면 speaker를 비워 두고 **계획 파일을 사람이 고치게 한다.**
느슨하게 채우면 미상은 줄지만 틀린 화자가 섞이고, 그러면 그 인물의 목소리가 편 안에서 갈라진다 —
목소리 통일이 이 도구의 존재 이유인데 그것을 스스로 깨는 셈이다. **미상이 많은 쪽이 옳다.**
"""
import argparse
import json
import pathlib
import re
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "image"))
sys.path.insert(0, str(SCRIPTS / "render"))
from generate_image import find_env_key  # noqa: E402
from veo_hook import (  # noqa: E402
    PROMPT_TEMPLATE_PJN, conform_to_render, strip_hangul_outside_dialogue, veo_pjn,
)
from video_engine_policy import assert_video_engine, assert_video_key  # noqa: E402

DEFAULT_CPM = 284
DEFAULT_INTERVAL = 2.0        # 분 — script-guide §5-7 대사 공백 상한과 같은 값
DEFAULT_DURATION = 8
QUOTE = re.compile(r'["“”]')

REGISTRY_TEMPLATE = {
    "_comment": (
        "인물별 목소리 고정 — 같은 인물의 클립은 같은 seed, 같은 영문 묘사, "
        "같은 계열 시작 프레임으로 뽑는다. ref_audios(음성 레퍼런스)는 작동하지 않으므로 "
        "쓰지 않는다(2026-09-06 실측). seed는 아무 정수나 좋고, 한 번 정하면 "
        "그 인물의 클립 전부에 그대로 쓴다."
    ),
    "characters": {
        "인물이름": {
            "seed": 100101,
            "speaker_en": "The old man with the grey beard in the brown robe",
            "listener_en": "the young woman beside him",
        }
    },
}


def no_space(s):
    return len(re.sub(r"\s", "", s))


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def timeline(paras, cpm):
    """문단마다 (시작분, 끝분) — 낭독 시간 축."""
    out, acc = [], 0.0
    for p in paras:
        d = no_space(p) / cpm
        out.append((acc, acc + d))
        acc += d
    return out, acc


def dialogue_lines(paras):
    """대사 문단의 인덱스 목록.

    ★덩어리가 아니라 **한 줄**이 단위다. 8초 립싱크 클립에는 한 사람의 한 마디만 들어간다 —
    주고받기 네 줄을 한 클립에 넣으면 화자가 섞이고 립싱크가 무너진다."""
    return [i for i, p in enumerate(paras) if QUOTE.search(p)]


# 서술이 화자를 지목하는 꼴 — "덕보가 물었습니다" / "노인이 대답했어요"
SAYS = re.compile(r"(말했|물었|대답|묻|외쳤|소리쳤|받았|덧붙|중얼|뇌까|일렀|했지요|했습니다|했어요)")


def guess_speaker(paras, i, names):
    """대사 한 줄의 화자 **후보**와 그 근거 문장을 돌려준다. (guess, evidence)

    ★이 값을 신뢰하지 않는다. 계획 파일에는 `speaker_guess`로만 싣고,
    실제 생성이 쓰는 `speaker` 필드는 **사람이 채운다.**

    왜 자동으로 안 채우는가 — 실측에서 틀렸다. 한국어는 대사 옆 서술이
    화자가 아니라 **듣는 쪽**을 가리키는 일이 잦다:

        "때리시려면 나를 때리시오."      ← 여인의 말
        덕보가 말문이 막혔습니다.          ← 옆 서술은 덕보

    이 꼴에서 이름만 주우면 화자가 뒤집힌다. 스물한 자리 중 넉 자리가 그랬다.
    틀린 화자로 생성하면 그 인물의 목소리가 편 안에서 갈라지는데, 목소리 통일이
    이 도구의 존재 이유다. **모르면 비워 두는 쪽이 옳다.**"""
    for j, req in ((i + 1, True), (i - 1, True), (i - 1, False), (i + 1, False)):
        if not (0 <= j < len(paras)) or QUOTE.search(paras[j]):
            continue
        if req and not SAYS.search(paras[j]):
            continue
        found = [n for n in names if n in paras[j]]
        if len(found) == 1:
            return found[0], paras[j][:60]
    return None, None


# 8초 클립에 담기는 한국어 대사 길이. 너무 짧으면("예.") 클립이 심심하고,
# 너무 길면 8초에 안 들어가 립싱크가 밀린다.
MIN_CHARS, MAX_CHARS = 6, 45


def pick_slots(paras, cpm, interval):
    """2분 간격 목표 지점마다 가장 가까운 대사 '한 줄'을 고른다(중복 없이)."""
    tl, total = timeline(paras, cpm)
    cands = [i for i in dialogue_lines(paras)
             if MIN_CHARS <= no_space(paras[i]) <= MAX_CHARS]
    if not cands:
        return [], total
    # ★가장 가까운 줄이 아니라, 목표 지점 근처에서 **가장 실한 줄**을 고른다.
    #   "여름에도." 같은 한 낱말짜리 대답에 8초 클립을 쓰면 자리만 버린다.
    #   창(±interval/2) 안에 후보가 있으면 그중 긴 것을, 없으면 그냥 가장 가까운 것을 쓴다.
    window = interval / 2
    used, slots, target = set(), [], interval / 2
    while target < total:
        near = [(i, abs((tl[i][0] + tl[i][1]) / 2 - target)) for i in cands if i not in used]
        if not near:
            break
        inwin = [i for i, d in near if d <= window]
        best = (max(inwin, key=lambda i: no_space(paras[i])) if inwin
                else min(near, key=lambda x: x[1])[0])
        used.add(best)
        slots.append((best, (tl[best][0] + tl[best][1]) / 2))
        target += interval
    slots.sort(key=lambda x: x[1])
    return slots, total


def load_settings(P):
    for up in (P, *P.parents):
        c = up / "config" / "settings.json"
        if c.exists():
            return json.loads(c.read_text(encoding="utf-8"))
    return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--chapter", required=True, help="편 번호 (예: 01)")
    ap.add_argument("--plan", action="store_true", help="계획 파일만 만든다(무료)")
    ap.add_argument("--generate", action="store_true", help="계획대로 클립을 만든다")
    ap.add_argument("--only", default=None, help="특정 슬롯만 (쉼표구분, 예: 1,4,9)")
    ap.add_argument("--force", action="store_true", help="mp4가 있어도 재생성")
    ap.add_argument("--cpm", type=float, default=DEFAULT_CPM)
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="클립 간격(분)")
    ap.add_argument("--duration", type=int, default=DEFAULT_DURATION)
    ap.add_argument("--quality", type=float, default=1.0, choices=[0.4, 0.6, 0.8, 1.0])
    ap.add_argument("--video-subdir", default="_video")
    a = ap.parse_args()

    P = a.project_dir
    V = P / a.video_subdir
    V.mkdir(parents=True, exist_ok=True)
    ch = P / "_script" / "chapters" / (a.chapter + ".md")
    if not ch.exists():
        print("대본 없음: %s" % ch, file=sys.stderr)
        return 2

    reg_path = V / "voice_registry.json"
    if not reg_path.exists():
        reg_path.write_text(json.dumps(REGISTRY_TEMPLATE, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print("★목소리 레지스트리를 새로 만들었습니다: %s" % reg_path)
        print("  인물마다 seed와 영문 묘사를 채운 뒤 다시 돌리세요.")
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    chars = {k: v for k, v in (reg.get("characters") or {}).items() if k != "인물이름"}

    plan_path = V / ("dialogue_clips_%s.json" % a.chapter)

    if a.plan or not plan_path.exists():
        paras = paragraphs(ch.read_text(encoding="utf-8"))
        slots, total = pick_slots(paras, a.cpm, a.interval)
        items = []
        for n, (i, mid) in enumerate(slots, 1):
            g, ev = guess_speaker(paras, i, list(chars))
            items.append({
                "slot": n,
                "at_min": round(mid, 2),
                "para": i,
                "speaker": None,              # ★사람이 채운다. 아래 guess는 참고일 뿐이다
                "speaker_guess": g,
                "guess_evidence": ev,
                "dialogue": paras[i],
                "scene": None,
                "output": "dialogue_%s_%02d.mp4" % (a.chapter, n),
                "status": "planned",
            })
        plan = {"chapter": a.chapter, "cpm": a.cpm, "interval_min": a.interval,
                "runtime_min": round(total, 1), "slots": len(items), "items": items}
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        guessed = sum(1 for x in items if x["speaker_guess"])
        print("계획 생성: %s" % plan_path)
        print("  러닝타임 %.1f분 · 클립 자리 %d개 (%.1f분 간격)" % (total, len(items), a.interval))
        print("  ★speaker 는 전부 비어 있습니다 — 사람이 채워야 --generate 가 돕니다.")
        print("    참고용 speaker_guess 를 %d/%d 개 달아 두었지만 **틀릴 수 있습니다**"
              % (guessed, len(items)))
        print("    (한국어는 대사 옆 서술이 듣는 쪽을 가리키는 일이 잦습니다 — 실측 오류 4/21)")
        print("  scene 도 함께 채우세요(그 대사를 말하는 인물이 서 있는 씬 번호).")
        if not a.generate:
            return 0

    if not a.generate:
        return 0

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    engine = assert_video_engine("pjn", "dialogue_clips")          # ★정책 관문 1겹
    key = find_env_key(P, "PJN_API_KEY")
    if not key:
        print("error: PJN_API_KEY 없음 (.env)", file=sys.stderr)
        return 1
    assert_video_key(key, engine, "dialogue_clips")                # ★정책 관문 3겹

    render = load_settings(P).get("render") or {}
    only = {int(x) for x in a.only.split(",")} if a.only else None

    done = fail = 0
    for it in plan["items"]:
        if only and it["slot"] not in only:
            continue
        out = V / it["output"]
        if out.exists() and not a.force:
            print("  [%02d] 존재 — 건너뜀" % it["slot"])
            continue
        spk = it.get("speaker")
        if not spk or spk not in chars:
            print("  [%02d] 화자 미정 — 건너뜀 (계획 파일의 speaker 를 채우세요)" % it["slot"])
            continue
        scene = it.get("scene")
        if scene is None:
            print("  [%02d] scene 미지정 — 건너뜀" % it["slot"])
            continue
        frame = P / "scenes" / ("scene_%02d.png" % int(scene))
        if not frame.exists():
            print("  [%02d] 시작 프레임 없음: %s" % (it["slot"], frame))
            continue

        c = chars[spk]
        listener = ""
        if c.get("listener_en"):
            listener = ("The other people in the frame do not speak — %s stays silent, "
                        "only listening, lips closed." % c["listener_en"])
        line = it["dialogue"].strip().strip('"“” ')
        prompt = strip_hangul_outside_dialogue(PROMPT_TEMPLATE_PJN.format(
            scene_desc="", dialogue=line, duration=a.duration,
            speaker=c["speaker_en"], listener=listener))
        (V / ("dialogue_%s_%02d_prompt.txt" % (plan["chapter"], it["slot"]))).write_text(
            prompt, encoding="utf-8")

        print("  [%02d] %s (%.1f분) seed=%s 생성 중" % (it["slot"], spk, it["at_min"], c["seed"]))
        rc = veo_pjn(prompt, frame, out, key, "16:9", a.quality, a.duration, seed=c.get("seed"))
        if rc == 0 and out.exists():
            conform_to_render(out, int(render.get("width", 1920)),
                              int(render.get("height", 1080)), int(render.get("fps", 30)))
            it["status"] = "done"
            done += 1
        else:
            it["status"] = "failed"
            fail += 1
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print("완료 %d · 실패 %d" % (done, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
