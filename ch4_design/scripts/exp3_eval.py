"""실험 3 평가. 루프 밖 검사만. py3.11 + z3. 설계는 docs/design_exp3.md 평가 절

  python scripts/exp3_eval.py e1                          생성 정확성: 과정 기록, intent와 effect 방향(실험 2 비교)
  python scripts/exp3_eval.py cells                       상황 칸 포괄 SMT 부분(수집 전에도 됨)
  python scripts/exp3_eval.py logs --src data/outputs/arms_exp3    발화율, 실현율, 칸 노출 가중, 실주행 조건부 발화
  python scripts/exp3_eval.py sheet                       사람 평가지(실험 2 30줄 + 실험 3 30개, 출처 가림)
  python scripts/exp3_eval.py all --src data/outputs/arms_exp3     e1, cells, logs

출력 results/exp3/. 사람 평가 대응표는 data/outputs/exp3_rating_key.csv(gitignore, 평가 끝까지 안 봄)
"""
import argparse
import glob
import itertools
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
import exp3_schema as S  # noqa: E402
import bt_nodes as N  # noqa: E402

BT = os.path.join(ROOT, "data", "outputs", "bt")
OUT = os.path.join(ROOT, "results", "exp3")
SRC = os.path.join(ROOT, "data", "outputs", "arms_exp3")
KEY = os.path.join(ROOT, "data", "outputs", "exp3_rating_key.csv")
# 실험 4가 같은 경로를 씀(--arm ptree --sets "exp4_*"). 기본값이 실험 3
TREE_ARM = "tree3"
SETS = "exp3_r*"
SEED = 0
N_BOOT = 2000
MIN_TICKS = 50          # 실현율 판정 하한. 판단값
FAST_DV = 3.0           # 빠른 접근 경계 m/s. 판단값
APPROACH_M = 30.0       # 교차로 접근 경계. 지각이 10 m 단위라 20, 30 m 칸까지. 판단값

# effect -> rule_intent 방향 낱말. speed는 slower가 down(의도 사전이 감속을 down으로 셈)
EFF_DIR = {("speed", "slower"): "down", ("speed", "faster"): "up", ("gap", "wider"): "up",
           ("gap", "narrower"): "down", ("lane_change", "allow"): "up", ("lane_change", "forbid"): "down"}
SET_KNOB = {"speed": "speed_diff", "gap": "gap_m", "lane_change": "lane_change"}
# 실현율 관측량의 기대 부호. speed는 제한속도 대비 속도비
REAL_SIGN = {("speed", "slower"): -1, ("speed", "faster"): 1, ("gap", "wider"): 1, ("gap", "narrower"): -1}


def eff_dir(s, e):
    return EFF_DIR.get((s, e), "up")


def trees():
    """{이름: (행동 목록, 순서)}. 이름은 collect.load_trees와 같은 꼴(세트/tree_NN)"""
    out = {}
    for f in sorted(glob.glob(os.path.join(BT, SETS, "tree_*.json"))):
        r = json.load(open(f, encoding="utf-8"))
        if not r.get("deployed"):
            continue
        B = r["final"]
        ids = [b["id"] for b in B]
        order = dict((s, [ids.index(i) for i in lst]) for s, lst in r["order"].items())
        out["%s/%s" % (r["set"], os.path.basename(f)[:-5])] = (B, order)
    return out


def md_table(df, fmt=None):
    fmt = fmt or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _i, r in df.iterrows():
        lines.append("| " + " | ".join((fmt[c] % r[c]) if c in fmt and pd.notna(r[c]) else str(r[c])
                                       for c in cols) + " |")
    return "\n".join(lines)


def relabel(text):
    # 보고서 문구가 실험 3 기준으로 박혀 있음. 다른 트리 조건으로 돌리면 이름만 바꿔 씀(숫자, 판정은 그대로)
    if TREE_ARM == "tree3":
        return text
    return text.replace("실험 3", "실험 4").replace("(3 - 2)", "(4 - 2)").replace("tree3", TREE_ARM)


def write(name, lines):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(relabel("\n".join(lines) + "\n"))
    print("-> %s" % os.path.relpath(p, ROOT))


# ---- E1 ----

