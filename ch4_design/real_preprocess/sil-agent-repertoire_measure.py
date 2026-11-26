"""계측 코어. 궤적을 자르고 다섯 숫자로 줄이고 층을 매긴다.

원래 파일 다섯을 합쳤다. score_segments가 나머지를 다 import 하던 사슬이라 합쳐도
경계가 안 없어진다. maps는 이 파일만 쓰던 것이라 뒤에 같이 넣었다. 다른 스크립트는
이 파일을 G나 S로 부른다.

arm 이름 규칙(grp_of, is_probe)이 여기 있다. 적재기가 셋인데 저마다 정규식을 적어
두면 갈린다. 실제로 갈려서 상황 축이 죽었다.

이름이 부딪혀 고친 것(본문은 그대로다):
    build_strata.OUT -> OUT_STRATA
    build_strata.units -> track_units
    maps.OUT -> OUT_MAPS
"""

import os
import numpy as np
import pandas as pd
import argparse
import glob
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
sys.path.insert(0, "scripts")
import collections
from scipy.spatial import cKDTree

S = sys.modules[__name__]   # 합치기 전 별칭. 부르는 쪽을 안 고치려고


# ==========================================================================
# maps: OpenDRIVE 기하와 층 분류의 입력. 원래 따로 있던 파일
# ==========================================================================

"""CARLA OpenDRIVE 맵을 ODD 구간으로 분리한다. CARLA 서버 불요, xodr 파싱만.

왜: 지금 zone_mask는 프레임마다 관측된 제한속도, 차로수, 신호등 거리로 판정한다. 지도에서 미리
분리해두면 결정적이고, 수집 전에 어느 ODD에 얼마나 표본이 있는지 알 수 있고, 채점은 road_id
조회만 하면 된다.

교차로 상속: CARLA xodr는 교차로 연결로에 <type><speed>를 안 적는다(실측 확인: 교차로 도로의
100%가 speed 없음, 비교차 도로의 100%가 있음). 그래서 junction 요소의 connection에서 incomingRoad를
찾아 그 속도와 차로수를 물려받는다. 안 그러면 교차로가 전부 저속으로 분류된다.

단위 주의: <speed unit="mph">다. km/h로 읽으면 105 km/h 도로가 65로 보여 고속 구간이 통째로
사라진다(한 번 당했다).
"""

MPS = {"mph": 1.609344, "km/h": 1.0, "kmh": 1.0, "m/s": 3.6}
HW_SPEED = 80.0      # km/h. 표지가 40/48/56/64/80/89/105라 80은 앞 넷을 버리는 자연 경계
HW_LANES = 3         # 실측 고속 참조가 5~6차로, 도심이 2~3차로
# OpenDRIVE 원본 위치. 레포 안 data/maps/OpenDrive가 1순위라 스크립트가 밖을 안 본다.
# 비었으면 CARLA 설치본을 본다. CARLA_ROOT나 MAP_DIR로 덮어쓸 수 있음.
# xodr 여덟은 CARLA 배포물이라 레포에 안 넣었다. 채점만 하려면 거기로 복사해야 함.
_REPO_MAPS = os.path.join("data", "maps", "OpenDrive")
_CARLA_MAPS = os.path.join(os.environ.get("CARLA_ROOT", r"C:\CARLA\WindowsNoEditor"),
                           "CarlaUE4", "Content", "Carla", "Maps", "OpenDrive")
MAP_DIR = os.environ.get(
    "MAP_DIR",
    _REPO_MAPS if glob.glob(os.path.join(_REPO_MAPS, "Town*.xodr")) else _CARLA_MAPS)
OUT_MAPS = os.path.join("data", "outputs", "map_partition")


def road_speed_kmh(road):
    """type/speed를 km/h로. 단위 변환 필수. 없으면 None(교차로 연결로가 그렇다)."""
    best = None
    for t in road.findall("type/speed"):
        u = t.get("unit")
        if u not in MPS:
            raise ValueError("unknown speed unit %r" % u)
        v = float(t.get("max", 0)) * MPS[u]
        best = v if best is None else max(best, v)
    return best


def road_lanes(road):
    """첫 laneSection의 주행 차로 수(좌우 합)."""
    ls = road.find("lanes/laneSection")
    if ls is None:
        return 0
    return sum(1 for side in ("left", "right") for lane in ls.findall(side + "/lane")
               if lane.get("type") == "driving")


def radii(road):
    """planView 기하별 (곡률반경, 길이). 직선은 inf. spiral/poly3는 근사 못 해 None으로 센다."""
    out = []
    pv = road.find("planView")
    if pv is None:
        return out
    for g in pv.findall("geometry"):
        L = float(g.get("length", 0))
        if g.find("line") is not None:
            out.append((float("inf"), L))
        elif g.find("arc") is not None:
            c = abs(float(g.find("arc").get("curvature", 0)))
            out.append((float("inf") if c < 1e-12 else 1.0 / c, L))
        else:
            out.append((None, L))
    return out


def analyze(path):
    root = ET.parse(path).getroot()
    roads = {r.get("id"): r for r in root.iter("road")}
    spd = {}
    lanes = {}
    for rid, r in roads.items():
        spd[rid] = road_speed_kmh(r)
        lanes[rid] = road_lanes(r)

    # 교차로 연결로 -> 그 교차로에 물린 진입 도로들
    incoming = defaultdict(set)
    for j in root.iter("junction"):
        conns = j.findall("connection")
        ins = {c.get("incomingRoad") for c in conns}
        for c in conns:
            cr = c.get("connectingRoad")
            if cr is not None:
                incoming[cr] |= {i for i in ins if i is not None}

    def is_hw(rid):
        """비교차 도로의 고속 여부."""
        s, n = spd.get(rid), lanes.get(rid, 0)
        return s is not None and s >= HW_SPEED and n >= HW_LANES

    cls = {}
    junc = {}
    spd_eff = {}       # 교차로 연결로는 진입로에서 물려받은 속도. 갈리면 None
    for rid, r in roads.items():
        junc[rid] = r.get("junction", "-1") != "-1"
        if not junc[rid]:
            cls[rid] = "HW_link" if is_hw(rid) else "URB_link"
            spd_eff[rid] = spd[rid]
        else:
            src = [i for i in incoming.get(rid, ()) if i in roads]
            if not src:
                cls[rid] = "URB_junction"          # 진입 도로 못 찾음. 보수적으로 도심
            else:
                flags = [is_hw(i) for i in src]
                cls[rid] = ("HW_junction" if all(flags)
                            else "URB_junction" if not any(flags) else "MIX_junction")
            # 진입로 속도가 한 종류일 때만 물려받는다. 갈리는데 하나를 고르면 그게 판단값이 되고
            # 실측으로 교차로 연장의 37%가 두 종류라 무시 못 할 양이다
            vals = {spd[i] for i in src if spd.get(i) is not None}
            spd_eff[rid] = vals.pop() if len(vals) == 1 else None

    length = Counter()
    rad = defaultdict(list)
    total = 0.0
    for rid, r in roads.items():
        L = float(r.get("length", 0))
        total += L
        length[cls[rid]] += L
        for R, gl in radii(r):
            rad[cls[rid]].append((R, gl))
    return {"total": total, "length": length, "rad": rad, "cls": cls,
            "spd": spd, "spd_eff": spd_eff, "junction": junc, "lanes": lanes}


def _set_thresholds(speed, lanes):
    """민감도 스윕용. analyze()가 모듈 전역을 보므로 여기서 갈아끼운다."""
    global HW_SPEED, HW_LANES
    HW_SPEED, HW_LANES = speed, lanes


SIGNAL_TYPE = {"1000001": "signal", "206": "stop", "205": "yield"}


def junction_control(path):
    """교차로 단위로 (클래스, 통제방식) -> (개수, 연결로 연장). 통제방식은 맵이 주는 값이라
    고속/도심 임계와 달리 내 판단이 안 들어간다. 신호 > 정지 > 양보 순으로 우선."""
    root = ET.parse(path).getroot()
    cls = analyze(path)["cls"]
    roads = {r.get("id"): r for r in root.iter("road")}
    sig = {rid: {SIGNAL_TYPE.get(s.get("type")) for s in r.iter("signal")}
           for rid, r in roads.items()}
    out = defaultdict(lambda: [0, 0.0])
    for j in root.iter("junction"):
        conns = j.findall("connection")
        cr = {c.get("connectingRoad") for c in conns} & set(roads)
        ins = {c.get("incomingRoad") for c in conns} & set(roads)
        types = set().union(*(sig[r] for r in cr | ins)) if (cr | ins) else set()
        ctrl = next((t for t in ("signal", "stop", "yield") if t in types), "uncontrolled")
        kinds = Counter(cls.get(r) for r in cr)
        kind = kinds.most_common(1)[0][0].replace("_junction", "") if kinds else "?"
        e = out["%s / %s" % (kind, ctrl)]
        e[0] += 1
        e[1] += sum(float(roads[r].get("length", 0)) for r in cr)
    return {k: tuple(v) for k, v in out.items()}


def radius_bands(pairs):
    """반경 구간별 길이 비중. 접선 기준 횡오프셋이 s^2/(2R)이라 R이 작으면 앞차를 놓친다."""
    bands = [(0, 200), (200, 500), (500, 1000), (1000, float("inf"))]
    out = [0.0] * (len(bands) + 1)      # 마지막 칸은 spiral 등 미분류
    for R, L in pairs:
        if R is None:
            out[-1] += L
            continue
        for i, (lo, hi) in enumerate(bands):
            if lo <= R < hi:
                out[i] += L
                break
        else:
            out[len(bands) - 1] += L
    return out


lines = []


def emit(s):
    print(s, flush=True)
    lines.append(s)


def _report():
    """단독 실행 때만 돈다. 부르는 쪽이 analyze만 import하므로 부작용을 막는다."""
    os.makedirs(OUT_MAPS, exist_ok=True)
    files = [f for f in sorted(glob.glob(os.path.join(MAP_DIR, "Town*.xodr")))
             if os.path.basename(f) != "Town10.xodr"]
    emit("CARLA map partition | HW = speed >= %.0f km/h AND lanes >= %d" % (HW_SPEED, HW_LANES))
    emit("maps: %d, dir: %s" % (len(files), MAP_DIR))
    emit("")
    emit("%-9s %8s | %8s %8s | %8s %8s %8s" %
         ("map", "total_m", "HW_link", "HW_junc", "URB_link", "URB_junc", "MIX_junc"))

    grand = Counter()
    grand_rad = defaultdict(list)
    lookup = {}
    for f in files:
        name = os.path.basename(f).replace(".xodr", "")
        a = analyze(f)
        total, length, rad = a["total"], a["length"], a["rad"]
        lookup[name] = a["cls"]
        s = sum(length.values())
        assert abs(s - total) < 1.0, "length mismatch in %s" % name
        emit("%-9s %8.0f | %8.0f %8.0f | %8.0f %8.0f %8.0f"
             % (name, total, length["HW_link"], length["HW_junction"],
                length["URB_link"], length["URB_junction"], length["MIX_junction"]))
        for k, v in length.items():
            grand[k] += v
        for k, v in rad.items():
            grand_rad[k].extend(v)

    emit("%-9s %8.0f | %8.0f %8.0f | %8.0f %8.0f %8.0f"
         % ("TOTAL", sum(grand.values()), grand["HW_link"], grand["HW_junction"],
            grand["URB_link"], grand["URB_junction"], grand["MIX_junction"]))

    emit("")
    emit("곡률반경 구간별 연장(m). 접선 기준 횡오프셋 ~ s^2/(2R)이라 R이 작으면 원거리 앞차를 놓친다.")
    emit("%-14s %10s %10s %10s %10s %10s" %
         ("class", "R<200", "200-500", "500-1000", "R>=1000", "미분류"))
    for k in ("HW_link", "HW_junction", "MIX_junction", "URB_link", "URB_junction"):
        if not grand_rad.get(k):
            continue
        b = radius_bands(grand_rad[k])
        emit("%-14s %10.0f %10.0f %10.0f %10.0f %10.0f" % (k, b[0], b[1], b[2], b[3], b[4]))

    emit("")
    emit("임계 민감도. 고속/도심 축이 내가 고른 두 값에 얼마나 흔들리는지.")
    emit("%-6s %-6s | %9s %9s %9s %9s %9s" %
         ("spd", "lanes", "HW_link", "HW_junc", "MIX_junc", "URB_link", "URB_junc"))
    keep = (HW_SPEED, HW_LANES)
    for sp in (56.0, 64.0, 70.0, 80.0, 89.0):
        for ln in (2, 3, 4):
            _set_thresholds(sp, ln)
            tot = Counter()
            for f in files:
                for k, v in analyze(f)["length"].items():
                    tot[k] += v
            emit("%-6.0f %-6d | %9.0f %9.0f %9.0f %9.0f %9.0f" %
                 (sp, ln, tot["HW_link"], tot["HW_junction"], tot["MIX_junction"],
                  tot["URB_link"], tot["URB_junction"]))
    _set_thresholds(*keep)

    emit("")
    emit("교차로 통제 방식별 연장(m). signal/stop/yield는 xodr <signal> type 1000001/206/205.")
    emit("교차로에 물린 연결로와 진입로를 다 훑어 하나라도 있으면 그 통제로 본다.")
    emit("%-24s %6s %9s" % ("class / control", "n", "len_m"))
    ctl_len, ctl_n = Counter(), Counter()
    for f in files:
        for key, (n, L) in junction_control(f).items():
            ctl_len[key] += L
            ctl_n[key] += n
    for k in sorted(ctl_len, key=lambda x: -ctl_len[x]):
        emit("%-24s %6d %9.0f" % (k, ctl_n[k], ctl_len[k]))

    dst = os.path.join(OUT_MAPS, "partition.txt")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    js = os.path.join(OUT_MAPS, "road_class.json")
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(lookup, fh, indent=1, sort_keys=True)
    print("\nwrote %s" % dst)
    print("wrote %s (road_id -> class, 채점 시 조회용)" % js)


# ==========================================================================
# map_overlay: 도로 기하 표본. measure가 쓴다
# ==========================================================================

STEP = 2.0                       # 도로 따라 표본 간격(m)

def sample_road(road, step=STEP):
    """road의 planView를 (x, y) 점열로. line과 arc만 처리한다."""
    pts = []
    pv = road.find("planView")
    if pv is None:
        return pts
    for g in pv.findall("geometry"):
        x0 = float(g.get("x")); y0 = float(g.get("y"))
        hdg = float(g.get("hdg")); L = float(g.get("length", 0))
        n = max(2, int(L / step))
        arc = g.find("arc")
        c = float(arc.get("curvature", 0)) if arc is not None else 0.0
        for i in range(n + 1):
            t = L * i / n
            if abs(c) < 1e-12:
                pts.append((x0 + t * math.cos(hdg), y0 + t * math.sin(hdg)))
            else:
                h = hdg + c * t
                pts.append((x0 + (math.sin(h) - math.sin(hdg)) / c,
                            y0 - (math.cos(h) - math.cos(hdg)) / c))
        pts.append((float("nan"), float("nan")))   # 기하 사이 끊기
    return pts


def road_point(road, s, t=0.0):
    """도로 기준선의 station s에서 (x, y). t는 좌측이 양인 횡오프셋.

    signal은 s와 t로만 위치를 주므로 기하를 그 지점까지 따라가야 좌표가 나온다.
    geometry의 s는 도로 시작부터의 누적거리라 그걸로 구간을 찾는다.
    """
    pv = road.find("planView")
    if pv is None:
        return None
    geos = pv.findall("geometry")
    if not geos:
        return None
    g = geos[0]
    for cand in geos:                      # s를 품는 마지막 구간. 넘어가면 마지막 것을 쓴다
        if float(cand.get("s", 0)) <= s:
            g = cand
        else:
            break
    x0 = float(g.get("x")); y0 = float(g.get("y"))
    hdg = float(g.get("hdg"))
    ds = max(0.0, s - float(g.get("s", 0)))
    arc = g.find("arc")
    c = float(arc.get("curvature", 0)) if arc is not None else 0.0
    if abs(c) < 1e-12:
        x, y, h = x0 + ds * math.cos(hdg), y0 + ds * math.sin(hdg), hdg
    else:
        h = hdg + c * ds
        x = x0 + (math.sin(h) - math.sin(hdg)) / c
        y = y0 - (math.cos(h) - math.cos(hdg)) / c
    return x - math.sin(h) * t, y + math.cos(h) * t



# ==========================================================================
# build_strata: 실측 궤적을 트랙 단위로 자른다
# ==========================================================================
"""실측을 층별로 가른다. CARLA 불요, 원본 CSV만 있으면 된다.

층은 08에서 확정한 넷이다.
  hw_link          고속 링크              NGSIM us101, i80, TGSIM 고속 5사이트
  urb_block_narrow 교차로 사이 구간(방향당 2차로)  peachtree
  urb_block_wide   교차로 사이 구간(방향당 3차로)  lankershim
  signal_junction  신호교차로              lankershim, peachtree int_id > 0

"도심 링크"가 아니라 "교차로 사이 구간"이라고 부른다. 실측 도심 간선은 블록이 90~160 m라
교차로에서 50 m씩 떼면 남는 게 없다. 즉 교차로 영향권 밖의 자유주행 구간이 아예 없다.
이름을 링크라고 붙이면 없는 것을 있다고 말하는 셈이 된다.

주의 다섯. 안 하면 값이 통째로 틀린다.

1. 트랙 끊기. NGSIM은 차량 ID를 재사용한다(lankershim 두 세션, peachtree 두 시간대).
   안 끊으면 Lankershim 차량당 관측이 중앙값 893초로 나온다. 전체 관측이 2067초인데도 그렇다.
   시간 간격 GAP 초 이상이면 다른 차량으로 본다.

2. 트랙을 층으로 쪼개지 않는다. 차량 서명은 한 차량의 시간 변화를 재는 것이라 트랙이 끊기면
   안 된다. 층은 표본마다 붙이고, 서명 단위는 (트랙, 층)이다. 그 조합의 표본이 비연속이어도
   서명은 분포 요약이라 문제없다. 대신 지속시간은 max-min이 아니라 표본수 x dt다.

3. 좌회전 베이 제외. 링크 구간에 베이 표본이 섞인다(Lankershim lane 12에 15만).
   위치 분포로는 못 가른다. 정상 차로도 신호 대기로 같은 자리에 몰리기 때문이다.
   그래서 용도로 가른다: 그 차로에 있던 차가 다음 교차로에서 어디로 가는가.

   좌회전만 본다. 우회전은 전용 차로 없이 맨 오른쪽 통과 차로에서 그냥 한다.
   좌우를 합치면 Lankershim 방향2 lane 4(좌 0%, 우 57%, 회랑 471 m 통과)가 베이로 잘못
   걸려 표본 6.4만이 날아간다. 좌회전율로 보면 베이 66~99%와 통과 0~13%로 갈라져
   LEFT_CUT을 그 사이 아무 데나 둬도 판정이 안 바뀐다.

   전용 우회전 차로가 있으면 이 기준으로 못 잡는다. 그건 짧을 테니 좌회전율이 낮은데
   회랑 통과 길이가 MIN_SPAN 미만인 차로를 따로 찍어 확인한다.

4. 코드값 차로 제외. lane 0과 9999는 차로가 아니라 미상 코드다. 9999는 표본 1122개가
   차량 607대에 흩어져 있고 section_id가 거의 0이며 local_x가 정상 범위 밖으로 나간다.

5. 방향. 간선 층은 주 통행 방향(2, 4)만 쓴다. 방향 1과 3은 간선을 가로지르는 교차 도로라
   표본의 47~85%가 교차로 안이고(간선은 16~23%), local_x가 간선 폭 밖으로 뻗는다.
   교차 도로 차량은 간선으로 합류하느라 대부분 회전하므로 베이 판정 기준이 성립하지 않는다.
   반대로 교차로 층은 전 방향을 쓴다. 교차 방향 진입이 빠지면 교차로가 직진 위주로 편향된다.

출력:
  data/outputs/strata/strata.txt   층별 요약과 차로 판정
  data/outputs/strata/units.csv    (트랙, 층)마다 한 줄. 서명의 단위가 된다
"""

