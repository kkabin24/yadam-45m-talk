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
# ★뷰는 '단어'가 아니라 '각도'로 못박는다 (2026-07-26 실측).
# "front, 3/4, side, back"이라고만 쓰면 모델이 3/4와 side를 구분하지 못하고
# 2·3번을 좌우 옆모습(서로 거울상)으로 뽑는다 → 실질 3뷰가 되고, 정작 씬에서
# 가장 많이 쓰이는 3/4 각도의 참조가 통째로 빠진다. 도수 + 거울 금지를 명시할 것.
_VIEWS = (
    "EXACTLY FOUR panels of equal width filling the whole canvas edge to edge, evenly spaced, "
    "all four present — not three, not five. "
    # ★라벨 모양 토큰을 쓰지 않는다 (2026-09-01) — "(1) FRONT view"처럼 번호+대문자 명칭으로
    #   적으면 모델이 그것을 패널 캡션으로 읽고 시트에 글자로 구워 넣는다. style.json의
    #   텍스트 금지보다 이 포지티브 형태가 이긴다(CLAUDE.md 2026-08-17 원칙과 같은 계열).
    #   순서는 번호가 아니라 위치말(leftmost/next/rightmost)로 지시한다.
    "reading left to right the figure turns FURTHER in the SAME direction in each panel. "
    "Leftmost, facing the viewer straight on, both ears visible, nose centred. "
    "Next to it, head and body rotated about 45 degrees — BOTH eyes still visible, "
    "the far eyebrow and far cheek partly hidden by the bridge of the nose, one ear hidden. "
    "Next, rotated a full 90 degrees to a pure profile — only ONE eye visible, the nose and lips "
    "fully in silhouette against the background. "
    "Rightmost, seen from directly behind, no face at all, showing the back of the head and shoulders. "
    "These are four DIFFERENT rotation angles of one continuous turn. "
    "Do NOT mirror any panel, do NOT repeat the same angle twice, and the second and third "
    "panels must be clearly distinguishable from each other. "
)

FRAMING = {
    "waist": ("four WAIST-UP views " + _VIEWS +
              "Each view is framed from the waist up so the FACE IS LARGE and clearly detailed — "
              "the face is the most important part of this sheet. "),
    "full":  ("four FULL-BODY views " + _VIEWS +
              "Each figure fills the full height of the frame from head to feet, "
              "with minimal empty margin above and below. "),
}


def find_style_anchor(start, explicit=None):
    """채널 화풍 앵커 이미지 경로. config/style_anchor.png 를 위로 올라가며 찾는다.

    ★왜 필요한가 (2026-08-16 실측)
      build.py(씬)는 인물 시트를 ref로 넘기는데 turnaround.py는 ref를 한 장도 안 넘겼다.
      그래서 인물 시트끼리 서로를 못 보고, 같은 편 안에서도 화풍이 제각각으로 나온다
      (5편 춘보=부드러운 그라데이션 / 곱단=깔끔한 셀 셰이딩).
      확정된 시트 한 장을 채널 기준으로 붙이면 편·영상을 넘어 화풍이 묶인다.
    """
    if explicit:
        pth = pathlib.Path(explicit)
        return pth if pth.exists() else None
    cur = pathlib.Path(start).resolve()
    for _ in range(6):
        pth = cur / "config" / "style_anchor.png"
        if pth.exists():
            return pth
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def find_channel_config(start, name):
    """project_dir에서 위로 올라가며 config/<name>을 찾는다.
    ★프로젝트 깊이를 가정하지 않는다 — 옴니버스는 projects/01편/<프로젝트>/ 처럼
    편(영상) 폴더가 한 겹 더 끼므로 parent.parent 고정은 깨진다(2026-08-15)."""
    cur = pathlib.Path(start).resolve()
    for _ in range(6):
        p = cur / "config" / name
        if p.exists():
            return p
        if cur.parent == cur:
            break
        cur = cur.parent
    raise FileNotFoundError(f"config/{name} 을 {start} 상위에서 찾지 못했습니다")
# ─── 시대(era) 전환 — 2026-08-29 신설 ────────────────────────────────────────
# style.json은 종전에 '조선' 앵커를 preset/scene_preset/scene_preset_nocast 셋과
# scene_negative/scene_negative_nocast 둘, 모두 다섯 군데에 그대로 박아 두었다.
# 지금은 그 자리에 {ERA_ANCHOR}·{ERA_PROPS} 자리표만 두고 여기서 채운다.
# find_channel_config와 같은 이유로 세 스크립트에 그대로 복제해 둔다(공용 모듈 없음).
ERA_FIELDS = ("preset", "scene_preset", "scene_preset_nocast",
              "negative", "scene_negative", "scene_negative_nocast")


