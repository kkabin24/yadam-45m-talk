#!/usr/bin/env python3
"""레터박스/흰 액자 제거 — ★check_scenes.py --fix 로 통합됐다 (2026-08-18).

왜 합쳤나 (사용자 지적):
    이 파일과 check_scenes.py가 **같은 그림을 서로 다른 규칙으로** 잘랐다.
    - 이 파일: 행 평균만 보고 띠를 세고, 잘라낸 뒤 16:9로 되맞춤
    - check_scenes: 흰 액자 / 바깥 띠 / 테두리 안쪽 여백을 각각 다른 경로로 처리하고 최대 4회 반복
    그래서 "테두리 4개를 지울 때"와 "위아래만 지울 때"의 결과가 제각각이었고,
    반복 크롭이 돌면서 멀쩡한 그림까지 확대·손실됐다.
    이제 판정과 크롭은 check_scenes.plan_crop / fix_image 한 곳에만 있다.

Usage:
    python3 scripts/render/check_scenes.py {P}          # 검사
    python3 scripts/render/check_scenes.py {P} --fix    # 한 번만 잘라내고 16:9 복원
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_scenes import fix_image, check_image  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    apply_ = "--apply" in sys.argv
    if not args:
        print(__doc__)
        return 2
    d = pathlib.Path(args[0])
    files = sorted(p for p in d.glob("*.png") if not p.name.startswith("_"))
    hits = 0
    for p in files:
        framed, bands, _dup, inner = check_image(p)
        if not (framed or any(bands.values()) or inner):
            continue
        hits += 1
        tag = []
        if framed:
            tag.append("흰 액자")
        tag += [f"{k} {v}px 띠" for k, v in bands.items() if v]
        tag += [f"안쪽 {k} {v}px" for k, v in inner.items()]
        print(f"  {p.name:18} {' · '.join(tag)}", end="")
        if apply_:
            size, why = fix_image(p)
            print("  → 건너뜀: " + why if size is None else "  → 크롭 완료 (원본 _orig/)")
        else:
            print("  (검사만)")
    print(f"\n이상: {hits} / 전체 {len(files)}"
          + ("" if apply_ else "  — check_scenes.py --fix 권장"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
