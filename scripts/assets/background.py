#!/usr/bin/env python3
"""
배경(location) 시트 생성 — locations.json 을 읽어 재등장 장소별
establishing 배경 시트(인물 없음)를 생성한다. 같은 장소가 씬마다 드리프트하는 것 방지.

Usage:
    python3 scripts/assets/background.py <project_dir> [--only id1,id2] [--dry-run] [--force]

→ assets/locations/<id>_sheet.png  (locations.json의 sheet 경로)
sheet 파일이 이미 존재하면 스킵(--force로 재생성).
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


def background_prompt(desc, style):
    return (
        f"{desc}. Environment / location establishing reference sheet, wide shot, "
        f"NO people, NO characters, empty scene. "
        # ★시트에 액자·여백이 생기면, 이 시트를 ref로 쓰는 씬으로 그대로 복제된다
        #   (2026-08-16 실측: s1_sutmak 시트의 흰 액자가 scene_13/16으로 옮았다)
        f"FULL BLEED: the artwork must reach all four edges — no white margin, no border, "
        f"no frame or matte around the picture, not a framed illustration placed on a white "
        f"background, no letterbox or pillarbox bands. "
        f"{style['preset']} {style.get('negative', '')}"
    ).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=0)
    ap.add_argument("--era", default=None, help="시대 덮어쓰기 (style.json eras의 키). 미지정이면 style.json era. 01~10편 재생성은 --era joseon")
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    style = resolve_era(json.loads(find_channel_config(project_dir, "style.json").read_text(encoding="utf-8")),
                        args.era or project_era(project_dir))
    lp = project_dir / "locations.json"
    if not lp.exists():
        print(f"locations.json 없음 — 생성할 배경 없음: {lp}")
        return 0
    locations = json.loads(lp.read_text())
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None

    tasks = []
    for lid, loc in locations.items():
        if lid.startswith("_") or not isinstance(loc, dict):
            continue
        if only and lid not in only:
            continue
        rel = loc.get("sheet") or f"assets/locations/{lid}_sheet.png"
        tasks.append((lid, rel, loc.get("desc", lid)))

    key = find_env_key(project_dir)
    env = dict(os.environ)
    if key:
        env["GEMINI_API"] = key
    conc = args.concurrency or int(style.get("max_concurrent", 5))

    def run(t):
        lid, rel, desc = t
        out = project_dir / rel
        if out.exists() and not args.force and not args.dry_run:
            return lid, rel, "SKIP(exists)"
        prompt = background_prompt(desc, style)
        out.parent.mkdir(parents=True, exist_ok=True)
        pf = out.with_suffix(".prompt.txt")
        pf.write_text(prompt, encoding="utf-8")
        if args.dry_run:
            return lid, rel, "DRY"
        r = subprocess.run(["python3", str(GEN), str(pf), str(out)], capture_output=True, text=True, env=env)
        return lid, rel, ("OK" if (out.exists() and r.returncode == 0) else "FAIL: " + (r.stderr or r.stdout)[-200:])

    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
        results = list(ex.map(run, tasks))
    for lid, rel, status in results:
        print(f"[{lid}] {rel} -> {status}")
    ok = sum(1 for _, _, s in results if s == "OK")

    # ★[2026-09-04] 시트 경로를 locations.json 에 되기록한다.
    #   build.py 는 locations[loc].get("sheet") 만 읽고 경로 폴백이 없다. 그동안 이 함수가
    #   시트를 굽기만 하고 필드를 비워 둔 탓에 배경 시트가 씬 ref 에서 조용히 빠졌다
    #   — 에러도 경고도 안 나고 씬은 그럴듯하게 나오므로 컨택트 시트로는 못 잡는다(12편이 그 상태로 나갔다).
    if not args.dry_run:
        wrote = []
        for lid, rel, status in results:
            if (project_dir / rel).exists() and locations.get(lid, {}).get("sheet") != rel:
                locations[lid]["sheet"] = rel
                wrote.append(lid)
        if wrote:
            lp.write_text(json.dumps(locations, ensure_ascii=False, indent=1), encoding="utf-8")
            print("locations.json sheet 필드 기록: " + ", ".join(wrote))
    print(f"\n배경 시트: OK {ok} / 전체 {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
