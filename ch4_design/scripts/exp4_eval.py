"""실험 4 평가 중 실험 3 경로(exp3_eval, measure score)에 없는 것. py3.11 + z3. 설계 docs/design_exp4.md 평가 절

  python scripts/exp4_eval.py manip            조작 확인. plan.csv만 읽어 빠름
  python scripts/exp4_eval.py smt              정적 결함. results/exp4/e2_firing.csv 있으면 실주행 발화 대조까지
  python scripts/exp4_eval.py realism          recall, precision 대비 구간. diversity/tests.csv 있으면 묶어 읽는 표까지
  python scripts/exp4_eval.py floor            시드 잡음 바닥: knob 16~23(실험 3)과 24~31을 한 판으로 채점
  python scripts/exp4_eval.py mech [--ticks]   기제 기술. --ticks면 틱 기록 쓰는 둘(칸 안 분산, TM 압축)도. 무거움
  python scripts/exp4_eval.py placebo          실현율 위약 대조. 무거움

출력 results/exp4/. 판정 규칙과 판단값은 design_exp4.md 변경 기록 2026-11-22 행
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
from scipy.stats import t as tdist

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "diag"))
import bt_nodes as N  # noqa: E402
import exp3_schema as S  # noqa: E402
import exp3_eval as E  # noqa: E402
from exp4_check import want_means  # noqa: E402

OUT = os.path.join(ROOT, "results", "exp4")
SRC = os.path.join(ROOT, "data", "outputs", "arms_exp4")
UNITS = os.path.join(ROOT, "data", "outputs", "units_exp4")
UNITS3 = os.path.join(ROOT, "data", "outputs", "units_exp3")
ARMS = ["plain", "knob", "ptree", "pknob"]
STYLES = ["reckless", "anxious", "careful", "angry"]
FAST, SLOW = ("reckless", "angry"), ("anxious", "careful")
# 판정 대비 둘이 앞, 뒤 둘은 기술(knob-plain은 지표가 알려진 차이를 잡는지 검증)
CONTR = [("ptree", "knob"), ("ptree", "pknob"), ("pknob", "knob"), ("knob", "plain")]
LABEL = "실험 4"
DIV = None  # 다양성 tests.csv 위치. None이면 OUT/diversity
PLAN_RE = re.compile(r"^(plain|knob|ptree|pknob)_(Town\w+?)_s(\d+)_plan\.csv$")
END_FRAC = 0.02      # 범위 끝 근처 판정 폭(상자 폭 비율). 설계의 2%
MIN_VTICKS = 20      # 칸 안 차량 평균을 낼 최소 틱. 판단값
MIN_VEH = 5          # 칸 안 분산을 낼 최소 차량 수. 판단값
MIN_FREE = 50        # TM 압축 기울기에 쓸 차량의 최소 자유 주행 틱. 판단값

def use_exp3():
    # 실험 3을 실험 4와 같은 잣대로 다시 읽기(realism만). precision 구간은 실험 3 판에 없던 것이라 사후 추가
    global UNITS, ARMS, CONTR, LABEL, DIV
    UNITS, ARMS, LABEL = UNITS3, ["plain", "knob", "tree3"], "실험 3"
    CONTR = [("tree3", "knob"), ("knob", "plain")]
    DIV = os.path.join(ROOT, "results", "exp3", "diversity", "tests.csv")


def use_exp4():
    # import 때 바꾸면 같은 프로세스의 실험 3 시험이 ptree를 봄. 명령 시작에서만 바꿈
    E.TREE_ARM, E.SETS = "ptree", "exp4_*"


def style_of(tree):
    return str(tree).split("/")[0].replace("exp4_", "")


def t_ci(x, level=0.95):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return np.nan, np.nan
    h = tdist.ppf(0.5 + level / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return x.mean() - h, x.mean() + h


def welch(a, b, level=0.95):
    """(평균 a - 평균 b, 하한, 상한). 트리 단위 두 묶음"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    d = a.mean() - b.mean()
    h = tdist.ppf(0.5 + level / 2, df) * np.sqrt(va + vb)
    return d, d - h, d + h


def verdict(lo, hi):
    return "증가" if lo > 0 else "감소" if hi < 0 else "판정 안 함"


def write(name, lines):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("-> %s" % os.path.relpath(os.path.join(OUT, name), ROOT))


def load_plans(arm, src=SRC):
    out = []
    for f in sorted(glob.glob(os.path.join(src, "%s_*_plan.csv" % arm))):
        m = PLAN_RE.match(os.path.basename(f))
        if not m or not os.path.exists(f[:-9] + ".csv"):
            continue
        p = pd.read_csv(f)
        p["town"], p["seed"] = m.group(2), int(m.group(3))
        out.append(p)
    return pd.concat(out, ignore_index=True)


KEYS = list(N.KNOBS) + ["lane_change"]


