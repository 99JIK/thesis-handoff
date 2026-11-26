"""행동트리 노드 카탈로그와 검증기. 표준 라이브러리만 씀(py3.7 수집 환경에서도 import).

출처: C:\\Github\\sil-agent-repertoire scripts/bt_nodes.py (커밋 a604b91). 바꾼 것:
- 카탈로그를 학위논문 생성 판(행동 8, 조건 20)으로 고정. 교착 실험용 확장 노드 삭제
- 행동 값의 범위가 CARLA 정의역이 아니라 KNOBS 상자. 추첨 조건과 트리 조건의 범위를 같게
  맞추는 게 설계(docs/design.md 범위 일치). 프롬프트, 검증기, 실행기 클램프가 전부 이 표를 봄
- 오류를 형식, 의미로 가르고 도달 불가는 경고로 따로 냄. 도달 불가는 수정 피드백에 안 들어감
- 도달 불가 후처리 reorder(2026-11-10 사후). 구체적 가드를 앞으로 옮기는 결정적 재배치

평가 지표는 프롬프트에 안 들어감. 다양성, 채점 층, 실측 수치 전부 누설 금지.
"""
import math
import re
import xml.etree.ElementTree as ET

# 차량별 TM 파라미터. (기준값, 하한, 상한).
# 기준 조건은 기준값, 추첨 조건은 하한~상한 균등, 트리 조건은 하한~상한 안의 값만 씀.
# 값 자체는 앞 레포 폭 배수 1. CARLA가 정한 범위 아님
KNOBS = {
    "speed_diff":   (0.0, -30.0, 30.0),   # %. 0이 제한속도, 음수가 초과
    "gap_m":        (2.5, 0.5, 4.5),      # m
    "keep_right":   (50.0, 0.0, 100.0),   # %
    "ign_lights":   (0.0, 0.0, 10.0),     # %
    "ign_signs":    (0.0, 0.0, 10.0),
    "ign_vehicles": (0.0, 0.0, 5.0),
    "ign_walkers":  (0.0, 0.0, 5.0),
}
LANE_CHANGE_P = 0.8      # 추첨 조건의 auto_lane_change 참 확률

# CARLA API가 뜻을 정한 구간. 넓힌 상자를 여기로 자름(앞 판 bt_nodes DOMAIN과 같은 값)
DOMAIN = {
    "speed_diff":   (-100.0, 100.0),
    "gap_m":        (0.0, 100.0),
    "keep_right":   (0.0, 100.0),
    "ign_lights":   (0.0, 100.0),
    "ign_signs":    (0.0, 100.0),
    "ign_vehicles": (0.0, 100.0),
    "ign_walkers":  (0.0, 100.0),
}


def widen(width):
    """KNOBS 상자를 기준값 축으로 width배 한 {knob: (lo, hi)}. 앞 판 sample_knobs scale과 같은 정의.
    DOMAIN으로 잘라서 배수대로 안 넓어지는 노브 있음: keep_right는 x1이 이미 벽,
    ign_*와 gap_m은 아래쪽이 0에서 막힘(x2면 gap 0~6.5)"""
    out = {}
    for k, (b, lo, hi) in KNOBS.items():
        dlo, dhi = DOMAIN[k]
        out[k] = (max(dlo, b + (lo - b) * width), min(dhi, b + (hi - b) * width))
    return out

# 행동 노드 -> (ID, 파라미터, 인자 이름, 설명). 파라미터가 None이면 불리언 스위치
ACTIONS = [
    ("SetSpeedDiff", "speed_diff", "pct",
     "Set this vehicle's target speed as a percentage below the posted limit. "
     "0 means drive at the limit, negative means exceed it."),
    ("SetGap", "gap_m", "meters",
     "Set the minimum bumper-to-bumper distance parameter for this vehicle."),
    ("SetKeepRight", "keep_right", "pct",
     "Set how strongly this vehicle prefers to stay in the right lane."),
    ("SetIgnoreLights", "ign_lights", "pct",
     "Set the chance this vehicle disregards a traffic light."),
    ("SetIgnoreSigns", "ign_signs", "pct",
     "Set the chance this vehicle disregards a stop or yield sign."),
    ("SetIgnoreVehicles", "ign_vehicles", "pct",
     "Set the chance this vehicle disregards another vehicle."),
    ("SetIgnoreWalkers", "ign_walkers", "pct",
     "Set the chance this vehicle disregards a pedestrian."),
    ("EnableLaneChange", None, "enabled",
     "Allow or forbid this vehicle from changing lanes on its own. "
     "Value is true or false."),
]

