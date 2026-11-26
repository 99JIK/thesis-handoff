"""실험 3 행동 스키마, 컴파일러, 루프 안 검사. py3.11 + z3. 설계는 docs/design_exp3.md

LLM은 행동 목록(JSON)만 쓰고 트리 XML은 여기서 만듦. 실험 2에서 값 부호(보조 실험 8)와 배치(보조 실험 6)를
LLM이 틀려서, 뜻은 필드로 받고 숫자와 배치는 프로그램이 정함.
수집기(py3.7)는 이 파일을 안 씀. 컴파일된 XML만 bt_runtime으로 읽음.

루프 안 검사는 카탈로그와 실행기 사양만 봄(느슨 정의역 = 지각 치역 상위집합, 보조 실험 6과 같음).
지도별 사실, 수집 결과, 평가 지표는 안 씀. 누설 경계.
"""
import copy
import json
import os
import random
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "diag"))
sys.path.insert(0, os.path.join(ROOT, "spec"))
import bt_nodes as N  # noqa: E402
import bt_runtime as RT  # noqa: E402

CATALOG = "v2"
N.set_catalog(CATALOG)
TREE_ID = "background_vehicle"

# set -> (노브, 행동 노드, {effect: 부호}). 부호 +1이면 기준값 + amount.
# speed: SetSpeedDiff는 양수가 감속이라 slower가 +. 실험 2 역전 36%가 이 부호에서 났음
# keep_right 뺌(실험 2 offset 추돌 초과), ign은 more만(offset이 기준값 0 아래를 0으로 자름). 둘 다 실험 2 보고 정함
SETS = {
    "speed": ("speed_diff", "SetSpeedDiff", {"slower": 1, "faster": -1}),
    "gap": ("gap_m", "SetGap", {"wider": 1, "narrower": -1}),
    "lane_change": (None, "EnableLaneChange", {"allow": True, "forbid": False}),
    "ign_lights": ("ign_lights", "SetIgnoreLights", {"more": 1}),
    "ign_signs": ("ign_signs", "SetIgnoreSigns", {"more": 1}),
    "ign_vehicles": ("ign_vehicles", "SetIgnoreVehicles", {"more": 1}),
    "ign_walkers": ("ign_walkers", "SetIgnoreWalkers", {"more": 1}),
}
FIELDS = ("id", "dimension", "when", "set", "effect", "amount", "priority", "intent")


def amount_max(s):
    """크기 상한 = KNOBS 기준값에서 그 방향 벽까지. 표 하나에서 나와야 함(CLAUDE.md)"""
    knob, _a, eff = SETS[s]
    if knob is None:
        return None
    b, lo, hi = N.KNOBS[knob]
    return min((hi - b) if sg > 0 else (b - lo) for sg in eff.values())


def cond_arg(cid):
    return N.COND_BY_ID[cid][1]


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_range(x):
    return isinstance(x, list) and len(x) == 2 and all(_is_num(v) for v in x)


# ---- 정규화(초안만) ----

def normalize(raw):
    """초안 정리. (행동 목록, 메모). 표현 불가 행동은 빼고 기록, 고칠 수 없는 것이라서.
    set, effect, 조건 ID가 카탈로그 밖이면 수정 루프에서 바꾸는 게 금지라 영원히 못 통과함.
    id 없음, 중복은 프로그램이 이름만 붙임(뜻 안 바뀜)"""
    notes = []
    if isinstance(raw, dict):
        for k in ("behaviours", "behaviors"):
            if isinstance(raw.get(k), list):
                raw = raw[k]
                break
    if not isinstance(raw, list):
        return None, ["top level is not a list"]
    out, seen = [], set()
    for i, b in enumerate(raw):
        if not isinstance(b, dict):
            notes.append("drop #%d: not an object" % i)
            continue
        b = copy.deepcopy(b)
        bid = b.get("id")
        if not isinstance(bid, str) or not bid.strip():
            bid = "b%02d" % i
            notes.append("rename #%d: missing id -> %s" % (i, bid))
        bid = bid.strip()
        if bid in seen:
            k = 2
            while "%s_%d" % (bid, k) in seen:
                k += 1
            notes.append("rename #%d: duplicate id %s -> %s_%d" % (i, bid, bid, k))
            bid = "%s_%d" % (bid, k)
        b["id"] = bid
        why = None
        s = b.get("set")
        if s not in SETS:
            why = "set %r not in schema" % (s,)
        elif b.get("effect") not in SETS[s][2]:
            why = "effect %r not allowed for %s" % (b.get("effect"), s)
        when = b.get("when", [])
        if why is None and not isinstance(when, list):
            why = "when is not a list"
        if why is None:
            for w in when:
                if not isinstance(w, dict) or w.get("cond") not in N.COND_BY_ID:
                    why = "condition %r not in catalogue" % (w.get("cond") if isinstance(w, dict) else w,)
                    break
        if why:
            notes.append("drop %s: %s" % (bid, why))
            continue
        b["when"] = when
        seen.add(bid)
        out.append(b)
    return out, notes


