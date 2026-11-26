"""앞 판 논문의 자 넷으로 다시 재기. py3.11. 채점 경로 밖 보조 진단, 2026-11-05 사후 추가

  python diag/realism.py real      실측 단위와 곡선 캐시. 실측 원본 필요, 한 시간쯤
  python diag/realism.py curves
                                   수집물 -> 41점 속도 곡선 캐시 (data/outputs/curves/)
  python diag/realism.py score

출력 results/realism/report.md 와 자별 csv.

앞 판(JOK 원고 표 2)의 자 넷. 힐 수만 주 지표로 남기고 나머지 셋은 design-v1에서 뺐던 것.
  힐 수 q1      격자 칸의 실효 수. scripts/measure.py diversity 그대로
  실측 덮개      실측 단위 중 그 조건이 밟은 칸에 떨어지는 비율. 넓어지면 오르는 한쪽 자
  판별기 AUC     실측과 시뮬 단위를 얼마나 가르나. 좁아도 넓어도 오르는 양쪽 자. 낮을수록 실측다움
  곡선 종류 수   41점 속도 곡선끼리 RMS로 r(m/s)보다 먼 것을 몇 개 고를 수 있나. 격자 없는 자

출처: C:\\Github\\sil-agent-repertoire (커밋 a604b91). 판별기는 analyze.py main_discrim, 곡선은
analyze.py main_curve와 cut, 덮개는 repertoire.py main_coverage A. 계산 규칙은 그대로. 바꾼 것:
- 실측은 옛 레포 measure.real_frames, units를 불러 캐시. 단위 규칙(LEN, MIN_FR, A_MAX, 평활)이 지금 것과 같음
- 축척은 config/scale.yaml(옛 판 실측 다섯 종 층별 sd와 같은 값)
- 맵마다 내고 기준(plain) 대비로 맵 단위 대응 검정. 다양성 대비와 같은 틀
- 덮개는 맵 안 조건마다 실행 8개 전부를 쓰고 단위 수만 맞춤(옛 판은 실행 수도 맞춤. 지금은 다 8개)

읽는 법. 판별기 절대값은 높게 나옴: 실측 촬영지와 CARLA 맵이 다른 장소라 장소 차이까지 잡힘.
조건 사이 순서만 봄. 넷 다 사실성 판정이 아니라 각자의 투영에서 본 차이.
"""
import argparse
import gc
import glob
import importlib.util
import os
import sys
import warnings

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))
# HGB가 첫 학습 때 물리 코어를 wmic으로 세다 실패하고 traceback째 경고를 찍음. 무해라 가림
warnings.filterwarnings("ignore", message="Could not find the number of physical cores")

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from threadpoolctl import threadpool_limits

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import measure as M  # noqa: E402

OLD = r"C:\Github\sil-agent-repertoire"
REAL_DIR = os.path.join(ROOT, "data", "outputs", "real")
CURVE_DIR = os.path.join(ROOT, "data", "outputs", "curves")
OUT = os.path.join(ROOT, "results", "realism")
SOURCES = ("ngsim", "tgsim", "ind", "sind", "highd")
SEED = 0

# 판별기. 앞 판 값
NFOLD = 5
NMAX = 4000      # 층별 한쪽 표본 상한
MIN_N = 200      # 이보다 적은 (층, 조건)은 안 냄

# 곡선. 앞 판 값
NG = 41
NSUB = 250       # (실행, 층)마다 뽑는 곡선 수. 거리 행렬이 n^2이라 크게 못 잡음
RAD = (0.5, 1.0, 1.5, 2.0, 3.0)   # m/s. 하나로 안 박고 훑음
RMAIN = 1.0      # 표 2에 올린 반지름. 앞 판과 같은 판단값
NORD = 10        # 포장 순서 반복

# 덮개
NDRAW_COV = 10   # 격자 원점 반복. 앞 판 값
MIN_REAL = 500   # 그 층 실측 단위가 이보다 적으면 안 냄. 앞 판 값


