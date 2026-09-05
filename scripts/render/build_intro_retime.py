#!/usr/bin/env python3
"""고정 인트로 — clip1의 **긴 연속 구간**을 오디오의 말/쉼에 맞춰 이어 붙인다.

★설계 근거 (2026-08-14, 사용자 피드백 반영)
1차: Veo 통짜 → 우리 녹음과 입이 어긋남(상관 −0.27). Veo는 오디오 입력이 없어 구조적으로 불가.
2차: 정지 이미지 + 입 교체(벤치 방식) → 잘 맞지만 7초간 머리가 멈춰 "입만 뻥끗"해 보임.
3차: 프레임 단위 Viterbi 재배열 → 상관 +0.77이지만 **점프 35회로 머리가 떨림**.
→ **4차(현재): 구간 단위 조립.** 사용자 요구 = *"입모양 씽크는 조금 안 맞더라도 말하는 구간
   영상만 길게 넣고, 대사 없는 구간에만 입을 닫고 있으면 된다."*

방법
- 오디오를 VAD로 **말/쉼** 구간으로 나눈다.
- clip1을 입 벌림으로 **말하는 풀 / 입 다문 풀**로 나눈다.
- 구간마다 해당 풀에서 **연속 재생**으로 채운다. 길이가 모자라면 **핑퐁(정재생↔역재생)**으로
  늘려 컷 없이 잇는다. 구간 경계에서만 점프하며, 직전 프레임과 가장 닮은 프레임에서 시작한다.
→ 점프가 구간 수만큼(≈9회)으로 줄어 머리 떨림이 사라진다.

사용법:
    python3 scripts/render/build_intro_retime.py --channel yadam
    python3 scripts/render/build_intro_retime.py --channel yadam --sil-db -25 --min-sil 0.15
"""
import argparse, json, pathlib, shutil, subprocess, sys
import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOUTH_BOX = (895, 450, 1040, 555)   # clip1(1920x1080) 입 영역


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(r.stderr[-2500:], file=sys.stderr)
        raise SystemExit(f"명령 실패: {' '.join(map(str, cmd))}")
    return r.stdout.strip()


def dur(p):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(p)]))