def resolve_era(style, era=None):
    """{ERA_ANCHOR}·{ERA_PROPS} 자리표를 고른 시대 문구로 채운다.

    고르는 순서: --era 인자 > style["era"].
    자리표가 없으면(구 style.json) 아무것도 하지 않는다.
    ★자리표가 있는데 시대를 못 찾으면 문화·시대 앵커가 통째로 빠져 서양 판타지로
      드리프트하므로(CLAUDE.md) 조용히 넘기지 않고 즉시 멈춘다.
    """
    if not any("{ERA_" in (style.get(k) or "") for k in ERA_FIELDS):
        return style
    eras = style.get("eras") or {}
    name = era or style.get("era")
    if not name or name not in eras:
        raise SystemExit(
            f"error: style.json의 시대 자리표를 채울 수 없습니다 — era={name!r} / "
            f"고를 수 있는 값: {', '.join(eras) if eras else '(eras 블록이 없습니다)'}")
    blk = eras[name]
    for k in ERA_FIELDS:
        if isinstance(style.get(k), str):
            style[k] = (style[k].replace("{ERA_ANCHOR}", blk.get("anchor", ""))
                                .replace("{ERA_PROPS}", blk.get("props", "")))
    print(f"[era] {name} — {blk.get('label', '')}")
    return style

# lock이 이 길이를 넘으면 뒤따르는 지시(각도·화풍)가 희석된다 — 2026-08-17 실측 기준선.
# 얼굴 6축을 완전한 문장으로 풀어 쓰면 700~900자가 되고 그때 4뷰가 무너졌다.
# 명사구로 나열하면 같은 정보가 절반 길이에 들어간다(SKILL.md 레지스트리 절).
LOCK_SOFT_LIMIT = 600
NEG_SOFT_LIMIT = 6


def project_era(project_dir):
    """{프로젝트}/era.txt 에 적힌 시대 이름. 없으면 None.

    ★시대는 프로젝트마다 다른데 style.json은 채널 공용이다. 채널 기본값(style["era"])에만
      기대면 시대가 다른 프로젝트를 동시에 만들 때 조용히 섞인다 — 2026-08-29 실측:
      다른 세션이 style.json의 기본 era를 modern1970s로 바꾼 뒤, 조선 편(10편 3편) 16장이
      시멘트 블록 담·철 대문·슬레이트 지붕·자전거가 있는 1970년대 마을로 생성됐다.
      프롬프트에 'Joseon'과 '1960s~1980s'가 동시에 들어간 모순 상태였다.
      그래서 프로젝트가 제 시대를 파일로 직접 선언한다.
    우선순위: --era > {프로젝트}/era.txt > style["era"]
    """
    p = pathlib.Path(project_dir) / "era.txt"
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    return None