def e1():
    import fix_check as FC
    import rule_intent as RI
    js = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(BT, SETS, "tree_*.json")))]
    dep = [j for j in js if j.get("deployed")]
    rounds = [r for j in js for r in (j.get("rounds") or [])]
    proc = [
        ("시도", len(js)), ("배치", len(dep)),
        ("초안 구조 통과", sum(1 for j in js if j.get("rounds") and not j["rounds"][0].get("structure"))),
        ("초안 구조, 의미 모두 통과", sum(1 for j in js if j.get("rounds") and j["rounds"][0]["status"] == "ok")),
        ("최종 의미 오류 0", sum(1 for j in dep if not j.get("semantic_left"))),
        ("수정 회차 평균", round(np.mean([j.get("repairs", 0) for j in js if "repairs" in j] or [0]), 2)),
        ("금지 수정 거부 회차", sum(1 for r in rounds if r.get("status") == "rejected")),
        ("JSON 없는 회차", sum(1 for r in rounds if r.get("status") == "no_json")),
        ("초안 행동 수", sum(len(j.get("draft") or []) for j in js)),
        ("정규화에서 뺀 행동(표현 불가)", sum(sum(1 for x in j.get("normalize", []) if x.startswith("drop")) for j in js)),
        ("배치 행동 수", sum(len(j["final"]) for j in dep)),
        ("intent, dimension 되돌림", sum(r.get("text_restored", 0) for r in rounds)),
    ]
    e2 = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(BT, "prop_r*", "tree_*.json")))]
    proc2 = [("시도", len(e2)), ("실행 가능(최종)", sum(1 for j in e2 if j.get("executable"))),
             ("초안 실행 가능", sum(1 for j in e2 if j.get("rounds") and j["rounds"][0]["status"] == "ok")),
             ("수정 회차 평균", round(np.mean([j.get("repairs", 0) or 0 for j in e2] or [0]), 2))]

    rows = []
    for name, (B, _o) in trees().items():
        for b in B:
            knob = SET_KNOB.get(b["set"], b["set"])
            idir = RI.intent_dir(knob, b["intent"].lower())
            vd = "zero" if b.get("amount") == 0 else eff_dir(b["set"], b["effect"])
            st, why = RI.judge(knob, vd, idir)
            rows.append(dict(tree=name, id=b["id"], set=b["set"], effect=b["effect"], intent=b["intent"],
                             intent_dir=idir, status=st, why=why))
    t3 = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    t3.to_csv(os.path.join(OUT, "e1_intent.csv"), index=False, encoding="utf-8-sig")
    t2 = pd.read_csv(os.path.join(ROOT, "results", "intent", "auto.csv"))
    lines = ["# 실험 3 E1 생성 정확성", "", "생성: `scripts/exp3_eval.py e1`", "",
             "## 과정 기록(증거 아님)", "", "루프 안 검사는 통과할 때까지 고치므로 정확성 증거가 아니다.", "",
             "| 실험 3 | 값 |", "|---|---|"] + ["| %s | %s |" % x for x in proc] + [
             "", "| 실험 2 (prop_r*) | 값 |", "|---|---|"] + ["| %s | %s |" % x for x in proc2]
    lines += ["", "## intent와 effect 방향(루프 밖, 자동)", "",
              "실험 2는 의도문과 값 부호(보조 실험 8 `results/intent/auto.csv`), 실험 3은 intent와 effect. 같은 사전. "
              "실험 2 단위는 규칙 줄, 실험 3은 행동. 실험 3은 keep_right가 없어 전체 비교에서 실험 2 keep_right를 뺌.", "",
              "| 묶음 | 실험 3 불일치/판정 | 실험 2 불일치/판정 | 차이(3 - 2) [95%] |", "|---|---|---|---|"]
    for lab, m3, m2 in (("speed", t3.set == "speed", t2.knob == "speed_diff"),
                        ("전체(keep_right 뺌)", t3.set.notna(), t2.knob != "keep_right")):
        a3, a2 = t3[m3], t2[m2]
        k3, n3 = int((a3.status == "불일치").sum()), int(a3.status.isin(["일치", "불일치"]).sum())
        k2, n2 = int((a2.status == "불일치").sum()), int(a2.status.isin(["일치", "불일치"]).sum())
        if n3 and n2:
            d, lo, hi = FC.newcombe(k3, n3, k2, n2)
            ci = "%+.1f%%p [%+.1f, %+.1f]" % (100 * d, 100 * lo, 100 * hi)
        else:
            ci = "판정 0"
        lines.append("| %s | %d/%d | %d/%d | %s |" % (lab, k3, n3, k2, n2, ci))
    lines += ["", "실험 3 판정 분포:", "", md_table(t3.groupby(["set", "status"]).size().rename("n").reset_index())]
    write("e1.md", lines)


# ---- 상황 칸 ----

AXES = [("road", ["30~40", "60~90"]), ("junction", ["안", "접근", "밖"]),
        ("lead", ["없음", "추종", "빠른 접근", "정지"]), ("signal", ["적색", "그 외"]), ("lane", ["맨 오른쪽", "그 외"])]
CELLS = list(itertools.product(*[range(len(v)) for _k, v in AXES]))


def cell_z3(P, c):
    import z3
    r, j, l, s, n = c
    out = [z3.And(P["limit_kmh"] >= 30, P["limit_kmh"] <= 40) if r == 0
           else z3.And(P["limit_kmh"] >= 60, P["limit_kmh"] <= 90)]
    out.append([P["in_junction"],
                z3.And(z3.Not(P["in_junction"]), P["junction_m"] <= APPROACH_M),
                z3.And(z3.Not(P["in_junction"]), P["junction_m"] > APPROACH_M)][j])
    out.append([z3.Not(P["lead"]),
                z3.And(P["lead"], z3.Not(P["lead_stopped"]), P["dv"] <= FAST_DV),
                z3.And(P["lead"], z3.Not(P["lead_stopped"]), P["dv"] > FAST_DV),
                z3.And(P["lead"], P["lead_stopped"])][l])
    out.append(P["tl_red"] if s == 0 else z3.Not(P["tl_red"]))
    out.append(P["rightmost"] if n == 0 else z3.Not(P["rightmost"]))
    return z3.And(*out)


