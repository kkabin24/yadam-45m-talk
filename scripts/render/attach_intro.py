#!/usr/bin/env python3
"""완성본 mp4 앞에 채널 인트로를 붙인다 (ffmpeg concat).

★인트로는 CapCut 타임라인에 넣지 않는다 (2026-08-17 사용자 지시).
CapCut 드래프트에 인트로 클립을 얹으면 편집 중 자막·씬 타이밍이 인트로 길이만큼
전부 밀려 관리가 어렵다. 인트로는 **CapCut에서 mp4를 내보낸 뒤** 이 스크립트로
앞에 이어 붙인다. 본편은 재인코딩하지 않는다(스트림 복사) — 2시간짜리 8GB 파일을
다시 굽지 않으려는 것.

인트로/본편의 오디오 샘플레이트나 영상 파라미터가 다르면 **인트로 쪽만** 본편에
맞춰 다시 굽는다(10초짜리라 순식간).

사용법:
    # 파일 직접 지정
    python3 scripts/render/attach_intro.py "D:/.../01편_옛이야기여섯편.mp4"

    # 프로젝트 폴더 지정 ({P}/output/*.mp4 를 찾는다)
    python3 scripts/render/attach_intro.py channels/yadam/projects/01편

    # 원본을 결과물로 교체
    python3 scripts/render/attach_intro.py <target> --replace
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER_NAME = "intro_attached.json"

# ★채널 고정 인트로 — 파일명을 코드에 박아 둔다 (2026-08-29 사용자 지시).
# 종전에는 assets/intro/intro.json 의 approved_file 을 읽었는데, 그 파일은
# build_intro_retime.py 가 재빌드할 때마다 통째로 덮어써서 approved_file 이 날아간다
# (실제로 날아가 fallback 인 intro_final.mp4 를 찾다 실패했다). 승인본이 바뀌면 이 상수를 고친다.
# 다른 파일을 붙이려면 --intro 로 지정한다.
# ★2026-09-05: 음량 승인본 교체. 종전 intro_ver3_bgm_sub.mp4 는 -16.38 LUFS 로
#   본편(-12.8)보다 3.6dB 낮아 영상이 조용히 시작했다가 갑자기 커졌다.
#   화면·자막·나레이션은 그대로이고 음량만 -12.77 LUFS 로 맞춘 것이다(원본 파일은 보존).
DEFAULT_INTRO = "intro_ver3_bgm_sub_lufs128.mp4"


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def probe(path):
    """영상/오디오 주요 파라미터를 dict로 반환"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    data = json.loads(out)
    info = {"duration": float(data.get("format", {}).get("duration", 0) or 0),
            "video": None, "audio": None}
    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and info["video"] is None:
            num, _, den = st.get("r_frame_rate", "0/1").partition("/")
            # ★타임스케일(time_base 분모)이 핵심 — concat 데먹서는 입력들의 time_base가
            #   다르면 재스케일하지 않고 그대로 흘려보내 영상 타임스탬프가 깨진다.
            _, _, tb_den = st.get("time_base", "1/15360").partition("/")
            info["video"] = {
                "codec": st.get("codec_name"), "profile": st.get("profile"),
                "width": st.get("width"), "height": st.get("height"),
                "pix_fmt": st.get("pix_fmt"),
                "fps": (float(num) / float(den)) if float(den or 0) else 0.0,
                "timescale": int(tb_den or 15360),
                "duration": float(st.get("duration") or 0),
                "frames": int(st.get("nb_frames") or 0),
            }
        elif kind == "audio" and info["audio"] is None:
            info["audio"] = {
                "codec": st.get("codec_name"),
                "sample_rate": int(st.get("sample_rate", 0) or 0),
                "channels": int(st.get("channels", 0) or 0),
            }
    return info


def find_intro(channel_dir, explicit=None):
    """채널 인트로 승인본 경로. 코드에 박은 DEFAULT_INTRO 가 기준이다."""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            sys.exit(f"인트로 파일 없음: {p}")
        return p
    intro_dir = Path(channel_dir) / "assets" / "intro"
    pinned = intro_dir / DEFAULT_INTRO
    if pinned.exists():
        return pinned
    # 옛 자산 호환 — 고정 인트로가 없는 채널만 여기로 온다.
    meta = intro_dir / "intro.json"
    if meta.exists():
        try:
            approved = json.loads(meta.read_text(encoding="utf-8")).get("approved_file")
        except (json.JSONDecodeError, OSError):
            approved = None
        if approved and (intro_dir / approved).exists():
            return intro_dir / approved
    fallback = intro_dir / "intro_final.mp4"
    if fallback.exists():
        return fallback
    sys.exit(f"인트로 승인본을 찾을 수 없습니다: {intro_dir}/{DEFAULT_INTRO}")


