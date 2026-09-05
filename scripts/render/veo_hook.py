#!/usr/bin/env python3
"""
VEO_HOOK — 훅 인트로 립싱크 클립 자동화 (SCENE_TIMING 후, RENDER 전).

씬1 이미지를 시작 프레임으로 Veo i2v 8초 립싱크 클립을 생성하고,
렌더 storyboard(`{V}/storyboard.json`) 씬1에 `video_path`를 주입한다.

엔진 (settings.json image.veo.engine, 기본 gemini):
  - gemini: Gemini API predictLongRunning (⚠️유료, GEMINI_API_KEY — 데몬 불필요, 기본)
            모델 기본 veo-3.1-fast-generate-preview, 1080p, 8초, 네이티브 오디오(대사 포함)
  - flow:   labs.google 웹세션 (flow_veo.py — 데몬 :포트 + Chrome 로그인 필요, ~20크레딧/8초)
  - pjn:    로컬 5090 서버 api.project-n.work (무료, PJN_API_KEY — MiniMax H3 i2v, 오디오 포함)
            최대 1376x768 · 대사는 H3 태그 형식 별도 프롬프트(veo_hook_prompt_pjn.txt) 사용
            첫 프레임을 입력 이미지에 픽셀 고정(스틸→클립 컷 연결이 Veo보다 자연스러움)

실패해도 프롬프트/매니페스트 파일은 항상 남으므로 수동 재시도 가능:
  {V}/veo_hook.json 의 manual_cmd 를 그대로 실행하면 된다.

동작:
  1) 훅 대사 추출 — script.txt 앞부분의 첫 따옴표 대사 (playbook "대사 선행" 전제)
  2) {V}/veo_hook_prompt.txt 생성(이미 있으면 그대로 사용 — PD가 다듬은 뒤 재실행 지원)
  3) {V}/veo_hook.json 매니페스트 기록 (시작 프레임·프롬프트·수동 커맨드 — 항상)
  4) Veo 생성 → {V}/veo_hook_scene01.mp4 (이미 있으면 건너뜀 — 과금 보호, --force로 재생성)
  5) {V}/storyboard.json 씬1에 "video_path" 주입 (capcut_export가 스틸 대신 클립으로 싣는다)

Usage:
    python3 scripts/render/veo_hook.py <project_dir> [--prompt-only] [--force]
        [--engine gemini|flow|pjn] [--model MODEL] [--duration 8] [--config settings.json]
"""
import argparse
import base64
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "image"))
from generate_image import find_env_key, ssl_context  # noqa: E402

# ★Windows 인증서 저장소 우회 (2026-08-26). 이 PC는 ROOT 저장소에 ASN1이 깨진 항목이 있어
# ssl.create_default_context()가 [ASN1: NOT_ENOUGH_DATA]로 죽는다 — urlopen이 전량 실패한다.
# generate_image.ssl_context()(certifi 번들 직접 지정)를 그대로 재사용. None이면 기본 동작.
SSLCTX = ssl_context()

OUT_NAME = "veo_hook_scene01.mp4"
PROMPT_NAME = "veo_hook_prompt.txt"
PJN_PROMPT_NAME = "veo_hook_prompt_pjn.txt"  # H3 대사 태그 형식 — Veo 프롬프트와 분리
MANIFEST_NAME = "veo_hook.json"

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
POLL_INTERVAL = 10.0
POLL_TIMEOUT = 900.0

PJN_API_BASE = "https://api.project-n.work"
PJN_POLL_INTERVAL = 5.0
PJN_POLL_TIMEOUT = 1800.0  # 단일 GPU 큐 대기 포함
# Cloudflare가 Python-urllib 기본 UA를 차단(1010) — 명시 UA 필수
PJN_UA = "veo-hook-pjn/1.0"

PROMPT_TEMPLATE = """Animate this Korean webtoon-style illustration.
{scene_desc}

{speaker} slowly begins to speak in Korean — a natural voice \
matching the character's age and mood, lips synced precisely to the words:
"{dialogue}"
{listener}

Camera holds on the face, slowly pushing in. Subtle ambient motion only.

Audio: quiet ambient sound and the Korean dialogue only. No background music.
Strictly no subtitles, no captions, no on-screen text of any kind.
Keep the original 2D Korean webtoon illustration art style — do not make it photorealistic.
"""

# MiniMax H3(pjn)는 대사를 (S1) <d>[언어] …</d> 태그로 받아야 발음이 안정적 — 2026-08 실측
PROMPT_TEMPLATE_PJN = """Korean webtoon-style 2D illustration animation. \
Keep the original 2D Korean webtoon illustration art style — do not make it photorealistic.
{scene_desc}

[0s-2s] {speaker} comes alive with subtle ambient motion. \
Camera holds on the face, slowly pushing in.
[2s-{duration}s] {speaker} slowly begins to speak — a natural Korean voice matching \
the character's age and mood, lips synced precisely to the words: \
(S1) <d>[Korean] {dialogue}</d>
{listener}
Audio: quiet ambient sound and the Korean dialogue only. No background music.
Strictly no subtitles, no captions, no on-screen text of any kind.
"""