def cell_of(A):
    """틱 배열 -> 칸 번호(CELLS 순서). 제한속도가 두 구간 밖이면 -1"""
    lim = A["lim"]
    r = np.where((lim >= 30) & (lim <= 40), 0, np.where((lim >= 60) & (lim <= 90), 1, -1))
    j = np.where(A["inj"], 0, np.where(A["jm"] <= APPROACH_M, 1, 2))
    l = np.where(~A["lead"], 0, np.where(A["lstop"], 3, np.where(A["dv"] > FAST_DV, 2, 1)))
    s = np.where(A["red"], 0, 1)
    n = np.where(A["right"], 0, 1)
    dims = [len(v) for _k, v in AXES]
    idx = np.ravel_multi_index((np.clip(r, 0, 1), j, l, s, n), dims)
    return np.where(r < 0, -1, idx)


def cell_responses(B, order):
    """칸마다 끝 명령이 될 수 있는 조건부 행동의 (set, effect) 집합. 존재 판정(어떤 차, 칸 안 어떤 상황)"""
    import z3
    smt = S.Smt(B)
    sol = z3.Solver()
    sol.set("timeout", smt.timeout)
    sol.add(smt.D)
    out = []
    for c in CELLS:
        sol.push()
        sol.add(cell_z3(smt.P, c))
        resp = set()
        for s, ks in order.items():
            for r, k in enumerate(ks):
                if not B[k]["when"] or (s, B[k]["effect"]) in resp:
                    continue
                sol.push()
                sol.add(smt.C[k], smt.G[k], *[smt.C[i] for i in ks[:r]], *[z3.Not(smt.G[i]) for i in ks[:r]])
                ok = sol.check()
                sol.pop()
                if ok == z3.unknown:
                    raise RuntimeError("z3 unknown")
                if ok == z3.sat:
                    resp.add((s, B[k]["effect"]))
        sol.pop()
        out.append(frozenset(resp))
    return out


def cells():
    rows = []
    for name, (B, order) in trees().items():
        resp = cell_responses(B, order)
        for c, r in zip(CELLS, resp):
            rows.append(dict(tree=name, cell=CELLS.index(c), covered=bool(r),
                             response=";".join("%s:%s" % x for x in sorted(r))))
        print("  %s: 칸 %d/%d" % (name, sum(bool(r) for r in resp), len(CELLS)), flush=True)
    t = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    t.to_csv(os.path.join(OUT, "e3_cells.csv"), index=False)
    return t


def cells_report(t, expo=None):
    per = t.groupby("tree").agg(covered=("covered", "sum"),
                                distinct=("response", lambda x: len(set(v for v in x if v))))
    lines = ["# 실험 3 E3 상황 칸 포괄", "", "생성: `scripts/exp3_eval.py cells`, `logs`", "",
             "칸 = %s. 모두 %d칸. 경계(빠른 접근 %g m/s, 접근 %g m)와 축은 판단값." % (
                 " x ".join("%s(%s)" % (k, ", ".join(v)) for k, v in AXES), len(CELLS), FAST_DV, APPROACH_M),
             "조건 없는 행동은 셈에서 뺌(추첨값을 옮길 뿐 상황 대응이 아님). 칸 안 어떤 차, 어떤 상황에서든 끝 명령이 될 수 "
             "있으면 덮음(존재, 느슨 정의역). **상한이다.** 칸 안에서 속도, 임계 같은 나머지 지각값이 자유라 대부분 트리가 "
             "거의 모든 칸을 덮는다(스모크 트리 하나에서 96/96). 실제로 켜지는지는 아래 실주행 절.", "",
             "| 지표 | 트리 평균 | 최소 | 최대 |", "|---|---|---|---|",
             "| 덮은 칸 수 | %.1f | %d | %d |" % (per.covered.mean(), per.covered.min(), per.covered.max()),
             "| 서로 다른 칸 반응 수 | %.1f | %d | %d |" % (per.distinct.mean(), per.distinct.min(), per.distinct.max())]
    if expo is not None and expo.sum() > 0:
        w = expo / expo.sum()
        cov = t.pivot(index="tree", columns="cell", values="covered").reindex(columns=range(len(CELLS))).fillna(False)
        wc = (cov.astype(float) * w).sum(1)
        lines += ["| 노출 가중 포괄(tree3 주행 칸 비율로 가중) | %.3f | %.3f | %.3f |" % (wc.mean(), wc.min(), wc.max()),
                  "", "제한속도가 두 구간 밖인 틱은 노출에서 뺌. 민감도(축 하나씩 빼고 합친 칸, 합친 칸은 하위 칸 중 하나라도 "
                  "덮이면 덮음):", "", "| 뺀 축 | 노출 가중 포괄 평균 |", "|---|---|"]
        dims = [len(v) for _k, v in AXES]
        for ax in range(len(AXES)):
            keep = [i for i in range(len(AXES)) if i != ax]
            sub = [np.ravel_multi_index(tuple(CELLS[c][i] for i in keep), [dims[i] for i in keep]) for c in range(len(CELLS))]
            sub = np.array(sub)
            wm = pd.Series(w.values, index=range(len(CELLS))).groupby(sub).sum()
            cm = cov.T.groupby(sub).max().T.astype(float)
            lines.append("| %s | %.3f |" % (AXES[ax][0], (cm * wm).sum(1).mean()))
        top = w.sort_values(ascending=False).head(8)
        lines += ["", "노출 상위 칸:", "", "| 칸 | 노출 비율 | 덮은 트리 비율 |", "|---|---|---|"]
        for c, v in top.items():
            lines.append("| %s | %.3f | %.2f |" % (", ".join(AXES[i][1][CELLS[c][i]] for i in range(len(AXES))), v,
                                                   cov[c].mean()))
    return lines