# 지각 계약. bt_runtime.Perception이 이 값을 읽음
SENSE = {
    "lead_max_m": 100.0,      # 선행차 원뿔 종거리
    "lead_lat_m": 2.0,        # 원뿔 횡폭. 차로가 아니라 이 폭으로 판정
    "v_stop_kmh": 1.8,        # IsStopped, LeaderStopped 임계
    "junction_scan_m": 60.0,  # 교차로 탐지 사거리. 10 m씩 6회
    "slow_period_s": 0.5,     # 느린 항목 갱신 주기
}

# 조건 임계의 합법 범위. 그 양이 실제로 취할 수 있는 구간.
# 속도 상한은 트리 행동이 만들 수 있는 최고속에서 나옴(speed_diff 하한 -30이면 제한의 1.3배)
MAP_LIMIT_MAX_KMH = 90.0
MAX_SPEED_KMH = MAP_LIMIT_MAX_KMH * (1.0 - KNOBS["speed_diff"][1] / 100.0)
MAX_DV_MPS = MAX_SPEED_KMH / 3.6
MAX_CURV = math.radians(180.0) / 10.0     # Perception 곡률 함수의 치역 상한
MAP_LANES_MAX = 5
MAX_LANE_TIME_S = 120.0    # 자연 상한 없음. 찍은 값

CONDITIONS = [
    ("IsStopped", None, "",
     "The vehicle is not moving (below %g km/h)." % SENSE["v_stop_kmh"]),
    ("SpeedBelow", "kmh", "0..%g" % MAX_SPEED_KMH,
     "Own speed is below the given value."),
    ("SpeedAbove", "kmh", "0..%g" % MAX_SPEED_KMH,
     "Own speed is above the given value."),
    ("LeaderPresent", None, "",
     "There is a vehicle ahead within a cone %g m long and %g m wide along this "
     "vehicle's heading. It is not restricted to the same lane."
     % (SENSE["lead_max_m"], SENSE["lead_lat_m"])),
    ("LeaderStopped", None, "",
     "The vehicle ahead is not moving (below %g km/h)." % SENSE["v_stop_kmh"]),
    ("GapBelow", "meters", "0..100",
     "Bumper-to-bumper distance to the vehicle ahead is below the value."),
    ("GapAbove", "meters", "0..100",
     "Bumper-to-bumper distance to the vehicle ahead is above the value."),
    ("ClosingFaster", "mps", "0..%g" % round(MAX_DV_MPS, 1),
     "Own speed exceeds the leader's speed by more than the value."),
    ("OpeningFaster", "mps", "0..%g" % round(MAX_DV_MPS, 1),
     "The leader's speed exceeds own speed by more than the value."),
    ("InJunction", None, "", "The vehicle is currently inside an intersection."),
    ("JunctionAhead", "meters", "0..%g" % SENSE["junction_scan_m"],
     "The next intersection is closer than the given distance. Nothing is seen "
     "beyond %g m." % SENSE["junction_scan_m"]),
    ("TrafficLightRed", None, "", "The traffic light governing this vehicle is red."),
    ("TrafficLightGreen", None, "", "The traffic light governing this vehicle is green."),
    ("SpeedLimitBelow", "kmh", "0..%g" % MAP_LIMIT_MAX_KMH,
     "The posted limit here is below the value."),
    ("SpeedLimitAbove", "kmh", "0..%g" % MAP_LIMIT_MAX_KMH,
     "The posted limit here is above the value."),
    ("IsRightmostLane", None, "", "The vehicle is in the rightmost lane."),
    ("IsLeftmostLane", None, "", "The vehicle is in the leftmost lane."),
    ("LaneCountAbove", "lanes", "1..%g" % MAP_LANES_MAX,
     "The road here has more lanes in this direction than the value."),
    ("TimeInLaneAbove", "seconds", "0..%g" % MAX_LANE_TIME_S,
     "The vehicle has been in its current lane longer than the value."),
    ("CurvatureAbove", "invmeters", "0..%.3g" % MAX_CURV,
     "The road ahead curves more sharply than the given curvature."),
]
SLOW_CONDS = ["JunctionAhead", "SpeedLimitBelow", "SpeedLimitAbove", "IsRightmostLane",
              "IsLeftmostLane", "LaneCountAbove", "CurvatureAbove"]

