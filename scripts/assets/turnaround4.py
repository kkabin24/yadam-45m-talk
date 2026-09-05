#!/usr/bin/env python3
"""캐릭터 턴어라운드 — ★뷰당 1장씩 생성해 이어 붙인다 (turnaround.py의 4패널 방식 대체).

왜 나눴나 (2026-08-17 실측):
    한 장에 4패널을 요구하면 모델이 2번(3/4)과 3번(측면)을 **같은 각도로** 뽑는다.
    각도를 도수로 못 박고 거울상·중복을 금지해도 마찬가지다(04편 19장 중 절반 이상).
    실질 3뷰가 되고, 정작 씬에서 가장 많이 쓰이는 3/4 참조가 통째로 빠진다.
    뷰를 하나씩 주문하면 각도 준수는 쉬워지지만 인물 동일성이 흔들리므로,
    **정면을 먼저 뽑아 나머지 세 뷰의 ref로 넣어** 얼굴을 잠근다.

비용: 인물당 4콜(4패널 방식의 4배). 대신 재시도가 없어져 실질 차이는 더 작다.

Usage:
    python3 scripts/assets/turnaround4.py <project_dir> [--only id1,id2] [--force] [--dry-run]
"""
import argparse, concurrent.futures, json, os, pathlib, subprocess, sys
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
GEN = HERE.parent / "image" / "generate_image.py"

VIEWS = {
    "front": "The character faces the viewer STRAIGHT ON, head and shoulders square to camera, "
             "both ears visible, nose centred, both eyes fully visible and symmetrical.",
    "q34":   "The character's head and body are turned 45 DEGREES to their own LEFT — a three-quarter view. "
             "BOTH eyes are still visible but the far eye sits close to the edge of the face, the far eyebrow "
             "and far cheek are partly hidden behind the bridge of the nose, and only ONE ear is visible. "
             "This is NOT a full profile: the far cheek and the far eye must still be seen.",
    "side":  "A FULL PROFILE: the head and body are turned exactly 90 DEGREES to their own LEFT, so the face is "
             "seen edge-on. ONLY ONE eye and ONE ear are visible, and the nose, lips and chin form a clean "
             "silhouette against the background. The far eye is completely hidden.",
    "back":  "The character is seen from DIRECTLY BEHIND, facing away from the viewer. NO face, NO eyes, NO nose "
             "are visible at all — only the back of the head, the nape and the shoulders.",
}
ORDER = ["front", "q34", "side", "back"]


def find_up(start, rel):
    cur = pathlib.Path(start).resolve()
    for _ in range(6):
        p = cur / rel
        if p.exists():
            return p
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def find_env_key(start, name="GEMINI_API_KEY"):
    env = find_up(start, ".env")
    if not env:
        return None
    for line in env.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def build(lock, negatives, style, anchor, view, framing, has_identity_ref):
    neg = (" " + ", ".join(negatives) + ".") if negatives else ""
    anc = f" Signature identifying feature, clearly visible in this view: {anchor}." if anchor else ""
    frame = ("Framed from the WAIST UP so the FACE IS LARGE and clearly detailed."
             if str(framing).lower().startswith("waist")
             else "FULL BODY from head to feet, filling the frame height.")
    ref = (" IDENTITY REFERENCE: the attached image is the SAME character seen from another angle. Keep the face, "
           "age, hair, clothing and colours EXACTLY the same — only the viewing angle changes. "
           if has_identity_ref else
           " STYLE REFERENCE: the attached image is ONLY a drawing-style sample. Match its lineart weight, flat cel "
           "shading and colour finish, but do NOT copy the person in it. ")
    return (f"A single character reference drawing of ONE {lock}. {VIEWS[view]} {frame} "
            f"ONE figure only, standing still, arms relaxed, neutral calm expression. "
            f"Plain pure white background. ABSOLUTELY NO TEXT, no numbers, no labels, no captions, "
            f"no panel borders, no frame lines.{anc}{neg}{ref} "
            f"{style['preset']} {style.get('negative', '')}").strip()


def stitch(paths, dest):
    ims = [Image.open(p).convert("RGB") for p in paths]
    h = min(i.height for i in ims)
    ims = [i.resize((int(i.width * h / i.height), h)) for i in ims]
    sheet = Image.new("RGB", (sum(i.width for i in ims), h), "white")
    x = 0
    for i in ims:
        sheet.paste(i, (x, 0))
        x += i.width
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return sheet.size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=3)
    a = ap.parse_args()

    pd = a.project_dir.resolve()
    style = json.loads(find_up(pd, "config/style.json").read_text(encoding="utf-8"))
    chars = json.loads((pd / "characters.json").read_text(encoding="utf-8"))
    style_anchor = find_up(pd, "config/style_anchor.png")
    only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None

    env = dict(os.environ)
    key = find_env_key(pd)
    if key:
        env["GEMINI_API"] = key

    tasks = [(cid, c) for cid, c in chars.items()
             if not cid.startswith("_") and isinstance(c, dict) and (not only or cid in only)]

    def run(item):
        cid, c = item
        dest = pd / (c.get("turnaround") or f"assets/characters/{cid}_turnaround.png")
        if dest.exists() and not a.force and not a.dry_run:
            return cid, "SKIP(exists)"
        tmp = pd / "assets/characters/_views"
        tmp.mkdir(parents=True, exist_ok=True)
        made = []
        for i, v in enumerate(ORDER):
            out = tmp / f"{cid}_{v}.png"
            prompt = build(c["lock"], c.get("negatives", []), style, c.get("anchorProp"),
                           v, c.get("framing", "waist"), i > 0)
            pf = out.with_suffix(".prompt.txt")
            pf.write_text(prompt, encoding="utf-8")
            if a.dry_run:
                made.append(out)
                continue
            refs = ([str(made[0])] if i else []) + ([str(style_anchor)] if style_anchor else [])
            r = subprocess.run(["python3", str(GEN), str(pf), str(out)] + refs,
                               capture_output=True, text=True, env=env)
            if not out.exists() or r.returncode != 0:
                return cid, f"FAIL({v}): " + (r.stderr or r.stdout)[-160:]
            made.append(out)
        if a.dry_run:
            return cid, "DRY"
        size = stitch(made, dest)
        return cid, f"OK {size[0]}x{size[1]}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        results = list(ex.map(run, tasks))
    for cid, st in results:
        print(f"[{cid}] {st}")
    ok = sum(1 for _, s in results if s.startswith("OK") or s == "DRY")
    print(f"\n턴어라운드(뷰별 생성): OK {ok} / 전체 {len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