def turnaround_prompt(lock, negatives, style, anchor=None, framing="waist", has_style_ref=False):
    neg_extra = (" " + ", ".join(negatives) + ".") if negatives else ""
    anchor_extra = (
        f" Signature identifying feature — must be clearly visible in ALL four views "
        f"including the back view: {anchor}." if anchor else ""
    )
    # "Full"/"full-body" 같은 표기 흔들림을 흡수한다 (characters.json은 LLM이 쓰므로 흔함).
    # 모르는 값이면 조용히 waist로 떨어지지 말고 경고 — framing="full"의 존재 이유가
    # '하반신 앵커(발목 끈 등)' 보존인데, 오타로 no-op 되면 앵커만 소리 없이 사라진다.
    # ★lock·negatives가 길면 뒤따르는 각도·화풍 지시가 희석된다(2026-08-17 실측).
    #   조용히 나빠지는 종류의 실패라 반드시 눈에 보이게 경고한다.
    if len(lock) > LOCK_SOFT_LIMIT:
        print(f"warning: lock이 {len(lock)}자입니다(권장 {LOCK_SOFT_LIMIT}자 이하) — "
              f"얼굴 묘사를 명사구로 줄이세요. 길면 4뷰 각도와 화풍이 무너집니다.", file=sys.stderr)
    if len(negatives) > NEG_SOFT_LIMIT:
        print(f"warning: negatives가 {len(negatives)}줄입니다(권장 {NEG_SOFT_LIMIT}줄 이하) — "
              f"공통 금지(사진풍·일본풍·텍스트)는 style.json에 이미 있으니 빼세요.", file=sys.stderr)

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
    # ★화풍 앵커를 붙일 때는 "사람이 아니라 그림체만 보라"를 아주 분명히 해야 한다.
    #   안 그러면 앵커 인물을 그대로 베껴 온다.
    style_ref = (
        " STYLE REFERENCE: the attached image is provided ONLY as a drawing-style sample. "
        "Match its lineart weight, its flat cel shading, its colour saturation and its overall "
        "finish EXACTLY. Do NOT copy the person in it — the character described above is a "
        "DIFFERENT person with different age, face, build and clothing. "
    ) if has_style_ref else ""
    # ★순서가 결과를 바꾼다 (2026-08-17 03편 실측)
    #   예전에는 lock을 맨 앞에 두고 {views}를 그 뒤에 붙였다. 그런데 얼굴 지문을 자세히 쓰면
    #   lock이 700~900자가 되고, 그만큼 각도 지시가 뒤로 밀려 **4뷰가 정면 2장으로 무너졌다**
    #   (최 첨지·월곡댁·강 좌수). 같은 인물이라도 각도 문구를 lock 맨 앞에 손으로 넣으면
    #   즉시 4각도가 살아났다 — 즉 문제는 문구의 유무가 아니라 위치였다.
    #   그래서 뷰 지시를 프롬프트 선두로 올리고, 인물 묘사를 그 뒤에 둔다.
    #   덧붙여 마지막에 두 규칙(각도·화풍)을 한 번 더 못 박는다(최신성 효과).
    return (
        # ★화풍 지시를 맨 앞(0%)에 한 번 더 둔다 (2026-08-23 신설).
        #   뷰 지시문만 1,200자라 채널 화풍이 44~47% 지점으로 밀렸고, 그 결과 인물마다
        #   그림체가 갈렸다(06편 실측 — 사용자 지적). build.py가 2026-08-18에 FULL_BLEED로
        #   쓴 해법과 같다: 위치가 곧 강도이므로 앞뒤 양쪽에 박는다.
        f"DRAWING STYLE (applies to the whole image, non-negotiable): {style['preset']} "
        # ★텍스트 금지도 선두에 (2026-08-23) — 본문 45% 지점에만 두면 뷰 캡션이 그대로 박힌다.
        #   06편 실측: 다섯 장 중 네 장에 FRONT/THREE-QUARTER/… 캡션이 찍혔다.
        f"THE IMAGE MUST CONTAIN NO TEXT AT ALL — no view labels, no captions, no numbers, no watermark. "
        f"Character turnaround model sheet. {views}"
        f"The single character drawn in all four panels is: {lock}. "
        f"Plain pure white background, one continuous white background shared by all four views. "
        # ★시트에 번호·테두리가 자주 따라붙는다(사용자 지적 2026-08-16). style.json negative의
        #   "no numbers" 만으로는 희석돼 안 잡히므로 시트 프롬프트 본문에서 직접 금지한다.
        f"ABSOLUTELY NO TEXT OR NUMBERS ANYWHERE: no view numbers, no \"1 2 3 4\", no labels such as "
        f"front/side/back, no captions above or below the figures, no colour swatches, no annotations. "
        f"NO panel borders, NO frame lines, NO boxes or rectangles drawn around or between the views, "
        f"no dividing lines of any kind — the four figures simply stand on one empty white field. "
        f"SAME identical person across all four views, drawn in ONE consistent drawing style."
        f"{anchor_extra}{neg_extra}{style_ref} "
        f"{style['preset']} {style.get('negative', '')} "
        # ★맨 끝 재확인 — 앞에서 한 번 말한 것이 긴 프롬프트에서 묻히는 것을 막는다.
        #   이 두 줄이 실제로 깨졌던 항목이다(각도 중복 / 인물마다 다른 화풍).
        f"FINAL CHECK before drawing: the four panels must be FOUR DIFFERENT rotation angles "
        f"(front / 45-degree three-quarter / 90-degree profile / back) — panel 1 and panel 2 must "
        f"NOT both be front views. And all four panels, and every character sheet in this series, "
        f"must share ONE identical drawing style — same lineart weight, same cel shading, same "
        f"colour treatment. Do not switch to a different art style for this character."
    ).strip()


def _negs(d, fallback=None):
    """씬과 공유하는 negatives + 턴어라운드 전용 negatives_turnaround.

    ★negatives는 build.py가 씬 프롬프트에도 그대로 넣는다(2026-08-18 04편 실측).
    얼굴형·눈매처럼 시트에서만 필요한 금지문을 negatives에 두면 씬 프롬프트가 길어져
    화풍 지시가 뒤로 밀린다(07편 화풍 갈림). 시트 전용 금지는 negatives_turnaround에 적는다.
    """
    base = d.get("negatives", fallback if fallback is not None else [])
    return list(base) + list(d.get("negatives_turnaround", []))


