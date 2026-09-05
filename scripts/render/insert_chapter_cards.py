#!/usr/bin/env python3
"""편 사이 장부(章) 카드 삽입 — 옴니버스 전용, SCENE_TIMING 뒤 · RENDER 앞.

옴니버스는 편이 바뀌어도 화면과 소리가 끊김 없이 이어져, 자다 깬 시청자가 "지금 몇 번째
이야기인지" 알 방법이 없다. 편 경계마다 3초짜리 장부 카드를 끼우고 그동안 낭독을 멈춘다.

★카드는 **1편 앞에도 붙는다** (사용자 지시 2026-08-20). 종전에는 첫 편을 건너뛰어
  2편부터만 제목이 떴는데, 그러면 1편만 이름 없이 시작해 형식이 어긋난다.
  결합 순서: 고정 인트로 → 1편 장부 카드 → 1편 본문 → 2편 카드 → …
  편이 N개면 카드도 N장이고, 전체 길이는 3초 x N 만큼 늘어난다.

  audio.mp3     경계마다 무음 삽입 (진짜 챕터 브레이크 — 카드가 뜨는 동안 소리도 쉰다)
  subtitle.srt  경계 뒤 큐를 누적 시프트
  storyboard    경계마다 카드 씬 삽입(start/end 명시 → capcut_export 어댑터가 건너뛴다)

★오디오를 건드리므로 원본을 audio.orig.mp3 등으로 백업하고, 재실행 시 백업본을 원본으로
  삼는다. 몇 번을 돌려도 결과가 같다(멱등).

★카드 이미지는 AI가 아니라 PIL로 그린다 — 씬 파이프라인은 한글 텍스트를 금지하고
  (style.json negative), AI에게 한글을 맡기면 글자가 깨진다.

Usage:
    python3 scripts/render/insert_chapter_cards.py {P}
    python3 scripts/render/insert_chapter_cards.py {P} --gap 3 --titles "눈먼 계모,밤마다..."
    python3 scripts/render/insert_chapter_cards.py {P} --verify      # 싱크 검증만
    python3 scripts/render/insert_chapter_cards.py {P} --restore     # 원본으로 되돌린다

편 경계는 {V}/storyboard.json 의 씬 중 소스 storyboard.json 에서 act="story{N}" 인 것을
찾아 자동 판별한다.
"""

import argparse
import io
import json
import os
import pathlib
import subprocess
import sys

ORDINALS = ["", "첫 번째", "두 번째", "세 번째", "네 번째", "다섯 번째",
            "여섯 번째", "일곱 번째", "여덟 번째", "아홉 번째"]
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\batang.ttc",      # 바탕 — 야담 톤에 가장 맞다
    r"C:\Windows\Fonts\malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf",
]
BG = (26, 30, 38)
FG = (238, 232, 220)
SUB = (150, 158, 172)


def s2f(s):
    h, m, x = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(x.replace(",", "."))


