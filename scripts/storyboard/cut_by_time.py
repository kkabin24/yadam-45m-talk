"""낭독 실측 시각으로 씬 경계를 끊어 storyboard 뼈대를 만든다.

왜 필요한가 (2026-08-18)
    사용자가 씬 간격을 초 단위로 지정한다("1편 0~5분은 20초, 나머지 30초 / 2~6편은 1분").
    글자수로 환산해 줄 범위를 손으로 잡으면 실제 낭독과 어긋난다. `_video/sentences.json`에
    문장별 실측 start/end가 있으므로 **그 시각으로 직접 끊는다.**

동작
    문장을 순서대로 담다가 누적 시간이 간격을 넘으면 거기서 씬을 끊는다.
    씬 경계는 항상 문장 경계이므로 자막 큐 경계와도 어긋나지 않는다(split_long_cues가 보장).
    cast·location·visual_desc는 빈 채로 두고 사람이 채운다.

사용법
    python3 scripts/storyboard/cut_by_time.py {P} --story 1 --plan "0-300:20,300-:30"
    python3 scripts/storyboard/cut_by_time.py {P} --story 2 --plan "0-:60"
"""
import argparse
import json
import pathlib
import re
import sys


def parse_plan(spec: str):
    """'0-300:20,300-:30' → [(0,300,20),(300,inf,30)]"""
    out = []
    for part in spec.split(","):
        rng, iv = part.split(":")
        a, b = rng.split("-")
        out.append((float(a), float(b) if b else float("inf"), float(iv)))
    return out


def interval_at(t: float, plan) -> float:
    for a, b, iv in plan:
        if a <= t < b:
            return iv
    return plan[-1][2]


def main() -> int:
    ap = argparse.ArgumentParser(description="실측 시각으로 씬 경계 끊기")
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--story", type=int, required=True, help="편 번호 1~6")
    ap.add_argument("--plan", required=True, help="구간:간격 목록 (편 시작 기준 초)")
    ap.add_argument("--out", type=pathlib.Path, help="출력 storyboard.json (미지정 시 표준출력)")
    ap.add_argument("--estimate", type=float, metavar="CPM",
                    help="★잠정 모드 — 낭독 실측(_video/sentences.json)이 아직 없을 때 "
                         "script_sentences.json + 확정 낭독 속도(자/분)로 시각을 추정한다. "
                         "낭독이 도착하면 이 옵션 없이 다시 돌려 경계를 확정할 것.")
    args = ap.parse_args()

    P = args.project_dir
    if args.estimate:
        # 상태머신은 STORYBOARD를 TTS보다 먼저 둔다(SKILL.md). 그런데 씬 간격 규칙(guide §9-1)은
        # 실측 시각을 쓰므로, 낭독 전에는 씬을 못 끊는 교착이 생긴다.
        # → 채널 확정 속도(자/분)로 문장별 지속시간을 추정해 같은 알고리즘을 태운다.
        #   문장 분리·idx는 script_sentences.json 그대로라 나중 실측본과 1:1로 대응된다.
        src = json.loads((P / "_video/script_sentences.json").read_text(encoding="utf-8"))["sentences"]
        sents, t = [], 0.0
        for s in src:
            dur = len(re.sub(r"\s", "", s["text"])) / args.estimate * 60
            sents.append({"idx": s["idx"], "text": s["text"], "start": t, "end": t + dur})
            t += dur
        print(f"  ★잠정 모드 — {args.estimate:g}자/분 추정 (낭독 도착 후 재실행 필요)", file=sys.stderr)
    else:
        sents = json.loads((P / "_video/sentences.json").read_text(encoding="utf-8"))["sentences"]
    ch = (P / f"_script/chapters/{args.story:02d}.md").read_text(encoding="utf-8")
    want = [l.strip() for l in ch.splitlines() if l.strip()]

    # 병합 문장 목록에서 이 편의 구간을 찾는다 (첫 문장·마지막 문장으로 앵커)
    norm = lambda s: re.sub(r"\s", "", s)
    # 병합본의 문장 분리는 대본 줄과 1:1이 아니다(대사·따옴표 처리가 다르다).
    # 끝 문장으로 찾으면 안 된다 — 닫는 정형구가 여러 편에 공통이라 엉뚱한 편을 잡는다(1·3·6편).
    # 그래서 **여섯 편의 첫 문장을 차례로 찾아** 편 경계를 만든다.
    # ★편 수는 chapters/ 를 세어서 정한다 (2026-08-21). 종전에는 range(1,7) 고정이라
    #   3편 편성(05편)에서 04.md 를 찾다가 StopIteration 으로 죽었다.
    n_ch = len(sorted((P / "_script/chapters").glob("[0-9][0-9].md")))
    firsts = []
    pos = 0
    for k in range(1, n_ch + 1):
        head = next(l.strip() for l in
                    (P / f"_script/chapters/{k:02d}.md").read_text(encoding="utf-8").splitlines()
                    if l.strip())
        # ★2026-08-29 — 편 머리가 훅 대사(§3-0)면 문장 분리기가 다음 문장과 붙여 놓는다.
        #   ("해야 해야 넘어가지 말고." + 전라도 무주 산골에…) 그래서 정확 일치가 아니라
        #   **접두 일치**로 찾는다. 정확 일치 케이스도 그대로 통과한다.
        i = next(j for j in range(pos, len(sents))
                 if norm(sents[j]["text"]).startswith(norm(head)))
        firsts.append(i)
        pos = i + 1
    firsts.append(len(sents))
    first = firsts[args.story - 1]
    seg = sents[first:firsts[args.story]]
    print(f"  편 {args.story}: 병합본 문장 {first}~{first + len(seg) - 1} "
          f"({len(seg)}개, 대본 줄 {len(want)}개)", file=sys.stderr)

    # ★start/end 폴백 (2026-08-21) — split_long_cues --apply 전에는 문장 레벨 start/end 가 없다.
    #   그때는 words[] 의 처음·끝을 쓴다(값은 SRT 큐 시각에서 나온 것이라 경계로 쓰기에 충분하다).
    for s in seg:
        if "start" not in s and s.get("words"):
            s["start"] = s["words"][0]["start"]
            s["end"] = s["words"][-1]["end"]

    base = seg[0]["start"]
    scenes, cur, cur_start = [], [], 0.0
    for s in seg:
        rel = s["start"] - base
        if cur and rel - cur_start >= interval_at(cur_start, parse_plan(args.plan)):
            scenes.append((cur, cur_start))
            cur, cur_start = [], rel
        cur.append(s)
    if cur:
        scenes.append((cur, cur_start))

    out = {"scenes": []}
    for n, (grp, st) in enumerate(scenes, 1):
        out["scenes"].append({
            "id": n,
            "act": f"story{args.story}",
            "narration": " ".join(g["text"] for g in grp),
            "cast": [],
            "location": None,
            "visual_desc": "",
            "sentences": [grp[0]["idx"] - first, grp[-1]["idx"] - first],
            "_t": [round(st, 1), round(grp[-1]["end"] - base, 1)],
        })
    txt = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(txt, encoding="utf-8")
        print(f"✓ {args.out}  씬 {len(scenes)}개 "
              f"(편 길이 {(seg[-1]['end'] - base) / 60:.1f}분)")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