# ---- 구조 검사 ----

def structure_errors(B):
    """LLM에 그대로 나가는 문장. 영어"""
    errs = []
    if not isinstance(B, list) or not B:
        return ["The answer must be a non-empty JSON list of behaviours."]
    ids = [b.get("id") if isinstance(b, dict) else None for b in B]
    for i, b in enumerate(B):
        if not isinstance(b, dict):
            errs.append("Item %d is not a JSON object." % i)
            continue
        tag = "Behaviour '%s'" % b.get("id")
        if ids.count(b.get("id")) > 1:
            errs.append("%s: id is used more than once." % tag)
        for k in b:
            if k not in FIELDS:
                errs.append("%s: unknown field \"%s\". Allowed fields: %s." % (tag, k, ", ".join(FIELDS)))
        for k in ("dimension", "intent"):
            if not isinstance(b.get(k), str) or not b.get(k).strip():
                errs.append("%s: \"%s\" must be a non-empty string." % (tag, k))
        p = b.get("priority")
        if not (isinstance(p, int) and not isinstance(p, bool)):
            errs.append("%s: \"priority\" must be an integer." % tag)
        s = b.get("set")
        if s not in SETS:
            errs.append("%s: \"set\" must be one of %s." % (tag, ", ".join(SETS)))
            continue
        if b.get("effect") not in SETS[s][2]:
            errs.append("%s: \"effect\" for %s must be one of %s." % (tag, s, ", ".join(SETS[s][2])))
        mx = amount_max(s)
        a = b.get("amount")
        if mx is None:
            if a is not None:
                errs.append("%s: %s takes no \"amount\". Remove it." % (tag, s))
        elif _is_num(a):
            # 0 허용: 그 상황에서 평상값 유지(뒤 행동을 막는 뜻). 스모크 생성에서 "신호 준수" 행동이 0을 썼고,
            # 0을 막으니 수정 루프가 1로 올려 무시 확률을 높이는 반대 뜻이 됐음(2026-11-19)
            if not 0 <= a <= mx:
                errs.append("%s: \"amount\" %g is outside [0, %g] for %s." % (tag, a, mx, s))
        elif _is_range(a):
            if not (0 <= a[0] < a[1] <= mx):
                errs.append("%s: \"amount\" range [%g, %g] must satisfy 0 <= low < high <= %g for %s."
                            % (tag, a[0], a[1], mx, s))
        else:
            errs.append("%s: \"amount\" must be a number or a [low, high] pair." % tag)
        when = b.get("when")
        if not isinstance(when, list):
            errs.append("%s: \"when\" must be a list (empty means always)." % tag)
            continue
        for j, w in enumerate(when):
            if not isinstance(w, dict) or w.get("cond") not in N.COND_BY_ID:
                errs.append("%s: when[%d] must name a catalogue condition in \"cond\"." % (tag, j))
                continue
            cid = w["cond"]
            arg = cond_arg(cid)
            allowed = {"cond", "not"} | ({arg} if arg else set())
            for k in w:
                if k not in allowed:
                    errs.append("%s: when[%d] %s has unknown key \"%s\". Allowed: %s."
                                % (tag, j, cid, k, ", ".join(sorted(allowed))))
            if "not" in w and not isinstance(w["not"], bool):
                errs.append("%s: when[%d] \"not\" must be true or false." % (tag, j))
            if not arg:
                continue
            v = w.get(arg)
            lo, hi = N.cond_range(cid)
            if _is_num(v):
                if not lo <= v <= hi:
                    errs.append("%s: when[%d] %s %s=%g is outside [%g, %g]." % (tag, j, cid, arg, v, lo, hi))
            elif _is_range(v):
                if not (lo <= v[0] < v[1] <= hi):
                    errs.append("%s: when[%d] %s %s range [%g, %g] must satisfy %g <= low < high <= %g."
                                % (tag, j, cid, arg, v[0], v[1], lo, hi))
            else:
                errs.append("%s: when[%d] %s needs \"%s\" as a number or a [low, high] pair." % (tag, j, cid, arg))
    return errs