def f2s(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def parse_srt(p):
    out = []
    for blk in io.open(p, encoding="utf-8-sig").read().strip().split("\n\n"):
        L = [x for x in blk.split("\n") if x.strip()]
        if len(L) >= 3 and "-->" in L[1]:
            a, b = L[1].split(" --> ")
            out.append([int(L[0]), s2f(a), s2f(b), "\n".join(L[2:])])
    return out


def pick_font():
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    raise SystemExit("한글 폰트를 찾지 못했습니다 — FONT_CANDIDATES 에 경로를 추가하세요")


# ★카드는 반드시 정확한 16:9 로 굽는다 (2026-08-29).
# 예전 기본값 1344x768 은 비율이 1.750 이라 16:9(1.7778)보다 **좁아서**, CapCut 캔버스에
# 얹으면 좌우 양끝에 배경색이 닿지 않는 빈 띠(필러박스)가 남았다. 씬 이미지는 1376x768
# (1.792)로 도리어 약간 넓어 같은 문제가 없었기에 카드에서만 티가 났다.
# 렌더 목표 해상도와 같은 1920x1080 으로 구우면 캔버스를 정확히 채우고 글자도 선명하다.
CARD_SIZE = (1920, 1080)


def make_card(path, ordinal, title, size=CARD_SIZE):
    from PIL import Image, ImageDraw, ImageFont
    W, H = size
    if abs(W / H - 16 / 9) > 0.002:
        print("[경고] 카드 비율 %.4f — 16:9(1.7778)가 아니라 좌우/상하에 빈 띠가 생깁니다" % (W / H))
    font = pick_font()
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    rule = max(2, round(H / 384))                      # 768 기준 2px 를 해상도에 맞춰 키운다
    d.line([(W * 0.30, H * 0.34), (W * 0.70, H * 0.34)], fill=(70, 78, 92), width=rule)
    d.line([(W * 0.30, H * 0.66), (W * 0.70, H * 0.66)], fill=(70, 78, 92), width=rule)

    def center(y, text, px, fill):
        f = ImageFont.truetype(font, px)
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        d.text(((W - (r - l)) / 2 - l, y), text, font=f, fill=fill)

    # 글자 크기는 높이에 비례시킨다 (768 기준 46 / 84 px 과 같은 비율)
    center(H * 0.40, ordinal + " 이야기", round(H * 0.0599), SUB)
    center(H * 0.50, title, round(H * 0.1094), FG)
    im.save(path)


def episode_titles(P):
    """meta.txt 제작 메모에서 편 제목을 읽는다."""
    p = P / "meta.txt"
    if not p.exists():
        return []
    out = []
    for ln in io.open(p, encoding="utf-8").read().split("\n"):
        ln = ln.strip()
        if len(ln) > 3 and ln[0].isdigit() and "편 " in ln and "|" in ln:
            out.append(ln.split("편 ", 1)[1].split("|")[0].strip())
    return out


def boundaries(P, V):
    """소스 storyboard.json 의 act='story{N}' 로 편 경계 씬 id를 찾는다.

    ★1편 앞에도 카드를 넣는다 (사용자 지시 2026-08-20). 종전에는 ids[1:] 로 첫 편을
    건너뛰었는데, 그러면 2·3편만 제목을 얻고 1편은 이름 없이 시작해 형식이 어긋난다.
    영상 맨 앞은 고정 인트로 → 1편 장부 카드 → 1편 본문 순서가 된다.
    """
    src = json.load(io.open(P / "storyboard.json", encoding="utf-8"))["scenes"]
    ids, seen = [], set()
    for s in src:                      # 편마다 **첫 씬 하나만** — 전 씬을 담으면 카드가 150장이 된다
        act = str(s.get("act", ""))
        if act.startswith("story") and act not in seen:
            seen.add(act)
            ids.append(s["id"])
    return ids


def restore(V):
    n = 0
    for a, b in (("audio.orig.mp3", "audio.mp3"), ("subtitle.orig.srt", "subtitle.srt"),
                 ("storyboard.orig.json", "storyboard.json")):
        if (V / a).exists():
            (V / b).write_bytes((V / a).read_bytes()); n += 1
    print("원본 복원 %d개" % n)


def verify(V, gap):
    """무음 구간과 자막 싱크를 실측한다."""
    try:
        import numpy as np
    except ImportError:
        print("numpy 없음 — 검증 건너뜀"); return 0
    sb = json.load(io.open(V / "storyboard.json", encoding="utf-8"))["scenes"]
    cards = [s for s in sb if s.get("is_card")]
    cues = parse_srt(V / "subtitle.srt")

    def rms(t0, t1):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(max(0, t0)), "-t", str(t1 - t0),
                            "-i", str(V / "audio.mp3"), "-ac", "1", "-ar", "8000",
                            "-f", "s16le", "-"], capture_output=True)
        a = np.frombuffer(r.stdout, dtype=np.int16).astype(float)
        return 0.0 if len(a) == 0 else float(np.sqrt((a ** 2).mean()))

    bad = 0
    print("장부 구간이 무음인지:")
    for c in cards:
        st = s2f(c["start"])
        before, mid, after = rms(st - 2, st - 0.3), rms(st + 0.3, st + gap - 0.3), rms(st + gap + 0.3, st + gap + 2)
        ok = mid < 30 and before > 200 and after > 200
        bad += (not ok)
        print("  %-10s 직전%6.0f → 장부%5.1f → 직후%6.0f  %s"
              % (c["description"], before, mid, after, "OK" if ok else "★확인"))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(V / "audio.mp3")],
                               capture_output=True, text=True).stdout)
    diff = dur - cues[-1][2]
    print("자막 끝 %.1f초 / 오디오 %.1f초 (차 %.1f초) %s"
          % (cues[-1][2], dur, diff, "OK" if abs(diff) < 2 else "★확인"))
    picks = [0, len(cues) // 3, len(cues) // 2, 2 * len(cues) // 3, len(cues) - 1]
    silent = [n for n, a, b, t in (cues[i] for i in picks) if rms(a + 0.05, min(b, a + 1.2)) < 150]
    print("자막 표본 %d개 중 무음 %d개 %s" % (len(picks), len(silent), "OK" if not silent else "★확인"))
    return bad + len(silent) + (abs(diff) >= 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir", type=pathlib.Path)
    ap.add_argument("--gap", type=float, default=3.0, help="장부 길이(초, 기본 3)")
    ap.add_argument("--titles", default="", help="2편부터의 제목을 쉼표로. 생략 시 meta.txt에서 읽는다")
    ap.add_argument("--verify", action="store_true", help="싱크 검증만 하고 끝낸다")
    ap.add_argument("--restore", action="store_true", help="원본으로 되돌린다")
    a = ap.parse_args()
    P = a.project_dir
    V = P / "_video"
    if a.restore:
        restore(V); return 0
    if a.verify:
        return 1 if verify(V, a.gap) else 0

    # 재실행해도 결과가 같도록, 원본이 있으면 그것을 입력으로 쓴다
    for name in ("audio.mp3", "subtitle.srt", "storyboard.json"):
        orig = V / name.replace(".", ".orig.", 1)
        if not orig.exists():
            orig.write_bytes((V / name).read_bytes())

    rv = json.load(io.open(V / "storyboard.orig.json", encoding="utf-8"))["scenes"]
    cues = parse_srt(V / "subtitle.orig.srt")
    cue_start = {c[0]: c[1] for c in cues}
    bounds = boundaries(P, V)
    if not bounds:
        print("편 경계를 찾지 못했습니다 (storyboard.json 의 act='story{N}')"); return 1

    titles = [t for t in a.titles.split(",") if t.strip()] or episode_titles(P)
    if len(titles) < len(bounds):
        print("제목이 모자랍니다 (%d개 필요, %d개)" % (len(bounds), len(titles))); return 1

    marks = [cue_start[next(s for s in rv if s["id"] == b)["subtitle_range"][0]] for b in bounds]
    print("편 경계:", ["%.1f분" % (m / 60) for m in marks])

    # ── 1) 오디오에 무음 삽입
    segs, prev = [], 0.0
    for m in marks:
        segs.append((prev, m)); prev = m
    segs.append((prev, None))
    # ★1편 경계는 0초라 첫 구간이 길이 0이 된다 — 빈 atrim 을 concat 에 넣으면 ffmpeg 이
    #   입력 수를 못 맞춰 죽는다. 길이 0 구간은 건너뛰고 무음만 넣는다(2026-08-20).
    f, order, n_in = [], "", 0
    for i, (s0, s1) in enumerate(segs):
        if s1 is not None and s1 - s0 < 1e-3:
            pass                                   # 길이 0 — 오디오 조각 없이 무음만
        else:
            rng = "atrim=start=%.3f" % s0 + (":end=%.3f" % s1 if s1 is not None else "")
            f.append("[0:a]%s,asetpts=PTS-STARTPTS[a%d]" % (rng, i))
            order += "[a%d]" % i
            n_in += 1
        if i < len(segs) - 1:
            f.append("anullsrc=r=44100:cl=stereo,atrim=duration=%s,asetpts=PTS-STARTPTS[s%d]"
                     % (a.gap, i))
            order += "[s%d]" % i
            n_in += 1
    f.append("%sconcat=n=%d:v=0:a=1[out]" % (order, n_in))
    r = subprocess.run(["ffmpeg", "-y", "-i", str(V / "audio.orig.mp3"),
                        "-filter_complex", ";".join(f), "-map", "[out]",
                        "-c:a", "libmp3lame", "-q:a", "2", str(V / "audio.mp3")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1200:]); return 1
    print("오디오: 무음 %d곳 x %.0f초 삽입" % (len(marks), a.gap))

    def shift(t):
        return t + a.gap * sum(1 for m in marks if t >= m - 1e-6)

    # ── 2) 자막 시프트
    # ★end 를 따로 shift 하면 안 된다 — 경계 직전 마지막 큐는 end 가 경계 시각과 같아서
    #   한 번 더 밀리고, 그 자막이 장부 카드 위에 3초간 그대로 남는다(2026-08-17 실측).
    #   한 큐는 start 기준으로 계산한 양만큼 통째로 민다.
    out = []
    for i, (n, s0, s1, txt) in enumerate(cues, 1):
        d = shift(s0) - s0
        out.append("%d\n%s --> %s\n%s\n" % (i, f2s(s0 + d), f2s(s1 + d), txt))
    io.open(V / "subtitle.srt", "w", encoding="utf-8").write("\n".join(out))
    print("자막 %d큐 시프트" % len(cues))

    # ── 3) 카드 이미지 + 스토리보드 삽입
    cards_dir = P / "cards"
    cards_dir.mkdir(exist_ok=True)
    new, sid = [], 1
    for s in rv:
        if s["id"] in bounds:
            k = bounds.index(s["id"])
            n = k + 1                              # ★1편 카드부터 센다(2026-08-20)
            make_card(cards_dir / ("card_%02d.png" % n), ORDINALS[n], titles[k])
            st = shift(cue_start[s["subtitle_range"][0]]) - a.gap
            new.append({"id": sid, "image_path": "../cards/card_%02d.png" % n,
                        "description": "%d편 장부" % n, "is_card": True,
                        "start": f2s(st), "end": f2s(st + a.gap)})
            sid += 1
        s = dict(s); s["id"] = sid
        new.append(s); sid += 1
    json.dump({"scenes": new}, io.open(V / "storyboard.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("스토리보드: %d씬 (장부 %d장)" % (len(new), len(bounds)))
    print()
    return 1 if verify(V, a.gap) else 0


if __name__ == "__main__":
    sys.exit(main())
