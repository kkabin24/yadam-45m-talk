#!/usr/bin/env python3
"""완성본 mp4 **뒤에** 수면 노이즈 아웃트로를 붙인다 (ffmpeg concat).

왜 (2026-08-21 사용자 지시)
    수면 채널은 이야기가 끝난 뒤에도 소리가 이어지는 편이 좋다. 본편 뒤에
    조용한 노이즈를 몇 시간 이어 붙여 "틀어 놓고 잔다"를 끝까지 받쳐 준다.
    아웃트로 원본은 **오디오(mp3)뿐**이므로, 여기서 화면을 얹어 본편과 같은
    스펙의 영상으로 굽고 이어 붙인다.

    ★화면은 **완전 검정**이 기본이다. 잠들려고 트는 자리라 그림을 깔면 빛이
    남는다. 어두운 밤 그림도 마찬가지다 — 방을 밝힌다. 그림을 굳이 깔아야
    하면 --image 로 지정한다.

    붙이는 순서는 인트로와 반대다:
        고정 인트로 → 본편 → 아웃트로
    인트로는 attach_intro.py가 먼저 붙인다. 이 스크립트는 그 결과물(_intro.mp4)을
    받아도 되고 본편을 받아도 된다.

원칙 (attach_intro.py와 동일)
    · **본편은 재인코딩하지 않는다** (스트림 복사). 10GB짜리를 다시 굽지 않는다.
    · 아웃트로만 본편 파라미터에 맞춰 굽는다 — 코덱·해상도·fps·pix_fmt·오디오
      샘플레이트·채널 수, 그리고 **타임스케일**까지. 타임스케일이 어긋나면
      concat -c copy 가 본편 타임스탬프를 뭉갠다(attach_intro 주석 참조).
    · concat demuxer 가 실패하면 MPEG-TS 경유로 자동 재시도한다.

챕터 시각
    아웃트로는 본편 **뒤**라서 앞선 챕터 시각을 밀지 않는다. 그래서 meta.txt 의
    챕터 시각은 손대지 않는다. 다만 아웃트로 시작 시각을 마커에 남긴다 —
    설명문에 "그 뒤로는 조용한 빗소리"처럼 적을 때 쓴다.

사용법
    python3 scripts/render/attach_outro.py <완성본.mp4> \
        --outro channels/yadam/assets/outro/sleep_noise_3h_quiet.mp3
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attach_intro import concat, probe, run  # noqa: E402  (같은 폴더의 검증된 구현 재사용)

MARKER_NAME = "outro_attached.json"


SEG_SEC = 60          # 반복 단위 조각 길이


def build_outro(audio, image, main_info, workdir, fade=3.0):
    """정지 이미지 + 오디오 → 본편 스펙의 mp4.

    ★한 프레임을 324,000번 굽지 않는다 (2026-08-21 실측). 처음엔 `-loop 1`로 3시간을
      통째로 인코딩했는데 **35분을 돌고도 절반**이었다. 정지 화면인데도 느린 이유는
      매 프레임 PNG를 lanczos로 리스케일하고 x264가 프레임마다 판단을 하기 때문이다.
      화면이 **한 장뿐**이라는 사실을 쓰면 세 단계로 끝난다:
        ① 이미지를 **한 번만** 목표 해상도로 리스케일한다.
        ② 60초짜리 조각을 **한 번만** 굽는다.
        ③ 그 조각을 concat 데먹서로 **스트림 복사** 반복해 길이를 채운다(재인코딩 0).
      오디오만 새로 굽는데, 3시간 aac 인코딩은 몇 분이면 끝난다.

    fade: 아웃트로 **시작**에 넣을 페이드인(초). 본편 마지막 장면에서 노이즈로
          넘어갈 때 소리가 뚝 붙는 느낌을 없앤다. 0이면 끈다.
    """
    mv, ma = main_info["video"], main_info["audio"]
    if not mv:
        sys.exit("본편에서 영상 스트림을 찾지 못했습니다.")
    work = Path(workdir)
    timescale = mv.get("timescale", 15360)
    sr = (ma or {}).get("sample_rate", 44100)
    ch = (ma or {}).get("channels", 2)
    dur = probe(audio)["duration"]
    fps = mv["fps"] or 30.0

    # ① 화면 소스 — 기본은 **완전 검정**. 잠들려고 트는 자리라 빛이 없어야 한다
    #    (2026-08-21 사용자 지시). --image 를 준 경우에만 그 그림을 1회 리스케일해 쓴다.
    still = None
    if image:
        still = work / "still.png"
        run(["ffmpeg", "-y", "-i", str(image),
             "-vf", f"scale={mv['width']}:{mv['height']}:force_original_aspect_ratio=increase:"
                    f"flags=lanczos,crop={mv['width']}:{mv['height']}",
             str(still)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ② 60초 조각 1회 (영상만)
    what = "정지 화면" if still else "검정 화면"
    print(f"아웃트로 {dur / 3600:.2f}시간 ({what}) — {SEG_SEC}초 조각 1개를 굽고 "
          f"스트림 복사로 반복합니다")
    seg = work / "seg.mp4"
    src = (["-loop", "1", "-framerate", f"{fps:.6f}", "-i", str(still)] if still else
           ["-f", "lavfi", "-i",
            f"color=c=black:s={mv['width']}x{mv['height']}:r={fps:.6f}"])
    run(["ffmpeg", "-y"] + src + ["-t", str(SEG_SEC),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-tune", "stillimage",
         "-g", str(int(fps * 10)), "-pix_fmt", mv["pix_fmt"], "-profile:v", "high",
         "-video_track_timescale", str(timescale), "-an", str(seg)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ③ 스트림 복사 반복 → 오디오보다 살짝 길게 채운 뒤 아래 mux에서 정확히 자른다
    n = int(dur // SEG_SEC) + 2
    lst = work / "loop.txt"
    lst.write_text("".join(f"file '{seg.name}'\n" for _ in range(n)), encoding="utf-8")
    loop = work / "loop.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-video_track_timescale", str(timescale), str(loop)],
        cwd=str(work), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ④ 오디오만 굽고 합친다 (영상은 복사)
    dst = work / "outro.mp4"
    af = f"afade=t=in:st=0:d={fade}" if fade > 0 else "anull"
    run(["ffmpeg", "-y", "-i", str(loop), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-t", f"{dur:.3f}",
         "-af", af,
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(sr), "-ac", str(ch),
         "-video_track_timescale", str(timescale),
         "-movflags", "+faststart", str(dst)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for f in (still, seg, loop, lst):
        if f is not None:
            f.unlink(missing_ok=True)
    return dst, probe(dst)


def hms(t):
    t = int(round(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description="완성본 mp4 뒤에 수면 노이즈 아웃트로 붙이기")
    ap.add_argument("target", help="완성본 mp4 (인트로가 이미 붙은 _intro.mp4 여도 된다)")
    ap.add_argument("--outro", required=True, help="아웃트로 오디오 (mp3/wav) 또는 mp4")
    ap.add_argument("--image", help="아웃트로 화면으로 쓸 그림 (기본: 완전 검정 — 수면용)")
    ap.add_argument("--fade", type=float, default=3.0, help="아웃트로 시작 페이드인 초 (기본 3, 0이면 끔)")
    ap.add_argument("--out", help="결과 경로 (기본: <원본>_outro.mp4)")
    ap.add_argument("--replace", action="store_true", help="결과로 원본을 교체")
    ap.add_argument("--force", action="store_true", help="이미 붙어 있어도 다시 실행")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.is_file():
        sys.exit(f"완성본 mp4를 찾을 수 없습니다: {target}")
    outro_src = Path(args.outro)
    if not outro_src.is_file():
        sys.exit(f"아웃트로 파일 없음: {outro_src}")

    marker = target.parent / MARKER_NAME
    if marker.exists() and not args.force:
        sys.exit(f"이미 아웃트로가 붙어 있습니다 ({marker.name}). 다시 붙이려면 --force")

    main_info = probe(target)
    main_dur = main_info["duration"]
    print(f"본편 {hms(main_dur)} · {main_info['video']['width']}x{main_info['video']['height']} "
          f"@{main_info['video']['fps']:.0f}fps")

    out = Path(args.out) if args.out else target.with_name(target.stem + "_outro.mp4")

    with tempfile.TemporaryDirectory(dir=str(target.parent)) as work:
        if outro_src.suffix.lower() == ".mp4":
            outro, outro_info = outro_src, probe(outro_src)
        else:
            img = None
            if args.image:
                img = Path(args.image)
                if not img.is_file():
                    sys.exit(f"정지 화면 없음: {img}")
            outro, outro_info = build_outro(outro_src, img, main_info, work, args.fade)

        print(f"아웃트로 {hms(outro_info['duration'])} → 결합 (본편은 스트림 복사)")
        concat(target, outro, out, work, main_info["video"].get("timescale", 15360))

    final = probe(out)
    expect = main_dur + outro_info["duration"]
    gap = final["duration"] - expect
    print(f"✓ {out}")
    print(f"  총 {hms(final['duration'])} (본편 {hms(main_dur)} + 아웃트로 "
          f"{hms(outro_info['duration'])}, 차 {gap:+.1f}초)")
    if abs(gap) > 2.0:
        print("  ⚠ 길이가 예상과 어긋납니다 — 결과물을 재생해 확인하세요.")

    marker.write_text(json.dumps({
        "source": str(target), "outro": str(outro_src), "image": args.image,
        "outro_start_sec": round(main_dur, 3), "outro_start": hms(main_dur),
        "total_sec": round(final["duration"], 3), "total": hms(final["duration"]),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.replace:
        target.unlink()
        out.replace(target)
        print(f"  원본 교체: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