def src_of(site):
    s = str(site).lower()
    return next((x for x in SOURCES if s.startswith(x)), "other")


# ---- 곡선 자르기 ----

def cut(d):
    """프레임 표 -> (층, 41점 속도 곡선). measure.units와 같은 run, 같은 창, 같은 교차로 절단 판정.
    시간축 정규화라 지속시간은 버려짐. 정차는 거리가 안 늘어 거리축 보간이 깨져서 시간축"""
    d = d[d["stratum"].notna()].sort_values(["uid", "t"]).reset_index(drop=True)
    if d.empty:
        return [], np.zeros((0, NG), np.float32)
    nw = ((d["uid"] != d["uid"].shift()) | (d["stratum"] != d["stratum"].shift())
          | (d["t"].diff().abs() > 1.5 * M.DT))
    d["run"] = nw.cumsum()
    sp = d["step"].to_numpy(float).copy()
    sp[nw.to_numpy()] = 0.0
    d["s"] = pd.Series(sp).groupby(d["run"]).cumsum().to_numpy()
    nrun = d.groupby("uid")["run"].nunique()
    sub = d[d["uid"].isin(set(nrun[nrun >= 3].index))]
    first = set(sub.groupby("uid")["run"].min()) if len(sub) else set()
    last = set(sub.groupby("uid")["run"].max()) if len(sub) else set()
    grid = np.linspace(0, 1, NG)
    S, P = [], []
    for run, g in d.groupby("run", sort=False):
        st = g["stratum"].iloc[0]
        L = M.LEN.get(st)
        v, a = g["v"].to_numpy(float), g["a"].to_numpy(float)
        s, tt = g["s"].to_numpy(float), g["t"].to_numpy(float)
        step = np.r_[0.0, np.diff(s)]
        if L is None:
            if run in first or run in last:
                continue
            parts = [np.ones(len(g), bool)]
        else:
            w = (s // L).astype(int)
            parts = [w == k for k in range(int(s[-1] // L))]
        for m in parts:
            if m.sum() < 10 or not M.sig_of(v[m], a[m], step[m]):
                continue
            t0 = tt[m]
            if np.ptp(t0) <= 0 or not np.isfinite(v[m]).all():
                continue
            S.append(st)
            P.append(np.interp(grid, (t0 - t0[0]) / np.ptp(t0), v[m]))
    return S, np.array(P, np.float32).reshape(-1, NG)


def dmat(P):
    q = (P * P).mean(1)
    d2 = q[:, None] + q[None, :] - 2 * (P @ P.T) / P.shape[1]
    return np.sqrt(np.maximum(d2, 0.0))


def packing(D, r, rng):
    """서로 r보다 먼 곡선 최대 개수(탐욕). 순서에 걸려서 NORD번 평균. q0 대응"""
    tot = 0
    for _ in range(NORD):
        keep = []
        for i in rng.permutation(len(D)):
            if all(D[i, j] > r for j in keep):
                keep.append(i)
        tot += len(keep)
    return tot / float(NORD)


def lc_div(D, r):
    """Leinster-Cobbold 유사도 가중 다양성 차수 1. q1 대응"""
    zp = np.maximum(np.exp(-D / r).mean(1), 1e-300)
    return float(np.exp(-np.mean(np.log(zp))))


def curve_score(P, rng):
    """raw는 곡선 그대로(속도 수준 포함), shape는 곡선마다 자기 평균 뺌(모양만)"""
    out = {}
    for kind, Q in (("raw", P), ("shape", P - P.mean(1, keepdims=True))):
        D = dmat(Q.astype(float))
        for r in RAD:
            out["%s_pack_%g" % (kind, r)] = packing(D, r, rng)
            out["%s_lc_%g" % (kind, r)] = lc_div(D, r)
    return out


# ---- 캐시 ----

def main_real():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(SOURCES))
    a = ap.parse_args()
    # 옛 로더가 data/... 상대경로라 옛 레포로 이동해서 부름. 모듈명이 measure라 따로 적재
    spec = importlib.util.spec_from_file_location("old_measure", os.path.join(OLD, "scripts", "measure.py"))
    G = importlib.util.module_from_spec(spec)
    sys.modules["old_measure"] = G   # 옛 모듈이 sys.modules[__name__]로 자기를 찾음
    # 다섯 종을 합치면 3800만 프레임이라 합치는 순간 메모리가 터짐(871 MiB 할당 실패).
    # 출처 하나씩 읽고 촬영지마다 잘라서 버림. run이 차량 안에서만 이어져서 촬영지별로 잘라도 같음
    U, S, P, site = [], [], [], []
    here = os.getcwd()
    os.chdir(OLD)
    try:
        spec.loader.exec_module(G)
        for nm in a.sources.split(","):
            d = G.real_frames(nm)
            print("%s 프레임 %d" % (nm, len(d)), flush=True)
            for s, g in d.groupby("site"):
                U.append(G.units(g, extra=("site",))[["uid", "site", "stratum"] + M.KIN])
                ss, pp = cut(g)
                S += ss
                P.append(pp)
                site += [s] * len(ss)
            d = g = None
            gc.collect()
    finally:
        os.chdir(here)
    os.makedirs(REAL_DIR, exist_ok=True)
    u = pd.concat(U, ignore_index=True)
    u.to_csv(os.path.join(REAL_DIR, "units.csv"), index=False)
    np.savez_compressed(os.path.join(REAL_DIR, "curves.npz"), P=np.vstack(P),
                        stratum=np.array(S), site=np.array(site))
    print(u.assign(src=u.site.map(src_of)).groupby(["stratum", "src"]).size().unstack(fill_value=0))
    print("곡선", len(S), "->", REAL_DIR)


def main_curves():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="plain,knob,tree,offset,offkr,knobw2,offkrw2")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    arms = set(a.arms.split(","))
    os.makedirs(CURVE_DIR, exist_ok=True)
    files = sorted(f for f in glob.glob(os.path.join(M.ARM_DIR, "*.csv"))
                   if M.FILE_RE.match(os.path.basename(f))
                   and M.FILE_RE.match(os.path.basename(f)).group(1) in arms)
    maps = {}
    for i, f in enumerate(files, 1):
        b = os.path.basename(f)
        dst = os.path.join(CURVE_DIR, b[:-4] + ".npz")
        if os.path.exists(dst) and not a.force:
            continue
        town = M.FILE_RE.match(b).group(2)
        if town not in maps:
            maps[town] = M.road_map(town)
        S, P = cut(M.frames(f, town, maps[town]))
        np.savez_compressed(dst + ".part.npz", P=P, stratum=np.array(S))
        os.replace(dst + ".part.npz", dst)
        print("(%d/%d) %s 곡선 %d" % (i, len(files), b, len(S)), flush=True)


# ---- 자 셋 ----

def auc(X, y, seed=SEED):
    if min(np.bincount(y)) < NFOLD * 2:
        return np.nan
    out = []
    for tr, te in StratifiedKFold(NFOLD, shuffle=True, random_state=seed).split(X, y):
        m = HistGradientBoostingClassifier(max_iter=200, random_state=seed).fit(X[tr], y[tr])
        out.append(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]))
    return float(np.mean(out))


