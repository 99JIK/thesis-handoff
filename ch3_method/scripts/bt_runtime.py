"""트리를 차량에 얹어 매 틱 돌리는 실행기. py3.7(수집)과 py3.11(검증) 둘 다에서 import.

출처: C:\\Github\\sil-agent-repertoire scripts/collect.py 의 bt_runtime 부분 (커밋 a604b91).
바꾼 것: 블랙보드, 수동 제어 삭제(카탈로그 8/20에 없음). 행동 값은 KNOBS 상자로
클램프하고 횟수를 셈. 검증을 통과한 트리면 클램프 0회여야 함. 오프셋 합성은 한 번 지웠다가
offset 조건으로 다시 넣음(기준값 대비 차이로, Act.update 주석).

carla는 Perception만 씀. 없어도 build, tick, selftest, exec_check는 돔.
"""
import math
import os
import sys
import xml.etree.ElementTree as ET

import py_trees
from py_trees.common import Status

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt_nodes import (ACTIONS, CONDITIONS, KNOBS, PARAM_REF, PARAMS_TAG,  # noqa: E402
                      SENSE, clamp, parse_params, validate)

# 조건 노드 -> (지각값, 임계) -> 참거짓. bt_nodes.CONDITIONS, STRONGER와 맞아야 함.
# dv = 자차속도 - 선행차속도. 양수가 접근
EVAL = {
    "IsStopped":        lambda p, v: p["stopped"],
    "SpeedBelow":       lambda p, v: p["speed_kmh"] < v,
    "SpeedAbove":       lambda p, v: p["speed_kmh"] > v,
    "LeaderPresent":    lambda p, v: p["lead"],
    "LeaderStopped":    lambda p, v: p["lead"] and p["lead_stopped"],
    "GapBelow":         lambda p, v: p["lead"] and p["gap_m"] < v,
    "GapAbove":         lambda p, v: p["lead"] and p["gap_m"] > v,
    "ClosingFaster":    lambda p, v: p["lead"] and p["dv"] > v,
    "OpeningFaster":    lambda p, v: p["lead"] and -p["dv"] > v,
    "InJunction":       lambda p, v: p["in_junction"],
    "JunctionAhead":    lambda p, v: p["junction_m"] < v,
    "TrafficLightRed":  lambda p, v: p["tl"] == "Red",
    "TrafficLightGreen": lambda p, v: p["tl"] == "Green",
    "SpeedLimitBelow":  lambda p, v: p["limit_kmh"] < v,
    "SpeedLimitAbove":  lambda p, v: p["limit_kmh"] > v,
    "IsRightmostLane":  lambda p, v: p["rightmost"],
    "IsLeftmostLane":   lambda p, v: p["leftmost"],
    "LaneCountAbove":   lambda p, v: p["lane_count"] > v,
    "TimeInLaneAbove":  lambda p, v: p["lane_time_s"] > v,
    "CurvatureAbove":   lambda p, v: p["curvature"] > v,
}
assert set(EVAL) == set(c[0] for c in CONDITIONS), "EVAL과 카탈로그가 어긋남"
ACT = dict((a[0], (a[1], a[2])) for a in ACTIONS)
COND_ARG = dict((c[0], c[1]) for c in CONDITIONS)

# 지각 기본값. CARLA에서 못 채운 항목이 있어도 트리가 안 죽게
BLANK = {"speed_kmh": 0.0, "stopped": True, "lead": False, "lead_stopped": False,
         "gap_m": 999.0, "dv": 0.0, "in_junction": False, "junction_m": 999.0,
         "tl": "", "limit_kmh": 30.0, "rightmost": True, "leftmost": True,
         "lane_count": 1, "lane_time_s": 0.0, "curvature": 0.0}


def baseline_want():
    """기준 조건의 값. 트리 조건에서 발화 없을 때 돌아가는 값이기도 함"""
    w = dict((k, v[0]) for k, v in KNOBS.items())
    w["lane_change"] = True
    return w