OUT_STRATA = os.path.join("data", "outputs", "strata")
GAP = 2.0            # 트랙 끊기 기준(s)
LEFT_CUT = 0.40      # 다음 교차로 좌회전 비율이 이 위면 좌회전 베이. 판단값
MAIN_DIR = (2, 4)    # 간선 주 통행 방향. 1과 3은 교차 도로
SPAN_FRAC = 0.5      # 통과 차로는 회랑의 이 비율 이상을 지나야 한다. 판단값
MIN_SHARE = 0.01     # 그 방향 링크 표본의 이 비율 미만인 차로는 코드값으로 보고 버린다
BAD_LANES = {0, 9999}
FT = 0.3048
FTS_KMH = FT * 3.6
MS_KMH = 3.6

NGSIM_HW = ["ngsim_us101", "ngsim_i80"]
TGSIM_HW = ["tgsim_i90_94_moving", "tgsim_i90_94_stationary",
            "tgsim_i294_l1", "tgsim_i294_l2", "tgsim_i395"]
NGSIM_URB = {"ngsim_lankershim": "urb_block_wide",
             "ngsim_peachtree": "urb_block_narrow"}


def add_tracks(d, gap=GAP):
    """ID 재사용을 끊어 트랙 번호를 붙인다. vehicle_id와 t로 정렬돼 있어야 한다."""
    t = d["t"].to_numpy(dtype=np.float64)
    v = d["vehicle_id"].to_numpy()
    new = np.empty(len(t), bool)
    new[0] = True
    new[1:] = (v[1:] != v[:-1]) | (np.diff(t) > gap)
    d["trk"] = np.cumsum(new)
    return d


def bumper_gap(d):
    """NGSIM space_headway를 범퍼 간 순간격(m)으로 바꾼다. d에 preceding이 int로 있어야 함.

    space_headway는 앞범퍼 간 거리다. local_y가 앞범퍼 중심이라
    space_headway == |y_lead - y_ego|가 두 사이트에서 100% 성립하는 것으로 확인했다.
    즉 선행차 길이(중앙 4.6 m)가 포함돼 있다. CARLA 쪽은 s - (L_i + L_j)/2로 범퍼 간을
    쓰므로 그대로 비교하면 실측 간격이 4.6 m 크게 잡힌다. 도심 gap p10이 6.7 대 1.4로
    벌어져 보이던 것이 이 정의 차이였다(보정하면 2.1 대 1.4).

    선행차가 그 시각에 안 잡히면 nan. 그건 선행차 없음으로 처리하면 된다.
    """
    lead = (d[["global_time", "vehicle_id", "v_length"]]
            .rename(columns={"vehicle_id": "preceding", "v_length": "L_lead"})
            .drop_duplicates(["global_time", "preceding"]))
    L = d[["global_time", "preceding"]].merge(
        lead, on=["global_time", "preceding"], how="left")["L_lead"].to_numpy()
    return d["space_headway"].to_numpy() * FT - L * FT


def load_ngsim(name):
    cols = ["vehicle_id", "global_time", "v_vel", "lane_id", "int_id",
            "direction", "local_y", "movement"]
    d = pd.read_csv("data/ngsim/%s.csv" % name, usecols=cols)
    d["int_id"] = d["int_id"].fillna(0).astype(int)
    d["lane_id"] = d["lane_id"].fillna(0).astype(int)
    d["direction"] = d["direction"].fillna(0).astype(int)
    d["t"] = d["global_time"] / 1000.0
    d["v"] = d["v_vel"] * FTS_KMH
    d["y"] = d["local_y"] * FT
    d = d.sort_values(["vehicle_id", "t"]).reset_index(drop=True)
    return add_tracks(d)


def load_tgsim(name):
    head = pd.read_csv("data/tgsim/%s.csv" % name, nrows=1)
    idc = "ID" if "ID" in head.columns else "id"
    cols = [idc, "time", "speed_kf"]
    if "run_index" in head.columns:
        cols.append("run_index")
    d = pd.read_csv("data/tgsim/%s.csv" % name, usecols=cols)
    d = d.rename(columns={idc: "vehicle_id", "time": "t", "speed_kf": "v"})
    d["v"] = d["v"].abs() * MS_KMH
    if "run_index" in d.columns:
        # 촬영분이 다르면 다른 차량이다. 안 붙이면 서로 다른 촬영분이 한 트랙으로 이어진다
        d["vehicle_id"] = d["run_index"].astype(str) + "_" + d["vehicle_id"].astype(str)
    d = d.sort_values(["vehicle_id", "t"]).reset_index(drop=True)
    return add_tracks(d)


def lane_purpose(d):
    """(direction, lane_id) -> 다음 교차로에서 회전한 표본 비율.

    movement는 교차로 안에서만 채워져 있다. 그래서 트랙마다 뒤에서 앞으로 채워
    링크 표본에 다음 교차로의 이동 방향을 붙인다.
    """
    mv = d["movement"].where(d["int_id"] > 0)
    nxt = mv.groupby(d["trk"]).bfill()
    link = d[(d["int_id"] == 0) & (~d["lane_id"].isin(BAD_LANES))
             & d["direction"].isin(MAIN_DIR)].copy()
    link["nxt"] = nxt[link.index]
    g = link.groupby(["direction", "lane_id"])
    return pd.DataFrame({
        "표본": g.size(),
        "좌회전율": g["nxt"].apply(lambda s: (s == 2).mean()),
        "우회전율": g["nxt"].apply(lambda s: (s == 3).mean()),
        "통과길이": (g["y"].max() - g["y"].min()),
    })


def intersection_spans(d):
    """int_id -> (y시작, y끝). 교차로까지 거리와 블록 길이를 구하는 데 쓴다."""
    g = d[d["int_id"] > 0].groupby("int_id")["y"]
    return sorted(zip(g.min(), g.max()))


def dist_and_block(y, spans):
    """교차로까지 최단거리와 그 지점이 속한 블록 길이. spans는 y로 정렬돼 있다."""
    dist = np.full(len(y), np.nan)
    block = np.full(len(y), np.nan)
    edges = [s for sp in spans for s in sp]
    for i, (lo, hi) in enumerate(spans):
        dist = np.fmin(dist, np.where((y >= lo) & (y <= hi), 0.0,
                                      np.minimum(np.abs(y - lo), np.abs(y - hi))))
    for a, b in zip(edges[1::2], edges[2::2]):     # 앞 교차로 끝 ~ 다음 교차로 시작
        block = np.where((y > a) & (y < b), b - a, block)
    return dist, block


def track_units(site, stratum, d, dt):
    """(트랙, 층)마다 한 줄. 지속시간은 표본수 x dt다(비연속이라 max-min을 못 쓴다)."""
    if d.empty:
        return []
    g = d.groupby("trk")
    out = pd.DataFrame({
        "n": g.size(),
        "v50": g["v"].median(), "v90": g["v"].quantile(0.9),
        "v_sd": g["v"].std(),
    })
    if "dist" in d.columns:
        out["dist50"] = g["dist"].median()
        out["block"] = g["block"].median()
    out["dur"] = out["n"] * dt
    out["site"] = site
    out["stratum"] = stratum
    out["trk"] = out.index
    return out.reset_index(drop=True).to_dict("records")


def main_strata():
    os.makedirs(OUT_STRATA, exist_ok=True)
    lines = []

    def emit(s):
        print(s, flush=True)
        lines.append(s)

    emit("실측 층화. 트랙은 %g초 간격으로 끊고, 트랙은 쪼개지 않는다." % GAP)
    emit("서명 단위는 (트랙, 층). 좌회전 베이는 다음 교차로 좌회전율 %g 초과로 판정." % LEFT_CUT)

    rows = []
    for name in NGSIM_HW:
        d = load_ngsim(name)
        dt = float(np.median(np.diff(d["t"].to_numpy()[:20000])))
        rows += track_units(name, "hw_link", d, dt)
        emit("  %s: 표본 %d, dt %.2f s" % (name, len(d), dt))

    for name in TGSIM_HW:
        d = load_tgsim(name)
        dt = float(np.median(np.diff(d["t"].to_numpy()[:20000])))
        rows += track_units(name, "hw_link", d, dt)
        emit("  %s: 표본 %d, dt %.2f s" % (name, len(d), dt))

    for name, stratum in NGSIM_URB.items():
        d = load_ngsim(name)
        dt = float(np.median(np.diff(d["t"].to_numpy()[:20000])))
        lp = lane_purpose(d)
        total = lp.groupby("direction")["표본"].transform("sum")
        lp["비중"] = lp["표본"] / total
        corridor = lp.groupby("direction")["통과길이"].transform("max")
        lp["회랑비"] = lp["통과길이"] / corridor
        # 짧은 부가 차로. 좌우 어느 쪽도 아니면서 회랑의 일부만 존재한다(Lankershim lane 31)
        lp["판정"] = np.where(lp["비중"] < MIN_SHARE, "코드값",
                     np.where(lp["좌회전율"] > LEFT_CUT, "좌회전베이",
                     np.where(lp["회랑비"] < SPAN_FRAC, "부가차로", "통과")))
        emit("")
        emit("== %s 차로 판정 (dt %.2f s)" % (name, dt))
        emit(lp.assign(좌회전율=(100 * lp["좌회전율"]).round(0),
                       우회전율=(100 * lp["우회전율"]).round(0),
                       통과길이=lp["통과길이"].round(0),
                       회랑비=(100 * lp["회랑비"]).round(0),
                       비중=(100 * lp["비중"]).round(1)).to_string())
        near = lp[(lp["판정"] != "코드값")
                  & (lp["좌회전율"] - LEFT_CUT).abs().lt(0.20)]
        if len(near):
            emit("  컷 근처(±0.20) 차로: %s  <- 민감도로 확인 필요"
                 % sorted(near.index.tolist()))
        rt = lp[(lp["판정"] == "부가차로") & (lp["우회전율"] > 0.3)]
        if len(rt):
            emit("  전용 우회전 차로로 보이는 부가 차로: %s" % sorted(rt.index.tolist()))

        keep = set(lp.index[lp["판정"] == "통과"])
        nlane = lp[lp["판정"] == "통과"].groupby("direction").size()
        emit("  방향별 통과 차로 수: %s" % nlane.to_dict())

        spans = intersection_spans(d)
        emit("  교차로 %d곳, 블록 길이 %s m"
             % (len(spans), [round(b - a) for a, b in zip(
                 [s[1] for s in spans][:-1], [s[0] for s in spans][1:])]))

        key = pd.Series(list(zip(d["direction"], d["lane_id"])), index=d.index)
        link = d[(d["int_id"] == 0) & key.isin(keep)
                 & d["direction"].isin(MAIN_DIR)].copy()
        junc = d[d["int_id"] > 0].copy()
        for part in (link, junc):
            dd, bb = dist_and_block(part["y"].to_numpy(), spans)
            part["dist"] = dd
            part["block"] = bb
        rows += track_units(name, stratum, link, dt)
        rows += track_units(name, "signal_junction", junc, dt)
        emit("  링크 표본 %d, 교차로 표본 %d" % (len(link), len(junc)))

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_STRATA, "units.csv"), index=False)

    emit("")
    emit("층별 서명 단위 요약. dur은 그 층에 머문 시간(표본수 x dt)이다.")
    g = df.groupby("stratum")
    tot = pd.DataFrame({
        "단위": g.size(), "표본": g["n"].sum(),
        "중앙s": g["dur"].median().round(1),
        "p90s": g["dur"].quantile(0.9).round(1),
        "30s이상%": (100 * g["dur"].apply(lambda s: (s >= 30).mean())).round(0),
        "60s이상%": (100 * g["dur"].apply(lambda s: (s >= 60).mean())).round(0),
        "v50": g["v50"].median().round(1),
    })
    emit(tot.to_string())

    emit("")
    emit("사이트별")
    g = df.groupby(["stratum", "site"])
    emit(pd.DataFrame({
        "단위": g.size(), "표본": g["n"].sum(),
        "중앙s": g["dur"].median().round(1),
        "30s이상%": (100 * g["dur"].apply(lambda s: (s >= 30).mean())).round(0),
        "v50": g["v50"].median().round(1),
    }).to_string())

    dst = os.path.join(OUT_STRATA, "strata.txt")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\nwrote %s\nwrote %s" % (dst, os.path.join(OUT_STRATA, "units.csv")))


# ==========================================================================
# score_strata: OpenDRIVE와 수집물에서 층(교차로/도심/고속)을 매긴다
# ==========================================================================
"""arm 수집물을 실측 참조와 층별로 채점한다. CARLA 불요.

옛 score_arms.py는 폐기된 설계(zone x regime x free/lead)라 건드리지 않고 새로 만들었다.

계측 스택을 실측과 똑같이 맞춘다. 위치를 sEMA(Thiemann 2008, Δ=0.5s) 평활한 뒤 미분해
속도와 가속을 얻는다. CARLA에서 get_velocity()를 안 읽는 이유가 이것이다. 실측은 위치만
있어서 미분으로 얻는데 한쪽만 참값 속도를 쓰면 계측 차이가 결과에 섞인다.

층은 지도에서 붙인다. road_id -> 제한속도는 lane_speed.json(표지 전파), 교차로 여부와 통제는
xodr에서 온다.
  hw_link          60/90 링크
  urb_block        30/40 링크
  signal_junction  신호 통제 교차로

서명 둘 다 낸다.
  무선행차 5성분  [v평균, v산포, a평균, a산포, za]. 앞 FIX초로 길이 고정.
                 길이를 안 맞추면 도심 recall이 0.94에서 0.32로 무너진다(실측에서 확인).
  추종 9성분      같은 선행차 MIN_FOLLOW초 이상 구간. 선행차는 같은 (road_id, lane_id)에서
                 진행방향 앞쪽 최근접.

출력: data/outputs/arms/scores.txt
"""

# 수집물 자리. 채점기가 --src 로 갈아끼운다. 치워둔 arms_8tree 처럼 같은 코드로
# 다른 수집물을 채점할 때 쓴다. 모듈 전역이라 여기만 바꾸면 적재기 전부가 따라온다
ARM_DIR = os.path.join("data", "outputs", "arms")


ARM_DIRS = [ARM_DIR]


def set_arm_dir(p):
    """수집물 자리를 바꾼다. 쉼표로 여럿. 적재 전에 불러야 한다."""
    global ARM_DIR, ARM_DIRS
    ds = [x.strip() for x in p.replace(";", ",").split(",") if x.strip()]
    for d in ds:
        if not os.path.isdir(d):
            raise SystemExit("수집물 자리가 없다: %s" % d)
    ARM_DIR, ARM_DIRS = ds[0], ds
    for i, d in enumerate(ds):
        print("수집물 자리 %d: %s" % (i + 1, d))


_ARMKEY = re.compile(r"^(.+)_Town[0-9A-Za-z]+_s\d+(?:_plan)?\.csv$")


def arm_files(pat="*.csv"):
    """수집물 파일 목록. arm 이 겹치면 앞자리가 통째로 이긴다.

    파일 이름으로 거르면 안 된다. 10벌 재수집이 f25 에 새 (맵, 시드) 칸을 더 만들어서
    8벌판 16개와 10벌판 8개가 한 arm 안에 섞였다. 다른 실험 둘이 한 줄로 평균된다.

    자리가 하나면 그냥 그 자리의 글롭이다.
    """
    if len(ARM_DIRS) == 1:
        return sorted(glob.glob(os.path.join(ARM_DIRS[0], pat)))
    taken, out = set(), []
    for d in ARM_DIRS:
        here = sorted(glob.glob(os.path.join(d, pat)))
        mine = set()
        for f in here:
            m = _ARMKEY.match(os.path.basename(f))
            a = m.group(1) if m else None
            if a in taken:
                continue
            out.append(f)
            if a:
                mine.add(a)
        taken |= mine
    return sorted(out, key=os.path.basename)

# arm 이름 규칙의 단일 출처. 파일 이름은 arm + 꼬리표이고, 적재기마다 이 둘을 따로
# 적어 두면 갈린다. 실제로 갈렸었다. 상황 축만 접두사 매칭을 쓰고 있어서, arm 이름을
# 바꾸자 tm_knob이 tm_knob_domain까지 물어 판이 다섯 배가 되고 메모리에서 죽었다.
#
# _p0은 기본 트리 세트라 이름에서 뗀다. -tree_NN은 세트 안의 한 벌이라 같이 뗀다.
# 나머지 꼬리표는 다른 arm을 뜻하므로 안 뗀다.
GRP_RE = re.compile(r"-tree_\d+")
# 교란 칸. 사다리의 단이 아니라 자를 검증하려고 만든 칸이라 기본 판에서 뺀다. probe로 켠다.
# 폭 표시를 _w\d\d로 받는다. _w05|_w20만 적었더니 나중에 붙인 _w33, _w50이 기본 판에
# 새서 사다리에 끼고 희소화 바닥까지 바꿨다. _ponly 는 조건 뗀 대조군. _p[1-9]에 안 걸려
# 따로 적는다
PROBE_RE = re.compile(r"v30|_w\d\d|_p[1-9]|_ponly|_rnd|_f\d\d")


def grp_of(arm):
    """파일 이름의 arm을 판에서 쓰는 칸 이름으로. 적재기는 전부 이걸 쓴다."""
    return GRP_RE.sub("", arm).replace("_p0", "")


def is_probe(grp):
    """교란 칸인가. grp_of를 통과한 이름을 넣어야 한다."""
    return bool(PROBE_RE.search(grp))


LANE_JSON = os.path.join("data", "outputs", "carla_speed", "lane_speed.json")
RING_JSON = os.path.join("data", "outputs", "map_partition", "roundabout.json")
CEIL = os.path.join("data", "outputs", "ceiling")
DT = 0.1
FIX = 10.0            # 무선행차 서명 길이 고정(s). 실측 천장 측정과 같은 값
MIN_FOLLOW = 5.0
V_MIN = 2.0           # m/s. THW = gap/v라 v->0에서 터진다. 판단값이다.
                      # 실측 스윕(lankershim): 컷 없으면 THW max 3.9e8초. 0.5에서 643초,
                      # 2.0에서 175초, 5.0에서 87초. 발산 자체는 0.5로 이미 죽고 0.5->2.0은
                      # 꼬리 정리(sd 8.4 -> 5.6)다. 엘보 없음. 2가 특별한 지점은 아니다.
                      # 서명이 thw_mean/thw_sd를 쓰니 꼬리에 민감해 높은 컷이 낫다는 논거는
                      # 서지만 3이나 5도 같은 논거를 만족한다. 민감도 축으로 돌려야 한다.
                      # 표본 대가: 도심 62.8% -> 57.8%, 고속 97.2% -> 91.5%
