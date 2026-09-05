#!/usr/bin/env python3
"""
씬 이미지 자동 검수 — 흰 액자 프레임 + 레터박스/필러박스 띠 검출.

배치 뒤 눈으로 100장 넘게 훑는 것보다 확실하다. 해상도는 정상으로 나오기 때문에
파일 크기·개수 점검으로는 절대 안 걸리는 종류의 사고다.

검사 항목
  1) 흰 액자 프레임 — 그림이 흰 여백 안에 액자처럼 들어앉은 경우.
     네 변 6px 평균 밝기의 최소값이 임계(기본 235)를 넘으면 의심.
     (실측: 혼구석 96씬 중 2장, 산가지 127씬 중 1장)
  2) 레터박스/필러박스 — 위아래(또는 좌우)에 균일한 검은/흰 띠가 깔린 경우.
     style.json scene_negative에 full-bleed 금지구를 두 번 넣어도 계속 나온다
     (산가지 실측 127씬 중 17장 = 13%). 텍스트로 못 막으니 검출해서 재생성한다.

     ★오탐 억제: 의도적으로 어두운 컷(암전 배경 클로즈업 등)이 걸리지 않도록
     ① 거의 완전 균일(std < 2) ② 극단 밝기(<15 or >240) ③ 띠가 끝나는 자리에서
     평균이 급격히 점프(>25) — 셋을 모두 만족할 때만 띠로 판정한다.

Usage:
    python3 scripts/render/check_scenes.py {P}
    python3 scripts/render/check_scenes.py {P} --dir assets/characters   # 다른 폴더 검사
    python3 scripts/render/check_scenes.py {P} --json                    # 기계 판독용

종료 코드: 0 = 이상 없음 / 1 = 재생성 대상 있음
출력 마지막 줄에 build.py에 그대로 넣을 수 있는 --only 목록을 찍는다.
"""

import argparse
import json
import pathlib
import re
import shutil
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError:
    print("numpy / Pillow 필요: pip install numpy pillow", file=sys.stderr)
    raise SystemExit(2)

WHITE_EDGE = 235.0   # 네 변 평균이 이 값을 넘으면 흰 액자
FLAT_STD = 2.0       # 띠로 인정할 행 내부 표준편차 상한
DARK, LIGHT = 15.0, 240.0
MIN_BAND = 8         # 이 줄 수 이상 이어져야 띠
JUMP = 25.0          # 띠 경계에서 요구하는 평균 점프