# ---- 주행 기록 ----

def arrays(T):
    tl = T.tl.astype(str).to_numpy()
    lead = T.lead.to_numpy().astype(bool)
    return {"speed": T.speed_kmh.to_numpy(float), "lead": lead, "lstop": lead & T.lead_stopped.to_numpy().astype(bool),
            "gap": T.gap_m.to_numpy(float), "dv": T.dv.to_numpy(float), "inj": T.in_junction.to_numpy().astype(bool),
            "jm": T.junction_m.to_numpy(float), "red": tl == "Red", "green": tl == "Green",
            "lim": T.limit_kmh.to_numpy(float), "right": T.rightmost.to_numpy().astype(bool),
            "left": T.leftmost.to_numpy().astype(bool), "lanes": T.lane_count.to_numpy(float),
            "ltime": T.lane_time_s.to_numpy(float), "curv": T.curvature.to_numpy(float)}


def cond_vec(A, cid, v):
    """bt_runtime.EVAL의 배열판. v는 스칼라나 틱별 배열"""
    L = A["lead"]
    return {
        "IsStopped": lambda: A["speed"] < N.SENSE["v_stop_kmh"],
        "SpeedBelow": lambda: A["speed"] < v, "SpeedAbove": lambda: A["speed"] > v,
        "LeaderPresent": lambda: L, "LeaderStopped": lambda: A["lstop"],
        "GapBelow": lambda: L & (A["gap"] < v), "GapAbove": lambda: L & (A["gap"] > v),
        "ClosingFaster": lambda: L & (A["dv"] > v), "OpeningFaster": lambda: L & (-A["dv"] > v),
        "InJunction": lambda: A["inj"], "JunctionAhead": lambda: A["jm"] < v,
        "TrafficLightRed": lambda: A["red"], "TrafficLightGreen": lambda: A["green"],
        "SpeedLimitBelow": lambda: A["lim"] < v, "SpeedLimitAbove": lambda: A["lim"] > v,
        "IsRightmostLane": lambda: A["right"], "IsLeftmostLane": lambda: A["left"],
        "LaneCountAbove": lambda: A["lanes"] > v, "TimeInLaneAbove": lambda: A["ltime"] > v,
        "CurvatureAbove": lambda: A["curv"] > v,
    }[cid]()


def final_masks(B, order, A, th):
    """{행동 번호: 끝 명령 틱 마스크}. th는 Param 이름 -> 틱별 값 배열"""
    n = len(A["speed"])
    out = {}
    for _s, ks in order.items():
        taken = np.zeros(n, bool)
        for k in ks:
            g = np.ones(n, bool)
            for j, w in enumerate(B[k]["when"]):
                arg = S.cond_arg(w["cond"])
                v = None
                if arg:
                    v = th[S._pname(k, j)] if S._is_range(w[arg]) else float(w[arg])
                c = cond_vec(A, w["cond"], v)
                g &= ~c if w.get("not") else c
            out[k] = g & ~taken
            taken |= g
    return out


def parse_params(s):
    if not isinstance(s, str) or not s:
        return {}
    return dict((k, float(v)) for k, v in (x.split("=") for x in s.split(";")))


PAIRS = sorted((s, e) for s, (_k, _a, eff) in S.SETS.items() for e in eff)


def code_text(co):
    return ", ".join("%s %s" % PAIRS[i] for i in range(len(PAIRS)) if co >> i & 1) or "없음"


RUN_RE = re.compile(r"^(plain|knob|tree3|ptree|pknob|pdraft)_(Town\w+?)_s(\d+)\.csv$")


def run_files(src):
    """src는 폴더 하나 또는 쉼표로 여럿(새 조건 수집물과 기존 knob 수집물이 다른 폴더일 때)"""
    out = {}
    for f in [f for d in src.split(",") for f in glob.glob(os.path.join(d, "*.csv"))]:
        m = RUN_RE.match(os.path.basename(f))
        if m and os.path.exists(f[:-4] + "_tick.csv.gz"):
            out[(m.group(1), m.group(2), int(m.group(3)))] = f[:-4]
    return out


def load_tick(base):
    return pd.read_csv(base + "_tick.csv.gz", na_filter=False,
                       dtype={"tl": str, "fired": str})