K = 3
# 회차 고리 판별. 셋을 다 만족해야 한다. 방향전환만 보면 외곽 대곡선까지 걸린다
# (Town03 road 53은 967 m에 179도지만 반경 60 m라 그냥 굽은 길이다. 그걸 빼면 Town06
# 프레임 8%가 날아갔다). 세 값 사이가 넓게 비어 있어 그 안이면 어디를 잡아도 같은 집합이
# 나온다. 회차 고리 반경 11.9~15.7 대 외곽 곡선 39.9~60.1, 길이 79~122 대 교차로 연결로 10~25
LOOP_TURN = math.radians(150.0)
LOOP_RADIUS = 25.0     # m. 이보다 조여 돌면 전방 원뿔이 앞차를 놓친다
LOOP_LEN = 50.0        # m. 교차로 안 좌우회전 연결로(10~25 m)를 고리로 안 세려는 하한
DEFAULT_LIMIT = 30.0   # km/h. 표지가 없으면 CARLA가 이 값을 준다. 관측으로 확인한 값이다
SIG_FREE = ["v_mean", "v_sd", "a_mean", "a_sd", "za"]
SIG_FOLLOW = ["v_mean", "v_sd", "a_mean", "a_sd", "thw_mean", "thw_sd",
              "dv_mean", "dv_sd", "z"]
REF_OF = {"hw_link": ["hw_link"],
          "urb_block": ["urb_block_wide", "urb_block_narrow"],
          "signal_junction": ["signal_junction"],
          # 신호가 아닌 교차로에 대응할 실측이 없다. NGSIM 두 회랑의 교차로는 전부 신호다.
          # 빈 목록이라 참조가 필요한 채점에서는 빠지고 arm 간 비교와 다양성에만 남는다
          "unsig_junction": [],
          "stop_junction": []}
# ceiling.csv의 signature 열과 맞추는 접두어. 앞글자 잘라 쓰면 안 된다.
# 저쪽은 "추종 9성분"이라 공백이 있어서 "추종9"로 startswith하면 조용히 nan이 됐다
CEIL_KEY = {"무선행차5": "무선행차", "추종9": "추종"}


def ring_roads(town):
    """회전교차로 순환로 도로 집합. map_roundabout.py 산출물.

    빼는 이유가 표본 크기가 아니다(urb_block 오염이 0.2%뿐). 계측 기계가 안 맞는다.
    선행차 판정이 진행방향 앞쪽 원뿔인데, 회전교차로 진입에서 양보 대상은 왼쪽에서
    순환해 오는 차라 앞차가 아니다. thw_eq, start_lag, accel이 전부 추종 관계를
    전제하는데 거기서는 그게 지배 관계가 아니다.

    그리고 CARLA 전 맵에 쓸 만한 회전교차로가 Town03 중앙 하나뿐이라 맵 효과와
    층 효과가 안 나뉜다. 실측 참조도 없다.
    """
    if not os.path.exists(RING_JSON):
        return set()
    with open(RING_JSON, encoding="utf-8") as fh:
        return set(json.load(fh).get(town, []))


def road_map(town):
    """road_id -> 제한속도, road_id -> 통제 방식."""
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
        kind = next((t for t in ("signal", "stop", "yield") if t in types),
                    "uncontrolled")
        for rid in cr:
            ctrl[rid] = kind
    # 교차로 연결로는 표지가 안 붙어 있어 lane_speed에 값이 없는 것이 많다(Town03 100개,
    # Town06 40개). 관측 덤프에도 없어서 전파 실패가 아니라 애초에 값이 붙은 적이 없다.
    # 진입로에서 물려받는다. 진입로가 값을 주는 경우 전부 한 종류라 충돌이 0건이었다.
    # 두 종 이상이면 안 채운다. 임의로 고르면 그게 판단값이 된다.
    # 연결로가 연결로를 물고 있는 사슬이 있어 안 변할 때까지 돈다
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
    # 그래도 남은 미상은 30으로 본다. CARLA 기본값이다. 관측 덤프가 그렇게 말한다.
    # Town03 도로 245개가 30, 1개만 90이고 xodr의 type_speed(80.5, 88.5)는 차가 보는 값이
    # 아니다. Town10HD는 88개가 전부 미상이라 이 기본값 없이는 층이 하나도 안 선다
    for rid in roads:
        if lim.get(rid) is None:
            lim[rid] = DEFAULT_LIMIT
    # 무신호 교차로의 직진 연결로는 링크로 돌린다. 곡률이 없으면 그 차는 안 돈다는 뜻이고,
    # 신호도 없으니 정지도 양보도 안 한다. 위상은 교차로 안이지만 거동은 링크 주행이다.
    # 신호 교차로는 직진이어도 적신호에 서므로 그대로 둔다. Town03 8시 방향의 40 m 직선
    # 연결로가 무신호 교차로로 잡히던 것이 이 경우다
    for j in root.iter("junction"):
        conns = j.findall("connection")
        cr = {c.get("connectingRoad") for c in conns} & set(roads)
        # 다리가 둘이면 교차로가 아니라 도로를 이어붙인 것이다. OpenDRIVE가 램프 합류나
        # 도로 연속을 junction으로 감싼다. Town06 junction 34개 중 16개가 그렇다
        two_leg = len({c.get("incomingRoad") for c in conns}) <= 2
        for rid in cr:
            if ctrl[rid] not in ("uncontrolled",) and not two_leg:
                continue
            straight = all(g.find("arc") is None
                           for g in roads[rid].iter("geometry"))
            if two_leg or straight:
                ctrl[rid] = "link"

    # 회차 고리는 통제 방식과 무관하게 뺀다. 로터리와 같은 이유다. 선행차 판정이 진행방향
    # 앞쪽 원뿔(횡 2 m)인데 조여 돌면 앞차가 원뿔 밖으로 나간다. Town03 하나, Town06 둘이다
    for rid, r in roads.items():
        arcs = [g for g in r.iter("geometry") if g.find("arc") is not None]
        if not arcs:
            continue
        turn = sum(abs(float(g.find("arc").get("curvature"))) * float(g.get("length"))
                   for g in arcs)
        rad = min(1.0 / abs(float(g.find("arc").get("curvature"))) for g in arcs)
        if turn >= LOOP_TURN and rad <= LOOP_RADIUS \
                and float(r.get("length")) >= LOOP_LEN:
            ctrl[rid] = "loop"

    # 로터리 순환로와 진출입 연결로. 진입에서 양보 대상이 앞차가 아니라 왼쪽에서 순환해
    # 오는 차라 우리 계측이 안 맞는다. 접속 링크(진입로 본선)까지는 안 뺀다. 그 도로는
    # 대부분 구간이 로터리와 무관한 일반 주행이라 통째로 빼면 표본을 크게 잃는다
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
    """road_id -> 층. 모르는 것은 None이고 채점에서 빠진다.

    교차로를 통제 방식으로 가르는 이유: 실측 signal_junction은 lankershim과 peachtree의
    신호 교차로다. 무신호 교차로를 거기 섞으면 다른 것을 비교하게 된다. 그래서 층을 나누되
    빼지는 않는다. Town03 프레임의 9.4%가 무신호 교차로였다.

    unsig_junction은 대응할 실측이 없다. 현실성(P/R)은 못 재고 arm 간 비교와 다양성만
    된다. REF_OF가 빈 목록이라 참조가 필요한 경로에서는 자동으로 빠진다.

    30/40 제한을 교차로에도 거는 이유는 도심 교차로만 보려는 것이다. Town06에 제한속도 60인
    신호 교차로가 6개 있는데(프레임 1.2%) 실측 대응이 없어 뺀다. 조건문 부작용이 아니라
    명시적 판단이다.
    """
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
    # stop과 yield를 갈라 쓴 근거는 Town07 측정이다(carla_sign_response). 접근 단독 사건의
    # 완전정지율이 stop 23%, yield 2%, 무통제 0%다. yield는 무통제와 사실상 같아 합치고
    # stop만 따로 둔다. stop도 23%라 전원 정지는 아니다. 2방향 정지에서 주도로는 안 서기
    # 때문으로 보이는데 xodr이 접근별 표지를 안 줘서 확정은 못 한다
    if c == "stop":
        return "stop_junction"
    if c in ("uncontrolled", "yield"):
        return "unsig_junction"
    return None


def kinematics(d):
    """실측과 같은 스택. 차량마다 위치를 sEMA 평활한 뒤 미분."""
    v = np.full(len(d), np.nan)
    a = np.full(len(d), np.nan)
    for _, idx in d.groupby("vid").indices.items():
        if len(idx) < 5:
            continue
        xs = _sema(d["x"].to_numpy()[idx], DT)
        ys = _sema(d["y"].to_numpy()[idx], DT)
        sp = np.hypot(np.gradient(xs, DT), np.gradient(ys, DT))
        v[idx] = sp
        a[idx] = np.gradient(sp, DT)
    d["v"] = v
    d["a"] = a
    return d


def first_run(d, key, fix, dt, seed=0, salt=0):
    """(key)마다 시간이 연속인 첫 구간에서 앞 fix초. 끊긴 표본을 이어 붙이면 안 된다.

    층으로 고른 표본은 차량이 층을 드나들며 시간상 끊긴다. 그대로 묶으면 경계에서 가속
    부호 전환이 가짜로 세어지고 미분값도 튄다.

    seed는 창 위치만 바꾼다. arm당 단위가 차량 수(120)로 묶여 있어 창 뽑기 운이 P/R에
    얼마나 실리는지 재려고 뺐다. seed=0은 스트림 첫 값이라 예전 값과 같다.
    """
    d = d.sort_values([key, "t"]).reset_index(drop=True)
    k = d[key].to_numpy()
    t = d["t"].to_numpy()
    new = np.empty(len(d), bool)
    new[0] = True
    new[1:] = (k[1:] != k[:-1]) | (np.diff(t) > 1.5 * dt)
    run = np.cumsum(new)
    need = int(round(fix / dt))
    cnt = pd.Series(run).groupby(run).transform("size").to_numpy()
    rank = pd.Series(run).groupby(run).cumcount().to_numpy()
    # 창을 구간 안 무작위 위치에서 딴다. 항상 앞에서 따면 진입 직후 거동만 보게 된다.
    # 시작점은 run 번호로 결정론적이라 재실행해도 같은 값이 나온다
    # run 고유값에서만 뽑아 되돌린다. 행마다 RandomState를 만들면 메르센 트위스터 상태
    # 624워드 초기화가 행 수만큼 돈다. 100만 행에 120초 넘김. 같은 run이면 같은 난수라
    # 고유값만 계산해도 값은 비트 단위로 같다
    uniq, inv = np.unique(run, return_inverse=True)
    # salt는 파일(CARLA 시드)마다 달라야 한다. run 번호는 파일마다 1부터 다시 세므로
    # salt가 없으면 다른 실행의 run 5번이 같은 난수를 받아 창 위치가 공통으로 흔들린다.
    # 그러면 시드를 합쳐도 평균이 안 되고 표준편차가 그대로 남는다
    vals = np.fromiter(
        (np.random.RandomState([int(r) & 0x7fffffff, int(salt)]).rand(seed + 1)[-1]
         for r in uniq), float, len(uniq))
    start = vals[inv]
    off = np.floor(start * np.maximum(cnt - need, 0)).astype(int)
    rank = rank - off
    # rank >= 0을 빼먹으면 구간 앞부분이 통째로 남아 창 길이가 off+need가 된다.
    # 그러면 관측이 길수록 구간이 길고 off도 커져 단위 길이가 늘어난다. 창 길이만
    # 바꿔도 recall이 흔들렸던 원인
    m = (cnt >= need) & (rank >= 0) & (rank < need)
    d = d[m].copy()
    if d.empty:
        return d
    d["run"] = run[m]
    # key마다 첫 구간만 쓴다. 한 차량이 여러 점이 되면 독립성이 깨진다(03의 미정 3)
    first = d.groupby(key)["run"].transform("min")
    return d[d["run"] == first]


def free_units(d, fix=FIX, seed=0, salt=0):
    """(차량, 층)마다 무선행차 서명 한 줄. 구간 안 무작위 위치에서 fix초만 쓴다."""
    d = d[d["v"].notna()].copy()
    if d.empty:
        return pd.DataFrame()
    d["u"] = d["vid"].astype(str) + "|" + d["stratum"]
    d = first_run(d, "u", fix, DT, seed, salt)
    if d.empty:
        return pd.DataFrame()
    g = d.groupby("u")
    out = pd.DataFrame({
        "n": g.size(), "stratum": g["stratum"].first(),
        "v_mean": g["v"].mean(), "v_sd": g["v"].std(),
        "a_mean": g["a"].mean(), "a_sd": g["a"].std()})
    sign = np.sign(d["a"].to_numpy())
    u = d["u"].to_numpy()
    flip = np.zeros(len(d))
    flip[1:] = (sign[1:] != sign[:-1]) & (u[1:] == u[:-1])
    out["za"] = pd.Series(flip).groupby(u).sum().to_numpy() / (out["n"] * DT) * 10.0
    return out.reset_index(drop=True)


def add_leader(d, lead_max=100.0):
    """같은 시각 전체에서 진행방향 앞쪽 최근접 차량. 순간격과 선행차 속도.

    road_id, lane_id로 묶으면 안 된다. CARLA 도로는 중앙 35 m라 조금만 떨어져도 선행차가
    다음 도로에 있어 통째로 안 잡힌다. 실측 NGSIM preceding은 회랑 전체를 보므로 그렇게
    묶으면 실측과 규칙이 달라진다. 대신 자차 진행방향 원뿔(종 lead_max, 횡 2 m)로 찾는다.

    gap은 범퍼 간이다. 실측 space_headway는 앞범퍼 간이라 그쪽에서 선행차 길이를 뺀다
    (build_strata.bumper_gap).
    """
    gap = np.full(len(d), np.nan)
    vlead = np.full(len(d), np.nan)
    pos = d.reset_index(drop=True)
    arr = pos[["x", "y", "yaw", "v", "length"]].to_numpy(float)
    for _, idx in pos.groupby("t").indices.items():
        if len(idx) < 2:
            continue
        p = arr[idx]
        for i in range(len(idx)):
            yaw = np.radians(p[i, 2])
            fx, fy = np.cos(yaw), -np.sin(yaw)          # y 부호 반전 좌표계
            dx, dy = p[:, 0] - p[i, 0], p[:, 1] - p[i, 1]
            s = dx * fx + dy * fy
            lat = np.abs(-dx * fy + dy * fx)
            ok = (s > 0.5) & (s <= lead_max) & (lat < 2.0)
            if not ok.any():
                continue
            j = np.where(ok)[0][int(np.argmin(s[ok]))]
            gap[idx[i]] = s[j] - 0.5 * (p[i, 4] + p[j, 4])
            vlead[idx[i]] = p[j, 3]
    d = pos
    d["gap"] = gap
    d["v_lead"] = vlead
    return d


def follow_units(d):
    """추종 구간마다 9성분 서명 한 줄."""
    d = d[(d["gap"] > 0.1) & (d["v"] >= V_MIN) & d["v_lead"].notna()].copy()
    if d.empty:
        return pd.DataFrame()
    d = d.sort_values(["vid", "t"]).reset_index(drop=True)
    t = d["t"].to_numpy()
    vid = d["vid"].to_numpy()
    st = d["stratum"].to_numpy()
    new = np.empty(len(d), bool)
    new[0] = True
    new[1:] = (vid[1:] != vid[:-1]) | (st[1:] != st[:-1]) | (np.diff(t) > 2 * DT)
    d["seg"] = np.cumsum(new)
    d["thw"] = d["gap"] / d["v"]
    d["dv"] = d["v_lead"] - d["v"]
    g = d.groupby("seg")
    out = pd.DataFrame({
        "n": g.size(), "stratum": g["stratum"].first(),
        "v_mean": g["v"].mean(), "v_sd": g["v"].std(),
        "a_mean": g["a"].mean(), "a_sd": g["a"].std(),
        "thw_mean": g["thw"].mean(), "thw_sd": g["thw"].std(),
        "dv_mean": g["dv"].mean(), "dv_sd": g["dv"].std()})
    sign = np.sign(d["dv"].to_numpy())
    seg = d["seg"].to_numpy()
    flip = np.zeros(len(d))
    flip[1:] = (sign[1:] != sign[:-1]) & (seg[1:] == seg[:-1])
    out["z"] = pd.Series(flip).groupby(seg).sum().to_numpy() / (out["n"] * DT) * 10.0
    out["dur"] = out["n"] * DT
    return out[out["dur"] >= MIN_FOLLOW].reset_index(drop=True)


