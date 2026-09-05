"""편별 storyboard 6개를 영상 하나짜리 storyboard로 합친다.

왜 필요한가
    옴니버스는 **편마다 하위 프로젝트**를 갖는다(characters/locations/scenes가 편별로 갈린다).
    그런데 `scene_timing.py`·`capcut_export.py`는 **영상 하나**를 다루므로 `{P}/storyboard.json`
    한 장을 본다. 그래서 편별 산출물을 여기서 이어 붙인다.

    이어 붙일 때 두 가지를 고친다:
      1. **문장 index를 병합본 기준으로 옮긴다** — 편별 storyboard의 `sentences`는 그 편 안에서
         0부터 세지만, `sentences.json`은 여섯 편을 이어 놓은 것이다. 편 시작 offset을 더한다.
         (안 더하면 2편 이후가 전부 1편 시각에 붙는다.)
      2. **이미지 경로를 {P} 기준 상대경로로 바꾼다** — 편별로는 `scenes/scene_01.png`이지만
         {P}에서 보면 `260815_1902_.../scenes/scene_01.png`이다.

    씬 id는 1부터 통째로 다시 매긴다. `act`에 `story{N}`이 남아 있으므로 편 경계는 그대로 읽힌다.

사용법
    python3 scripts/storyboard/merge_omnibus.py {P}
"""
import argparse
import json
import pathlib
import re
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="편별 storyboard → 영상 storyboard 병합")
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, help="기본 {P}/storyboard.json")
    args = ap.parse_args()

    P = args.project_dir
    out = args.out or (P / "storyboard.json")

    sents = json.loads((P / "_video/sentences.json").read_text(encoding="utf-8"))["sentences"]
    norm = lambda s: re.sub(r"\s", "", s)

    # 편별 시작 문장 index — 각 편 대본의 첫 줄을 병합본에서 순서대로 찾는다
    # ★편 수·편 폴더 이름을 가정하지 않는다 (2026-08-21). 종전에는 range(1,7)과
    #   glob("260815_*") 고정이라 3편 편성(05편)이나 다른 날짜 폴더에서 통째로 깨졌다.
    chapters = sorted((P / "_script/chapters").glob("[0-9][0-9].md"))
    n_ch = len(chapters)
    offsets, pos = [], 0
    for ch in chapters:
        head = next(l.strip() for l in ch.read_text(encoding="utf-8").splitlines() if l.strip())
        # ★2026-08-29 — 편 머리가 훅 대사(§3-0)면 문장 분리기가 다음 문장과 붙여 놓는다.
        #   ("해야 해야 넘어가지 말고." + 전라도 무주 산골에…) 그래서 정확 일치가 아니라
        #   **접두 일치**로 찾는다. 정확 일치 케이스도 그대로 통과한다.
        i = next(j for j in range(pos, len(sents))
                 if norm(sents[j]["text"]).startswith(norm(head)))
        offsets.append(i)
        pos = i + 1

    # 편 폴더 = storyboard.built.json 을 가진 하위 폴더(이름은 {YYMMDD_HHMM_짧은제목}).
    dirs = sorted(d for d in P.iterdir()
                  if d.is_dir() and (d / "storyboard.built.json").exists())
    if len(dirs) != n_ch:
        print(f"✗ 편 폴더 {len(dirs)}개 ≠ 대본 {n_ch}편 — "
              f"build.py 를 안 돌린 편이 있는지 확인하세요", file=sys.stderr)
        for d in sorted(x for x in P.iterdir() if x.is_dir() and not x.name.startswith("_")):
            mark = "✓" if (d / "storyboard.built.json").exists() else "✗"
            print(f"    {mark} {d.name}", file=sys.stderr)
        return 1

    merged, sid = [], 0
    for k, d in enumerate(dirs):
        src = d / "storyboard.built.json"
        if not src.exists():
            print(f"✗ {d.name}: storyboard.built.json 없음 — build.py 를 먼저 돌리세요", file=sys.stderr)
            return 1
        for s in json.loads(src.read_text(encoding="utf-8"))["scenes"]:
            if s.get("status") != "OK":
                print(f"⚠ {d.name} 씬 {s['id']}: status={s.get('status')} — 건너뜁니다", file=sys.stderr)
                continue
            sid += 1
            a, b = s["sentences"]
            merged.append({
                "id": sid,
                "act": s.get("act") or f"story{k + 1}",
                "narration": s["narration"],
                "cast": s.get("cast") or [],
                "location": s.get("location"),
                "sentences": [a + offsets[k], b + offsets[k]],
                "image": f"{d.name}/{s['image']}",
            })
            # ★훅 필드는 반드시 병합 보드로 옮긴다 (2026-08-29 실측). attach_hook.py 도
            #   burn_hook_subs.py 도 **병합 보드의 hook_line** 으로 편 도입부를 찾는다.
            #   빠뜨리면 "hook_line이 있는 씬이 없습니다" 로 훅 붙이기가 통째로 막힌다.
            for key in ("hook_line", "hook_speaker"):
                if s.get(key):
                    merged[-1][key] = s[key]

    out.write_text(json.dumps({"scenes": merged}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    # 커버리지 확인 — 빠지거나 겹치면 자막이 씬과 어긋난다
    prev = -1
    for s in merged:
        a, b = s["sentences"]
        if a != prev + 1:
            print(f"⚠ 씬 {s['id']}: 문장 {prev + 1}~{a - 1} 누락/중복", file=sys.stderr)
        prev = b
    tail = len(sents) - 1 - prev
    print(f"✓ {out}  씬 {len(merged)}개 · 문장 0~{prev} 커버"
          + (f" (끝 {tail}문장 남음)" if tail else " (빠짐 없음)"))
    for k, d in enumerate(dirs):
        n = sum(1 for s in merged if s["act"] == f"story{k + 1}")
        print(f"   {k + 1}편 {d.name[7:]:20s} {n:2d}장  문장 {offsets[k]}~")
    return 0


if __name__ == "__main__":
    sys.exit(main())