class Ctx(object):
    """차량 하나의 틱 상태. 트리가 p를 읽고 want에 씀"""

    def __init__(self, base=None, offset=False, fixed=(), box=None):
        self.p = dict(BLANK)
        self.want = {}
        self.applied = {}       # TM에 실제로 넣은 값. 바뀔 때만 부르려고 캐시
        self.fired = []
        self.clamped = 0
        # offset 조건: base가 그 차 추첨값이고 매 틱 거기로 돌아감. 트리 값은 기준값 대비 차이로
        # 그 위에 얹고 KNOBS 상자로 자름(범위 일치 유지). clipped가 그 자른 횟수, 0 아니어도 정상
        self.offset = offset
        self.clipped = 0
        self.base = dict(base) if base else baseline_want()
        # offkr 조건: 트리가 써도 매 틱 base로 되돌리는 노브. keep_right 동적 변경이 추돌 초과 원인이라
        # (diag nokr, offkr) 넣음. 트리는 그대로 돌고 덮어쓰기만 함, clipped엔 고정 노브 몫도 섞임
        self.fixed = tuple(fixed)
        # 넓힌 상자 조건(knobw2 짝): offset 합성 결과를 KNOBS 대신 이 상자로 자름.
        # 트리 값 자체는 KNOBS 클램프 그대로라 트리가 더하는 차이는 x1과 같음. 추첨만 넓어짐
        self.box = box


class Cond(py_trees.behaviour.Behaviour):
    def __init__(self, nid, val, ctx):
        super(Cond, self).__init__(name=nid)
        self.nid, self.val, self.ctx = nid, val, ctx

    def update(self):
        return Status.SUCCESS if EVAL[self.nid](self.ctx.p, self.val) else Status.FAILURE


class Act(py_trees.behaviour.Behaviour):
    _n = [0]

    def __init__(self, nid, knob, val, ctx, bid=None):
        super(Act, self).__init__(name=nid)
        self.nid, self.knob, self.val, self.ctx = nid, knob, val, ctx
        # 실험 3 컴파일 트리는 Action name에 행동 id를 씀. 발화 기록용, 의미엔 영향 없음
        self.bid = bid
        self.idx = Act._n[0]
        Act._n[0] += 1

    def update(self):
        v = self.val
        if self.knob in KNOBS:
            c = clamp(self.knob, v)
            if c != v:
                self.ctx.clamped += 1
            v = c
            if self.ctx.offset:
                # 앞 판(sil-agent-repertoire)은 절대값을 base에 그대로 더하고 CARLA 정의역으로만
                # 잘랐음. 기준값이 0 아닌 gap, keep_right는 SetGap 1.5(좁힘)가 넓힘이 됐음
                o = self.ctx.base[self.knob] + v - KNOBS[self.knob][0]
                if self.ctx.box:
                    lo, hi = self.ctx.box[self.knob]
                    v = min(hi, max(lo, o))
                else:
                    v = clamp(self.knob, o)
                if v != o:
                    self.ctx.clipped += 1
        self.ctx.want[self.knob] = v
        self.ctx.fired.append((self.nid, self.idx, self.bid))
        return Status.SUCCESS


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _resolve(raw, pv):
    """리프 값 하나. {이름}이면 이 차량이 뽑은 값, 아니면 리터럴. 빌드 때 한 번만 풂"""
    m = PARAM_REF.match(raw or "")
    if not m:
        return _num(raw)
    name = m.group(1)
    if name not in pv:
        raise ValueError("파라미터 {%s} 값이 안 주어짐" % name)
    return float(pv[name])