def main_lanes():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=0.0,
                    help="arm 궤적을 앞 N초로 자른다. 0이면 안 자름")
    ap.add_argument("--reps", type=int, default=1,
                    help="무선행차 창 위치 시드를 N개 돌려 평균과 표준편차를 낸다")
    a = ap.parse_args()
    lines = []

    def emit(s):
        print(s, flush=True)
        lines.append(s)

    ref_free = pd.read_csv(os.path.join(CEIL, "free_units.csv"))
    ref_fol = pd.read_csv(os.path.join(CEIL, "segments.csv"))
    ceil = pd.read_csv(os.path.join(CEIL, "ceiling.csv"))
    ref_free = ref_free[ref_free["dur"] >= FIX]

    emit("arm 층별 채점. 계측 스택은 실측과 동일(위치 sEMA 평활 후 미분). k=%d." % K)
    emit("무선행차 서명은 arm을 앞 %g초로 고정. 참조는 길이 하한만 걸었다." % FIX)
    emit("**검토 필수**: 엄밀히는 참조도 앞 %g초로 다시 잘라야 대칭이 된다." % FIX)
    emit("관측창: %s" % ("앞 %g초로 통일" % a.secs if a.secs else "자르지 않음(arm마다 다름)"))
    emit("")
    emit("무선행차 창 위치 시드 %d개. 추종은 창 뽑기가 없어 1회." % a.reps)
    emit("CARLA 시드는 합쳐서 한 arm 다양체로 본다. bt_tree_shared은 트리마다 따로 둔다.")
    emit("")
    emit("%-26s %-8s %-16s %-9s %14s %14s %8s %8s %3s %6s"
         % ("arm", "town", "stratum", "서명", "precision", "recall",
            "천장P", "천장R", "시드", "n_arm"))

    # (arm, town)으로 묶어 시드를 합친다. 무선행차 단위가 차량마다 하나라 한 실행의
    # arm 표본이 차량 수(120)에 묶이고, 그 상태에서 recall 표준편차가 0.09~0.16이라
    # arm 간 차이가 안 갈렸다. 실측 참조가 urb_block_wide 914라 8시드를 합쳐야 맞는다.
    # bt_tree_shared은 파일명에 트리가 박혀 있어 자동으로 트리마다 다른 그룹이 된다. 트리를
    # 합치면 차량 간 다양성이 없는 arm에 실행 간 차이가 그것처럼 들어온다
    groups = {}
    for f in sorted(glob.glob(os.path.join(ARM_DIR, "*.csv"))):
        m = re.match(r"(.+)_(Town[0-9A-Za-z]+)_s(\d+)\.csv$", os.path.basename(f))
        if m:
            groups.setdefault((m.group(1), m.group(2)), []).append((int(m.group(3)), f))

    KEEP = ["vid", "t", "x", "y", "yaw", "length", "v", "a", "stratum"]
    for (arm, town), items in sorted(groups.items()):
        lim, ctrl = road_map(town)
        parts = []
        for seed, f in sorted(items):
            d = pd.read_csv(f)
            # 관측창을 arm끼리 맞춘다. 창이 길면 무선행차 창 개수가 그냥 늘어 recall이
            # 표본 수만으로 올라간다. arm 비교에서 이건 신호가 아니다
            if a.secs:
                d = d[d["t"] < a.secs].reset_index(drop=True)
            # 미분을 먼저 한다. 층으로 거른 뒤 미분하면 궤적에 구멍이 생겨 그 경계에서
            # 가속이 터진다(Town06 hw_link에서 a_sd가 실측의 9배로 나왔다)
            d = kinematics(d)
            d["stratum"] = [stratum_of(lim, ctrl, r) for r in d["road_id"]]
            d = d[d["stratum"].notna()].reset_index(drop=True)
            if d.empty:
                continue
            # 시드끼리 vid도 t도 겹친다. 단위를 시드별로 뽑고 나중에 이어붙이는 이유가
            # 이것이다. 프레임을 먼저 합치면 add_leader가 다른 실행의 차를 선행차로
            # 잡고 free_units는 다른 실행의 궤적을 한 차량으로 잇는다
            d["vid"] = d["vid"].astype(str) + "#" + str(seed)
            parts.append(d[KEEP])
        if not parts:
            continue
        n_seed = len(parts)

        def report(A_dfs, cols, ref_df, tag):
            """A_dfs는 창 시드마다 하나. 여러 개면 평균과 표준편차로 낸다."""
            for st in sorted(set().union(*[set(x["stratum"]) for x in A_dfs])):
                R = ref_df[ref_df["stratum"].isin(REF_OF[st])][cols].to_numpy(float)
                if len(R) < K + 1:
                    continue
                rs = []
                for A_df in A_dfs:
                    A = A_df[A_df["stratum"] == st][cols].to_numpy(float)
                    if len(A) < K + 1:
                        continue
                    rs.append(precision_recall(R, A, k=K))
                if not rs:
                    continue
                p = np.array([r["precision"] for r in rs])
                q = np.array([r["recall"] for r in rs])
                c = ceil[ceil["stratum"].isin(REF_OF[st])
                         & ceil["signature"].str.startswith(CEIL_KEY[tag])]
                pm = "%.3f+-%.3f" % (p.mean(), p.std(ddof=1)) if len(p) > 1 \
                    else "%.3f" % p.mean()
                qm = "%.3f+-%.3f" % (q.mean(), q.std(ddof=1)) if len(q) > 1 \
                    else "%.3f" % q.mean()
                emit("%-26s %-8s %-16s %-9s %14s %14s %8.3f %8.3f %3d %6d"
                     % (arm, town, st, tag, pm, qm,
                        c["precision"].mean() if len(c) else np.nan,
                        c["recall"].mean() if len(c) else np.nan, n_seed,
                        int(np.mean([r["n_arm"] for r in rs]))))

        # 무선행차만 창 위치 시드에 의존한다. 추종은 구간을 통째로 써서 무관
        report([pd.concat([free_units(p, seed=s) for p in parts], ignore_index=True)
                for s in range(a.reps)], SIG_FREE, ref_free, "무선행차5")
        report([pd.concat([follow_units(add_leader(p)) for p in parts],
                          ignore_index=True)], SIG_FOLLOW, ref_fol, "추종9")

    dst = os.path.join(ARM_DIR, "scores.txt")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\nwrote %s" % dst)


# ==========================================================================
# score_segments: 고정 거리 자르기, 다섯 숫자 서명, 실측 다섯 종 로더
# ==========================================================================
"""고정 거리 단위로 채점한다. CARLA 불요(수집물 CSV만).

단위를 이렇게 잡는 이유. 차 한 대를 통째로 요약한 서명은 그 차가 지나온 경로의 함수다. 그런데
경로가 통제되지 않는다. 같은 시드, 같은 스폰, set_random_device_seed도 같은데 tm_stock과
tm_knob은 차량 120대 중 한 대도 같은 길을 안 갔다(접두 일치 중앙 0.01). arm이 속도를
바꾸면 교차로 도착 순서가 바뀌고 같은 난수열을 다른 순서로 꺼내 쓴다. 0.9.10 egg에 경로
지정 API가 없어(spec/carla_egg.json) 경로를 맞출 방법도 없다.

그래서 경로 대신 관측을 고정한다. 실측이 원래 그렇게 생겼다. NGSIM은 한 구간에 카메라를
걸어둔 것이라 모든 차가 같은 도로를 지난다. 우리는 CARLA 차를 맵 전체에 풀어놓고 차량당
도로 52개를 지나게 했다. 고정 카메라를 떠돌이 함대와 비교한 셈이다.

단위는 층이 안 바뀌는 동안 이동한 L미터다. 도로 경계로는 안 끊는다. OpenDRIVE가 직선
링크도 road_id를 여러 개로 쪼개는데 그건 거동과 무관한 분할이다. 앞 판본은 road 하나를
단위로 삼아서 실측 644 m와 CARLA 84 m를 같은 서명으로 비교했다. 통과 시간이 73초 대 7.9초라
v산포가 다를 수밖에 없었고, 고속 precision 0.02가 거기서 나왔을 가능성이 크다.

교차로만 예외다. 구간이 21~47 m라 L이 안 나온다. 진입과 진출이 자연 경계이므로 통과 전체가
한 단위다.

층은 셋이다. 교차로를 통제 방식으로 안 가른다. 실측 참조가 신호 상태를 모르기 때문이다.
NGSIM에 신호 컬럼이 없어 int_id > 0이 전부다. 참조가 못 가르는 것을 arm 쪽만 가르면 비대칭이라
통제 방식은 열로만 남기고 민감도에서 본다. 고속 인접 여부도 같은 이유로 열이다.

서명은 자차 좌표 5성분이다. 선행차 좌표(THW, dv)를 넣으면 선행차 유무로 다시 분할해야 하는데
선행차 유무는 자극이지 행동이 아니다. 부수적으로 TGSIM처럼 선행차 컬럼이 없는 자료도 같은
서명으로 들어올 수 있게 된다.

출력: data/outputs/segments/segments.txt
"""

OUT = os.path.join("data", "outputs", "segments")
DT = 0.1
K = 3
SIG = ["v_mean", "v_sd", "a_mean", "a_sd", "za"]
# 층별 창 길이(m). None이면 통과 전체가 한 단위다.
# 도심 40은 Town03이 제약이다(60이면 자격 구간이 26%로 떨어지고 40이면 53%). 고속 80은
# Town06에서 68%가 산다. 양쪽 구간 길이 분포가 겹치는 데서 골랐고 민감도 축이다.
#
# 고정 창은 양쪽에서 트랙을 자른다. 길면 그 거리를 못 가는 느린 차가 빠지고, 짧으면
# MIN_FR을 못 채우는 빠른 차가 빠진다. 교차로에 창 15/20/25/30/40을 씌워 재봤을 때 트랙
# 생존율이 0.45/0.59/0.71/0.77/0.75, 참조 중앙속도가 18.4/22.2/24.8/26.9/30.6 km/h였다.
# 속도가 창 길이를 따라 움직이는 게 그 편향의 크기다. 링크 층 40/80도 같은 성질을 탄다.
#
# 빠지는 게 무작위가 아니다. inD loc1에서 살아남은 run이 32.2 km/h에 정지율 5.3%인데
# 빠진 run은 14.7 km/h에 17.8%다. 무신호 교차로에서 양보하는 차가 그 차다. NGSIM은
# 반대로 빠른 쪽(83.5 대 35.2)이 빠진다. 창 하나로 양쪽을 다 없애지는 못한다.
#
# 교차로만 통과 전체다. 고정거리 창을 씌워봤다가 되돌렸다.
#
# 되돌린 이유. 교차로는 크기가 제각각이다. Town03 본체 통과가 8.5~38.8 m로 흩어지고 그
# 차이가 차로 수와 형상에서 온다. 30 m로 고정하면 작은 교차로는 창을 하나도 못 만들고
# (통과의 74%가 버려졌다) 큰 교차로는 통과가 둘로 잘려 어느 창도 조작 전체를 안 담는다.
# 둘 다 교차로 통과라는 현상을 왜곡한다. 접근부와 유출부를 붙이던 규칙(widen_junction)을 끈
# 것도 같이 작용한다. 붙여 놓으면 교차로 run 길이가 도로가 아니라 촬영 범위가 정하는
# 값이 되는데(중앙 inD 65~104, SinD 50~97, foggy 70, NGSIM 110, CARLA 88~163 m), 지금은
# 지도가 선언한 본체만 쓰므로 run 길이가 다시 교차로 크기다.
#
# 고정거리 창을 썼던 이유 둘 중 하나는 사라졌다. 절단 규칙이 촬영 전체가 교차로인
# 사이트(inD, SinD)를 통째로 버리던 문제다. 트랙 하나가 run 하나라 첫 run이면서 끝
# run이라 전부 잘린 표본으로 버려졌고 inD가 교차로 참조에 6단위만 기여했다. 절단을 run
# 3개 이상인 트랙에만 걸어 고쳤다. 남은 이유는 성분 일부가 창 길이를 탄다는 것인데,
# 재보니 아주 짧은 통과에서만 생기는 바닥 효과다. 길이 사분위별 v_sd가 11 m에서
# 0.218이고 18 m를 넘으면 0.55~0.64로 평평해진다. 길이 상관도 v_sd +0.31, a_sd +0.25이고
# v_mean과 a_mean은 0에 가깝다.
#
# 최소 길이를 따로 안 건다. 짧은 통과의 v_sd가 낮은 것은 측정 결함이 아니라 작은
# 교차로에서 실제로 덜 흔들리기 때문이다. 그걸 버리면 큰 교차로만 남아 30 m 고정창이
# 통과의 74%를 버리던 것과 같은 편향이 된다. 통계적으로 필요한 하한은 MIN_FR이 이미
# 걸고 있다. 프레임 15개를 요구하므로 Town03에서 최소 길이가 5.0 m로 잡힌다.
#
# za만 짧은 통과에서 위로 퍼진다(p95가 최단 분위 19.7 대 최장 분위 10.8). 100 m 환산의
# 분모가 작아서인데, 최댓값은 중간 분위에서 나오고 중앙값은 전 분위 0이라 구조적 폭발은
# 아니다. 성분별로 보고할 때 이 성질을 밝히고 읽는다.
LEN = {"hw_link": 80.0, "urb_block": 40.0, "junction": None}
MIN_L = {"junction": float(os.environ.get("MIN_L_J", 0.0))}
MIN_FR = int(os.environ.get("MIN_FR", 15))
# 창 최소 프레임. 산포를 추정할 표본 수를 보장하려는 것인데, 고정거리 창에서는 이게
# 속도 상한이 된다. 창 L을 v로 지나면 프레임이 N = L/(v*dt)개다. N >= MIN_FR을 걸면
#
#     v <= L / (MIN_FR * dt)
#
# 10 Hz에 MIN_FR=30이면 30 m 창은 36 km/h, 40 m는 48, 80 m는 96 km/h가 상한이다.
# 그보다 빠른 차는 그 층에서 구조적으로 못 들어온다. 실제로 ind_loc4(54 km/h)가 30 m
# 창에서 25%만 살고, 그 사이트 도심 링크(56 km/h)가 40 m 창에서 32%, TGSIM
# i294_l2(99 km/h)가 80 m 창에서 64%만 산다.
#
# inD가 25 Hz이던 동안에는 이 상한이 안 보였다. 프레임 수 고정이라 inD에서만 1.2초였고
# 상한이 90 km/h였다. 10 Hz로 솎아 CARLA와 맞추자 드러났다.
#
# 그래서 MIN_FR과 LEN은 따로 못 고른다. 둘 다 민감도 축이고 환경변수로 바꾼다.
#
# 15인 이유는 평탄역이다. L=30에서 MIN_FR 30/20/15/10에 대해 단위 26616/47662/54210/54759,
# 최악 사이트 트랙생존 0.25/0.72/0.91/0.91, 참조 중앙속도 23.2/34.2/37.0/37.2다.
# 15와 10이 사실상 같아서 그 아래 어디를 잡아도 결과가 같다. 30은 평탄역 밖이다.
#
# 상한이 풀리자 창 길이가 참조를 안 바꾼다. MIN_FR=15에서 L=30/40/50의 참조 속도가
# 37.0/36.1/37.0으로 붙는다. 앞서 창을 따라 12.0에서 29.2까지 끌려다니던 건 창의
# 효과가 아니라 속도 상한이 만든 인공물이었다.
#
# 1.5초 15표본으로 산포를 재는 게 성기지만, 10(1초)으로 낮춰도 결과가 같다는 것이
# 그 성김이 결론을 안 흔든다는 증거다.
MIN_UNIT = 20      # 층마다 최소 단위 수
EPS_STEP = 0.05    # m. 프레임당 이동이 이 아래면 앞 프레임과 같은 위치로 본다. 10 Hz에서
                   # 0.5 m/s이고 다른 스크립트가 쓰는 정지 기준과 같은 값이다. 민감도 축
A_MAX = 8.0        # m/s^2. 이 밖은 계측 이상치. 실측 peachtree의 a_sd가 8.78로 나왔는데
                   # 승용차 제동 한계가 대략 8이라 물리량이 아니다. 양쪽에 똑같이 건다.
                   # 한쪽에만 걸면 계측 스택이 갈려서 그 차이가 arm 차이로 잡힌다
JUNC = {"signal_junction": "signal", "stop_junction": "stop",
        "unsig_junction": "unsig"}


# 교차로 본체에 경로를 따라 붙이는 거리(m). 기본 0이다.
#
# 끈 이유. 판정 불확실성 보정으로 넣었는데 재보니 층을 갈아엎고 있었다. 40이면 TGSIM
# foggy_bottom이 교차로 9.2%에서 68.8%로, NGSIM이 4.6%에서 15.2%로 간다. 도심 참조가
# 사이트 둘뿐인데 그중 하나가 3분의 2를 잃는다. CARLA는 통과 길이 중앙이 25.6 m에서
# 161.4 m로, 도심 단위가 1509개에서 622개로 준다.
#
# 그리고 보정이 필요한 자리가 없다. CARLA는 지도가 선언하고, inD와 SinD는 사이트 전체가
# 교차로라 JUNCTION_SITE가 처리하며, NGSIM은 자료가 준 int_id 라벨이다. 좌표 근사인
# foggy_bottom 하나만 불확실한데 거기서 40은 보정이 아니라 파괴다.
#
# 민감도 축으로 남긴다. JUNC_D=20/40으로 흔들어 결론이 안 바뀌는지 본다
JUNC_D = float(os.environ.get("JUNC_D", 0.0))

# inD와 SinD를 통째 교차로로 본다. 사이트 단위 판단이다.
#
# 근거. 그 촬영들은 교차로 하나를 화면 가운데 놓고 찍은 것이지 회랑을 찍은 게 아니다.
# 교차로 밖으로 담긴 18~66 m는 회랑이 아니라 접근부와 유출부다. 무신호(inD)는 양보 감속
# 구간이고 신호(SinD)는 대기열이다. 실제로 JUNC_D=40이면 SinD 85.7~99.8%, inD loc1~3이
# 99.6~100%가 이미 교차로로 잡힌다. 남는 건 inD loc4 하나뿐이라 규칙이 거의 다 한 일을
# 사이트 단위로 마무리하는 것.
#
# 판단인 지점을 밝힌다. inD loc4(aseag)는 자기 프로파일이 32 m에서 평평해지므로
# 40 m 밖을 링크라 부를 근거도 있다. 그걸 교차로로 넘기는 건 방어적 선택이지 유일한
# 정답이 아니다. 링크 참조에서 1,322단위(도심 링크의 25%, 56.3 km/h 자유류)가 빠진다.
#
# 커버리지로 확인했다. CARLA 도심이 참조보다 느려서(24.8 대 38~42 km/h) 빠른 구간을
# 빼도 CARLA가 참조 안에 드는 비율이 94.3%에서 96.1%로 오른다. 고속에서 겪은 실패
# (CARLA가 정체 참조보다 빨라 밖으로 나감)와 반대 방향이라 재발이 아니다.
JUNCTION_SITE = os.environ.get("JUNCTION_SITE", "1") != "0"


def merge3(st):
    """다섯 층을 셋으로. 교차로 통제 방식은 층이 아니라 열로 간다."""
    return "junction" if st in JUNC else st


def widen_junction(uid, step, jm, D=None):
    """교차로 본체에 경로를 따라 앞뒤 D 미터를 붙인다. 기본은 꺼져 있다(JUNC_D=0).

    민감도 축으로만 남긴다. 껐다 켰다 해서 결론이 안 바뀌는지 보는 용도.

    붙이려던 이유. 교차로 바로 밖은 회랑이 아니다. 무신호 접근부는 양보 감속 구간이고
    신호 접근부는 대기열이다. 둘 다 교차로가 만든 거동이라 링크 참조에 넣으면 회랑을
    교차로 접근부로 정의하는 꼴이 된다.

    끈 이유는 JUNC_D 주석에 있다. 요지는 보정이 필요한 자리가 없다는 것. CARLA는 지도가
    선언하고, inD와 SinD는 JUNCTION_SITE가 사이트째 처리하고, NGSIM은 자료의 int_id
    라벨이다. 좌표 근사인 foggy_bottom 하나만 불확실한데 거기서 40은 파괴다.

    유클리드가 아니라 경로를 따라 잰다. 직선거리로 부풀리면 교차로 옆을 스치는
    다른 도로까지 먹는다. CARLA처럼 도로가 촘촘한 맵에서 특히 그렇다.

    D 근거(켤 때). 사이트마다 거동이 평평해지는 거리가 inD aseag 32 m, NGSIM 도심 40 m
    안팎, foggy_bottom 55~60 m, CARLA Town03 52 m(정지율)다. SinD는 촬영 전 구간(최대
    60 m)에서 평평해지지 않는다. 하나로 고르는 건 판단이다.
    """
    D = JUNC_D if D is None else D
    jm = np.asarray(jm, bool).copy()
    if D <= 0 or not jm.any():
        return jm
    uid = np.asarray(uid)
    s = np.nan_to_num(np.asarray(step, float))
    newtrk = np.r_[True, uid[1:] != uid[:-1]]
    s = np.where(newtrk, 0.0, s)
    cum = np.cumsum(s)
    out = jm.copy()
    # 트랙 경계를 넘지 않게 트랙마다 본다. 누적거리는 전체 하나를 써도 트랙 안에서만
    # 비교하므로 상관없다
    idx = np.flatnonzero(newtrk)
    ends = np.r_[idx[1:], len(uid)]
    for a, b in zip(idx, ends):
        j = np.flatnonzero(jm[a:b])
        if not len(j):
            continue
        c = cum[a:b]
        # 교차로 run 마다 앞뒤로 D 만큼. run 경계만 보면 되므로 구간으로 처리
        brk = np.r_[0, np.flatnonzero(np.diff(j) > 1) + 1, len(j)]
        m = out[a:b]
        for u, w in zip(brk[:-1], brk[1:]):
            lo, hi = c[j[u]] - D, c[j[w - 1]] + D
            m |= (c >= lo) & (c <= hi)
        out[a:b] = m
    return out


