#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meta.txt 규격 검사 — 업로드 전에 돌린다.

★왜 필요한가 (2026-09-05 사용자 지시 「이거를 룰에 막아줘」)
  설명문 확정 서식은 script-guide.md 「설명문 — 확정 서식」에 2026-08-21부터 적혀 있는데,
  13편 meta 가 조용히 어긋나 있었다 — 안내 문장이 바뀌고, 줄거리 3줄이 끼어들고,
  백색소음 줄과 해시태그 순서가 달랐다. 규칙만 있고 검사기가 없으면 매 편 다시 어긋난다.
  upload.py 는 형식을 안 보고 그대로 올리므로 **여기서 막는다.**

  검사 항목
    · 필수 블록 4개 — [제목] [썸네일 문구] [설명문] [제작 메모]
    · 제목 — `[N시간 연속] … ㅣ옛날이야기ㅣ오디오북ㅣ수면동화ㅣ야담ㅣ`, 68자 이내, N 미확정 금지
    · 설명문 — 인사말·안내 3줄이 서식과 글자까지 같은가, 줄거리를 적지 않았는가
    · 챕터 — 첫 줄 0:00:00, 오름차순, 백색소음이 마지막이고 `(M시간)` 표기
    · 해시태그 — 확정 열과 같고 [설명문] 블록 안에 있는가
    · 숫자 일치 — 제목 N시간 / 「백색소음이 M시간」 / 챕터 `(M시간)` / 「옛이야기 K편」 = 「수록된 K 이야기」 = 챕터 수

Usage:
    python3 scripts/script/check_meta.py {프로젝트 폴더 또는 meta.txt} [...]
