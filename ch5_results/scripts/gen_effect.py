"""생성 방식(단계형, 제로샷) x 수리(전, 후) 정적 분석. 사후 기술. CARLA 불요, z3만.
설계는 docs/design/design_exp4.md 생성 방식과 수리 효과 절

  python scripts/gen_effect.py          results/gen_effect/ 에 trees.csv, changes.csv, report.md

목록 하나 = (방식, 트리, 단계). 단계 draft는 정규화 뒤 0회차, final은 배치 목록.
SMT 지표는 exp4_eval.static_defects와 같은 문장 태그로 셈. 같은 함수 써야 본 실험 smt.md와 맞음.
"""
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "diag"))
import exp4_author as E4  # noqa: E402  (v3 카탈로그로 바꿈)
import exp3_schema as S  # noqa: E402
import exp3_eval as E  # noqa: E402
from exp4_eval import t_ci, welch  # noqa: E402

BT = os.path.join(ROOT, "data", "outputs", "bt")
OUT = os.path.join(ROOT, "results", "gen_effect")
# staged_re: 같은 단계형을 제로샷과 같은 날 다시 생성(생성 날짜 차이 제거용). 본 실험 트리는 staged 그대로
METHODS = {"staged": "exp4_*", "staged_re": "st_*", "zeroshot": "zs_*", "onecall": "one_*"}
LABEL = {"staged": "단계형", "staged_re": "단계형 재생성", "zeroshot": "제로샷", "onecall": "단일 호출", "draft": "수리 전", "final": "수리 후"}
# (a, b)는 a - b. 같은 날 짝(zeroshot, staged_re)이 방식 비교 주 대비, 나머지는 날짜 차이 점검과 기존 대비
BETWEEN = [("zeroshot", "staged_re"), ("zeroshot", "staged"), ("staged_re", "staged"),
           # 단일 호출 분류 포함(2026-11-26): 호출 분리 효과, 같은 한 번 호출에서 지시 구조 효과
           ("onecall", "staged_re"), ("zeroshot", "onecall")]
RID = re.compile(r"Behaviour '([^']+)'")


def records():
    out = []
    for m, pat in METHODS.items():
        for f in sorted(glob.glob(os.path.join(BT, pat, "tree_*.json"))):
            r = json.load(open(f, encoding="utf-8"))
            out.append((m, "%s/%s" % (r["set"], os.path.basename(f)[:-5]), r))
    return out


def smt_counts(B):
    """(모순, 가려짐, 동률, 오류 문장). 구조 실패 목록에도 부름(민감도). 식을 못 세우면 예외"""
    smt = S.Smt(B)
    errs, _o = S.semantic_errors(B, smt)
    dead = [RID.match(e).group(1) for e in errs if "can never all hold" in e]
    shad = [RID.match(e).group(1) for e in errs if "never takes effect: whenever" in e]
    tie = sum("neither is a special case" in e for e in errs)
    return dead, shad, tie


def intent_rows(B):
    import rule_intent as RI
    rows = []
    for b in B:
        knob = E.SET_KNOB.get(b["set"], b["set"])
        idir = RI.intent_dir(knob, str(b.get("intent", "")).lower())
        vd = "zero" if b.get("amount") == 0 else E.eff_dir(b["set"], b["effect"])
        st, _why = RI.judge(knob, vd, idir)
        rows.append((b["set"], st))
    return rows


def list_row(m, tree, stage, B, r):
    row = dict(method=m, tree=tree, style=tree.split("/")[0].split("_", 1)[1], stage=stage,
               exists=B is not None)
    if B is None:
        return row
    se = S.structure_errors(B)
    row.update(n=len(B), struct_ok=not se, n_struct_err=len(se),
               n_sets=len(set(b["set"] for b in B)), n_always=sum(1 for b in B if not b.get("when")))
    try:
        dead, shad, tie = smt_counts(B)
        row.update(smt_ok=True, dead=len(dead), shadow=len(shad), tie=tie,
                   static_dead_rate=(len(dead) + len(shad)) / len(B))
    except Exception as e:  # noqa: BLE001
        row.update(smt_ok=False, smt_err="%s: %s" % (type(e).__name__, e))
    ir = intent_rows(B)
    sp = [s for k, s in ir if k == "speed"]
    row.update(int_speed_ok=sp.count("일치"), int_speed_n=sum(s in ("일치", "불일치") for s in sp),
               int_all_ok=sum(s == "일치" for _k, s in ir),
               int_all_n=sum(s in ("일치", "불일치") for _k, s in ir))
    if stage == "final":
        row["deployed"] = bool(r.get("deployed"))
    return row