def logs(src):
    TR = trees()
    files = run_files(src)
    keys = sorted(k for k in files if k[0] == TREE_ARM)
    if not keys:
        raise SystemExit("%s 틱 기록이 없음: %s" % (TREE_ARM, src))
    fire = {}          # (tree, bid) -> [끝 명령 틱, 그 트리 차량 틱]
    agree = [0, 0]     # 재현 일치 틱, 전체 틱
    real = []          # (tree, bid, cluster, sum w*d, sum w, n_t, n_k)
    expo = np.zeros(len(CELLS))
    cond_any = {}      # tree -> [조건부 행동이 끝 명령인 틱, 전체 틱]
    cellresp = {}      # (칸, 반응 코드) -> 틱. 코드는 끝 명령인 조건부 행동의 (set, effect) 비트
    for key in keys:
        _a, town, seed = key
        base = files[key]
        P = pd.read_csv(base + "_plan.csv")
        T = load_tick(base)
        vinfo = dict((r.vid, (r.tree, parse_params(r.params), int(r.spawn_idx))) for r in P.itertuples())
        T["tree"] = T.vid.map(lambda v: vinfo[v][0])
        A_all = arrays(T)
        cid = cell_of(A_all)
        expo += np.bincount(cid[cid >= 0], minlength=len(CELLS))
        kb = files.get(("knob", town, seed))
        K = KP = None
        if kb:
            KP = pd.read_csv(kb + "_plan.csv")
            K = load_tick(kb)
            k_sidx = dict(zip(KP.vid, KP.spawn_idx))
            K["sidx"] = K.vid.map(k_sidx)
        for name, sub in T.groupby("tree"):
            if name not in TR:
                continue
            B, order = TR[name]
            sub = sub.reset_index(drop=True)
            A = arrays(sub)
            pv = sub.vid.map(lambda v: vinfo[v][1])
            th = dict((p, pv.map(lambda d, p=p: d.get(p, np.nan)).to_numpy(float))
                      for p in set().union(*[set(d) for d in pv]) if pv.size)
            pred = final_masks(B, order, A, th)
            ex = sub.fired.str.split(";").explode()
            ex = ex[ex != ""]
            logged = {}
            for k, b in enumerate(B):
                m = np.zeros(len(sub), bool)
                m[ex.index[ex.values == b["id"]].to_numpy()] = True
                logged[k] = m
                f = fire.setdefault((name, b["id"]), [0, 0])
                f[0] += int(m.sum())
                f[1] += len(sub)
            # 재현: 기록된 지각값으로 다시 돌린 끝 명령이 실행기 기록과 같은 틱 비율. 반올림(3자리) 탓 어긋남 가능
            same = np.ones(len(sub), bool)
            for k in logged:
                same &= pred[k] == logged[k]
            agree[0] += int(same.sum())
            agree[1] += len(sub)
            ca = np.zeros(len(sub), bool)
            code = np.zeros(len(sub), np.int64)
            for k, b in enumerate(B):
                if b["when"]:
                    ca |= logged[k]
                    code |= np.where(logged[k], 1 << PAIRS.index((b["set"], b["effect"])), 0)
            c = cond_any.setdefault(name, [0, 0])
            c[0] += int(ca.sum())
            c[1] += len(sub)
            sc = cell_of(A)
            ok = sc >= 0
            for (ci, co), n in pd.Series(1, index=pd.MultiIndex.from_arrays([sc[ok], code[ok]])).groupby(level=[0, 1]).sum().items():
                cellresp[(int(ci), int(co))] = cellresp.get((int(ci), int(co)), 0) + int(n)
            if K is None:
                continue
            # 실현율: 같은 시드 knob 판의 같은 스폰 차(추첨값 같음)에서 이 행동이 끝 명령이 됐을 틱과 비교
            sidx_of = sub.vid.map(lambda v: vinfo[v][2])
            mine = set(sidx_of)
            KS = K[K.sidx.isin(mine)].reset_index(drop=True)
            if KS.empty:
                continue
            p_of = dict((vinfo[v][2], vinfo[v][1]) for v in set(sub.vid))
            kpv = KS.sidx.map(p_of)
            kth = dict((p, kpv.map(lambda d, p=p: d.get(p, np.nan)).to_numpy(float)) for p in th)
            KA = arrays(KS)
            kpred = final_masks(B, order, KA, kth)
            obs_t = {"speed": np.where(A["speed"] >= N.SENSE["v_stop_kmh"], A["speed"] / np.maximum(A["lim"], 1e-9),
                                       np.nan),
                     "gap": np.where(A["lead"] & (A["gap"] < 100), A["gap"], np.nan)}
            obs_k = {"speed": np.where(KA["speed"] >= N.SENSE["v_stop_kmh"], KA["speed"] / np.maximum(KA["lim"], 1e-9),
                                       np.nan),
                     "gap": np.where(KA["lead"] & (KA["gap"] < 100), KA["gap"], np.nan)}
            st = sidx_of.to_numpy()
            ks_ = KS.sidx.to_numpy()
            for k, b in enumerate(B):
                if (b["set"], b["effect"]) not in REAL_SIGN:
                    continue
                ot, ok_ = obs_t[b["set"]], obs_k[b["set"]]
                mt = logged[k] & np.isfinite(ot)
                mk = kpred[k] & np.isfinite(ok_)
                if not mt.any() or not mk.any():
                    continue
                dt = pd.DataFrame({"s": st[mt], "v": ot[mt]}).groupby("s").v.agg(["sum", "count"])
                dk = pd.DataFrame({"s": ks_[mk], "v": ok_[mk]}).groupby("s").v.agg(["sum", "count"])
                j = dt.join(dk, lsuffix="_t", rsuffix="_k", how="inner")
                if j.empty:
                    continue
                d = j.sum_t / j.count_t - j.sum_k / j.count_k
                w = np.minimum(j.count_t, j.count_k)
                real.append((name, b["id"], b["set"], b["effect"], "%s_s%d" % (town, seed), float((w * d).sum()),
                             float(w.sum()), int(j.count_t.sum()), int(j.count_k.sum())))
        print("  %s %s s%d" % key, flush=True)
    return fire, agree, pd.DataFrame(real, columns=["tree", "id", "set", "effect", "cluster", "wd", "w", "nt", "nk"]), \
        expo, cond_any, cellresp