def applied(arm, src=SRC):
    """차량별 적용값. ptree는 측정 구간 want 평균(시간 평균), 나머지는 상수"""
    P = load_plans(arm, src)
    if arm == "ptree":
        W = P.want.map(want_means)
        for k in KEYS:
            P["draw_" + k] = P[k].astype(str).map(lambda x: 1.0 if x == "True" else 0.0 if x == "False" else float(x))
            P["app_" + k] = W.map(lambda d, k=k: d.get(k, np.nan))
    else:
        for k in KEYS:
            P["app_" + k] = P[k].astype(str).map(lambda x: 1.0 if x == "True" else 0.0 if x == "False" else float(x))
    return P


def exp3_tree_deltas():
    """실험 3 트리별 평균 이동(적용 - 추첨). 덱의 신중 수렴 그림용 기술. 실험 3 want는 워밍업 포함 평균(11-20 전 수집)"""
    from exp4_check import want_means
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "outputs", "arms_exp3", "tree3_*_plan.csv"))):
        P = pd.read_csv(f)
        W = P.want.map(want_means)
        for k in ("speed_diff", "gap_m"):
            P["d_" + k] = W.map(lambda d, k=k: d.get(k, np.nan)) - P[k].astype(float)
        out.append(P[["tree", "d_speed_diff", "d_gap_m"]])
    T = pd.concat(out).groupby("tree").mean()
    T["n_veh"] = pd.concat(out).groupby("tree").size()
    d = os.path.join(ROOT, "results", "exp3", "rescore")
    os.makedirs(d, exist_ok=True)
    T.to_csv(os.path.join(d, "tree_deltas.csv"))
    print("-> results/exp3/rescore/tree_deltas.csv  감속 %d/%d, 간격 넓힘 %d/%d" % (
        (T.d_speed_diff > 0).sum(), len(T), (T.d_gap_m > 0).sum(), len(T)))


# ---- 조작 확인 ----

def manip_table(P):
    """트리별 델타(적용 - 추첨) 평균과 영역 묶음 대비"""
    for k in KEYS:
        P["d_" + k] = P["app_" + k] - P["draw_" + k]
    T = P.groupby("tree")[["d_" + k for k in KEYS]].mean()
    T["n_veh"] = P.groupby("tree").size()
    T["style"] = [style_of(t) for t in T.index]
    res = {}
    for k in ("speed_diff", "gap_m"):
        a = T[T["style"].isin(FAST)]["d_" + k]
        b = T[T["style"].isin(SLOW)]["d_" + k]
        res[k] = welch(a, b)
    return T, res


def manip():
    P = applied("ptree")
    T, res = manip_table(P)
    os.makedirs(OUT, exist_ok=True)
    T.to_csv(os.path.join(OUT, "manip_trees.csv"))
    ok = all(res[k][2] < 0 for k in res)
    L = ["# 실험 4 조작 확인", "", "생성: `scripts/exp4_eval.py manip`. `ptree` 차량별 델타 = 측정 구간 적용값 평균 - 추첨값.",
         "트리마다 차량 평균을 내고 트리를 단위로 봄. speed_diff는 +가 느림(TM 관례), gap_m은 +가 넓음.", "",
         "## 판정", "",
         "(무모, 분노) 트리 평균 델타 - (불안, 신중) 트리 평균 델타. Welch 95% t 구간. 통과는 둘 다 상한 < 0.", "",
         "| 설정 | 차이 | 95% 구간 | 기대 |", "|---|---|---|---|"]
    for k, lab in (("speed_diff", "더 빠름(음수)"), ("gap_m", "더 좁음(음수)")):
        d, lo, hi = res[k]
        L.append("| %s | %+.3f | [%+.3f, %+.3f] | %s |" % (k, d, lo, hi, lab))
    L += ["", "**조작 확인 %s.**" % ("통과" if ok else "실패. 다양성 결과를 성향 효과로 읽지 않음"), "",
          "## 영역별 기술", "", "트리 평균의 평균(트리 5개), 괄호는 트리 간 sd.", "",
          "| 영역 | 트리 | 차량 | " + " | ".join(KEYS) + " |", "|---" * (len(KEYS) + 3) + "|"]
    for st in STYLES:
        g = T[T["style"] == st]
        if g.empty:
            continue
        L.append("| %s | %d | %d | " % (st, len(g), int(g.n_veh.sum())) + " | ".join(
            "%+.2f (%.2f)" % (g["d_" + k].mean(), g["d_" + k].std(ddof=1)) for k in KEYS) + " |")
    write("manip.md", L)
    return ok


# ---- 정적 결함 ----

def static_defects():
    rows, dead_ids = [], set()
    for name, (B, order) in E.trees().items():
        smt = S.Smt(B)
        errs, _o = S.semantic_errors(B, smt, order)
        dead = [re.match(r"Behaviour '([^']+)'", e).group(1) for e in errs if "can never all hold" in e]
        shad = [re.match(r"Behaviour '([^']+)'", e).group(1) for e in errs if "never takes effect: whenever" in e]
        tie = sum("neither is a special case" in e for e in errs)
        dead_ids |= set((name, i) for i in dead + shad)
        rows.append(dict(tree=name, style=style_of(name), n=len(B), dead=len(dead), shadow=len(shad), tie=tie,
                         shadow_rate=len(shad) / len(B), static_dead_rate=(len(dead) + len(shad)) / len(B)))
        print("  %s 가려짐 %d, 모순 %d, 동률 %d" % (name, len(shad), len(dead), tie), flush=True)
    return pd.DataFrame(rows), dead_ids