# 임계 방향. high는 값이 클수록 강한 조건. bt_runtime.EVAL 부등호와 같아야 함
STRONGER = {
    "SpeedAbove": "high", "GapAbove": "high", "ClosingFaster": "high",
    "OpeningFaster": "high", "SpeedLimitAbove": "high", "LaneCountAbove": "high",
    "TimeInLaneAbove": "high", "CurvatureAbove": "high",
    "SpeedBelow": "low", "GapBelow": "low", "SpeedLimitBelow": "low",
    "JunctionAhead": "low",
}

CONTROL = {"Sequence", "Fallback", "Parallel", "Selector"}
DECORATOR = {"Repeat", "Inverter", "ForceSuccess", "ForceFailure",
             "RetryUntilSuccessful"}
LEAF = {"Action", "Condition"}
SKIP = {"root", "BehaviorTree", "TreeNodesModel", "Params", "Param"}

ACTION_BY_ID = dict((a[0], a) for a in ACTIONS)
COND_BY_ID = dict((c[0], c) for c in CONDITIONS)

# 카탈로그 판. v1이 실험 2 그대로, v2는 보조 실험 9(2026-11-19)에서 설명만 고친 판.
# 실행기 의미, 노드 ID, 인자 이름, KNOBS는 안 바뀜. 그래서 v2 트리도 v1 검증기를 통과함(v2가 더 좁음)
CATALOG = "v1"
_V1 = (list(CONDITIONS), list(ACTIONS))
_V2_COND = {
    # Perception._slow_of가 10 m 간격 탐색이라 10 m 이하는 안 나옴. 보조 실험 6 공허 규칙 원인
    "JunctionAhead": ("JunctionAhead", "meters", "11..%g" % SENSE["junction_scan_m"],
                      "The next intersection is closer than the given distance. The distance is only "
                      "seen in 10 m steps (10, 20, ..., %g m), so a threshold of 10 or less is never "
                      "true. Nothing is seen beyond %g m." % (SENSE["junction_scan_m"], SENSE["junction_scan_m"])),
    # (road_id, lane_id) 바뀌면 리셋. 차로 안 바꿔도 도로 경계에서 0
    "TimeInLaneAbove": ("TimeInLaneAbove", "seconds", "0..%g" % MAX_LANE_TIME_S,
                        "The vehicle has been in its current lane longer than the value. It resets to 0 "
                        "whenever the vehicle enters a new road, even without changing lanes, and roads "
                        "are about 35 m long."),
}
_V2_ACT = {
    # 보조 실험 8: 감속 의도에 음수를 준 규칙이 판정한 속도 규칙의 36%
    "SetSpeedDiff": ("SetSpeedDiff", "speed_diff", "pct",
                     "Set how many percent SLOWER than the posted limit this vehicle aims to drive. "
                     "Positive values slow it down: 10 means 90% of the limit. Negative values make it "
                     "exceed the limit: -10 means 110% of the limit. 0 means drive at the limit. "
                     "To slow down, use a positive value."),
}


# v3 = v2 + 제한속도 문구(실험 4, 2026-11-20). 지각값은 get_speed_limit()라 차가 마지막으로 지난 표지판 값이고
# 표지판 전엔 30. TM도 같은 값을 씀. v2의 "posted limit here"는 도로 값처럼 읽혀서 실험 3에서 뜻이 어긋났음
_LIMIT_NOTE = ("That limit is the value of the last limit sign the vehicle passed, or 30 km/h until it passes "
               "one, so it can differ from the road the vehicle is on now.")
