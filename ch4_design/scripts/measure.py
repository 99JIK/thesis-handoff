"""실험 2 채점. py3.11. 수집물(data/outputs/arms)에서 다양성과 부작용을 한 판으로.

  python scripts/measure.py units      수집물 -> 단위와 부작용 캐시 (data/outputs/units/)
  python scripts/measure.py score      캐시 -> results/exp2/ 표와 검정

출처: C:\\Github\\sil-agent-repertoire scripts/measure.py, repertoire.py (커밋 a604b91)에서
층 판정(road_map, stratum_of), 평활(_sema), 단위 자르기(units), 다섯 숫자(sig_of),
힐 수와 희박화(hill, cells)를 가져옴. 판정 로직은 그대로. 바꾼 것:
- 축척을 실측 원본 대신 config/scale.yaml 고정 표에서
- 조건이 plain, knob, tree 셋. 셋을 한 판으로만 채점(희박화 바닥이 조건 목록에 걸림)
- 부작용 셋(정지 비율, 충돌, 공간 평균 속도)과 맵 단위 신뢰구간, 검정을 새로 씀

offset(2026-11-03 사후 추가)은 따로 채점:
  python scripts/measure.py score --arms plain,knob,tree,offset --out results/exp2_offset
바닥이 조건 목록에 걸려서 넷으로 채점하면 knob, tree 값도 셋일 때와 조금 다름. 본 결과는 셋 판.
offkr(2026-11-04 사후 추가, offset에서 keep_right 고정)도 같은 식:
  python scripts/measure.py score --arms plain,knob,tree,offset,offkr --out results/exp2_offkr
넓힌 상자 짝(2026-11-05 사후 추가):
  python scripts/measure.py score --arms plain,knob,offkr,knobw2,offkrw2 --out results/exp2_w2
"""
import argparse
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from itertools import product

import numpy as np
import pandas as pd
import yaml
from scipy.stats import rankdata, t as tdist

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARM_DIR = os.path.join(ROOT, "data", "outputs", "arms")
UNIT_DIR = os.path.join(ROOT, "data", "outputs", "units")
OUT = os.path.join(ROOT, "results", "exp2")
LANE_JSON = os.path.join(ROOT, "data", "outputs", "carla_speed", "lane_speed.json")
RING_JSON = os.path.join(ROOT, "data", "outputs", "map_partition", "roundabout.json")
MAP_DIR = os.environ.get("MAP_DIR") or os.path.join(
    os.environ.get("CARLA_ROOT", "C:/CARLA/WindowsNoEditor"),
    "CarlaUE4", "Content", "Carla", "Maps", "OpenDrive")
SCALE = os.path.join(ROOT, "config", "scale.yaml")

ARMS = ["plain", "knob", "tree"]
BASE = "plain"
STRATA = ["junction", "urb_block", "hw_link"]
TESTED = ["junction", "urb_block"]      # 고속은 맵 3개라 검정 안 함
KIN = ["v_mean", "v_sd", "a_mean", "a_sd", "za"]
DT = 0.1
FILE_RE = re.compile(r"^(plain|knob|tree|offset|offkr|knobw2|offkrw2|knobw05|offkrw05|treero|offkrro|offkrw05ro|offkrw2ro|treec|treef|tree3|ptree|pknob|pdraft)_(Town[0-9A-Za-z]+)_s(\d+)\.csv$")
# (a, b): a - b를 맵 단위로 검정. Holm은 대비마다 두 도로 유형에
# offkrw2-knobw2가 같은 넓은 상자에서 트리가 더하는 몫. knobw2-knob은 상자만 넓힌 몫. x0.5 짝도 같은 구조
# *ro는 도달 불가 재배치 트리(2026-11-10 사후). 같은 시드 원본과 짝이라 *ro - 원본이 재배치 몫
CONTRASTS = [("tree", "knob"), ("offset", "knob"), ("offkr", "knob"), ("offkr", "offset"),
             ("offkrw2", "knobw2"), ("knobw2", "knob"), ("offkrw05", "knobw05"), ("knob", "knobw05"),
             ("treero", "knob"), ("offkrro", "knob"), ("offkrw05ro", "knobw05"), ("offkrw2ro", "knobw2"),
             ("treero", "tree"), ("offkrro", "offkr"), ("offkrw05ro", "offkrw05"), ("offkrw2ro", "offkrw2"),
             # 보조 실험 9(2026-11-19). treef - treec가 주, 나머지 기술
             ("treef", "treec"), ("treef", "knob"), ("treec", "knob"), ("treec", "tree"),
             # 실험 3(docs/design_exp3.md). 다양성 주 지표는 보류라 이 판정은 기술
             ("tree3", "knob"),
             # 실험 4(docs/design_exp4.md). 앞 둘이 판정, pknob-knob은 기술. knob-plain은 지표가 알려진 차이를
             # 잡는지 보는 검증 줄(실험 3 판에도 같이 나옴)
             ("ptree", "knob"), ("ptree", "pknob"), ("pknob", "knob"), ("knob", "plain"),
             # 수리 효과(2026-11-25 사후). ptree-pdraft가 주, pdraft-knob은 맥락. 따로 채점(조건 목록에 pdraft 있을 때만 나옴)
             ("ptree", "pdraft"), ("pdraft", "knob")]

