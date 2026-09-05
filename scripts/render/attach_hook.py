#!/usr/bin/env python3
"""완성본 mp4의 편 도입부를 훅 클립으로 갈아끼운다 (post-export 스플라이스).

★왜 CapCut이 아니라 여기서 하는가 (SKILL+ §9)
    veo/pjn 클립을 CapCut 타임라인에 올리면 — 자동 주입이든 사람이 직접 얹든 —
    Windows CapCut이 그 자리에서 죽는다. 규격(1920x1080/30fps/AAC)을 맞춰도 마찬가지다.
    그래서 드래프트는 끝까지 **스틸만**으로 두고, 사람이 내보낸 mp4를 여기서 손본다.

두 가지 방식이 편마다 자동으로 갈린다
    · **교체(replace)** — 그 편 도입부 낭독이 훅 대사를 그대로 읽는 경우(대사가 편의 첫
      문장). 선두 큐부터 대사 큐 끝까지를 클립으로 갈아끼운다. 같은 말이 두 번 들리지 않는다.
    · **삽입(insert)** — 대사가 편 뒤쪽에 있는 경우. 편이 시작하는 자리에 클립을 끼워
      넣기만 한다(콜드 오픈). 낭독은 그대로 두고, 그 대사는 나중에 제자리에서 다시 나온다.

    장부 카드는 건드리지 않는다 — 카드가 먼저 뜨고, 그 다음이 훅이다.

★전체를 다시 굽지 않는다
    2시간 반짜리(11GB)를 CRF로 재인코딩하면 몇 시간이 걸린다. 스트림 복사는 **시작이
    키프레임이어야 하지만 끝은 아무 데서나 끊어도 된다**는 점을 이용한다:

        복사[0 → 훅1 자리] · 훅1 · 재인코딩[훅1 끝 → 다음 키프레임]
        복사[키프레임 → 훅2 자리] · 훅2 · 재인코딩[…] · … · 복사[… → 끝]

    다시 굽는 것은 훅 클립 3개와 다리 구간 몇 초뿐이다.

★타임라인이 밀린다
    각 훅마다 Δ = 클립 길이 − 갈아낸 구간. 이 스크립트가 `{V}/storyboard.json` 의 장부 카드
    start/end 와 meta.txt 챕터 시각을 **누적 Δ로** 함께 옮긴다. 그래야 뒤이어 도는
    attach_intro.py 의 챕터 재계산이 맞는다.

순서: CapCut 내보내기 → **attach_hook** → attach_intro → attach_outro → UPLOAD

사용법:
    python3 scripts/render/attach_hook.py channels/yadam/projects/06편
    python3 scripts/render/attach_hook.py {P} --dry-run
    python3 scripts/render/attach_hook.py {P} --mp4 "D:/.../yadam_06.mp4"
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attach_intro import probe, find_target_mp4  # noqa: E402

MARKER_NAME = "hook_attached.json"
# 대사 큐를 편 시작 뒤 이 개수 안에서만 찾는다 — 훅 대사는 편 도입부에 있어야 하고,
# 뒤쪽에서 우연히 같은 말이 나온 큐를 잡으면 편 한복판을 잘라낸다.
MAX_LEAD_CUES = 12
ORDINALS = ["첫", "두", "세", "네", "다섯", "여섯", "일곱", "여덟"]


def parse_srt(path):
    """[(idx, start_sec, end_sec, text)]"""
    def to_sec(ts):
        h, m, s = ts.split(":")
        s, ms = s.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    cues, block = [], []
    def flush():
        if len(block) >= 3:
            m = re.match(r"([\d:,]+)\s*-->\s*([\d:,]+)", block[1])
            if m:
                cues.append((int(block[0]), to_sec(m.group(1)), to_sec(m.group(2)),
                             " ".join(block[2:]).strip()))
    # ★utf-8-sig — Vrew/split_long_cues 가 내보낸 자막에 BOM 이 붙어 있다(2026-08-29 실측).
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            block.append(line)
        else:
            flush()
            block = []
    flush()
    return cues


def _norm(s):
    return re.sub(r'[\s"“”\'’.,!?…·]', "", s or "")


def ts_to_sec(ts):
    m = re.match(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})", ts or "")
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def sec_to_ts(sec):
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    mi, s = divmod(rem, 60)
    return "%02d:%02d:%02d,%03d" % (h, mi, int(s), round((s - int(s)) * 1000))


def episode_starts(video_dir):
    """편이 시작하는 시각 = 그 편 장부 카드의 끝. [(편번호, 초)]"""
    sb = Path(video_dir) / "storyboard.json"
    data = json.loads(sb.read_text(encoding="utf-8"))
    out = []
    for sc in data.get("scenes", []):
        if sc.get("is_card"):
            t = ts_to_sec(sc.get("end"))
            if t is not None:
                out.append(t)
    return [(i + 1, t) for i, t in enumerate(out)]


def episode_starts_from_board(project_dir, video_dir, cues):
    """장부 카드가 없을 때 — 편별 첫 씬의 첫 자막 큐 시각을 편 시작으로 삼는다.

    ★2026-08-29 신설. 종전에는 편 시작을 **장부 카드의 끝**에서만 읽었다
    (`episode_starts`). 카드를 넣지 않고 내보낸 완성본에는 카드가 없어
    편별 훅을 붙일 자리를 하나도 못 찾는다. 병합 보드에서 `hook_line` 이 있는 씬을
    찾고, 렌더 보드의 그 씬 `subtitle_range` 첫 큐 시각을 쓴다.
    """
    src = Path(project_dir) / "storyboard.json"
    rb = Path(video_dir) / "storyboard.json"
    if not src.exists() or not rb.exists():
        return []
    board = json.loads(src.read_text(encoding="utf-8"))
    scenes = board["scenes"] if isinstance(board, dict) else board
    hook_ids = [s["id"] for s in scenes if s.get("hook_line")]
    rmap = {}
    for s in json.loads(rb.read_text(encoding="utf-8")).get("scenes", []):
        rng = s.get("subtitle_range")
        if rng:
            rmap[s.get("id")] = rng[0]
    # ★subtitle_range 는 **1-based SRT 번호**다 (리스트 인덱스가 아니다 — 2026-08-29 실측).
    #   인덱스로 쓰면 편 시작이 자막 한 칸씩 밀려 훅이 대사 뒷조각부터 덮는다.
    by_idx = {c[0]: c for c in cues}
    out = []
    for n, sid in enumerate(hook_ids, start=1):
        ci = rmap.get(sid)
        c = by_idx.get(ci)
        if c is None:
            continue
        out.append((n, c[1]))
    return out


def hook_lines_in_order(project_dir):
    """병합 보드의 hook_line 을 편 순서대로 — 매니페스트에 story 가 없을 때 쓴다."""
    src = Path(project_dir) / "storyboard.json"
    if not src.exists():
        return []
    board = json.loads(src.read_text(encoding="utf-8"))
    scenes = board["scenes"] if isinstance(board, dict) else board
    return [s["hook_line"] for s in scenes if s.get("hook_line")]


def make_card_video(dst, info, ordinal, title, seconds, workdir):
    """장부 카드 3초 클립(무음)을 본편 규격으로 굽는다.

    ★내보낸 mp4에 사후로 끼우기 위한 것 (2026-08-29). 정석은 RENDER 앞의
    insert_chapter_cards.py 이지만, 카드를 빠뜨린 채 이미 내보냈다면 10GB를 다시 굽는
    대신 여기서 훅과 같은 자리에 함께 끼운다. 카드 그림은 그 스크립트의 make_card 를 그대로 쓴다.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from insert_chapter_cards import make_card
    v, a = info["video"], info["audio"]
    png = Path(workdir) / ("card_%s.png" % ordinal)
    make_card(str(png), ordinal, title, size=(v["width"], v["height"]))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-loop", "1", "-framerate", "%.6f" % v["fps"], "-t", "%.3f" % seconds, "-i", str(png),
           "-f", "lavfi", "-t", "%.3f" % seconds,
           "-i", "anullsrc=channel_layout=stereo:sample_rate=%d" % a.get("sample_rate", 44100),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-r", "%.6f" % v["fps"], "-video_track_timescale", str(v["timescale"]),
           "-c:a", "aac", "-b:a", "%dk" % max(96, int(a.get("bit_rate", 128000) / 1000)),
           "-ar", str(a.get("sample_rate", 44100)), "-ac", "2",
           "-shortest", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def load_hooks(video_dir):
    """{V}/veo_hook*.json 매니페스트 → [(story, clip경로, 대사)] (story 순)."""
    V = Path(video_dir)
    hooks = []
    for f in sorted(V.glob("veo_hook*.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        clip = V / (m.get("output") or "")
        # ★자막을 구운 판이 있으면 그쪽 (burn_hook_subs.py). 훅은 CapCut 밖이라
        #   자막이 따로 안 붙는다 — 안 구운 판을 쓰면 그 구간만 자막이 없다.
        sub = clip.with_name(clip.stem + "_sub.mp4")
        if sub.exists():
            clip = sub
        if not clip.exists() or not m.get("dialogue"):
            continue
        hooks.append({"story": m.get("story"), "clip": clip,
                      "dialogue": m["dialogue"], "manifest": f.name})
    # ★story 가 비어 있으면(=veo_hook.py 가 편 번호를 안 적었으면) 병합 보드의
    #   hook_line 순서로 매긴다. 종전에는 전부 1편이 되어 세 훅이 같은 자리를 노렸다.
    order = hook_lines_in_order(Path(video_dir).parent)
    if order:
        idx = {_norm(x): i + 1 for i, x in enumerate(order)}
        for h in hooks:
            if not h["story"]:
                h["story"] = idx.get(_norm(h["dialogue"]), 0)
    for n, h in enumerate(sorted(hooks, key=lambda x: x["story"] or 99), start=1):
        if not h["story"]:
            h["story"] = n
    hooks.sort(key=lambda h: h["story"])
    return hooks


def plan_windows(hooks, cues, ep_starts):
    """훅마다 갈아끼울 구간을 정한다 → [{story, clip, start, end, mode, cues}]"""
    starts = dict(ep_starts)
    plans = []
    for h in hooks:
        ep = h["story"]
        t0 = starts.get(ep)
        if t0 is None:
            print(f"⚠ {h['manifest']}: {ep}편 장부 카드를 찾지 못해 건너뜁니다", file=sys.stderr)
            continue
        lead = [c for c in cues if c[1] >= t0 - 1e-3][:MAX_LEAD_CUES]
        target = _norm(h["dialogue"])
        # ★대사가 여러 큐로 쪼개져 있으면 **끝까지** 덮는다 (2026-08-29 실측).
        #   split_long_cues 가 13자로 자르므로 한 줄 대사도 큐 둘이 되는 일이 흔하다.
        #   앞 큐만 덮으면 훅이 대사를 말한 직후 낭독이 뒷조각을 또 읽는다.
        hit = None
        for i, c in enumerate(lead):
            tn = _norm(c[3])
            if not tn or len(tn) < 2 or not target.startswith(tn[:min(len(tn), 4)]):
                continue
            acc, j = "", i
            while j < len(lead):
                nxt = acc + _norm(lead[j][3])
                if nxt not in target and not target.startswith(nxt[:len(target)]):
                    break
                acc, j = nxt, j + 1
                if len(acc) >= len(target) * 0.9:
                    hit = j - 1
                    break
            if hit is not None:
                break
        if hit is not None:
            plans.append({**h, "start": lead[0][1], "end": lead[hit][2],
                          "mode": "교체", "covered": lead[:hit + 1]})
        else:
            plans.append({**h, "start": t0, "end": t0, "mode": "삽입", "covered": []})
    plans.sort(key=lambda p: p["start"])
    return plans


def keyframe_at_or_after(mp4, t, window=30.0):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-show_entries", "frame=best_effort_timestamp_time",
         "-read_intervals", f"{max(0.0, t):.3f}%+{window:.0f}",
         "-of", "csv=p=0", str(mp4)],
        capture_output=True, text=True)
    for line in r.stdout.splitlines():
        try:
            v = float(line.strip().rstrip(","))
        except ValueError:
            continue
        if v >= t - 1e-3:
            return v
    return None