def face_extras(style, node, char=None):
    """성별 축 + 미모 등급을 lock 뒤에 붙일 문자열 (2026-08-31 신설).

    세 층 가운데 둘을 담당한다.
      ① 화풍  = style.json preset/scene_preset의 FACE RENDERING — 전원에게 자동(여기서 안 함)
      ② 성별  = face_sex[physical.sex] — 전원
      ③ 미모  = lead_face[sex] — characters.json의 tier == "lead" 인 인물만

    ★생김새를 지정하지 않는다. 인물별 얼굴 지문이 이겨야 하므로 성별 신호와 미모 등급만 준다
      (preset에 생김새를 넣으면 전원이 같은 얼굴로 수렴한다 — style.json _comment_faces).
    ★sex를 못 찾으면 성별 축을 조용히 건너뛴다(구 레지스트리 호환). tier도 마찬가지다.
    node = variant dict 또는 char dict, char = 부모 char dict(variant일 때 상속용).
    """
    def _get(key):
        v = (node or {}).get(key)
        if v is None and char:
            v = char.get(key)
        return v
    sex = ((_get("physical") or {}).get("sex") or "").strip().lower()
    sex = {"m": "male", "f": "female", "man": "male", "woman": "female"}.get(sex, sex)
    out = ""
    if sex in ("male", "female"):
        out += (style.get("face_sex") or {}).get(sex, "")
        if str(_get("tier") or "").strip().lower() == "lead":
            out += (style.get("lead_face") or {}).get(sex, "")
    return out


def jobs_for_char(cid, char, style=None):
    """(out_rel, lock, negatives, anchor, framing) 목록. flat=1개, variant=variant수만큼.

    ★style을 주면 lock 뒤에 성별 축·미모 등급을 붙인다(face_extras).
    """
    anchor = char.get("anchorProp")
    framing = char.get("framing", "waist")
    if "variants" in char:
        out = []
        for v, vd in char["variants"].items():
            rel = vd.get("turnaround") or f"assets/characters/{cid}_{v}_turnaround.png"
            out.append((rel, vd["lock"] + (face_extras(style, vd, char) if style else ""),
                        _negs(vd, char.get("negatives", [])),
                        vd.get("anchorProp", anchor), vd.get("framing", framing)))
        return out
    rel = char.get("turnaround") or f"assets/characters/{cid}_turnaround.png"
    return [(rel, char["lock"] + (face_extras(style, char) if style else ""),
             _negs(char), anchor, framing)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=0)
    ap.add_argument("--style-ref", default=None, help="화풍 앵커 이미지 경로(기본: 채널 config/style_anchor.png)")
    ap.add_argument("--no-style-ref", action="store_true", help="화풍 앵커를 붙이지 않는다")
    ap.add_argument("--era", default=None, help="시대 덮어쓰기 (style.json eras의 키). 미지정이면 style.json era. 01~10편 재생성은 --era joseon")
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    style = resolve_era(json.loads(find_channel_config(project_dir, "style.json").read_text(encoding="utf-8")),
                        args.era or project_era(project_dir))
    characters = json.loads((project_dir / "characters.json").read_text())
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None

    tasks = []
    for cid, char in characters.items():
        if cid.startswith("_") or not isinstance(char, dict):
            continue
        if only and cid not in only:
            continue
        for rel, lock, negs, anchor, framing in jobs_for_char(cid, char, style):
            tasks.append((cid, rel, lock, negs, anchor, framing))

    key = find_env_key(project_dir)
    env = dict(os.environ)
    if key:
        env["GEMINI_API"] = key
    conc = args.concurrency or int(style.get("max_concurrent", 5))
    style_anchor = None if args.no_style_ref else find_style_anchor(project_dir, args.style_ref)
    if style_anchor:
        print(f"화풍 앵커: {style_anchor}")
    else:
        print("화풍 앵커 없음 — config/style_anchor.png 를 두면 인물 간 화풍이 묶입니다")

    def run(t):
        # 태스크 단위로 예외를 가둔다 — 인물 하나의 잘못된 필드가 배치 전체를 죽이면
        # 이미 과금된 다른 인물들의 결과 보고까지 통째로 날아간다.
        cid, rel, lock, negs, anchor, framing = t
        try:
            out = (project_dir / rel)
            if out.exists() and not args.force and not args.dry_run:
                return cid, rel, "SKIP(exists)"
            prompt = turnaround_prompt(lock, negs, style, anchor, framing, bool(style_anchor))
            out.parent.mkdir(parents=True, exist_ok=True)
            pf = out.with_suffix(".prompt.txt")
            pf.write_text(prompt, encoding="utf-8")
            if args.dry_run:
                return cid, rel, "DRY"
            cmd = ["python3", str(GEN), str(pf), str(out)]
            if style_anchor:
                cmd.append(str(style_anchor))
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
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