def smt():
    D, dead_ids = static_defects()
    os.makedirs(OUT, exist_ok=True)
    D.to_csv(os.path.join(OUT, "smt_trees.csv"), index=False)
    lo, hi = t_ci(D.shadow_rate)
    L = ["# 실험 4 정적 결함(SMT)", "", "생성: `scripts/exp4_eval.py smt`. 루프가 이 검사를 향해 고치므로 증거가 아니라 남은 결함의 기술.", "",
         "| 지표 | 값 |", "|---|---|",
         "| 트리 | %d |" % len(D), "| 배치 행동 | %d |" % D.n.sum(),
         "| 가려진 행동 | %d (%.1f%%) |" % (D.shadow.sum(), 100 * D.shadow.sum() / D.n.sum()),
         "| 조건 모순 | %d |" % D.dead.sum(), "| 우선순위 동률(오류 아님) | %d |" % D.tie.sum(),
         "| 가려짐 있는 트리 | %d/%d |" % ((D.shadow > 0).sum(), len(D)),
         "| 트리별 가려짐 비율 평균 [95%% t] | %.3f [%.3f, %.3f] |" % (D.shadow_rate.mean(), lo, hi), "",
         "| 영역 | 트리 | 행동 | 가려짐 | 모순 | 동률 |", "|---|---|---|---|---|---|"]
    for st in STYLES:
        g = D[D["style"] == st]
        L.append("| %s | %d | %d | %d | %d | %d |" % (st, len(g), g.n.sum(), g.shadow.sum(), g.dead.sum(), g.tie.sum()))
    fp = os.path.join(OUT, "e2_firing.csv")
    L += ["", "## 실주행 발화 대조", ""]
    if not os.path.exists(fp):
        L.append("`e2_firing.csv`가 없어 안 함. `exp3_eval.py logs --arm ptree --sets \"exp4_*\" --out results/exp4` 뒤 다시.")
    else:
        F = pd.read_csv(fp)
        F["static_dead"] = [(t, i) in dead_ids for t, i in zip(F.tree, F.id)]
        bad = F[F.static_dead & (F.fired > 0)]
        L += ["| 묶음 | 행동 | 발화 0 |", "|---|---|---|",
              "| 정적으로 죽음 | %d | %d |" % (F.static_dead.sum(), int((F.static_dead & (F.fired == 0)).sum())),
              "| 나머지 | %d | %d |" % ((~F.static_dead).sum(), int((~F.static_dead & (F.fired == 0)).sum())), "",
              "**일치 점검 %s.** 정적으로 죽은 행동 중 실주행에서 켜진 것 %d개." % (
                  "통과" if bad.empty else "실패. SMT 인코딩이나 실행기 오류로 보고 이 지표 철회", len(bad))]
        for r in bad.itertuples():
            L.append("- %s %s 발화 %d" % (r.tree, r.id, r.fired))
    write("smt.md", L)


# ---- recall, precision ----

def precision_by_town(units, arms, st):
    """slides_figs.precision과 같은 칸, 같은 오프셋, 같은 실측 파일. 맵마다(시드 평균) 돌려줌"""
    import measure as M
    import slides_figs as SF
    sc = M.load_scale()[st]
    R = pd.read_csv(SF.REAL, usecols=["stratum"] + M.KIN)
    R = R[R.stratum == st][M.KIN].to_numpy(float) / sc
    R = R[np.isfinite(R).all(1)]
    rng = np.random.RandomState(0)
    offs = [rng.rand(len(M.KIN)) for _ in range(10)]
    seen = [set(map(tuple, np.floor(R + o).astype(np.int32))) for o in offs]
    rows = []
    for f in glob.glob(os.path.join(units, "*.csv")):
        m = M.FILE_RE.match(os.path.basename(f))
        if not m or m.group(1) not in arms:
            continue
        u = pd.read_csv(f)
        Z = u[u.stratum == st][M.KIN].to_numpy(float) / sc
        Z = Z[np.isfinite(Z).all(1)]
        if len(Z) == 0:
            continue
        p = np.mean([np.mean([tuple(c) in s for c in np.floor(Z + o).astype(np.int32)]) for o, s in zip(offs, seen)])
        rows.append(dict(arm=m.group(1), town=m.group(2), prec=p))
    return pd.DataFrame(rows).groupby(["town", "arm"]).prec.mean().unstack("arm")


JOINT = [  # (q1, recall, precision) -> 서술. design_exp4.md 다양성 절 표 그대로
    (lambda q, r, p: q == "증가" and r == "증가" and p != "감소", "실측 행동 쪽으로 다양해짐"),
    (lambda q, r, p: q == "증가" and r == "판정 안 함" and p != "감소", "다양해졌으나 실측 포괄은 안 늘어남"),
    (lambda q, r, p: q == "증가" and r == "감소" and p != "감소", "다양해졌으나 실측 행동 포괄은 줄어듦"),
    (lambda q, r, p: q == "증가" and p == "감소", "실측 밖 움직임이 늘어 다양해짐(부작용으로 서술)"),
    (lambda q, r, p: q != "증가", "다양성 효과 없음, 또는 깎임"),
]