def encode_piece(src, dst, info, ss=None, to=None):
    """조각을 본편 파라미터로 다시 굽는다 (concat -c copy 가 되도록)."""
    v, a = info["video"], info["audio"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    if to is not None:
        cmd += ["-to", f"{to:.3f}"]
    cmd += ["-i", str(src),
            "-vf", f"scale={v['width']}:{v['height']}:flags=lanczos,"
                   f"fps={v['fps']:.6f},format={v['pix_fmt']}",
            "-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "16",
            "-pix_fmt", v["pix_fmt"],
            "-c:a", "aac", "-b:a", "192k",
            "-ar", str(a["sample_rate"] if a else 48000),
            "-ac", str(a["channels"] if a else 2),
            "-video_track_timescale", str(v["timescale"]),
            "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not Path(dst).exists():
        raise RuntimeError(f"조각 인코딩 실패 ({Path(dst).name}): {r.stderr[-400:]}")


def copy_piece(src, dst, ss, to, timescale):
    """스트림 복사 — 시작은 반드시 키프레임, 끝은 아무 데나."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ss:.3f}"]
    if to is not None:
        cmd += ["-to", f"{to:.3f}"]
    cmd += ["-i", str(src), "-c", "copy", "-map", "0:v:0", "-map", "0:a:0",
            "-video_track_timescale", str(timescale),
            "-avoid_negative_ts", "make_zero", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not Path(dst).exists():
        raise RuntimeError(f"복사 실패 ({Path(dst).name}): {r.stderr[-400:]}")


def concat_many(parts, out, workdir, timescale):
    list_path = Path(workdir) / "concat_hook.txt"
    lines = []
    for f in parts:
        posix = str(Path(f).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{posix}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", "-map", "0:v:0", "-map", "0:a:0",
         "-video_track_timescale", str(timescale),
         "-avoid_negative_ts", "make_zero", str(out)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-40:])
        raise RuntimeError(f"concat 실패 (exit {r.returncode})\n{tail}")


def shift_storyboard_cards(video_dir, shifts):
    """장부 카드 start/end 를 누적 Δ만큼 민다. shifts = [(기준시각, Δ)] 시간순."""
    sb = Path(video_dir) / "storyboard.json"
    if not sb.exists():
        return 0
    data = json.loads(sb.read_text(encoding="utf-8"))
    n = 0
    for sc in data.get("scenes", []):
        for key in ("start", "end"):
            t = ts_to_sec(sc.get(key))
            if t is None:
                continue
            # ★경계는 strict — 삽입 지점이 장부 카드 끝과 같을 때(콜드 오픈은 카드 뒤에
            # 들어간다) 포함 비교를 쓰면 카드 자신의 end 가 밀려 카드가 6초 길어진다.
            delta = sum(d for cut, d in shifts if t > cut + 1e-3)
            if delta:
                sc[key] = sec_to_ts(t + delta)
                n += 1
    if n:
        sb.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return n


def shift_meta_chapters(meta_path, shifts):
    p = Path(meta_path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").split("\n")
    changed = []
    pat = re.compile(r"\((\d+):(\d{2})(?::(\d{2}))?\)")
    for i, ln in enumerate(lines):
        m = pat.search(ln)
        if not m:
            continue
        a, b, c = m.groups()
        sec = (int(a) * 3600 + int(b) * 60 + int(c)) if c else (int(a) * 60 + int(b))
        delta = sum(d for cut, d in shifts if sec > cut + 1e-3)
        if not delta:
            continue
        new = sec + delta
        h, rem = divmod(int(round(new)), 3600)
        mi, s = divmod(rem, 60)
        ts = ("%d:%02d:%02d" % (h, mi, s)) if h else ("%d:%02d" % (mi, s))
        lines[i] = ln[:m.start()] + f"({ts})" + ln[m.end():]
        changed.append((m.group(0), f"({ts})"))
    if changed:
        p.write_text("\n".join(lines), encoding="utf-8")
    return changed


def main():
    ap = argparse.ArgumentParser(description="완성본 mp4 편 도입부를 훅 클립으로 교체/삽입")
    ap.add_argument("project_dir")
    ap.add_argument("--mp4", help="대상 mp4 (기본: {P}/output 에서 탐색)")
    ap.add_argument("--srt", help="자막 (기본: {V}/subtitle.srt)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="마커가 있어도 다시 실행")
    ap.add_argument("--cards", action="store_true",
                    help="훅 앞에 장부 카드도 함께 끼운다 (RENDER 앞 insert_chapter_cards 를 건너뛴 경우)")
    ap.add_argument("--gap", type=float, default=3.0, help="장부 카드 길이(초, 기본 3)")
    args = ap.parse_args()

    P = Path(args.project_dir).resolve()
    V = P / "_video"
    srt = Path(args.srt) if args.srt else V / "subtitle.srt"
    if not srt.exists():
        print(f"error: 자막이 없습니다: {srt}", file=sys.stderr)
        return 2

    hooks = load_hooks(V)
    if not hooks:
        print("훅 매니페스트/클립이 없습니다 — 이 단계를 건너뜁니다.")
        return 0
    cues = parse_srt(srt)
    eps = episode_starts(V)
    if not eps:
        eps = episode_starts_from_board(P, V, cues)
        if eps:
            print("장부 카드가 없어 스토리보드에서 편 시작을 유도했습니다.")
    plans = plan_windows(hooks, cues, eps)
    if not plans:
        print("error: 붙일 자리를 정하지 못했습니다.", file=sys.stderr)
        return 2

    print(f"편 시작 시각: " + " / ".join(f"{n}편 {t:.3f}s" for n, t in eps))
    total = 0.0
    for p in plans:
        p["clip_dur"] = probe(p["clip"])["duration"]
        p["card"] = args.gap if args.cards else 0.0
        p["delta"] = p["card"] + p["clip_dur"] - (p["end"] - p["start"])
        total += p["delta"]
        print(f"\n[{p['story']}편] {p['mode']}  «{p['dialogue']}»  ({p['clip'].name} {p['clip_dur']:.2f}s)")
        print(f"    구간 {p['start']:.3f}s ~ {p['end']:.3f}s  →  Δ {p['delta']:+.3f}s")
        for c in p["covered"]:
            print(f"      큐{c[0]:>4} [{c[1]:8.3f}~{c[2]:8.3f}] {c[3]}")
    print(f"\n합계 시프트 {total:+.3f}s")

    if args.dry_run:
        print("--dry-run: 여기까지.")
        return 0

    main_mp4 = Path(args.mp4).resolve() if args.mp4 else find_target_mp4(str(P))
    if not main_mp4 or not Path(main_mp4).exists():
        print(f"error: 대상 mp4를 찾지 못했습니다. --mp4 로 지정하세요.", file=sys.stderr)
        return 2
    main_mp4 = Path(main_mp4)
    out_dir = main_mp4.parent
    marker = out_dir / MARKER_NAME
    if marker.exists() and not args.force:
        print(f"이미 훅이 붙어 있습니다: {marker}  (--force 로 다시 실행)")
        return 0

    info = probe(main_mp4)
    v = info["video"]
    print(f"\n본편: {main_mp4.name}  {info['duration']:.1f}s  "
          f"{v['width']}x{v['height']}/{v['fps']:.3f}fps ts={v['timescale']}")

    titles = []
    if args.cards:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from insert_chapter_cards import episode_titles
        titles = episode_titles(P)
        if len(titles) < len(plans):
            print(f"error: meta.txt 편성표에서 제목을 {len(titles)}개만 읽었습니다(훅 {len(plans)}개).",
                  file=sys.stderr)
            return 2
        print("장부 카드: " + " / ".join(f"{ORDINALS[i]} {t}" for i, t in enumerate(titles[:len(plans)])))

    work = Path(tempfile.mkdtemp(prefix="attach_hook_"))
    parts = []
    try:
        cursor = 0.0            # 지금까지 처리한 원본 시각
        cursor_is_kf = True     # cursor 가 키프레임인가 (복사 가능한가)
        for i, p in enumerate(plans, 1):
            seg = work / f"{i:02d}a_seg.mp4"
            if p["start"] - cursor > 0.05:
                if cursor_is_kf:
                    print(f"  [{i}] 앞 구간 복사 {cursor:.1f}s~{p['start']:.1f}s")
                    copy_piece(main_mp4, seg, cursor, p["start"], v["timescale"])
                else:
                    print(f"  [{i}] 앞 구간 재인코딩 {cursor:.1f}s~{p['start']:.1f}s")
                    encode_piece(main_mp4, seg, info, ss=cursor, to=p["start"])
                parts.append(seg)
            if p.get("card"):
                card = work / f"{i:02d}a2_card.mp4"
                print(f"  [{i}] 장부 카드 굽기 ({p['story']}편 «{titles[p['story'] - 1]}») {p['card']:.1f}s")
                make_card_video(card, info, ORDINALS[p["story"] - 1],
                                titles[p["story"] - 1], p["card"], work)
                parts.append(card)
            hook = work / f"{i:02d}b_hook.mp4"
            print(f"  [{i}] 훅 클립 인코딩 ({p['story']}편)")
            encode_piece(p["clip"], hook, info)
            parts.append(hook)

            kf = keyframe_at_or_after(main_mp4, p["end"])
            if kf is None:
                raise RuntimeError(f"{p['end']:.3f}s 뒤 키프레임을 찾지 못했습니다")
            if kf - p["end"] > 0.02:
                bridge = work / f"{i:02d}c_bridge.mp4"
                print(f"  [{i}] 다리 인코딩 {p['end']:.1f}s~{kf:.1f}s ({kf - p['end']:.2f}s)")
                encode_piece(main_mp4, bridge, info, ss=p["end"], to=kf)
                parts.append(bridge)
            cursor, cursor_is_kf = kf, True

        tail = work / "99_tail.mp4"
        print(f"  마지막 구간 복사 {cursor:.1f}s~끝")
        copy_piece(main_mp4, tail, cursor, None, v["timescale"])
        parts.append(tail)

        out_path = out_dir / (main_mp4.stem + "_hook.mp4")
        print("  결합…")
        concat_many(parts, out_path, work, v["timescale"])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    res = probe(out_path)
    expect = info["duration"] + total
    print(f"\n완성: {out_path}")
    print(f"  길이 {res['duration']:.1f}s (예상 {expect:.1f}s, 차 {res['duration'] - expect:+.1f}s)")

    shifts = [(p["end"], p["delta"]) for p in plans]
    n = shift_storyboard_cards(V, shifts)
    print(f"  storyboard 장부 카드 시각 {n}개 이동")
    for meta in [q for q in (P / "meta.txt", out_dir / "meta.txt") if q.exists()]:
        ch = shift_meta_chapters(meta, shifts)
        if ch:
            print(f"  {meta}: " + ", ".join(f"{a}→{b}" for a, b in ch))

    marker.write_text(json.dumps({
        "source": str(main_mp4), "output": str(out_path),
        "hooks": [{"story": p["story"], "clip": p["clip"].name, "mode": p["mode"],
                   "dialogue": p["dialogue"], "start": p["start"], "end": p["end"],
                   "clip_duration": p["clip_dur"], "delta": p["delta"]} for p in plans],
        "total_shift": total,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  마커: {marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