def pair(R, S, k, seed):
    """실측과 시뮬을 같은 k개씩. 안 맞추면 AUC가 표본 불균형과 학습량을 탐"""
    rng = np.random.RandomState(seed)
    X = np.vstack([R[rng.choice(len(R), k, replace=False)], S[rng.choice(len(S), k, replace=False)]])
    y = np.r_[np.ones(k, int), np.zeros(k, int)]
    ok = np.isfinite(X).all(1)
    return X[ok], y[ok]


def discrim(u, real, arms, sc, repeats, nmax):
    z = lambda d, st: d[M.KIN].to_numpy(float) / sc[st]   # noqa: E731. 분류기는 눈금 무관, 거리 표만 탐
    pooled, wrows, mrows = [], [], []
    for st in M.STRATA:
        R = z(real[real.stratum == st], st)
        ns = {g: int(((u.arm == g) & (u.stratum == st)).sum()) for g in arms}
        ok = [n for n in ns.values() if n >= MIN_N]
        if len(R) < MIN_N or not ok:
            continue
        k = min(min(ok), len(R), nmax)
        for g in arms:
            if ns[g] < MIN_N:
                continue
            S = z(u[(u.arm == g) & (u.stratum == st)], st)
            reps = [auc(*pair(R, S, k, SEED + 977 * t), seed=SEED + t) for t in range(repeats)]
            X, y = pair(R, S, k, SEED)
            loo = {"auc_no_" + c: auc(np.delete(X, j, 1), y) for j, c in enumerate(M.KIN)}
            pooled.append(dict(stratum=st, arm=g, n=k, auc=np.mean(reps),
                               auc_sd=np.std(reps, ddof=1) if len(reps) > 1 else 0.0, **loo))
            wrows.append(dict(stratum=st, arm=g, **{c: wasserstein_distance(S[:, j], R[:, j])
                                                    for j, c in enumerate(M.KIN)},
                              **{"mean_" + c: float(np.nanmean(S[:, j]) * sc[st][j])
                                 for j, c in enumerate(M.KIN)}))
            print("판별기 %-9s %-8s k=%d AUC %.3f" % (st, g, k, pooled[-1]["auc"]), flush=True)
        wrows.append(dict(stratum=st, arm="real", **{c: 0.0 for c in M.KIN},
                          **{"mean_" + c: float(np.nanmean(R[:, j]) * sc[st][j]) for j, c in enumerate(M.KIN)}))
        # 맵마다 따로 학습. 실측은 맵이 없으니 매번 같은 실측 풀. 표본이 작아 절대값은 풀린 판보다 조금 낮음
        if st not in M.TESTED:
            continue
        for town, d in u[u.stratum == st].groupby("town"):
            ns = {g: int((d.arm == g).sum()) for g in arms}
            if min(ns.values()) < MIN_N:
                continue
            k = min(min(ns.values()), len(R), nmax)
            for g in arms:
                X, y = pair(R, z(d[d.arm == g], st), k, SEED)
                mrows.append(dict(stratum=st, town=town, arm=g, auc=auc(X, y)))
    return pd.DataFrame(pooled), pd.DataFrame(wrows), pd.DataFrame(mrows)