"""
import argparse
import pathlib
import re
import sys

TITLE_TAIL = "ㅣ옛날이야기ㅣ오디오북ㅣ수면동화ㅣ야담ㅣ"
TITLE_MAX = 68
GREETING = "스르르 잠드는 이야기 들려주는 옛뜰이에요😊"
LINE_1 = "잠이 오지 않는 밤에 그냥 틀어 놓으시라고, 옛이야기 {K}편을 이어 담았습니다."
LINE_2 = "어느 편부터 들으셔도 되고, 중간에 잠드셔도 괜찮습니다."
LINE_3 = "이야기가 끝난 뒤에는 조용한 백색소음이 {M}시간 더 이어집니다. 그대로 두고 주무세요."
HASHTAGS = ("#야담 #옛날이야기 #오디오북 #수면동화 #수면유도 #잠오는이야기 "
            "#전래동화 #민담 #불면 #자기전듣는이야기")

TS = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)\s\s+(.+?)\s*$")


def blocks(text):
    """`[헤더]` 로 나눈 블록 사전. upload.py 와 같은 방식(헤더 다음부터 다음 헤더 전까지)."""
    out, cur, buf = {}, None, []
    for ln in text.split("\n"):
        # upload.py 와 같은 헤더 판정 — 줄 전체가 대괄호 한 쌍일 때만 블록 헤더다.
        #   제목 줄 `[3시간 연속] 혼자 있는…` 을 새 블록으로 오인하지 않기 위해서다.
        m = re.fullmatch(r"\[([^\]]+)\]", ln.strip())
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def head(name, bl):
    """`[설명문]` 처럼 접미사가 붙은 헤더(`[챕터 시각 — 확정]`)도 찾는다."""
    for k in bl:
        if k == name or k.startswith(name):
            return bl[k]
    return None


def secs(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def check(path: pathlib.Path):
    bad = []
    text = path.read_text(encoding="utf-8")
    bl = blocks(text)

    for need in ("제목", "썸네일 문구", "설명문", "제작 메모"):
        if head(need, bl) is None:
            bad.append("블록 없음: [%s]" % need)
    if head("설명문", bl) is None:
        return bad

    # ── 제목
    tb = (head("제목", bl) or "").strip().split("\n")
    title = next((l.strip() for l in tb if l.strip() and not l.strip().startswith("★")), "")
    n_title = None
    if not title:
        bad.append("제목이 비었다")
    else:
        m = re.match(r"^\[(\d+|N)시간 연속\]", title)
        if not m:
            bad.append("제목 형식: `[N시간 연속] …` 으로 시작해야 한다 → %r" % title[:40])
        elif m.group(1) == "N":
            bad.append("제목의 N이 미확정이다 — 아웃트로를 붙인 뒤 완성본 길이로 채운다")
        else:
            n_title = int(m.group(1))
        if not title.endswith(TITLE_TAIL):
            bad.append("제목 꼬리 키워드가 다르다 — `%s` 로 끝나야 한다" % TITLE_TAIL)
        if len(title) > TITLE_MAX:
            bad.append("제목이 %d자 — %d자 이내" % (len(title), TITLE_MAX))

    # ── 설명문
    desc = head("설명문", bl)
    lines = [l.rstrip() for l in desc.split("\n")]
    body = [l for l in lines if l.strip() and not l.lstrip().startswith("★")]

    if not body or body[0].strip() != GREETING:
        bad.append("설명문 첫 줄이 인사말과 다르다 — %r" % (body[0].strip()[:40] if body else ""))

    k_desc = m_desc = None
    m = re.search(r"옛이야기\s*(\d+)편을 이어 담았습니다", desc)
    if m:
        k_desc = int(m.group(1))
        if LINE_1.format(K=k_desc) not in desc:
            bad.append("안내 첫 문장이 서식과 다르다 — `%s`" % LINE_1.format(K=k_desc))
    else:
        bad.append("안내 첫 문장이 없다 — `%s`" % LINE_1.format(K="{K}"))
    if LINE_2 not in desc:
        bad.append("안내 둘째 문장이 없거나 다르다 — `%s`" % LINE_2)
    m = re.search(r"백색소음이\s*(\d+)시간 더 이어집니다", desc)
    if m:
        m_desc = int(m.group(1))
        if LINE_3.format(M=m_desc) not in desc:
            bad.append("안내 셋째 문장이 서식과 다르다 — `%s`" % LINE_3.format(M=m_desc))
    else:
        bad.append("안내 셋째 문장이 없다 — `%s`" % LINE_3.format(M="{M}"))

    # 줄거리 금지 — 인사말·안내 3줄·목차 머리·챕터·해시태그 말고 다른 산문이 있으면 지적
    allowed = {GREETING, LINE_2, HASHTAGS}
    if k_desc:
        allowed.add(LINE_1.format(K=k_desc))
    if m_desc:
        allowed.add(LINE_3.format(M=m_desc))
    # ★[설명문] 블록은 통째로 유튜브 설명란이 된다(upload.py 가 description 으로 그대로 쓴다).
    #   그러니 ★주석도 서식 밖 문장과 똑같이 올라간다 — 여기서는 예외로 봐주지 않는다.
    for l in desc.splitlines():
        s = l.strip()
        if not s or s in allowed or TS.match(s) or s.startswith("■") or s.startswith("#"):
            continue
        if s.startswith("★"):
            bad.append("[설명문] 안의 주석은 그대로 설명란에 올라간다 — 블록 밖으로 옮길 것: %r" % s[:44])
        else:
            bad.append("설명문에 서식 밖 문장이 있다(줄거리를 적지 않는다) — %r" % s[:46])

    # ── 목차
    m = re.search(r"■ 수록된\s*(\d+)\s*이야기", desc)
    k_toc = int(m.group(1)) if m else None
    if k_toc is None:
        bad.append("목차 머리가 없다 — `■ 수록된 {K} 이야기`")

    ch = [TS.match(l.strip()) for l in lines]
    ch = [c for c in ch if c]
    if not ch:
        bad.append("챕터 줄이 없다")
    else:
        if secs(*ch[0].groups()[:3]) != 0:
            bad.append("첫 챕터가 0:00:00 이 아니다 — 유튜브가 챕터를 통째로 인식하지 않는다")
        prev = -1
        for c in ch:
            t = secs(*c.groups()[:3])
            if t <= prev:
                bad.append("챕터 시각이 오름차순이 아니다 — %s" % c.group(0).strip())
            prev = t
        last = ch[-1].group(4).strip()
        if not last.startswith("백색소음"):
            bad.append("마지막 챕터가 백색소음이 아니다 — %r" % last[:30])
        else:
            m = re.search(r"백색소음\s*\((\d+)시간\)", last)
            if not m:
                bad.append("백색소음 챕터에 `(M시간)` 표기가 없다 — %r" % last)
            elif m_desc is not None and int(m.group(1)) != m_desc:
                bad.append("백색소음 길이가 안내 문장(%d시간)과 챕터(%s시간)에서 다르다"
                           % (m_desc, m.group(1)))
        n_story = len(ch) - 1
        if k_toc is not None and n_story != k_toc:
            bad.append("목차 머리(%d)와 실제 편 수(%d)가 다르다" % (k_toc, n_story))
        if k_desc is not None and n_story != k_desc:
            bad.append("안내 문장(%d편)과 실제 편 수(%d)가 다르다" % (k_desc, n_story))
        for c in ch[:-1]:
            if len(c.group(4).strip()) > 20:
                bad.append("챕터 제목이 길다(편 제목만 적는다) — %r" % c.group(4).strip()[:30])

    # ── 해시태그
    tag_lines = [l.strip() for l in lines if l.strip().startswith("#")]
    if not tag_lines:
        bad.append("[설명문] 안에 해시태그 줄이 없다 — 다른 블록에 두면 태그 0개로 올라간다")
    elif tag_lines[0] != HASHTAGS:
        bad.append("해시태그 열이 확정 서식과 다르다\n      기대: %s\n      실제: %s"
                   % (HASHTAGS, tag_lines[0]))

    # ── 제목 N ↔ 아웃트로 M
    if n_title is not None and m_desc is not None:
        # 완성본 = 본편 + 아웃트로. 제목 N은 내림 표기이므로 N >= M 이어야 한다.
        if n_title < m_desc:
            bad.append("제목 %d시간 < 아웃트로 %d시간 — 완성본 길이를 다시 확인할 것"
                       % (n_title, m_desc))
    return bad


def main():
    ap = argparse.ArgumentParser(description="meta.txt 규격 검사 (업로드 전)")
    ap.add_argument("targets", nargs="+", type=pathlib.Path,
                    help="프로젝트 폴더 또는 meta.txt")
    a = ap.parse_args()

    rc = 0
    for t in a.targets:
        p = t / "meta.txt" if t.is_dir() else t
        if not p.exists():
            print("✗ %s 없음" % p)
            rc = 1
            continue
        bad = check(p)
        if bad:
            rc = 1
            print("\n✗ %s — 지적 %d건" % (p, len(bad)))
            for b in bad:
                print("    · %s" % b)
        else:
            print("✓ %s — 규격 충족" % p)
    if rc:
        print("\n서식 원본: .claude/skills/story-pd/prompts/script-guide.md 「설명문 — 확정 서식」")
    return rc


if __name__ == "__main__":
    sys.exit(main())