def build(el, ctx, pv=None):
    if pv is None:
        pv = default_params(tree_params(el))
    tag = el.tag
    kids = [c for c in el if isinstance(c.tag, str)]
    if tag in ("root", "BehaviorTree"):
        for c in kids:
            if c.tag in ("TreeNodesModel", PARAMS_TAG):
                continue
            return build(c, ctx, pv)
        raise ValueError("빈 %s" % tag)
    if tag == "Condition":
        nid = el.get("ID")
        arg = COND_ARG[nid]
        return Cond(nid, _resolve(el.get(arg), pv) if arg else None, ctx)
    if tag == "Action":
        nid = el.get("ID")
        knob, arg = ACT[nid]
        raw = el.get(arg)
        bid = el.get("name")
        if knob is None:
            return Act(nid, "lane_change", str(raw).strip().lower() == "true", ctx, bid)
        return Act(nid, knob, _resolve(raw, pv), ctx, bid)
    ch = [build(c, ctx, pv) for c in kids]
    if tag == "Sequence":
        # 리프가 전부 즉시 반환이라 RUNNING이 안 생김. 매 틱 처음부터 재평가
        n = py_trees.composites.Sequence(name="Seq", memory=False)
    elif tag in ("Fallback", "Selector"):
        n = py_trees.composites.Selector(name="Sel", memory=False)
    elif tag == "Parallel":
        n = py_trees.composites.Parallel(
            name="Par", policy=py_trees.common.ParallelPolicy.SuccessOnAll())
    elif tag == "Inverter":
        return py_trees.decorators.Inverter(child=ch[0], name="Inv")
    elif tag == "ForceSuccess":
        return py_trees.decorators.FailureIsSuccess(child=ch[0], name="FS")
    elif tag == "ForceFailure":
        return py_trees.decorators.SuccessIsFailure(child=ch[0], name="FF")
    elif tag in ("Repeat", "RetryUntilSuccessful"):
        # 한 틱 안 반복은 즉시 반환 리프에 의미 없음. spec/runtime.py가 LLM에 그렇게 알림
        return ch[0]
    else:
        raise ValueError("모르는 태그 <%s>" % tag)
    for c in ch:
        n.add_child(c)
    return n


def tree_params(xml):
    root = ET.fromstring(xml) if isinstance(xml, str) else xml
    errs = []
    decl = parse_params(root, lambda _k, m: errs.append(m))
    if errs:
        raise ValueError("파라미터 선언이 잘못됨:\n  " + "\n  ".join(errs[:5]))
    return decl


def draw_params(decl, rng):
    """차량 하나의 파라미터. 스폰 때 한 번 뽑고 안 바뀜. 추첨 조건과 같은 균등.
    선언 순서대로 뽑으므로 <Params> 순서가 바뀌면 같은 시드에서 다른 값"""
    return dict((n, float(rng.uniform(lo, hi))) for n, (lo, hi, _d) in decl.items())


def default_params(decl):
    return dict((n, float(d)) for n, (_lo, _hi, d) in decl.items())


def load(path, ctx, pv=None):
    xml = open(path, encoding="utf-8").read()
    errs, _ = validate(xml)
    if errs:
        raise ValueError("실행 못 하는 트리:\n  " + "\n  ".join(errs[:5]))
    return build(ET.fromstring(xml), ctx, pv)


def tick(root, ctx):
    """한 틱. want를 기준값으로 초기화하고 트리를 돌림.
    안 정한 파라미터는 기준값으로 돌아감(spec/runtime.py 리셋 규약과 같아야 함)"""
    ctx.want = dict(ctx.base)
    ctx.fired = []
    root.tick_once()
    for k in ctx.fixed:
        ctx.want[k] = ctx.base[k]
    return ctx.want


def apply_to_tm(tm, veh, ctx):
    """값이 바뀐 파라미터만 TM에 넣음. 넣은 개수 반환"""
    n = 0
    for k, v in ctx.want.items():
        if ctx.applied.get(k) == v:
            continue
        if k == "speed_diff":
            tm.vehicle_percentage_speed_difference(veh, v)
        elif k == "gap_m":
            tm.distance_to_leading_vehicle(veh, v)
        elif k == "keep_right":
            tm.set_percentage_keep_right_rule(veh, v)
        elif k == "ign_lights":
            tm.ignore_lights_percentage(veh, v)
        elif k == "ign_signs":
            tm.ignore_signs_percentage(veh, v)
        elif k == "ign_vehicles":
            tm.ignore_vehicles_percentage(veh, v)
        elif k == "ign_walkers":
            tm.ignore_walkers_percentage(veh, v)
        elif k == "lane_change":
            tm.auto_lane_change(veh, bool(v))
        else:
            continue
        ctx.applied[k] = v
        n += 1
    return n