def coverage(u, real, arms, sc):
    """맵마다: 실측 단위 중 그 조건 단위가 밟은 칸에 떨어지는 비율(단위 가중이 주 값).
    맵 안 조건 사이 단위 수를 맞춤. 안 맞추면 덮개가 정책 대신 표본 크기를 잼"""
    rng = np.random.RandomState(SEED)
    offs = [rng.rand(len(M.KIN)) for _ in range(NDRAW_COV)]
    rows = []
    for st in M.STRATA:
        R = real[real.stratum == st][M.KIN].to_numpy(float) / sc[st]
        R = R[np.isfinite(R).all(1)]
        if len(R) < MIN_REAL:
            continue
        for town, d in u[u.stratum == st].groupby("town"):
            Zs = {g: d[d.arm == g][M.KIN].to_numpy(float) / sc[st] for g in arms}
            nmin = min(len(v) for v in Zs.values())
            if nmin < MIN_REAL:
                continue
            for off in offs:
                rc = [tuple(r) for r in np.floor(R + off).astype(np.int32)]
                cnt = pd.Series(rc).value_counts()
                for g, Z in Zs.items():
                    Z = Z[rng.choice(len(Z), nmin, replace=False)]
                    seen = set(map(tuple, np.floor(Z + off).astype(np.int32)))
                    hit = np.fromiter((c in seen for c in cnt.index), bool, len(cnt))
                    rows.append(dict(stratum=st, town=town, arm=g, n=nmin,
                                     cov_cell=hit.mean(), cov_unit=cnt[hit].sum() / cnt.sum()))
            print("덮개 %-9s %s" % (st, town), flush=True)
    return pd.DataFrame(rows).groupby(["stratum", "town", "arm"], as_index=False)[["cov_cell", "cov_unit"]].mean()


