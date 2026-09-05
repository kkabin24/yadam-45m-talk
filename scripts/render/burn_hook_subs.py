#!/usr/bin/env python3
"""훅 클립에 **본편과 똑같은 자막**을 구워 넣는다 (VEO_HOOK 다음, RENDER 앞).

왜 필요한가
    훅 클립은 CapCut 타임라인에 올리지 않는다(Windows CapCut이 동영상 클립에서 죽는다 — SKILL+ §9).
    그래서 CapCut이 입히는 자막이 훅 구간에는 **붙지 않는다.** 완성본 앞부분만 자막이 없으면
    영상이 자막 없이 시작했다가 갑자기 생긴다. 여기서 미리 구워 둔다.

무엇을 구우나
    ★새로 쓰지 않고 **본편 자막(`{V}/subtitle.srt`)에서 그 대사 큐를 그대로 가져온다.**
    split_long_cues 가 13자로 나눠 놓은 그 조각을 그대로 쓰므로 줄바꿈·표기가 본편과 같다.
    스타일도 `burn_intro_subs.py` 를 그대로 불러 쓴다 — 맑은 고딕 Bold, 글자 높이 107px,
    검은 외곽선 8px, 바닥에서 57px (01편 완성본 프레임 실측값).

타이밍
    클립은 앞 2초쯤 조용히 있다가 말한다(pjn 프롬프트 규약). 그 시각을 추측하지 않고
    **ffmpeg silencedetect 로 실제 발화 구간을 찾아** 그 안에 큐를 음절 비례로 배분한다.

사용법:
    python3 scripts/render/burn_hook_subs.py <project_dir> [--channel yadam] [--dry-run]
    → {V}/veo_hook_sceneNN_sub.mp4  (원본은 건드리지 않는다)
"""
import argparse
import json
import os
import shutil
import tempfile
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]


def norm(s):
    return re.sub(r'[\s"“”.,!?…]', "", s or "")


def parse_srt(path):
    out = []
    txt = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    for blk in txt.strip().split("\n\n"):
        ls = [l for l in blk.strip().split("\n") if l.strip()]
        if len(ls) < 3 or "-->" not in ls[1]:
            continue
        a, _, b = ls[1].partition("-->")
        out.append([ls[0].strip(), a.strip(), b.strip(), "\n".join(ls[2:]).strip()])
    return out


def to_sec(ts):
    h, m, rest = ts.replace(".", ",").split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def to_ts(sec):
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%02d:%02d:%02d,%03d" % (h, m, int(s), round((s - int(s)) * 1000))


SR = 16000
WIN_SEC = 0.05



def _norm_line(s):
    """대사 비교용 — 따옴표·문장부호·공백을 무시한다."""
    return re.sub(r"[^가-힣0-9a-zA-Z]", "", s or "")


def speech_window_whisper(clip, expect=""):
    """whisper 단어 타임스탬프로 발화 구간 (start, end). 못 쓰면 None.

    ★RMS 포락선으로는 부족하다 (2026-08-31 실측, 사용자 재지적 "자막이 매칭이 안 돼").
      훅 클립은 배경 앰비언스를 모델이 같이 만드는데 — 갯벌 편은 바람·물소리가 깔린다 —
      그 소리가 RMS 임계를 넘어 **말이 시작되기 1.71초 전**을 발화 시작으로 잡았다.
      10편 실측: RMS 0.45s vs whisper 2.16s. 자막이 그만큼 먼저 떴다.
      훅은 8초짜리라 whisper 가 몇 초면 끝난다 — 정확도를 택한다.
      (split_long_cues 의 whisper 금지는 두 시간짜리 낭독 얘기라 여기와 무관하다.)
    """
    exe = shutil.which("whisper")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        if subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1",
                           "-ar", "16000", wav, "-y"]).returncode:
            return None
        if subprocess.run([exe, wav, "--model", "small", "--language", "ko",
                           "--word_timestamps", "True", "--output_format", "json",
                           "--output_dir", td, "--fp16", "False"],
                          capture_output=True).returncode:
            return None
        js = os.path.join(td, "a.json")
        if not os.path.exists(js):
            return None
        try:
            d = json.loads(open(js, encoding="utf-8").read())
        except (OSError, ValueError):
            return None
    ws = [w for seg in d.get("segments", []) for w in seg.get("words", [])]
    if not ws:
        return None
    # ★[2026-09-04] 첫 낱말이 대사의 첫 낱말이 아닐 수 있다 — 그러면 자막이 그만큼 먼저 뜬다.
    #   13편 실측: 2편 클립이 대본대로 "픽 웃고" 를 연기해 웃음소리("하하하")가 앞에 붙었고,
    #   3편은 머뭇거림("음...")이 붙었다. 프롬프트로 막으면 대본에 있는 연기까지 막히므로
    #   (금지어는 오히려 그 소리를 부른다 — 램프 사고와 같은 계열) **자막을 대사에 맞춘다.**
    #   인물 이름이 새어 나온 경우(veo-hook-korean-name-leak)도 같은 방법으로 건너뛴다.
    def _k(s):
        return re.sub(r"[^가-힣0-9a-zA-Z]", "", s or "")
    head = _k(expect)
    if head:
        for i, w in enumerate(ws):
            kw = _k(w.get("word", ""))
            if kw and (head.startswith(kw[:2]) or kw[:2] and kw[:2] in head[:6]):
                return float(ws[i]["start"]), float(ws[-1]["end"])
    return float(ws[0]["start"]), float(ws[-1]["end"])