def joint_text(q, r, p):
    return next(txt for f, txt in JOINT if f(q, r, p))


def realism():
    import measure as M
    import realism as R
    import rep_realism as RR
    sc = M.load_scale()
    real = pd.read_csv(os.path.join(R.REAL_DIR, "units.csv"))
    real = real[real.site.map(R.src_of).isin(R.SOURCES)]
    u = RR.load_units(UNITS, ARMS)
    cov = R.coverage(u, real, ARMS, sc)
    rows = []
    for st in M.TESTED:
        rec = cov[cov.stratum == st].pivot(index="town", columns="arm", values="cov_unit") * 100
        pre = precision_by_town(UNITS, ARMS, st) * 100
        for a in ARMS:
            rows.append(dict(stratum=st, kind="level", contrast=a, recall=rec[a].mean(), precision=pre[a].mean()))
        for a, b in CONTR:
            for lab, X in (("recall", rec), ("precision", pre)):
                d = (X[a] - X[b]).dropna().to_numpy(float)
                lo, hi = t_ci(d)
                rows.append(dict(stratum=st, kind=lab, contrast="%s-%s" % (a, b), diff=d.mean(), ci_lo=lo, ci_hi=hi,
                                 maps=len(d), wins=int((d > 0).sum()), verdict=verdict(lo, hi)))
    T = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    T.to_csv(os.path.join(OUT, "realism.csv"), index=False)
    L = ["# %s 실측 행동 포괄" % LABEL, "", "생성: `scripts/exp4_eval.py realism%s`. 맵 단위(%%), 대비는 맵 8개 95%% t 구간. "
         "보정 없음." % (" --exp3" if LABEL == "실험 3" else ""),
         "recall은 `realism.coverage`, precision은 `slides_figs.precision`과 같은 규칙.", ""]
    for st in M.TESTED:
        s = T[T.stratum == st]
        L += ["## %s" % st, "", "| 조건 | recall | precision |", "|---|---|---|"]
        for r in s[s.kind == "level"].itertuples():
            L.append("| %s | %.1f | %.1f |" % (r.contrast, r.recall, r.precision))
        L += ["", "| 대비 | 지표 | 차이(%p) | 95% 구간 | 앞쪽 우세 맵 | 판정 |", "|---|---|---|---|---|---|"]
        for r in s[s.kind != "level"].itertuples():
            L.append("| %s | %s | %+.2f | [%+.2f, %+.2f] | %d/%d | %s |" % (
                r.contrast, r.kind, r.diff, r.ci_lo, r.ci_hi, r.wins, r.maps, r.verdict))
        L.append("")
    tp = DIV or os.path.join(OUT, "diversity", "tests.csv")
    L += ["## 묶어 읽기", ""]
    if not os.path.exists(tp):
        L.append("`diversity/tests.csv`가 없어 안 함. `measure.py score` 뒤 다시.")
    else:
        Q = pd.read_csv(tp)
        Q = Q[Q.metric == "diversity_q1"]
        L += ["| 대비 | 층 | q1 | recall | precision | 서술 |", "|---|---|---|---|---|---|"]
        for a, b in CONTR[:2]:
            c = "%s-%s" % (a, b)
            for st in M.TESTED:
                q = Q[(Q.contrast == c) & (Q.stratum == st)]
                if q.empty:
                    continue
                qv = verdict(q.ci_lo.iloc[0], q.ci_hi.iloc[0])
                rv = T[(T.stratum == st) & (T.contrast == c) & (T.kind == "recall")].verdict.iloc[0]
                pr = T[(T.stratum == st) & (T.contrast == c) & (T.kind == "precision")].iloc[0]
                pv = "감소" if pr.ci_hi < 0 else "감소 아님"
                L.append("| %s | %s | %s | %s | %s | %s |" % (c, st, qv, rv, pv, joint_text(qv, rv, pv)))
    write("realism.md", L)


# ---- Vendi 민감도(탐색) ----

VENDI_H = (0.5, 1.0, 2.0)   # 커널 폭, 축척 단위. 격자 폭 0.5, 1, 2와 맞춤. 판단값


