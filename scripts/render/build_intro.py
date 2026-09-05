#!/usr/bin/env python3
"""채널 고정 인트로 클립 제작 (1회성 자산 — SKILL.md INTRO 절).

마스코트가 인사하는 립싱크 영상을 PJN(로컬 5090 MiniMax H3, 무료)으로 만들고,
**사용자가 녹음한 음성**을 얹는다. ★Gemini 영상 생성은 영구 차단(video_engine_policy.py).
클립은 최대 8초라 오디오가 8초를 넘으면 이어붙인다
(2번째 클립의 시작 프레임 = 1번째 클립의 마지막 프레임 → 연속성 유지).

사용법:
    python3 scripts/render/build_intro.py --channel yadam --stage clip1     # 무료(PJN)
    python3 scripts/render/build_intro.py --channel yadam --stage clip2     # 무료(PJN)
    python3 scripts/render/build_intro.py --channel yadam --stage assemble  # 무료

자산: channels/{채널}/assets/intro/
    draft_ref_v1.png  시작 프레임(확정본)   인트로.mp3  사용자 녹음
    → clip1.mp4, clip2.mp4, intro.mp4, intro.json
"""
import argparse, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "render"))
# ★2026-09-06 — 인트로 클립도 PJN(무료·PJN_API_KEY)으로 만든다.
#   veo_gemini 는 video_engine_policy 에 의해 영구 차단됐다(호출하면 즉시 죽는다).
from veo_hook import veo_pjn, find_env_key  # noqa: E402
from video_engine_policy import assert_video_engine, assert_video_key  # noqa: E402

# ★대사를 프롬프트에 넣지 않는다 (2026-08-14 실측)
# 따옴표 대사를 주면 Veo가 그 말을 **깨진 한글 자막으로 화면에 구워 넣는다**
# (1차 clip1에서 저고리 위에 "작잔묵"·"점" 렌더링됨). 오디오는 어차피 사용자 녹음으로
# 교체하므로 립싱크 정확도는 필요 없고, "말하는 입 움직임"만 있으면 된다.
BEAT = {
    1: "opening greeting — she is just starting to speak, bright and welcoming",
    2: "closing invitation — she is finishing her greeting, warm and settling into calm",
}

NEGATIVE = ("text, letters, hangul, korean characters, chinese characters, captions, "
            "subtitles, on-screen text, writing on clothing, watermark, logo, signature, "
            "camera movement, zoom, pan, moving background, extra people, distorted face")

PROMPT = """A young Korean woman in a cream hanbok stands in a moonlit hanok courtyard at night, looking straight at the camera and speaking warmly to the viewer, as if greeting a guest at her gate. ({beat})

Her lips move naturally and continuously, at a calm unhurried speaking pace, as though she is saying a gentle friendly greeting. She blinks softly a couple of times and her head moves only very slightly. Her warm smile stays throughout. Her hands stay relaxed and low, never rising toward her face.

The camera is completely STATIC — no zoom, no pan, no dolly, no parallax. The background stays still: the full moon, the blossom branches, the paper lanterns, the tiled roof and the stone wall do not move, apart from a barely perceptible drift of one or two petals. Her face, hairstyle, floral hairpin, tassel and hanbok stay EXACTLY as in the source image and must not change or drift at any point.

Soft warm night lighting, calm and cosy. The frame contains NO writing of any kind — no captions, no subtitles, no letters on her clothing, no watermark, no logo."""


def use_certifi_ssl():
    """★Windows+miniconda 함정: urllib의 기본 SSL 컨텍스트가 윈도우 인증서 저장소를 읽다
    `ssl.SSLError: [ASN1: NOT_ENOUGH_DATA]`로 죽는다. SSL_CERT_FILE 환경변수로는 못 막는다
    (load_default_certs가 윈도우 저장소를 '추가로' 읽기 때문). 기본 컨텍스트 자체를 갈아끼운다.
    requests를 쓰는 generate_image.py는 certifi 번들을 쓰므로 이 문제가 없다."""
    try:
        import ssl, certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        ssl._create_default_https_context = lambda *a, **k: ctx
        print(f"  SSL: certifi 번들 사용 ({certifi.where()})", file=sys.stderr)
    except Exception as e:  # certifi 없으면 기본값 그대로 시도
        print(f"  SSL: certifi 사용 불가 ({e}) — 기본 컨텍스트로 진행", file=sys.stderr)


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"명령 실패: {' '.join(map(str, cmd))}")
    return r.stdout.strip()


