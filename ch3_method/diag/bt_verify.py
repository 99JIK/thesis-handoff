"""보조 실험 6: 트리 수준 전수 검사. py3.11 + z3. 2026-11-19 추가, 탐색.
규칙은 docs/design.md 보조 실험 6 절(커밋 e7d7518)에서 고정. 여기서 안 바꿈.

  python diag/bt_verify.py        -> results/verify/

순서: 1) 같은 인코더를 구체값 백엔드로 돌려 실행기(py3.11, py3.7 둘 다)와 무작위 입력 일치 확인,
z3 백엔드도 일부 표본에서 대입 확인. 하나라도 어긋나면 SMT 단계 안 감. 2) 규칙 이행, 노드 도달 검사.
3) 반례를 실행기에 다시 넣어 재현 확인.

인코더 하나를 두 백엔드로 쓰는 게 핵심. 구체값 백엔드가 실행기와 맞으면 합성 노드 의미(게이팅, 부작용,
덮어쓰기 순서)는 검증된 것이고, z3 백엔드는 같은 코드에 기호 연산만 바꾼 것.
"""
import glob
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np
import pandas as pd
import z3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bt_nodes as BN  # noqa: E402
import bt_runtime as RT  # noqa: E402

BT_DIR = os.path.join(ROOT, "data", "outputs", "bt")
RO_DIR = os.path.join(ROOT, "data", "outputs", "bt_ro")
WORK = os.path.join(ROOT, "data", "outputs", "verify")
OUT = os.path.join(ROOT, "results", "verify")
LANE_JSON = os.path.join(ROOT, "data", "outputs", "carla_speed", "lane_speed.json")   # measure.LANE_JSON과 같은 파일

N_THETA, N_P = 100, 100       # 트리당 θ 100개 x p 100개 = 10,000
N_Z3 = 200                    # z3 대입 확인 표본(트리당)
TOL = 1e-6
SEED = 0
QTIMEOUT_MS = 20000