def vendi_runs(u, hs=VENDI_H, ndraw=20, seed=0):
    """실행마다 Vendi score. 층 안 모든 실행을 q1과 같은 깊이(최소 단위 수)로 뽑고 ndraw번 평균.
    가우스 커널이라 대각 1, K/n의 고윳값 합이 1. 수치 음수 고윳값은 0으로"""
    import measure as M
    scd = M.load_scale()
    rng = np.random.RandomState(seed)
    rows = []
    for st in M.TESTED:
        us = u[u["stratum"] == st]
        groups = [(k, g) for k, g in us.groupby(["arm", "town", "seed"]) if len(g) >= M.MIN_U]
        if not groups:
            continue
        nr = min(len(g) for _k, g in groups)
        for (arm, town, sd), g in groups:
            Z = g[M.KIN].to_numpy(float) / scd[st]
            acc = np.zeros(len(hs))
            for _ in range(ndraw):
                X = Z[rng.choice(len(Z), nr, replace=False)]
                sq = (X * X).sum(1)
                D2 = np.maximum(sq[:, None] + sq[None, :] - 2 * X @ X.T, 0)
                for j, h in enumerate(hs):
                    lam = np.clip(np.linalg.eigvalsh(np.exp(-D2 / (2 * h * h)) / nr), 0, None)
                    lam = lam[lam > 1e-12] / lam.sum()
                    acc[j] += np.exp(-(lam * np.log(lam)).sum())
            r = {"stratum": st, "arm": arm, "town": town, "seed": sd, "nr": nr}
            r.update({"vs_h%g" % h: v / ndraw for h, v in zip(hs, acc)})
            rows.append(r)
    return pd.DataFrame(rows)


def vendi():
    import measure as M
    import rep_realism as RR
    M.ARMS = list(ARMS)
    u = RR.load_units(UNITS, ARMS)
    keep = M.check_balance(u)
    u = u[[(t, s) in keep for t, s in zip(u["town"], u["seed"])]]
    V = vendi_runs(u)
    os.makedirs(OUT, exist_ok=True)
    V.to_csv(os.path.join(OUT, "vendi_runs.csv"), index=False)
    want = set("%s-%s" % c for c in CONTR)
    rows = []
    for h in VENDI_H:
        col = "vs_h%g" % h
        rows += [r for r in M.tests(M.map_ratio(V, col), col) if r["contrast"] in want]
        for r in M.summarize(M.map_ratio(V, col), col):
            if r["stratum"] in M.TESTED:
                rows.append(dict(r, contrast="level:" + r["arm"]))
    T = pd.DataFrame(rows)
    T.to_csv(os.path.join(OUT, "vendi.csv"), index=False)
    q1 = None
    tp = DIV or os.path.join(OUT, "diversity", "tests.csv")
    if os.path.exists(tp):
        q1 = pd.read_csv(tp)
        q1 = q1[q1.metric == "diversity_q1"].set_index(["contrast", "stratum"])
    L = ["# %s 다양성 민감도: Vendi score(탐색)" % LABEL, "",
         "생성: `scripts/exp4_eval.py vendi%s`. 격자 칸 없이 점 사이 가우스 커널로 잰 다양성. 결과를 본 뒤 추가한 탐색이라 "
         "주 지표(q1) 판정을 바꾸지 않음. 점 수는 q1 희박화 깊이와 같고 20번 평균. 기준 대비 배수의 맵 8개 95%% t 구간, "
         "판정 규칙은 q1과 같음." % (" --exp3" if LABEL == "실험 3" else ""), "",
         "| 대비 | 층 | q1(주, 격자) | h = 0.5 | h = 1 | h = 2 |", "|---|---|---|---|---|---|"]
    for c in sorted(want, key=lambda c: [("%s-%s" % x) for x in CONTR].index(c)):
        for st in M.TESTED:
            cells = []
            if q1 is not None and (c, st) in q1.index:
                r = q1.loc[(c, st)]
                cells.append("%+.2f [%+.2f, %+.2f] %s" % (r["diff"], r.ci_lo, r.ci_hi, verdict(r.ci_lo, r.ci_hi)))
            else:
                cells.append("")
            for h in VENDI_H:
                r = T[(T.metric == "vs_h%g" % h) & (T.contrast == c) & (T.stratum == st)].iloc[0]
                cells.append("%+.2f [%+.2f, %+.2f] %s" % (r["diff"], r.ci_lo, r.ci_hi, verdict(r.ci_lo, r.ci_hi)))
            L.append("| %s | %s | %s |" % (c, st, " | ".join(cells)))
    L += ["", "## 수준(기준 대비 배수, 맵 평균)", "", "| 층 | 조건 | h = 0.5 | h = 1 | h = 2 |", "|---|---|---|---|---|"]
    for st in M.TESTED:
        for a in ARMS:
            v = [T[(T.metric == "vs_h%g" % h) & (T.contrast == "level:" + a) & (T.stratum == st)]["mean"].iloc[0]
                 for h in VENDI_H]
            L.append("| %s | %s | %s |" % (st, a, " | ".join("%.3f" % x for x in v)))
    write("vendi.md", L)


# ---- 시드 잡음 바닥 ----