def curves(arms, keep, csrc):
    rng = np.random.RandomState(SEED)
    rows = []
    for f in sorted(glob.glob(os.path.join(CURVE_DIR, "*.npz"))):
        m = M.FILE_RE.match(os.path.basename(f)[:-4] + ".csv")
        if not m or m.group(1) not in arms or (m.group(2), int(m.group(3))) not in keep:
            continue
        z = np.load(f)
        for st in M.STRATA:
            P = z["P"][z["stratum"] == st]
            if len(P) >= NSUB:
                rows.append(dict(stratum=st, arm=m.group(1), town=m.group(2), seed=int(m.group(3)),
                                 **curve_score(P[rng.choice(len(P), NSUB, replace=False)], rng)))
    real = []
    f = os.path.join(REAL_DIR, "curves.npz")
    if os.path.exists(f):
        z = np.load(f)
        src = np.array([src_of(s) for s in z["site"]])
        for site in np.unique(z["site"]):
            if src_of(site) not in csrc:
                continue
            for st in M.STRATA:
                P = z["P"][(z["site"] == site) & (z["stratum"] == st)]
                if len(P) >= NSUB:
                    real.append(dict(stratum=st, site=site,
                                     **curve_score(P[rng.choice(len(P), NSUB, replace=False)], rng)))
        del src
    return pd.DataFrame(rows), pd.DataFrame(real)


def contrast_rows(r, label, arms, lower_better=False):
    """r: 행 (층, 맵), 열 조건. 대비마다 맵 단위 차이, Holm은 대비 하나 안에서 두 층에"""
    out = []
    for x, b in M.CONTRASTS:
        if x not in arms or b not in arms:
            continue
        rows = []
        for st in M.TESTED:
            if st not in r.index.get_level_values(0):
                continue
            d = (r.loc[st][x] - r.loc[st][b]).dropna().to_numpy(float)
            if len(d) < 2:
                continue
            lo, hi = M.t_ci(d)
            rows.append(dict(ruler=label, contrast="%s-%s" % (x, b), stratum=st, maps=len(d),
                             diff=d.mean(), ci_lo=lo, ci_hi=hi,
                             wins=int(((d < 0) if lower_better else (d > 0)).sum()), p=M.signed_rank_p(d)))
        for row, pa in zip(rows, M.holm([q["p"] for q in rows]) if rows else []):
            row["p_holm"] = float(pa)
        out += rows
    return out