def strip_hangul_outside_dialogue(text):
    """대사 태그 <d>…</d> 밖의 한글을 지운다.

    ★MiniMax H3(pjn)는 프롬프트에 있는 **한글을 그대로 소리 내어 읽는다** (2026-08-31 실측).
      인물 lock 이 「갯골댁 — a 62-year-old Korean tidal-flat gatherer…」처럼 한글 이름으로
      시작하는데, 그것이 씬 서술에 치환돼 들어가면 모델이 대사 앞에 그 이름을 먼저 말한다.
      whisper 받아쓰기로 확인: 「복실도」/「개꿀덱」/「봉출」이 본 대사 앞에 붙어 있었다.
      그 결과 자막이 실제 발화보다 4초 이상 먼저 떠서 매칭이 어긋났다.
      한글이 필요한 곳은 <d> 안(=실제 대사)뿐이므로 나머지는 지운다.
    """
    def clean(s):
        s = re.sub(r"[가-힣]+(?:\s+[가-힣]+)*\s*(?:—|-|–)\s*", "", s)   # 「이름 — 」 접두
        s = re.sub(r"[가-힣]+", "", s)                                    # 남은 한글
        return re.sub(r"[ 	]{2,}", " ", s)
    out, last = [], 0
    for m in re.finditer(r"<d>.*?</d>", text, re.S):
        out.append(clean(text[last:m.start()])); out.append(m.group(0)); last = m.end()
    out.append(clean(text[last:]))
    return "".join(out)

# ★화자 기본값 (2026-08-27). 인물이 둘 이상인 씬에서 "가운데 인물"은 누가 말할지를
# 모델에 맡기는 것과 같다 — 06편 씬1(노인+젊은이)에서 운 좋게 맞았을 뿐이다.
# --speaker 로 인물을 지목하면 그 문장이 들어가고, 함께 선 인물은 --listener 로 묶어 둔다.
DEFAULT_SPEAKER = "The character at the center of the frame"


def extract_dialogue(script_path: pathlib.Path) -> str | None:
    """대본 앞부분에서 첫 따옴표 대사 추출 (곧은/굽은 따옴표 모두)."""
    if not script_path.exists():
        return None
    head = script_path.read_text(encoding="utf-8")[:800]
    m = re.search(r'[“"]([^”"]{2,80}?)[”"]', head)
    return m.group(1).strip() if m else None


def extract_dialogue_from_prompt(prompt_path: pathlib.Path) -> str | None:
    """생성된 프롬프트에서 실제 클립 대사 추출.

    PD가 프롬프트의 대사를 다듬었을 수 있으므로(예: 유튜브 수위 조절로 욕설 제거),
    프롬프트가 있으면 그 안의 대사가 '클립이 실제 말하는 것'의 진실이다. 매니페스트 dialogue는
    capcut 콜드오픈이 선두 자막 큐와 매칭하는 기준이라, 클립 실제 발화와 반드시 일치해야 한다.
    프롬프트 내 유일한 한글 포함 따옴표 = 대사(scene_desc는 전부 영문).
    pjn(MiniMax H3) 프롬프트는 따옴표가 아니라 <d>[Korean] …</d> 태그로 대사를 싣는다."""
    if not prompt_path.exists():
        return None
    txt = prompt_path.read_text(encoding="utf-8")
    m = re.search(r'<d>\s*(?:\[[^\]]*\]\s*)?([^<\n]*[가-힣][^<\n]*)</d>', txt)
    if m:
        return m.group(1).strip()
    m = re.search(r'[“"]([^”"\n]*[가-힣][^”"\n]*)[”"]', txt)
    return m.group(1).strip() if m else None


def scene1_desc(project: pathlib.Path, scene_no: int | None = None) -> tuple[int, str]:
    """소스 storyboard 씬의 (id, visual_desc — {id} placeholder를 lock으로 치환).

    scene_no 를 주면 그 id의 씬을, 없으면 첫 씬을 쓴다. ★첫 씬에 화자가 없을 때가 있다
    (2026-08-28 실측: 「살림 장부」 씬1은 사람 없는 부엌이라 훅을 걸 수 없다).
    """
    board = json.loads((project / "storyboard.json").read_text(encoding="utf-8"))
    scenes = board["scenes"] if isinstance(board, dict) else board
    sc = scenes[0]
    if scene_no is not None:
        sc = next((x for x in scenes if x.get("id") == scene_no), sc)
    # ★파일명은 **요청한(병합 보드) 씬 id**로 고정한다 (2026-08-29 실측). 아래 하위 보드 폴백은
    #   sc 를 편 하위 보드의 씬(항상 id=1)으로 갈아치우기 때문에, 그대로 두면 세 편의 훅이
    #   전부 veo_hook_scene01 로 나와 서로를 덮어썼다(2·3편이 1편 클립을 물려받았다).
    want_id = sc.get("id", 1)
    desc = sc.get("visual_desc", "")

    # ★옴니버스 대응 (2026-08-27). 병합 보드({P}/storyboard.json)는 편별 보드를 이어 붙인 것이고,
    # characters.json은 편 하위 프로젝트에만 있다. image 경로 첫 마디로 편 폴더를 찾는다.
    # ★2026-08-28 수정 — 종전에는 `if not desc` 일 때만 편 폴더를 찾았다. 병합본이 visual_desc를
    # 싣기 시작하자 이 분기가 통째로 건너뛰어져 **{s1_seobi} placeholder가 치환되지 않은 채**
    # 프롬프트로 나갔다. 묘사가 있든 없든 lock을 읽으려면 편 폴더를 먼저 확정해야 한다.
    # 그리고 하위 보드로 폴백할 때도 **요청한 씬 id로** 고른다(종전에는 무조건 첫 씬이었다).
    src = project
    img = sc.get("image") or sc.get("image_path") or ""
    parts = pathlib.PurePosixPath(img.replace("\\", "/")).parts
    cand = project / parts[0] if parts else None
    if cand and cand.is_dir() and (cand / "storyboard.json").exists():
        src = cand
        if not desc:
            sub = json.loads((cand / "storyboard.json").read_text(encoding="utf-8"))
            subs = sub["scenes"] if isinstance(sub, dict) else sub
            want = sc.get("id")
            sc2 = next((x for x in subs if x.get("id") == want), subs[0] if subs else None)
            if sc2:
                sc = sc2
                desc = sc.get("visual_desc", "")

    chars_path = src / "characters.json"
    if chars_path.exists():
        chars = json.loads(chars_path.read_text(encoding="utf-8"))
        for cid, c in (chars.items() if isinstance(chars, dict) else []):
            if cid.startswith("_") or not isinstance(c, dict):
                continue
            lock = c.get("lock")
            if not lock and isinstance(c.get("variants"), dict):
                dv = c["variants"].get(c.get("default_variant")) or next(iter(c["variants"].values()), {})
                lock = (dv or {}).get("lock")
            desc = desc.replace("{" + cid + "}", lock or c.get("name") or cid)
    # ★편별 훅 (2026-08-28): 씬에 적어 둔 대사·화자를 그대로 돌려준다.
    #   병합 보드의 편별 첫 씬에 hook_line/hook_speaker 를 적어 두면 --dialogue/--speaker 없이도 맞는다.
    return want_id, desc, sc.get("hook_line"), sc.get("hook_speaker")