def floor():
    import measure as M
    import rep_realism as RR
    u = pd.concat([RR.load_units(UNITS3, ["plain", "knob"]), RR.load_units(UNITS, ["plain", "knob"])],
                  ignore_index=True)
    div = M.diversity(u)
    div["set"] = np.where(div.seed >= 24, "s24_31", "s16_23")
    os.makedirs(OUT, exist_ok=True)
    div.to_csv(os.path.join(OUT, "floor_runs.csv"), index=False)
    L = ["# 시드 잡음 바닥", "", "생성: `scripts/exp4_eval.py floor`. 실험 3(시드 16~23)과 실험 4(24~31)의 plain, knob을 한 판으로 "
         "채점(희박화 깊이 공유). 맵마다 knob/plain q1 배수의 차이(24~31 빼기 16~23). 통과는 구간이 0을 품음.", "",
         "| 층 | 맵 | 차이 | 95% 구간 | 통과 |", "|---|---|---|---|---|"]
    for st in M.TESTED:
        m = div[div.stratum == st].groupby(["town", "set", "arm"]).q1.mean().unstack(["set", "arm"])
        r = {s: m[(s, "knob")] / m[(s, "plain")] for s in ("s16_23", "s24_31")}
        d = (r["s24_31"] - r["s16_23"]).dropna().to_numpy(float)
        lo, hi = t_ci(d)
        L.append("| %s | %d | %+.3f | [%+.3f, %+.3f] | %s |" % (st, len(d), d.mean(), lo, hi,
                                                              "예" if lo <= 0 <= hi else "아니오"))
    L += ["", "이 폭이 본 대비(`ptree - knob`, `ptree - pknob`) 차이와 비슷하면 그 차이는 시드 바뀜 수준이라는 뜻(기술)."]
    write("floor.md", L)


# ---- 기제 기술 ----

def mech(ticks=False):
    box = {k: (lo, hi) for k, (_b, lo, hi) in N.KNOBS.items()}
    L = ["# 실험 4 기제 기술(판정 밖)", "", "생성: `scripts/exp4_eval.py mech%s`." % (" --ticks" if ticks else ""), "",
         "## 적용값 퍼짐", "",
         "차량별 적용값(ptree는 측정 구간 시간 평균, 나머지는 상수)의 판(실행) 안 차량 간 sd를 판마다 내고 평균. "
         "범위 끝은 상자 폭 %g%% 안. ptree는 시간 평균이라 틱 단위 자름보다 작게 셈." % (END_FRAC * 100), "",
         "| 조건 | speed_diff sd | gap_m sd | speed_diff 끝 비율 | gap_m 끝 비율 |", "|---|---|---|---|---|"]
    P = {a: applied(a) for a in ARMS}
    for a in ARMS:
        p = P[a]
        sd = {k: p.groupby(["town", "seed"])["app_" + k].std(ddof=1).mean() for k in ("speed_diff", "gap_m")}
        end = {}
        for k in ("speed_diff", "gap_m"):
            lo, hi = box[k]
            w = END_FRAC * (hi - lo)
            v = p["app_" + k]
            end[k] = ((v <= lo + w) | (v >= hi - w)).mean()
        L.append("| %s | %.2f | %.3f | %.3f | %.3f |" % (a, sd["speed_diff"], sd["gap_m"], end["speed_diff"], end["gap_m"]))
    pt = P["ptree"]
    L += ["", "ptree 추첨값과 델타의 상관(차량 전체, Pearson): " + ", ".join(
        "%s %+.2f" % (k, np.corrcoef(pt["draw_" + k], pt["app_" + k] - pt["draw_" + k])[0, 1])
        for k in ("speed_diff", "gap_m")) + ". 음수면 끝 쪽 추첨값이 안쪽으로 밀림(자름 포함).", ""]
    if ticks:
        L += mech_ticks(P)
    else:
        L.append("틱 기록 기반 둘(칸 안 분산, TM 압축)은 `--ticks`로.")
    write("mech.md", L)