# ---- 금지 수정 ----

def signature(b):
    when = b.get("when") if isinstance(b.get("when"), list) else []
    conds = tuple(sorted((w.get("cond"), bool(w.get("not", False))) if isinstance(w, dict) else (str(w), False)
                         for w in when))
    return (b.get("set"), b.get("effect"), conds)


def forbidden(draft, cand):
    """초안 대비 금지 수정. 행동 추가, 삭제(id 집합), set, effect, 조건 ID와 not 목록.
    허용: 형식, 임계, amount, priority. intent와 dimension은 거부 대신 초안 값으로 되돌림(restore_text)"""
    if not isinstance(cand, list):
        return ["The answer is not a JSON list."]
    d = dict((b["id"], b) for b in draft)
    c = dict((b.get("id"), b) for b in cand if isinstance(b, dict))
    out = []
    add = sorted(str(x) for x in set(c) - set(d))
    rem = sorted(set(d) - set(c))
    if add:
        out.append("Behaviours were added (%s). Adding behaviours is not allowed." % ", ".join(add))
    if rem:
        out.append("Behaviours were removed (%s). Removing behaviours is not allowed." % ", ".join(rem))
    for k in sorted(set(d) & set(c), key=str):
        if signature(d[k]) != signature(c[k]):
            out.append("Behaviour '%s': its set, effect or list of conditions changed. Only thresholds, "
                       "amount and priority may change." % k)
    return out


def restore_text(draft, cand):
    """intent, dimension을 초안 값으로. 평가 E1이 intent를 쓰는데 수정 회차에 바뀌면 안 돼서. 바꾼 개수 반환"""
    d = dict((b["id"], b) for b in draft)
    n = 0
    for b in cand:
        o = d.get(b.get("id"))
        if not o:
            continue
        for k in ("intent", "dimension"):
            if b.get(k) != o.get(k):
                b[k] = o.get(k)
                n += 1
    return n


# ---- SMT ----

class Smt(object):
    """행동마다 가드 G[k]와 임계 제약 C[k]. 임계 변수는 행동마다 따로(같은 차라도 Param이 따로 뽑힘)"""

    def __init__(self, B, timeout_ms=20000):
        import z3
        import bt_verify as V
        self.z3, self.V = z3, V
        self.B = B
        self.P = V.z3_p()
        self.D = V.domain(self.P, False, None)
        self.timeout = timeout_ms
        self.G, self.C = [], []
        for k, b in enumerate(B):
            cons, terms = [], []
            for j, w in enumerate(b["when"]):
                arg = cond_arg(w["cond"])
                v = None
                if arg:
                    x = w[arg]
                    if _is_range(x):
                        v = z3.Real("th_%d_%d" % (k, j))
                        cons += [v >= x[0], v <= x[1]]
                    else:
                        v = z3.RealVal(float(x))
                e = V.cond_expr(V.Z3, self.P, w["cond"], v)
                terms.append(z3.Not(e) if w.get("not") else e)
            self.G.append(V.Z3.And(*terms))
            self.C.append(V.Z3.And(*cons))
        self._imp = {}

    def sat(self, *fs):
        s = self.z3.Solver()
        s.set("timeout", self.timeout)
        s.add(self.D, *fs)
        r = s.check()
        if r == self.z3.unknown:
            raise RuntimeError("z3 unknown")
        return r == self.z3.sat

    def can(self, k):
        return self.sat(self.C[k], self.G[k])

    def implies(self, a, b):
        """a가 참이면 b도 참. 모든 지각, 모든 임계 뽑기에서"""
        key = (a, b)
        if key not in self._imp:
            self._imp[key] = not self.sat(self.C[a], self.C[b], self.G[a], self.z3.Not(self.G[b]))
        return self._imp[key]

    def overlap(self, a, b):
        return self.sat(self.C[a], self.C[b], self.G[a], self.G[b])