def find_channel_dir(target):
    """target 경로에서 위로 올라가며 channels/{채널} 디렉터리를 찾는다.

    완성본이 외장 드라이브(D:/…/★Final)에 있는 경우가 많아 못 찾을 수 있다.
    그때는 레포의 channels/ 밑에서 인트로를 가진 채널을 찾는다(하나뿐이면 그것).
    """
    for parent in [target.resolve()] + list(target.resolve().parents):
        if (parent / "assets" / "intro").is_dir():
            return parent
        if parent.parent.name == "channels":
            return parent

    repo = Path(__file__).resolve().parents[2]
    cands = [d for d in sorted((repo / "channels").glob("*")) if (d / "assets" / "intro").is_dir()]
    if len(cands) == 1:
        return cands[0]
    return None


def find_target_mp4(arg):
    """파일이면 그대로, 폴더면 그 안(또는 output/)의 mp4 하나를 고른다."""
    p = Path(arg)
    if p.is_file():
        return p
    if not p.is_dir():
        sys.exit(f"경로 없음: {p}")
    for d in (p / "output", p):
        if not d.is_dir():
            continue
        mp4s = [f for f in sorted(d.glob("*.mp4")) if not f.stem.endswith("_intro")]
        if len(mp4s) == 1:
            return mp4s[0]
        if len(mp4s) > 1:
            sys.exit(f"mp4가 여러 개입니다 — 파일을 직접 지정하세요:\n" +
                     "\n".join(f"  {f}" for f in mp4s))
    sys.exit(f"완성본 mp4를 찾을 수 없습니다: {p}")


def normalize_intro(intro, main_info, workdir):
    """인트로를 본편 파라미터에 맞춘다. 맞으면 원본 그대로 반환.

    ★타임스케일까지 반드시 맞춘다. 2026-08-17 실측: 인트로 15360 / 본편 30(CapCut
    내보내기)인 상태로 concat -c copy 하면 본편 타임스탬프가 재스케일되지 않아
    2시간 영상이 24초로 뭉개진다(오디오만 정상이라 format duration 검사로는 안 걸린다).
    """
    intro_info = probe(intro)
    iv, ia = intro_info["video"], intro_info["audio"]
    mv, ma = main_info["video"], main_info["audio"]
    timescale = (mv or {}).get("timescale", 15360)

    video_ok = bool(iv and mv) and all(iv[k] == mv[k] for k in ("codec", "width", "height", "pix_fmt")) \
        and abs((iv or {}).get("fps", 0) - (mv or {}).get("fps", 0)) < 0.01
    audio_ok = bool(ia and ma) and ia["codec"] == ma["codec"] \
        and ia["sample_rate"] == ma["sample_rate"] and ia["channels"] == ma["channels"]
    ts_ok = bool(iv and mv) and iv["timescale"] == timescale

    if video_ok and audio_ok and ts_ok:
        return intro, intro_info, "그대로"

    dst = Path(workdir) / "intro_norm.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(intro)]
    what = []
    if video_ok:
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "17",
                "-pix_fmt", mv["pix_fmt"], "-profile:v", "high",
                "-vf", f"scale={mv['width']}:{mv['height']}:flags=lanczos,fps={mv['fps']:.6f}"]
        what.append("영상 재인코딩")
    if not audio_ok:
        what.append("오디오 재인코딩")
    if not ts_ok:
        what.append(f"타임스케일 {iv['timescale']}→{timescale}")
    cmd += ["-c:a", "aac", "-b:a", "192k",
            "-ar", str(ma["sample_rate"] if ma else 44100),
            "-ac", str(ma["channels"] if ma else 2),
            "-video_track_timescale", str(timescale), str(dst)]
    print(f"인트로 정규화: {', '.join(what)}")
    run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst, probe(dst), " + ".join(what)


