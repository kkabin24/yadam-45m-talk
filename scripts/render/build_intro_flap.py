#!/usr/bin/env python3
"""고정 인트로 — 벤치마크 방식(입 모양 교체) 립싱크 빌더.

★왜 이 방식인가 (2026-08-14)
Veo는 **자기가 생성한 음성**에 입을 맞춘다. 우리 녹음을 덮으면 반드시 어긋난다
(실측: 입-오디오 상관 −0.27, 시간을 밀어도 최대 +0.20). Veo에는 오디오 입력이 없어
SRT를 줘도 우리 mp3의 타이밍을 알 방법이 없다.
벤치마크 ①(97만)의 인트로를 픽셀 분석해 보면 **화면의 0.26%만 움직이고 그게 정확히 입**이다.
즉 영상 생성이 아니라 정지 이미지 + 입 모양 교체다. 이 방식은 오디오 진폭으로 입을 여닫으므로
**우리 녹음과 정확히 동기**되고 비용이 0이다.

구성: clip1의 앞부분(Veo 실사 모션)을 그대로 쓰고, 그 뒤를 립플랩으로 잇는다(사용자 지시).
      이음매는 짧은 크로스페이드로 덮어 프레이밍 차이를 감춘다.

사용법:
    python3 scripts/render/build_intro_flap.py --channel yadam
    python3 scripts/render/build_intro_flap.py --channel yadam --cut 3.2 --xfade 0.25
"""
import argparse, json, pathlib, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parents[2]
# 입 영역 — clip1(1920x1080) 프레임 기준
MOUTH_BOX = (895, 450, 1040, 555)
FEATHER = 12


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(r.stderr[-2500:], file=sys.stderr)
        raise SystemExit(f"명령 실패: {' '.join(map(str, cmd))}")
    return r.stdout.strip()


def dur(p):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(p)]))


def composite_mouth(base_p, variant_p, out_p):
    """variant의 입 영역만 base에 부드럽게 얹는다 → 나머지 픽셀은 base와 동일."""
    base = Image.open(base_p).convert("RGB")
    var = Image.open(variant_p).convert("RGB")
    if var.size != base.size:
        var = var.resize(base.size, Image.LANCZOS)
    mask = Image.new("L", base.size, 0)
    inner = (MOUTH_BOX[0] + FEATHER, MOUTH_BOX[1] + FEATHER,
             MOUTH_BOX[2] - FEATHER, MOUTH_BOX[3] - FEATHER)
    ImageDraw.Draw(mask).rectangle(inner, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(FEATHER))
    out = Image.composite(var, base, mask)
    out.save(out_p)
    changed = (np.asarray(mask) > 2).mean() * 100
    print(f"  {out_p.name}: 변경 픽셀 {changed:.2f}% (벤치 실측 0.26%)")
    return out