def start_frame_path(project: pathlib.Path, video_dir: pathlib.Path, scene_id: int) -> pathlib.Path | None:
    """씬1 시작 프레임: 렌더 storyboard의 image_path 우선, 없으면 scenes/ 규약 경로."""
    # ★프로젝트 자신의 씬 파일이 1순위다 (2026-08-28 실측으로 순서를 뒤집었다).
    # 종전에는 렌더 보드({V}/storyboard.json)를 먼저 봤는데, 옴니버스에서 편 하위
    # 프로젝트를 대상으로 돌리면 --video-subdir 가 가리키는 공용 _video 의 **병합 보드**
    # 첫 씬을 집어 온다 — 편 3개의 훅이 전부 1편 씬1을 시작 프레임으로 받았다.
    # 클립은 프롬프트 쪽으로 모핑해 버려서 앞부분이 엉뚱한 장면으로 시작한다.
    p = project / "scenes" / f"scene_{scene_id:02d}.png"
    if p.exists():
        return p
    rb = video_dir / "storyboard.json"
    if rb.exists():
        scenes = json.loads(rb.read_text(encoding="utf-8")).get("scenes", [])
        # ★장부 카드를 건너뛴다 (2026-08-27). insert_chapter_cards.py가 1편 앞에도 카드를
        # 끼우므로 렌더 보드의 씬1은 제목 카드다. 그대로 쓰면 말하는 제목 카드가 생성된다.
        scenes = [s for s in scenes if not s.get("is_card")]
        # ★요청한 씬 id 로 고른다 (2026-08-29). 종전에는 무조건 scenes[0] 이라 옴니버스에서
        #   2·3편 훅이 **1편 씬1 이미지**를 시작 프레임으로 받았다.
        sc = next((x for x in scenes if x.get("id") == scene_id), None)
        if sc is None and scene_id in (None, 1) and scenes:
            sc = scenes[0]
        if sc:
            q = (video_dir / sc.get("image_path", "")).resolve()
            if q.exists():
                return q
    # 옴니버스: 씬 이미지는 편 하위 프로젝트에 있다 — 소스 보드의 image 경로를 따라간다.
    # ★2026-08-28 수정 — 종전에는 무조건 `scenes[0]`(병합 보드의 첫 씬)을 집었다.
    # 편마다 훅을 붙이기 시작하자 **세 편의 시작 프레임이 전부 1편 씬1**이 됐다.
    # 요청한 scene_id 의 씬을 찾아 그 image 를 쓴다.
    sb = project / "storyboard.json"
    if sb.exists():
        board = json.loads(sb.read_text(encoding="utf-8"))
        scenes = board["scenes"] if isinstance(board, dict) else board
        sc = next((x for x in scenes if x.get("id") == scene_id), None)
        if sc is None and scenes:
            sc = scenes[0]
        if sc:
            p = (project / str(sc.get("image", ""))).resolve()
            if p.exists():
                return p
    return None


