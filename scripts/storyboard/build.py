#!/usr/bin/env python3
"""
씬 이미지 일괄 생성 — characters.json + locations.json + storyboard.json 을 읽어
씬별 cast(+location) ref를 골라 첨부하고 Nano Banana Pro로 생성한다.

빈밥상 build_storyboard.py의 검증된 방식(씬별 cast ref만 첨부 + trait-lock 치환 +
STYLE 문화앵커 append)을 프로젝트/채널 무관하게 일반화한 것.

Usage:
    python3 scripts/storyboard/build.py <project_dir> [--only 1,2,3] [--dry-run] [--concurrency N]

project_dir = channels/<채널>/projects/<프로젝트>  (characters.json/locations.json/storyboard.json 위치)
채널 config(style.json)은 project_dir 상위(../../config/style.json)에서 자동 로드.

입력(소스, 보존):  storyboard.json  { "scenes": [ {id, act, narration, cast[], location, visual_desc}, ... ] }
출력:              storyboard.built.json  (각 씬에 image_prompt / image / status 추가)
                   scenes/scene_NN.png,  scenes/_pNN.txt (프롬프트)

cast 토큰: "id" 또는 "id:variant". visual_desc의 {id}(base id)는 해당 인물 lock 문장으로 치환.
refs: cast turnaround(순서대로) + location.sheet(존재 시) → style.max_refs 로 캡, cast 우선.
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GEN = HERE.parent / "image" / "generate_image.py"


# ★씬 서술 감사용 정규식 (2026-09-02, 12편 157씬 전수 검수).
#   구도(얼굴 크기)와 표정은 씬 visual_desc 에만 쓸 수 있다 —
#   구도는 style.json 이 "인물은 크게"라고 해도 씬이 전경을 요구하면 지고,
#   표정은 인물 lock 에 쓰면 편 전체가 그 얼굴로 굳는다(CLAUDE.md).
#   그래서 둘 다 빠지면 눈은 회색 얼룩, 얼굴은 무표정으로 나온다.
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

# ★이름 있는 인물을 작게/흐리게 그리라고 쓴 자리 (2026-09-02, 12편 1편 씬49 실측).
#   군중을 흐리게 하는 것은 정상이므로, 같은 문장에 군중 낱말이 있으면 넘어간다.
_RE_SHRINK_WORD = re.compile(
    r"\b(?:smaller|small and|small,|out of focus|blurred|far back|"
    r"in the distance|further off|distant)\b", re.I)
_RE_SHRINK = re.compile(
    r"[^.]*\b(?:smaller|small and|small,|out of focus|blurred|far back|"
    r"in the distance|further off|distant)\b[^.]*", re.I)
_RE_CROWD_WORD = re.compile(
    r"\b(?:background people|onlookers|villagers|crowd|bystanders|shoppers|guests|"
    r"everyone else|other people|the others|passers-?by|porters|women|men|children|"
    r"customers|traders|visitors|figures)\b", re.I)
# 사람이 아니라 배경을 흐리게 한 문장은 정상이다 — 풍경 낱말이 있으면 넘어간다.
_RE_SCENERY = re.compile(
    r"\b(?:ridges?|hills?|mountains?|valley|sky|water|stream|field|woods?|trees?|"
    r"background|backdrop|wall|walls|roofs?|eaves|houses?|buildings?|street|lane|road|"
    r"market|stalls?|room|yard|courtyard|kitchen|shelves|pass|cairn|switchback)\b", re.I)
# 문장에 사람이 있어야 '인물 축소'다.
# 시트를 가진 인물을 가리키는 말 — 대명사와 {id} 토큰.
_RE_PRONOUN = re.compile(
    r"(?:\{[a-z0-9_]+\}|\b(?:he|she|him|her|his|they|them|their)\b)", re.I)
# 이름 없는 단역을 가리키는 말 — 관사+명사(the miller / the broker's man).
_RE_ROLE = re.compile(r"\bthe [a-z']+(?: [a-z']+)?\b", re.I)
# "not blurred", "never small" 처럼 금지문이면 도리어 올바른 지시다.
_RE_NEGATED = re.compile(r"\b(?:not|never|no|neither|avoid)\b[^.]{0,40}?\b(?:small|smaller|"
                         r"blurred|out of focus|distant|background figure)\b", re.I)


def scene_problems(scene, characters, style, project_dir):
    """씬 하나의 연출·ref 문제 목록. 없으면 빈 리스트."""
    v = scene.get("visual_desc", "") or ""
    cast = [str(c).partition(":")[0] for c in (scene.get("cast") or [])]
    out = []
    if cast:
        if not _RE_FRAMING.search(v):
            out.append("구도없음")
        if not _RE_EXPRESSION.search(v):
            out.append("표정없음")
        for m in _RE_SHRINK.finditer(v):
            sent = m.group(0)
            if _RE_CROWD_WORD.search(sent) or _RE_NEGATED.search(sent):
                continue                      # 군중을 흐리게/금지문 — 정상
            key = _RE_SHRINK_WORD.search(sent)
            if not key:
                continue
            before = sent[:key.start()]
            # ★어순으로 가린다 — 흐리게 하라는 말 **바로 앞**의 표지가 무엇인가:
            #   대명사·{id} 라면 시트를 가진 인물(지적), 풍경 낱말이면 배경(정상),
            #   관사+명사라면 이름 없는 단역(정상).
            pp = max((x.end() for x in _RE_PRONOUN.finditer(before)), default=-1)
            sp = max((x.end() for x in _RE_SCENERY.finditer(before)), default=-1)
            rp = max((x.end() for x in _RE_ROLE.finditer(before)), default=-1)
            if pp < 0 or pp < sp or pp < rp:
                continue
            out.append("인물축소:" + " ".join(sent.split())[:60])
            break
    orphan = sorted({c for c in characters if "{" + c + "}" in v} - set(cast))
    if orphan:
        out.append("캐스팅누락:" + ",".join(orphan))
    for cid in cast:
        if cid not in characters:
            out.append("없는인물:" + cid)
            continue
        try:
            rel = resolve_cast(cid, characters, style)["turnaround"]
        except Exception:
            continue
        if rel and not (project_dir / rel).exists():
            out.append("시트없음:" + cid)
    if len(cast) > int(style.get("max_refs", 5)):
        out.append("cast %d명>max_refs — 뒤쪽 인물 시트가 잘린다" % len(cast))
    return out


def preflight(scenes, characters, style, project_dir, allow=False):
    """생성 전 검사. 문제가 있으면 목록을 찍고 True(=중단해야 함)를 돌려준다."""
    bad = [(s["id"], scene_problems(s, characters, style, project_dir)) for s in scenes]
    bad = [(i, pr) for i, pr in bad if pr]
    if not bad:
        return False
    head = ("경고: 연출·ref 점검" if allow
            else "✗ 연출·ref 점검 실패 — 한 장도 생성하지 않았습니다")
    print("", file=sys.stderr)
    print("%s (%d씬)" % (head, len(bad)), file=sys.stderr)
    for i, probs in bad[:60]:
        print("    씬%-4d %s" % (i, " / ".join(probs)), file=sys.stderr)
    if len(bad) > 60:
        print("    … 외 %d씬" % (len(bad) - 60), file=sys.stderr)
    print("", file=sys.stderr)
    print("  고치는 법 — 그 씬 visual_desc 에:", file=sys.stderr)
    print("    구도: 'CLOSE MEDIUM SHOT - her face LARGE in frame, cut at the chest'", file=sys.stderr)
    print("    표정: 그 순간의 표정. 씬마다 다르게 쓴다(다 같으면 무표정과 다를 바 없다).", file=sys.stderr)
    print("    ★인물축소: 이름 있는 인물을 작게·흐리게 그리라고 쓰지 않는다 — 얼굴이 작아지면",
          file=sys.stderr)
    print("      턴어라운드 ref 가 죽어 딴사람이 된다. 멀리 두려면 어깨 너머 구도로 잡는다.",
          file=sys.stderr)
    print("  일괄 점검: python3 scripts/storyboard/check_desc.py <편 폴더...>", file=sys.stderr)
    if allow:
        print("  (--allow-undirected 라서 그대로 진행합니다)", file=sys.stderr)
        print("", file=sys.stderr)
        return False
    print("  강행하려면 --allow-undirected", file=sys.stderr)
    print("", file=sys.stderr)
    return True


def load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_env_key(start: pathlib.Path, name: str = "GEMINI_API_KEY") -> str | None:
    cur = start.resolve()
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


def resolve_cast(token: str, characters: dict, style=None) -> dict:
    """cast 토큰("id" | "id:variant") → {base_id, lock, turnaround(상대경로), negatives, anchor}."""
    base, _, variant = token.partition(":")
    char = characters.get(base)
    if not char:
        raise KeyError(f"characters.json에 없는 인물: {base!r} (씬 cast={token!r})")
    if "variants" in char:
        v = variant or char.get("default_variant")
        if not v:
            raise KeyError(f"{base!r}는 variant 인물인데 variant 미지정이고 default_variant 없음")
        vd = char["variants"].get(v)
        if not vd:
            raise KeyError(f"{base!r}에 variant {v!r} 없음")
        return {"base": base, "lock": vd["lock"] + (face_extras(style, vd, char) if style else ""),
                # ★v(해석된 variant)를 쓴다 — variant는 토큰에 없을 수 있고(default_variant 사용),
                #   그때 {variant}는 빈 문자열이라 경로가 "..._ _turnaround.png"로 깨진다 (2026-08-29)
                "turnaround": vd.get("turnaround") or f"assets/characters/{base}_{v}_turnaround.png",
                "negatives": vd.get("negatives", char.get("negatives", [])),
                "anchor": vd.get("anchorProp", char.get("anchorProp"))}
    # ★turnaround 경로가 비어 있으면 turnaround.py와 같은 기본 경로를 쓴다.
    #   (2026-08-16 실측: build.py만 기본값이 없어 ref가 조용히 0장으로 붙었다)
    return {"base": base, "lock": char["lock"] + (face_extras(style, char) if style else ""),
            "turnaround": char.get("turnaround") or f"assets/characters/{base}_turnaround.png",
            "negatives": char.get("negatives", []), "anchor": char.get("anchorProp")}


def build_scene(scene, characters, locations, style, project_dir):
    """씬 하나 → (prompt_text, ref_paths[])."""
    desc = scene["visual_desc"]
    scene_desc_raw = desc
    cast_tokens = scene.get("cast") or []
    ref_paths = []
    cast_anchors = []
    char_refs = 0          # 실제로 붙은 '인물' 시트 수 (배경 시트는 제외 — identity 게이트용)
    loc_ref = False        # 배경 시트가 실제로 붙었는가 (place 게이트용)

    # cast: lock 치환 + turnaround ref + negatives/anchor 수집
    # ★negatives는 인물별로 lock 바로 뒤에 붙인다 — 씬 전체에 뭉쳐 붙이면 여러 인물의 금지어가
    #   섞여 서로 충돌한다(늙은 마님 "not young" + 젊은 하인 "not old"가 한 줄에 들어가는 식).
    for token in cast_tokens:
        r = resolve_cast(token, characters, style)
        negs = list(dict.fromkeys(r.get("negatives") or []))
        who = r["lock"] + (f" ({', '.join(negs)})" if negs else "")
        desc = desc.replace("{" + r["base"] + "}", who)
        if r["turnaround"]:
            p = (project_dir / r["turnaround"]).resolve()
            if p.exists():
                ref_paths.append(str(p))
                char_refs += 1
        if r.get("anchor"):
            cast_anchors.append(r["anchor"])

    # 남은 미치환 {id} 안전망 (cast에 없지만 desc에 등장 시 lock만 주입, ref 없이)
    # ★2026-09-02: 조용히 넘어가지 않고 경고한다. visual_desc에 인물이 나오는데 cast가 비면
    #   ①턴어라운드 ref가 안 붙어 얼굴이 드리프트하고 ②cast=[] 이라 build 가 군중 씬으로 보고
    #   scene_preset_nocast / scene_negative_nocast 를 쓴다 — 거기엔 미형 조항도 홍채 색 지정도
    #   없고 도리어 'NO LARGE FACE ANYWHERE' 가 걸려 있다. 11편 1편 씬07·09 실측(얼굴이 딴사람).
    _orphans = [cid for cid in characters if "{" + cid + "}" in desc]
    if _orphans:
        print(f"warning: 씬 {scene.get('id')} — visual_desc 에 {', '.join(_orphans)} 가 있는데 "
              f"cast 에 없습니다. ref·미형·홍채 지정이 빠져 얼굴이 달라집니다. cast 에 추가하세요.",
              file=sys.stderr)

    # ★2026-09-02: 구도·표정 지시 누락 경고 (12편 157씬 전수 검수에서 확립).
    #   ①구도를 안 적으면 모델이 전경(全景)으로 잡아 얼굴이 화면 높이의 1/10도 안 된다.
    #     그 크기에는 홍채를 그릴 화소가 없어 눈이 회색 얼룩이 된다 — style.json 이 아무리
    #     'VERY DARK BROWN iris' 를 외쳐도 그릴 자리가 없으면 못 그린다.
    #   ②표정을 안 적으면 무표정 미인 얼굴로 수렴한다. 표정은 lock 에 쓰면 편 전체가 그 얼굴이
    #     되므로(CLAUDE.md) visual_desc 가 유일한 자리인데, 12편은 87%가 비어 있었다.
    #   자동으로 끼워 넣지는 않는다 — 연출은 사람이 정한다. 여기서는 빠진 것만 알린다.
    if cast_tokens:
        if not _RE_FRAMING.search(scene_desc_raw):
            print(f"warning: 씬 {scene.get('id')} — visual_desc 에 구도 지시가 없습니다. "
                  f"얼굴이 작게 잡혀 눈동자가 뿌옇게 나옵니다 "
                  f"(예: 'CLOSE MEDIUM SHOT - her face large in frame, cut at the chest').",
                  file=sys.stderr)
        if not _RE_EXPRESSION.search(scene_desc_raw):
            print(f"warning: 씬 {scene.get('id')} — visual_desc 에 표정 지시가 없습니다. "
                  f"무표정으로 나옵니다 (예: 'her brows drawn together, her mouth pressed thin').",
                  file=sys.stderr)
    for cid, char in characters.items():
        tok = "{" + cid + "}"
        if tok in desc:
            lock = char.get("lock") or (char.get("variants", {}).get(char.get("default_variant", ""), {}) or {}).get("lock", cid)
            desc = desc.replace(tok, lock)

    # location sheet (존재할 때만, cast 다음 순위)
    loc = scene.get("location")
    if loc and loc in locations:
        sheet = locations[loc].get("sheet")
        if sheet:
            sp = (project_dir / sheet).resolve()
            if sp.exists():
                ref_paths.append(str(sp))
                loc_ref = True

    # max_refs 캡 (cast 우선 — 리스트 앞쪽이 cast)
    max_refs = int(style.get("max_refs", 5))
    ref_paths = ref_paths[:max_refs]
    char_refs = min(char_refs, len(ref_paths))   # cast가 앞이라 캡에 잘리면 인물 시트가 먼저 남는다

    # [일관성 2026-07-26] 씬은 편당 ~100장이라 여기서 새는 만큼 드리프트가 누적된다.
    # 그동안 씬 프롬프트에는 lock만 들어가고 turnaround에만 걸리던 방어선을 씬에도 건다:
    #  ① 동일 인물 지시 — ref를 붙이기만 하고 "이 얼굴을 쓰라"는 말이 없으면 모델이 참고 수준으로만 쓴다.
    #  ② negatives — 위 cast 루프에서 인물별로 lock 뒤에 붙임(뭉치면 충돌하므로).
    #  ③ anchorProp 재확인 — 보통 lock에 녹여 쓰지만 누락된 프로젝트가 있어 안전망.
    # ★게이트는 ref_paths가 아니라 char_refs — ref_paths에는 배경 시트도 섞여 있어서,
    #   cast가 빈 씬(무인 establishing shot 등)에 "render each character"가 붙는 사고가 났다
    #   (옹기과부 297씬 중 31씬, visual_desc가 "No people"인 씬 포함. 2026-07-26 검증에서 발견).
    identity = (
        " The attached character sheet(s) are the definitive reference for the people in this scene — "
        "render each character with the SAME face, hairstyle, body proportions and outfit as their "
        "reference sheet. Same person, not a look-alike."
        # ★ref가 4분할 턴어라운드 시트라, 씬까지 패널로 쪼개 그리는 실패가 난다
        #   (2026-08-16 실측: 씬10이 같은 그림 두 장을 좌우로 붙인 이중 패널로 나왔다)
        " This is ONE single continuous illustration of one moment — NOT a character sheet, "
        "NOT a diptych or triptych, no split panels, no vertical or horizontal dividing line, "
        "no repeated or mirrored copy of the same figure anywhere in the frame."
        # ★시트는 순백 배경이다. 그 배경까지 따라 그려서 씬에 흰 액자·레터박스가 생긴다
        #   (2026-08-16 실측: 1편 16씬 중 3장 — scene_10/13/16)
        " Use the sheets ONLY for who the characters are — do NOT copy the sheet's plain white "
        "background, do NOT copy its panel layout, and do NOT leave any white margin. "
        "This scene has a fully painted environment reaching all four edges."
        # ★[ref 흔들림 방지 2026-09-02] 씬 서술이 인물을 작게/흐리게 그리라고 하면 얼굴에
        #   홍채와 이목구비를 그릴 화소가 없어지고, 그 순간 모델은 시트를 버리고 평균적인
        #   얼굴을 만들어 넣는다(12편 1편 씬49 실측 — 종배가 딴사람이 됐다).
        #   크기가 곧 ref 의 생존 조건이므로, 시트를 붙인 씬에는 언제나 이 문장을 건다.
        " EVERY character who has a reference sheet must be drawn LARGE ENOUGH THAT THEIR "
        "FACE IS CLEARLY READABLE — eyes, brows, nose and mouth fully drawn, the iris dark "
        "with a catchlight. Never reduce a character who has a sheet to a small, distant or "
        "blurred background figure, even when they stand further away than the others: keep "
        "them in focus and give their face real space in the frame. Only unnamed background "
        "people may be small and softly out of focus."
    ) if char_refs else ""
    # ★[배경 고정 방지 2026-09-04 사용자 지적 — "배경이 계속 고정되고 인물들만 위에 올라가는 느낌"]
    #   배경 시트는 그동안 '아무 설명 없이' ref로만 붙었다. 인물 시트에는 "얼굴·의상만 참조하고
    #   포즈는 베끼지 말라"가 붙는데 배경에는 대응 문장이 없어서, 모델이 시트의 구도를 통째로
    #   재현하고 인물만 그 위에 올렸다. 13편 실측: 장소가 지정된 117씬이 전부 같은 각도였다.
    #   시트는 '이 장소가 무엇으로 되어 있는가'를 정의할 뿐 카메라 위치가 아니다.
    place = (
        " The attached BACKGROUND sheet is the reference for WHAT THIS PLACE IS MADE OF — its buildings, "
        "materials, roofline, walls, fence, ground and the objects that belong to it — and NOT for the camera. "
        "Do NOT reproduce the background sheet's viewpoint, framing or composition. This picture is seen from "
        "ITS OWN vantage point, as the scene description says: a different part of the place, a different "
        "distance, a different direction and a different height, so that two pictures of this place never look "
        "like the same photograph with different people pasted onto it. Show only the part of the place this "
        "moment needs - a corner, a doorway, a wall, a work surface - rather than the whole site every time."
    ) if loc_ref else ""
    anchor_extra = (
        " Keep these identifying features clearly visible: " + "; ".join(dict.fromkeys(cast_anchors)) + "."
    ) if cast_anchors else ""

    # 씬은 조명·입체감이 살아야 하고 턴어라운드는 흰 배경 평면 시트라 필요한 렌더링 어휘가 다르다.
    # style.json에 scene_preset이 있으면 씬에만 그걸 쓰고, 없으면 기존대로 preset.
    # (turnaround.py / background.py는 계속 preset을 쓴다 — 확정된 시트 톤 보존)
    preset = style.get("scene_preset") or style["preset"]
    negative = style.get("scene_negative") or style.get("negative", "")

    # ★[얼굴 환각 방어 2026-08-02] cast가 빈 씬에는 인물 시트가 한 장도 안 붙는다.
    # 그런데 scene_preset의 절반 이상이 "얼굴을 이렇게 렌더링하라"는 지시다(눈동자 홍채 결,
    # 속눈썹, 피부 subsurface, 얼굴을 조각하는 광원…). 참조 시트도 없고 화면에 얼굴도 없으니
    # 모델이 그 지시를 만족시키려고 **없는 얼굴을 만들어 넣고, 잡아 줄 앵커가 없어 사진풍으로
    # 그린다.** 산가지 실측: cast 없는 19씬 중 7씬(9·10·14·90·105·116·122)이 이렇게 깨졌다
    # — 무인 풍경 절반을 사진 같은 여자 얼굴이 차지하는 식.
    # → cast가 없으면 얼굴 렌더링 블록을 뺀 프리셋으로 갈아끼운다.
    #   style.json에 scene_preset_nocast / scene_negative_nocast가 있을 때만 동작하므로
    #   해당 필드가 없는 다른 채널은 기존 동작 그대로다.
    if not char_refs:
        preset = style.get("scene_preset_nocast") or preset
        negative = style.get("scene_negative_nocast") or negative

    # ★풀블리드 강제 (2026-08-17 사용자 지시 — "이미지 생성할 때 하얀색 테두리 생성되지 않도록 코드에 박아줘")
    #   style.json scene_negative의 'no letterboxing, no black bars'만으로는 안 잡힌다(fix_letterbox.py 배경 참조).
    #   turnaround.py가 시트 번호·테두리를 프롬프트 본문에서 직접 금지해 잡은 것과 같은 처방 —
    #   네거티브는 희석되지만 본문 지시는 살아남는다.
    # ★"멀뚱히 서 있는 사진"만 나오는 문제 (2026-08-17 사용자 지적, A/B 판정 완료)
    #   원인은 ref가 아니라 프롬프트였다 — 씬 3장을 동작·카메라 지시로 다시 써서 뽑자 즉시 해결됐다.
    #   cast ref(턴어라운드)는 4뷰 모두 '차렷 자세'라, 아무 말도 안 하면 그 포즈가 씬으로 그대로 넘어온다.
    #   그래서 ①이건 인물 소개컷이 아니라 서사 스틸이고 ②ref는 얼굴·의상만 참조하라를 매 씬에 못 박는다.
    CANDID = (
        " THIS IS A CANDID NARRATIVE FILM STILL, NOT A CHARACTER PORTRAIT. "
        "The people are caught in the middle of doing something — mid-action, mid-stride, mid-gesture. "
        "NOBODY poses for the camera, NOBODY smiles at the viewer, NOBODY stands squarely facing front "
        "with arms at their sides. Bodies may be turned away, backs to camera, faces partly hidden or "
        "out of frame if the moment calls for it. "
        "The attached reference sheets define FACE, HAIR, BUILD AND CLOTHING ONLY — "
        "do NOT copy the standing pose or the front-facing angle from them. "
    )
    FULL_BLEED = (
        " FULL-BLEED COMPOSITION: the artwork fills the ENTIRE 16:9 canvas edge to edge, "
        "corner to corner, with NOTHING around it. "
        "ABSOLUTELY NO white border, NO white frame, NO white margin, NO paper mount or passe-partout, "
        "NO black letterbox bars top or bottom, NO pillarbox bars left or right, "
        "NO rounded corners, NO drop shadow behind the image, NO polaroid or postcard framing, "
        "NO panel border or outline of any kind. The illustration bleeds off all four edges. "
    )
    # ★풀블리드 지시를 프롬프트 맨 앞으로 (2026-08-18 사용자 지적 — "처음 뽑을 때 흰 테두리가 생긴다")
    #   FULL_BLEED는 원래 desc·lock 뒤(전체의 36% 지점)에 있었다. 인물 lock이 3,000자를 넘는 씬에서는
    #   그 앞을 다 읽고 나서야 나오므로, 턴어라운드 시트의 순백 배경을 따라 그리려는 힘을 못 이긴다.
    #   CLAUDE.md의 "긴 lock이 화풍·구도 지시를 밀어낸다"와 같은 현상이다 — 위치가 곧 강도다.
    #   앞(LEAD)과 뒤(FULL_BLEED)에 한 번씩 두어 양쪽에서 잡는다.
    LEAD = (
        "ONE single full-bleed 16:9 illustration. The picture fills the whole canvas edge to edge and "
        "bleeds off all four sides: NO white border, NO white margin, NO frame or mount, NO black bars, "
        "NO rounded corners. Every pixel to the very edge is painted scene. "
    )
    prompt = (f"{LEAD}{desc.strip()}{identity}{place}{anchor_extra}{CANDID}{FULL_BLEED} {preset} {negative}").strip()
    return prompt, ref_paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--only", default="", help="생성할 씬 id 목록 (쉼표구분). 미지정 시 전체.")
    ap.add_argument("--dry-run", action="store_true", help="프롬프트/ref만 출력, 이미지 생성 안 함.")
    ap.add_argument("--concurrency", type=int, default=0, help="동시 생성 수 (0=style.max_concurrent, flow면 레인 수로 캡).")
    ap.add_argument("--force", action="store_true", help="체크포인트(OK+PNG 존재) 씬도 재생성.")
    ap.add_argument("--allow-undirected", action="store_true",
                    help="연출·ref 점검에 걸려도 강행 (표정 없는 얼굴이 나온다)")
    ap.add_argument("--era", default=None, help="시대 덮어쓰기 (style.json eras의 키). 미지정이면 style.json era. 01~10편 재생성은 --era joseon")
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    style_path = find_channel_config(project_dir, "style.json")
    for pth, label in [(style_path, "style.json"),
                       (project_dir / "characters.json", "characters.json"),
                       (project_dir / "storyboard.json", "storyboard.json")]:
        if not pth.exists():
            print(f"error: {label} 없음: {pth}", file=sys.stderr)
            return 2

    style = resolve_era(load_json(style_path), args.era or project_era(project_dir))
    characters = load_json(project_dir / "characters.json")
    lp = project_dir / "locations.json"
    locations = load_json(lp) if lp.exists() else {}
    storyboard = load_json(project_dir / "storyboard.json")
    scenes = storyboard["scenes"] if isinstance(storyboard, dict) else storyboard

    all_scenes = scenes
    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    if only:
        scenes = [s for s in scenes if s["id"] in only]

    scenes_dir = project_dir / "scenes"
    scenes_dir.mkdir(exist_ok=True)

    # 체크포인트: 기존 built.json에서 OK이고 PNG가 실재하는 씬은 스킵 (--force로 무시)
    out_json = project_dir / "storyboard.built.json"
    prev = {}
    if out_json.exists():
        try:
            prev = {s["id"]: s for s in load_json(out_json).get("scenes", [])}
        except Exception:
            prev = {}

    def done(sid):
        rec = prev.get(sid)
        return bool(rec and rec.get("status") == "OK"
                    and (scenes_dir / f"scene_{sid:02d}.png").exists())

    if not args.force and not args.dry_run:
        skipped = [s["id"] for s in scenes if done(s["id"])]
        scenes = [s for s in scenes if not done(s["id"])]
        if skipped:
            print(f"체크포인트 스킵 {len(skipped)}씬 (OK+PNG 존재). --force로 재생성 가능.")

    # ★생성 전 게이트 (2026-09-02) — 돈이 나가기 전에 막는다.
    if scenes and preflight(scenes, characters, style, project_dir,
                            args.allow_undirected):
        return 2

    key = find_env_key(project_dir)
    env = dict(os.environ)
    if key:
        env["GEMINI_API"] = key

    conc = args.concurrency or int(style.get("max_concurrent", 5))
    # flow 엔진이면 레인(포트) 수 이상의 동시성은 "모든 레인이 사용 중" 즉시 실패만 낳는다 — 자동 캡
    try:
        img = load_json(find_channel_config(project_dir, "settings.json")).get("image", {})
        if img.get("engine", "flow") == "flow":
            flow = img.get("flow", {})
            lanes = len(flow.get("ports") or ([flow["port"]] if flow.get("port") else [])) or 1
            if conc > lanes:
                print(f"flow 레인 {lanes}개 — 동시성 {conc}→{lanes}로 자동 캡")
                conc = lanes
    except Exception:
        pass
    built = []

    def run(scene):
        sid = scene["id"]
        try:
            prompt, refs = build_scene(scene, characters, locations, style, project_dir)
        except KeyError as e:
            return {**scene, "status": f"FAIL(build): {e}"}
        pf = scenes_dir / f"_p{sid:02d}.txt"
        pf.write_text(prompt, encoding="utf-8")
        out = scenes_dir / f"scene_{sid:02d}.png"
        rec = {**scene, "image_prompt": prompt, "image": f"scenes/scene_{sid:02d}.png"}
        if args.dry_run:
            rec["status"] = f"DRY (refs={len(refs)})"
            return rec
        cmd = ["python3", str(GEN), str(pf), str(out)] + refs
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        ok = out.exists() and r.returncode == 0
        rec["status"] = "OK" if ok else "FAIL: " + (r.stderr or r.stdout)[-300:]
        return rec

    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
        for rec in ex.map(run, scenes):
            built.append(rec)
            print(f"[{rec['id']:02d}] {(rec.get('act') or ''):8} cast={rec.get('cast')} loc={rec.get('location')} refs->{rec['status']}")

    # 병합 저장: 이번에 돌린 씬은 새 결과, 안 돌린 씬은 기존 '생성 결과'만 물려받는다.
    # ★2026-09-02: 종전에는 안 돌린 씬의 기록을 id 기준으로 **통째로** 물려받았다. 그래서 씬을
    #   쪼개거나 끼워 넣어 **번호가 밀리면 옛 씬의 sentences·cast·visual_desc 가 따라와** built.json
    #   이 소스 보드와 어긋났다(11편 1편 실측 — merge_omnibus 가 "문장 누락/중복"으로 잡았다).
    #   소스 필드는 언제나 현재 storyboard.json 이 진실이고, 물려받는 것은 image/status 뿐이다.
    #   image_prompt 는 씬 내용에 딸린 것이라 물려받지 않는다(재생성 시 다시 쓰인다).
    CARRY = ("image", "status")
    new_by_id = {s["id"]: s for s in built}

    def _carry(src):
        rec = dict(src)
        old_rec = prev.get(src["id"]) or {}
        for k in CARRY:
            if k in old_rec:
                rec[k] = old_rec[k]
        return rec

    merged = [new_by_id.get(s["id"]) or _carry(s) for s in all_scenes]
    json.dump({"scenes": merged}, open(out_json, "w"), ensure_ascii=False, indent=2)
    ok = sum(1 for s in merged if s.get("status") == "OK")
    dry = sum(1 for s in merged if str(s.get("status", "")).startswith("DRY"))
    print(f"\n완료(전체 기준): OK {ok} / DRY {dry} / 전체 {len(merged)} (이번 실행 {len(built)}씬)  → {out_json.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