def exec_check(xml):
    """형식 검증 4단계. 실제 실행기로 빌드하고 기본 지각값으로 1틱. (통과, 오류 문자열)"""
    try:
        ctx = Ctx()
        root = build(ET.fromstring(xml), ctx)
        tick(root, ctx)
    except Exception as e:  # noqa: BLE001
        return False, "Executor failed to build or tick the tree: %s: %s" % (
            type(e).__name__, e)
    return True, ""


class Perception(object):
    """차량들의 지각값을 매 틱 채움. CARLA 필요.

    매 틱: 속도, 선행차, 간격, dv, 신호, 교차로 안 여부.
    SLOW 틱: 교차로까지 거리, 차로 수와 위치, 곡률, 제한속도(waypoint 순회가 비쌈).
    좌표는 수집기와 같이 y를 뒤집어 씀
    """
    LEAD_MAX = SENSE["lead_max_m"]
    LAT = SENSE["lead_lat_m"]
    V_STOP_KMH = SENSE["v_stop_kmh"]
    JUNC_SCAN = SENSE["junction_scan_m"]
    SLOW = int(round(SENSE["slow_period_s"] / 0.1))

    def __init__(self, world, dt, carla_mod):
        self.m = world.get_map()
        self.dt = dt
        self.carla = carla_mod
        self.step = 0
        self.lane_of = {}
        self.lane_since = {}
        self.slow = {}

    def _slow_of(self, veh, wp):
        d = 999.0
        w = wp
        for i in range(int(self.JUNC_SCAN // 10)):
            nxt = w.next(10.0)
            if not nxt:
                break
            w = nxt[0]
            if w.is_junction:
                d = (i + 1) * 10.0
                break
        left = right = 0
        driving = self.carla.LaneType.Driving
        w = wp
        for _ in range(4):
            w = w.get_left_lane()
            if w is None or w.lane_type != driving:
                break
            left += 1
        w = wp
        for _ in range(4):
            w = w.get_right_lane()
            if w is None or w.lane_type != driving:
                break
            right += 1
        nxt = wp.next(10.0)
        curv = 0.0
        if nxt:
            dy = abs(nxt[0].transform.rotation.yaw - wp.transform.rotation.yaw)
            dy = min(dy, 360.0 - dy)
            curv = math.radians(dy) / 10.0
        return {"junction_m": d, "lane_count": left + right + 1,
                "leftmost": left == 0, "rightmost": right == 0,
                "curvature": curv, "limit_kmh": float(veh.get_speed_limit())}

    def update(self, vehicles, ctxs):
        import numpy as np
        self.step += 1
        alive = [v for v in vehicles if v.is_alive]
        if not alive:
            return
        n = len(alive)
        arr = np.empty((n, 5))
        wps, tls = [], []
        for i, v in enumerate(alive):
            tr = v.get_transform()
            vel = v.get_velocity()
            arr[i] = (tr.location.x, -tr.location.y, tr.rotation.yaw,
                      math.sqrt(vel.x ** 2 + vel.y ** 2 + vel.z ** 2),
                      v.bounding_box.extent.x * 2)
            wps.append(self.m.get_waypoint(tr.location, project_to_road=True))
            t = ""
            if v.is_at_traffic_light() and v.get_traffic_light() is not None:
                t = str(v.get_traffic_light_state())
            tls.append(t)
        x, y, yaw, spd, ln = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
        rad = np.radians(yaw)
        fx, fy = np.cos(rad), -np.sin(rad)
        for i, v in enumerate(alive):
            ctx = ctxs.get(v.id)
            wp = wps[i]
            if ctx is None or wp is None:
                continue
            dx, dy = x - x[i], y - y[i]
            s = dx * fx[i] + dy * fy[i]
            lat = np.abs(-dx * fy[i] + dy * fx[i])
            ok = (s > 0.5) & (s <= self.LEAD_MAX) & (lat < self.LAT)
            p = ctx.p
            if ok.any():
                j = int(np.where(ok)[0][int(np.argmin(s[ok]))])
                p["lead"] = True
                p["gap_m"] = float(s[j] - 0.5 * (ln[i] + ln[j]))
                p["dv"] = float(spd[i] - spd[j])
                p["lead_stopped"] = bool(spd[j] * 3.6 < self.V_STOP_KMH)
            else:
                p["lead"], p["gap_m"], p["dv"] = False, 999.0, 0.0
                p["lead_stopped"] = False
            p["speed_kmh"] = float(spd[i] * 3.6)
            p["stopped"] = p["speed_kmh"] < self.V_STOP_KMH
            p["in_junction"] = bool(wp.is_junction)
            p["tl"] = tls[i]
            lane = (wp.road_id, wp.lane_id)
            if self.lane_of.get(v.id) != lane:
                self.lane_of[v.id] = lane
                self.lane_since[v.id] = self.step
            p["lane_time_s"] = (self.step - self.lane_since[v.id]) * self.dt
            if self.step % self.SLOW == 1 or v.id not in self.slow:
                self.slow[v.id] = self._slow_of(v, wp)
            p.update(self.slow[v.id])


SCENES = [
    ("정지, 앞차도 정지, 간격 2 m",
     {"stopped": True, "speed_kmh": 0, "lead": True, "lead_stopped": True,
      "gap_m": 2.0, "dv": 0.0}),
    ("정지, 앞차 출발",
     {"stopped": True, "speed_kmh": 0, "lead": True, "lead_stopped": False,
      "gap_m": 4.0, "dv": -3.0}),
    ("도심 주행, 앞차 20 m",
     {"stopped": False, "speed_kmh": 30, "lead": True, "gap_m": 20.0, "dv": 1.0,
      "limit_kmh": 30, "lane_count": 2}),
    ("도심 접근 중, 앞차 가까움",
     {"stopped": False, "speed_kmh": 25, "lead": True, "gap_m": 8.0, "dv": 5.0,
      "limit_kmh": 30, "junction_m": 40}),
    ("적색 신호 접근",
     {"stopped": False, "speed_kmh": 20, "tl": "Red", "junction_m": 15, "limit_kmh": 30}),
    ("교차로 안, 청색",
     {"stopped": False, "speed_kmh": 15, "tl": "Green", "in_junction": True,
      "limit_kmh": 30}),
    ("고속 자유주행",
     {"stopped": False, "speed_kmh": 85, "lead": False, "limit_kmh": 90,
      "lane_count": 3, "rightmost": False, "leftmost": False, "lane_time_s": 40}),
    ("고속 추종, 접근 중",
     {"stopped": False, "speed_kmh": 80, "lead": True, "gap_m": 25.0, "dv": 6.0,
      "limit_kmh": 90, "lane_count": 3, "rightmost": False, "leftmost": False}),
    ("곡선 구간",
     {"stopped": False, "speed_kmh": 40, "limit_kmh": 60, "curvature": 0.03}),
]


def selftest(path):
    """CARLA 없이 상황 아홉에 넣어 트리가 무엇을 정하는지 봄. 진단이지 평가 지표 아님.
    여기 결과 보고 프롬프트 고치면 순환"""
    ctx = Ctx()
    root = load(path, ctx)
    base = baseline_want()
    print("== %s" % os.path.basename(path))
    for name, over in SCENES:
        ctx.p = dict(BLANK)
        ctx.p.update(over)
        want = tick(root, ctx)
        diff = dict((k, v) for k, v in want.items() if v != base[k])
        print("  %-24s %s" % (name, diff if diff else "기준값 그대로"))
    print("  클램프 %d회" % ctx.clamped)


if __name__ == "__main__":
    import glob
    for pat in sys.argv[1:]:
        for f in sorted(glob.glob(pat)) or [pat]:
            selftest(f)