def realization(R, clusters=None):
    """행동마다 차이와 실현 여부. 판정은 양쪽 틱 MIN_TICKS 이상"""
    if clusters is not None:
        R = pd.concat([R[R.cluster == c] for c in clusters], ignore_index=True)
    g = R.groupby(["tree", "id", "set", "effect"])[["wd", "w", "nt", "nk"]].sum().reset_index()
    g["diff"] = g.wd / g.w
    g["judged"] = (g.nt >= MIN_TICKS) & (g.nk >= MIN_TICKS)
    g["realized"] = g.judged & (np.sign(g["diff"]) == [REAL_SIGN[(s, e)] for s, e in zip(g.set, g.effect)])
    return g


def logs_report(src):
    fire, agree, R, expo, cond_any, cellresp = logs(src)
    TR = trees()
    F = pd.DataFrame([dict(tree=t, id=i, fired=a, ticks=b, rate=a / b if b else np.nan) for (t, i), (a, b) in fire.items()])
    F["set"] = [next(x["set"] for x in TR[t][0] if x["id"] == i) for t, i in zip(F.tree, F.id)]
    F["conditional"] = [bool(next(x["when"] for x in TR[t][0] if x["id"] == i)) for t, i in zip(F.tree, F.id)]
    os.makedirs(OUT, exist_ok=True)
    F.to_csv(os.path.join(OUT, "e2_firing.csv"), index=False)
    lines = ["# 실험 3 E2 실행 의미", "", "생성: `scripts/exp3_eval.py logs --src %s`" % os.path.relpath(src, ROOT), "",
             "재현 확인: 기록된 지각값으로 행동 목록을 다시 해석한 끝 명령이 실행기 기록과 같은 틱 %d/%d (%.4f). "
             "1보다 작으면 반올림(3자리) 경계 탓인지 먼저 볼 것." % (agree[0], agree[1], agree[0] / max(1, agree[1])), "",
             "## 발화율", "", "행동마다 tree3 주행에서 그 트리를 받은 차량-틱 중 끝 명령으로 남은 비율.", "",
             "| set | 행동 수 | 발화 0 | 중앙값 | 조건부 행동 중 발화 0 |", "|---|---|---|---|---|"]
    for s, g in F.groupby("set"):
        lines.append("| %s | %d | %d | %.4f | %d/%d |" % (s, len(g), int((g.fired == 0).sum()), g.rate.median(),
                                                        int(((g.fired == 0) & g.conditional).sum()), int(g.conditional.sum())))
    lines.append("| 전체 | %d | %d | %.4f | %d/%d |" % (len(F), int((F.fired == 0).sum()), F.rate.median(),
                                                     int(((F.fired == 0) & F.conditional).sum()), int(F.conditional.sum())))
    lines += ["", "## 실현율(speed, gap)", "",
              "켜진 틱의 관측량(speed: 제한속도 대비 속도비, 정지 틱 뺌. gap: 앞차 있을 때 간격)을 같은 시드 knob 판 같은 "
              "스폰 차가 그 행동이 켜졌을 틱과 비교. 차량별 차이를 min(틱 수)로 가중 평균. 양쪽 틱 %d 이상만 판정. "
              "lane_change, ign은 사건이 드물어 발화율만 봄(판단)." % MIN_TICKS, ""]
    if R.empty:
        lines.append("knob 틱 기록이 없어 계산 안 함.")
    else:
        g = realization(R)
        g.to_csv(os.path.join(OUT, "e2_realization.csv"), index=False)
        cl = sorted(R.cluster.unique())
        rng = np.random.RandomState(SEED)
        boots = []
        for _ in range(N_BOOT):
            gb = realization(R, list(rng.choice(cl, len(cl), replace=True)))
            j = gb[gb.judged]
            boots.append(j.realized.mean() if len(j) else np.nan)
        lo, hi = np.nanpercentile(boots, [2.5, 97.5])
        j = g[g.judged]
        lines += ["| set, effect | 판정 | 실현 |", "|---|---|---|"]
        for (s, e), gg in j.groupby(["set", "effect"]):
            lines.append("| %s %s | %d | %d |" % (s, e, len(gg), int(gg.realized.sum())))
        lines += ["", "실현 비율 %d/%d = %.3f, (맵, 시드) 묶음 부트스트랩 95%% [%.3f, %.3f]. 판정 불가 %d개." % (
            int(j.realized.sum()), len(j), j.realized.mean() if len(j) else np.nan, lo, hi, int((~g.judged).sum()))]
    write("e2.md", lines)

    cp = os.path.join(OUT, "e3_cells.csv")
    t = pd.read_csv(cp) if os.path.exists(cp) else cells()
    e3 = cells_report(t, pd.Series(expo, index=range(len(CELLS))))
    e3 += ["", "## 실주행 조건부 발화", "",
           "tree3 차량-틱 중 조건 있는 행동이 하나라도 끝 명령인 비율(실행기 기록).", "",
           "| 지표 | 값 |", "|---|---|"]
    rates = np.array([a / b for a, b in cond_any.values() if b])
    tot = sum(a for a, _b in cond_any.values()) / max(1, sum(b for _a, b in cond_any.values()))
    e3 += ["| 전체 | %.3f |" % tot, "| 트리별 중앙값 [최소, 최대] | %.3f [%.3f, %.3f] |" % (
        np.median(rates), rates.min(), rates.max())]
    CR = pd.DataFrame([dict(cell=c, code=co, n=n) for (c, co), n in cellresp.items()])
    if not CR.empty:
        CR.to_csv(os.path.join(OUT, "e3_cell_response.csv"), index=False)
        tot_c = CR.groupby("cell").n.sum()
        act = CR[CR.code > 0].groupby("cell").n.sum().reindex(tot_c.index).fillna(0) / tot_c
        top = tot_c.sort_values(ascending=False).head(12)
        e3 += ["", "칸별(tree3 노출 상위 12칸). 활성률 = 그 칸 틱 중 조건부 행동이 끝 명령인 비율. 최빈 반응은 조건부 행동이 "
               "하나라도 켜진 틱 중 가장 흔한 (set, effect) 조합.", "",
               "| 칸 | 노출 비율 | 활성률 | 최빈 반응(비율) |", "|---|---|---|---|"]
        for c, n in top.items():
            g = CR[(CR.cell == c) & (CR.code > 0)].sort_values("n", ascending=False)
            mode = "%s (%.2f)" % (code_text(int(g.iloc[0].code)), g.iloc[0].n / g.n.sum()) if len(g) else "-"
            e3 += ["| %s | %.3f | %.3f | %s |" % (", ".join(AXES[i][1][CELLS[c][i]] for i in range(len(AXES))),
                                                n / tot_c.sum(), act[c], mode)]
    write("e3.md", e3)