_V3_COND = {
    "SpeedLimitBelow": ("SpeedLimitBelow", "kmh", "0..%g" % MAP_LIMIT_MAX_KMH,
                        "The speed limit this vehicle currently obeys is below the value. " + _LIMIT_NOTE),
    "SpeedLimitAbove": ("SpeedLimitAbove", "kmh", "0..%g" % MAP_LIMIT_MAX_KMH,
                        "The speed limit this vehicle currently obeys is above the value. " + _LIMIT_NOTE),
}


def set_catalog(version):
    """카탈로그 판 고름. 리스트와 사전을 제자리에서 바꿔 이미 import한 쪽도 같은 걸 봄"""
    global CATALOG
    if version not in ("v1", "v2", "v3"):
        raise ValueError("카탈로그 판은 v1, v2, v3: %s" % version)
    conds, acts = _V1
    if version in ("v2", "v3"):
        conds = [_V2_COND.get(c[0], c) for c in conds]
        acts = [_V2_ACT.get(a[0], a) for a in acts]
    if version == "v3":
        conds = [_V3_COND.get(c[0], c) for c in conds]
    CONDITIONS[:] = conds
    ACTIONS[:] = acts
    COND_BY_ID.clear()
    COND_BY_ID.update((c[0], c) for c in CONDITIONS)
    ACTION_BY_ID.clear()
    ACTION_BY_ID.update((a[0], a) for a in ACTIONS)
    CATALOG = version

PARAM_NAME = r"[A-Za-z_][A-Za-z0-9_-]*"
PARAM_REF = re.compile(r"^\s*\{(" + PARAM_NAME + r")\}\s*$")
NAME_RE = re.compile(r"^" + PARAM_NAME + r"$")
PARAMS_TAG = "Params"
PARAM_TAG = "Param"


def cond_range(nid):
    spec = COND_BY_ID.get(nid)
    if not spec or not spec[1]:
        return None
    m = re.match(r"^\s*(-?[\d.]+)\s*\.\.\s*(-?[\d.]+)\s*$", spec[2])
    return (float(m.group(1)), float(m.group(2))) if m else None


def action_range(nid):
    """행동 값의 허용 범위 = 추첨 상자. 스위치면 None"""
    spec = ACTION_BY_ID.get(nid)
    if not spec or spec[1] is None:
        return None
    _b, lo, hi = KNOBS[spec[1]]
    return (lo, hi)


def clamp(knob, v):
    """실행기 안전 클램프. 검증 통과한 트리는 여기 안 걸려야 함"""
    _b, lo, hi = KNOBS[knob]
    return min(hi, max(lo, v))


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ---- 카탈로그 ----

def _tm_api():
    """spec/tm_api를 늦게 import. spec 없어도 카탈로그는 나와야 함"""
    import os
    import sys
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spec")
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        import tm_api
        return tm_api
    except Exception:
        return None


def catalogue_text(with_api=True):
    """프롬프트에 넣을 카탈로그. 범위까지 적어야 범위 밖 값을 안 씀"""
    api = _tm_api() if with_api else None
    facts = api.knob_facts() if api else {}
    out = ["CONDITION nodes (use as <Condition ID=\"...\" .../>):"]
    for name, arg, rng, desc in CONDITIONS:
        if arg:
            out.append('  <Condition ID="%s" %s="..."/>   %s  [%s: %s]'
                       % (name, arg, desc, arg, rng))
        else:
            out.append('  <Condition ID="%s"/>   %s' % (name, desc))
    out.append("")
    out.append("These conditions read values refreshed every %g s, not every tick: %s."
               % (SENSE["slow_period_s"], ", ".join(SLOW_CONDS)))
    out.append("")
    out.append("ACTION nodes (use as <Action ID=\"...\" .../>):")
    out.append("The range shown for each is the full range a value may take. Values "
               "outside it are rejected.")
    for name, knob, arg, desc in ACTIONS:
        # 기준값을 꼭 적음. 매 틱 기준값으로 리셋되는데 그 값을 모르면
        # 아무 가지도 안 걸릴 때를 설계 못 함
        if knob is None:
            out.append('  <Action ID="%s" %s="true|false"/>   %s  [baseline: true]'
                       % (name, arg, desc))
            k = "lane_change"
        else:
            b, lo, hi = KNOBS[knob]
            out.append('  <Action ID="%s" %s="..."/>   %s  [%s: %g to %g, baseline %g]'
                       % (name, arg, desc, arg, lo, hi, b))
            k = knob
        f = facts.get(k)
        if not f:
            continue
        if f["doc"]:
            out.append("      CARLA %s: %s" % (f["method"], " ".join(f["doc"].split())))
        elif f["in_egg"]:
            out.append("      CARLA %s: not documented in 0.9.10. It exists but its "
                       "exact effect is unspecified." % f["method"])
    out.append("")
    out.append(PARAMS_DOC)
    return "\n".join(out)