def main_score():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="plain,knob,tree,offset,offkr,knobw2,offkrw2")
    ap.add_argument("--real", default=os.path.join(REAL_DIR, "units.csv"))
    ap.add_argument("--sources", default=",".join(SOURCES), help="판별기, 덮개에 쓸 실측 출처")
    ap.add_argument("--curve-sources", default="ind,sind",
                    help="곡선 실측 띠. 앞 판 기본값(교차로 촬영 위주)")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--nmax", type=int, default=NMAX)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    arms = [x for x in a.arms.split(",") if x]
    if M.BASE not in arms:
        raise SystemExit("기준 %s가 --arms에 있어야 함" % M.BASE)
    M.ARMS = arms
    fs = sorted(f for f in glob.glob(os.path.join(M.UNIT_DIR, "*.csv"))
                if M.FILE_RE.match(os.path.basename(f)) and M.FILE_RE.match(os.path.basename(f)).group(1) in arms)
    u = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    keep = M.check_balance(u)
    u = u[[(t, s) in keep for t, s in zip(u.town, u.seed)]].reset_index(drop=True)
    real = pd.read_csv(a.real)
    real = real[real.site.map(src_of).isin(a.sources.split(","))]
    sc = M.load_scale()
    os.makedirs(a.out, exist_ok=True)
    tab, tests = {}, []

    div = M.diversity(u)
    r = M.map_ratio(div, "q1")
    tab["q1"] = r
    tests += contrast_rows(r, "q1", arms)

    # HGB의 OpenMP가 작은 표본에서 오히려 9배 느림(16코어, 1000행 2.07 대 0.24초). 한 스레드로
    with threadpool_limits(1):
        pooled, wdist, amap = discrim(u, real, arms, sc, a.repeats, a.nmax)
    if len(amap):
        r = amap.pivot_table(index=["stratum", "town"], columns="arm", values="auc")
        tab["auc"] = r
        tests += contrast_rows(r, "auc", arms, lower_better=True)

    cov = coverage(u, real, arms, sc)
    if len(cov):
        r = cov.pivot_table(index=["stratum", "town"], columns="arm", values="cov_unit")
        tab["cov"] = r.div(r[M.BASE], axis=0)
        tests += contrast_rows(tab["cov"], "cov", arms)

    cv, cvr = curves(arms, keep, a.curve_sources.split(","))
    col = "raw_pack_%g" % RMAIN
    if len(cv):
        m = cv.groupby(["stratum", "town", "arm"])[col].mean().unstack("arm")
        tab["curve"] = m.div(m[M.BASE], axis=0)
        tests += contrast_rows(tab["curve"], "curve", arms)
        cv.to_csv(os.path.join(a.out, "curves_runs.csv"), index=False)
    if len(cvr):
        cvr.to_csv(os.path.join(a.out, "curves_real.csv"), index=False)

    pooled.to_csv(os.path.join(a.out, "auc.csv"), index=False)
    amap.to_csv(os.path.join(a.out, "auc_map.csv"), index=False)
    wdist.to_csv(os.path.join(a.out, "wdist.csv"), index=False)
    cov.to_csv(os.path.join(a.out, "coverage.csv"), index=False)
    tests = pd.DataFrame(tests)
    tests.to_csv(os.path.join(a.out, "tests.csv"), index=False)
    write_report(a, arms, real, tab, tests, pooled, wdist, cov, cv, cvr)


