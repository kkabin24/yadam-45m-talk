#!/usr/bin/env python3
"""
캐릭터 턴어라운드 시트 생성 — characters.json 을 읽어 인물별
정면·3/4·측면·후면 4뷰(흰 배경, 텍스트 0) 시트를 생성한다.

검증된 일관성 앵커: trait-lock(특히 build=키/체형/연령) + anchorProp + 문화앵커(STYLE).

Usage:
    python3 scripts/assets/turnaround.py <project_dir> [--only id1,id2] [--dry-run]

flat 인물  → assets/characters/<id>_turnaround.png
variant 인물 → assets/characters/<id>_<variant>_turnaround.png (각 variant마다)
characters.json의 turnaround 경로가 이미 파일로 존재하면 스킵(--force로 재생성).
"""
import argparse, concurrent.futures, json, os, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
GEN = HERE.parent / "image" / "generate_image.py"


def find_env_key(start, name="GEMINI_API_KEY"):
    cur = pathlib.Path(start).resolve()
    for _ in range(6):
        env = cur / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


# 구도(framing) — 출력은 1344x768 고정이라 4뷰로 나누면 한 칸이 336px뿐이다.
# 전신이면 얼굴 세로가 ~90px밖에 안 남아 홍채·속눈썹이 물리적으로 안 그려진다(2026-07-26 실측).
# 씬 100장 내내 시청자가 추적하는 건 얼굴이고 옷은 lock 텍스트로 잠글 수 있으므로,
# 통제하기 어려운 쪽(얼굴)에 픽셀을 몰아준다 → 기본 waist(얼굴 ~250px, 면적 7배).
# 예외: anchorProp이 하반신에 있거나(발목 끈 등) 실루엣 자체가 앵커인 인물은 characters.json에
#       "framing": "full" 을 지정한다.
FRAMING = {
    "waist": ("four WAIST-UP views in a row: front, 3/4, side, back. "
              "Each view is framed from the waist up so the FACE IS LARGE and clearly detailed — "
              "the face is the most important part of this sheet. "),
    "full":  ("four FULL-BODY views in a row: front, 3/4, side, back. "
              "Each figure fills the full height of the frame from head to feet, "
              "with minimal empty margin above and below. "),
}


def turnaround_prompt(lock, negatives, style, anchor=None, framing="waist"):
    neg_extra = (" " + ", ".join(negatives) + ".") if negatives else ""
    anchor_extra = (
        f" Signature identifying feature — must be clearly visible in ALL four views "
        f"including the back view: {anchor}." if anchor else ""
    )
    # "Full"/"full-body" 같은 표기 흔들림을 흡수한다 (characters.json은 LLM이 쓰므로 흔함).
    # 모르는 값이면 조용히 waist로 떨어지지 말고 경고 — framing="full"의 존재 이유가
    # '하반신 앵커(발목 끈 등)' 보존인데, 오타로 no-op 되면 앵커만 소리 없이 사라진다.
    key = str(framing or "waist").strip().lower()
    for k in FRAMING:                       # full / full-body / full_body / fullbody, waist / waist-up …
        if key.startswith(k):
            key = k
            break
    if key not in FRAMING:
        print(f"warning: 알 수 없는 framing={framing!r} — waist로 진행 "
              f"(가능한 값: {'/'.join(FRAMING)})", file=sys.stderr)
        key = "waist"
    views = FRAMING[key]
    return (
        f"Character turnaround model sheet of ONE single {lock}, "
        f"{views}"
        f"Plain pure white background. No labels, no swatches. "
        f"SAME identical person across all four views.{anchor_extra}{neg_extra} "
        f"{style['preset']} {style.get('negative', '')}"
    ).strip()


def jobs_for_char(cid, char):
    """(out_rel, lock, negatives, anchor, framing) 목록. flat=1개, variant=variant수만큼."""
    anchor = char.get("anchorProp")
    framing = char.get("framing", "waist")
    if "variants" in char:
        out = []
        for v, vd in char["variants"].items():
            rel = vd.get("turnaround") or f"assets/characters/{cid}_{v}_turnaround.png"
            out.append((rel, vd["lock"], vd.get("negatives", char.get("negatives", [])),
                        vd.get("anchorProp", anchor), vd.get("framing", framing)))
        return out
    rel = char.get("turnaround") or f"assets/characters/{cid}_turnaround.png"
    return [(rel, char["lock"], char.get("negatives", []), anchor, framing)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=0)
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    style = json.loads((project_dir.parent.parent / "config" / "style.json").read_text())
    characters = json.loads((project_dir / "characters.json").read_text())
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None

    tasks = []
    for cid, char in characters.items():
        if cid.startswith("_") or not isinstance(char, dict):
            continue
        if only and cid not in only:
            continue
        for rel, lock, negs, anchor, framing in jobs_for_char(cid, char):
            tasks.append((cid, rel, lock, negs, anchor, framing))

    key = find_env_key(project_dir)
    env = dict(os.environ)
    if key:
        env["GEMINI_API"] = key
    conc = args.concurrency or int(style.get("max_concurrent", 5))

    def run(t):
        # 태스크 단위로 예외를 가둔다 — 인물 하나의 잘못된 필드가 배치 전체를 죽이면
        # 이미 과금된 다른 인물들의 결과 보고까지 통째로 날아간다.
        cid, rel, lock, negs, anchor, framing = t
        try:
            out = (project_dir / rel)
            if out.exists() and not args.force and not args.dry_run:
                return cid, rel, "SKIP(exists)"
            prompt = turnaround_prompt(lock, negs, style, anchor, framing)
            out.parent.mkdir(parents=True, exist_ok=True)
            pf = out.with_suffix(".prompt.txt")
            pf.write_text(prompt, encoding="utf-8")
            if args.dry_run:
                return cid, rel, "DRY"
            r = subprocess.run(["python3", str(GEN), str(pf), str(out)], capture_output=True, text=True, env=env)
            return cid, rel, ("OK" if (out.exists() and r.returncode == 0) else "FAIL: " + (r.stderr or r.stdout)[-200:])
        except Exception as e:
            return cid, rel, f"FAIL(build): {type(e).__name__}: {e}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
        results = list(ex.map(run, tasks))
    for cid, rel, status in results:
        print(f"[{cid}] {rel} -> {status}")
    ok = sum(1 for _, _, s in results if s == "OK")
    print(f"\n턴어라운드: OK {ok} / 전체 {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