def fallback_order(B, smt):
    """{set: [행동 번호]}. priority 오름차순, 같으면 엄격히 더 구체적인 쪽 먼저, 그래도 같으면 선언 순서"""
    out = {}
    for s in SETS:
        ks = [k for k, b in enumerate(B) if b["set"] == s]
        if not ks:
            continue
        order = []
        for p in sorted(set(B[k]["priority"] for k in ks)):
            grp = [k for k in ks if B[k]["priority"] == p]
            before = dict((i, set(j for j in grp if j != i and smt.implies(j, i) and not smt.implies(i, j)))
                          for i in grp)
            done = []
            while len(done) < len(grp):
                k = min(x for x in grp if x not in done and before[x] <= set(done))
                done.append(k)
            order += done
        out[s] = order
    return out


def _value_eq(a, b):
    if a["effect"] != b["effect"]:
        return False
    return a.get("amount") == b.get("amount")


def semantic_errors(B, smt=None, order=None):
    """(오류 문장, 컴파일 순서). 구조 검사를 통과한 목록에만 부름"""
    smt = smt or Smt(B)
    order = order or fallback_order(B, smt)
    errs = []
    dead = set()
    for k, b in enumerate(B):
        if not smt.can(k):
            dead.add(k)
            errs.append("Behaviour '%s': its conditions can never all hold at the same time, so it never "
                        "takes effect." % b["id"])
    z3 = smt.z3
    for s, ks in order.items():
        for r, k in enumerate(ks):
            if k in dead:
                continue
            prev = [i for i in ks[:r] if i not in dead]
            if not prev:
                continue
            # 앞 행동 임계도 차마다 뽑히므로 존재로 봄: 어떤 차, 어떤 상황에서든 이 행동이 끝 명령이 되나
            f = [smt.C[i] for i in prev] + [smt.C[k], smt.G[k]] + [z3.Not(smt.G[i]) for i in prev]
            if not smt.sat(*f):
                hit = [B[i]["id"] for i in prev if smt.overlap(i, k)]
                errs.append("Behaviour '%s' never takes effect: whenever its conditions hold, %s for the same "
                            "setting applies first (smaller priority number, or same priority and more "
                            "specific, or listed earlier)." % (B[k]["id"], ", ".join("'%s'" % x for x in hit)))
        for x in range(len(ks)):
            for y in range(x + 1, len(ks)):
                a, c = ks[x], ks[y]
                if a in dead or c in dead or B[a]["priority"] != B[c]["priority"]:
                    continue
                if _value_eq(B[a], B[c]) or not smt.overlap(a, c):
                    continue
                ia, ic = smt.implies(a, c), smt.implies(c, a)
                if ia != ic:
                    continue        # 한쪽이 다른 쪽의 특수한 경우. 구체적인 쪽이 먼저라 의도대로
                errs.append("Behaviours '%s' and '%s' both set %s with priority %d and can hold at the same "
                            "time, but neither is a special case of the other, so only their order in the "
                            "list decides which applies. Give them different priorities."
                            % (B[a]["id"], B[c]["id"], s, B[a]["priority"]))
    return errs, order


# ---- 컴파일 ----

def _fmt(x):
    return "%.6g" % float(x)


def _pname(k, j):
    return "b%02d_%s" % (k, "amt" if j is None else "c%d" % j)


def action_value(b):
    """(리터럴 또는 None, (하한, 상한) 또는 None). 기준값 + 부호 x amount"""
    knob, _a, eff = SETS[b["set"]]
    sg = eff[b["effect"]]
    if knob is None:
        return ("true" if sg else "false"), None
    base = N.KNOBS[knob][0]
    a = b["amount"]
    if _is_range(a):
        lo, hi = base + sg * a[0], base + sg * a[1]
        return None, (min(lo, hi), max(lo, hi))
    return _fmt(base + sg * a), None