# ---- 사람 평가지 ----

UNIT = {"kmh": "km/h", "meters": "m", "mps": "m/s", "seconds": "s", "lanes": "", "invmeters": "1/m"}
AMT = {"speed": "% of the limit", "gap": " m", "ign_lights": " percentage points", "ign_signs": " percentage points",
       "ign_vehicles": " percentage points", "ign_walkers": " percentage points"}


def _v(x):
    return "[%g, %g] per vehicle" % tuple(x) if isinstance(x, (list, tuple)) else "%g" % x


def render(conds, s, e, amount, intent):
    cs = []
    for cid, v, neg in conds:
        arg = S.cond_arg(cid)
        t = cid if not arg else "%s %s %s" % (cid, _v(v), UNIT.get(arg, ""))
        cs.append(("not " if neg else "") + t.strip())
    head = "When " + " and ".join(cs) if cs else "Always"
    act = "%s %s" % (s, e) if amount is None else "%s %s by %s%s" % (s, e, _v(amount), AMT[s])
    return "%s: %s, to %s" % (head, act, intent)


def exp2_items():
    """실험 2 규칙 중 행동 하나, 조건 읽힘, 방향 정해짐(구간이 기준값 안 품음), keep_right 아님. 같은 문장꼴로 바꿈"""
    import bt_verify as BV
    import rule_intent as RI
    out = []
    for r in RI.rules():
        p, _why = BV.parse_rule(r["rule"], r["decl"])
        a, _w2 = RI.parse_action(r["rule"], r["decl"])
        if not p or not a or not a[3] or a[0] == "keep_right":
            continue
        knob, kind, v, intent = a
        vd = RI.value_dir(knob, kind, v)
        if vd not in ("up", "down"):
            continue
        conds = []
        for cid, cv in p["conds"]:
            if cv is None:
                conds.append((cid, None, False))
            elif cv[0] == "lit":
                conds.append((cid, cv[1], False))
            else:
                lo, hi, _d = r["decl"][cv[1]]
                conds.append((cid, [lo, hi], False))
        s = {"speed_diff": "speed", "gap_m": "gap"}.get(knob, knob)
        if knob == "lane_change":
            out.append(dict(src="exp2", tree=r["tree"], text=render(conds, s, "allow" if v else "forbid", None, intent)))
            continue
        b = N.KNOBS[knob][0]
        sg = 1 if vd == "up" else -1
        e = {("speed", -1): "slower", ("speed", 1): "faster", ("gap", 1): "wider", ("gap", -1): "narrower"}.get(
            (s, sg), "more" if sg > 0 else "less")
        if knob == "speed_diff":
            amt = abs(v - b) if kind == "lit" else sorted([abs(v[0] - b), abs(v[1] - b)])
        else:
            amt = abs(v - b) if kind == "lit" else sorted([abs(v[0] - b), abs(v[1] - b)])
        out.append(dict(src="exp2", tree=r["tree"], text=render(conds, s, e, amt, intent)))
    return out