def fill_ctrl(uid, stratum, ctrl):
    """교차로 run 안의 통제 방식을 run 전체로 편다.

    widen_junction으로 접근부를 붙이면 그 프레임의 ctrl이 아직 link다. units는 run의
    첫 값을 단위 속성으로 쓰므로, 접근부에서 시작한 교차로 단위가 link로 찍힌다.
    ctrl은 민감도 축(신호만 대조)으로 쓸 열이라 맞춰둔다.
    """
    st = np.asarray(stratum, object)
    c = np.asarray(ctrl, object).copy()
    uid = np.asarray(uid)
    new = np.r_[True, (uid[1:] != uid[:-1]) | (st[1:] != st[:-1])]
    run = np.cumsum(new)
    j = st == "junction"
    if not j.any():
        return c
    t = pd.DataFrame({"run": run[j], "c": c[j]})
    t = t[~t["c"].isin(["link", "approach"])]
    if t.empty:
        return c
    m = t.groupby("run")["c"].agg(lambda s: s.value_counts().index[0])
    fill = m.reindex(run[j]).to_numpy()
    c[j] = np.where(pd.isna(fill), c[j], fill)
    return c


def hw_adjacent(town):
    """road -> 같은 junction에 고속 링크가 물려 있는가. 층이 아니라 열로 쓴다.

    Town06 도심 프레임의 86.6%가 여기 걸린다. 그런데 거동은 안 갈렸다(고속인접 16.9 km/h,
    Town03 순수 도심 17.0). 위상으로 뺄 근거가 안 서서 표시만 하고 민감도에서 본다.
    """
    lim, ctrl = S.road_map(town)
    root = ET.parse(os.path.join(MAP_DIR, town + ".xodr")).getroot()
    st = {r.get("id"): S.stratum_of(lim, ctrl, r.get("id")) for r in root.iter("road")}
    nb = collections.defaultdict(set)
    for j in root.iter("junction"):
        rs = set()
        for c in j.findall("connection"):
            rs.add(c.get("incomingRoad"))
            rs.add(c.get("connectingRoad"))
        for a in rs:
            nb[a] |= (rs - {a})
    return {r: any(st.get(x) == "hw_link" for x in nb.get(r, ())) for r in st}


def sig_of(v, a, step):
    """창 하나를 한 점으로.

    같은 자리에 머문 프레임은 뺀다. 신호 대기가 30초면 같은 위치가 300프레임인데 그것이
    통계를 지배한다. inD에 40 m를 530초에 간 단위가 있었다. 멈춤 자체는 앞뒤 프레임으로
    남으니 사건을 버리는 게 아니라 체류만 접는 것이다. 부수적으로 정지 구간이 빠지는데,
    거기가 참 가속이 정확히 0이라 잡음이 부호를 제일 잘 뒤집는 자리다.

    za는 시간이 아니라 이동거리로 나눈다. 시간으로 나누면 빨리 간 차가 낮게 나온다.
    실제로 inD 접근부가 54 km/h라 도심 za가 0.78로 나왔는데 거리 기준으로는 2.49로
    TGSIM 2.54와 붙는다. 단위가 이미 고정 거리라 정의도 이쪽이 일관된다.
    x100은 읽기 좋으라는 배율이다. 채점은 실참조 표준편차로 정규화하므로 상수배에
    안 흔들린다.
    """
    ok = np.isfinite(v) & np.isfinite(a) & (np.abs(a) <= A_MAX)
    v, a, sp = v[ok], a[ok], np.asarray(step, float)[ok]
    if len(v) == 0:
        return None
    keep = np.r_[True, sp[1:] > EPS_STEP]
    v, a, sp = v[keep], a[keep], sp[keep]
    if len(v) < MIN_FR:            # 중복 제거 후에 건다. 대기만 한 단위가 여기서 걸린다
        return None
    s = np.sign(a)
    s = s[s != 0]
    flip = int((np.diff(s) != 0).sum()) if len(s) > 1 else 0
    return {"v_mean": v.mean(), "v_sd": v.std(), "a_mean": a.mean(),
            "a_sd": a.std(), "za": flip / max(np.nansum(sp), 1e-9) * 100.0,
            "n": len(v)}