def runs_of(mask):
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def pingpong(start, length, lo, hi, d=1):
    """[lo,hi) 안에서 start부터 재생하다 끝에 닿으면 방향을 뒤집는다 — 컷 없이 길이를 채운다.
    반환: (인덱스 목록, 다음 위치, 다음 방향) — 이어 재생하려고 상태를 넘긴다."""
    if hi - lo < 2:
        v = max(lo, min(start, hi - 1))
        return [v] * length, v, d
    idx, i = [], max(lo, min(start, hi - 1))
    for _ in range(length):
        idx.append(i)
        i += d
        if i >= hi:
            i, d = hi - 2, -1
        elif i < lo:
            i, d = lo + 1, 1
    return idx, i, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="yadam")
    ap.add_argument("--video", default="clip1.mp4")
    ap.add_argument("--audio", default="인트로.mp3")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--sil-db", type=float, default=-25.0, help="무음 판정 임계(최대 대비 dB)")
    ap.add_argument("--min-sil", type=float, default=0.5,
                    help="이보다 짧은 무음은 무시(초). ★크게 잡을수록 말 구간이 길게 이어져 컷이 준다")
    ap.add_argument("--keep-head", type=float, default=2.91,
                    help="이 시각까지는 clip1을 **자르지 않고 원본 그대로** 쓴다 "
                         "(기본 2.91 = '안녕하세요, 옛뜰이예요' 끝. 사용자 지시 2026-08-14)")
    ap.add_argument("--closed-pct", type=float, default=35.0, help="입 다묾으로 볼 하위 백분위")
    ap.add_argument("--talk-pct", type=float, default=45.0,
                    help="말하는 풀로 볼 입벌림 하위 백분위 문턱(올릴수록 활발한 구간만)")
    ap.add_argument("--open-w", type=float, default=2.5,
                    help="말 구간 시작점을 고를 때 '입이 많이 열린 창'을 선호하는 가중치")
    ap.add_argument("--sil-release", type=float, default=0.25,
                    help="쉼 구간 시작을 이만큼 늦춘다(초). ★말꼬리가 남았는데 화면이 먼저 "
                         "바뀌는 느낌을 막는다(2026-08-15 사용자 지적)")
    ap.add_argument("--sil-jump", action="store_true",
                    help="쉼 구간에서 다문 풀로 점프한다. ★기본은 끄기 — 점프 대신 **입이 닫힐 "
                         "때까지 이어 재생한 뒤 멈춘다**. 사람은 말을 멈추면 화면이 바뀌지 않고 가만히 있는다")
    ap.add_argument("--tail-cut", action="store_true",
                    help="맨 끝 쉼 구간에서도 다문 풀로 점프한다. ★기본은 끄기 — 끝에서 화면이 "
                         "한 번 더 바뀌는 게 어색하다는 사용자 지적(2026-08-15)")
    ap.add_argument("--out", default="intro.mp4")
    args = ap.parse_args()

    D = ROOT / "channels" / args.channel / "assets" / "intro"
    audio, video = D / args.audio, D / args.video
    a_dur, fps = dur(audio), args.fps
    T = int(round(a_dur * fps))

    # 1) 소스 프레임 + 입 벌림
    fr = D / "_frames"
    shutil.rmtree(fr, ignore_errors=True); fr.mkdir()
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", f"fps={fps}", str(fr / "%04d.png")])
    files = sorted(fr.glob("*.png"))
    N = len(files)
    x0, y0, x1, y1 = MOUTH_BOX
    small, op, val = [], [], []
    for f in files:
        im = Image.open(f).convert("RGB")
        small.append(np.asarray(im.resize((64, 36), Image.LANCZOS).convert("L"), np.float32))
        g = np.asarray(im.crop((x0, y0, x1, y1)).convert("L"), np.float32)
        skin = np.percentile(g, 75)
        op.append(float((g < skin - 45).mean()))
        val.append(float((g < 55).mean()) < 0.12)      # 머리카락이 박스에 들면 무효
    small = np.stack(small).reshape(N, -1)
    op, val = np.array(op), np.array(val)

    # 2) 말하는 풀 / 다문 풀 (가장 긴 연속 구간을 하나씩)
    th = np.percentile(op[val], args.closed_pct)
    closed = (op < th) & val
    sil_runs = [r for r in runs_of(closed) if r[1] - r[0] >= 6]
    if not sil_runs:
        raise SystemExit("입 다문 연속 구간을 못 찾았다 — --closed-pct를 올려볼 것")
    SIL = max(sil_runs, key=lambda r: r[1] - r[0])
    # ★말하는 풀 = 입이 **실제로 활발한** 구간만. 유효 구간 전체를 쓰면 clip1의 조용한
    #   뒷부분(4초 이후 벌림 0.05대)까지 끌어다 써서 "말하는데 입을 다물고 있는" 시간이 길어진다
    #   (2026-08-14 사용자 지적 → 08-15 수정).
    w = max(3, int(0.5 * fps))
    roll = np.convolve(np.where(val, op, 0.0), np.ones(w) / w, mode="same")
    talk_th = np.percentile(op[val], args.talk_pct)
    talk_runs = [r for r in runs_of((roll > talk_th) & val) if r[1] - r[0] >= fps // 2]
    if not talk_runs:
        talk_runs = [max(runs_of(val), key=lambda r: r[1] - r[0])]
    TALK = max(talk_runs, key=lambda r: r[1] - r[0])
    print(f"  말하는 풀 평균 벌림 {op[TALK[0]:TALK[1]].mean():.3f} / "
          f"제외한 구간 평균 {op[val & ~((np.arange(N) >= TALK[0]) & (np.arange(N) < TALK[1]))].mean():.3f}")
    print(f"소스 {N}프레임 | 말하는 풀 {TALK[0]/fps:.2f}~{TALK[1]/fps:.2f}s "
          f"| 다문 풀 {SIL[0]/fps:.2f}~{SIL[1]/fps:.2f}s")

    # 3) 오디오 VAD → 말/쉼 구간
    raw_p = D / "_a.raw"
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio),
        "-ac", "1", "-ar", "8000", "-f", "s16le", "-y", str(raw_p)])
    raw = np.fromfile(raw_p, dtype=np.int16).astype(np.float32)
    n = 8000 // fps
    rms = np.array([np.sqrt((raw[i*n:(i+1)*n]**2).mean() + 1e-9) for i in range(T)])
    db = 20*np.log10(rms/(rms.max()+1e-9) + 1e-9)
    sil = db < args.sil_db
    for a, b in runs_of(sil):                       # 너무 짧은 무음은 말로 되돌린다
        if (b - a) < args.min_sil * fps:
            sil[a:b] = False
    rel = int(round(args.sil_release * fps))
    if rel > 0:                                      # 무음 구간의 앞머리를 말로 되돌린다
        sr = runs_of(sil)
        for i, (a, b) in enumerate(sr):
            # ★맨 끝 무음에는 지연을 걸지 않는다 — 입을 닫을 시간이 필요하다(2026-08-15)
            if i == len(sr) - 1 and b >= T - 2:
                continue
            sil[a:min(a + rel, b)] = False
    head_f = min(int(round(args.keep_head * fps)), T, N)
    sil[:head_f] = False                             # 도입부는 통짜로 쓰므로 구간을 나누지 않는다
    spans, i = [], 0
    while i < T:
        j = i
        while j < T and sil[j] == sil[i]:
            j += 1
        spans.append((i, j, bool(sil[i])))
        i = j
    # 도입부를 독립 구간으로 떼어낸다 (원본 그대로 재생)
    if head_f > 0:
        merged = []
        for a, b, isl in spans:
            if b <= head_f:
                continue
            merged.append((max(a, head_f), b, isl))
        spans = [(0, head_f, False)] + [s for s in merged if s[1] > s[0]]
    # ★짧은 조각은 앞 구간에 흡수한다 — 도입부를 떼면서 잘린 0.1초짜리 쉼 같은 것이
    #   불필요한 점프를 2회씩 만든다(2026-08-14 실측).
    cleaned = []
    for a, b, isl in spans:
        if cleaned and (b - a) < args.min_sil * fps:
            pa, pb, pisl = cleaned[-1]
            cleaned[-1] = (pa, b, pisl)
        else:
            cleaned.append((a, b, isl))
    spans = cleaned
    print(f"오디오 구간 {len(spans)}개 "
          f"(말 {sum(b-a for a,b,s in spans if not s)/fps:.2f}초 / 쉼 {sum(b-a for a,b,s in spans if s)/fps:.2f}초)")

    # 4) 구간마다 연속 재생으로 채운다.
    #    ★풀마다 '재생 위치'를 이어 간다 — 구간이 바뀔 때마다 처음으로 되감으면 같은 장면이
    #      곧바로 반복돼 어색하다(2026-08-15 사용자 지적: "도입부 영상을 바로 다음에 또 붙였다").
    path, prev = [], None
    head = {False: None, True: None}          # 풀별 (위치, 방향)
    for k, (a, b, is_sil) in enumerate(spans):
        # ★맨 끝 쉼 구간은 점프하지 않고 말하는 풀을 그대로 이어 간다 — 마지막에 화면이
        #   한 번 더 바뀌면 눈에 띈다. 어차피 소리가 끝나는 구간이라 입 상태는 덜 중요하다.
        tail_keep = (not args.tail_cut) and is_sil and k == len(spans) - 1
        pool_sil = is_sil and not tail_keep
        lo, hi = SIL if pool_sil else TALK
        L = b - a
        if prev is None:
            start, direction = 0, 1                  # 도입부 = clip1 원본 처음부터 통짜
        elif head[pool_sil] is not None:
            start, direction = head[pool_sil]        # ★같은 풀은 재생 위치를 이어받는다
        else:
            cand = np.arange(lo, hi)
            d = np.sqrt(((small[cand] - small[prev])**2).mean(axis=1))
            d = d / (d.max() + 1e-9)
            # ★앞으로 재생할 여유가 없는 시작점에 벌점 — 없으면 곧장 역재생으로 넘어가
            #   머리가 거꾸로 움직여 어색해진다(2026-08-14 실측).
            short = np.clip((L - (hi - cand)) / max(L, 1), 0, 1)
            cost = d + 1.2 * short
            if not pool_sil:
                seqs = [pingpong(int(c), L, lo, hi)[0] for c in cand]
                # ★그 시작점으로 L프레임을 재생했을 때 입이 얼마나 열려 있나 — 높을수록 좋다
                ow = np.array([op[q].mean() for q in seqs])
                ow = ow / (ow.max() + 1e-9)
                cost = cost - args.open_w * ow
                # ★뒤에 쉼이 오면 **끝 프레임이 입을 다물고 있는** 시작점을 고른다.
                #   그래야 말이 끝나며 입이 자연스럽게 닫힌다(2026-08-15 사용자 지적).
                if k + 1 < len(spans) and spans[k + 1][2]:
                    ew = np.array([op[q[-1]] for q in seqs])
                    cost = cost + 1.0 * ew / (ew.max() + 1e-9)
            start, direction = int(cand[int(cost.argmin())]), 1
        if prev is None:                             # 도입부는 자르지 않고 순방향 그대로
            seg = list(range(0, min(L, N)))
            seg += [seg[-1]] * (L - len(seg))
            head[False] = (min(L, N), 1)             # 다음 말 구간은 여기서 이어받는다
        elif is_sil and not args.sil_jump:
            # ★쉼 = 점프하지 않는다. 지금 재생 위치에서 **가장 가까운 '입 다문' 프레임까지
            #   이어 재생**한 뒤 거기서 멈춘다. 말이 끝나고 가만히 있는 사람의 움직임이 된다.
            #   문턱으로 걷다 멈추면 반쯤 벌린 입에서 굳는다(2026-08-15 실측 0.08) → 최솟값을 찾는다.
            pos, d2 = head[False] if head[False] else (TALK[0], 1)
            lo2, hi2 = TALK
            pos = min(max(pos, lo2), hi2 - 1)
            reach = min(L, int(0.8 * fps))
            cand = np.arange(max(lo2, pos - reach), min(hi2, pos + reach + 1))
            score = op[cand] / (op[cand].max() + 1e-9) + 0.15 * np.abs(cand - pos) / max(reach, 1)
            target = int(cand[int(score.argmin())])
            step = 1 if target >= pos else -1
            walk = list(range(pos, target + step, step))[:L]
            seg = walk + [target] * (L - len(walk))
            head[False] = (target, step if step != 0 else d2)
        else:
            seg, npos, ndir = pingpong(start, L, lo, hi, direction)
            head[pool_sil] = (npos, ndir)
        path += seg
        prev = seg[-1]
        tag = ('도입(원본)' if k == 0 else
               ('쉼(멈춤)' if is_sil and not args.sil_jump else
                ('끝(이어감)' if tail_keep else ('쉼 ' if is_sil else '말 '))))
        print(f"  {a/fps:5.2f}~{b/fps:5.2f}s {tag:9} {L/fps:.2f}초 ← 소스 {seg[0]/fps:.2f}s부터")
    path = np.array(path[:T], np.int32)
    jumps = int(sum(1 for t in range(1, len(path)) if abs(path[t]-path[t-1]) > 1))
    rev = int(sum(1 for t in range(1, len(path)) if path[t] == path[t-1] - 1))
    print(f"점프 {jumps}회 | 역재생 {rev}프레임 ({rev/len(path)*100:.0f}%)")

    # 5) 인코딩
    seq = D / "_seq"
    shutil.rmtree(seq, ignore_errors=True); seq.mkdir()
    for t, idx in enumerate(path):
        shutil.copy(files[idx], seq / f"{t:04d}.png")
    out = D / args.out
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-framerate", str(fps),
        "-i", str(seq / "%04d.png"), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-t", f"{a_dur:.3f}",
        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-y", str(out)])

    # 검증: 쉼 구간에 입이 닫혀 있나
    op_path = op[path]
    sil_mask = np.array([s for a, b, s in spans for _ in range(b-a)])[:len(path)]
    print(f"\n검증 — 입 벌림 평균: 말 구간 {op_path[~sil_mask].mean():.3f} / "
          f"쉼 구간 {op_path[sil_mask].mean():.3f} (쉼이 작을수록 좋음)")

    raw_p.unlink(missing_ok=True)
    shutil.rmtree(seq); shutil.rmtree(fr)
    (D / "intro.json").write_text(json.dumps({
        "method": "clip1 구간 조립 — 말 구간은 긴 연속 재생, 쉼 구간은 입 다문 구간",
        "duration": round(dur(out), 3), "audio": args.audio, "audio_duration": round(a_dur, 3),
        "source": args.video, "fps": fps, "jumps": jumps, "reverse_frames": rev,
        "talk_pool_sec": [round(TALK[0]/fps, 2), round(TALK[1]/fps, 2)],
        "silent_pool_sec": [round(SIL[0]/fps, 2), round(SIL[1]/fps, 2)],
        "open_mean": {"speech": round(float(op_path[~sil_mask].mean()), 3),
                      "silence": round(float(op_path[sil_mask].mean()), 3)},
        "params": {"sil_db": args.sil_db, "min_sil": args.min_sil, "keep_head": args.keep_head,
                   "sil_release": args.sil_release, "sil_jump": args.sil_jump,
                   "closed_pct": args.closed_pct, "mouth_box": list(MOUTH_BOX)},
        "why": "프레임 단위 재배열은 립싱크는 좋지만 점프가 잦아 머리가 떨렸다. 구간 단위로 길게 붙이면 "
               "움직임이 자연스럽고, 쉼 구간에만 입을 닫으면 충분하다(사용자 지시 2026-08-14).",
        "note": "자막 없음 — CapCut에서 입힌다.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out}  {dur(out):.2f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
