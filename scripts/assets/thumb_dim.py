#!/usr/bin/env python3
"""썸네일 배경 감광 — 인물은 살리고 배경만 어둡게 (재생성 없음).

썸네일 문구를 얹을 때 배경(하늘·담장·창호)이 너무 밝아 글자가 묻히는 문제를
이미 뽑은 PNG를 후처리해서 해결한다. 알파 매트(세그멘테이션) 없이,
인물이 있는 영역을 소프트 마스크로 보호하고 나머지를 감마 보정 곱연산으로 낮춘다.

마스크 모양 2종:
  band    (기본) 인물 중심 x에서 좌우로 떨어지는 세로 띠.
          인물이 화면 높이를 거의 다 쓰는 구도(머리 위~치마 아래)에 안전하다.
  ellipse 인물 중심의 타원 스포트라이트. 인물이 한쪽 아래에 몰린 구도용.

인물 위치는 에지 밀도(머리카락·이목구비·자수)로 자동 추정하고, 빗나가면
--center / --half-width 로 직접 지정한다(추정값은 항상 stdout에 찍힌다).

Usage:
    # 폴더 전체, 기본값
    python3 scripts/assets/thumb_dim.py --dir channels/yadam/projects/_thumbtest --out-dir .../dim

    # 한 장, 세기 조절
    python3 scripts/assets/thumb_dim.py --image a.png --strength 0.55 --top-fade 0.35

    # 인물 위치 직접 지정 (화면 폭 기준 0~1)
    python3 scripts/assets/thumb_dim.py --image a.png --center 0.72 --half-width 0.20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

EXTS = {".png", ".jpg", ".jpeg", ".webp"}
GAMMA = 2.2


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def detect_subject(img: Image.Image) -> tuple[float, float, float, float]:
    """에지 밀도로 인물 위치를 추정 → (cx, cy, sx, sy) 모두 0~1 비율."""
    small = img.convert("L").resize((320, max(1, round(320 * img.height / img.width))),
                                    Image.LANCZOS)
    a = np.asarray(small, dtype=np.float32) / 255.0
    gx = np.abs(np.diff(a, axis=1, prepend=a[:, :1]))
    gy = np.abs(np.diff(a, axis=0, prepend=a[:1, :]))
    edge = Image.fromarray(np.clip((gx + gy) * 255.0, 0, 255).astype(np.uint8))
    e = np.asarray(edge.filter(ImageFilter.GaussianBlur(7)), dtype=np.float32)

    # 상위 15%만 남겨 "가장 촘촘한 덩어리"(=인물)로 무게중심을 몬다.
    lo, hi = np.percentile(e, 85), np.percentile(e, 99)
    w = np.clip((e - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    if w.sum() < 1e-6:
        return 0.5, 0.5, 0.25, 0.25

    h, wd = w.shape
    ys, xs = np.mgrid[0:h, 0:wd]
    tot = w.sum()
    cx = float((w * xs).sum() / tot) / wd
    cy = float((w * ys).sum() / tot) / h
    sx = float(np.sqrt((w * (xs / wd - cx) ** 2).sum() / tot))
    sy = float(np.sqrt((w * (ys / h - cy) ** 2).sum() / tot))
    return cx, cy, sx, sy


def build_keep_mask(size: tuple[int, int], shape: str, cx: float, cy: float,
                    half_w: float, half_h: float, feather: float) -> np.ndarray:
    """인물 보호 마스크. 1=원본 유지, 0=최대 감광."""
    w, h = size
    xs = (np.arange(w, dtype=np.float32) / w)[None, :]
    ys = (np.arange(h, dtype=np.float32) / h)[:, None]

    if shape == "band":
        d = np.abs(xs - cx) / max(half_w, 1e-6)
        d = np.broadcast_to(d, (h, w))
    else:  # ellipse
        d = np.sqrt(((xs - cx) / max(half_w, 1e-6)) ** 2
                    + ((ys - cy) / max(half_h, 1e-6)) ** 2)
    return 1.0 - _smoothstep(1.0, 1.0 + max(feather, 1e-3), d)


def dim_image(path: Path, out_path: Path, *, shape: str, strength: float,
              center: float | None, cy_override: float | None, half_w: float | None,
              half_h: float, feather: float, top_fade: float, desat: float,
              hw_scale: float = 1.0, keep_highlights: float = 0.0) -> dict:
    img = Image.open(path).convert("RGB")
    cx, cy, sx, sy = detect_subject(img)
    detected = (cx, cy, sx, sy)

    if center is not None:
        cx = center
    if cy_override is not None:
        cy = cy_override
    if half_w is None:
        # 에지 분산 × 2 를 반폭으로. 너무 좁거나(인물 잘림) 넓지(감광 무의미) 않게 클램프.
        half_w = float(np.clip(sx * 2.0, 0.16, 0.34))
    half_w *= hw_scale  # 보호 폭을 줄이면 그만큼 어두운 영역이 넓어진다
    half_h *= hw_scale

    keep = build_keep_mask(img.size, shape, cx, cy, half_w, half_h, feather)

    # 위쪽 문구 영역은 한 번 더 눌러 준다(인물 보호 마스크는 그대로 적용).
    if top_fade > 0:
        ys = (np.arange(img.height, dtype=np.float32) / img.height)[:, None]
        top = (1.0 - _smoothstep(0.0, 0.55, ys)) * top_fade
        extra = np.broadcast_to(top, keep.shape) * (1.0 - keep)
    else:
        extra = np.zeros_like(keep)

    a = np.asarray(img, dtype=np.float32) / 255.0
    lin = np.power(a, GAMMA)

    mult = np.clip(1.0 - strength * (1.0 - keep) - extra, 0.02, 1.0)

    if keep_highlights > 0:
        # 등잔 불꽃·달 같은 극단 하이라이트는 덜 누른다 (배경이 죽어도 광원은 살아 있어야 함)
        luma = (a * np.array([0.299, 0.587, 0.114], dtype=np.float32)).sum(axis=2)
        hl = _smoothstep(0.78, 0.96, luma) * keep_highlights
        mult = mult + (1.0 - mult) * hl

    lin *= mult[:, :, None]

    out = np.power(np.clip(lin, 0.0, 1.0), 1.0 / GAMMA)

    if desat > 0:  # 어두워진 곳의 채도를 살짝 빼야 탁해 보이지 않는다
        gray = (out * np.array([0.299, 0.587, 0.114], dtype=np.float32)).sum(axis=2, keepdims=True)
        f = (desat * (1.0 - keep))[:, :, None]
        out = out * (1.0 - f) + gray * f

    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(out * 255.0, 0, 255).astype(np.uint8)).save(out_path)
    return {"file": path.name, "detected": detected, "cx": cx, "cy": cy, "half_w": half_w}


def main() -> int:
    p = argparse.ArgumentParser(description="썸네일 배경 감광 (인물 보호)")
    p.add_argument("--image", help="단일 이미지")
    p.add_argument("--dir", help="이미지 폴더 (_ 로 시작하는 파일 제외)")
    p.add_argument("--out", help="--image 일 때 출력 경로")
    p.add_argument("--out-dir", help="--dir 일 때 출력 폴더. 기본 {dir}/dim")
    p.add_argument("--shape", choices=["band", "ellipse"], default="band")
    p.add_argument("--strength", type=float, default=0.45, help="배경 감광 세기 0~1")
    p.add_argument("--center", type=float, help="인물 중심 x (0~1). 미지정 시 자동")
    p.add_argument("--center-y", type=float, help="인물 중심 y (0~1, ellipse 전용)")
    p.add_argument("--half-width", type=float, help="보호 반폭 (0~1). 미지정 시 자동")
    p.add_argument("--half-height", type=float, default=0.42, help="보호 반높이 (ellipse 전용)")
    p.add_argument("--hw-scale", type=float, default=1.0,
                   help="보호 폭 배율. <1 이면 어두운 영역이 넓어진다 (자동/수동 반폭 공통 적용)")
    p.add_argument("--feather", type=float, default=0.55, help="경계 흐림 폭 (반폭 배수)")
    p.add_argument("--top-fade", type=float, default=0.0, help="상단 문구 영역 추가 감광 0~1")
    p.add_argument("--desat", type=float, default=0.15, help="배경 채도 감소 0~1")
    p.add_argument("--keep-highlights", type=float, default=0.6,
                   help="등잔·달 등 극단 하이라이트 보존 0~1 (0=끔)")
    args = p.parse_args()

    if args.image:
        src = Path(args.image)
        targets = [(src, Path(args.out) if args.out else src.with_name(src.stem + "_dim.png"))]
    elif args.dir:
        d = Path(args.dir)
        out_dir = Path(args.out_dir) if args.out_dir else d / "dim"
        targets = [(f, out_dir / f"{f.stem}_dim.png")
                   for f in sorted(d.iterdir())
                   if f.suffix.lower() in EXTS and not f.name.startswith("_")]
    else:
        p.error("--image 또는 --dir 필요")

    if not targets:
        print("[ERROR] 대상 이미지 없음", file=sys.stderr)
        return 1

    for src, dst in targets:
        info = dim_image(src, dst, shape=args.shape, strength=args.strength,
                         center=args.center, cy_override=args.center_y,
                         half_w=args.half_width, half_h=args.half_height,
                         feather=args.feather, top_fade=args.top_fade, desat=args.desat,
                         hw_scale=args.hw_scale, keep_highlights=args.keep_highlights)
        print(f"[OK] {info['file']:<34} cx={info['cx']:.2f} cy={info['cy']:.2f} "
              f"halfW={info['half_w']:.2f} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