def changes(m, tree, r):
    """수리가 바꾼 필드. id로 맞춤(수리는 추가, 삭제, id 변경 금지)"""
    if not r.get("deployed"):
        return []
    d = dict((b["id"], b) for b in r["draft"])
    out = []
    for b in r["final"]:
        a = d.get(b["id"])
        if a is None:
            continue
        thr = [w for w, v in zip(a.get("when", []), b.get("when", [])) if w != v]
        out.append(dict(method=m, tree=tree, id=b["id"], set=b["set"], thr=bool(thr),
                        amount=a.get("amount") != b.get("amount"), priority=a.get("priority") != b.get("priority")))
    return out


def process(m, tree, r):
    rd = r.get("rounds") or []
    last = rd[-1]["status"] if rd else None
    return dict(method=m, tree=tree, status=r.get("status"), deployed=bool(r.get("deployed")),
                repairs=r.get("repairs"), rejected=sum(x.get("status") == "rejected" for x in rd),
                no_json_rounds=sum(x.get("status") == "no_json" for x in rd),
                cap=bool(rd) and len(rd) - 1 >= S_MAX and last != "ok", sem_left=len(r.get("semantic_left") or []))


S_MAX = 6  # bt_author.MAX_REPAIR. 기록에도 max_repair로 남음


def fmt_ci(x):
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 2:
        return "n=%d" % len(x)
    lo, hi = t_ci(x)
    return "%.3f [%.3f, %.3f] (n=%d)" % (x.mean(), lo, hi, len(x))


def paired(T, m, mask_draft):
    """같은 트리 수리 후 - 수리 전, static_dead_rate. 둘 다 SMT 값 있는 트리만"""
    d = T[(T.method == m) & (T.stage == "draft") & mask_draft].set_index("tree")
    f = T[(T.method == m) & (T.stage == "final") & (T.deployed == True)].set_index("tree")  # noqa: E712
    k = d.index.intersection(f.index)
    x = (f.loc[k, "static_dead_rate"] - d.loc[k, "static_dead_rate"]).dropna()
    if len(x) < 2:
        return "n=%d" % len(x)
    lo, hi = t_ci(x)
    return "%+.3f [%+.3f, %+.3f], 나아짐 %d, 나빠짐 %d, 같음 %d (n=%d)" % (
        x.mean(), lo, hi, (x < 0).sum(), (x > 0).sum(), (x == 0).sum(), len(x))


def between(T, ma, mb, stage, mask):
    a = T[(T.method == ma) & (T.stage == stage) & mask].static_dead_rate.dropna()
    b = T[(T.method == mb) & (T.stage == stage) & mask].static_dead_rate.dropna()
    if len(a) < 2 or len(b) < 2:
        return "n=%d, %d" % (len(a), len(b))
    d, lo, hi = welch(a, b)
    return "%+.3f [%+.3f, %+.3f] (n=%d, %d)" % (d, lo, hi, len(a), len(b))