def dur(path):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(path)]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="yadam")
    ap.add_argument("--stage", required=True, choices=["clip1", "clip2", "assemble"])
    ap.add_argument("--quality", type=float, default=1.0, choices=[0.4, 0.6, 0.8, 1.0],
                    help="PJN 화질 (1.0=1376x768)")
    ap.add_argument("--frame", default="draft_ref_v1.png")
    ap.add_argument("--audio", default="인트로.mp3")
    ap.add_argument("--seconds", type=int, default=None, help="클립 길이 강제 (4/6/8초)")
    args = ap.parse_args()

    D = ROOT / "channels" / args.channel / "assets" / "intro"
    audio = D / args.audio
    if not audio.exists():
        raise SystemExit(f"오디오 없음: {audio}")
    a_dur = dur(audio)

    if args.stage in ("clip1", "clip2"):
        use_certifi_ssl()
        engine = assert_video_engine("pjn", "build_intro")   # ★정책 관문 1겹
        key = find_env_key(D, "PJN_API_KEY")
        if not key:
            raise SystemExit("PJN_API_KEY 없음 (.env)")
        assert_video_key(key, engine, "build_intro")         # ★정책 관문 3겹
        n = 1 if args.stage == "clip1" else 2
        out = D / f"clip{n}.mp4"
        if n == 1:
            frame, secs = D / args.frame, (args.seconds or 8)
        else:
            # 2번째 클립 시작 프레임 = 1번째 클립의 마지막 프레임 (연속성)
            c1 = D / "clip1.mp4"
            if not c1.exists():
                raise SystemExit("clip1.mp4 먼저 만들 것")
            frame = D / "clip1_lastframe.png"
            sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-sseof", "-0.1",
                "-i", str(c1), "-frames:v", "1", "-y", str(frame)])
            need = a_dur - dur(c1)
            # ★1080p는 durationSeconds=4를 거부한다("1080p is not supported for a duration of
            #   4 seconds", 2026-08-14 실측). 남는 길이는 assemble에서 잘라내므로 넉넉히 뽑는다.
            secs = args.seconds or (6 if need <= 6 else 8)
            print(f"  남은 오디오 {need:.2f}초 → 클립2 {secs}초 (초과분은 assemble에서 트림)")
        prompt = PROMPT.format(beat=BEAT[n])
        print(f"PJN 생성 (무료, minimax-h3): {secs}s q{args.quality} ← {frame.name}")
        rc = veo_pjn(prompt, frame, out, key, "16:9", args.quality, secs)
        if rc:
            raise SystemExit("PJN 생성 실패")
        print(f"✓ {out.name}  {dur(out):.2f}초")
        return 0

    # ---- assemble: 클립 이어붙이기 → 오디오 길이로 자르기 → 사용자 음성 얹기 ----
    clips = [D / f"clip{i}.mp4" for i in (1, 2) if (D / f"clip{i}.mp4").exists()]
    if not clips:
        raise SystemExit("clip1.mp4 없음")
    v_dur = sum(dur(c) for c in clips)
    print(f"클립 {len(clips)}개 합계 {v_dur:.2f}초 / 오디오 {a_dur:.2f}초")
    if v_dur < a_dur - 0.05:
        print(f"⚠️ 영상이 오디오보다 {a_dur - v_dur:.2f}초 짧다 — 마지막 프레임을 늘려 채운다")

    silent = D / "_video_only.mp4"
    if len(clips) == 1:
        src = clips[0]
    else:
        lst = D / "_concat.txt"
        lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        src = D / "_concat.mp4"
        # 키프레임이 안 맞아 스트림 복사 불가 → 재인코딩 (SKILL+ §25)
        sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(lst), "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-an", "-y", str(src)])
    # 규격 통일 + 오디오 길이에 맞춰 트림 (모자라면 마지막 프레임 정지로 패드)
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,tpad=stop_mode=clone:stop_duration=2",
        "-t", f"{a_dur:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-an", "-y", str(silent)])

    out = D / "intro.mp4"
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(silent), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", "-y", str(out)])

    for f in (D / "_concat.txt", D / "_concat.mp4", silent):
        if f.exists():
            f.unlink()

    meta = {"duration": round(dur(out), 3), "audio": args.audio, "audio_duration": round(a_dur, 3),
            "clips": [c.name for c in clips], "clip_seconds": [round(dur(c), 2) for c in clips],
            "start_frame": args.frame, "engine": "pjn", "model": "minimax-h3",
            "quality": args.quality,
            "dialogue_in_audio": "인트로.srt 참조 (프롬프트에는 대사를 넣지 않는다 — 자막 구워짐 방지)",
            "note": "자막 없음 — CapCut에서 입힌다. 매 편 CapCut export 뒤 ffmpeg concat으로 앞에 붙일 것."}
    (D / "intro.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out}  {meta['duration']}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