# ---- 층 판정 (앞 판 그대로) ----
SIGNAL_TYPE = {"1000001": "signal", "206": "stop", "205": "yield"}
LOOP_TURN = np.radians(150.0)
LOOP_RADIUS = 25.0
LOOP_LEN = 50.0
DEFAULT_LIMIT = 30.0   # 표지 없을 때 CARLA가 주는 값. 관측으로 확인
JUNC = {"signal_junction", "stop_junction", "unsig_junction"}


def ring_roads(town):
    if not os.path.exists(RING_JSON):
        return set()
    with open(RING_JSON, encoding="utf-8") as fh:
        return set(json.load(fh).get(town, []))


def road_map(town):
    """road_id -> 제한속도, road_id -> 통제 방식"""
    with open(LANE_JSON, encoding="utf-8") as fh:
        lanes = json.load(fh)[town]
    acc = {}
    for s in lanes:
        acc.setdefault(str(s["road"]), []).append(s["limit"])
    lim = {}
    for r, vs in acc.items():
        good = [v for v in vs if v is not None]
        lim[r] = Counter(good).most_common(1)[0][0] if good else None
    root = ET.parse(os.path.join(MAP_DIR, town + ".xodr")).getroot()
    roads = {r.get("id"): r for r in root.iter("road")}
    sig = {rid: {SIGNAL_TYPE.get(s.get("type")) for s in r.iter("signal")}
           for rid, r in roads.items()}
    ctrl = {rid: "link" for rid in roads}
    for j in root.iter("junction"):
        conns = j.findall("connection")
        cr = {c.get("connectingRoad") for c in conns} & set(roads)
        ins = {c.get("incomingRoad") for c in conns} & set(roads)
        types = set().union(*(sig[r] for r in cr | ins)) if (cr | ins) else set()
        kind = next((t for t in ("signal", "stop", "yield") if t in types), "uncontrolled")
        for rid in cr:
            ctrl[rid] = kind
    # 교차로 연결로는 표지가 없어 진입로 값을 물려받음. 진입로 값이 둘 이상이면 안 채움
    inc = {}
    for j in root.iter("junction"):
        for c in j.findall("connection"):
            inc.setdefault(c.get("connectingRoad"), set()).add(c.get("incomingRoad"))
    for _ in range(5):
        n = 0
        for rid, srcs in inc.items():
            if rid not in lim or lim.get(rid) is not None:
                continue
            vals = {lim.get(s) for s in srcs} - {None}
            if len(vals) == 1:
                lim[rid] = vals.pop()
                n += 1
        if not n:
            break
    for rid in roads:
        if lim.get(rid) is None:
            lim[rid] = DEFAULT_LIMIT
    # 무신호 직진 연결로와 다리 둘짜리 junction은 링크로
    for j in root.iter("junction"):
        conns = j.findall("connection")
        cr = {c.get("connectingRoad") for c in conns} & set(roads)
        two_leg = len({c.get("incomingRoad") for c in conns}) <= 2
        for rid in cr:
            if ctrl[rid] != "uncontrolled" and not two_leg:
                continue
            straight = all(g.find("arc") is None for g in roads[rid].iter("geometry"))
            if two_leg or straight:
                ctrl[rid] = "link"
    # 회차 고리. 선행차 원뿔이 조여 도는 구간에서 앞차를 놓쳐서 뺌
    for rid, r in roads.items():
        arcs = [g for g in r.iter("geometry") if g.find("arc") is not None]
        if not arcs:
            continue
        turn = sum(abs(float(g.find("arc").get("curvature"))) * float(g.get("length"))
                   for g in arcs)
        rad = min(1.0 / abs(float(g.find("arc").get("curvature"))) for g in arcs)
        if turn >= LOOP_TURN and rad <= LOOP_RADIUS and float(r.get("length")) >= LOOP_LEN:
            ctrl[rid] = "loop"
    ring = ring_roads(town)
    near = set(ring)
    for j in root.iter("junction"):
        conns = j.findall("connection")
        cr = {c.get("connectingRoad") for c in conns} & set(roads)
        ins = {c.get("incomingRoad") for c in conns}
        if (cr | ins) & ring:
            near |= cr
    for rid in near:
        ctrl[rid] = "roundabout"
    return lim, ctrl