def concat(intro, main, out, workdir, timescale):
    """concat demuxer 로 스트림 복사 결합 (본편 재인코딩 없음)"""
    list_path = Path(workdir) / "concat.txt"
    lines = []
    for f in (intro, main):
        posix = str(Path(f).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{posix}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ★stderr를 버리지 않는다 (2026-08-19 04편 실측) — 결합이 3.17GB에서 죽었는데
    #   stderr=DEVNULL이라 ffmpeg가 남긴 사유가 통째로 사라졌고, 파이썬은 exit code만
    #   들고 올라와 원인을 못 찾았다. 같은 명령을 손으로 돌리니 정상 완료됐다.
    #   10GB짜리를 다시 돌리는 비용이 크므로, 실패 시 마지막 40줄을 그대로 보여 준다.
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", "-map", "0:v:0", "-map", "0:a:0",
         "-video_track_timescale", str(timescale),
         "-avoid_negative_ts", "make_zero", str(out)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode == 0:
        return

    tail = "\n".join((r.stderr or "").strip().splitlines()[-40:])
    print(f"⚠ concat demuxer 실패 (exit {r.returncode}) — MPEG-TS 경유로 재시도합니다.")
    print(f"--- ffmpeg stderr (마지막 40줄) ---\n{tail}\n")
    Path(out).unlink(missing_ok=True)
    concat_via_ts(intro, main, out, workdir)


def concat_via_ts(intro, main, out, workdir):
    """MPEG-TS로 바꿔 이어 붙인 뒤 mp4로 되돌린다 (concat demuxer 우회).

    ★2026-08-19 03편 실측 — concat demuxer가 **매번 다른 지점에서** 죽었다
    (1:55:57 / 1:21:15). 원인 후보를 하나씩 지웠다:
      · 본편 전체 읽기 → 정상(167초, 오류 0)
      · 본편을 그대로 리먹스해 같은 디스크에 8.9GB 쓰기 → 정상(260초)
      · 타임베이스·샘플레이트·start_time → 인트로와 본편이 이미 동일
    읽기도 쓰기도 되는데 concat demuxer만 실패하므로, **그 경로를 쓰지 않는다.**
    TS는 컨테이너에 전역 인덱스가 없어 단순 이어 붙이기가 되고, 이 방식은 성공했다.

    비용: TS 중간 파일이 본편과 비슷한 크기로 잠깐 생긴다(여유 공간 2배 필요).
    본편은 여기서도 재인코딩하지 않는다.
    """
    work = Path(workdir)
    ts_files = []
    for i, src in enumerate((intro, main)):
        ts = work / f"part{i}.ts"
        run(["ffmpeg", "-y", "-i", str(src), "-c", "copy",
             "-bsf:v", "h264_mp4toannexb", "-f", "mpegts", str(ts)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ts_files.append(str(ts))

    r = subprocess.run(
        ["ffmpeg", "-y", "-i", "concat:" + "|".join(ts_files),
         "-c", "copy", "-bsf:a", "aac_adtstoasc",
         "-movflags", "+faststart", str(out)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for f in ts_files:
        Path(f).unlink(missing_ok=True)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-40:])
        raise SystemExit(
            f"✗ MPEG-TS 결합도 실패 (exit {r.returncode})\n"
            f"--- ffmpeg stderr (마지막 40줄) ---\n{tail}\n"
            f"※ 부분 출력이 남아 있으면 지우고 다시 실행할 것: {out}")
    print("✓ MPEG-TS 경유로 결합 완료")


def _fmt_ts(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def chapter_times(project_dir, intro_sec):
    """편별 챕터 시각을 스토리보드 장부 카드(실측)에서 다시 계산한다.

    ★meta.txt 의 시각을 그대로 +인트로만큼 미는 것으로는 부족하다. 2026-08-17 실측:
    01편 meta.txt 의 시각은 글자 수로 추정한 값이라 실제 카드 위치와 최대 12초 어긋나
    있었다. 카드 씬의 start 가 곧 편의 시작이므로 그것을 진실로 삼는다.

    반환: [(초, 카드텍스트)] — 1편(=인트로 직후)부터 순서대로.
    """
    sb_path = Path(project_dir) / "_video" / "storyboard.json"
    if not sb_path.exists():
        return None
    sb = json.loads(sb_path.read_text(encoding="utf-8"))
    scenes = sb.get("scenes", sb) if isinstance(sb, dict) else sb

    def to_sec(ts):
        import re as _re
        m = _re.match(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})", ts or "")
        if not m:
            return None
        h, mi, s, ms = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000

    cards = []
    for sc in scenes:
        if sc.get("is_card"):
            t = to_sec(sc.get("start"))
            if t is not None:
                cards.append(t)
    if not cards:
        return None
    # 1편은 인트로 직후부터, 2편~ 은 각 카드 위치 + 인트로 길이
    # ★2026-09-02 수정 — 장부 카드는 2026-08-20부터 **1편 앞에도** 붙는다(insert_chapter_cards).
    #   그래서 cards[0] 이 이미 1편의 시작이다. 종전 코드는 여기에 intro_sec 을 하나 더 앞에
    #   끼워 넣어 **챕터가 한 줄씩 밀렸다** (11편 실측: 없는 호랑이가 0:00:11 로 찍혔다).
    return [c + intro_sec for c in cards]


def rewrite_meta_chapters(meta_path, starts, write=False, outro_start=None):
    """meta.txt 설명문의 챕터 줄 시각을 starts 로 교체한다(문구는 보존)."""
    import re as _re
    lines = Path(meta_path).read_text(encoding="utf-8").split("\n")
    idxs = [i for i, ln in enumerate(lines)
            if _re.match(r"^\d+:\d{2}(:\d{2})?\s+\S", ln)]
    # ★설명문에는 편 목록 뒤에 「백색소음」줄이 하나 더 있다(OUTRO 절).
    # 그 줄은 카드가 아니라 본편이 끝나는 자리이므로 outro_start 로 따로 받는다.
    starts = list(starts)
    if len(idxs) == len(starts) + 1:
        if outro_start is not None:
            starts.append(outro_start)
        else:
            idxs = idxs[:len(starts)]
    if len(idxs) != len(starts):
        print(f"⚠️ 챕터 줄 {len(idxs)}개 ≠ 계산된 편 {len(starts)}개 — meta.txt 자동 수정 건너뜀")
        return None
    preview = []
    for i, (ln_i, sec) in enumerate(zip(idxs, starts)):
        # ★YouTube 챕터는 첫 줄이 0:00 이어야 활성화된다 — 1편을 0:00 으로 둔다
        ts = "0:00:00" if i == 0 else _fmt_ts(sec)
        rest = _re.sub(r"^\d+:\d{2}(:\d{2})?\s+", "", lines[ln_i])
        old = lines[ln_i].split()[0]
        lines[ln_i] = f"{ts}  {rest}"
        preview.append(f"  {old} → {ts}  {rest[:38]}")
    print("챕터 시각 재계산 (장부 카드 실측 + 인트로):")
    print("\n".join(preview))
    if write:
        Path(meta_path).write_text("\n".join(lines), encoding="utf-8")
        print(f"meta.txt 갱신: {meta_path}")
    else:
        print("  (반영하려면 --write-meta)")
    return lines


def report_chapters(args, main_mp4, out_dir, intro_sec):
    """인트로가 붙은 뒤의 챕터 시각을 계산해 보여주고(옵션) meta.txt에 쓴다."""
    proj = Path(args.project) if args.project else None
    if proj is None:
        for parent in main_mp4.resolve().parents:
            if (parent / "_video" / "storyboard.json").exists():
                proj = parent
                break
    if not proj:
        print(f"\n※ 프로젝트 폴더를 못 찾아 챕터 시각을 계산하지 않았습니다 "
              f"(--project 로 지정). 챕터는 +{intro_sec:.1f}초 밀립니다.")
        return
    starts = chapter_times(proj, intro_sec)
    if not starts:
        print("\n※ 스토리보드에 장부 카드가 없어 챕터 시각 계산을 건너뜁니다.")
        return
    metas = [Path(args.meta)] if args.meta else \
        [p for p in (proj / "meta.txt", out_dir / "meta.txt") if p.exists()]
    for mp in metas:
        print(f"\n[{mp}]")
        rewrite_meta_chapters(mp, starts, write=args.write_meta)


def main():
    ap = argparse.ArgumentParser(description="완성본 mp4 앞에 채널 인트로 붙이기")
    ap.add_argument("target", help="완성본 mp4 경로 또는 프로젝트 폴더")
    ap.add_argument("--intro", help="인트로 mp4 (기본: 채널 assets/intro 승인본)")
    ap.add_argument("--channel-dir", help="채널 디렉터리 (기본: target에서 자동 탐색)")
    ap.add_argument("--out", help="결과 경로 (기본: <원본>_intro.mp4)")
    ap.add_argument("--replace", action="store_true", help="결과로 원본을 교체")
    ap.add_argument("--force", action="store_true", help="이미 붙어 있어도 다시 실행")
    ap.add_argument("--project", help="프로젝트 폴더 {P} — 챕터 시각 재계산에 사용")
    ap.add_argument("--meta", help="갱신할 meta.txt (기본: {P}/meta.txt 와 mp4 옆 meta.txt)")
    ap.add_argument("--write-meta", action="store_true", help="계산된 챕터 시각을 meta.txt에 기록")
    args = ap.parse_args()

    main_mp4 = find_target_mp4(args.target)
    out_dir = main_mp4.parent

    marker = out_dir / MARKER_NAME
    if marker.exists() and not args.force:
        prev = json.loads(marker.read_text(encoding="utf-8"))
        print(f"이미 인트로가 붙어 있습니다 ({prev.get('attached_at')}) — 결합은 건너뜁니다. "
              f"다시 붙이려면 --force")
        report_chapters(args, main_mp4, out_dir, prev.get("intro_duration", 0))
        return

    channel_dir = args.channel_dir or find_channel_dir(main_mp4)
    if not channel_dir and not args.intro:
        sys.exit("채널 디렉터리를 찾지 못했습니다 — --intro 또는 --channel-dir 로 지정하세요")
    intro_src = find_intro(channel_dir, args.intro)

    main_info = probe(main_mp4)
    print(f"본편: {main_mp4.name}  {main_info['duration']:.1f}s "
          f"({main_mp4.stat().st_size / 1e9:.2f} GB)")

    with tempfile.TemporaryDirectory(prefix="attach_intro_") as workdir:
        intro_use, intro_info, how = normalize_intro(intro_src, main_info, workdir)
        print(f"인트로: {intro_src.name}  {intro_info['duration']:.2f}s ({how})")

        tmp_out = out_dir / f".{main_mp4.stem}_intro.part.mp4"
        print("결합 중 (본편은 스트림 복사 — 재인코딩 없음)…")
        concat(intro_use, main_mp4, tmp_out, workdir,
               (main_info["video"] or {}).get("timescale", 15360))

        # ★검증은 반드시 '비디오 스트림'까지 본다. format duration 은 오디오만 맞아도
        #   통과해버려서, 영상이 24초로 뭉개진 파일을 정상으로 판정한 적이 있다(2026-08-17).
        result = probe(tmp_out)
        expected = main_info["duration"] + intro_info["duration"]
        rv, mv, iv = result["video"], main_info["video"], intro_info["video"]
        problems = []
        if abs(result["duration"] - expected) > 1.0:
            problems.append(f"전체 길이 {result['duration']:.1f}s ≠ 기대 {expected:.1f}s")
        if rv and abs(rv["duration"] - expected) > 1.5:
            problems.append(f"영상 트랙 길이 {rv['duration']:.1f}s ≠ 기대 {expected:.1f}s "
                            f"(타임스케일 불일치 의심)")
        if rv and mv and iv and mv["frames"] and iv["frames"]:
            want = mv["frames"] + iv["frames"]
            if rv["frames"] and abs(rv["frames"] - want) > 2:
                problems.append(f"프레임 수 {rv['frames']} ≠ 기대 {want}")
        if problems:
            tmp_out.unlink(missing_ok=True)
            sys.exit("검증 실패:\n  - " + "\n  - ".join(problems))

        if args.replace:
            final = main_mp4
            backup = out_dir / f"{main_mp4.stem}.nointro.mp4"
            main_mp4.replace(backup)
            tmp_out.replace(final)
            print(f"원본 교체 — 인트로 없는 원본은 {backup.name} 으로 보관")
        else:
            final = Path(args.out) if args.out else out_dir / f"{main_mp4.stem}_intro.mp4"
            final.parent.mkdir(parents=True, exist_ok=True)
            tmp_out.replace(final)

    from datetime import datetime
    marker.write_text(json.dumps({
        "output": str(final), "intro": str(intro_src),
        "intro_duration": round(intro_info["duration"], 3),
        "total_duration": round(result["duration"], 3),
        "attached_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"완료: {final}")
    print(f"  {result['duration']:.1f}s ({result['duration']/60:.1f}분), "
          f"{final.stat().st_size / 1e9:.2f} GB")
    print(f"  마커: {marker}")

    report_chapters(args, main_mp4, out_dir, intro_info["duration"])


if __name__ == "__main__":
    main()
