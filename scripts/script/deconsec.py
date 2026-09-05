# -*- coding: utf-8 -*-
"""같은 종결이 이어지는 자리를 훑어 한 문장씩만 바꾼다.
왼쪽에서 오른쪽으로 가면서, 앞 문장과 종결이 같으면 그 문장을 바꾼다.
바꿀 어미는 (1) 앞 문장과 다르고 (2) 뒤 문장과도 다르고 (3) 목표 비율에서 먼 쪽을 고른다.
문장 자체는 건드리지 않고 종결 형태만 바꾸므로 뜻이 변하지 않는다.
바뀐 문장은 전부 출력하므로 사람이 눈으로 훑을 수 있다.
"""
import io, re, sys

MAPS = {
    "지요": [("이었습니다", "이었지요"), ("였습니다", "였지요"), ("았습니다", "았지요"),
           ("었습니다", "었지요"), ("겠습니다", "겠지요"), ("입니다", "이지요"),
           ("합니다", "하지요"), ("됩니다", "되지요"), ("납니다", "나지요"),
           ("옵니다", "오지요"), ("갑니다", "가지요"), ("습니다", "지요"),
           ("이었어요", "이었지요"), ("였어요", "였지요"), ("았어요", "았지요"),
           ("었어요", "었지요"), ("이에요", "이지요"), ("예요", "이지요"),
           ("해요", "하지요"), ("돼요", "되지요"), ("나요", "나지요"), ("어요", "지요")],
    "어요": [("이었습니다", "이었어요"), ("였습니다", "였어요"), ("았습니다", "았어요"),
           ("었습니다", "었어요"), ("입니다", "이에요"), ("합니다", "해요"),
           ("됩니다", "돼요"), ("납니다", "나요"), ("옵니다", "와요"), ("습니다", "어요"),
           ("이었지요", "이었어요"), ("였지요", "였어요"), ("았지요", "았어요"),
           ("었지요", "었어요"), ("이지요", "이에요"), ("하지요", "해요"),
           ("되지요", "돼요"), ("나지요", "나요"), ("지요", "어요")],
    "니다": [("이었지요", "이었습니다"), ("였지요", "였습니다"), ("았지요", "았습니다"),
           ("었지요", "었습니다"), ("겠지요", "겠습니다"), ("이지요", "입니다"),
           ("하지요", "합니다"), ("되지요", "됩니다"), ("나지요", "납니다"),
           ("오지요", "옵니다"), ("가지요", "갑니다"),
           ("이었어요", "이었습니다"), ("였어요", "였습니다"), ("았어요", "았습니다"),
           ("었어요", "었습니다"), ("이에요", "입니다"), ("예요", "입니다"),
           ("해요", "합니다"), ("돼요", "됩니다"), ("나요", "납니다"), ("와요", "옵니다"),
           ("지요", "습니다"), ("죠", "습니다"), ("어요", "습니다")],
}
KINDS = [("니다", re.compile(r"니다$")),
         ("지요", re.compile(r"(지요|죠)$")),
         ("어요", re.compile(r"(어요|아요|여요|예요|에요|네요|해요|워요)$"))]
TRIM = '."\'?!… ”'
TARGET = {"니다": 0.50, "지요": 0.18, "어요": 0.18}


def kind_of(s):
    core = s.rstrip(TRIM)
    for name, pat in KINDS:
        if pat.search(core):
            return name
    return None


def convert(s, kind):
    core = s.rstrip()
    tail = ""
    while core and core[-1] in '"”':
        tail = core[-1] + tail
        core = core[:-1]
    if not core.endswith("."):
        return None
    core = core[:-1]
    for a, b in MAPS[kind]:
        if core.endswith(a):
            return core[: -len(a)] + b + "." + tail
    return None


def main(path, keep_ratio=0.05):
    text = io.open(path, encoding="utf-8").read()
    paras = text.split("\n\n")
    sents, owner = [], []
    for pi, para in enumerate(paras):
        body = para.strip()
        if not body:
            continue
        for s in re.split(r"(?<=[.!?…])\s+", body):
            s = s.strip()
            if s:
                sents.append(s)
                owner.append(pi)
    kinds = [kind_of(s) for s in sents]
    n = len(sents)
    cnt = {k: kinds.count(k) for k in TARGET}
    changed = []
    for i in range(1, n):
        if kinds[i] is None or kinds[i] != kinds[i - 1]:
            continue
        nxt = kinds[i + 1] if i + 1 < n else None
        cands = []
        for k in TARGET:
            if k == kinds[i - 1] or k == kinds[i]:
                continue
            new = convert(sents[i], k)
            if not new:
                continue
            penalty = cnt[k] / n - TARGET[k]
            if k == nxt:
                penalty += 1.0          # 뒤와도 겹치면 크게 미룬다
            cands.append((penalty, k, new))
        if not cands:
            continue
        cands.sort()
        _, k, new = cands[0]
        pi = owner[i]
        if paras[pi].count(sents[i]) != 1:
            continue
        paras[pi] = paras[pi].replace(sents[i], new)
        changed.append((i, sents[i], new))
        cnt[kinds[i]] -= 1
        cnt[k] += 1
        sents[i], kinds[i] = new, k
    io.open(path, "w", encoding="utf-8").write("\n\n".join(paras))
    print("바꾼 문장", len(changed), "/", n)
    for i, a, b in changed:
        print("  %4d  %s\n        -> %s" % (i, a[-30:], b[-30:]))


if __name__ == "__main__":
    main(sys.argv[1])
