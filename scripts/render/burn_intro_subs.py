#!/usr/bin/env python3
"""인트로 승인본에 자막을 구워 넣은 변형본을 만든다.

인트로 원본(`intro_final.mp4`)에는 자막이 없다 — 본편 자막은 CapCut이 입히기 때문.
이 스크립트는 **본편 자막과 똑같이 보이는** 자막을 인트로에 직접 번인해
`intro_final_sub.mp4` 를 만든다(승인본은 건드리지 않는다).

★스타일은 눈대중이 아니라 **01편 완성본 프레임에서 실측**해 맞췄다 (2026-08-17):
  - 폰트: 맑은 고딕 **Bold** — 후보 7종을 글자 마스크 IoU로 비교해 확정(0.69, 2위 HY견고딕 0.61)
  - 글자 높이 107px @1080p, 가운데 정렬, 글자 아랫변이 화면 바닥에서 57px
  - 검은 외곽선 8px (= 글자높이 × settings capcut.subtitle.border_width 0.08)
  - libass 의 Fontsize 는 글자 실제 높이가 아니다 — 실측 환산계수 1.354 를 곱한다

사용법:
    python3 scripts/render/burn_intro_subs.py --channel yadam
    python3 scripts/render/burn_intro_subs.py --channel yadam --srt <다른.srt> --out <경로>
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# 01편 완성본 프레임 실측에서 나온 환산 상수 (1920x1080 기준)
GLYPH_PX_PER_CAPCUT_FONT = 10.8   # capcut.subtitle.font_size 10.0 → 글자 높이 108px
ASS_FONTSIZE_PER_GLYPH_PX = 1.354  # libass Fontsize ÷ 글자 실제 높이
MARGIN_V = 43                      # 글자 아랫변이 바닥에서 57px 이 되는 값
FONT_NAME = "Malgun Gothic"


def parse_srt(text):
    cues = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, _, end = lines[1].partition("-->")
        cues.append((start.strip(), end.strip(), " ".join(lines[2:]).strip()))
    return cues


def srt_to_ass_time(ts):
    """00:00:01,234 → 0:00:01.23 (ASS는 1/100초)"""
    hms, _, ms = ts.partition(",")
    h, m, s = hms.split(":")
    return f"{int(h)}:{m}:{s}.{int(ms):03d}"[:-1]


def build_ass(cues, font_size, outline, ass_path):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,{FONT_NAME},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,60,60,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "".join(
        f"Dialogue: 0,{srt_to_ass_time(s)},{srt_to_ass_time(e)},Def,,0,0,0,,{t}\n"
        for s, e, t in cues
    )
    Path(ass_path).write_text(head + body, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="인트로에 자막을 구워 변형본 생성")
    ap.add_argument("--channel", default="yadam")
    ap.add_argument("--srt", help="자막 SRT (기본: assets/intro/인트로_자막.srt)")
    ap.add_argument("--intro", help="원본 인트로 mp4 (기본: intro.json 승인본)")
    ap.add_argument("--out", help="출력 경로 (기본: <원본>_sub.mp4)")
    ap.add_argument("--crf", type=int, default=16, help="번인 재인코딩 품질 (낮을수록 고화질)")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[2]
    intro_dir = repo / "channels" / args.channel / "assets" / "intro"
    # ★--intro 와 --srt 를 둘 다 주면 인트로 폴더가 없어도 된다 (2026-08-29).
    #   훅 클립에 같은 스타일 자막을 굽는 데 이 스크립트를 그대로 쓴다(burn_hook_subs.py).
    if not intro_dir.is_dir() and not (args.intro and args.srt):
        sys.exit(f"인트로 폴더 없음: {intro_dir}")

    # 원본 인트로 — 승인본을 읽기만 한다
    if args.intro:
        src = Path(args.intro)
    else:
        meta = intro_dir / "intro.json"
        approved = "intro_final.mp4"
        if meta.exists():
            approved = json.loads(meta.read_text(encoding="utf-8")).get(
                "approved_file", approved)
        src = intro_dir / approved
    if not src.exists():
        sys.exit(f"인트로 원본 없음: {src}")

    srt = Path(args.srt) if args.srt else intro_dir / "인트로_자막.srt"
    if not srt.exists():
        sys.exit(f"자막 SRT 없음: {srt}")
    cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
    if not cues:
        sys.exit(f"자막 큐를 읽지 못했습니다: {srt}")

    # 채널 자막 스타일 → 픽셀 환산
    settings = json.loads((repo / "channels" / args.channel / "config" /
                           "settings.json").read_text(encoding="utf-8"))
    cfg = settings.get("capcut", {}).get("subtitle", {})
    glyph_px = cfg.get("font_size", 10.0) * GLYPH_PX_PER_CAPCUT_FONT
    font_size = round(glyph_px * ASS_FONTSIZE_PER_GLYPH_PX)
    outline = max(1, round(glyph_px * cfg.get("border_width", 0.08)))

    out = Path(args.out) if args.out else src.with_name(f"{src.stem}_sub.mp4")
    print(f"원본: {src.name}")
    print(f"자막: {srt.name} — {len(cues)}큐")
    print(f"스타일: {FONT_NAME} Bold / 글자 {glyph_px:.0f}px (ASS {font_size}) / 외곽선 {outline}px")

    with tempfile.TemporaryDirectory(prefix="intro_subs_") as tmp:
        ass_path = Path(tmp) / "subs.ass"
        build_ass(cues, font_size, outline, ass_path)
        # ffmpeg 필터 인자에서 경로 구분자·콜론을 이스케이프
        f = str(ass_path).replace("\\", "/").replace(":", r"\:")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vf", f"ass='{f}'",
             "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
             "-pix_fmt", "yuv420p", "-profile:v", "high",
             "-c:a", "copy", str(out)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"완료: {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