# 기전만 적음. 목적(차량끼리 달라야 한다 등)은 안 적음
PARAMS_DOC = """PARAMETERS.

Every numeric value above can be written either as a literal number or as a parameter
reference. A parameter is declared once and referred to as {name}:

  <root BTCPP_format="4">
    <Params>
      <Param name="pump_rate"  min="2"  max="8"  default="5"/>
      <Param name="low_mark"   min="10" max="30" default="20"/>
    </Params>
    <BehaviorTree ID="...">
      ...
      <Sequence>
        <Condition ID="LevelBelow" pct="{low_mark}"/>
        <Action ID="SetPumpRate" lpm="{pump_rate}"/>
      </Sequence>

(That example uses made-up nodes from an unrelated domain. It shows the syntax only.)
An Action value and a Condition threshold take a parameter the same way. On an Action
value it changes what the object does in a given situation. On a Condition threshold it
changes the point at which that branch becomes the active one for that object.

Each object running the tree draws its own value for each parameter, once, uniformly
from [min, max], and keeps that value for its whole lifetime. Different objects running
the same tree therefore hold different values. A literal is the same for every object.

Use a parameter for a quantity whose right value you think differs from one individual
to another. Use a literal for a quantity you think is the same for everyone. Both are
available for Action values and for Condition thresholds.

The default is the value used when the parameters are not drawn, so write the single
value you would have written if you had to write a literal there.

Rules the declaration must obey:
- [min, max] must lie inside the range shown for the node where the parameter is used.
- min < max, and min <= default <= max.
- Every declared parameter must be used at least once.
- true/false attributes cannot be parameters."""


# ---- 검증 ----
# 형식: 문법, 스키마, 구조. 의미: 카탈로그와 범위, 파라미터 선언/참조 일치.
# 두 목록을 따로 모으려고 err(kind, msg)로 씀

FORMAT, SEMANTIC = "format", "semantic"


def parse_params(root, err):
    """<Params> 선언. 이름 -> (min, max, default). 선언 꼴이 틀린 건 형식 오류"""
    out = {}
    blocks = [e for e in root.iter(PARAMS_TAG)]
    if len(blocks) > 1:
        err(FORMAT, "There must be at most one <%s> block, found %d"
            % (PARAMS_TAG, len(blocks)))
    for blk in blocks:
        for el in blk:
            if not isinstance(el.tag, str):
                continue
            if el.tag != PARAM_TAG:
                err(FORMAT, "Only <%s> elements are allowed inside <%s>, found <%s>"
                    % (PARAM_TAG, PARAMS_TAG, el.tag))
                continue
            name = el.get("name")
            if not name:
                err(FORMAT, "<%s> is missing the [name] attribute" % PARAM_TAG)
                continue
            if not NAME_RE.match(name):
                err(FORMAT, "<%s name=\"%s\"> is not a valid name. Use letters, digits, "
                    "underscore and hyphen, starting with a letter or underscore."
                    % (PARAM_TAG, name))
                continue
            if name in out:
                err(FORMAT, "<%s name=\"%s\"> is declared twice" % (PARAM_TAG, name))
                continue
            lo, hi = _num(el.get("min")), _num(el.get("max"))
            if lo is None or hi is None:
                err(FORMAT, "<%s name=\"%s\"> needs numeric [min] and [max]"
                    % (PARAM_TAG, name))
                continue
            if not lo < hi:
                err(FORMAT, "<%s name=\"%s\"> needs min < max, got %g and %g"
                    % (PARAM_TAG, name, lo, hi))
                continue
            d = _num(el.get("default"))
            if d is None:
                d = 0.5 * (lo + hi)
            elif not (lo <= d <= hi):
                err(FORMAT, "<%s name=\"%s\"> default %g is outside [%g, %g]"
                    % (PARAM_TAG, name, d, lo, hi))
                continue
            out[name] = (lo, hi, d)
    return out