def write_report(a, arms, real, tab, tests, pooled, wdist, cov, cv, cvr):
    f3 = lambda v: "%.3f" % v   # noqa: E731
    name = {"q1": "힐 수 q1 (plain 대비)", "cov": "실측 덮개 (단위 가중, plain 대비)",
            "auc": "판별기 AUC (맵별 학습, 낮을수록 실측다움)",
            "curve": "곡선 종류 수 r=%g raw (plain 대비)" % RMAIN}
    L = ["# 앞 판 자 넷으로 다시 재기", "",
         "생성: `diag/realism.py score --arms %s`. 사후 추가 진단이라 탐색." % a.arms, "",
         "실측 단위(판별기, 덮개, 출처 %s): " % a.sources
         + ", ".join("%s %d" % (s, n) for s, n in real.groupby("stratum").size().items()), "",
         "## 자 넷의 일치", "", "맵 평균. 판별기만 원값(맵별 학습 AUC), 나머지는 맵마다 plain으로 나눈 배수.", ""]
    for st in M.STRATA:
        have = [k for k in ("q1", "cov", "auc", "curve") if k in tab and st in tab[k].index.get_level_values(0)]
        if not have:
            continue
        L += ["### %s" % st, "", "| 자 | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
        for k in have:
            r = tab[k].loc[st]
            L.append("| %s | %s |" % (name[k], " | ".join(f3(r[g].mean()) if g in r else "" for g in arms)))
        L.append("")
    L += ["## 맵 단위 대비", "",
          "차이는 앞쪽 - 뒤쪽. 판별기는 음수가 앞쪽이 더 실측다움이고 우세 맵도 그 방향으로 셈. "
          "Holm은 대비와 자 하나 안에서 두 층에.", "",
          "| 자 | 대비 | 도로 유형 | 맵 | 차이 | 95% CI | 앞쪽 우세 맵 | p | p (Holm) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in tests.itertuples():
        L.append("| %s | %s | %s | %d | %+.3f | [%+.3f, %+.3f] | %d/%d | %.4f | %.4f |" % (
            r.ruler, r.contrast, r.stratum, r.maps, r.diff, r.ci_lo, r.ci_hi, r.wins, r.maps, r.p, r.p_holm))
    L += ["", "## 판별기 풀린 판", "",
          "층별 실측과 조건을 k개씩, 반복 %d회 평균. -성분 열은 그 성분을 빼고 학습한 AUC(1회). "
          "크게 떨어지면 그 성분이 들키는 이유." % a.repeats, "",
          "| 도로 유형 | 조건 | k | AUC | sd | " + " | ".join("-" + c for c in M.KIN) + " |",
          "|---|---|---|---|---|" + "---|" * len(M.KIN)]
    for r in pooled.itertuples():
        L.append("| %s | %s | %d | %s | %s | %s |" % (r.stratum, r.arm, r.n, f3(r.auc), f3(r.auc_sd),
                 " | ".join(f3(getattr(r, "auc_no_" + c)) for c in M.KIN)))
    L += ["", "## 성분별 거리", "",
          "Wasserstein, scale.yaml 눈금(실측 sd 단위). 넓히면 커지는 자라 참고만. 오른쪽은 평균 원단위.", "",
          "| 도로 유형 | 조건 | " + " | ".join(M.KIN) + " | " + " | ".join("평균 " + c for c in M.KIN) + " |",
          "|---|---|" + "---|" * (2 * len(M.KIN))]
    for r in wdist.itertuples():
        L.append("| %s | %s | %s | %s |" % (r.stratum, r.arm, " | ".join(f3(getattr(r, c)) for c in M.KIN),
                 " | ".join(f3(getattr(r, "mean_" + c)) for c in M.KIN)))
    if len(cov):
        L += ["", "## 실측 덮개 원값", "", "맵 평균. 칸 기준은 희귀 칸과 흔한 칸을 같게 봄. 단위 기준이 주 값.", "",
              "| 도로 유형 | 조건 | 칸 덮개 | 단위 덮개 |", "|---|---|---|---|"]
        for (st, g), d in cov.groupby(["stratum", "arm"]):
            L.append("| %s | %s | %.1f%% | %.1f%% |" % (st, g, 100 * d.cov_cell.mean(), 100 * d.cov_unit.mean()))
    if len(cv):
        L += ["", "## 곡선 종류 수 원값", "",
              "(실행, 층)마다 곡선 %d개를 뽑아 셈. raw는 속도 수준 포함, shape는 모양만. "
              "실측 띠는 촬영지마다 같은 수를 뽑아 셈(출처 %s)." % (NSUB, a.curve_sources), ""]
        for kind in ("raw", "shape"):
            cols = ["%s_pack_%g" % (kind, r) for r in RAD]
            L += ["### %s" % kind, "", "| 도로 유형 | 조건 | " + " | ".join("r=%g" % r for r in RAD) + " |",
                  "|---|---|" + "---|" * len(RAD)]
            for (st, g), d in cv.groupby(["stratum", "arm"]):
                L.append("| %s | %s | %s |" % (st, g, " | ".join("%.1f" % d[c].mean() for c in cols)))
            if len(cvr):
                for st, d in cvr.groupby("stratum"):
                    for lab, fn in (("실측 평균", np.mean), ("실측 최소", np.min)):
                        L.append("| %s | %s (%d곳) | %s |" % (st, lab, len(d), " | ".join("%.1f" % fn(d[c]) for c in cols)))
            L.append("")
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("->", os.path.join(a.out, "report.md"))


if __name__ == "__main__":
    cmds = {"real": main_real, "curves": main_curves, "score": main_score}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    c = sys.argv.pop(1)
    cmds[c]()