def speech_window(clip):
    """클립의 실제 발화 구간 (start, end) — RMS 포락선으로 잰다.

    ★2026-08-29 전면 교체 (사용자 지적 "veo hook에서 자막이랑 매칭이 안 되는 문제").
      종전에는 ffmpeg `silencedetect` 의 **고정 dB 임계**(-40dB)를 썼는데,
      i2v 클립은 배경 앰비언스가 깔려 있어 그 임계를 **말이 아닌 소리도 넘는다.**
      08편 실측 — 실제 발화는 씬01 5.00s / 씬64 5.25s 에 시작하는데 -40dB 검출은
      2.00s / 1.88s 를 내놨다. 자막이 **3초 넘게 먼저 떴다.**

      대신 50ms RMS 포락선을 만들어 **하위 20%를 환경음 바닥**으로 잡고,
      바닥의 4배(또는 바닥~피크의 18% 지점) 위로 0.2초 이상 유지되는 구간만 발화로 본다.
      절대 dB가 아니라 그 클립 자신의 분포를 기준으로 하므로 테이크마다 달라도 맞는다.

    ★왜 매번 재야 하나 — 훅 클립은 **음성을 모델이 스스로 만든다.** 프롬프트에
      `[0s-2s] 앰비언스 / [2s-8s] 발화` 라고 적어도 지키지 않는다(08편 세 클립 모두
      마지막 3초쯤에 말했다). 그러므로 말 시작 시각은 **클립에서 재는 수밖에 없다.**
    """
    import array
    import math
    dur = 8.0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(clip)], capture_output=True, text=True)
        dur = float(r.stdout.strip())
    except Exception:
        pass
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1",
                        "-ar", str(SR), "-f", "s16le", "-"], capture_output=True)
    a = array.array("h")
    a.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    win = int(SR * WIN_SEC)
    vals = []
    for i in range(0, len(a) - win, win):
        s = 0
        for v in a[i:i + win]:
            s += v * v
        vals.append(math.sqrt(s / win))
    if len(vals) < 10:
        return 2.0, dur
    srt = sorted(vals)
    floor = srt[len(srt) // 5] or 1.0
    peak = srt[int(len(srt) * 0.95)]
    thr = max(floor * 4.0, floor + (peak - floor) * 0.18)
    hold = 4                                   # 0.2초 이상 유지
    st = en = None
    for i, v in enumerate(vals):
        if v > thr and all(vals[j] > thr for j in range(i, min(i + hold, len(vals)))):
            st = i
            break
    for i in range(len(vals) - 1, -1, -1):
        if vals[i] > thr and all(vals[j] > thr for j in range(max(0, i - hold), i + 1)):
            en = i
            break
    if st is None or en is None or (en - st) * WIN_SEC < 0.5:
        return 2.0, dur                        # 못 재면 안전한 기본값
    return max(0.0, st * WIN_SEC - 0.15), min(dur, (en + 1) * WIN_SEC + 0.25)


def find_cues(cues, line):
    """본편 자막에서 그 대사를 이루는 연속 큐들. 없으면 [].

    ★시작 큐가 대사의 **앞부분**이어야 한다 (2026-08-29). 종전에는 누적 문자열이
    대사를 '포함하기만' 하면 잡아서, 앞 편 마지막 큐들까지 함께 물었다
    (2편 훅에 「그렇게 두 사람은 오래도록 이웃으로 살았습니다」가 붙어 나왔다).
    """
    target = norm(line)
    if not target:
        return []
    for i in range(len(cues)):
        head = norm(cues[i][3])
        if not head or not target.startswith(head[:max(2, min(len(head), 6))]):
            continue
        acc = ""
        for j in range(i, min(i + 5, len(cues))):
            nxt = acc + norm(cues[j][3])
            if not target.startswith(nxt[:len(target)]) and nxt not in target:
                break                      # 대사 밖으로 새면 그만
            acc = nxt
            if len(acc) >= len(target) * 0.9:
                return cues[i:j + 1]
    return []


def main():
    ap = argparse.ArgumentParser(description="훅 클립에 본편과 동일한 자막을 번인")
    ap.add_argument("project_dir")
    ap.add_argument("--channel", default="yadam")
    ap.add_argument("--video-subdir", default="_video")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--crf", type=int, default=16)
    args = ap.parse_args()

    P = pathlib.Path(args.project_dir).resolve()
    V = P / args.video_subdir
    srt_path = V / "subtitle.srt"
    if not srt_path.exists():
        print("error: 본편 자막이 없습니다: %s\n  ingest_vrew → split_long_cues 를 먼저 돌리세요." % srt_path,
              file=sys.stderr)
        return 2
    board = json.loads((P / "storyboard.json").read_text(encoding="utf-8"))
    scenes = board["scenes"] if isinstance(board, dict) else board
    hooks = [s for s in scenes if s.get("hook_line")]
    if not hooks:
        print("error: storyboard에 hook_line이 있는 씬이 없습니다.", file=sys.stderr)
        return 2

    cues = parse_srt(srt_path)
    print("본편 자막 %d큐 · 훅 %d개" % (len(cues), len(hooks)))
    rc = 0
    for sc in hooks:
        # ★클립 파일명은 veo_hook.py 가 **렌더 보드**(장부 카드가 끼워져 id가 밀린 {V}/storyboard.json)
        #   기준으로 짓는데, 여기 hooks 는 **병합 보드**({P}/storyboard.json, 카드 없음) 기준이라
        #   편마다 id 가 어긋난다 — 10편 실측: 2편 첫 씬이 병합 68 / 렌더 70, 3편이 120 / 123.
        #   이름으로 맞히려 하지 말고 매니페스트의 dialogue 로 짝을 찾는다 (2026-08-30).
        clip = V / ("veo_hook_scene%02d.mp4" % sc["id"])
        if not clip.exists():
            want = _norm_line(sc["hook_line"])
            for mf in sorted(V.glob("veo_hook*.json")):
                try:
                    m = json.loads(mf.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if _norm_line(m.get("dialogue", "")) == want and m.get("output"):
                    cand = V / m["output"]
                    if cand.exists():
                        clip = cand
                        break
        if not clip.exists():
            print("  ! 씬%d 클립 없음: %s" % (sc["id"], clip.name)); rc = 1; continue
        got = find_cues(cues, sc["hook_line"])
        if not got:
            print("  ! 씬%d 자막에서 대사를 못 찾음: %r" % (sc["id"], sc["hook_line"])); rc = 1; continue
        st, en = speech_window_whisper(clip, sc["hook_line"]) or speech_window(clip)
        # 큐들을 발화 구간 안에 음절 비례로 배분
        lens = [max(1, len(norm(c[3]))) for c in got]
        tot = sum(lens)
        span = en - st
        lines, t = [], st
        for k, (c, L) in enumerate(zip(got, lens), start=1):
            d = span * L / tot
            lines.append("%d\n%s --> %s\n%s\n" % (k, to_ts(t), to_ts(t + d), c[3]))
            t += d
        out_srt = clip.with_suffix(".srt")
        out_srt.write_text("\n".join(lines), encoding="utf-8")
        out_mp4 = clip.with_name(clip.stem + "_sub.mp4")
        print("  씬%-4d %-24s 큐 %d개 · 발화 %.2f~%.2fs → %s"
              % (sc["id"], repr(sc["hook_line"]), len(got), st, en, out_mp4.name))
        for c in got:
            print("        %s" % c[3].replace("\n", " / "))
        if args.dry_run:
            continue
        cmd = [sys.executable, str(HERE / "burn_intro_subs.py"),
               "--channel", args.channel, "--srt", str(out_srt),
               "--intro", str(clip), "--out", str(out_mp4), "--crf", str(args.crf)]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print("  ! 씬%d 번인 실패" % sc["id"]); rc = 1
    if not args.dry_run and rc == 0:
        print("\n완료 — 원본 클립은 그대로 두고 `_sub.mp4` 를 만들었습니다.")
        print("  ATTACH_HOOK 은 자막이 구워진 `_sub.mp4` 쪽을 쓰세요.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