def _check_value(el, nid, arg, val, rng, params, used, err):
    m = PARAM_REF.match(val or "")
    if not m:
        x = _num(val)
        if x is None:
            err(FORMAT, "<%s ID=\"%s\"> attribute [%s] must be a number or a parameter "
                "reference like {name}, got [%s]" % (el.tag, nid, arg, val))
        elif rng and not (rng[0] <= x <= rng[1]):
            err(SEMANTIC, "<%s ID=\"%s\"> attribute [%s]=%g is outside the allowed "
                "range [%g, %g]" % (el.tag, nid, arg, x, rng[0], rng[1]))
        return
    name = m.group(1)
    used.setdefault(name, []).append((el.tag, nid, arg))
    if name not in params:
        err(SEMANTIC, "<%s ID=\"%s\"> uses parameter {%s} but it is not declared in "
            "<%s>" % (el.tag, nid, name, PARAMS_TAG))
        return
    lo, hi, _d = params[name]
    if rng and (lo < rng[0] or hi > rng[1]):
        err(SEMANTIC, "Parameter {%s} spans [%g, %g] but it is used at <%s ID=\"%s\"> "
            "[%s], whose allowed range is [%g, %g]. Narrow the declaration."
            % (name, lo, hi, el.tag, nid, arg, rng[0], rng[1]))


def _check_leaf(el, params, used, err):
    nid = el.get("ID")
    if not nid:
        err(FORMAT, "<%s> is missing the ID attribute" % el.tag)
        return
    if el.tag == "Action":
        if nid not in ACTION_BY_ID:
            err(SEMANTIC, "Unknown Action ID [%s]. Use only IDs from the catalogue." % nid)
            return
        _n, knob, arg, _d = ACTION_BY_ID[nid]
        val = el.get(arg)
        if val is None:
            err(FORMAT, "<Action ID=\"%s\"> is missing the required attribute [%s]"
                % (nid, arg))
            return
        if knob is None:
            if PARAM_REF.match(val):
                err(FORMAT, "<Action ID=\"%s\"> attribute [%s] is true or false, so it "
                    "cannot be a parameter. Write the value directly." % (nid, arg))
            elif val.strip().lower() not in ("true", "false"):
                err(FORMAT, "<Action ID=\"%s\"> attribute [%s] must be true or false, "
                    "got [%s]" % (nid, arg, val))
            return
        _check_value(el, nid, arg, val, action_range(nid), params, used, err)
    else:
        if nid not in COND_BY_ID:
            err(SEMANTIC, "Unknown Condition ID [%s]. Use only IDs from the catalogue."
                % nid)
            return
        _n, arg, _r, _d = COND_BY_ID[nid]
        if not arg:
            return
        val = el.get(arg)
        if val is None:
            err(FORMAT, "<Condition ID=\"%s\"> is missing the required attribute [%s]"
                % (nid, arg))
            return
        _check_value(el, nid, arg, val, cond_range(nid), params, used, err)


def _walk(el, params, used, err, depth=0):
    kids = [c for c in el if isinstance(c.tag, str)]
    if el.tag in SKIP:
        pass
    elif el.tag in CONTROL:
        if not kids:
            err(FORMAT, "%s at depth %d has no children" % (el.tag, depth))
        if el.get("ID"):
            err(FORMAT, "Attribute [ID] is not allowed in <%s>" % el.tag)
    elif el.tag in DECORATOR:
        if len(kids) != 1:
            err(FORMAT, "%s decorator at depth %d must have exactly 1 child, has %d"
                % (el.tag, depth, len(kids)))
    elif el.tag in LEAF:
        if kids:
            err(FORMAT, "<%s> must not have children" % el.tag)
        _check_leaf(el, params, used, err)
    else:
        err(FORMAT, "Unknown node tag <%s>. Allowed: %s"
            % (el.tag, ", ".join(sorted(CONTROL | DECORATOR | LEAF))))
    for c in kids:
        _walk(c, params, used, err, depth + 1)


# ---- 도달 불가 ----

