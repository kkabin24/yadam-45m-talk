#!/usr/bin/env python3
"""단계별 리뷰 게이트 패킷 생성기 (2026-09-06 신설 · 사용자 지시).

    python3 scripts/script/review_gate.py <게이트> <경로> [--cpm 284] [--out FILE]

게이트: g1 소재 · g2 골격 · g3 초반3분 · g4 초고 · g5 최종   (기준표 = prompts/review-gates.md)

무엇을 하는가 — **리뷰어에게 넘길 봉투를 싼다.**
  ① 그 단계에 맞는 기계 검사를 돌려 수치를 뽑고
  ② 그 단계가 봐야 할 분량만 잘라 내고
  ③ 리뷰어 페르소나·판정 기준·출력 서식을 앞에 붙인다.

왜 코드로 만드는가: 사람이 그때그때 프롬프트를 쓰면 회차마다 기준이 흔들린다.
같은 봉투를 써야 **회차 사이의 지적이 비교 가능해진다** — 2차에서 1차 지적이 사라졌는지 보려면
1차와 같은 것을 물어야 한다.

리뷰어에게 **집필 의도를 주지 않는다.** 의도를 알면 의도대로 읽어 준다.
"""
import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GATES_DOC = ROOT / ".claude/skills/story-pd/prompts/review-gates.md"
DEFAULT_CPM = 284
OPEN_MIN = 3.0

PERSONA = """당신은 수면·야담 유튜브 채널을 여럿 봐 온 **리텐션 분석가**입니다.
대본을 문학으로 읽지 말고 **영상 이탈률 곡선으로** 읽으세요.
판단 기준은 하나입니다 — 시청자가 어디서 손가락을 움직이는가.
칭찬은 쓰지 마세요. 고칠 자리와 그 이유만 쓰세요.
집필자의 의도를 추측해 변호하지 마세요. 당신은 의도를 모르는 시청자입니다."""

OUTPUT_FORM = """[판정] 통과 | 조건부 통과 | 반려
[가장 큰 이탈 위험] 한 문장
[구간별 지적]  ※ 분 단위 위치를 반드시 적는다
  - 0~3분   : …
  - 3~15분  : …
  - 15~30분 : …
  - 30분~   : …
[항목 판정] A1~E4 중 불합격만. 항목번호 + 근거 문장 인용 + 고치는 법
[반영 우선순위] 1) … 2) … 3) …   ※ 셋까지만"""

GATES = {
    "g1": ("소재", "이 소재로 썸네일과 제목이 서는가. 클릭될 이유가 있는가.", ["A"]),
    "g2": ("골격", "45분 동안 이탈하지 않을 계단이 있는가. 2분마다 붙잡을 것이 있는가.", ["A", "C", "E"]),
    "g3": ("초반 3분", "여기서 손가락이 멈추는가. ★이탈이 가장 몰리는 자리다.", ["B"]),
    "g4": ("초고", "늘어지는 구간이 어디인가. 어디서 끄고 싶어지는가.", ["B", "C", "D", "E"]),
    "g5": ("최종", "남은 위화감. 편끼리 한 편처럼 들리지 않는가.", ["A", "B", "C", "D", "E"]),
}


def no_space(s):
    return len(re.sub(r"\s", "", s))


def head_minutes(text, minutes, cpm):
    """앞에서부터 지정한 분량(분)만큼 잘라 낸다 — 문단 경계에서 끊는다."""
    budget, out = minutes * cpm, []
    for p in re.split(r"\n\s*\n", text):
        if not p.strip():
            continue
        out.append(p.strip())
        budget -= no_space(p)
        if budget <= 0:
            break
    return "\n\n".join(out)


def run(cmd):
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, cwd=ROOT)
    return (r.stdout or "") + (r.stderr or "")


def checks_for(gate, path, cpm):
    """게이트마다 돌릴 기계 검사. 수치 없이 감으로 리뷰하면 회차마다 말이 달라진다."""
    if gate in ("g1", "g2"):
        return ""       # 소재·골격 단계에는 본문이 없다
    out = [f"$ check_retention.py {path.name} --map",
           run(["scripts/script/check_retention.py", str(path), "--cpm", str(cpm), "--map"])]
    if gate in ("g4", "g5"):
        out += [f"\n$ validate_script.py {path.name} --style",
                run(["scripts/script/validate_script.py", str(path), "--cpm", str(cpm), "--style"])]
    return "\n".join(out)


def criteria(sections):
    """review-gates.md에서 해당 기준표만 오려 온다 — 문서와 코드가 갈라지지 않게."""
    if not GATES_DOC.exists():
        return "(review-gates.md 없음)"
    doc = GATES_DOC.read_text(encoding="utf-8")
    keep, grab = [], False
    for line in doc.splitlines():
        m = re.match(r"^### ([A-E])\.", line)
        if m:
            grab = m.group(1) in sections
        elif line.startswith("## "):
            grab = False
        if grab:
            keep.append(line)
    return "\n".join(keep).strip() or "(기준표를 찾지 못함)"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gate", choices=sorted(GATES))
    ap.add_argument("path", type=pathlib.Path)
    ap.add_argument("--cpm", type=float, default=DEFAULT_CPM)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    a = ap.parse_args()

    if not a.path.exists():
        print(f"경로 없음: {a.path}", file=sys.stderr)
        return 2
    label, question, sections = GATES[a.gate]
    text = a.path.read_text(encoding="utf-8")

    if a.gate == "g3":
        material = head_minutes(text, OPEN_MIN, a.cpm)
        scope = f"검토 대상 — 편의 **첫 {OPEN_MIN:.0f}분**(약 {int(OPEN_MIN*a.cpm)}자)만 봅니다. 뒤는 아직 보지 마세요."
    else:
        material = text
        scope = f"검토 대상 — 전문 (공백 제외 {no_space(text):,}자 · 약 {no_space(text)/a.cpm:.0f}분)"

    packet = f"""# 리뷰 요청 — 게이트 {a.gate.upper()} ({label})

{PERSONA}

## 이 게이트에서 답할 질문
{question}

## {scope}

## 기계 검사 결과 (참고 — 이 수치가 통과여도 지루하면 지루하다고 쓰세요)
```
{checks_for(a.gate, a.path, a.cpm).strip() or "(이 단계에는 기계 검사가 없습니다)"}
```

## 판정 기준
{criteria(sections)}

## 출력 서식 (반드시 이대로)
```
{OUTPUT_FORM}
```

---

# 검토 자료

{material}
"""
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(packet, encoding="utf-8")
        print(f"패킷 저장: {a.out}  ({len(packet):,}자)")
    else:
        print(packet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