def mech_ticks(P):
    cols = ["vid", "speed_kmh", "lead", "lead_stopped", "gap_m", "dv", "in_junction", "junction_m", "tl", "limit_kmh",
            "rightmost", "leftmost", "lane_count", "lane_time_s", "curvature"]
    vstop = N.SENSE["v_stop_kmh"]
    rows = []
    for a in ("knob", "ptree", "pknob"):
        p = P[a]
        for (town, seed), pp in p.groupby(["town", "seed"]):
            base = os.path.join(SRC, "%s_%s_s%d" % (a, town, seed))
            T = pd.read_csv(base + "_tick.csv.gz", usecols=cols, na_filter=False, dtype={"tl": str})
            A = E.arrays(T)
            cid = E.cell_of(A)
            mov = A["speed"] >= vstop
            ratio = A["speed"] / np.maximum(A["lim"], 1e-9)
            # 칸 안 차량 간 분산: (칸, 차) 평균 속도비 -> 칸마다 차량 간 분산 -> 차량 수 가중 평균
            d = pd.DataFrame({"c": cid, "v": T.vid.to_numpy(), "r": ratio})[(cid >= 0) & mov]
            g = d.groupby(["c", "v"]).r.agg(["mean", "count"])
            g = g[g["count"] >= MIN_VTICKS]
            cv = g.groupby(level=0)["mean"].agg(["var", "count"])
            cv = cv[cv["count"] >= MIN_VEH]
            wvar = float((cv["var"] * cv["count"]).sum() / cv["count"].sum()) if len(cv) else np.nan
            # TM 압축: 자유 주행 틱(앞차 없음, 교차로 밖 30 m 넘게, 적색 아님, 움직임)의 차량 평균 속도비를
            # 명령 비(1 - 적용 speed_diff/100)에 회귀. ptree는 시간 평균 적용값이라 근사
            free = mov & ~A["lead"] & ~A["inj"] & (A["jm"] > E.APPROACH_M) & ~A["red"]
            f = pd.DataFrame({"v": T.vid.to_numpy(), "r": ratio})[free].groupby("v").r.agg(["mean", "count"])
            f = f[f["count"] >= MIN_FREE]
            cmd = 1 - pp.set_index("vid")["app_speed_diff"].reindex(f.index) / 100.0
            ok = np.isfinite(cmd.to_numpy())
            slope = np.polyfit(cmd.to_numpy()[ok], f["mean"].to_numpy()[ok], 1)[0] if ok.sum() >= 10 else np.nan
            rows.append(dict(arm=a, town=town, seed=seed, cell_var=wvar, tm_slope=slope))
            print("  %s %s s%d" % (a, town, seed), flush=True)
    D = pd.DataFrame(rows)
    D.to_csv(os.path.join(OUT, "mech_ticks.csv"), index=False)
    m = D.groupby(["town", "arm"])[["cell_var", "tm_slope"]].mean()
    L = ["## 같은 상황 칸 안 차량 간 속도비 분산", "",
         "실험 3 E3 칸. (칸, 차) 평균 속도비(정지 틱 뺌, 차당 %d틱 이상), 칸마다 차량 간 분산(%d대 이상), 차량 수 가중 평균. "
         "ptree가 knob보다 작으면 트리가 같은 상황의 차들을 비슷하게 만든 것." % (MIN_VTICKS, MIN_VEH), "",
         "| 조건 | 맵 평균 |", "|---|---|"]
    cvar = m.cell_var.unstack("arm")
    for a in ("knob", "ptree", "pknob"):
        L.append("| %s | %.5f |" % (a, cvar[a].mean()))
    L += ["", "| 대비 | 차이 | 95% 구간 | 앞쪽 큰 맵 |", "|---|---|---|---|"]
    for a, b in (("ptree", "knob"), ("ptree", "pknob"), ("pknob", "knob")):
        d = (cvar[a] - cvar[b]).dropna().to_numpy(float)
        lo, hi = t_ci(d)
        L.append("| %s-%s | %+.5f | [%+.5f, %+.5f] | %d/%d |" % (a, b, d.mean(), lo, hi, (d > 0).sum(), len(d)))
    sl = m.tm_slope.unstack("arm")
    L += ["", "## TM 압축", "",
          "자유 주행 틱(앞차 없음, 교차로 밖 %g m 초과, 적색 아님, 움직임) 차량 평균 속도비를 명령 비(1 - speed_diff/100)에 "
          "회귀한 기울기. 판마다 차량 %d틱 이상, 차량 10대 이상. 1이면 명령이 그대로 실현." % (E.APPROACH_M, MIN_FREE), "",
          "| 조건 | 기울기 맵 평균 | 95% 구간 |", "|---|---|---|"]
    for a in ("knob", "ptree", "pknob"):
        lo, hi = t_ci(sl[a])
        L.append("| %s | %.3f | [%.3f, %.3f] |" % (a, sl[a].mean(), lo, hi))
    return L


# ---- 위약 대조 ----