def _ord(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, "%dth" % n)


def _show(gs):
    def one(g):
        if g[1] is None:
            return g[0]
        lo, hi = g[1]
        return "%s %g" % (g[0], lo) if lo == hi else "%s {%g..%g}" % (g[0], lo, hi)
    return " and ".join(one(g) for g in gs) or "no conditions"


def _guards(ch, params=None):
    """자식 하나의 가드. (조건 ID, (하한, 상한) 또는 None). 파라미터 임계는 선언 구간"""
    params = params or {}
    out = []
    for e in ch.iter("Condition"):
        nid = e.get("ID")
        spec = COND_BY_ID.get(nid)
        arg = spec[1] if spec else None
        v = None
        if arg:
            raw = e.get(arg)
            m = PARAM_REF.match(raw or "")
            if m and m.group(1) in params:
                lo, hi, _d = params[m.group(1)]
                v = (lo, hi)
            else:
                x = _num(raw)
                v = (x, x) if x is not None else None
        out.append((nid, v))
    return out


def _implies(a, b):
    """가드 a가 성립하면 b도 성립하는가. 구간이면 모든 뽑기에서 성립할 때만 참"""
    if a[0] != b[0]:
        return False
    if a[1] is None or b[1] is None:
        return a[1] == b[1]
    (alo, ahi), (blo, bhi) = a[1], b[1]
    d = STRONGER.get(a[0])
    if d == "high":
        return alo >= bhi
    if d == "low":
        return ahi <= blo
    return alo == ahi == blo == bhi


def unreachable(root, params):
    """Fallback 형제 중 앞 자식에 가려 안 걸리는 것. 경고 문자열 목록.

    앞 판과 판정 규칙 같음. 조건 없는 자식이 마지막이 아니면 뒤가 전부 죽고, 뒤 자식의
    가드가 앞 자식 가드를 전부 함의하면 죽음. 맵 사정으로 생기는 그림자(도심에서 늘 참인
    SpeedLimitBelow 50 등)는 못 잡음
    """
    out = []
    fbs = [e for e in root.iter() if e.tag in ("Fallback", "Selector")]
    for n_fb, fb in enumerate(fbs, 1):
        where = ("the %s <%s> in document order" % (_ord(n_fb), fb.tag)
                 if len(fbs) > 1 else "the <%s>" % fb.tag)
        kids = [c for c in fb if isinstance(c.tag, str)]
        G = [_guards(c, params) for c in kids]
        for j in range(len(kids)):
            for i in range(j):
                if not G[i]:
                    out.append("In %s: child %d has no conditions, so it always succeeds "
                               "and child %d can never run." % (where, i + 1, j + 1))
                    break
                if all(any(_implies(gj, gi) for gj in G[j]) for gi in G[i]):
                    out.append("In %s: child %d [%s] can never run because child %d [%s] "
                               "comes first." % (where, j + 1, _show(G[j]), i + 1,
                                                 _show(G[i])))
                    break
    return out


def _covers(Gj, Gi):
    return all(any(_implies(gj, gi) for gj in Gj) for gi in Gi)