def units(d, extra=()):
    """프레임 표를 단위로 자른다. 실측과 CARLA에 같은 함수를 쓴다.

    d에 있어야 하는 열: uid, t, v, a, step(프레임당 이동거리), stratum.
    extra는 단위마다 같이 실어 보낼 열 이름들(통제 방식, 사이트 같은 것).

    run = (uid, stratum)이 이어지고 시간이 안 끊긴 구간. road 경계로는 안 끊는다.
    run 안에서 누적거리를 재고 L미터마다 자른다. 꼬리(L 미만)는 버린다. L이 None인 층은
    run 전체가 한 단위이고, 이때만 관측 경계에 붙은 것을 잘린 표본으로 뺀다.
    """
    if d.empty:
        return pd.DataFrame()
    d = d.sort_values(["uid", "t"]).reset_index(drop=True)
    new = ((d["uid"] != d["uid"].shift()) | (d["stratum"] != d["stratum"].shift())
           | (d["t"].diff().abs() > 1.5 * DT))
    d["run"] = new.cumsum()
    step = d["step"].to_numpy(float).copy()
    step[new.to_numpy()] = 0.0
    d["s"] = pd.Series(step).groupby(d["run"]).cumsum().to_numpy()
    # 통과 전체가 단위인 층(교차로)은 잘린 표본을 빼야 한다. 판정을 시각이 아니라 run
    # 순서로 한다. 앞뒤로 같은 차량의 다른 run이 있으면 그 사이를 온전히 지난 것이다.
    # 시각으로 하면 자료마다 시간 원점이 달라 실측이 통째로 걸러진다(실제로 그랬다)
    # 절단 판정을 차량이 run을 셋 이상 가질 때만 건다. 촬영 전체가 교차로인 사이트
    # (inD, SinD)는 트랙 하나가 run 하나라 첫이자 끝이 되어 통째로 버려졌다. 그때
    # 대응이 고정거리 창이었는데, 그건 교차로를 30 m로 자르는 부작용을 낳았다.
    # run이 하나뿐인 차량은 절단인지 아닌지 판정할 근거 자체가 없으므로 최소 길이로만
    # 거른다. run이 여럿인 차량(CARLA, NGSIM)은 앞뒤 run이 있으니 그대로 잡아낸다
    nrun = d.groupby("uid")["run"].nunique()
    multi = set(nrun[nrun >= 3].index)
    first = set(d[d["uid"].isin(multi)].groupby("uid")["run"].min())
    last = set(d[d["uid"].isin(multi)].groupby("uid")["run"].max())
    rows = []
    for run, g in d.groupby("run", sort=False):
        st = g["stratum"].iloc[0]
        L = LEN.get(st)
        base = {"uid": g["uid"].iloc[0], "stratum": st, "run": int(run)}
        for c in extra:
            base[c] = g[c].iloc[0]
        v = g["v"].to_numpy(float)
        a = g["a"].to_numpy(float)
        sp = np.r_[0.0, np.diff(g["s"].to_numpy(float))]
        # 진행방향 누적. 좌회전과 직진이 같은 서명을 갖는 것을 막는 축이다.
        # Town03 교차로에서 서명이 거의 같은 짝의 40.9%가 조작이 달랐다. 다섯 성분이
        # 전부 종방향 속도라 경로 정보가 아예 없어서다. head 열이 없는 출처(NGSIM 회랑,
        # 1차원이라 방향이 안 나옴)는 NaN이고 그 단위는 turn 판에서 빠진다
        hd = (g["head"].to_numpy(float)
              if "head" in g and np.isfinite(g["head"].to_numpy(float)).all()
              else None)
        if L is None:
            if run in first or run in last:
                continue
            tot = float(g["s"].iloc[-1])
            if tot < MIN_L.get(st, 0.0):
                continue
            s = sig_of(v, a, sp)
            if s:
                s.update(base)
                s["unit"] = 0
                s["length"] = tot
                s["turn"] = _turn(hd, sp)
                rows.append(s)
            continue
        w = (g["s"].to_numpy() // L).astype(int)
        for k in range(int(g["s"].iloc[-1] // L)):
            m = w == k
            s = sig_of(v[m], a[m], sp[m])
            if s:
                s.update(base)
                s["unit"] = k
                s["length"] = L
                s["turn"] = (np.nan if hd is None else _turn(hd[m], sp[m]))
                rows.append(s)
    return pd.DataFrame(rows)


def carla_frames(path, town, adj):
    """수집물 -> 프레임 표. 층 판정은 score_strata와 같은 규칙이다."""
    d = pd.read_csv(path)
    lim, ctrl = S.road_map(town)
    d = S.kinematics(d).sort_values(["vid", "t"]).reset_index(drop=True)
    raw = [S.stratum_of(lim, ctrl, r) for r in d["road_id"]]
    d["ctrl"] = [JUNC.get(x, "link") for x in raw]
    d["stratum"] = [merge3(x) if x else None for x in raw]
    d["hw_adj"] = [bool(adj.get(str(r))) for r in d["road_id"]]
    d["uid"] = d["vid"].astype(str)
    d["step"] = np.hypot(d["x"].diff(), d["y"].diff())
    d.loc[d["uid"] != d["uid"].shift(), "step"] = 0.0
    # 진행방향은 기록된 yaw가 아니라 위치에서 유도한다. 실측에 yaw가 없어 같은
    # 스택을 걸려면 이쪽이어야 한다. CARLA는 y가 뒤집힌 좌표계라 atan2(-dy, dx)다.
    # 기록 yaw와 대조: 절대중앙 0.5도, 좌우직 판정 97.7% 일치. 부호를 안 뒤집으면
    # 상관이 -0.689로 나온다
    hx, hy = [], []
    for _, gg in d.groupby("uid", sort=False):
        xs = _sema(gg["x"].to_numpy(float), DT)
        ys = _sema(gg["y"].to_numpy(float), DT)
        hx.append(np.gradient(xs, DT)); hy.append(np.gradient(ys, DT))
    d["head"] = np.arctan2(-np.concatenate(hy), np.concatenate(hx))
    # 기본은 아무것도 안 붙인다(JUNC_D=0). widen_junction 주석에 끈 이유가 있다.
    #
    # 대가가 있다. 실측 교차로 단위는 inD/SinD가 사이트째 처리해 접근부와 유출부를 포함하고
    # (중앙 78 m) CARLA는 지도 폴리곤뿐이라(중앙 25 m) 단위가 서로 다른 물건이 된다.
    # 원본 대비 배수에는 안 걸린다(양쪽 다 CARLA라 대칭). 실측과 견주는 덮개에는 걸린다
    wj = widen_junction(d["uid"].to_numpy(), d["step"].to_numpy(),
                        (d["stratum"] == "junction").to_numpy())
    grow = wj & (d["stratum"] == "urb_block").to_numpy()
    d.loc[grow, "stratum"] = "junction"
    d["junc_ext"] = grow                       # 그림에서 지도 선언분과 가르려고 남긴다
    d["ctrl"] = fill_ctrl(d["uid"], d["stratum"], d["ctrl"])
    d["site"] = town
    return d[d["stratum"].notna()]


def _kin(y):
    """실측 계측 스택. 위치를 sEMA 평활한 뒤 미분한다. CARLA도 같은 스택이다."""
    v = np.abs(np.gradient(_sema(y, DT), DT))
    return v, np.gradient(v, DT)


TG_HW = ["tgsim_i90_94_moving", "tgsim_i90_94_stationary",
         "tgsim_i294_l1", "tgsim_i294_l2", "tgsim_i395"]


def _turn(head, step):
    """단위 하나의 순 방향 변화(도). 진입 방향과 진출 방향의 각도차다.

    누적 방식을 쓰면 안 된다. 방향을 프레임마다 unwrap해서 더하면 정지 중 위치
    잡음이 랜덤워크로 쌓인다. 실측 신호 교차로에서 |turn|>180도가 26~41%, 360도 초과가
    648개 나왔다. 무신호(inD 2.5~5.7%)와 고속(0.3%)에 안 나오는 걸 보면 대기 시간이
    원인이다.

    이동량으로 가중해 앞 3분의 1과 뒤 3분의 1의 평균 방향을 내고 그 차를 쓴다. 정차
    프레임은 step이 0이라 저절로 빠지고, 값이 ±180도로 유계다.
    """
    if head is None or len(head) < 6:
        return np.nan
    z = np.asarray(step, float) * np.exp(1j * np.asarray(head, float))
    k = max(2, len(z) // 3)
    a, b = z[:k].sum(), z[-k:].sum()
    if abs(a) < 1e-9 or abs(b) < 1e-9:
        return np.nan
    return float(np.degrees(np.angle(b / a)))


def _head_xy(gid, x, y, dt=DT):
    """트랙별로 평활한 위치에서 진행방향(rad). 실측과 CARLA에 같은 방식을 건다.

    yaw를 기록하는 자료가 CARLA뿐이라 위치 유도로 통일한다. CARLA 기록 yaw와 대조하면
    절대중앙 0.5도, 좌우직 판정 97.7% 일치다(부호 규약만 맞추면).
    """
    gid = np.asarray(gid)
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    out = np.full(len(x), np.nan)
    b = np.r_[0, np.flatnonzero(gid[1:] != gid[:-1]) + 1, len(x)]
    for i, j in zip(b[:-1], b[1:]):
        if j - i < 3:
            continue
        xs, ys = _sema(x[i:j], dt), _sema(y[i:j], dt)
        out[i:j] = np.arctan2(np.gradient(ys, dt), np.gradient(xs, dt))
    return out


def _units_from_xy(gid, x, y, stratum, ctrl, site, dt=DT, t_abs=None,
                   lane="", corr=None):
    """위치 배열 -> 프레임 표 한 조각. NGSIM과 같은 계측 스택(sEMA 평활 후 미분).

    stratum은 스칼라도 배열도 받는다. foggy_bottom은 한 트랙 안에서 링크와 교차로를
    오가므로 배열로 준다. 그래야 units가 그 교대를 run 경계로 잡는다.
    """
    xs = _sema(np.asarray(x, float), dt)
    ys = _sema(np.asarray(y, float), dt)
    dx, dy = np.gradient(xs, dt), np.gradient(ys, dt)
    v = np.hypot(dx, dy)
    return pd.DataFrame({
        "uid": gid, "t": np.arange(len(v)) * dt, "v": v,
        "a": np.gradient(v, dt),
        "step": np.r_[0.0, np.hypot(np.diff(xs), np.diff(ys))],
        # 평활한 위치에서 유도한 진행방향. CARLA도 같은 방식으로 낸다. 기록된 yaw와
        # 대조했더니 절대중앙 0.5도, 좌우직 판정 97.7% 일치
        "head": np.arctan2(dy, dx),
        "stratum": stratum, "ctrl": ctrl, "site": site,
        "hw_adj": False, "width": "",
        # 위치는 평활 전 원본을 남긴다. 평활은 속도 미분을 위한 것이고 밀도를 셀
        # 때는 있는 그대로가 맞다. t_abs를 안 주면 트랙 자기 시각이라 같은 시각의
        # 차를 못 모은다. 그때는 기본도표를 못 그린다
        "x": np.asarray(x, float), "y": np.asarray(y, float),
        "t_abs": (np.arange(len(v)) * dt if t_abs is None
                  else np.asarray(t_abs, float)),
        # 기본도표는 차로 단위여야 한다. 지점 전체를 한 링크로 치면 접근로 여럿이
        # 겹쳐 밀도가 차로 수만큼 부푼다. 실제로 실측 밀도가 285 대/km로 나왔다
        "lane": lane,
        # 회랑. site 하나에 서로 다른 관측 구간이 여럿 들어 있는 자료가
        # 있다(NGSIM 넷). 기본도표는 구간마다 좌표계가 달라 갈라야 한다.
        # site를 바꾸면 P/R의 참조 분해가 깨져서 열을 따로 둔다
        "corr": site if corr is None else corr})


def real_frames_tgsim():
    """TGSIM -> 프레임 표. 고속 5사이트와 도심 구획 foggy_bottom.

    왜 지금까지 안 썼나: build_strata의 load_tgsim이 speed_kf만 읽었다. 위치 컬럼
    (xloc_kf, yloc_kf)이 있는데 안 봤고, foggy_bottom은 속도가 x, y 성분으로 와서
    이름이 안 맞아 목록에서 통째로 빠져 있었다.

    foggy_bottom은 200 m 남짓한 도심 격자다. lane_kf가 동서 도로 둘과 남북 도로 둘로
    갈리고, 회랑 차로(연장 104~132 m)와 그 사이 짧은 차로(8~31 m)가 뚜렷이 나뉜다.
    긴 쪽이 링크, 짧은 쪽이 교차로다. 실제로 링크 정지율 55.7%(대기열), 교차로 33.2%에
    중앙 11.6 km/h(통과)로 CARLA와 같은 모양이 나온다.

    NGSIM과 달리 회랑이 축에 안 맞아 2차원 거리를 쓴다.
    """
    out = []
    for name in TG_HW:
        d = pd.read_csv("data/tgsim/%s.csv" % name)
        idc = "ID" if "ID" in d.columns else "id"
        d = d.rename(columns={idc: "vehicle_id"})
        if "run_index" in d.columns:      # 촬영분이 다르면 다른 차량이다
            d["vehicle_id"] = (d["run_index"].astype(str) + "_"
                               + d["vehicle_id"].astype(str))
        d = d.dropna(subset=["xloc_kf", "yloc_kf", "time"])
        d = d.sort_values(["vehicle_id", "time"]).reset_index(drop=True)
        d["t"] = d["time"]
        d = add_tracks(d)
        for trk, g in d.groupby("trk", sort=False):
            if len(g) < MIN_FR:
                continue
            out.append(_units_from_xy("%s#%s" % (name, trk), g["xloc_kf"],
                                      g["yloc_kf"], "hw_link", "link", name))

    d = pd.read_csv("data/tgsim/tgsim_foggy_bottom.csv")
    d = d[d["type_most_common"] == 3]     # 승용차. 길이 중앙 6.2 m
    d = d.rename(columns={"id": "vehicle_id"}).sort_values(["vehicle_id", "time"])
    d["t"] = d["time"]
    d = add_tracks(d.reset_index(drop=True))
    jm = lane_junction_mask(d["vehicle_id"], d["lane_kf"], d["speed_kf_x"],
                            d["speed_kf_y"])
    fstep = np.hypot(d.groupby("trk")["xloc_kf"].diff(),
                     d.groupby("trk")["yloc_kf"].diff()).fillna(0.0)
    jm = widen_junction(d["trk"].to_numpy(), fstep.to_numpy(), jm)
    d["stratum"] = np.where(jm, "junction", "urb_block")
    # 트랙 단위로 넣는다. (트랙, 층)으로 쪼개면 한 트랙 안의 링크와 교차로 교대가 사라져
    # 교차로 통과가 전부 트랙 첫 run이 되고 잘린 표본으로 걸러진다
    for trk, g in d.groupby("trk", sort=False):
        if len(g) < MIN_FR:
            continue
        st = g["stratum"].to_numpy()
        # 신호 여부를 자료가 안 준다. NGSIM과 같은 처지라 같은 표시를 쓴다
        out.append(_units_from_xy("fb#%s" % trk, g["xloc_kf"], g["yloc_kf"], st,
                                  np.where(st == "junction", "signal?", "link"),
                                  "tgsim_foggy_bottom",
                                  t_abs=g["time"].to_numpy(float),
                                  lane=g["lane_kf"].to_numpy()))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def real_frames_ngsim():
    """NGSIM 네 사이트 -> 프레임 표. 회랑이 사실상 1차원이라 종방향 y만 쓴다."""
    out = []
    for name in ("ngsim_us101", "ngsim_i80"):
        d = pd.read_csv("data/ngsim/%s.csv" % name,
                        usecols=["vehicle_id", "global_time", "local_y", "lane_id"])
        d["lane_id"] = d["lane_id"].fillna(0).astype(int)
        d = d[~d["lane_id"].isin(BAD_LANES)]
        d["t"] = d["global_time"] / 1000.0
        d["y"] = d["local_y"] * FT
        d = d.sort_values(["vehicle_id", "t"]).reset_index(drop=True)
        d = add_tracks(d)
        d["stratum"] = "hw_link"
        d["ctrl"] = "link"
        d["corr"] = name
        out.append(d)

    for name, width in (("ngsim_lankershim", "wide"), ("ngsim_peachtree", "narrow")):
        cols = ["vehicle_id", "global_time", "local_y", "lane_id", "int_id",
                "direction", "movement"]
        d = pd.read_csv("data/ngsim/%s.csv" % name, usecols=cols)
        for c in ("int_id", "lane_id", "direction"):
            d[c] = d[c].fillna(0).astype(int)
        d["t"] = d["global_time"] / 1000.0
        d["y"] = d["local_y"] * FT
        d = d.sort_values(["vehicle_id", "t"]).reset_index(drop=True)
        d = add_tracks(d)
        # 통과 차로만. 좌회전 베이는 대기가 목적이라 링크 주행이 아니다
        mv = d["movement"].where(d["int_id"] > 0)
        nxt = mv.groupby(d["trk"]).bfill()
        link = d[(d["int_id"] == 0) & (~d["lane_id"].isin(BAD_LANES))
                 & d["direction"].isin(MAIN_DIR)].copy()
        link["nxt"] = nxt[link.index]
        gg = link.groupby(["direction", "lane_id"])
        lp = pd.DataFrame({"n": gg.size(),
                           "left": gg["nxt"].apply(lambda s: (s == 2).mean()),
                           "span": gg["y"].max() - gg["y"].min()})
        lp["share"] = lp["n"] / lp.groupby("direction")["n"].transform("sum")
        lp["frac"] = lp["span"] / lp.groupby("direction")["span"].transform("max")
        keep = set(lp.index[(lp["share"] >= MIN_SHARE) & (lp["left"] <= LEFT_CUT)
                            & (lp["frac"] >= SPAN_FRAC)])
        key = pd.Series(list(zip(d["direction"], d["lane_id"])), index=d.index)
        lab = pd.Series(None, index=d.index, dtype=object)
        lab[d["int_id"] > 0] = "junction"
        lab[(d["int_id"] == 0) & key.isin(keep)
            & d["direction"].isin(MAIN_DIR)] = "urb_block"
        # 교차로 영향 구간을 링크에서 뺀다. 회랑이 1차원이라 경로거리는 |dy|다.
        # 좌회전 베이(라벨 없음)는 그대로 둔다. 이미 양쪽 층에서 다 빠져 있어 링크를
        # 오염시키지 않는다
        wj = widen_junction(d["trk"].to_numpy(),
                            d.groupby("trk")["y"].diff().abs().fillna(0.0).to_numpy(),
                            (lab == "junction").to_numpy())
        lab[wj & (lab == "urb_block").to_numpy()] = "junction"
        d["stratum"] = lab
        d["junc_ext"] = wj & (d["int_id"] == 0).to_numpy()
        # 실측 교차로 통제 방식은 모른다. NGSIM에 신호 컬럼이 없다. 신호 간선이라는 건
        # 촬영지에 대한 외부 지식이지 파일 안의 값이 아니라 그렇게 표시한다
        d["ctrl"] = fill_ctrl(d["trk"], d["stratum"].fillna("-"),
                              np.where(d["int_id"] > 0, "signal?", "link"))
        d["width"] = width
        d["corr"] = name
        out.append(d)

    rows = []
    for d in out:
        d = d[d["stratum"].notna()].copy()
        v = np.full(len(d), np.nan)
        a = np.full(len(d), np.nan)
        for _, idx in d.groupby("trk").indices.items():
            if len(idx) < 5:
                continue
            v[idx], a[idx] = _kin(d["y"].to_numpy()[idx])
        d["v"], d["a"] = v, a
        d["uid"] = d["trk"].astype(str)
        d["step"] = d["y"].diff().abs()
        rows.append(d)
    r = pd.concat(rows, ignore_index=True, sort=False)
    r["site"] = "ngsim"
    r["hw_adj"] = False
    if "width" not in r:
        r["width"] = ""
    # 회랑이 사실상 1차원이라 x가 없다. 기본도표는 링크를 따라간 위치만 있으면
    # 되므로 y로 충분하다. x는 자리만 채운다
    r["x"] = np.nan
    r["t_abs"] = r["t"]
    r["lane"] = r["lane_id"] if "lane_id" in r else ""
    if "corr" not in r:
        r["corr"] = "ngsim"
    r["corr"] = r["corr"].fillna("ngsim")
    return r[["uid", "t", "v", "a", "step", "stratum", "ctrl", "site",
              "hw_adj", "width", "x", "y", "t_abs",
              "lane", "corr"]].fillna({"width": ""})


IND_DIR = os.path.join("data", "inD-dataset-v1.1", "data")
IND_MAP = os.path.join("data", "inD-dataset-v1.1", "maps", "opendrive")
IND_XODR = {1: "01_bendplatz/bendplatz.xodr", 2: "02_frankenburg/frankenburg.xodr",
            3: "03_heckstrasse/heckstrasse.xodr", 4: "04_aseag/aseag.xodr"}
DRIVE_LANE = {"driving", "entry", "exit", "onRamp", "offRamp", "connectingRamp",
              "bidirectional"}
POLY_STEP = 2.0    # m. 도로 면적 다각형을 만들 때 기준선 표본 간격


def _lane_width(lane, ds):
    """차로 폭. OpenDRIVE는 sOffset부터의 3차 다항식으로 준다."""
    w = 0.0
    best = None
    for e in lane.findall("width"):
        so = float(e.get("sOffset", 0))
        if so <= ds and (best is None or so > float(best.get("sOffset", 0))):
            best = e
    if best is None:
        return 0.0
    d = ds - float(best.get("sOffset", 0))
    for k, name in enumerate("abcd"):
        w += float(best.get(name, 0)) * d ** k
    return max(w, 0.0)


def road_area(road, step=POLY_STEP):
    """도로 포장면 다각형. 좌측 가장자리로 갔다가 우측 가장자리로 돌아온다.

    중심선에서 고정 거리로 자르면 안 된다. OpenDRIVE는 다차로 접근이라도 연결로를
    1차로짜리 여럿으로 쪼개 모델링하는 일이 흔하다. inD aseag가 그렇고, 거기서 3 m 컷을
    쓰니 교차로 상자 안 프레임의 9.7%가 도심으로 샜다. 빠진 것들이 중심선에서 중앙
    4.3 m, 즉 바로 옆 차로였다.

    CARLA는 road_id 소속으로 판정해 차로가 몇이든 다 잡는다. 좌표로 판정하는 자료도
    같은 것을 봐야 비대칭이 안 생긴다.
    """
    pv = road.find("planView")
    if pv is None:
        return None
    total = sum(float(g.get("length", 0)) for g in pv.findall("geometry"))
    lanes = road.find("lanes")
    if lanes is None or total <= 0:
        return None
    secs = sorted(lanes.findall("laneSection"), key=lambda e: float(e.get("s", 0)))
    offs = sorted(lanes.findall("laneOffset"), key=lambda e: float(e.get("s", 0)))
    left, right = [], []
    n = max(2, int(total / step))
    for i in range(n + 1):
        s = total * i / n
        sec = secs[0]
        for c in secs:
            if float(c.get("s", 0)) <= s:
                sec = c
        ds = s - float(sec.get("s", 0))
        t0 = 0.0                                  # 기준선 횡오프셋
        for o in offs:
            if float(o.get("s", 0)) <= s:
                d = s - float(o.get("s", 0))
                t0 = sum(float(o.get(k, 0)) * d ** j
                         for j, k in enumerate("abcd"))
        tl = tr = 0.0
        for side, sign in (("left", 1), ("right", -1)):
            grp = sec.find(side)
            if grp is None:
                continue
            acc = 0.0
            for ln in sorted(grp.findall("lane"),
                             key=lambda e: abs(int(e.get("id", 0)))):
                acc += _lane_width(ln, ds)
                if ln.get("type") in DRIVE_LANE:   # 주행 차로 바깥 끝까지만
                    if sign > 0:
                        tl = acc
                    else:
                        tr = acc
        pl = road_point(road, s, t0 + tl)
        pr = road_point(road, s, t0 - tr)
        if pl and pr:
            left.append(pl)
            right.append(pr)
    if len(left) < 2:
        return None
    return np.asarray(left + right[::-1])


def _connect_lane_width(root, roads):
    """연결로 회전차로 폭. 중심선에서 이만큼 안이면 그 차로를 타는 중으로 본다.

    컷의 상대가 연결로 차로 중심선이므로 재야 할 것도 차로 폭이다. 연결로는 inD 네 지점
    전부 1차로라(중앙 1) 폭이 2.60~3.67 m, 반폭 1.3~1.8 m다. 반폭이 아니라 폭을 쓰는 건
    옆으로 흔들리는 몫을 반폭만큼 더 주려는 것이다. 이 배수 1은 판단이지 측정이 아니다.

    아래쪽은 자료가 막는다. 컷을 낮추면 트랙이 띠를 들락날락해 교차로 통과가 트랙당
    여러 번으로 잡힌다(1.5 m에서 loc3 6.00회, loc2 2.96회). 2.5~3.0 m에서 1.00회로
    가라앉고 그 위로는 안 변한다. 위쪽은 자료가 안 막는다. 프레임 비율도 통과 길이도
    끊기는 데 없이 매끄럽게 커지므로 상한은 원리로 정한 것이다.

    진입로 반폭을 쓴 적이 있는데 틀렸다. 연결로 중심선에 도로 전체 반폭을 댄 꼴이라
    aseag에서 7.9 m가 나왔고(차로 반폭의 네 배), 교차로가 프레임의 58%를 먹어 통과
    319건 중 317건이 촬영 범위에 잘렸다. 차로 폭 다각형은 laneOffset과 다중
    laneSection이 얽힌 연결로에서 구현이 어긋나 따로 고칠 일이다.
    """
    ws = []
    for j in root.iter("junction"):
        for rid in {c.get("connectingRoad") for c in j.findall("connection")} & set(roads):
            r = roads[rid]
            L = float(r.get("length", 0))
            ln = r.find("lanes")
            if ln is None or L <= 0:
                continue
            secs = sorted(ln.findall("laneSection"), key=lambda e: float(e.get("s", 0)))
            for frac in (0.1, 0.5, 0.9):
                s_pos = L * frac
                sec = secs[0]
                for c in secs:
                    if float(c.get("s", 0)) <= s_pos:
                        sec = c
                ds = s_pos - float(sec.get("s", 0))
                for side in ("left", "right"):
                    g = sec.find(side)
                    if g is None:
                        continue
                    for l in g.findall("lane"):
                        if l.get("type") in DRIVE_LANE:
                            w = _lane_width(l, ds)
                            if w > 0.5:      # 0폭 차로는 그 s에서 아직 안 열린 것
                                ws.append(w)
    return float(np.median(ws)) if ws else 3.0


def map_junction_mask(x, y, xodr):
    """지도가 선언한 교차로 영역. 추정 대신 선언을 쓴다.

    좌표 규칙(충돌 핵 + 반경)은 근사라 NGSIM 라벨 대비 F1이 0.73에 그친다. 원이 등방인데
    교차로는 방향마다 다르고, 회전 궤적이 원 밖으로 나가며, 소수 방향 진출입구가 핵으로
    잡힌다. 방위별 반경(별 모양)과 셀 기반도 재봤는데 더 나빴다(F1 0.56, 0.35).

    inD는 지점 넷에 OpenDRIVE가 다 있고 좌표계가 궤적과 같다. 지도 교차로 폭이 27x28에서
    62x55 m까지 갈리는데 원 하나로는 그 차이를 못 따라간다. CARLA와 같은 판정(지도 선언)이
    되므로 규칙도 통일된다.

    영역은 연결로 중심선에서 차로 폭 안이고, 거기에 닫기와 구멍메움을 건다.
    연결로 띠만 합치면 교차로 안쪽인데 어느 연결로 경로에도 안 걸리는 자리가 빠진다.
    연결로 사이 틈, 넓은 교차로 한복판, 모델링 안 된 이동방향이 그렇다. 교차로는
    연속된 하나의 포장면이고 연결로는 그걸 이동방향별로 쪼갠 것뿐이라, 그 틈은 지도
    표기의 산물이지 도로가 아니다. SinD에 건 것과 같은 처리다(_close_fill).

    폭의 근거와 민감도는 _connect_lane_width.
    """
    root = ET.parse(os.path.join(IND_MAP, xodr)).getroot()
    roads = {r.get("id"): r for r in root.iter("road")}
    pad = _connect_lane_width(root, roads)
    pts = []
    for j in root.iter("junction"):
        cr = {c.get("connectingRoad") for c in j.findall("connection")} & set(roads)
        for rid in cr:
            # 주행 차로가 없는 연결로(보도, 갓길만)는 뺀다. aseag에 둘 있다
            ln = roads[rid].find("lanes")
            drv = any(l.get("type") in DRIVE_LANE
                      for l in ln.iter("lane")) if ln is not None else False
            if drv:
                pts += [q for q in sample_road(roads[rid]) if not np.isnan(q[0])]
    P = np.c_[np.asarray(x, float), np.asarray(y, float)]
    if not pts:
        return np.zeros(len(P), bool)
    C = np.asarray(pts)
    C = C[::max(1, len(C) // 3000)]
    return _band_close_fill(P, C, pad)


def _band_close_fill(P, C, pad, cell=0.5):
    """중심선 C에서 pad 안인 띠를 격자에 굽고, pad만큼 닫고 구멍을 메운 뒤 점 소속을 본다."""
    from scipy import ndimage
    lo = C.min(axis=0) - pad * 4
    hi = C.max(axis=0) + pad * 4
    nx = int((hi[0] - lo[0]) / cell) + 1
    ny = int((hi[1] - lo[1]) / cell) + 1
    img = np.zeros((ny, nx), bool)
    ix = np.clip(((C[:, 0] - lo[0]) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((C[:, 1] - lo[1]) / cell).astype(int), 0, ny - 1)
    img[iy, ix] = True
    r = max(int(round(pad / cell)), 1)
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    se = xx ** 2 + yy ** 2 <= r * r
    img = ndimage.binary_dilation(img, se)          # 중심선에서 pad 안
    img = ndimage.binary_fill_holes(ndimage.binary_closing(img, se))
    jx = np.floor((P[:, 0] - lo[0]) / cell).astype(int)
    jy = np.floor((P[:, 1] - lo[1]) / cell).astype(int)
    ok = (jx >= 0) & (jx < nx) & (jy >= 0) & (jy < ny)
    out = np.zeros(len(P), bool)
    out[ok] = img[jy[ok], jx[ok]]
    return out
VMIN_MOVE = 1.0     # m/s. 트랙 최고속이 이 아래면 주차 차량이다. 트랙 수로는 1~9%인데
                    # 안 움직이니 촬영 내내 잡혀 프레임의 35~88%를 차지한다. 반드시 뺀다

AX_DIFF = 30.0      # deg. 차선 축이 이만큼 다르면 다른 도로로 본다
CONN_SH = 0.10      # 통과가 축을 바꾸는 비율이 이 위면 연결 차선
CONN_MIN = 20       # 통과 최소 건수. 몇 건짜리 차선은 비율이 안 믿긴다


def lane_junction_mask(vid, lane, vx, vy):
    """교차로 = 축이 다른 차선을 잇는 연결 차선. TGSIM foggy_bottom용.

    CARLA와 inD가 쓰는 규칙(OpenDRIVE connectingRoad)을 제공자 차선 라벨에 얹은 것이다.
    foggy_bottom의 lane_kf는 추정이 아니라 제공자가 붙인 차선 지도다(48개). 그러면
    지도 없는 자료가 아니라 지도 표현이 다른 자료이고, 규칙도 같은 것을 쓸 수 있다.

    판정: 차량이 차선 L을 지날 때 들어온 차선과 나간 차선의 축이 AX_DIFF 넘게 다르면
    그 통과는 축을 바꾼 것이다. 그런 통과가 CONN_SH 넘는 차선이 연결 차선이다.
    사용자 정의 그대로다. 좌회전과 직진이 되고 다른 차선이 간섭하는 자리.

    좌표 규칙(충돌 핵 + 반경)을 버린 이유. foggy_bottom은 200 m 남짓한 블록 둘레라
    다음 교차로가 닿기 전에 축 집중도가 회복될 자리가 없어 반경이 상한 40 m로 갔다.
    프레임의 84.8%가 교차로가 되고 정지율이 교차로 52.6% 대 링크 27.0%로 뒤집혔다.
    대기열을 교차로로 빨아들인 것이라 부호가 반대다.

    원 대신 사각형도 두 가지로 재봤다. 전역 회랑 겹침과 핵 둘레 국소 상자다. NGSIM
    라벨 대비 F1이 0.33/0.41과 0.34/0.45로 원(0.73/0.74)보다 크게 나빴다. 방위별
    반경(별 모양) 0.56, 셀 기반 0.34도 마찬가지다. 모양을 궤적에서 뽑는 것 자체가
    안 된다. 차선 라벨이 있는 곳에서만 이 규칙을 쓴다.

    이 판본은 부호가 맞는다. 교차로 정지율 7.3%에 중앙속도 22.1 km/h(통과), 링크
    52.9%에 1.0 km/h(대기), 교차로 프레임 9.2%다. 컷도 평탄역에 있다. 축 변경 비율이
    1.00 여섯, 0.31, 0.24, 0.17, 0.15 다음 0.02로 떨어져 0.03~0.14 어디든 같은 집합이다.
    """
    vid = np.asarray(vid)
    lane = np.asarray(lane)
    mv = np.hypot(np.asarray(vx, float), np.asarray(vy, float)) > 1.0
    th = 2 * np.arctan2(np.asarray(vy, float)[mv], np.asarray(vx, float)[mv])
    a = pd.DataFrame({"l": lane[mv], "c": np.cos(th), "s": np.sin(th)})
    a = a.groupby("l").mean()
    axis = (np.degrees(np.arctan2(a["s"], a["c"])) / 2.0) % 180.0
    # 차선이 바뀌는 자리만 남겨 (앞, 현재, 뒤) 삼중을 만든다
    keep = (lane != np.r_[[None], lane[:-1]]) | (vid != np.r_[[None], vid[:-1]])
    t = pd.DataFrame({"v": vid[keep], "l": lane[keep]})
    t["p"] = t.groupby("v")["l"].shift(1)
    t["n"] = t.groupby("v")["l"].shift(-1)
    t = t.dropna()
    e = np.abs(axis.reindex(t["p"]).to_numpy() - axis.reindex(t["n"]).to_numpy())
    t["turn"] = np.minimum(e, 180.0 - e) >= AX_DIFF
    g = t.groupby("l").agg(n=("turn", "size"), sh=("turn", "mean"))
    conn = set(g[(g["sh"] >= CONN_SH) & (g["n"] >= CONN_MIN)].index)
    return np.isin(lane, list(conn))


def real_frames_ind():
    """inD 33녹화 -> 프레임 표. 독일 도심 교차로 넷.

    25 Hz라 위치를 미분하면 안 된다. highD 정답지로 확인했는데 sEMA를 어떤 순서로
    걸어도 제공값의 10배가 넘는 za가 나온다(5.2~6.8 대 0.45). 검출 상자 좌표라
    상자가 떨리고 우리 평활로는 못 지운다. 제공 속도와 종가속을 그대로 쓴다.

    교차로 영역은 OpenDRIVE가 선언한 것이다(map_junction_mask). 네 지점 다 지도가 있고
    좌표계가 궤적과 같아 CARLA와 같은 판정이 된다. 좌표 규칙은 안 쓴다.

    10 Hz로 솎는다. CARLA 수집과 나머지 참조(NGSIM, TGSIM, SinD)가 다 10 Hz인데
    inD만 25 Hz다. 그대로 두면 두 군데가 어긋난다. 하나는 MIN_FR이다. 프레임 수 고정
    30이 다른 데선 3초인데 inD에서만 1.2초라 단위 자격 문턱이 사이트마다 달라진다.
    다른 하나는 산포다. v_sd와 a_sd는 표본 간격에 따라 달라지는 양이라 25 Hz로 잰 것과
    10 Hz로 잰 것을 같은 다양체에 넣으면 그 차이가 거동 차이로 잡힌다.

    보간하지 않고 0.1초 칸마다 첫 프레임을 고른다. 제공 속도와 가속을 그대로 쓰는 자료라
    값을 섞으면 안 되고, 솎기는 CARLA가 0.1초마다 상태를 읽는 것과 같은 동작이다.
    간격이 0.08과 0.12로 번갈아 나오지만 우리가 쓰는 양(v, a, 프레임간 거리)은
    dt를 안 쓴다. highD를 넣게 되면 25 Hz라 같은 처리가 필요하다.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(IND_DIR, "*_tracks.csv"))):
        rec = os.path.basename(f)[:2]
        meta = pd.read_csv(os.path.join(IND_DIR, "%s_tracksMeta.csv" % rec))
        keep = set(meta[meta["class"].isin(["car", "truck_bus"])]["trackId"])
        d = pd.read_csv(f)
        d = d[d["trackId"].isin(keep)].copy()
        if d.empty:
            continue
        d["v"] = np.hypot(d["xVelocity"], d["yVelocity"])
        vmax = d.groupby("trackId")["v"].transform("max")
        d = d[vmax >= VMIN_MOVE]                  # 주차 차량 제거
        if len(d) < MIN_FR:
            continue
        rm = pd.read_csv(os.path.join(IND_DIR, "%s_recordingMeta.csv" % rec)).iloc[0]
        loc = int(rm["locationId"])
        dt = 1.0 / float(rm["frameRate"])
        # 10 Hz로 솎기. 트랙마다 0.1초 칸의 첫 프레임만 남긴다. 보간은 안 한다
        d = d.sort_values(["trackId", "frame"])
        binid = np.floor(d["frame"].to_numpy(float) * dt / DT + 1e-9).astype(np.int64)
        newbin = np.r_[True, (binid[1:] != binid[:-1])
                       | (d["trackId"].to_numpy()[1:] != d["trackId"].to_numpy()[:-1])]
        d = d[newbin]
        vmax = d.groupby("trackId")["v"].transform("max")
        d = d[vmax >= VMIN_MOVE]                  # 솎은 뒤 다시 본다
        if len(d) < MIN_FR:
            continue
        jm = map_junction_mask(d["xCenter"], d["yCenter"], IND_XODR[loc])
        step = np.hypot(d["xCenter"].diff(), d["yCenter"].diff()).fillna(0.0)
        step[d["trackId"] != d["trackId"].shift()] = 0.0
        jm = widen_junction(d["trackId"].to_numpy(), step.to_numpy(), jm)
        d["stratum"] = np.where(jm | JUNCTION_SITE, "junction", "urb_block")
        site = "ind_loc%d" % pd.read_csv(
            os.path.join(IND_DIR, "%s_recordingMeta.csv" % rec)
        ).iloc[0]["locationId"]
        out.append(pd.DataFrame({
            "uid": "ind%s#" % rec + d["trackId"].astype(str),
            "t": d["frame"].to_numpy(float) * dt,
            "v": d["v"].to_numpy(float),
            "a": d["lonAcceleration"].to_numpy(float),
            "step": step.to_numpy(float),
            "head": _head_xy(d["trackId"], d["xCenter"], d["yCenter"]),
            "stratum": d["stratum"].to_numpy(),
            # 통제 방식을 자료가 안 준다. inD 네 지점은 신호가 없는 교차로로 알려져
            # 있으나 파일 안의 값이 아니라 그렇게 표시한다
            "ctrl": np.where(d["stratum"].to_numpy() == "junction", "unsig?", "link"),
            "x": d["xCenter"].to_numpy(float),
            "y": d["yCenter"].to_numpy(float),
            "t_abs": d["frame"].to_numpy(float) * dt,
            "lane": "",
            "site": site,
            # 지점마다 교차로 하나라 회랑이 곧 site다. 기본도표가 서로 다른 회랑을
            # 한 구간으로 묶으면 밀도가 터지므로 가르는 열이다
            "corr": site,
            "hw_adj": False, "width": ""}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


SIND_DIR = os.path.join("data", "sind", "Data")
SIND_MAP = {"Changchun": "Changchun_Pudong.osm",
            "Chongqing": "NR_ll2.osm",
            "Tianjin": "map_relink_law_save.osm",
            "Xi'an": "Xi'an_Shanglin.osm"}
UTM_LON0 = np.radians(3.0)   # 존 31 중앙자오선. 원점 (0,0)이면 zone=floor(180/6)+1=31


def _utm(lat, lon):
    """WGS84 UTM 정투영. SinD 지도 좌표를 궤적 좌표로 옮긴다.

    위경도가 진짜 좌표가 아니다. SinD osm 노드는 0 근처 작은 값이고, 공식 도구가
    lanelet2.projection.UtmProjector(Origin(0, 0))으로 푼다. 즉 적도 원점 UTM이다.

    앞 판본은 도시의 실제 위도(43.8도 등)로 1도당 미터를 잡았다. cos(43.8)=0.72라
    x가 28% 줄어 지도가 궤적과 어긋났고, 교차로가 도로 절반만 덮었다. Changchun에서
    세로 도로 왼쪽 절반이 교차로 밖으로 샜다.

    pyproj가 없어 표준 급수를 직접 쓴다. 점이 원점에서 수백 m 안이라 급수 뒤항은
    필요 없지만 그냥 표준식을 다 쓴다.
    """
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    p = np.radians(np.asarray(lat, float))
    A = (np.radians(np.asarray(lon, float)) - UTM_LON0) * np.cos(p)
    N = a / np.sqrt(1 - e2 * np.sin(p) ** 2)
    T = np.tan(p) ** 2
    C = ep2 * np.cos(p) ** 2
    M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * p
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * np.sin(2 * p)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * np.sin(4 * p)
             - (35 * e2 ** 3 / 3072) * np.sin(6 * p))
    x = k0 * N * (A + (1 - T + C) * A ** 3 / 6
                  + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120)
    y = k0 * (M + N * np.tan(p) * (A ** 2 / 2
                                   + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
                                   + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2)
                                   * A ** 6 / 720))
    return x, y


def sind_nodes(site):
    """SinD osm 노드 id -> (x, y) 미터. 원점 (0,0)의 투영값을 빼 상대 좌표로 만든다."""
    root = ET.parse(os.path.join(SIND_DIR, site, SIND_MAP[site])).getroot()
    ids, la, lo = [], [], []
    for n in root.iter("node"):
        if n.get("lat") is not None:
            ids.append(n.get("id"))
            la.append(float(n.get("lat")))
            lo.append(float(n.get("lon")))
    x, y = _utm(la, lo)
    x0, y0 = _utm(0.0, 0.0)
    return root, dict(zip(ids, zip(x - x0, y - y0)))


def sind_junction_mask(x, y, site):
    """SinD 교차로. Lanelet2가 교차로 lanelet으로 선언한 영역이다.

    지도가 선언한 것을 쓴다. CARLA(xodr junction)와 inD(OpenDRIVE connectingRoad)와
    같은 규칙이 되고, 교차로 안 회전 궤적이 지나는 자리가 통째로 들어온다.

    가리는 법: lanelet 관계에서 좌우 경계가 둘 다 type=virtual인 것. Lanelet2 관례상
    교차로 안은 도색선이 없어 가상 경계를 쓰고, 회랑은 실제 선(line_thin, curbstone,
    guard_rail)을 쓴다. 사이트마다 15~34개가 잡힌다. 횡단보도 lanelet은 경계가
    zebra_marking이라 저절로 빠진다.

    영역은 그 lanelet들을 차로 폭만큼 닫고 구멍을 메운 것이다. 낱개 폴리곤 합집합만
    쓰면 타일 사이에 틈이 남는다. 교차로는 연속된 하나의 포장면이고 lanelet은 그걸
    이동방향별로 쪼갠 것뿐이라, 타일 사이 틈은 지도 표기의 산물이지 도로가 아니다.
    닫기 반경은 자유모수가 아니라 지도가 준 교차로 차로 폭이다(_sind_lane_w). inD가
    이미 같은 꼴이다. 연결로 중심선을 차로 폭만큼 부풀린다.

    볼록껍질로 묶는 것은 못 쓴다. 회전 lanelet이 접근로 쪽으로 길게 뻗은 사이트에서
    껍질이 촬영 범위를 통째로 삼킨다(Chongqing 100%).

    정지선은 판정이 아니라 교차검증에 쓴다. lanelet과 독립된 지도 항목이라 둘이
    맞으면 근거가 된다. 정지선 넷을 연장한 반평면 상자와 견주면 IoU가 Changchun 0.98,
    Xi'an 0.97, Tianjin 0.91이고 상자 안인데 여기 밖인 프레임이 0.2~3.6%다. Chongqing만
    IoU 0.68인데 그 지도가 virtual 경계를 접근부 lanelet에도 써서 교차로 팔이 정지선
    밖으로 뻗기 때문이다. 어느 쪽이 맞는지는 규칙으로 못 가르므로 민감도 축으로 둔다.
    다만 거동은 셋(합집합, 닫기, 정지선 상자)이 사실상 같다. 교차로 정지율 0.0~7.5%,
    링크 43~75%로 결론이 안 흔들린다.

    정지선을 판정에 안 쓰는 이유는 셋이다. 3갈래 교차로에서 반평면 교집합이 한쪽으로
    안 닫힌다. 볼록이라 비뚤어진 교차로에서 포장 아닌 데를 먹는다. foggy_bottom과
    NGSIM에는 정지선이 없어 규칙 통일이 안 된다.

    좌표 환산은 _utm이다. 위경도를 도시 위도로 환산하던 앞 판본은 지도가 궤적과
    어긋나 교차로가 도로 절반만 덮었다. 그게 틈의 진짜 원인이었다.
    """
    polys = _sind_junction_polys(site)
    P = np.c_[np.asarray(x, float), np.asarray(y, float)]
    if not polys:
        return np.zeros(len(P), bool)
    return _close_fill(P, polys, _sind_lane_w(polys))


SIND_CELL = 0.5     # m. 닫기용 격자. 차로 폭 3~4 m에 견줘 충분히 잘다


def _sind_junction_polys(site):
    """교차로 lanelet 폴리곤들. 좌경계 + 뒤집은 우경계."""
    root, coord = sind_nodes(site)
    wpts, wtype = {}, {}
    for w in root.iter("way"):
        t = [c.get("v") for c in w.findall("tag") if c.get("k") == "type"]
        wtype[w.get("id")] = t[0] if t else ""
        wpts[w.get("id")] = [coord[nd.get("ref")] for nd in w.findall("nd")
                             if nd.get("ref") in coord]
    out = []
    for r in root.iter("relation"):
        if not any(c.get("k") == "type" and c.get("v") == "lanelet"
                   for c in r.findall("tag")):
            continue
        side = {m.get("role"): m.get("ref") for m in r.findall("member")
                if m.get("role") in ("left", "right")}
        if len(side) != 2 or any(wtype.get(v) != "virtual" for v in side.values()):
            continue
        L, R = wpts.get(side["left"], []), wpts.get(side["right"], [])
        if len(L) >= 2 and len(R) >= 2:
            out.append(np.asarray(L + R[::-1], float))
    return out


def _sind_lane_w(polys):
    """교차로 차로 폭 중앙값. 양 끝 단면에서 좌우 경계 사이 거리를 잰다."""
    ws = []
    for q in polys:
        n = len(q) // 2
        L, R = q[:n], q[n:][::-1]
        ws += [float(np.hypot(*(L[0] - R[0]))), float(np.hypot(*(L[-1] - R[-1])))]
    ws = [w for w in ws if w > 0.5]
    return float(np.median(ws)) if ws else 3.5


def _close_fill(P, polys, rad):
    """폴리곤 합집합을 rad만큼 닫고 구멍을 메운 뒤 점 소속을 본다.

    격자로 한다. 폴리곤 불리언 연산 라이브러리(shapely)를 안 쓰려는 것이고, 셀이
    0.5 m라 차로 폭 대비 충분히 잘다.
    """
    from matplotlib.path import Path
    from scipy import ndimage
    Q = np.vstack(polys)
    lo = Q.min(axis=0) - rad * 3
    hi = Q.max(axis=0) + rad * 3
    nx = int((hi[0] - lo[0]) / SIND_CELL) + 1
    ny = int((hi[1] - lo[1]) / SIND_CELL) + 1
    gx, gy = np.meshgrid(lo[0] + np.arange(nx) * SIND_CELL,
                         lo[1] + np.arange(ny) * SIND_CELL)
    G_ = np.c_[gx.ravel(), gy.ravel()]
    img = np.zeros(len(G_), bool)
    for q in polys:
        img |= Path(q).contains_points(G_)
    img = img.reshape(ny, nx)
    r = max(int(round(rad / SIND_CELL)), 1)
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    img = ndimage.binary_fill_holes(ndimage.binary_closing(img, xx ** 2 + yy ** 2
                                                           <= r * r))
    ix = np.floor((P[:, 0] - lo[0]) / SIND_CELL).astype(int)
    iy = np.floor((P[:, 1] - lo[1]) / SIND_CELL).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    out = np.zeros(len(P), bool)
    out[ok] = img[iy[ok], ix[ok]]
    return out


SIND_CAR = {"car", "truck", "bus"}   # 이륜차와 삼륜차는 뺀다. 톈진 611트랙 중 335가
                                     # 오토바이, 자전거, 삼륜이라 안 빼면 참조가 그쪽이 된다


def real_frames_sind():
    """SinD 공개 샘플 4사이트 -> 프레임 표. 중국 신호교차로.

    10 Hz라 주기가 우리와 같고 평활본이라 제공 속도와 가속을 그대로 쓴다. 제공 a_lon이
    제공 v_lon의 생미분과 0.99배로 일치해 NGSIM 같은 이중미분 폭발이 없다.

    교차로 영역은 Lanelet2 정지선의 볼록껍질이다(sind_junction_mask). 정지선이
    교차로의 법적 경계라 좌표로 추정할 이유가 없다.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(SIND_DIR, "*", "*",
                                           "Veh_smoothed_tracks.csv"))):
        site = f.replace("\\", "/").split("/")[-3]
        d = pd.read_csv(f)
        d = d[d["agent_type"].isin(SIND_CAR)].copy()
        if d.empty:
            continue
        d["v"] = np.hypot(d["vx"], d["vy"])
        vmax = d.groupby("track_id")["v"].transform("max")
        d = d[vmax >= VMIN_MOVE]              # 주차 차량 제거
        if len(d) < MIN_FR:
            continue
        step = np.hypot(d["x"].diff(), d["y"].diff()).fillna(0.0)
        step[d["track_id"] != d["track_id"].shift()] = 0.0
        jm = widen_junction(d["track_id"].to_numpy(), step.to_numpy(),
                            sind_junction_mask(d["x"], d["y"], site))
        d["stratum"] = np.where(jm | JUNCTION_SITE, "junction", "urb_block")
        out.append(pd.DataFrame({
            "uid": "sind_%s#" % site + d["track_id"].astype(str),
            "t": d["timestamp_ms"].to_numpy(float) / 1000.0,
            "v": d["v"].to_numpy(float),
            "a": d["a_lon"].to_numpy(float),
            "step": step.to_numpy(float),
            "head": _head_xy(d["track_id"], d["x"], d["y"]),
            "stratum": d["stratum"].to_numpy(),
            # 네 사이트 다 신호 교차로다. 자료가 프레임별 신호 상태까지 주는 유일한
            # 참조인데 지금 서명이 그걸 안 쓴다. 표시만 해둔다
            "ctrl": np.where(d["stratum"].to_numpy() == "junction", "signal", "link"),
            "x": d["x"].to_numpy(float), "y": d["y"].to_numpy(float),
            "t_abs": d["timestamp_ms"].to_numpy(float) / 1000.0,
            "lane": "", "corr": "sind_%s" % site,
            "site": "sind_%s" % site, "hw_adj": False, "width": ""}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# x, y, t_abs는 기본도표(유량-밀도-속도)용이다. t는 안 건드린다. 단위 자르기가
# t 연속성으로 run을 끊어서 거기를 만지면 지금까지의 모든 결과가 달라진다.
# t_abs는 자료가 준 절대 시각이고, 같은 시각의 차들을 모을 때만 쓴다
COLS = ["uid", "t", "v", "a", "step", "head", "stratum", "ctrl", "site",
        "hw_adj", "width", "x", "y", "t_abs", "lane", "corr"]


HIGHD_DIR = os.path.join("data", "highD-dataset-v1.0", "data")
HIGHD_CAR = {"Car"}          # 트럭은 뺀다. 다른 자료도 승용만 쓴다


def real_frames_highd(max_rec=0):
    """highD -> 프레임 표. 독일 아우토반 60촬영, 지점 6곳, 25 Hz 드론.

    넣은 이유. 기존 고속 참조가 NGSIM과 TGSIM인데 둘 다 미국 정체 구간 촬영이라
    중앙 속도가 10.5 m/s다. CARLA(15.0)보다 느려서 "느린 arm이 실측을 잘 맞히는" 뒤집힘이
    생겼다. tm_stock(목표속도 70%)이 고속 덮개에서 원본과 같게 나온 게 그 증상이다.
    highD는 자유류가 들어 있어 그 편향을 없앤다.

    25 Hz를 10 Hz 격자로 보간해서 넣는다. 계측 스택(sEMA 평활 후 미분)이 dt를 고정으로
    쓰므로 다른 자료와 같은 눈금이어야 한다. 위치 보간이라 단조 시간축에서 안전하다.

    종방향은 x다. 위쪽 차로는 -x로 달려서 부호가 반대인데 거리와 속력만 쓰므로 상관없다.
    y(횡방향)는 폭이 20 m뿐이라 차로변경 몫만 들어온다.

    자료가 dhw/thw/ttc와 precedingId를 직접 준다. 지금 채점은 자차 좌표만 쓰므로 안 쓰지만
    선행차 짝이 필요해지면 유도 없이 바로 나온다.
    """
    out = []
    metas = sorted(glob.glob(os.path.join(HIGHD_DIR, "*_recordingMeta.csv")))
    if max_rec:
        metas = metas[:max_rec]
    for mf in metas:
        rec = os.path.basename(mf).split("_")[0]
        meta = pd.read_csv(mf).iloc[0]
        fps = float(meta["frameRate"])
        loc = int(meta["locationId"])
        tm = pd.read_csv(os.path.join(HIGHD_DIR, "%s_tracksMeta.csv" % rec))
        keep = set(tm[tm["class"].isin(HIGHD_CAR)]["id"])
        d = pd.read_csv(os.path.join(HIGHD_DIR, "%s_tracks.csv" % rec),
                        usecols=["frame", "id", "x", "y", "laneId"])
        d = d[d["id"].isin(keep)]
        if d.empty:
            continue
        for vid, g in d.groupby("id", sort=False):
            g = g.sort_values("frame")
            tt = g["frame"].to_numpy(float) / fps
            if tt[-1] - tt[0] < 5 * DT:
                continue
            # 10 Hz 균일 격자로 보간. 25 Hz를 그냥 솎으면 간격이 2.5프레임이라 안 맞는다
            tn = np.arange(tt[0], tt[-1], DT)
            if len(tn) < 5:
                continue
            out.append(_units_from_xy(
                "highd_%s_%d" % (rec, int(vid)),
                np.interp(tn, tt, g["x"].to_numpy(float)),
                np.interp(tn, tt, g["y"].to_numpy(float)),
                "hw_link", "link", "highd_loc%d" % loc, t_abs=tn,
                lane=g["laneId"].to_numpy()[
                    np.searchsorted(tt, tn).clip(0, len(tt) - 1)]))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=COLS)


def real_frames(sources="ngsim,tgsim"):
    """출처를 골라 합친다. 출처별로 따로 채점할 수 있어야 하므로 site 열을 남긴다.

    합치는 게 주 보고이고 출처별은 반드시 같이 낸다. 계측 품질이 출처마다 다르기
    때문이다. NGSIM은 위치를 이중미분해 가속을 얻는데 TGSIM은 칼만이 걸려 있고,
    그 차이가 za와 a산포에 크게 들어온다(고속에서 NGSIM 4.76 대 TGSIM 1.79).
    절대값은 참조 폭에 좌우되므로 천장 대비와 순위로만 읽는다.
    """
    want = set(sources.split(","))
    out = []
    for nm, fn in (("ngsim", real_frames_ngsim), ("tgsim", real_frames_tgsim),
                   ("ind", real_frames_ind), ("sind", real_frames_sind),
                   ("highd", real_frames_highd)):
        if nm in want:
            out.append((nm, fn()))
    got = []
    for nm, d in out:
        if not len(d):
            continue
        if "head" not in d:
            d["head"] = np.nan        # NGSIM 회랑은 1차원이라 방향이 안 나온다
        if "corr" not in d:
            d["corr"] = d["site"]     # 회랑을 안 가르는 출처. _units_from_xy와 같은 기본값
        # 여기까지 오는 데 한 시간이 든다. 열 하나 때문에 KeyError로 죽으면 그걸
        # 통째로 버린다. 어느 로더가 뭘 빠뜨렸는지 찍고 죽어야 한 번에 고친다
        miss = [c for c in COLS if c not in d]
        if miss:
            raise KeyError("%s 로더에 열이 없다: %s" % (nm, ", ".join(miss)))
        got.append(d[COLS])
    return pd.concat(got, ignore_index=True) if got else pd.DataFrame(columns=COLS)


def ceiling(sig, seeds=8):
    """차량 단위로 반씩 갈라 서로 채점. 한 차량이 창을 여럿 내므로 창으로 가르면 안 된다."""
    veh = sig["uid"].unique()
    ps, qs = [], []
    for s in range(seeds):
        idx = np.random.RandomState(s).permutation(len(veh))
        h = len(veh) // 2
        A = sig[sig["uid"].isin(veh[idx[:h]])][SIG].to_numpy(float)
        B = sig[sig["uid"].isin(veh[idx[h:2 * h]])][SIG].to_numpy(float)
        if min(len(A), len(B)) < K + 1:
            continue
        pr = precision_recall(A, B, k=K, seed=s)
        ps.append(pr["precision"])
        qs.append(pr["recall"])
    if not ps:
        return (np.nan,) * 4 + (0,)
    return (float(np.mean(ps)), float(np.std(ps, ddof=1)),
            float(np.mean(qs)), float(np.std(qs, ddof=1)), len(veh) // 2)


def main_segments():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="", help="arm 이름(grp) 쉼표 구분. 비우면 전부")
    # 맵이 Town03, Town06으로 박혀 있었다. 진단용일 때는 그걸로 됐지만
    # 사다리를 P/R로 재려면 다른 자들과 같은 여덟 맵이어야 짝이 맞는다
    ap.add_argument("--towns", default="Town01,Town02,Town03,Town04,"
                                       "Town05,Town06,Town07,Town10HD")
    ap.add_argument("--seeds", type=int, default=8, help="천장 분할 반복")
    ap.add_argument("--sources", default="ngsim,tgsim,ind,sind", help="참조 출처")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    lines = []

    def emit(s):
        print(s, flush=True)
        lines.append(s)

    emit("단위 채점. 층이 안 바뀌는 동안 이동한 구간이 한 점이다. 도로 경계로는 안 끊는다.")
    emit("링크 층은 고정 거리 창, 교차로는 통과 전체가 한 단위다.")
    emit("L: %s. 창 최소 %d프레임(고정 창의 속도 상한 %s km/h), 층 최소 %d단위, "
         "|a| <= %g m/s^2(양쪽 동일)."
         % (LEN, MIN_FR,
            "/".join("%s %.0f" % (k, 3.6 * v / (MIN_FR * DT))
                     for k, v in LEN.items() if v),
            MIN_UNIT, A_MAX))
    emit("교차로는 지도가 선언한 것 그대로%s. inD와 SinD는 사이트 전체가 교차로."
         % ("" if not JUNC_D else " + 경로 따라 앞뒤 %.0f m" % JUNC_D))
    emit("층 셋. 교차로 통제 방식과 고속 인접은 층이 아니라 열이다(실측이 신호를 모른다).")
    emit("서명 %s. 선행차 좌표는 안 쓴다." % SIG)

    real = units(real_frames(a.sources), extra=("ctrl", "site", "width"))
    # 참조 변형. arm 단위 만드는 게 비싸고 k-NN은 싸므로 한 번 읽어 여럿에 동시에 잰다.
    # 합침이 주 보고이고 나머지는 결론이 그 선택에 흔들리는지 보는 용도다
    VAR = [("합침", lambda d: d),
           ("NGSIM", lambda d: d[d["site"] == "ngsim"]),
           ("TGSIM", lambda d: d[d["site"].str.startswith("tgsim")]),
           ("inD", lambda d: d[d["site"].str.startswith("ind_")]),
           ("SinD", lambda d: d[d["site"].str.startswith("sind_")]),
           ("신호만", lambda d: d[d["ctrl"] != "unsig?"])]
    emit("")
    emit("== 실측  (참조 %s)" % a.sources)
    emit("%-12s %8s %6s %10s %14s %14s"
         % ("stratum", "단위", "지점", "중앙길이m", "천장 P", "천장 R"))
    ref = {}
    for st, sub in real.groupby("stratum"):
        if len(sub) < MIN_UNIT:
            continue
        for name, fn in VAR:
            v = fn(sub)
            if len(v) >= MIN_UNIT * 5:
                ref[(st, name)] = v
        cp, cps, cq, cqs, nh = ceiling(sub, a.seeds)
        emit("%-12s %8d %6d %10.0f %14s %14s"
             % (st, len(sub), sub["site"].nunique(), sub["length"].median(),
                "%.3f+-%.3f" % (cp, cps), "%.3f+-%.3f" % (cq, cqs)))
    var_names = [n for n, _ in VAR if any(k[1] == n for k in ref)]

    want = set(x for x in a.arms.split(",") if x)
    adj = {t: hw_adjacent(t) for t in a.towns.split(",") if t}
    per_seed = {}
    for f in sorted(glob.glob(os.path.join(S.ARM_DIR, "*.csv"))):
        m = re.match(r"(.+)_(Town[0-9A-Za-z]+)_s(\d+)\.csv$", os.path.basename(f))
        if not m:
            continue
        arm, town, seed = m.group(1), m.group(2), int(m.group(3))
        # 접두사 매칭은 안 쓴다. arm 이름이 서로의 접두사라 tm_knob 하나가
        # tm_knob_w33, tm_knob_domain까지 물어 온다. 다른 적재기와 같은 규칙
        g = grp_of(arm)
        if (want and g not in want) or town not in adj:
            continue
        u = units(carla_frames(f, town, adj[town]),
                  extra=("ctrl", "site", "hw_adj"))
        if u.empty:
            continue
        for st, sub in u.groupby("stratum"):
            if len(sub) < MIN_UNIT:
                continue
            A = sub[SIG].to_numpy(float)
            for name in var_names:
                R = ref.get((st, name))
                if R is None:
                    continue
                pr = precision_recall(R[SIG].to_numpy(float), A, k=K)
                per_seed.setdefault((arm, town, st, name), []).append(
                    (seed, pr["precision"], pr["recall"], len(sub)))

    emit("")
    emit("== arm   (`+-`는 수집 시드 사이 퍼짐 = 경로 잡음 바닥)")
    emit("주 보고는 참조 합침이다. 나머지 참조는 결론이 그 선택에 흔들리는지 보는 것이다.")
    emit("%-30s %-8s %-12s %-8s %7s %14s %14s"
         % ("arm", "town", "stratum", "참조", "단위", "precision", "recall"))
    rows = []
    for (arm, town, st, var), vs in sorted(per_seed.items()):
        p = np.array([x[1] for x in vs])
        q = np.array([x[2] for x in vs])
        n = np.array([x[3] for x in vs])

        def cell(v):
            return "%.3f+-%.3f" % (v.mean(), v.std(ddof=1)) if len(v) > 1 \
                else "%.3f" % v.mean()

        emit("%-30s %-8s %-12s %-8s %7d %14s %14s"
             % (arm, town, st, var, n.mean(), cell(p), cell(q)))
        rows.append({"arm": arm, "town": town, "stratum": st, "ref": var,
                     "seeds": len(vs),
                     "precision": p.mean(),
                     "precision_sd": p.std(ddof=1) if len(p) > 1 else np.nan,
                     "recall": q.mean(),
                     "recall_sd": q.std(ddof=1) if len(q) > 1 else np.nan,
                     "n_unit": n.mean()})

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "segments.csv"), index=False)
    pd.DataFrame([{"arm": k[0], "town": k[1], "stratum": k[2], "ref": k[3],
                   "seed": v[0], "precision": v[1], "recall": v[2], "n_unit": v[3]}
                  for k, vs in per_seed.items() for v in vs]
                 ).to_csv(os.path.join(OUT, "per_seed.csv"), index=False)
    real.to_csv(os.path.join(OUT, "real_units.csv"), index=False)
    dst = os.path.join(OUT, "segments.txt")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\nwrote %s" % dst)
    return 0


# ==========================================================================
# manifold_pr: sEMA 평활과 다양체 precision/recall
# ==========================================================================
"""평활(sEMA)과 다양체 precision/recall. score_segments와 score_strata가 쓴다.

상위 연구의 계측 패키지에 있던 것을 그대로 옮겼다. 그 패키지 나머지는 폐기된 채점 틀이라
안 들고 왔고, 실제로 쓰이는 건 이 파일의 둘뿐이다. 숫자를 바꾸면 안 되므로 함수 본문은
손대지 않았다. 논문 표가 이 코드로 나온 값이다.

_sema        NGSIM 평활 표준 처방(Thiemann et al. 2008). 속도와 가속도가 여기서 나온다
precision_recall  Kynkaanniemi et al. 2019 정의. recall이 논문의 실측 덮개다
"""

SEMA_DELTA = 0.5         # sEMA 평활폭 Δ(초) -- Thiemann et al. 2008이 NGSIM에 권장한 값


def _sema(x, dt=0.1, delta=SEMA_DELTA):
    """대칭 지수이동평균(Thiemann et al. 2008, TRR 2088) -- NGSIM 궤적 평활 표준 처방.

    x̃(t_i) = Σ_j x(t_j)·exp(-|t_i-t_j|/Δ) / Σ_j exp(-|t_i-t_j|/Δ), 창은 ±3Δ를 궤적 양끝에서
    대칭 절단(편향 방지). 내부는 컨볼루션, 가장자리 3Δ/dt개만 대칭 절단으로 재계산."""
    x = np.asarray(x, float)
    n = len(x)
    wing = int(round(3.0 * delta / dt))
    # 커널이 신호보다 길면 np.convolve(mode='same')이 커널 길이를 반환해 출력 길이가 어긋난다.
    # 짧은 궤적에선 창을 신호 길이에 맞춰 방어한다(약하게 평활되지만 길이는 항상 맞다).
    wing = min(wing, (n - 1) // 2)
    if n < 3 or wing < 1:
        return x.copy()
    off = np.arange(-wing, wing + 1)
    ker = np.exp(-np.abs(off) * dt / delta)              # 대칭 지수 커널
    num = np.convolve(x, ker, mode="same")
    den = np.convolve(np.ones(n), ker, mode="same")      # 정규화(경계 가중합)
    out = num / den
    edge = list(range(min(wing, n))) + list(range(max(0, n - wing), n))
    for i in set(edge):                                  # 가장자리는 대칭 절단으로 바로잡음
        h = min(wing, i, n - 1 - i)
        w = ker[wing - h: wing + h + 1]
        out[i] = np.dot(w, x[i - h: i + h + 1]) / w.sum()
    return out


K_DEFAULT = 3
MAX_SAMPLES = 6000          # k-NN 비용 상한(표본 추출은 시드 고정)


def std_normalizer(real, arm):
    """특성별 표준편차로 나눈다 (사전등록 기본값).

    동결본 인라인 정규화와 수치 동일 -- 실참조 std로 각 열을 나누되, 거의 상수인 열은 sd->1.0.
    """
    real = np.asarray(real, float)
    arm = np.asarray(arm, float)
    sd = real.std(axis=0)
    sd = np.array(sd, float)
    sd[sd < 1e-9] = 1.0
    return real / sd, arm / sd


def _sub(x, n=MAX_SAMPLES, seed=0):
    if len(x) <= n:
        return x
    return x[np.random.RandomState(seed).choice(len(x), n, replace=False)]


def _radii(x, k):
    """각 표본의 k번째 최근접 이웃까지 거리 = 그 표본이 대표하는 다양체 반경.

    cKDTree로 정확 k-NN. 브루트포스 전체 거리행렬과 같은 유클리드 값이지만, 6000점 5축이면
    호출당 1.4GB 임시배열에 ~5초 걸리던 게 트리로 밀리초대. query(k+1)의 첫 열은 자기 자신
    (거리 0)이라 k번째 열이 자기 제외 k번째 이웃 거리(브루트포스 대각 inf 처리와 동치)."""
    dd, _ = cKDTree(x).query(x, k=k + 1)
    return np.asarray(dd)[:, k]


def _in_manifold(query, ref, ref_radii):
    """query 각 점이 ref 다양체(가변 반경 반구들의 합집합) 안에 있는가.

    q가 안에 있음 = 어떤 ref점 r에 대해 dist(q,r) <= radius[r]. ref별 반경 반구가 덮는 query를
    cKDTree ball query로 모은다. 브루트포스 (d <= ref_radii).any(axis=1)와 같은 조건이고
    트리라 훨씬 빠르다(멤버십은 정확)."""
    if len(ref) == 0 or len(query) == 0:
        return np.zeros(len(query), bool)
    qtree = cKDTree(query)
    out = np.zeros(len(query), bool)
    for idx in qtree.query_ball_point(ref, ref_radii):     # ref점별로 덮인 query 인덱스
        if len(idx):
            out[idx] = True
    return out


def _estimate_manifold(points, k, radius_cap=None, trim=None):
    """다양체 추정 = (대표점, 반경). 기본값(cap, trim 모두 None)은 전체 점과 k번째 이웃 반경.

    이상치 방어 자유모수(기본 off):
      trim       상위 분위수 반경을 갖는 점을 제거하고 반경을 다시 계산한다.
      radius_cap 반경을 그 분위수에서 상한 잡아, 이상치 하나가 반경을 키워 다양체를 부풀리는
                 것을 완화한다.
    단순 k-NN 반경은 이상치 하나가 반경을 키워 precision을 부풀리는 약점이 있다
    (Kynkaanniemi 2019). 이 자유모수로 완화하며, 둘 다 None이면 동결본과 수치가 같다.
    """
    r = _radii(points, k)
    if trim:
        keep = r <= np.quantile(r, 1.0 - trim)
        points = points[keep]
        r = _radii(points, k)
    if radius_cap is not None:
        r = np.minimum(r, np.quantile(r, radius_cap))
    return points, r


def precision_recall(real, arm, k=K_DEFAULT, seed=0, normalizer=None,
                     radius_cap=None, trim=None):
    """실 다양체 기준 precision(실제성)과 recall(덮는 정도). 실 표준편차로 정규화한다.

    NaN 행은 제거한다. THW는 정지 시 정의되지 않아 NaN이고, 하나라도 섞이면 거리 비교가
    전부 False가 되어 P/R이 0으로 붕괴한다(실제로 천장값이 0.000으로 나온 적이 있다).

    normalizer: (real, arm) -> (real_norm, arm_norm), 실 참조로 fit한다. 기본 None은 사전등록된
      표준편차 정규화로 원본과 같다. 민감도에서 Mahalanobis, 분위수 대안을 넣는다.
    radius_cap, trim: 이상치 방어 자유모수(기본 None이면 동결본과 동일). _estimate_manifold 참조.
    """
    real = real[np.isfinite(real).all(axis=1)]
    arm = arm[np.isfinite(arm).all(axis=1)]
    if len(real) < k + 1 or len(arm) < k + 1:
        return {"precision": np.nan, "recall": np.nan, "n_real": len(real), "n_arm": len(arm)}
    norm = normalizer or std_normalizer
    real_n, arm_n = norm(real, arm)
    R = _sub(real_n, seed=seed)
    A = _sub(arm_n, seed=seed)
    Rref, Rrad = _estimate_manifold(R, k, radius_cap, trim)
    Aref, Arad = _estimate_manifold(A, k, radius_cap, trim)
    pr = float(_in_manifold(A, Rref, Rrad).mean())      # arm이 실 다양체 안에 있는 비율
    rc = float(_in_manifold(R, Aref, Arad).mean())      # 실이 arm 다양체 안에 덮이는 비율
    return {"precision": pr, "recall": rc, "n_real": len(R), "n_arm": len(A)}


if __name__ == "__main__":
    import run as _run          # 콘솔을 UTF-8로. 셸이 cmd든 bash든 한글이 안 깨지게
    _run.console()
    _CMDS = {
        "maps": _report,        # 층 경계와 회전교차로 판정. data/outputs/map_partition/
        "strata": main_strata,
        "lanes": main_lanes,
        "segments": main_segments,
    }
    _c = sys.argv[1] if len(sys.argv) > 1 else ""
    if _c not in _CMDS:
        sys.stderr.write(
            "usage: python scripts/measure.py {maps,strata,lanes,segments} [options]\n")
        raise SystemExit(2)
    del sys.argv[1]
    _CMDS[_c]()