def _ffprobe_vspec(path: pathlib.Path) -> tuple[int, int, int] | None:
    """클립 (width, height, fps) — ffprobe. 실패 시 None."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        lines = [ln for ln in r.stdout.strip().splitlines() if ln]
        w, h, rfr = int(lines[0]), int(lines[1]), lines[2]
        num, den = rfr.split("/")
        fps = round(float(num) / float(den)) if float(den) else 0
        return w, h, fps
    except (ValueError, IndexError, OSError):
        return None


def conform_to_render(clip_path: pathlib.Path, width: int, height: int, fps: int) -> None:
    """veo 클립을 렌더 규격(해상도·fps)으로 재인코딩(이미 맞으면 생략).

    Veo는 네이티브 24fps로, flow veo-fast는 720p로 뽑으므로 프로젝트(보통 1080p/30fps)와
    어긋난다. 규격 불일치 클립은 CapCut 미리보기에서 실시간 변환하다 불안정 환경에서 죽기도 해서,
    렌더 규격으로 미리 맞춰 둔다. ffmpeg/ffprobe 필요(PATH). 음량은 건드리지 않음(포맷만).
    24→fps 변환은 프레임 복제라 짧은 훅에선 티가 안 난다."""
    spec = _ffprobe_vspec(clip_path)
    if spec == (width, height, fps):
        print(f"  클립 규격 일치 ({width}x{height}/{fps}fps) — 변환 생략")
        return
    print(f"  클립 규격 {spec} → 렌더 {width}x{height}/{fps}fps 재인코딩")
    tmp = clip_path.with_name(clip_path.stem + "_conform.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path),
           "-vf", f"scale={width}:{height}:flags=lanczos,fps={fps}",
           "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-crf", "18", "-preset", "medium",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        print(f"  ⚠️ 규격 변환 건너뜀 (ffmpeg 없음? {e}) — 원본 클립 사용", file=sys.stderr)
        return
    if r.returncode != 0 or not tmp.exists():
        print(f"  ⚠️ 규격 변환 실패 — 원본 클립 사용: {r.stderr[-200:]}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return
    clip_path.unlink()
    tmp.rename(clip_path)
    print(f"  ✓ 규격 변환 완료: {width}x{height}/{fps}fps")


def normalize_loudness(clip_path: pathlib.Path, target_lufs: float, tp_ceil: float = -1.0) -> None:
    """클립 음량을 낭독과 같은 레벨로 맞춘다 (2026-08-27 신설 · 2026-09-05 방식 교체).

    ★왜 필요한가: 규격 변환은 포맷만 건드리고 음량은 그대로 둔다. 실측(06편)에서 pjn 클립은
    −24.0 LUFS로 나왔는데 낭독 audio.mp3는 −12.8 LUFS였다 — 11dB 차이라 훅만 유난히 작게 들린다.

    ★2026-09-05 방식 교체 (13편 실측). 종전 loudnorm 2패스는 두 가지로 빗나갔다:
      ① 허용 폭이 ±1.0 LU라 −14.05 클립이 「목표 −13.0 근처」로 판정돼 그냥 넘어갔다.
         그래서 세 편 훅이 −12.31 / −14.05 / −14.52 로 2.2dB나 벌어진 채 나갔다.
      ② `linear=true` + 측정 패스의 `target_offset` 재사용은 착지가 어긋난다
         (같은 배치에서 −14.5 클립이 **−21.1** 로 떨어졌다).
      → 측정 → 이득(`volume`) → True Peak 리미터(`alimiter`). 짧은 클립에 예측 가능하고,
        13편 실측 오차는 0.35 LU 이내였다(−13.15 / −12.84 / −13.08).

    ★목표는 YouTube의 −14가 아니라 **본편 낭독과 같은 값**이다 (사용자 확인 2026-09-05).
      YouTube는 −14로 내리기만 하므로 영상 전체가 균일하면 어떤 값이든 재생 음량은 같다.
      훅만 −14로 맞추면 본편(−12.8)보다 1.2dB 작아져 편이 시작될 때마다 작았다 커진다.
      값은 settings.json 의 render.loudness_lufs 로 온다.
    """
    def measure() -> dict | None:
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(clip_path),
               "-map", "0:a", "-af", "loudnorm=print_format=json", "-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as e:
            print(f"  ⚠️ 음량 정규화 건너뜀 (ffmpeg 없음? {e})", file=sys.stderr)
            return None
        m = re.search(r"\{[^{}]*input_i[^{}]*\}", r.stderr.replace(chr(13), ""), re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None

    stats = measure()
    if not stats:
        print("  ⚠️ 음량 측정 실패 — 정규화 생략", file=sys.stderr)
        return
    cur = float(stats.get("input_i", 0.0))
    # ★허용 폭 0.5 LU — 리미터가 피크를 깎으면서 목표보다 0.2~0.35 LU 낮게 착지한다.
    #   0.3 으로 좁히면 재실행 때마다 한 번 더 굽게 되고(AAC 재인코딩 누적), 0.5 면 한 번에 멈춘다.
    #   남는 편차는 0.5 LU 미만으로 귀에 잡히지 않는다(라우드니스 최소가청차 약 1 LU).
    if abs(cur - target_lufs) <= 0.5:
        print(f"  음량 {cur:.2f} LUFS — 목표 {target_lufs:.1f} 근처라 생략")
        return
    gain = target_lufs - cur
    print(f"  음량 {cur:.2f} LUFS → {target_lufs:.1f} LUFS 정규화 (이득 {gain:+.2f} dB)")
    tmp = clip_path.with_name(clip_path.stem + "_lufs.mp4")
    af = "volume=%.2fdB,alimiter=limit=%.4f:level=disabled" % (gain, 10 ** (tp_ceil / 20.0))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(clip_path),
           "-map", "0:v", "-map", "0:a", "-af", af, "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
           "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        print(f"  ⚠️ 음량 정규화 실패 — 원본 사용: {r.stderr[-200:]}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return
    clip_path.unlink()
    tmp.rename(clip_path)
    got = measure()
    if got:
        print(f"  ✓ 음량 정규화 완료: {float(got['input_i']):.2f} LUFS (TP {got['input_tp']})")
    else:
        print(f"  ✓ 음량 정규화 완료: {target_lufs:.1f} LUFS")


def inject_video_path(video_dir: pathlib.Path) -> bool:
    """렌더 storyboard 씬1에 video_path 주입. 렌더 storyboard가 아직 없으면 False."""
    rb = video_dir / "storyboard.json"
    if not rb.exists():
        return False
    data = json.loads(rb.read_text(encoding="utf-8"))
    scenes = data.get("scenes", [])
    # ★장부 카드는 건너뛴다 (2026-08-27) — 카드에 클립을 물리면 말하는 제목 카드가 된다.
    scenes = [s for s in scenes if not s.get("is_card")]
    if not scenes:
        return False
    if scenes[0].get("video_path") != OUT_NAME:
        scenes[0]["video_path"] = OUT_NAME
        json.dump(data, open(rb, "w"), ensure_ascii=False, indent=2)
    return True


# ---------------- gemini(Veo API) 백엔드 ----------------

def _api(url: str, key: str, body: dict | None = None, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=SSLCTX) as resp:
        return json.loads(resp.read())


def veo_gemini(prompt: str, frame: pathlib.Path, out_path: pathlib.Path, key: str,
               model: str, ratio: str, resolution: str, duration: int,
               negative_prompt: str | None = None) -> int:
    """Gemini API Veo i2v: predictLongRunning → 폴링 → URI 다운로드. 성공 0.

    negative_prompt: 선택. ★자막 구워짐 방지에 필요하다 — 프롬프트에 따옴표 대사를 넣으면
    Veo가 그 대사를 **깨진 한글 자막으로 화면에 렌더링**한다(2026-08-14 인트로 실측).
    """
    b64 = base64.b64encode(frame.read_bytes()).decode()
    mime = "image/png" if frame.suffix.lower() == ".png" else "image/jpeg"
    # durationSeconds는 정수여야 한다(문자열이면 400) — 2026-07-19 실측
    params = {"aspectRatio": ratio, "resolution": resolution, "durationSeconds": int(duration)}
    if negative_prompt:
        params["negativePrompt"] = negative_prompt
    inst = {"prompt": prompt, "image": {"bytesBase64Encoded": b64, "mimeType": mime}}
    try:
        data = _api(f"{API_BASE}/models/{model}:predictLongRunning", key,
                    {"instances": [inst], "parameters": params})
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} from Veo API: {e.read().decode(errors='replace')[:600]}", file=sys.stderr)
        return 1
    op_name = data.get("name")
    if not op_name:
        print(f"error: 생성 시작 응답에 operation name 없음: {json.dumps(data)[:400]}", file=sys.stderr)
        return 1

    print(f"  생성 시작 op={op_name[:60]}... 폴링 중", file=sys.stderr)
    deadline = time.time() + POLL_TIMEOUT
    op = {}
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            op = _api(f"{API_BASE}/{op_name}", key)
        except urllib.error.HTTPError as e:
            print(f"  폴링 HTTP {e.code} — 재시도", file=sys.stderr)
            continue
        if op.get("done"):
            break
        print("  ... 생성 중", file=sys.stderr)
    if not op.get("done"):
        print(f"error: 폴링 타임아웃({int(POLL_TIMEOUT)}s)", file=sys.stderr)
        return 1
    if op.get("error"):
        print(f"error: Veo 생성 실패: {json.dumps(op['error'], ensure_ascii=False)[:600]}", file=sys.stderr)
        return 1

    samples = (((op.get("response") or {}).get("generateVideoResponse") or {})
               .get("generatedSamples") or [])
    uri = (samples[0].get("video") or {}).get("uri") if samples else None
    if not uri:
        print(f"error: 응답에 video.uri 없음: {json.dumps(op, ensure_ascii=False)[:600]}", file=sys.stderr)
        return 1

    req = urllib.request.Request(uri, headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=300, context=SSLCTX) as resp:
            buf = resp.read()
    except urllib.error.HTTPError:
        sep = "&" if "?" in uri else "?"
        with urllib.request.urlopen(f"{uri}{sep}key={key}", timeout=300, context=SSLCTX) as resp:
            buf = resp.read()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buf)
    print(f"saved {out_path}  ({out_path.stat().st_size} bytes)  [gemini/{model} {duration}s {resolution}]")
    return 0


# ---------------- pjn(로컬 5090 MiniMax H3) 백엔드 ----------------

def _pjn_multipart(fields: dict, file_field: str, file_path: pathlib.Path) -> tuple[bytes, str]:
    """stdlib만으로 multipart/form-data 인코딩 (외부 의존성 없음)."""
    boundary = f"----pjnhook{int(time.time() * 1000)}"
    parts = []
    for k, v in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        )
    mime = "image/png" if file_path.suffix.lower() == ".png" else "image/jpeg"
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{file_path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
        + file_path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def veo_pjn(prompt: str, frame: pathlib.Path, out_path: pathlib.Path, key: str,
            ratio: str, quality: float, duration: int) -> int:
    """PJN 서버 i2v: POST /v1/jobs → 폴링 → mp4 다운로드. 성공 0."""
    import os
    base = (os.environ.get("PJN_API_URL") or PJN_API_BASE).rstrip("/")
    if ratio not in ("16:9", "3:4"):
        print(f"error: pjn 서버는 16:9/3:4만 지원 (요청 {ratio})", file=sys.stderr)
        return 1
    body, boundary = _pjn_multipart(
        {"mode": "i2v", "prompt": prompt, "aspect_ratio": ratio,
         "quality": str(quality), "duration": str(max(5, min(15, int(duration))))},
        "first_frame", frame,
    )
    req = urllib.request.Request(
        f"{base}/v1/jobs", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-API-Key": key, "User-Agent": PJN_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=SSLCTX) as resp:
            job_id = json.loads(resp.read())["job_id"]
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} from PJN API: {e.read().decode(errors='replace')[:600]}", file=sys.stderr)
        return 1

    print(f"  생성 시작 job={job_id} 폴링 중 (로컬 5090, 무료)", file=sys.stderr)
    deadline = time.time() + PJN_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(PJN_POLL_INTERVAL)
        req = urllib.request.Request(f"{base}/v1/jobs/{job_id}",
                                     headers={"X-API-Key": key, "User-Agent": PJN_UA})
        try:
            with urllib.request.urlopen(req, timeout=60, context=SSLCTX) as resp:
                status = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"  폴링 HTTP {e.code} — 재시도", file=sys.stderr)
            continue
        state = status.get("state")
        if state == "completed":
            req = urllib.request.Request(f"{base}/v1/jobs/{job_id}/video",
                                         headers={"X-API-Key": key, "User-Agent": PJN_UA})
            with urllib.request.urlopen(req, timeout=300, context=SSLCTX) as resp:
                buf = resp.read()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(buf)
            print(f"saved {out_path}  ({out_path.stat().st_size} bytes)  "
                  f"[pjn/minimax-h3 {duration}s q{quality}]")
            return 0
        if state in ("failed", "error"):
            print(f"error: PJN 생성 실패: {json.dumps(status, ensure_ascii=False)[:400]}", file=sys.stderr)
            return 1
    print(f"error: 폴링 타임아웃({int(PJN_POLL_TIMEOUT)}s)", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--config", type=pathlib.Path, default=None,
                    help="settings.json 경로 (기본: {P}/../../config/settings.json)")
    ap.add_argument("--prompt-only", action="store_true", help="무료: 프롬프트·매니페스트만 생성")
    ap.add_argument("--force", action="store_true", help="mp4가 있어도 재생성 (⚠️과금 재발생)")
    ap.add_argument("--engine", default=None, choices=["gemini", "flow", "pjn"],
                    help="기본: settings image.veo.engine → gemini. pjn=로컬 5090 서버(무료)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--duration", type=int, default=8, choices=[4, 6, 8], help="gemini 전용 (기본 8초)")
    ap.add_argument("--resolution", default=None, help="gemini 전용 (기본 settings → 1080p)")
    ap.add_argument("--ports", default=None, help="flow 전용: 유료 레인 포트(쉼표구분)")
    ap.add_argument("--video-subdir", default="_video")
    ap.add_argument("--no-conform", action="store_true",
                    help="veo 클립을 렌더 규격(해상도·fps)으로 자동 재인코딩하지 않음")
    ap.add_argument("--speaker", default=None,
                    help="말하는 인물을 영문으로 지목 (예: \"The old man on the left in the grey jacket\"). "
                         "인물이 둘 이상인 씬에서는 반드시 지정 — 미지정 시 모델이 화자를 고른다")
    ap.add_argument("--listener", default=None,
                    help="같은 화면에 선 다른 인물 (예: \"the young man in white\") — 입을 다물게 고정")
    ap.add_argument("--scene", type=int, default=None,
                    help="시작 프레임으로 쓸 씬 id (기본: 씬1). 씬1에 화자가 없을 때 지정")
    ap.add_argument("--dialogue", default=None,
                    help="훅 대사 직접 지정 (기본: script.txt 앞부분 첫 따옴표 대사)")
    ap.add_argument("--out", default=None,
                    help="출력 파일명 (기본 veo_hook_scene01.mp4). 편별로 여러 훅을 만들 때 지정")
    ap.add_argument("--manifest", default=None,
                    help="매니페스트 파일명 (기본 veo_hook.json)")
    ap.add_argument("--story", type=int, default=None,
                    help="이 훅이 붙을 편 번호 — attach_hook.py 가 읽는다")
    ap.add_argument("--inject", action="store_true",
                    help="⚠️금지(SKILL+ §9): 렌더 storyboard 씬1에 video_path를 주입한다. "
                         "CapCut이 클립을 타임라인에 올리다 죽으므로 쓰지 말 것 — "
                         "훅은 내보내기 뒤 attach_hook.py 로 붙인다")
    ap.add_argument("--lufs", type=float, default=None,
                    help="클립 음량을 이 LUFS로 정규화 (기본: settings render.loudness_lufs → -14). "
                         "0을 주면 정규화하지 않는다")
    args = ap.parse_args()

    P = args.project_dir.resolve()
    V = (P / args.video_subdir).resolve()
    V.mkdir(parents=True, exist_ok=True)
    # ★편별 훅 (2026-08-28) — 옴니버스는 편마다 훅을 하나씩 만든다.
    # --out/--manifest 로 파일명을 갈라 세 개가 한 폴더에 공존하게 한다.
    # 파일명은 씬 id 로 가른다 — 씬을 알아야 하므로 실제 확정은 scene1_desc 뒤에서 한다(아래).
    out_name = args.out
    manifest_name = args.manifest

    # settings: image.veo (channels/{ch}/projects/{proj} 관례 → 채널 config)
    cfg_path = args.config or (P.parents[1] / "config" / "settings.json")
    settings = {}
    if cfg_path and cfg_path.exists():
        settings = json.loads(cfg_path.read_text(encoding="utf-8"))
    veo_cfg = (settings.get("image") or {}).get("veo") or {}
    render_cfg = settings.get("render") or {}
    engine = args.engine or veo_cfg.get("engine") or "gemini"
    ratio = veo_cfg.get("ratio") or "16:9"
    resolution = args.resolution or veo_cfg.get("resolution") or "1080p"
    flow_cfg = veo_cfg.get("flow") or {}
    pjn_cfg = veo_cfg.get("pjn") or {}
    pjn_quality = float(pjn_cfg.get("quality") or 1.0)  # 0.4/0.6/0.8/1.0 (1.0→1376x768)
    if engine == "gemini":
        model = args.model or veo_cfg.get("model") or "veo-3.1-fast-generate-preview"
    elif engine == "pjn":
        model = "minimax-h3"
    else:
        model = args.model or flow_cfg.get("model") or "veo-fast"
    ports = ([int(x) for x in str(args.ports).split(",") if x.strip()] if args.ports
             else list(flow_cfg.get("ports") or [3849]))

    # 1) 소재 수집: 씬1 desc + 훅 대사 + 시작 프레임
    try:
        scene_id, desc, sc_hook_line, sc_hook_speaker = scene1_desc(P, args.scene)
    except (OSError, json.JSONDecodeError, IndexError, KeyError) as e:
        print(f"error: storyboard.json 읽기 실패: {e}", file=sys.stderr)
        return 2
    # ★우선순위 (2026-08-28): --dialogue > 그 씬의 hook_line > script.txt 앞부분 첫 대사.
    #   2·3편 훅은 script.txt 앞부분을 봐야 1편 대사가 나오므로 hook_line 이 없으면 틀린다.
    dialogue = args.dialogue or sc_hook_line or extract_dialogue(P / "script.txt")
    if not dialogue:
        print("error: script.txt 앞 800자에서 따옴표 대사를 찾지 못함 — playbook '대사 선행' 위반이거나 "
              "대본 없음. 프롬프트를 수동 작성 후 재실행.", file=sys.stderr)
        return 2
    # ★파일명 확정 (2026-08-28) — 편마다 훅이 하나씩이므로 씬 id 로 갈라 한 폴더에 공존시킨다.
    if not out_name:
        out_name = "veo_hook_scene%02d.mp4" % scene_id
    if not manifest_name:
        manifest_name = ("veo_hook.json" if scene_id == 1
                         else "veo_hook_scene%02d.json" % scene_id)
    prompt_suffix = pathlib.Path(out_name).stem.replace('veo_hook', '') or ''
    frame = start_frame_path(P, V, scene_id)

    # 2) 프롬프트: 있으면 존중, 없으면 템플릿 생성 (pjn은 H3 대사 태그 형식이라 파일 분리)
    prompt_name = PJN_PROMPT_NAME if engine == "pjn" else PROMPT_NAME
    if prompt_suffix:
        prompt_name = pathlib.Path(prompt_name).stem + prompt_suffix + ".txt"
    prompt_path = V / prompt_name
    if not prompt_path.exists():
        speaker = args.speaker or sc_hook_speaker or DEFAULT_SPEAKER
        listener = (f"The other people in the frame do not speak — {args.listener} stays silent, "
                    f"only listening, lips closed." if args.listener else "")
        if engine == "pjn":
            text = PROMPT_TEMPLATE_PJN.format(scene_desc=desc.strip(), dialogue=dialogue,
                                              duration=args.duration,
                                              speaker=speaker, listener=listener)
            text = strip_hangul_outside_dialogue(text)
        else:
            text = PROMPT_TEMPLATE.format(scene_desc=desc.strip(), dialogue=dialogue,
                                          speaker=speaker, listener=listener)
        prompt_path.write_text(text, encoding="utf-8")
        print(f"프롬프트 생성: {prompt_path}")
    else:
        print(f"프롬프트 재사용: {prompt_path}")

    # 프롬프트의 대사를 진실로 삼는다 — PD가 수위 조절 등으로 다듬었으면 그게 클립 실제 발화이고,
    # 매니페스트 dialogue(=capcut 콜드오픈 자막-큐 매칭 기준)는 그와 일치해야 한다.
    prompt_dialogue = extract_dialogue_from_prompt(prompt_path)
    if prompt_dialogue:
        dialogue = prompt_dialogue

    # 3) 매니페스트 (항상 기록 — 수동 폴백의 근거 파일)
    out_path = V / out_name
    if engine == "gemini":
        manual = f"python3 scripts/render/veo_hook.py '{P}' --force --engine gemini --model {model}"
    elif engine == "pjn":
        manual = f"python3 scripts/render/veo_hook.py '{P}' --force --engine pjn"
    else:
        manual = (f"python3 scripts/image/flow_veo.py '{V / PROMPT_NAME}' '{out_path}' "
                  f"'{frame}' --model {model} --ratio {ratio} --ports {','.join(map(str, ports))}")
    manifest = {
        "engine": engine,
        "start_frame": str(frame) if frame else None,
        "prompt_file": prompt_name,
        "dialogue": dialogue,
        "model": model, "ratio": ratio,
        "resolution": (resolution if engine == "gemini"
                       else f"quality={pjn_quality}" if engine == "pjn" else None),
        "duration": args.duration if engine in ("gemini", "pjn") else 8,
        "output": out_name,
        "story": args.story,
        "manual_cmd": manual,
        "status": "pending",
    }

    def save(status: str) -> None:
        manifest["status"] = status
        json.dump(manifest, open(V / manifest_name, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if frame is None:
        save("no_start_frame")
        print("error: 씬1 시작 프레임 이미지를 찾지 못함 (scenes/ 비었나?) — 매니페스트만 기록.", file=sys.stderr)
        return 1
    if args.prompt_only:
        save("prompt_ready")
        print("--prompt-only: 생성 생략. 매니페스트 기록 완료.")
        return 0

    # 4) 생성 (mp4 있으면 건너뜀 — 재실행 시 과금 보호)
    if out_path.exists() and not args.force:
        print(f"클립 존재 — 생성 건너뜀: {out_path}")
    else:
        prompt = prompt_path.read_text(encoding="utf-8")
        if engine == "gemini":
            key = find_env_key(P, "GEMINI_API", "GEMINI_API_KEY", "GOOGLE_API_KEY")
            if not key:
                save("failed")
                print("error: GEMINI_API_KEY 없음 (.env) — 매니페스트의 manual_cmd로 재시도.", file=sys.stderr)
                return 1
            print(f"⚠️ Veo 생성 시작 (유료 API, {model}, {args.duration}s, {resolution})")
            rc = veo_gemini(prompt, frame, out_path, key, model, ratio, resolution, args.duration)
        elif engine == "pjn":
            key = find_env_key(P, "PJN_API_KEY")
            if not key:
                save("failed")
                print("error: PJN_API_KEY 없음 (.env) — 매니페스트의 manual_cmd로 재시도.", file=sys.stderr)
                return 1
            print(f"로컬 5090 생성 시작 (무료, minimax-h3, {args.duration}s, q{pjn_quality})")
            rc = veo_pjn(prompt, frame, out_path, key, ratio, pjn_quality, args.duration)
        else:
            try:
                from flow_veo import run as flow_run
            except ImportError as e:
                save("failed")
                print(f"error: flow_veo import 실패: {e}", file=sys.stderr)
                return 1
            print(f"⚠️ Veo 생성 시작 (flow 유료 ~20크레딧, 레인 {ports}, {model})")
            rc = flow_run(prompt, out_path, frame, model=model, ratio=ratio, ports=ports)
        if rc != 0 or not out_path.exists():
            save("failed")
            print(f"생성 실패(rc={rc}) — 매니페스트의 manual_cmd로 수동 재시도 가능. "
                  f"RENDER는 씬1 스틸 폴백으로 진행해도 됨.", file=sys.stderr)
            return 1

    # 4.5) 렌더 규격 자동 맞춤 (Veo 네이티브 24fps·flow 720p → 프로젝트 1080p/30fps)
    if not args.no_conform and out_path.exists():
        conform_to_render(out_path, int(render_cfg.get("width", 1920)),
                          int(render_cfg.get("height", 1080)), int(render_cfg.get("fps", 30)))

    # 4.6) 음량을 낭독과 같은 레벨로 (2026-08-27) — 안 맞추면 훅만 작게 들린다
    lufs = args.lufs if args.lufs is not None else float(render_cfg.get("loudness_lufs", -14.0))
    if lufs and out_path.exists():
        normalize_loudness(out_path, lufs)

    # 5) 렌더 storyboard 주입 — ★기본 끔 (2026-08-27)
    # SKILL+ §9: veo 클립을 CapCut 타임라인에 올리면 Windows CapCut이 죽는다(자동 주입이든
    # 사람이 직접 얹든, 규격을 맞춰도). 주입하면 capcut_export가 클립을 드래프트에 싣기
    # 때문에 여기서 주입하는 것 자체가 사고다. 훅은 CapCut 내보내기 **뒤**에
    # attach_hook.py 가 mp4 앞부분을 갈아끼우는 방식으로 붙인다.
    if not args.inject:
        save("generated")
        print(f"완료: {out_path}")
        print("  ※ CapCut 드래프트에는 넣지 않는다(SKILL+ §9). 내보낸 mp4에 붙이려면:")
        print(f"     python3 scripts/render/attach_hook.py {P}")
        return 0

    if inject_video_path(V):
        save("injected")
        print(f"완료: 렌더 storyboard 씬1 ← video_path={OUT_NAME}")
        print("  ⚠️ --inject 는 SKILL+ §9 금지 사항이다 — CapCut에서 열면 죽을 수 있다.",
              file=sys.stderr)
    else:
        save("generated_not_injected")
        print("클립은 준비됐으나 렌더 storyboard(_video/storyboard.json)가 없어 주입 보류.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