def reorder(xml_text):
    """도달 불가 후처리. Fallback 형제를 구체적인 가드가 앞에 오게 안정 위상정렬.
    (2026-11-10 사후 추가, docs/design.md 변경 기록)

    판정은 unreachable과 같은 함의 규칙. 뒤 자식 가드가 앞 자식 가드를 엄격 함의하면 앞으로,
    조건 없는 자식은 조건 있는 자식 뒤로. 제약 없는 쌍은 원래 순서 유지.
    가정: 구체적 규칙이 일반 규칙의 예외. LLM이 일반 규칙을 일부러 최우선에 뒀다면 그 의도를 덮음.
    가드가 같은 중복과 조건 없는 자식 여럿은 순서로 못 풀어서 그대로 남음.
    반환 (xml, 옮긴 Fallback 수). 안 읽히면 원문 그대로"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text, 0
    params = parse_params(root, lambda *_a: None)
    moved = 0
    for fb in [e for e in root.iter() if e.tag in ("Fallback", "Selector")]:
        kids = [c for c in fb if isinstance(c.tag, str)]
        G = [_guards(c, params) for c in kids]
        n = len(kids)
        after = [set() for _ in range(n)]     # after[i]: i보다 앞에 와야 하는 자식
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if not G[i] and G[j]:
                    after[i].add(j)
                elif G[i] and G[j] and _covers(G[j], G[i]) and not _covers(G[i], G[j]):
                    after[i].add(j)
        order, done = [], set()
        while len(order) < n:
            k = min(x for x in range(n) if x not in done and after[x] <= done)
            order.append(k)
            done.add(k)
        if order != list(range(n)):
            moved += 1
            tail = kids[-1].tail
            for c in kids:
                fb.remove(c)
            for k in order:
                fb.append(kids[k])
            for c in fb[:-1]:
                c.tail = kids[0].tail
            fb[-1].tail = tail
    return ET.tostring(root, encoding="unicode"), moved


def check(xml_text):
    """{format, semantic, unreachable, stats}. format과 semantic이 비면 실행 가능"""
    res = {FORMAT: [], SEMANTIC: [], "unreachable": [], "stats": {}}

    def err(kind, msg):
        res[kind].append(msg)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        err(FORMAT, "XML parse error: %s" % e)
        return res
    bts = list(root.iter("BehaviorTree")) if root.tag != "BehaviorTree" else [root]
    if not bts:
        err(FORMAT, "No <BehaviorTree> element found")
    for bt in bts:
        kids = [c for c in bt if isinstance(c.tag, str)]
        if len(kids) != 1:
            err(FORMAT, "<BehaviorTree> must have exactly 1 child, has %d" % len(kids))
    params = parse_params(root, err)
    used = {}
    _walk(root, params, used, err)
    for name in sorted(set(params) - set(used)):
        err(SEMANTIC, "Parameter {%s} is declared but never used. Either use it in a leaf "
            "or remove the declaration." % name)
    res["unreachable"] = unreachable(root, params)
    tags = [el.tag for el in root.iter() if isinstance(el.tag, str)]
    res["stats"] = {
        "total": len([t for t in tags if t not in SKIP]),
        "action": tags.count("Action"), "condition": tags.count("Condition"),
        "control": sum(tags.count(t) for t in CONTROL),
        "param": len(params),
        "action_ids": sorted(set(el.get("ID") for el in root.iter("Action")
                                 if el.get("ID"))),
        "cond_ids": sorted(set(el.get("ID") for el in root.iter("Condition")
                               if el.get("ID"))),
    }
    return res


def validate(xml_text):
    """(실행 막는 오류, 통계). 실행기와 수집기가 씀. 도달 불가는 안 막음"""
    r = check(xml_text)
    return r[FORMAT] + r[SEMANTIC], r["stats"]


# 오류 문자열 -> 유형. 정확성 표의 유형별 해소 회차에 씀
ERR_TYPES = [
    ("xml", "XML 문법", FORMAT, ("XML parse error",)),
    ("structure", "구조", FORMAT, ("has no children", "must have exactly 1 child",
                                   "must not have children", "No <BehaviorTree>",
                                   "Unknown node tag", "Attribute [ID] is not allowed")),
    ("schema", "속성, 선언 꼴", FORMAT, ("is missing the", "must be a number",
                                       "must be true or false", "cannot be a parameter",
                                       "at most one <Params>", "elements are allowed inside",
                                       "declared twice", "needs numeric", "needs min",
                                       "is not a valid name", "> default ")),
    ("unknown_id", "카탈로그 밖 ID", SEMANTIC, ("Unknown Action ID", "Unknown Condition ID")),
    ("range", "범위 밖 값", SEMANTIC, ("is outside the allowed range", "allowed range is")),
    ("undeclared", "미선언 파라미터", SEMANTIC, ("but it is not declared",)),
    ("unused", "미사용 파라미터", SEMANTIC, ("is declared but never used",)),
]
TYPE_LABEL = dict((k, lab) for k, lab, _c, _p in ERR_TYPES)
TYPE_CLASS = dict((k, c) for k, _l, c, _p in ERR_TYPES)


def err_type(msg):
    for key, _lab, _cls, pats in ERR_TYPES:
        if any(p in msg for p in pats):
            return key
    return "other"


if __name__ == "__main__":
    print(catalogue_text())