def placebo():
    """실현율 계산에서 ptree 판 자리에 같은 시드 plain 판을 넣음. plain 차에 같은 자리 ptree 차의 트리, 파라미터를 붙여
    끝 명령을 예측(실행기 기록이 없으니 예측 마스크)하고 knob 같은 자리와 비교. 트리가 실제로 안 돌았으니 실현 비율이
    0.5 근처여야 함. 크게 벗어나면 실현율이 상황 선택만으로도 나온다는 뜻"""
    TR = E.trees()
    files = E.run_files(SRC)
    real = []
    for (arm, town, seed), base in sorted(files.items()):
        if arm != "plain" or ("ptree", town, seed) not in files or ("knob", town, seed) not in files:
            continue
        TP = pd.read_csv(files[("ptree", town, seed)] + "_plan.csv")
        by_sidx = dict((int(r.spawn_idx), (r.tree, E.parse_params(r.params))) for r in TP.itertuples())
        PP = pd.read_csv(base + "_plan.csv")
        vinfo = dict((r.vid, by_sidx.get(int(r.spawn_idx), (None, {})) + (int(r.spawn_idx),)) for r in PP.itertuples())
        T = E.load_tick(base)
        T["tree"] = T.vid.map(lambda v: vinfo[v][0])
        kb = files[("knob", town, seed)]
        KP = pd.read_csv(kb + "_plan.csv")
        K = E.load_tick(kb)
        K["sidx"] = K.vid.map(dict(zip(KP.vid, KP.spawn_idx)))
        for name, sub in T.groupby("tree"):
            if name not in TR:
                continue
            B, order = TR[name]
            sub = sub.reset_index(drop=True)
            A = E.arrays(sub)
            pv = sub.vid.map(lambda v: vinfo[v][1])
            th = dict((p, pv.map(lambda d, p=p: d.get(p, np.nan)).to_numpy(float))
                      for p in set().union(*[set(d) for d in pv]) if pv.size)
            pred = E.final_masks(B, order, A, th)
            sidx_of = sub.vid.map(lambda v: vinfo[v][2])
            KS = K[K.sidx.isin(set(sidx_of))].reset_index(drop=True)
            if KS.empty:
                continue
            p_of = dict((vinfo[v][2], vinfo[v][1]) for v in set(sub.vid))
            kpv = KS.sidx.map(p_of)
            kth = dict((p, kpv.map(lambda d, p=p: d.get(p, np.nan)).to_numpy(float)) for p in th)
            KA = E.arrays(KS)
            kpred = E.final_masks(B, order, KA, kth)

            def obs(X):
                return {"speed": np.where(X["speed"] >= N.SENSE["v_stop_kmh"], X["speed"] / np.maximum(X["lim"], 1e-9),
                                          np.nan),
                        "gap": np.where(X["lead"] & (X["gap"] < 100), X["gap"], np.nan)}
            ot_all, ok_all = obs(A), obs(KA)
            st, ks_ = sidx_of.to_numpy(), KS.sidx.to_numpy()
            for k, b in enumerate(B):
                if (b["set"], b["effect"]) not in E.REAL_SIGN:
                    continue
                ot, okk = ot_all[b["set"]], ok_all[b["set"]]
                mt, mk = pred[k] & np.isfinite(ot), kpred[k] & np.isfinite(okk)
                if not mt.any() or not mk.any():
                    continue
                dt = pd.DataFrame({"s": st[mt], "v": ot[mt]}).groupby("s").v.agg(["sum", "count"])
                dk = pd.DataFrame({"s": ks_[mk], "v": okk[mk]}).groupby("s").v.agg(["sum", "count"])
                j = dt.join(dk, lsuffix="_t", rsuffix="_k", how="inner")
                if j.empty:
                    continue
                d = j.sum_t / j.count_t - j.sum_k / j.count_k
                w = np.minimum(j.count_t, j.count_k)
                real.append((name, b["id"], b["set"], b["effect"], "%s_s%d" % (town, seed), float((w * d).sum()),
                             float(w.sum()), int(j.count_t.sum()), int(j.count_k.sum())))
        print("  plain %s s%d" % (town, seed), flush=True)
    R = pd.DataFrame(real, columns=["tree", "id", "set", "effect", "cluster", "wd", "w", "nt", "nk"])
    g = E.realization(R)
    os.makedirs(OUT, exist_ok=True)
    g.to_csv(os.path.join(OUT, "placebo_realization.csv"), index=False)
    cl = sorted(R.cluster.unique())
    rng = np.random.RandomState(E.SEED)
    boots = []
    for _ in range(E.N_BOOT):
        gb = E.realization(R, list(rng.choice(cl, len(cl), replace=True)))
        jb = gb[gb.judged]
        boots.append(jb.realized.mean() if len(jb) else np.nan)
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    j = g[g.judged]
    L = ["# 실현율 위약 대조", "", "생성: `scripts/exp4_eval.py placebo`. `ptree` 판 자리에 같은 시드 `plain` 판을 넣고, plain 차에 "
         "같은 자리 ptree 차의 트리와 파라미터를 붙여 끝 명령을 예측한 뒤 실험 3 E2와 같은 식으로 knob과 비교. 트리가 실제로 "
         "안 돌았으니 기대 약 0.5.", "",
         "위약 실현 비율 %d/%d = %.3f, (맵, 시드) 묶음 부트스트랩 95%% [%.3f, %.3f]." % (
             int(j.realized.sum()), len(j), j.realized.mean() if len(j) else np.nan, lo, hi)]
    rp = os.path.join(OUT, "e2_realization.csv")
    if os.path.exists(rp):
        r = pd.read_csv(rp)
        r = r[r.judged]
        L.append("같은 행동들의 실제(`ptree`) 실현 비율 %d/%d = %.3f. 실제가 위약 구간 위에 있어야 실현율을 트리 효과로 읽음."
                 % (int(r.realized.sum()), len(r), r.realized.mean()))
    write("placebo.md", L)


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("manip", "smt", "realism", "floor", "mech", "placebo", "exp3trees", "vendi"))
    ap.add_argument("--ticks", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--exp3", action="store_true", help="realism만. 실험 3 단위를 같은 잣대로, 기본 출력 results/exp3/rescore")
    a = ap.parse_args()
    if a.cmd == "exp3trees":
        return exp3_tree_deltas()
    if a.exp3:
        if a.cmd not in ("realism", "vendi"):
            raise SystemExit("--exp3는 realism, vendi만")
        use_exp3()
        OUT = os.path.join(ROOT, "results", "exp3", "rescore")
    else:
        use_exp4()
    if a.out:
        OUT = os.path.abspath(a.out)
    E.OUT = OUT
    if a.cmd == "manip":
        manip()
    elif a.cmd == "smt":
        smt()
    elif a.cmd == "realism":
        realism()
    elif a.cmd == "floor":
        floor()
    elif a.cmd == "mech":
        mech(a.ticks)
    elif a.cmd == "placebo":
        placebo()
    elif a.cmd == "vendi":
        vendi()


if __name__ == "__main__":
    main()