def srt_spans(p):
    """SRT → [(start, end)] 초. 자막이 없는 구간은 무조건 입을 닫는다."""
    if not p.exists():
        return []
    def t(s):
        h, m, rest = s.split(":"); sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        if "-->" in line:
            a, b = [x.strip() for x in line.split("-->")]
            out.append((t(a), t(b)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="yadam")
    ap.add_argument("--video", default="clip1.mp4", help="앞부분에 쓸 Veo 클립")
    ap.add_argument("--open-t", type=float, default=3.20,
                    help="입 벌린 상태로 쓸 clip1 시각(초). 이 프레임이 립플랩 베이스이자 컷 지점")
    ap.add_argument("--closed-t", type=float, default=3.58,
                    help="입 다문 상태를 가져올 clip1 시각(초)")
    ap.add_argument("--audio", default="인트로.mp3")
    ap.add_argument("--srt", default="인트로.srt")
    ap.add_argument("--xfade", type=float, default=0.0,
                    help="이음매 크로스페이드(초). ★기본 0 — 베이스가 컷 지점의 프레임 그 자체라 "
                         "하드컷이 오히려 안 보인다. 크로스페이드를 걸면 다른 렌더끼리 겹쳐 이중상이 생긴다")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--out", default="intro.mp4")
    args = ap.parse_args()

    D = ROOT / "channels" / args.channel / "assets" / "intro"
    audio, srt = D / args.audio, D / args.srt
    a_dur = dur(audio)
    cut = args.open_t
    print(f"오디오 {a_dur:.3f}초 / Veo 구간 0~{cut}초 / 립플랩 {cut}~{a_dur:.2f}초")

    # 1) 입 두 상태 — ★둘 다 clip1의 실제 프레임에서 뽑는다(1920x1080 native).
    #    생성 이미지는 1344폭이라 업스케일하면 입이 뭉갠다. 같은 렌더에서 가져오면 선명하고
    #    스타일이 정확히 일치한다. 비용도 0원.
    print("입 모양 추출 (clip1 native):")
    open_p, closed_p = D / "_flap_open.png", D / "_flap_closed.png"
    src_closed = D / "_src_closed.png"
    for t, outp in ((args.open_t, open_p), (args.closed_t, src_closed)):
        sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t}",
            "-i", str(D / args.video), "-frames:v", "1", "-y", str(outp)])
    composite_mouth(open_p, src_closed, closed_p)

    # 2) 오디오 포락선 → 프레임별 입 상태
    raw_p = D / "_a.raw"
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio),
        "-ac", "1", "-ar", "8000", "-f", "s16le", "-y", str(raw_p)])
    raw = np.fromfile(raw_p, dtype=np.int16).astype(float)
    n = 8000 // args.fps
    rms = np.array([np.sqrt((raw[i * n:(i + 1) * n] ** 2).mean() + 1e-9)
                    for i in range(int(a_dur * args.fps))])
    env = rms / (np.percentile(rms, 95) + 1e-9)
    spans = srt_spans(srt)
    print(f"자막 구간 {len(spans)}개")

    start_f = int(round(cut * args.fps))
    total_f = int(round(a_dur * args.fps))
    OPEN_T, CLOSE_T, HOLD = 0.32, 0.20, 2      # 히스테리시스 + 최소 유지 프레임

    states, cur, held = [], 0, 0
    for i in range(total_f):
        t = i / args.fps
        voiced = (not spans) or any(a <= t <= b for a, b in spans)
        e = env[i] if i < len(env) else 0.0
        want = cur
        if held >= HOLD:
            if cur == 0 and voiced and e > OPEN_T:
                want = 1
            elif cur == 1 and (not voiced or e < CLOSE_T):
                want = 0
        if want != cur:
            cur, held = want, 0
        else:
            held += 1
        states.append(cur)
    flap = states[start_f:]
    print(f"립플랩 {len(flap)}프레임 | 입 여닫힘 {sum(1 for i in range(1,len(flap)) if flap[i]!=flap[i-1])}회"
          f" | 벌린 비율 {np.mean(flap)*100:.0f}%")

    # 3) 프레임을 파이프로 흘려 립플랩 영상 인코딩 (크로스페이드용으로 앞을 겹쳐 만든다)
    W, H = 1920, 1080
    imgs = {0: np.asarray(Image.open(closed_p).convert("RGB"), np.uint8),
            1: np.asarray(Image.open(open_p).convert("RGB"), np.uint8)}
    over_f = int(round(args.xfade * args.fps))
    seq = states[max(0, start_f - over_f):]
    flap_mp4 = D / "_flap.mp4"
    p = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(args.fps), "-i", "-", "-c:v", "libx264", "-crf", "16",
         "-preset", "medium", "-pix_fmt", "yuv420p", "-an", "-y", str(flap_mp4)],
        stdin=subprocess.PIPE)
    for s in seq:
        p.stdin.write(imgs[s].tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("립플랩 인코딩 실패")

    # 4) Veo 앞부분 + 립플랩 크로스페이드 → 오디오 결합
    head_end = cut + args.xfade
    out = D / args.out
    join = (f"[a][b]xfade=transition=fade:duration={args.xfade}:offset={cut:.3f}[v]"
            if args.xfade > 0 else "[a][b]concat=n=2:v=1:a=0[v]")
    fc = (f"[0:v]trim=0:{head_end:.3f},setpts=PTS-STARTPTS,scale={W}:{H},fps={args.fps}[a];"
          f"[1:v]setpts=PTS-STARTPTS,fps={args.fps}[b];" + join)
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(D / args.video), "-i", str(flap_mp4), "-i", str(audio),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "2:a:0", "-t", f"{a_dur:.3f}",
        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-y", str(out)])

    for f in (raw_p, flap_mp4, src_closed):
        f.unlink(missing_ok=True)

    meta = {
        "method": "benchmark mouth-flap (still + mouth swap), Veo head only",
        "duration": round(dur(out), 3), "audio": args.audio, "audio_duration": round(a_dur, 3),
        "veo_head": {"clip": args.video, "range": [0, cut], "xfade": args.xfade},
        "flap": {"open_from": f"{args.video}@{args.open_t}s", "closed_from": f"{args.video}@{args.closed_t}s",
                 "mouth_box": list(MOUTH_BOX),
                 "fps": args.fps, "open_ratio": round(float(np.mean(flap)), 3)},
        "why": "Veo는 자기 생성 음성에 입을 맞춰 우리 녹음과 어긋난다(상관 −0.27). "
               "벤치 ①은 정지 이미지+입 교체(화면의 0.26%만 변화)이고 그 방식이 오디오와 정확히 동기된다.",
        "note": "자막 없음 — CapCut에서 입힌다. 매 편 CapCut export 뒤 concat으로 앞에 붙일 것.",
    }
    (D / "intro.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out}  {meta['duration']}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