def exp3_items():
    out = []
    for name, (B, _o) in trees().items():
        for b in B:
            conds = []
            for w in b["when"]:
                arg = S.cond_arg(w["cond"])
                conds.append((w["cond"], w[arg] if arg else None, bool(w.get("not"))))
            out.append(dict(src="exp3", tree=name, text=render(conds, b["set"], b["effect"], b.get("amount"),
                                                                 b["intent"].strip().rstrip("."))))
    return out


def sheet(n=30):
    rng = np.random.RandomState(SEED)
    i2, i3 = exp2_items(), exp3_items()
    if len(i2) < n or len(i3) < n:
        raise SystemExit("항목 부족: 실험 2 %d, 실험 3 %d" % (len(i2), len(i3)))
    pick = [i2[k] for k in rng.choice(len(i2), n, replace=False)] + [i3[k] for k in rng.choice(len(i3), n, replace=False)]
    rng.shuffle(pick)
    d = os.path.join(OUT, "rating")
    os.makedirs(d, exist_ok=True)
    sh = pd.DataFrame([dict(id=k + 1, behaviour=x["text"], Q1="", Q2="", Q3="", memo="") for k, x in enumerate(pick)])
    for who in ("A", "B"):
        sh.to_csv(os.path.join(d, "sheet_%s.csv" % who), index=False, encoding="utf-8-sig")
    pd.DataFrame([dict(id=k + 1, src=x["src"], tree=x["tree"]) for k, x in enumerate(pick)]).to_csv(KEY, index=False)
    import rule_intent as RI
    guide = ("# 실험 3 사람 평가 안내\n\n행동 한 줄마다 세 문항에 답한다. 다른 평가자와 상의하지 않는다.\n\n"
             "- speed slower/faster by X% of the limit: 목표 속도를 제한속도의 X%만큼 낮춤/높임\n"
             "- gap wider/narrower by X m: 앞차와 최소 간격을 X m 넓힘/좁힘\n"
             "- ign_* more by X percentage points: 신호, 표지, 차량, 보행자를 무시할 확률을 X%p 높임\n"
             "- lane_change allow/forbid: 스스로 차로 변경 허용/금지\n"
             "- [a, b] per vehicle: 차량마다 그 구간에서 한 번 뽑는 값\n\n" + RI.SIM + "\n\n"
             "## 문항\n\n- **Q1 의도와 행동:** `to ...` 의도와 행동 방향이 맞는가. 맞음 / 틀림 / 판단 불가\n"
             "- **Q2 타당성:** 실제 도로에서 운전자가 이 조건에서 이렇게 행동하는 게 그럴듯한가. 예 / 애매 / 아니오\n"
             "- **Q3 역할 침범:** 위 시뮬레이터 몫을 이 행동이 다시 하려 하는가. 예 / 아니오\n")
    # % 포맷 안 씀. 안내문의 "X% of"가 %o로 읽혀 터졌음
    with open(os.path.join(d, "GUIDE.md"), "w", encoding="utf-8") as fh:
        fh.write(guide)
    print("평가지 %d줄: %s, 대응표 %s(평가 끝까지 열지 말 것)" % (len(sh), os.path.relpath(d, ROOT), os.path.relpath(KEY, ROOT)))


def main():
    global TREE_ARM, SETS, OUT, BT
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("e1", "cells", "logs", "sheet", "all"))
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=None, help="파일럿은 따로 둘 것")
    ap.add_argument("--arm", default=TREE_ARM, help="트리 조건 이름. 실험 4는 ptree")
    ap.add_argument("--sets", default=SETS, help="트리 세트 glob. 실험 4는 exp4_*")
    ap.add_argument("--bt", default=None, help="트리 기록 폴더. 수리 효과 pdraft는 data/outputs/bt_draft")
    a = ap.parse_args()
    TREE_ARM, SETS = a.arm, a.sets
    if a.bt:
        BT = os.path.abspath(a.bt)
    if a.out:
        OUT = os.path.abspath(a.out)
    if a.cmd in ("e1", "all"):
        e1()
    if a.cmd in ("cells", "all"):
        write("e3.md", cells_report(cells()))
    if a.cmd in ("logs", "all"):
        logs_report(a.src)
    if a.cmd == "sheet":
        sheet()


if __name__ == "__main__":
    main()