def _band(a):
    """배열 위쪽 가장자리부터 균일한 극단 밝기 줄이 몇 줄 이어지는지 (아니면 0)."""
    n = 0
    for i in range(a.shape[0] // 3):
        line = a[i, :]
        if line.std() < FLAT_STD and (line.mean() < DARK or line.mean() > LIGHT):
            n += 1
        else:
            break
    if n < MIN_BAND:
        return 0
    inner = a[n:n + 6, :].mean()
    return n if abs(inner - a[:n, :].mean()) > JUMP else 0


# ── 이중 패널(턴어라운드 시트 모방) 검출 ─────────────────────────────────
# 씬 ref가 4분할 턴어라운드 시트라, 모델이 씬까지 패널로 쪼개 그리는 사고가 난다.
# (2026-08-16 실측: 1편 16씬 중 scene_04/10 — 같은 인물을 정면/3-4로 좌우에 나란히)
# 해상도·파일크기는 정상이라 이 검사 말고는 기계로 걸릴 방법이 없다.
#
# ★먼저 시도해 본 '중앙 이음매(열간 변화량)' 방식은 실패했다 — 정상 씬 4.6배 >
#   합성 이중 패널 3.5배로, 신호가 이음매가 아니라 그림 질감에 지배당한다.
#   그래서 좌우 반쪽의 정규화 상관으로 바꿨다.
#
# ⚠️ 한계: 완전 복제·거울 복제는 확실히 잡지만, 같은 인물을 **다른 각도로** 그린
#   이중 패널은 놓칠 수 있다. contact sheet 눈 검수를 반드시 병행할 것.
DUP_CORR = 0.88     # 정상 16씬 실측 최대 0.793 / 합성 복제 1.000


def _norm(x):
    x = np.asarray(x, dtype=float)
    return (x - x.mean()) / (x.std() + 1e-6)


def check_duplicate_half(a):
    """좌우 반쪽이 같은 그림인지 → (의심 여부, 상관계수)."""
    h, w = a.shape
    if w < 64:
        return False, 0.0
    m = w // 2
    L = _norm(Image.fromarray(a[:, :m].astype(np.uint8)).resize((48, 48)))
    R = _norm(Image.fromarray(a[:, w - m:].astype(np.uint8)).resize((48, 48)))
    direct = float((L * R).mean())
    mirror = float((L * R[:, ::-1]).mean())
    best = max(direct, mirror)
    return best >= DUP_CORR, best


# ── 테두리 안쪽 흰 밴드 검출 ─────────────────────────────────────────────
# 위 _band()/framed 판정은 **가장자리 픽셀 밝기**를 본다. 그래서 그림에 테두리 선이
# 그려져 있으면 가장자리가 어두워 통과해 버리는데, 정작 그 선 **안쪽**에는 흰 여백이
# 남아 있는 경우가 있다.
# (2026-08-17 실측: 이 유형 8장이 검수를 통과해 CapCut까지 갔고 사용자가 발견했다 —
#  3편 씬1·17·19, 6편 씬2·3·16·17·19)
INNER_SKIP = 6        # 테두리 선으로 볼 바깥 두께
INNER_LIGHT = 243     # 이보다 밝고
INNER_FLAT = 8        # 이보다 평탄하면 여백으로 본다
INNER_MIN = 10        # 이 두께 이상일 때만 보고


INNER_JUMP = 18.0     # 여백이 끝나는 자리에서 요구하는 밝기 낙차
INNER_LEAD = 24       # 테두리 선으로 봐줄 최대 두께 (이 안에서 여백이 시작해야 한다)


def _inner_band(a):
    """테두리 선 안쪽에 남은 흰 여백 두께. 없으면 0.

    ★2026-08-18 수정 — 종전 판정은 '밝고 평탄한 행'이면 무조건 여백으로 셌다. 그래서
      눈밭·흰 벽·환한 하늘이 여백으로 오인돼 **멀쩡한 그림이 잘려 나갔다**(5편 19씬 눈보라 컷에서
      위쪽 126px이 잘렸다). 두 가지를 건다:
        ① 여백은 테두리 선 바로 안쪽(INNER_LEAD 이내)에서 시작해야 한다 — 그림 한복판의
           밝은 띠를 가장자리 여백으로 착각하지 않는다.
        ② 여백이 끝나는 자리에서 밝기가 INNER_JUMP 이상 뚝 떨어져야 한다 — 하늘처럼
           서서히 어두워지는 것은 여백이 아니다.
    """
    edge = INNER_SKIP * 2
    h, w = a.shape

    def row(i):
        return a[i][edge:-edge] if w > edge * 3 else a[i]

    start = None
    n = 0
    for i in range(INNER_SKIP, h // 3):
        r = row(i)
        if r.mean() > INNER_LIGHT and r.std() < INNER_FLAT:
            if start is None:
                start = i
            n = i + 1
        elif n:
            break
        elif i - INNER_SKIP > INNER_LEAD:
            return 0                      # ①테두리 선 안쪽에서 시작하지 않았다
    if start is None or n - start < INNER_MIN:
        return 0
    nxt = a[n:n + 8, edge:-edge] if w > edge * 3 else a[n:n + 8]
    if nxt.size == 0:
        return 0
    return n if (a[start:n, edge:-edge].mean() - nxt.mean()) > INNER_JUMP else 0


def check_inner_bands(a):
    """테두리 선 안쪽에 남은 흰 밴드 두께 {상,하,좌,우}."""
    return {
        "top": _inner_band(a),
        "bottom": _inner_band(a[::-1, :]),
        "left": _inner_band(a.T),
        "right": _inner_band(a.T[::-1, :]),
    }


def check_image(path):
    """(흰액자, 띠 두께, (이중패널, 상관계수), 안쪽 흰 밴드) 반환."""
    return check_image_array(np.asarray(Image.open(path).convert("L"), dtype=float))


def check_image_array(a):
    """그레이스케일 배열 판정 — 파일을 거치지 않고 메모리에서 재검사할 때 쓴다."""
    edges = [a[:6, :].mean(), a[-6:, :].mean(), a[:, :6].mean(), a[:, -6:].mean()]
    framed = min(edges) > WHITE_EDGE
    bands = {
        "top": _band(a),
        "bottom": _band(a[::-1, :]),
        "left": _band(a.T),
        "right": _band(a.T[::-1, :]),
    }
    dup, dup_r = check_duplicate_half(a)
    inner = {k: v for k, v in check_inner_bands(a).items() if v >= INNER_MIN}
    return framed, bands, (dup, dup_r), inner


def content_bbox(path, thresh=238):
    """그림이 흰 여백 안에 액자처럼 들어앉은 경우, 실제 그림 영역의 bbox.

    ★띠(band) 로직은 '한 변에서 안쪽으로 균일한 밴드'를 찾기 때문에
      사방이 동시에 흰 액자인 경우를 잡지 못한다(2026-08-16 실측: scene_04가
      3라운드를 버티고도 --fix에서 0개 처리됨). 그래서 액자 전용 경로를 둔다.
    """
    a = np.asarray(Image.open(path).convert("L"), dtype=float)
    ink = a < thresh                       # 그림이 있는 화소
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    t, b = int(rows[0]), int(rows[-1]) + 1
    l, r = int(cols[0]), int(cols[-1]) + 1
    h, w = a.shape
    if (b - t) >= h - 2 and (r - l) >= w - 2:
        return None                        # 여백 없음
    return l, t, r, b


MAX_CROP = 0.30       # 한 축에서 잘라낼 수 있는 최대 비율 (넘으면 자르지 않고 재생성 대상)
INSET = 3             # 띠 경계에 남는 잔재까지 확실히 떨어뜨린다


def plan_crop(path):
    """네 변에서 잘라낼 픽셀 수를 **한 번에** 계산한다 → dict 또는 (None, 사유).

    ★2026-08-18 재작성 (사용자 지적: "테두리 4개 제거할 때와 위아래 제거할 때 등 종류가 여러 개")
      종전에는 흰 액자(content_bbox) · 바깥 띠(_band) · 안쪽 여백(_inner_band)을 **서로 다른 경로**로
      처리했고, fix_letterbox.py라는 별도 도구까지 있어 같은 그림이 도구에 따라 다르게 잘렸다.
      게다가 crop_bands는 '자르고 → 16:9로 되맞추고 → 다시 검사'를 최대 4번 돌아서, 검출이 한 번만
      헛나가도 **자를수록 확대되며 그림이 사라졌다.**
      이제 세 검출을 한 상자로 합치고, 자르기·비율맞춤·리사이즈를 **각각 한 번씩만** 한다.
    """
    src = Image.open(path).convert("RGB")
    W, H = src.size
    a = np.asarray(src.convert("L"), dtype=float)
    framed, bands, _dup, inner = check_image_array(a)

    cut = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    for k, v in bands.items():
        if v:
            cut[k] = max(cut[k], v + INSET)
    for k, v in inner.items():
        cut[k] = max(cut[k], v + INSET)
    if framed:
        box = content_bbox(path, thresh=245)
        if box:
            l, t_, r, b = box
            cut["left"] = max(cut["left"], l + INSET)
            cut["top"] = max(cut["top"], t_ + INSET)
            cut["right"] = max(cut["right"], W - r + INSET)
            cut["bottom"] = max(cut["bottom"], H - b + INSET)

    if not any(cut.values()):
        return None, "잘라낼 띠가 없다"
    if cut["left"] + cut["right"] > W * MAX_CROP:
        return None, f"좌우 크롭 과다({cut['left']}+{cut['right']}px) — 재생성 대상"
    if cut["top"] + cut["bottom"] > H * MAX_CROP:
        return None, f"상하 크롭 과다({cut['top']}+{cut['bottom']}px) — 재생성 대상"
    return cut, src


def fix_image(path, backup=True):
    """액자·띠·안쪽 여백을 한 상자로 잘라내고 원래 캔버스 비율·크기로 되맞춘다.

    반환: (새 크기, None) 성공 / (None, 사유) 건너뜀.
    """
    plan, info = plan_crop(path)
    if plan is None:
        return None, info
    src = info
    W, H = src.size
    img = src.crop((plan["left"], plan["top"], W - plan["right"], H - plan["bottom"]))

    # 비율 맞춤 — 한 번만. 남은 영역이 목표보다 넓으면 폭을, 높으면 높이를 가운데 기준으로 줄인다.
    target = W / H
    cw, ch = img.size
    if cw / ch > target:
        nw = int(round(ch * target))
        off = (cw - nw) // 2
        img = img.crop((off, 0, off + nw, ch))
    else:
        nh = int(round(cw / target))
        off = (ch - nh) // 2
        img = img.crop((0, off, cw, off + nh))

    if backup:
        bak_dir = path.parent / "_orig"
        bak_dir.mkdir(exist_ok=True)
        bak = bak_dir / path.name
        if not bak.exists():
            shutil.copy2(path, bak)
    out = img.resize((W, H), Image.LANCZOS)
    out.save(path)

    # ★한 번만 자르고, 남으면 다시 자르지 않고 보고한다 (반복 크롭이 그림을 먹는 원인이었다)
    framed, bands, _dup, inner = check_image_array(np.asarray(out.convert("L"), dtype=float))
    left = framed or any(bands.values()) or bool(inner)
    return out.size, ("한 번 잘랐는데도 남았다 — 재생성 권장" if left else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--dir", default="scenes", help="검사할 하위 폴더 (기본 scenes)")
    ap.add_argument("--glob", default="*.png")
    ap.add_argument("--json", action="store_true", help="JSON으로 출력")
    ap.add_argument("--fix", action="store_true",
                    help="레터박스 띠를 잘라내고 16:9로 되맞춘다(원본은 .bak 보관). "
                         "재생성으로 안 잡히는 씬에 쓴다. 흰 액자는 그림 자체가 액자라 "
                         "크롭으로 못 고치므로 재생성 대상으로 남긴다.")
    args = ap.parse_args()

    target = args.project_dir / args.dir
    files = sorted(p for p in target.glob(args.glob) if not p.name.startswith("_"))
    # ★--fix 가 남기는 .bak 백업이 다시 검사 대상으로 잡히면 안 된다
    files = [f for f in files if ".bak" not in f.name]
    files = [f for f in files if ".bak" not in f.name]
    if not files:
        print(f"검사할 이미지가 없습니다: {target}", file=sys.stderr)
        return 2

    bad = []
    for f in files:
        framed, bands, seam, inner = check_image(f)
        worst = max(bands.values())
        if framed or worst or seam[0] or inner:
            m = re.search(r"(\d+)", f.stem)
            bad.append({
                "file": f.name,
                "id": int(m.group(1)) if m else None,
                "white_frame": bool(framed),
                "bands": {k: v for k, v in bands.items() if v},
                "duplicate_half": round(seam[1], 3) if seam[0] else None,
                "inner_bands": inner,
            })

    if args.json:
        print(json.dumps({"checked": len(files), "bad": bad}, ensure_ascii=False, indent=2))
        return 1 if bad else 0

    print(f"검사한 이미지: {len(files)}개")
    if not bad:
        print("✓ 흰 액자 없음 · 레터박스 없음 · 이중 패널 없음 · 안쪽 여백 없음")
        return 0

    print(f"\n✗ 이상 {len(bad)}개:")
    for b in bad:
        tags = []
        if b["white_frame"]:
            tags.append("흰 액자")
        for k, v in b["bands"].items():
            tags.append(f"{k} {v}px 띠")
        for k, v in b.get("inner_bands", {}).items():
            tags.append(f"테두리 안쪽 {k} {v}px 여백")
        if b.get("duplicate_half"):
            tags.append(f"★이중 패널 의심 (좌우 상관 {b['duplicate_half']})")
        print(f"  {b['file']:<20} {' · '.join(tags)}")

    if args.fix:
        fixed, left, skipped = [], [], []
        for b in bad:
            if b.get("duplicate_half") and not (b["white_frame"] or b["bands"] or b.get("inner_bands")):
                left.append(b)                      # 이중 패널은 크롭으로 못 고친다
                continue
            size, why = fix_image(target / b["file"])
            if size is None:
                skipped.append((b["file"], why))
                left.append(b)
            else:
                fixed.append((b["file"], why))
                if why:
                    left.append(b)
        print(f"\n✓ 한 번에 잘라내고 16:9로 되맞춤: {len(fixed)}개 (원본은 _orig/)")
        for f, why in fixed:
            print(f"  {f}" + (f"   ⚠ {why}" if why else ""))
        if skipped:
            print(f"\n건너뜀 {len(skipped)}개 — 자르면 그림이 상한다:")
            for f, why in skipped:
                print(f"  {f}   {why}")
        bad = left
        if not bad:
            return 0

    ids = [str(b["id"]) for b in bad if b["id"] is not None]
    if ids:
        print("\n재생성:")
        print(f"  python3 scripts/storyboard/build.py {args.project_dir} "
              f"--only {','.join(ids)} --force")
        if not args.fix:
            print("  (재생성으로 안 잡히면 --fix 로 띠를 잘라낼 수 있습니다)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