def main():
    E4.use(None)  # 카탈로그 v3. 정의역, 상한이 v3 기준이어야 구조 검사가 생성 때와 같음
    rows, ch, pr = [], [], []
    for m, tree, r in records():
        rows.append(list_row(m, tree, "draft", r.get("draft"), r))
        rows.append(list_row(m, tree, "final", r.get("final") if r.get("deployed") else None, r))
        ch += changes(m, tree, r)
        pr.append(process(m, tree, r))
    T, C, P = pd.DataFrame(rows), pd.DataFrame(ch), pd.DataFrame(pr)
    ms = [m for m in METHODS if (T.method == m).any()]
    os.makedirs(OUT, exist_ok=True)
    T.to_csv(os.path.join(OUT, "trees.csv"), index=False, encoding="utf-8-sig")
    C.to_csv(os.path.join(OUT, "changes.csv"), index=False, encoding="utf-8-sig")
    P.to_csv(os.path.join(OUT, "process.csv"), index=False, encoding="utf-8-sig")

    L = ["# 생성 방식과 수리 효과(정적, 사후 기술)", "",
         "생성: `scripts/gen_effect.py`. 설계: [`design_exp4.md`](../../docs/design/design_exp4.md#생성-방식과-수리-효과사후-추가). "
         "결과를 본 뒤 추가한 분석이라 판정 문구 없이 기술만 함. 수리 루프는 이 검사를 향해 고치므로 수리 후 값은 정확성 증거가 아님.", "",
         "## 2x2 요약", "",
         "정적으로 죽은 비율 = (가려짐 + 모순) / 행동 수, 트리 단위 평균과 95% t 구간. 주 값은 구조 통과 목록만, "
         "민감도는 SMT 식을 세울 수 있는 목록 전부.", "",
         "| 방식 | 단계 | 시도 | 목록 있음 | 구조 통과 | 배치 | 행동 수 평균 | 죽은 비율(구조 통과) | 죽은 비율(전부, 민감도) | 가려짐 | 모순 | 동률 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in ms:
        for st in ("draft", "final"):
            g = T[(T.method == m) & (T.stage == st)]
            e = g[g.exists]
            ok = e[e.struct_ok == True]  # noqa: E712
            sm = e[e.smt_ok == True]  # noqa: E712
            dep = "-" if st == "draft" else "%d" % int((g.deployed == True).sum())  # noqa: E712
            L.append("| %s | %s | %d | %d | %d | %s | %.1f | %s | %s | %d | %d | %d |" % (
                LABEL[m], LABEL[st], len(g), len(e), len(ok), dep, e.n.mean(), fmt_ci(ok.static_dead_rate),
                fmt_ci(sm.static_dead_rate), sm.shadow.sum(), sm.dead.sum(), sm.tie.sum()))
    nosmt = T[T.exists & (T.smt_ok == False)]  # noqa: E712
    L += ["", "SMT 식을 못 세운 목록: %d개%s" % (len(nosmt), "" if nosmt.empty else ", " + ", ".join(
        "%s %s(%s)" % (r.tree, r.stage, r.smt_err[:60]) for r in nosmt.itertuples())), ""]

    L += ["## 수리 전후(같은 트리 짝)", "", "수리 후 - 수리 전 죽은 비율. 음수가 줄어든 것.", "",
          "| 방식 | 구조 통과 초안만 | 전부(민감도) |", "|---|---|---|"]
    for m in ms:
        L.append("| %s | %s | %s |" % (LABEL[m], paired(T, m, T.struct_ok == True),  # noqa: E712
                                       paired(T, m, T.smt_ok == True)))  # noqa: E712
    L += ["", "## 생성 방식 사이(짝 없음)", "", "앞 - 뒤 죽은 비율, Welch 95% 구간. 같은 날 생성한 제로샷 - 단계형 재생성이 방식 비교의 주 대비, "
          "단계형 재생성 - 단계형은 생성 날짜 차이 점검.", "",
          "| 대비 | 단계 | 구조 통과만 | 전부(민감도) |", "|---|---|---|---|"]
    for ma, mb in BETWEEN:
        if ma not in ms or mb not in ms:
            continue
        for st in ("draft", "final"):
            mk = (T.deployed == True) if st == "final" else T.exists  # noqa: E712
            L.append("| %s - %s | %s | %s | %s |" % (
                LABEL[ma], LABEL[mb], LABEL[st], between(T, ma, mb, st, mk & (T.struct_ok == True)),  # noqa: E712
                between(T, ma, mb, st, mk & (T.smt_ok == True))))  # noqa: E712

    L += ["", "## 의도와 값의 방향", "", "본 실험 e1과 같은 판정. 같은 방식의 수리 전후는 같아야 함(불변 점검).", "",
          "| 방식 | 단계 | speed 일치/판정 | 전체 일치/판정 |", "|---|---|---|---|"]
    inv = []
    for m in ms:
        for st in ("draft", "final"):
            g = T[(T.method == m) & (T.stage == st) & T.exists]
            if st == "final":
                g = g[g.deployed == True]  # noqa: E712
            a, b, c, d = (int(g[k].sum()) for k in ("int_speed_ok", "int_speed_n", "int_all_ok", "int_all_n"))
            L.append("| %s | %s | %d/%d = %.1f%% | %d/%d = %.1f%% |" % (
                LABEL[m], LABEL[st], a, b, 100 * a / max(b, 1), c, d, 100 * c / max(d, 1)))
        dd = T[(T.method == m) & (T.stage == "draft")].set_index("tree")
        ff = T[(T.method == m) & (T.stage == "final") & (T.deployed == True)].set_index("tree")  # noqa: E712
        for t in ff.index:
            if (dd.loc[t, "int_all_ok"], dd.loc[t, "int_all_n"]) != (ff.loc[t, "int_all_ok"], ff.loc[t, "int_all_n"]):
                inv.append("%s" % t)
    L += ["", "불변 점검(배치 트리의 수리 전후 방향 판정이 같은가): %s" % ("통과" if not inv else "실패 " + ", ".join(inv))]

    L += ["", "## 목록 모양", "", "| 방식 | 단계 | 행동 수 | 쓴 설정 종류 | 조건 없는 행동 |", "|---|---|---|---|---|"]
    for m in ms:
        for st in ("draft", "final"):
            g = T[(T.method == m) & (T.stage == st) & T.exists]
            L.append("| %s | %s | %.1f (%d~%d) | %.1f | %.1f |" % (LABEL[m], LABEL[st], g.n.mean(), g.n.min(), g.n.max(),
                                                                g.n_sets.mean(), g.n_always.mean()))

    L += ["", "## 수리가 바꾼 것과 과정", "",
          "| 방식 | 배치 행동 | 임계 바뀜 | amount 바뀜 | priority 바뀜 | 수정 회차 평균 | 거부 회차 | 상한 도달 | 의미 오류 0 | 빠짐 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for m in ms:
        c = C[C.method == m] if not C.empty else C
        p = P[P.method == m]
        L.append("| %s | %d | %d | %d | %d | %.1f | %d | %d/%d | %d/%d | %s |" % (
            LABEL[m], len(c), c.thr.sum(), c.amount.sum(), c.priority.sum(), p.repairs.dropna().mean(),
            p.rejected.sum(), p.cap.sum(), len(p), ((p.sem_left == 0) & p.deployed).sum(), len(p),
            ", ".join("%s(%s)" % (r.tree, r.status) for r in p[~p.deployed].itertuples()) or "없음"))

    L += ["", "## 성향별(기술)", "", "| 방식 | 성향 | 단계 | 구조 통과 | 죽은 비율 평균(SMT 가능 목록) |", "|---|---|---|---|---|"]
    for m in ms:
        for sty in sorted(T["style"].unique()):
            for st in ("draft", "final"):
                g = T[(T.method == m) & (T["style"] == sty) & (T.stage == st) & T.exists]
                L.append("| %s | %s | %s | %d/%d | %.3f |" % (LABEL[m], sty, LABEL[st], int(g.struct_ok.sum()), len(g),
                                                              g.static_dead_rate.mean()))
    L += ["", "KCC2026 수정 전후 결과는 [`results/exp1/report.md`](../exp1/report.md). 도메인, 검사기, 생성 방식이 달라 나란히 기술만 함."]
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("-> %s" % os.path.relpath(os.path.join(OUT, "report.md"), ROOT))


if __name__ == "__main__":
    main()