def stratum_of(lim, ctrl, road_id):
    """road_id -> 층. 모르는 건 None이라 채점에서 빠짐"""
    L = lim.get(str(road_id))
    c = ctrl.get(str(road_id), "link")
    if c == "link":
        if L in (60.0, 90.0):
            return "hw_link"
        if L in (30.0, 40.0):
            return "urb_block"
        return None
    if L not in (30.0, 40.0):
        return None
    if c == "signal":
        return "signal_junction"
    if c == "stop":
        return "stop_junction"
    if c in ("uncontrolled", "yield"):
        return "unsig_junction"
    return None


# ---- 운동학과 단위 (앞 판 그대로) ----
SEMA_DELTA = 0.5         # Thiemann et al. 2008이 NGSIM에 권장한 평활폭(s)
LEN = {"hw_link": 80.0, "urb_block": 40.0, "junction": None}
MIN_FR = 15
EPS_STEP = 0.05
A_MAX = 8.0
V_STOP = 2.0             # 정지 판정(m/s). 덱과 맞춤. 앞 판 정지 분석은 0.5


def _sema(x, dt=DT, delta=SEMA_DELTA):
    """대칭 지수이동평균. 창은 ±3Δ, 가장자리는 대칭 절단"""
    x = np.asarray(x, float)
    n = len(x)
    wing = min(int(round(3.0 * delta / dt)), (n - 1) // 2)
    if n < 3 or wing < 1:
        return x.copy()
    off = np.arange(-wing, wing + 1)
    ker = np.exp(-np.abs(off) * dt / delta)
    out = np.convolve(x, ker, mode="same") / np.convolve(np.ones(n), ker, mode="same")
    for i in set(list(range(min(wing, n))) + list(range(max(0, n - wing), n))):
        h = min(wing, i, n - 1 - i)
        w = ker[wing - h: wing + h + 1]
        out[i] = np.dot(w, x[i - h: i + h + 1]) / w.sum()
    return out


def frames(path, town, rm):
    """수집물 -> 프레임 표. 차량마다 위치 평활 후 미분"""
    d = pd.read_csv(path).sort_values(["vid", "t"]).reset_index(drop=True)
    v = np.full(len(d), np.nan)
    a = np.full(len(d), np.nan)
    for _vid, idx in d.groupby("vid").indices.items():
        if len(idx) < 5:
            continue
        xs = _sema(d["x"].to_numpy()[idx])
        ys = _sema(d["y"].to_numpy()[idx])
        sp = np.hypot(np.gradient(xs, DT), np.gradient(ys, DT))
        v[idx] = sp
        a[idx] = np.gradient(sp, DT)
    d["v"], d["a"] = v, a
    lim, ctrl = rm
    raw = [stratum_of(lim, ctrl, r) for r in d["road_id"]]
    d["stratum"] = [("junction" if x in JUNC else x) if x else None for x in raw]
    d["uid"] = d["vid"].astype(str)
    d["step"] = np.hypot(d["x"].diff(), d["y"].diff())
    d.loc[d["uid"] != d["uid"].shift(), "step"] = 0.0
    return d


def sig_of(v, a, step):
    """창 하나를 다섯 숫자로. 같은 자리 머문 프레임은 접음(신호 대기가 통계를 지배하지 않게).
    za는 이동거리 100 m당 가속도 부호 변화"""
    ok = np.isfinite(v) & np.isfinite(a) & (np.abs(a) <= A_MAX)
    v, a, sp = v[ok], a[ok], np.asarray(step, float)[ok]
    if len(v) == 0:
        return None
    keep = np.r_[True, sp[1:] > EPS_STEP]
    v, a, sp = v[keep], a[keep], sp[keep]
    if len(v) < MIN_FR:
        return None
    s = np.sign(a)
    s = s[s != 0]
    flip = int((np.diff(s) != 0).sum()) if len(s) > 1 else 0
    return {"v_mean": v.mean(), "v_sd": v.std(), "a_mean": a.mean(), "a_sd": a.std(),
            "za": flip / max(np.nansum(sp), 1e-9) * 100.0, "n": len(v)}


def units(d, extra=()):
    """run = (차량, 층)이 이어진 구간. 도심 40 m, 고속 80 m마다 자르고 꼬리는 버림.
    교차로는 통과 전체가 단위이고 관측 경계에 걸린 첫/끝 run은 뺌(run 셋 이상인 차만 판정).
    extra: 단위 프레임 평균을 같이 실을 열. diag/context.py가 시각과 앞차 비율 싣는 용도, 채점엔 안 씀"""
    d = d[d["stratum"].notna()].sort_values(["uid", "t"]).reset_index(drop=True)
    if d.empty:
        return pd.DataFrame()
    new = ((d["uid"] != d["uid"].shift()) | (d["stratum"] != d["stratum"].shift())
           | (d["t"].diff().abs() > 1.5 * DT))
    d["run"] = new.cumsum()
    step = d["step"].to_numpy(float).copy()
    step[new.to_numpy()] = 0.0
    d["s"] = pd.Series(step).groupby(d["run"]).cumsum().to_numpy()
    nrun = d.groupby("uid")["run"].nunique()
    multi = set(nrun[nrun >= 3].index)
    sub = d[d["uid"].isin(multi)]
    first = set(sub.groupby("uid")["run"].min())
    last = set(sub.groupby("uid")["run"].max())
    rows = []
    for run, g in d.groupby("run", sort=False):
        st = g["stratum"].iloc[0]
        L = LEN[st]
        v, a = g["v"].to_numpy(float), g["a"].to_numpy(float)
        sp = np.r_[0.0, np.diff(g["s"].to_numpy(float))]
        if L is None:
            if run in first or run in last:
                continue
            parts = [np.ones(len(g), bool)]
        else:
            w = (g["s"].to_numpy() // L).astype(int)
            parts = [w == k for k in range(int(g["s"].iloc[-1] // L))]
        for m in parts:
            s = sig_of(v[m], a[m], sp[m])
            if s:
                s.update({"uid": g["uid"].iloc[0], "stratum": st})
                s.update({c: float(g[c].to_numpy()[m].mean()) for c in extra})
                rows.append(s)
    return pd.DataFrame(rows)


def side_effects(d, coll_path):
    """기록 구간(t >= 0)의 부작용. 층별 정지 비율과 공간 평균 속도, 실행 전체 충돌"""
    out = {}
    ok = np.isfinite(d["v"].to_numpy())
    for st in STRATA + ["all"]:
        m = ok & ((d["stratum"] == st).to_numpy() if st != "all"
                  else d["stratum"].notna().to_numpy())
        n = int(m.sum())
        dist = float(d["step"].to_numpy()[m].sum())
        out[st] = {"frames": n,
                   "stop_ratio": float((d["v"].to_numpy()[m] < V_STOP).mean()) if n else np.nan,
                   "space_mean_speed": dist / (n * DT) if n else np.nan,
                   "veh_km": dist / 1000.0}
    total_km = float(d["step"].sum()) / 1000.0
    c = pd.read_csv(coll_path) if os.path.exists(coll_path) else pd.DataFrame()
    ev_v = ev_o = win_v = win_o = 0
    if len(c):
        c = c[c["t"] >= 0]
        veh = c[c["other_type"].astype(str).str.startswith("vehicle")]
        oth = c[~c["other_type"].astype(str).str.startswith("vehicle")]
        # 본 지표: 에피소드 안 같은 차 쌍은 1건, 물체 충돌은 차 1대당 1건. 창 값 없음.
        # 막혀서 계속 비비는 쌍이 수십 건으로 부풀던 것 때문(파일럿 Town03 s0, 196-248 쌍 20건)
        pairs = {(min(a, b), max(a, b)) for a, b in zip(veh["vid"], veh["other_id"])}
        ev_v, ev_o = len(pairs), int(oth["vid"].nunique())
        # 강건성: 예전 정의. 같은 쌍도 1초 넘게 떨어지면 새 사건
        seen = {}
        for r in veh.itertuples():
            key = (min(r.vid, r.other_id), max(r.vid, r.other_id))
            if not (key in seen and r.t - seen[key] <= 1.0):
                win_v += 1
            seen[key] = r.t
        last = {}
        for r in oth.itertuples():
            if not (r.vid in last and r.t - last[r.vid] <= 1.0):
                win_o += 1
            last[r.vid] = r.t
    per = (lambda n: 1000.0 * n / total_km if total_km else np.nan)
    out["collisions"] = {"vehicle": ev_v, "other": ev_o, "veh_km": total_km,
                         "per_1000km": per(ev_v + ev_o),
                         "vehicle_win1s": win_v, "other_win1s": win_o,
                         "per_1000km_win1s": per(win_v + win_o)}
    return out


def main_units():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=ARM_DIR)
    ap.add_argument("--dst", default=UNIT_DIR, help="파일럿은 따로 둘 것")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.dst, exist_ok=True)
    files = sorted(f for f in glob.glob(os.path.join(a.src, "*.csv"))
                   if FILE_RE.match(os.path.basename(f)))
    maps = {}
    for i, f in enumerate(files, 1):
        b = os.path.basename(f)
        arm, town, seed = FILE_RE.match(b).groups()
        dst = os.path.join(a.dst, b)
        if os.path.exists(dst) and not a.force:
            continue
        if town not in maps:
            maps[town] = road_map(town)
        d = frames(f, town, maps[town])
        u = units(d)
        u["arm"], u["town"], u["seed"] = arm, town, int(seed)
        se = side_effects(d, f[:-4] + "_coll.csv")
        se.update({"arm": arm, "town": town, "seed": int(seed)})
        with open(dst[:-4] + "_side.json", "w", encoding="utf-8") as fh:
            json.dump(se, fh)
        u.to_csv(dst + ".part", index=False)
        os.replace(dst + ".part", dst)
        print("(%d/%d) %s 단위 %d" % (i, len(files), b, len(u)), flush=True)


# ---- 다양성 ----
NDRAW = 20       # 격자 원점 반복
MIN_U = 40       # 단위가 이보다 적은 (실행, 층)은 안 냄


def load_scale():
    with open(SCALE, encoding="utf-8") as fh:
        s = yaml.safe_load(fh)["scale"]
    return {st: np.array([s[st][c] for c in KIN]) for st in s}


def diversity(u, w=1.0, ndraw=NDRAW, seed=0):
    """실행마다 (q0, q1, q2). 층 안 모든 실행을 같은 단위 수로 희박화.
    격자 원점은 NDRAW번 새로 뽑고 원점마다 희박화 한 번. 앞 판 repertoire.py와 같은 절차"""
    scd = load_scale()
    rng = np.random.RandomState(seed)
    offs = [rng.rand(len(KIN)) for _ in range(ndraw)]
    rows = []
    for st in STRATA:
        us = u[u["stratum"] == st]
        groups = [(k, g) for k, g in us.groupby(["arm", "town", "seed"]) if len(g) >= MIN_U]
        if not groups:
            continue
        nr = min(len(g) for _k, g in groups)
        for (arm, town, sd), g in groups:
            Z = g[KIN].to_numpy(float) / scd[st] / w
            q = np.zeros(3)
            for off in offs:
                C = np.floor(Z + off).astype(np.int32)
                s = C[rng.choice(len(C), nr, replace=False)]
                _, cnt = np.unique(s, axis=0, return_counts=True)
                p = cnt / float(nr)
                q += (len(cnt), np.exp(-(p * np.log(p)).sum()), 1.0 / (p * p).sum())
            q /= ndraw
            rows.append({"stratum": st, "arm": arm, "town": town, "seed": sd,
                         "n_units": len(g), "nr": nr, "q0": q[0], "q1": q[1], "q2": q[2]})
    return pd.DataFrame(rows)


# ---- 맵 단위 집계와 검정 ----

def signed_rank_p(d):
    """Wilcoxon 부호순위 양측 정확 p. 0 차이는 버림. 맵 8개면 2^8 전수"""
    d = np.asarray(d, float)
    nz = d[~np.isclose(d, 0)]
    if len(nz) == 0:
        return 1.0
    r = rankdata(np.abs(nz))
    w = r[nz > 0].sum()
    mu = r.sum() / 2
    dist = np.array([sum(ri for ri, s in zip(r, sg) if s)
                     for sg in product((0, 1), repeat=len(r))])
    return float(np.mean(np.abs(dist - mu) >= abs(w - mu) - 1e-9))


def holm(ps):
    order = np.argsort(ps)
    adj, run = np.empty(len(ps)), 0.0
    for rank, i in enumerate(order):
        run = max(run, (len(ps) - rank) * ps[i])
        adj[i] = min(1.0, run)
    return adj


def t_ci(x, level=0.95):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return (np.nan, np.nan)
    h = tdist.ppf(0.5 + level / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return (x.mean() - h, x.mean() + h)


def map_ratio(df, col):
    """맵마다 (조건 시드 평균) / (기준 시드 평균). 행 = 층 x 맵, 열 = 조건"""
    m = df.groupby(["stratum", "town", "arm"])[col].mean().unstack("arm")
    return m.div(m[BASE], axis=0)


def summarize(ratio, label):
    out = []
    for st in STRATA:
        if st not in ratio.index.get_level_values(0):
            continue
        r = ratio.loc[st]
        for arm in ARMS:
            if arm not in r:
                continue
            x = r[arm].to_numpy(float)
            lo, hi = t_ci(x)
            out.append({"metric": label, "stratum": st, "arm": arm, "maps": int(np.isfinite(x).sum()),
                        "mean": float(np.nanmean(x)), "ci_lo": lo, "ci_hi": hi,
                        "per_map": " ".join("%.2f" % v for v in x)})
    return out


def tests(ratio, label):
    """주 대비: 트리 대 추첨. offset, offkr이 있으면 그 대비도(탐색). Holm은 대비 하나 안에서만. 맵 단위 대응"""
    out = []
    for a, b in CONTRASTS:
        if a not in ARMS or b not in ARMS:
            continue
        rows = []
        for st in TESTED:
            if st not in ratio.index.get_level_values(0):
                continue
            r = ratio.loc[st].dropna(subset=[a, b])
            d = (r[a] - r[b]).to_numpy(float)
            lo, hi = t_ci(d)
            rows.append({"metric": label, "contrast": "%s-%s" % (a, b), "stratum": st,
                         "maps": len(d), "diff": float(d.mean()), "ci_lo": lo, "ci_hi": hi,
                         "wins": int((d > 0).sum()), "p": signed_rank_p(d)})
        for r, pa in zip(rows, holm([r["p"] for r in rows]) if rows else []):
            r["p_holm"] = float(pa)
        out += rows
    return out


def load_side(unit_dir):
    rows = []
    for f in sorted(glob.glob(os.path.join(unit_dir, "*_side.json"))):
        s = json.load(open(f, encoding="utf-8"))
        for st in STRATA:
            rows.append({"arm": s["arm"], "town": s["town"], "seed": s["seed"], "stratum": st,
                         "stop_ratio": s[st]["stop_ratio"],
                         "space_mean_speed": s[st]["space_mean_speed"]})
        rows.append({"arm": s["arm"], "town": s["town"], "seed": s["seed"], "stratum": "all",
                     "coll_per_1000km": s["collisions"]["per_1000km"],
                     "coll_per_1000km_win1s": s["collisions"].get("per_1000km_win1s", np.nan),
                     "coll_vehicle": s["collisions"]["vehicle"],
                     "coll_other": s["collisions"]["other"],
                     "coll_vehicle_per_1000km": 1000.0 * s["collisions"]["vehicle"]
                     / s["collisions"]["veh_km"] if s["collisions"]["veh_km"] else np.nan})
    return pd.DataFrame(rows)


def check_balance(u):
    """세 조건이 같은 (맵, 시드)를 가졌나. 아니면 판이 안 맞음"""
    have = {a: set(zip(g["town"], g["seed"])) for a, g in u.groupby("arm")}
    sets = [have.get(a, set()) for a in ARMS]
    common = set.intersection(*sets)
    for a, s in zip(ARMS, sets):
        if s - common:
            print("주의: %s에만 있는 (맵, 시드) %d개는 뺌: %s" % (a, len(s - common),
                                                     sorted(s - common)[:5]))
    return common


def main_score():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=UNIT_DIR)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--arms", default="plain,knob,tree")
    a = ap.parse_args()
    global ARMS
    ARMS = a.arms.split(",")
    # 고른 조건 파일만. 안 거르면 캐시에 있는 다른 조건이 희박화 바닥을 바꿈
    files = sorted(f for f in glob.glob(os.path.join(a.units, "*.csv"))
                   if FILE_RE.match(os.path.basename(f))
                   and FILE_RE.match(os.path.basename(f)).group(1) in ARMS)
    if not files:
        raise SystemExit("단위 캐시가 없음. python scripts/measure.py units 먼저")
    u = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    keep = check_balance(u)
    u = u[[(t, s) in keep for t, s in zip(u["town"], u["seed"])]]
    out = a.out
    os.makedirs(out, exist_ok=True)

    div = diversity(u)
    div.to_csv(os.path.join(out, "diversity_runs.csv"), index=False)
    summ = summarize(map_ratio(div, "q1"), "diversity_q1")
    tst = tests(map_ratio(div, "q1"), "diversity_q1")

    # 강건성: 격자 폭과 힐 차수. 본문에는 방향이 바뀌는지만
    robust = []
    for w in (0.5, 2.0):
        dw = diversity(u, w=w)
        robust += tests(map_ratio(dw, "q1"), "q1_w%.1f" % w)
    for q in ("q0", "q2"):
        robust += tests(map_ratio(div, q), q)

    side = load_side(a.units)
    side = side[[(t, s) in keep and arm in ARMS
                 for t, s, arm in zip(side["town"], side["seed"], side["arm"])]]
    side_rows = []
    for col in ("stop_ratio", "space_mean_speed"):
        sub = side[side["stratum"].isin(STRATA)].dropna(subset=[col])
        m = sub.groupby(["stratum", "town", "arm"])[col].mean().unstack("arm")
        for st in STRATA:
            if st not in m.index.get_level_values(0):
                continue
            for arm in ARMS:
                x = m.loc[st][arm].to_numpy(float) if arm in m else np.array([])
                if col == "space_mean_speed":
                    x = x / m.loc[st][BASE].to_numpy(float)
                lo, hi = t_ci(x)
                side_rows.append({"metric": col + ("_ratio" if col == "space_mean_speed" else ""),
                                  "stratum": st, "arm": arm, "maps": int(np.isfinite(x).sum()),
                                  "mean": float(np.nanmean(x)) if len(x) else np.nan,
                                  "ci_lo": lo, "ci_hi": hi})
    # 두 번째 줄은 강건성(1초 창, 지속 접촉을 여러 건으로 셈).
    # 셋째 줄 차량 간만: 맵 평균이 단일 차로 소형 맵(Town02, 07)의 물체 충돌에 끌려서 다차로 맵의
    # tree 추돌 초과가 묻혔음(본 수집 Town06 tree 83.7 대 plain 14.7). 그래서 맵별 값도 같이 냄
    coll_maps = []
    for col, label in (("coll_per_1000km", "collisions_per_1000km"),
                       ("coll_per_1000km_win1s", "collisions_per_1000km_win1s"),
                       ("coll_vehicle_per_1000km", "vehicle_collisions_per_1000km")):
        coll = side[side["stratum"] == "all"].groupby(["town", "arm"])[col].mean().unstack("arm")
        for arm in ARMS:
            if arm in coll:
                x = coll[arm].to_numpy(float)
                lo, hi = t_ci(x)
                side_rows.append({"metric": label, "stratum": "all", "arm": arm,
                                  "maps": int(np.isfinite(x).sum()), "mean": float(np.nanmean(x)),
                                  "ci_lo": lo, "ci_hi": hi})
        coll_maps.append((label, coll[[x for x in ARMS if x in coll]]))

    pd.DataFrame(summ).to_csv(os.path.join(out, "diversity.csv"), index=False)
    pd.DataFrame(tst).to_csv(os.path.join(out, "tests.csv"), index=False)
    pd.DataFrame(robust).to_csv(os.path.join(out, "robustness.csv"), index=False)
    pd.DataFrame(side_rows).to_csv(os.path.join(out, "side_effects.csv"), index=False)

    L = ["# 실험 2 다양성과 부작용", "",
         "맵 단위. 배수는 기준(plain) 대비, 평균과 95% t 구간. 생성: `scripts/measure.py score`", "",
         "## 행동 다양성 (힐 수 q1)", "",
         "| 도로 유형 | 조건 | 맵 | 평균 | 95% CI | 맵별 |", "|---|---|---|---|---|---|"]
    for r in summ:
        L.append("| %s | %s | %d | %.2f | [%.2f, %.2f] | %s |" % (
            r["stratum"], r["arm"], r["maps"], r["mean"], r["ci_lo"], r["ci_hi"], r["per_map"]))
    L += ["", "## 대비", "",
          "| 지표 | 대비 | 도로 유형 | 맵 | 차이 | 95% CI | 앞쪽 우세 맵 | p | p (Holm) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in tst + robust:
        L.append("| %s | %s | %s | %d | %.3f | [%.3f, %.3f] | %d/%d | %.4f | %.4f |" % (
            r["metric"], r["contrast"], r["stratum"], r["maps"], r["diff"], r["ci_lo"],
            r["ci_hi"], r["wins"], r["maps"], r["p"], r["p_holm"]))
    L += ["", "q1_w*, q0, q2 줄은 강건성 확인용. 본 결과는 diversity_q1 줄. "
          "offset, offkr 대비는 사후 추가라 탐색.", "",
          "## 부작용", "", "| 지표 | 도로 유형 | 조건 | 맵 | 평균 | 95% CI |",
          "|---|---|---|---|---|---|"]
    for r in side_rows:
        L.append("| %s | %s | %s | %d | %.4f | [%.4f, %.4f] |" % (
            r["metric"], r["stratum"], r["arm"], r["maps"], r["mean"], r["ci_lo"], r["ci_hi"]))
    for label, m in coll_maps:
        L += ["", "### 맵별 %s" % label, "", "| 맵 | " + " | ".join(m.columns) + " |",
              "|---|" + "---|" * len(m.columns)]
        for town, r in m.iterrows():
            L.append("| %s | %s |" % (town, " | ".join("%.1f" % v for v in r)))
    md = "\n".join(L) + "\n"
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)


if __name__ == "__main__":
    cmds = {"units": main_units, "score": main_score}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        raise SystemExit("usage: python scripts/measure.py {units,score}")
    del sys.argv[1]
    cmds[c]()
