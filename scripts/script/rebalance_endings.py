# -*- coding: utf-8 -*-
"""연속이 새로 생기지 않는 자리에서만 어미를 되돌려 비율을 맞춘다.
사용: rebalance.py <chapter.md> <목표kind> <목표비율>
"""
import io, re, sys
sys.path.insert(0, __file__.rsplit('\\', 1)[0])
from deconsec import KINDS, MAPS, TRIM, kind_of, convert


def main(path, want="니다", target=0.50):
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
    cur = kinds.count(want)
    need = int(target * n) - cur
    if need <= 0:
        print("이미 충족:", cur, "/", n)
        return
    changed = 0
    for i in range(n):
        if changed >= need:
            break
        if kinds[i] == want or kinds[i] is None:
            continue
        prev = kinds[i - 1] if i > 0 else None
        nxt = kinds[i + 1] if i + 1 < n else None
        if prev == want or nxt == want:
            continue                      # 되돌리면 연속이 생긴다
        new = convert(sents[i], want)
        if not new:
            continue
        pi = owner[i]
        if paras[pi].count(sents[i]) != 1:
            continue
        paras[pi] = paras[pi].replace(sents[i], new)
        sents[i], kinds[i] = new, want
        changed += 1
    io.open(path, "w", encoding="utf-8").write("\n\n".join(paras))
    print("되돌림", changed, "· 목표였던 수", need)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "니다",
         float(sys.argv[3]) if len(sys.argv) > 3 else 0.50)