def compile_tree(B, order):
    """행동 목록 -> XML 문자열. 루트 Parallel, set마다 Fallback, 행동 하나 = Sequence(조건들, 행동).
    Action에 name=행동 id. 실행기가 발화 기록에 씀"""
    root = ET.Element("root", BTCPP_format="4")
    params = ET.SubElement(root, "Params")
    bt = ET.SubElement(root, "BehaviorTree", ID=TREE_ID)
    par = ET.SubElement(bt, "Parallel")

    def param(name, lo, hi):
        ET.SubElement(params, "Param", name=name, min=_fmt(lo), max=_fmt(hi), default=_fmt(0.5 * (lo + hi)))
        return "{%s}" % name

    for s in SETS:
        if not order.get(s):
            continue
        fb = ET.SubElement(par, "Fallback")
        for k in order[s]:
            b = B[k]
            seq = ET.SubElement(fb, "Sequence")
            for j, w in enumerate(b["when"]):
                host = ET.SubElement(seq, "Inverter") if w.get("not") else seq
                el = ET.SubElement(host, "Condition", ID=w["cond"])
                arg = cond_arg(w["cond"])
                if arg:
                    x = w[arg]
                    el.set(arg, param(_pname(k, j), x[0], x[1]) if _is_range(x) else _fmt(x))
            _n, knob, arg, _d = N.ACTION_BY_ID[SETS[b["set"]][1]]
            lit, rng = action_value(b)
            act = ET.SubElement(seq, "Action", ID=SETS[b["set"]][1])
            act.set(arg, lit if rng is None else param(_pname(k, None), rng[0], rng[1]))
            act.set("name", b["id"])
    if not len(params):
        root.remove(params)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


# ---- 직접 해석(컴파일러 검증용) ----

def interpret(B, order, p, theta, base):
    """행동 목록을 실행 규칙대로 직접 해석한 want. 실행기 offset 합성과 같은 식.
    컴파일된 트리를 실행기로 돌린 결과와 같아야 함(트리 구조, Inverter, Param, offset 검증)"""
    want = dict(base)
    fired = []
    for s, ks in order.items():
        for k in ks:
            b = B[k]
            ok = True
            for j, w in enumerate(b["when"]):
                arg = cond_arg(w["cond"])
                v = None
                if arg:
                    v = theta[_pname(k, j)] if _is_range(w[arg]) else float(w[arg])
                t = bool(RT.EVAL[w["cond"]](p, v))
                if t == bool(w.get("not")):
                    ok = False
                    break
            if not ok:
                continue
            knob = SETS[s][0]
            lit, rng = action_value(b)
            if knob is None:
                want["lane_change"] = lit == "true"
            else:
                v = theta[_pname(k, None)] if rng else float(lit)
                o = base[knob] + v - N.KNOBS[knob][0]
                want[knob] = N.clamp(knob, o)
            fired.append(b["id"])
            break
    return want, fired


def sample_base(rng):
    w = dict((k, rng.uniform(lo, hi)) for k, (_b, lo, hi) in N.KNOBS.items())
    w["lane_change"] = rng.random() < N.LANE_CHANGE_P
    return w


def equivalence(B, order, xml, n=2000, seed=0):
    """무작위 지각, 임계, 추첨값에서 직접 해석 대 실행기. 어긋난 수 반환"""
    import numpy as np
    import bt_verify as V
    rng = random.Random(seed)
    nrng = np.random.RandomState(seed)
    decl = RT.tree_params(xml)
    root_el = ET.fromstring(xml)
    bad = 0
    for _ in range(n):
        th = dict((k, rng.uniform(lo, hi)) for k, (lo, hi, _d) in decl.items())
        base = sample_base(rng)
        ctx = RT.Ctx(base, True)
        root = RT.build(root_el, ctx, th)
        s = V.sample_p(nrng)
        p = V.runtime_p(s)
        ctx.p = dict(p)
        got = dict(RT.tick(root, ctx))
        names = [f[2] for f in ctx.fired]
        exp, fired = interpret(B, order, p, th, base)
        if any(abs(float(got[k]) - float(exp[k])) > 1e-9 for k in exp) or names != fired:
            bad += 1
    return bad


def load_json(text):
    """LLM 응답에서 JSON. ```json 블록, 없으면 첫 [ 부터 마지막 ] 까지"""
    import re
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    cands = [m.group(1)] if m else []
    i, j = text.find("["), text.rfind("]")
    if i >= 0 and j > i:
        cands.append(text[i:j + 1])
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None
