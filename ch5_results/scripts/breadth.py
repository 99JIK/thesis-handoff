"""행동 공간 폭 정적 비교(사후 탐색). 제로샷 대 단계형, 단계형 설계 의도(분류 먼저 세워 넓게 열거)가 트리에서 보이는가.
설계는 docs/design/design_exp4.md 생성 방식과 수리 효과 절의 행동 공간 폭 항목. CARLA 불요, z3만

  python scripts/breadth.py          results/gen_effect/breadth.csv, breadth.md

칸 반응은 본 실험 E3 cell_responses 그대로. 최종은 기록의 order, 초안은 컴파일과 같은 fallback_order.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_effect as G  # noqa: E402
from gen_effect import E4, S, E, smt_counts, t_ci, welch  # noqa: E402

OUT = G.OUT
# (방식, 단계). 제로샷 초안은 구조 통과만(구조 실패는 컴파일이 안 돼 순서가 없음)
GROUPS = [("zeroshot", "final"), ("staged_re", "final"), ("staged", "final"), ("zeroshot", "draft"),
          ("onecall", "final")]
# (a, b)는 a - b. 첫 줄이 주 대비
CONTRASTS = [(("zeroshot", "final"), ("staged_re", "final")),
             (("zeroshot", "final"), ("staged", "final")),
             (("staged_re", "final"), ("staged", "final")),
             (("zeroshot", "draft"), ("staged_re", "final")),
             (("zeroshot", "draft"), ("staged", "final")),
             # 단일 호출 분류 포함(2026-11-26). 세트 없으면 건너뜀
             (("onecall", "final"), ("staged_re", "final")),
             (("zeroshot", "final"), ("onecall", "final"))]
METRICS = [("cells_distinct", "칸 반응 수(주)"), ("resp_kinds", "반응 종류 수"), ("cond_kinds", "조건 종류 수(살아 있는 행동)"),
           ("n_sets", "설정 종류 수"), ("n", "행동 수")]


def order_of(B, r, stage):
    if stage == "draft":
        return S.fallback_order(B, S.Smt(B))
    ids = [b["id"] for b in B]
    return dict((s, [ids.index(i) for i in lst]) for s, lst in r["order"].items())


def breadth_row(B, order):
    dead, shad, _t = smt_counts(B)
    gone = set(dead) | set(shad)
    resp = E.cell_responses(B, order)
    kinds = set()
    for x in resp:
        kinds |= x
    conds = set(w["cond"] for b in B if b["id"] not in gone for w in b.get("when", []))
    return dict(n=len(B), n_sets=len(set(b["set"] for b in B)), cells_distinct=len(set(x for x in resp if x)),
                resp_kinds=len(kinds), cond_kinds=len(conds), n_alive=len(B) - len(gone))


def lab(g):
    return "%s %s" % (G.LABEL[g[0]], "초안" if g[1] == "draft" else "최종")


def main():
    E4.use(None)
    rows = []
    for m, tree, r in G.records():
        for stage in ("draft", "final"):
            if (m, stage) not in GROUPS:
                continue
            B = r.get(stage) if stage == "draft" else (r.get("final") if r.get("deployed") else None)
            if not B or S.structure_errors(B):
                continue
            row = dict(method=m, stage=stage, tree=tree)
            row.update(breadth_row(B, order_of(B, r, stage)))
            rows.append(row)
            print("  %s %s %s: 칸 반응 %d, 반응 종류 %d, 조건 종류 %d" % (m, stage, tree, row["cells_distinct"],
                                                                 row["resp_kinds"], row["cond_kinds"]), flush=True)
    T = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    T.to_csv(os.path.join(OUT, "breadth.csv"), index=False, encoding="utf-8-sig")

    def sel(g):
        return T[(T.method == g[0]) & (T.stage == g[1])]
    L = ["# 행동 공간 폭(정적, 사후 탐색)", "",
         "생성: `scripts/breadth.py`. 설계: [`design_exp4.md`](../../docs/design/design_exp4.md#생성-방식과-수리-효과사후-추가). "
         "칸 반응은 본 실험 E3과 같은 함수(96칸, 존재 판정). 구간은 Welch 95%, 보정 없음. 판정: 구간이 0 밖이면 많다/적다.", "",
         "| 묶음 | 트리 | " + " | ".join(n for _k, n in METRICS) + " |", "|---|---|" + "---|" * len(METRICS)]
    for g in GROUPS:
        x = sel(g)
        if len(x) < 2:
            continue
        cells = []
        for k, _n in METRICS:
            lo, hi = t_ci(x[k].to_numpy(float))
            cells.append("%.1f [%.1f, %.1f]" % (x[k].mean(), lo, hi))
        L.append("| %s | %d | %s |" % (lab(g), len(x), " | ".join(cells)))
    L += ["", "## 대비", "", "| 대비 | 지표 | 차이 | 95% 구간 | 판정 |", "|---|---|---|---|---|"]
    for a, b in CONTRASTS:
        if len(sel(a)) < 2 or len(sel(b)) < 2:
            continue
        for k, n in METRICS:
            d, lo, hi = welch(sel(a)[k].to_numpy(float), sel(b)[k].to_numpy(float))
            v = "많다" if lo > 0 else "적다" if hi < 0 else "차이 확인 못함"
            L.append("| %s - %s | %s | %+.2f | [%+.2f, %+.2f] | %s |" % (lab(a), lab(b), n, d, lo, hi, v))
    L += ["", "첫 대비(제로샷 최종 - 단계형 재생성 최종)가 주. 행동 수 줄은 판정 대상 아님(옆에 적는 값). 설계 의도대로면 단계형 쪽이 "
          "많아 차이가 음수."]
    p = os.path.join(OUT, "breadth.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("-> %s" % os.path.relpath(p, G.ROOT))


if __name__ == "__main__":
    main()