# 느슨 정의역. 지각 치역의 상위집합이어야 항상 이행 판정이 건전함. 그래서 상한은 넉넉히(속도 200 등).
# 판단값이지만 넓히는 쪽이라 항상 이행을 거짓으로 만들 순 있어도 참으로 만들진 않음
V_MAX = 200.0
GAP_LO, GAP_HI = -10.0, 100.0
DV_MAX = V_MAX / 3.6
LIM_MAX = 200.0
CURV_MAX = math.pi / 10.0
JUNC_VALUES = [10.0 * (i + 1) for i in range(int(BN.SENSE["junction_scan_m"] // 10))] + [999.0]
SIDE_MAX = 4                   # Perception._slow_of가 좌우로 4칸까지만 셈
TL = ["", "Red", "Yellow", "Green"]
V_STOP = BN.SENSE["v_stop_kmh"]

ACT_IDS = [a[0] for a in BN.ACTIONS]
COND_IDS = [c[0] for c in BN.CONDITIONS]


# ---- 백엔드 ----

class Concrete(object):
    T, F = True, False

    @staticmethod
    def And(*xs):
        return all(xs)

    @staticmethod
    def Or(*xs):
        return any(xs)

    @staticmethod
    def Not(x):
        return not x

    @staticmethod
    def If(c, a, b):
        return a if c else b


class Z3(object):
    T, F = z3.BoolVal(True), z3.BoolVal(False)

    @staticmethod
    def And(*xs):
        return z3.And(*xs) if xs else z3.BoolVal(True)

    @staticmethod
    def Or(*xs):
        return z3.Or(*xs) if xs else z3.BoolVal(False)

    Not = staticmethod(z3.Not)
    If = staticmethod(z3.If)


def cond_expr(B, P, nid, v):
    """bt_runtime.EVAL과 같은 식. P는 지각(구체값이든 z3든), v는 임계"""
    if nid == "IsStopped":
        return P["stopped"]
    if nid == "SpeedBelow":
        return P["speed_kmh"] < v
    if nid == "SpeedAbove":
        return P["speed_kmh"] > v
    if nid == "LeaderPresent":
        return P["lead"]
    if nid == "LeaderStopped":
        return B.And(P["lead"], P["lead_stopped"])
    if nid == "GapBelow":
        return B.And(P["lead"], P["gap_m"] < v)
    if nid == "GapAbove":
        return B.And(P["lead"], P["gap_m"] > v)
    if nid == "ClosingFaster":
        return B.And(P["lead"], P["dv"] > v)
    if nid == "OpeningFaster":
        return B.And(P["lead"], -P["dv"] > v)
    if nid == "InJunction":
        return P["in_junction"]
    if nid == "JunctionAhead":
        return P["junction_m"] < v
    if nid == "TrafficLightRed":
        return P["tl_red"]
    if nid == "TrafficLightGreen":
        return P["tl_green"]
    if nid == "SpeedLimitBelow":
        return P["limit_kmh"] < v
    if nid == "SpeedLimitAbove":
        return P["limit_kmh"] > v
    if nid == "IsRightmostLane":
        return P["rightmost"]
    if nid == "IsLeftmostLane":
        return P["leftmost"]
    if nid == "LaneCountAbove":
        return P["lane_count"] > v
    if nid == "TimeInLaneAbove":
        return P["lane_time_s"] > v
    if nid == "CurvatureAbove":
        return P["curvature"] > v
    raise KeyError(nid)


assert set(COND_IDS) == set(RT.EVAL)


def resolve(raw, TH):
    m = BN.PARAM_REF.match(raw or "")
    if m:
        return TH[m.group(1)]
    return BN._num(raw)


def clamp_e(B, knob, v):
    _b, lo, hi = BN.KNOBS[knob]
    return B.If(v < lo, lo, B.If(v > hi, hi, v))


class Enc(object):
    """트리 하나를 백엔드 B로 펼침. 틱 순서대로 쓰기(writer)를 남김.
    bt_runtime.build와 같은 분기. memory=False, 리프 즉시 반환이라 상태는 want 하나뿐"""

    def __init__(self, B, P, TH):
        self.B, self.P, self.TH = B, P, TH
        self.writers = []      # (Action 요소, 노브, 발화식, 값식)

    def run(self, root):
        st = dict((k, v) for k, v in RT.baseline_want().items())
        if self.B is Z3:
            st = dict((k, (z3.BoolVal(v) if isinstance(v, bool) else z3.RealVal(v))) for k, v in st.items())
        _s, st = self.ex(root, self.B.T, st)
        return st

    def ex(self, el, active, st):
        B = self.B
        tag = el.tag
        kids = [c for c in el if isinstance(c.tag, str)]
        if tag in ("root", "BehaviorTree"):
            for c in kids:
                if c.tag in ("TreeNodesModel", BN.PARAMS_TAG):
                    continue
                return self.ex(c, active, st)
            raise ValueError("빈 %s" % tag)
        if tag == "Condition":
            nid = el.get("ID")
            arg = RT.COND_ARG[nid]
            v = resolve(el.get(arg), self.TH) if arg else None
            return cond_expr(B, self.P, nid, v), st
        if tag == "Action":
            nid = el.get("ID")
            knob, arg = RT.ACT[nid]
            raw = el.get(arg)
            if knob is None:
                knob = "lane_change"
                v = str(raw).strip().lower() == "true"
                if B is Z3:
                    v = z3.BoolVal(v)
            else:
                v = resolve(raw, self.TH)
                if B is Z3 and not z3.is_expr(v):
                    v = z3.RealVal(v)
                v = clamp_e(B, knob, v)
            st = dict(st)
            st[knob] = B.If(active, v, st[knob])
            self.writers.append((el, knob, active, v))
            return B.T, st
        if tag == "Sequence":
            a, succ = active, []
            for c in kids:
                s, st = self.ex(c, a, st)
                succ.append(s)
                a = B.And(a, s)
            return B.And(*succ), st
        if tag in ("Fallback", "Selector"):
            a, succ = active, []
            for c in kids:
                s, st = self.ex(c, a, st)
                succ.append(s)
                a = B.And(a, B.Not(s))
            return B.Or(*succ), st
        if tag == "Parallel":
            # py_trees 2.1.6, 2.4.0 둘 다 RUNNING 아니면 매 틱 자식 리셋하고 전부 순서대로 틱
            succ = []
            for c in kids:
                s, st = self.ex(c, active, st)
                succ.append(s)
            return B.And(*succ), st
        if tag == "Inverter":
            s, st = self.ex(kids[0], active, st)
            return B.Not(s), st
        if tag == "ForceSuccess":
            _s, st = self.ex(kids[0], active, st)
            return B.T, st
        if tag == "ForceFailure":
            _s, st = self.ex(kids[0], active, st)
            return B.F, st
        if tag in ("Repeat", "RetryUntilSuccessful"):
            return self.ex(kids[0], active, st)
        raise ValueError("모르는 태그 <%s>" % tag)


# ---- 지각 ----

def derive(P, B):
    """원시 항목에서 파생 항목. 구체값, z3 공통"""
    P = dict(P)
    P["stopped"] = P["speed_kmh"] < V_STOP
    P["leftmost"] = P["nl"] == 0
    P["rightmost"] = P["nr"] == 0
    P["lane_count"] = P["nl"] + P["nr"] + 1
    return P


def concrete_p(s):
    P = dict(s)
    P["tl_red"] = s["tl"] == "Red"
    P["tl_green"] = s["tl"] == "Green"
    return derive(P, Concrete)


def runtime_p(s):
    """실행기 ctx.p 꼴. Perception.update와 _slow_of 출력 모양"""
    P = concrete_p(s)
    keys = list(RT.BLANK)
    return dict((k, P[k]) for k in keys)


def z3_p():
    P = {
        "speed_kmh": z3.Real("speed"), "lead": z3.Bool("lead"), "lead_stopped": z3.Bool("lead_stopped"),
        "gap_m": z3.Real("gap"), "dv": z3.Real("dv"), "in_junction": z3.Bool("in_junction"),
        "junction_m": z3.Real("junction_m"), "tl": z3.Int("tl"), "limit_kmh": z3.Real("limit"),
        "nl": z3.Int("nl"), "nr": z3.Int("nr"), "lane_time_s": z3.Real("lane_time"),
        "curvature": z3.Real("curv"),
    }
    P["tl_red"] = P["tl"] == 1
    P["tl_green"] = P["tl"] == 3
    return derive(P, Z3)


def domain(P, tight, lims):
    c = [P["speed_kmh"] >= 0, P["speed_kmh"] <= V_MAX,
         z3.If(P["lead"],
               z3.And(P["gap_m"] >= GAP_LO, P["gap_m"] <= GAP_HI, P["dv"] >= -DV_MAX, P["dv"] <= DV_MAX),
               z3.And(P["gap_m"] == 999, P["dv"] == 0, z3.Not(P["lead_stopped"]))),
         z3.Or(*[P["junction_m"] == v for v in JUNC_VALUES]),
         P["tl"] >= 0, P["tl"] <= 3,
         P["limit_kmh"] >= 0, P["limit_kmh"] <= LIM_MAX,
         P["nl"] >= 0, P["nl"] <= SIDE_MAX, P["nr"] >= 0, P["nr"] <= SIDE_MAX,
         P["lane_time_s"] >= 0, P["curvature"] >= 0, P["curvature"] <= CURV_MAX]
    if tight:
        lv = P["speed_kmh"] / 3.6 - P["dv"]        # 선행차 속도 m/s
        c += [z3.Implies(P["lead"], z3.And(lv >= 0, lv <= V_MAX / 3.6,
                                           P["lead_stopped"] == (lv * 3.6 < V_STOP))),
              z3.Or(*[P["limit_kmh"] == v for v in lims])]
    return z3.And(*c)


def z3_theta(decl):
    TH = dict((n, z3.Real("th_" + n)) for n in decl)
    cons = [z3.And(TH[n] >= lo, TH[n] <= hi) for n, (lo, hi, _d) in decl.items()]
    return TH, z3.And(*cons) if cons else z3.BoolVal(True)


def map_limits():
    with open(LANE_JSON, encoding="utf-8") as fh:
        d = json.load(fh)
    return sorted({float(s["limit"]) for town in d.values() for s in town if s.get("limit") is not None})


# ---- 표본과 실행기 대조 ----

def sample_p(rng):
    lead = bool(rng.rand() < 0.6)
    s = {"speed_kmh": float(rng.uniform(0, 130) if rng.rand() < 0.9 else rng.uniform(0, 3)),
         "lead": lead,
         "lead_stopped": bool(lead and rng.rand() < 0.3),
         "gap_m": float(rng.uniform(-5, 100)) if lead else 999.0,
         "dv": float(rng.uniform(-20, 20)) if lead else 0.0,
         "in_junction": bool(rng.rand() < 0.3),
         "junction_m": float(JUNC_VALUES[rng.randint(len(JUNC_VALUES))]),
         "tl": TL[rng.randint(4)],
         "limit_kmh": float(rng.choice([30, 40, 50, 60, 90]) if rng.rand() < 0.7 else rng.uniform(0, 100)),
         "nl": int(rng.randint(0, 3)), "nr": int(rng.randint(0, 3)),
         "lane_time_s": float(rng.uniform(0, 150)),
         "curvature": float(rng.uniform(0, CURV_MAX) if rng.rand() < 0.5 else rng.uniform(0, 0.02))}
    return s


def sample_theta(decl, rng):
    return dict((n, float(rng.uniform(lo, hi))) for n, (lo, hi, _d) in decl.items())


def model_want(root, s, th):
    e = Enc(Concrete, concrete_p(s), th)
    return e.run(root)


def runtime_want(xml_text, cases):
    """py3.11 실행기. cases = [(θ, [s, ...]), ...]"""
    el = ET.fromstring(xml_text)
    out = []
    for th, ps in cases:
        ctx = RT.Ctx()
        root = RT.build(el, ctx, th)
        for s in ps:
            ctx.p = runtime_p(s)
            out.append(dict(RT.tick(root, ctx)))
    return out


def same(a, b):
    if set(a) != set(b):
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, bool) or isinstance(y, bool):
            if bool(x) != bool(y):
                return False
        elif abs(float(x) - float(y)) > 1e-9:
            return False
    return True


def zval(e):
    e = z3.simplify(e)
    if z3.is_true(e):
        return True
    if z3.is_false(e):
        return False
    if z3.is_rational_value(e):
        return float(Fraction(e.numerator_as_long(), e.denominator_as_long()))
    if z3.is_algebraic_value(e):
        return float(e.approx(20).as_fraction())
    raise ValueError("구체값 아님: %s" % e)


def z3_subst_want(enc, s, th):
    P, TH, want = enc
    sub = [(z3.Real("speed"), z3.RealVal(s["speed_kmh"])), (z3.Bool("lead"), z3.BoolVal(s["lead"])),
           (z3.Bool("lead_stopped"), z3.BoolVal(s["lead_stopped"])), (z3.Real("gap"), z3.RealVal(s["gap_m"])),
           (z3.Real("dv"), z3.RealVal(s["dv"])), (z3.Bool("in_junction"), z3.BoolVal(s["in_junction"])),
           (z3.Real("junction_m"), z3.RealVal(s["junction_m"])), (z3.Int("tl"), z3.IntVal(TL.index(s["tl"]))),
           (z3.Real("limit"), z3.RealVal(s["limit_kmh"])), (z3.Int("nl"), z3.IntVal(s["nl"])),
           (z3.Int("nr"), z3.IntVal(s["nr"])), (z3.Real("lane_time"), z3.RealVal(s["lane_time_s"])),
           (z3.Real("curv"), z3.RealVal(s["curvature"]))]
    sub += [(TH[n], z3.RealVal(v)) for n, v in th.items()]
    return dict((k, zval(z3.substitute(v, *sub))) for k, v in want.items())


# ---- 트리 목록 ----

def trees():
    out = []
    for f in sorted(glob.glob(os.path.join(BT_DIR, "*", "tree_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        if not d.get("executable"):
            continue
        st = os.path.basename(os.path.dirname(f))
        xmlp = f[:-5] + ".xml"
        out.append(dict(name="%s/%s" % (st, os.path.basename(f)[:-5]), set=st, kind="orig",
                        strategy="prop" if st.startswith("prop") else "zs", xml=xmlp,
                        enum=(d.get("intermediate") or {}).get("enumeration")))
    for f in sorted(glob.glob(os.path.join(RO_DIR, "*", "tree_*.xml"))):
        st = os.path.basename(os.path.dirname(f))
        out.append(dict(name="%s/%s" % (st, os.path.basename(f)[:-4]), set=st, kind="ro",
                        strategy="prop", xml=f, enum=None))
    return out


def load_tree(t):
    xml = open(t["xml"], encoding="utf-8").read()
    root = ET.fromstring(xml)
    decl = BN.parse_params(root, lambda *_a: None)
    return xml, root, decl


def validate_all(ts):
    rng = np.random.RandomState(SEED)
    os.makedirs(WORK, exist_ok=True)
    jobs, rows = [], []
    for t in ts:
        xml, root, decl = load_tree(t)
        cases = [(sample_theta(decl, rng), [sample_p(rng) for _j in range(N_P)]) for _i in range(N_THETA)]
        model = [model_want(root, s, th) for th, ps in cases for s in ps]
        rt11 = runtime_want(xml, cases)
        bad11 = sum(not same(a, b) for a, b in zip(model, rt11))
        flat = [(th, s) for th, ps in cases for s in ps]
        idx = rng.choice(len(flat), size=min(N_Z3, len(flat)), replace=False)
        P = z3_p()
        TH, _c = z3_theta(decl)
        enc = (P, TH, Enc(Z3, P, TH).run(root))
        badz = sum(not same(z3_subst_want(enc, flat[i][1], flat[i][0]), model[i]) for i in idx)
        jobs.append(dict(name=t["name"], xml=t["xml"], cases=[dict(theta=th, ps=[runtime_p(s) for s in ps])
                                                               for th, ps in cases], model=model))
        rows.append(dict(tree=t["name"], n=len(model), mismatch_py311=bad11, n_z3=len(idx), mismatch_z3=badz))
    inp = os.path.join(WORK, "rt_in.json")
    outp = os.path.join(WORK, "rt37_out.json")
    with open(inp, "w", encoding="utf-8") as fh:
        json.dump([dict(name=j["name"], xml=j["xml"], cases=j["cases"]) for j in jobs], fh)
    r = subprocess.run(["py", "-3.7", "-X", "utf8", os.path.join(ROOT, "diag", "verify_runtime.py"), inp, outp],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError("py3.7 실행기 실패:\n" + r.stderr[-2000:])
    rt37 = json.load(open(outp, encoding="utf-8"))
    assert len(rt37) == len(jobs)
    for row, j, o in zip(rows, jobs, rt37):
        row["mismatch_py37"] = sum(not same(a, b) for a, b in zip(j["model"], o))
    return pd.DataFrame(rows)


# ---- 규칙 번역 ----

RULE_HEAD = re.compile(r"^(When|If)\s+(.*)$", re.I)
PIECE = re.compile(r'^([A-Za-z]+)(?:\s+(\w+)="([^"]*)")?$')
ACT_RE = re.compile(r"\b(%s)\b" % "|".join(ACT_IDS))


def parse_value(raw, decl):
    m = BN.PARAM_REF.match(raw or "")
    if m:
        return ("param", m.group(1)) if m.group(1) in decl else None
    x = BN._num(raw)
    return ("lit", x) if x is not None else None


def parse_rule(line, decl):
    """(규칙, None) 또는 (None, 못 읽은 이유)"""
    text = line.strip()[1:].strip()
    m = RULE_HEAD.match(text)
    if not m:
        return None, "형식"
    rest = m.group(2)
    acts = list(ACT_RE.finditer(rest))
    if len(acts) != 1:
        return None, "행동 수 %d" % len(acts)
    a0 = acts[0]
    ctext = rest[:a0.start()].strip()
    ctext = re.sub(r"(,|\bthen)\s*$", "", ctext, flags=re.I).strip()
    ctext = re.sub(r"(,|\bthen)\s*$", "", ctext, flags=re.I).strip()
    if not ctext:
        return None, "조건 없음"
    if re.search(r"\b(or|not|unless)\b", ctext, re.I):
        return None, "or, 부정"
    conds = []
    for piece in re.split(r"\s+and\s+", ctext):
        pm = PIECE.match(piece.strip())
        if not pm or pm.group(1) not in RT.COND_ARG:
            return None, "조건 형식"
        nid, an, av = pm.groups()
        need = RT.COND_ARG[nid]
        if need is None:
            if an is not None:
                return None, "조건 인자"
            conds.append((nid, None))
            continue
        if an != need:
            return None, "조건 인자"
        v = parse_value(av, decl)
        if v is None:
            return None, "조건 값"
        conds.append((nid, v))
    am = re.match(r'(\w+)\s+(\w+)="([^"]*)"', rest[a0.start():])
    if not am:
        return None, "행동 인자"
    aid, an, av = am.groups()
    knob, need = RT.ACT[aid]
    if an != need:
        return None, "행동 인자"
    if knob is None:
        if av.strip().lower() not in ("true", "false"):
            return None, "행동 값"
        val = ("bool", av.strip().lower() == "true")
        knob = "lane_change"
    else:
        val = parse_value(av, decl)
        if val is None:
            return None, "행동 값"
    return dict(conds=conds, knob=knob, val=val, text=line.strip()), None


def rule_lines(enum):
    return [ln for ln in (enum or "").splitlines() if ln.strip().startswith("-")]


def vexpr(v, TH):
    k, x = v
    if k == "param":
        return TH[x]
    if k == "bool":
        return z3.BoolVal(x)
    return z3.RealVal(x)


def is_base_rule(r, decl):
    base = RT.baseline_want()[r["knob"]]
    k, x = r["val"]
    if k == "bool":
        return x == base
    if k == "lit":
        return abs(x - base) <= TOL
    lo, hi, _d = decl[x]
    return lo <= base <= hi


def same_val(a, b):
    if a[0] != b[0]:
        return False
    if a[0] == "lit":
        return abs(a[1] - b[1]) <= 1e-9
    return a[1] == b[1]


# ---- SMT ----

def sat(s, *fs):
    s.push()
    s.add(*fs)
    r = s.check()
    m = s.model() if r == z3.sat else None
    s.pop()
    return r, m


def cex_of(m, P, TH):
    def g(e, kind):
        v = m.eval(e, model_completion=True)
        if kind == "b":
            return bool(z3.is_true(v))
        if kind == "i":
            return int(v.as_long())
        return zval(v)
    s = {"speed_kmh": g(P["speed_kmh"], "r"), "lead": g(P["lead"], "b"), "lead_stopped": g(P["lead_stopped"], "b"),
         "gap_m": g(P["gap_m"], "r"), "dv": g(P["dv"], "r"), "in_junction": g(P["in_junction"], "b"),
         "junction_m": g(P["junction_m"], "r"), "tl": TL[g(P["tl"], "i")], "limit_kmh": g(P["limit_kmh"], "r"),
         "nl": g(P["nl"], "i"), "nr": g(P["nr"], "i"), "lane_time_s": g(P["lane_time_s"], "r"),
         "curvature": g(P["curvature"], "r")}
    th = dict((n, g(v, "r")) for n, v in TH.items())
    return s, th


def eq_e(f, a):
    if z3.is_bool(f):
        return f == a
    return z3.And(f - a <= TOL, a - f <= TOL)


def check_rules(t, lims):
    xml, root, decl = load_tree(t)
    lines = rule_lines(t["enum"])
    parsed, rows = [], []
    for ln in lines:
        r, why = parse_rule(ln, decl)
        if r is None:
            rows.append(dict(tree=t["name"], rule=ln.strip(), status="번역 불가", why=why))
        else:
            parsed.append(r)
    P = z3_p()
    TH, thc = z3_theta(decl)
    want = Enc(Z3, P, TH).run(root)
    pvars = [P[k] for k in ("speed_kmh", "lead", "lead_stopped", "gap_m", "dv", "in_junction", "junction_m",
                            "tl", "limit_kmh", "nl", "nr", "lane_time_s", "curvature")]
    cexs = []
    for tight in (False, True):
        D = z3.And(domain(P, tight, lims), thc)
        s = z3.Solver()
        s.add(D)
        Cs = [z3.And(*[cond_expr(Z3, P, nid, (vexpr(v, TH) if v else None)) for nid, v in r["conds"]])
              for r in parsed]
        for i, r in enumerate(parsed):
            C = Cs[i]
            f = want[r["knob"]]
            a = vexpr(r["val"], TH)
            ok = eq_e(f, a)
            rc, _m = sat(s, C)
            row = dict(tree=t["name"], rule=r["text"], knob=r["knob"], domain="조임" if tight else "느슨",
                       base_rule=is_base_rule(r, decl), why="")
            if rc != z3.sat:
                row["status"] = "공허"
                rows.append(row)
                continue
            rh, _m = sat(s, C, ok)
            rv, mv = sat(s, C, z3.Not(ok))
            if rh != z3.sat:
                row["status"] = "불이행"
            elif rv != z3.sat:
                row["status"] = "항상 이행"
            else:
                row["status"] = "일부 이행"
            if rv == z3.sat:
                conf = [j for j, q in enumerate(parsed)
                        if j != i and q["knob"] == r["knob"] and not same_val(q["val"], r["val"])]
                row["plan_conflict"] = any(sat(s, C, Cs[j])[0] == z3.sat for j in conf)
                rs, ms = sat(s, C, z3.Not(ok), *[z3.Not(Cs[j]) for j in conf])
                row["synth_violation"] = rs == z3.sat
                use = ms if rs == z3.sat else mv
                ps, th = cex_of(use, P, TH)
                row["cex"] = json.dumps(dict(p=ps, theta=th), ensure_ascii=False)
                if not tight:
                    cexs.append(dict(tree=t["name"], xml=t["xml"], rule=r, p=ps, theta=th))
            if row["status"] == "일부 이행" and decl:
                # θ 따라 갈리나: 어떤 θ에선 모든 p에서 이행하는가(∃θ∀p)
                q = z3.Solver()
                q.set("timeout", QTIMEOUT_MS)
                q.add(thc)
                q.add(z3.ForAll(pvars, z3.Implies(z3.And(domain(P, tight, lims), C), ok)))
                rq = q.check()
                row["some_theta_always"] = True if rq == z3.sat else (False if rq == z3.unsat else "unknown")
            rows.append(row)
    return rows, cexs, len(lines), len(parsed)


def static_dead(root, decl):
    """bt_nodes.unreachable과 같은 판정으로 죽는다고 본 Action 요소 id 집합"""
    dead = set()
    for fb in [e for e in root.iter() if e.tag in ("Fallback", "Selector")]:
        kids = [c for c in fb if isinstance(c.tag, str)]
        G = [BN._guards(c, decl) for c in kids]
        for j in range(len(kids)):
            for i in range(j):
                if not G[i] or all(any(BN._implies(gj, gi) for gj in G[j]) for gi in G[i]):
                    dead |= {id(a) for a in kids[j].iter("Action")}
                    break
    return dead


def check_nodes(t, lims):
    xml, root, decl = load_tree(t)
    P = z3_p()
    TH, thc = z3_theta(decl)
    enc = Enc(Z3, P, TH)
    enc.run(root)
    sd = static_dead(root, decl)
    s = z3.Solver()
    s.add(domain(P, False, lims), thc)
    rows = []
    W = enc.writers
    for i, (el, knob, act, _v) in enumerate(W):
        later = [w[2] for w in W[i + 1:] if w[1] == knob]
        fires = sat(s, act)[0] == z3.sat
        eff = fires and sat(s, act, *[z3.Not(x) for x in later])[0] == z3.sat
        rows.append(dict(tree=t["name"], kind=t["kind"], strategy=t["strategy"], node=i, action=el.get("ID"),
                         knob=knob, static_dead=id(el) in sd, fires=fires, effective=eff))
    return rows


def replay(cexs):
    """반례를 py3.11 실행기에 넣어 조건 참, 명령이 규칙 값과 다른지 재현"""
    out = []
    for c in cexs:
        xml = open(c["xml"], encoding="utf-8").read()
        w = runtime_want(xml, [(c["theta"], [c["p"]])])[0]
        p = runtime_p(c["p"])
        r = c["rule"]
        cond_ok = all(RT.EVAL[nid](p, (c["theta"][v[1]] if v and v[0] == "param" else (v[1] if v else None)))
                      for nid, v in r["conds"])
        k, x = r["val"]
        a = c["theta"][x] if k == "param" else x
        diff = (bool(w[r["knob"]]) != bool(a)) if k == "bool" else abs(float(w[r["knob"]]) - float(a)) > TOL
        out.append(dict(tree=c["tree"], rule=r["text"], cond_true=cond_ok, cmd_differs=diff))
    return pd.DataFrame(out)


def main():
    os.makedirs(OUT, exist_ok=True)
    ts = trees()
    v = validate_all(ts)
    v.to_csv(os.path.join(OUT, "validation.csv"), index=False)
    bad = v[(v.mismatch_py311 > 0) | (v.mismatch_py37 > 0) | (v.mismatch_z3 > 0)]
    print("검증: 트리 %d, 표본 %d, py3.11 불일치 %d, py3.7 불일치 %d, z3 불일치 %d" % (
        len(v), v.n.sum(), v.mismatch_py311.sum(), v.mismatch_py37.sum(), v.mismatch_z3.sum()))
    if len(bad):
        print(bad.to_string())
        print("모형과 실행기가 어긋나서 SMT 단계 안 감")
        return
    lims = map_limits()
    rule_rows, cexs, tot = [], [], []
    for t in ts:
        if t["kind"] != "orig" or t["strategy"] != "prop":
            continue
        rows, cx, n_lines, n_parsed = check_rules(t, lims)
        rule_rows += rows
        cexs += cx
        tot.append(dict(tree=t["name"], lines=n_lines, parsed=n_parsed))
        print("규칙", t["name"], n_lines, n_parsed)
    pd.DataFrame(rule_rows).to_csv(os.path.join(OUT, "rules.csv"), index=False)
    pd.DataFrame(tot).to_csv(os.path.join(OUT, "rule_counts.csv"), index=False)
    node_rows = []
    for t in ts:
        node_rows += check_nodes(t, lims)
    pd.DataFrame(node_rows).to_csv(os.path.join(OUT, "nodes.csv"), index=False)
    rp = replay(cexs)
    rp.to_csv(os.path.join(OUT, "replay.csv"), index=False)
    with open(os.path.join(WORK, "cex.json"), "w", encoding="utf-8") as fh:
        json.dump([dict(c, rule=dict(c["rule"], conds=[list(x) for x in c["rule"]["conds"]])) for c in cexs],
                  fh, ensure_ascii=False)
    print("반례 %d, 재현 실패 %d" % (len(rp), int((~(rp.cond_true & rp.cmd_differs)).sum()) if len(rp) else 0))


def junction_attr(lims):
    """사후 기술(2026-11-19): 안 걸리는 노드 중 교차로 거리 10 m 양자화 몫. 교차로 거리만 연속 [0, 60]으로
    바꾼 정의역에서 발화 가능해지면 양자화 탓으로 봄"""
    rows = []
    for t in trees():
        _xml, root, decl = load_tree(t)
        P = z3_p()
        TH, thc = z3_theta(decl)
        enc = Enc(Z3, P, TH)
        enc.run(root)
        d = domain(P, False, lims)
        args = [x for x in d.children() if not (z3.is_or(x) and "junction_m" in str(x))]
        assert len(args) == len(d.children()) - 1
        s = z3.Solver()
        s.add(*args, z3.Or(z3.And(P["junction_m"] >= 0, P["junction_m"] <= 60), P["junction_m"] == 999), thc)
        for i, w in enumerate(enc.writers):
            rows.append(dict(tree=t["name"], kind=t["kind"], node=i, fires_cont=sat(s, w[2])[0] == z3.sat))
    return pd.DataFrame(rows)


def out_of_box(x):
    """불이행 규칙 중 리터럴 값이 KNOBS 상자 밖인 수. (그 수, 불이행 수)"""
    names = dict((t["name"], t) for t in trees() if t["kind"] == "orig")
    nv = x[x.status == "불이행"]
    k = 0
    for _i, row in nv.iterrows():
        _xml, _root, decl = load_tree(names[row.tree])
        rule, _w = parse_rule(row.rule, decl)
        kind, v = rule["val"]
        if kind == "lit" and rule["knob"] in BN.KNOBS:
            _b, lo, hi = BN.KNOBS[rule["knob"]]
            k += not (lo <= v <= hi)
    return k, len(nv)


def pct(a, b):
    return "%d/%d (%.0f%%)" % (a, b, 100.0 * a / b) if b else "0/0"


def report():
    from scipy.stats import mannwhitneyu
    v = pd.read_csv(os.path.join(OUT, "validation.csv"))
    r = pd.read_csv(os.path.join(OUT, "rules.csv"))
    rc = pd.read_csv(os.path.join(OUT, "rule_counts.csv"))
    n = pd.read_csv(os.path.join(OUT, "nodes.csv"))
    rp = pd.read_csv(os.path.join(OUT, "replay.csv"))
    ja = junction_attr(map_limits())
    n = n[[c for c in n.columns if not c.startswith("fires_cont")]]     # 다시 돌려도 같게
    n = n.merge(ja, on=["tree", "kind", "node"], validate="one_to_one")
    n.to_csv(os.path.join(OUT, "nodes.csv"), index=False)
    L = ["# 보조 실험 6. 트리 수준 전수 검사", "",
         "설계: [`docs/design.md`](../../docs/design.md#보조-실험-6-트리-수준-전수-검사). 사후 실험, 탐색, 전부 기술. "
         "생성: `python diag/bt_verify.py` 다음 `python diag/bt_verify.py report`.", "",
         "## 검사기 검증", "",
         "트리 %d개(제안 20, 제로샷 20, 재배치 20), 트리당 무작위 (p, θ) %d개. 모형 대 실행기 불일치: py3.11 %d, "
         "py3.7(수집 환경, py_trees 2.1.6) %d. z3 백엔드 대입 %d개 중 불일치 %d. 반례 %d개를 py3.11 실행기에 넣어 "
         "조건 참, 명령이 규칙 값과 다름을 재현 못 한 것 %d." % (
             len(v), v.n.iloc[0], v.mismatch_py311.sum(), v.mismatch_py37.sum(), v.n_z3.sum(), v.mismatch_z3.sum(),
             len(rp), int((~(rp.cond_true & rp.cmd_differs)).sum())), ""]
    lines, parsed = int(rc.lines.sum()), int(rc.parsed.sum())
    u = r[r.status == "번역 불가"]
    L += ["## 규칙 이행(제안 트리 20개)", "",
          "enumeration 규칙 %d줄 중 번역 %s. 번역 불가 사유: %s." % (
              lines, pct(parsed, lines), ", ".join("%s %d" % kv for kv in u.why.value_counts().items())), ""]
    L += ["| 정의역 | 공허 | 불이행 | 일부 이행 | 항상 이행 | 항상 이행(기준값 규칙 뺌) |", "|---|---|---|---|---|---|"]
    for d in ["느슨", "조임"]:
        x = r[r.domain == d]
        c = x.status.value_counts()
        nb = x[~x.base_rule.astype(bool)]
        L.append("| %s | %d | %d | %d | %d | %s |" % (d, c.get("공허", 0), c.get("불이행", 0), c.get("일부 이행", 0),
                                                   c.get("항상 이행", 0),
                                                   pct(int((nb.status == "항상 이행").sum()), len(nb))))
    x = r[r.domain == "느슨"]
    vio = x[x.status.isin(["일부 이행", "불이행"])]
    pc = vio.plan_conflict.astype(bool)
    sv = vio.synth_violation.astype(bool)
    sa = x[x.status == "일부 이행"].some_theta_always
    L += ["", "느슨 정의역 위반 규칙 %d개(일부 + 불이행):" % len(vio), "",
          "- 계획 충돌 있음 %s. 충돌 규칙이 하나도 안 걸린 입력에서도 위반(합성 위반) %s. 충돌 규칙이 아예 없는데 위반 %d" % (
              pct(int(pc.sum()), len(vio)), pct(int(sv.sum()), len(vio)), int((sv & ~pc).sum())),
          "- 일부 이행 중 어떤 θ에선 모든 p에서 이행(∃θ∀p) %s" % pct(int((sa.astype(str) == "True").sum()), len(sa)),
          "- 공허 %d개는 전부 `JunctionAhead meters=\"10\"`. 실행기 교차로 탐색이 10 m부터라 10 m 미만은 안 나옴" % int(
              (x.status == "공허").sum()),
          "- 합성 위반은 **상한**. 번역 불가 규칙 %d개가 충돌 규칙일 수 있는데 안 보임" % (lines - parsed),
          "- 불이행 중 계획 값이 상자(KNOBS) 밖이라 원래 못 쓰는 것 %d/%d(사후 기술)" % out_of_box(x), ""]
    L += ["노브별(느슨):", "", "| 노브 | 공허 | 불이행 | 일부 이행 | 항상 이행 |", "|---|---|---|---|---|"]
    kt = x.groupby("knob").status.value_counts().unstack(fill_value=0)
    for k, row in kt.iterrows():
        L.append("| %s | %d | %d | %d | %d |" % (k, row.get("공허", 0), row.get("불이행", 0), row.get("일부 이행", 0),
                                               row.get("항상 이행", 0)))
    L += ["", "## 노드 도달", "",
          "| 판 | 전략 | 노드 | 발화 가능 | 유효(끝 명령으로 남음) | 정적 도달 불가 | 안 걸리는데 정적 검사가 놓침 | 그중 10 m 양자화 몫 |",
          "|---|---|---|---|---|---|---|---|"]
    for (k, st), g in n.groupby(["kind", "strategy"]):
        miss = g[~g.fires & ~g.static_dead]
        L.append("| %s | %s | %d | %d | %d | %d | %d | %d |" % (
            k, st, len(g), g.fires.sum(), g.effective.sum(), g.static_dead.sum(), len(miss), miss.fires_cont.sum()))
    o = n[n.kind == "orig"]
    fp = int((o.static_dead & o.fires).sum())
    dead = int((~o.fires).sum())
    L += ["", "- 정적 검사(`unreachable`) 오탐 %d. 원본 40개에서 실제 안 걸리는 노드 %d개 중 정적 검사가 잡은 것 %s" % (
        fp, dead, pct(int((o.static_dead & ~o.fires).sum()), dead)),
          "- 10 m 양자화 몫은 결과를 본 뒤 더한 기술(교차로 거리만 연속으로 바꾼 정의역에서 발화 가능해지는 노드)", ""]
    per = o.groupby(["strategy", "tree"])[["fires", "effective", "static_dead"]].apply(
        lambda g: pd.Series(dict(dead=(~g.fires).mean(), ineff=(~g.effective).mean()))).reset_index()
    L += ["트리 단위 비율, 제안 대 제로샷(각 20, Mann-Whitney 양측, 기술):", ""]
    for col, nm in [("dead", "안 걸리는 노드"), ("ineff", "끝 명령으로 안 남는 노드")]:
        a, b = per[per.strategy == "prop"][col], per[per.strategy == "zs"][col]
        L.append("- %s: 제안 %.3f, 제로샷 %.3f, p = %.3g" % (nm, a.mean(), b.mean(), mannwhitneyu(a, b).pvalue))
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        main()
