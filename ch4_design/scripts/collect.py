"""CARLA 수집. py3.7 + CARLA 0.9.10 서버. 채점 쪽에서 import 하지 말 것.

  py -3.7 scripts/collect.py collect --arm plain --town Town03 --seed 0
  py -3.7 scripts/collect.py sweep --dry-run        계획만
  py -3.7 scripts/collect.py sweep                  8 맵 x 8 시드 x 3 조건
  py -3.7 scripts/collect.py sweep --towns Town03 --seeds 1    파일럿

출처: C:\\Github\\sil-agent-repertoire scripts/collect.py (커밋 a604b91)의 run, main_collect,
main_sweep. 바꾼 것:
- 조건을 plain, knob, tree 셋으로. 앞 판 이름으로 tm_base, tm_knob, bt_tree와 같은 설정
- offset은 2026-11-03 사후 추가. 앞 판 bt_knob_tree_offset 자리인데 합성을 기준값 대비 차이로,
  자르기를 KNOBS 상자로 바꿈(bt_runtime Act.update). 추첨값, 트리 배정 모두 같은 시드의 knob, tree와 같음
- offkr은 2026-11-04 사후 추가. offset에서 keep_right만 추첨값에 고정. offset의 추돌 초과가
  keep_right 동적 변경 탓으로 진단돼서(diag nokr, offkr) 넣음. 진단 offkr 실행과 같은 설정
- knobw2, offkrw2는 2026-11-05 사후 추가. 추첨 상자를 기준값 축으로 x2(bt_nodes.widen, 앞 판
  scale과 같은 정의). offkrw2는 그 추첨 위 offkr이고 합성 결과도 x2 상자로 자름. 앞 판 tree가
  knob을 이긴 게 트리 값이 상자 밖(gap 차량 평균 최대 66 m)이라서였는지, 같은 넓은 상자에서도
  트리가 더하는지 가르려고 넣음. 짝은 knobw2 대 offkrw2
- knobw05, offkrw05는 2026-11-05 사후 추가. 같은 정의로 x0.5. 폭 셋(x0.5, x1, x2)에서 같은 폭 짝을
  보려고 넣음(앞 판 그림 3 자리). x0.5를 고른 건 판단: x2에서 이미 실측 칸 밖 단위가 늘어서 더 넓히는
  쪽보다 좁히는 쪽을 봄
- treero, offkrro, offkrw05ro, offkrw2ro는 2026-11-10 사후 추가. 트리만 도달 불가 재배치판
  (data/outputs/bt_ro, scripts/reorder_trees.py)으로 바꾼 같은 조건. rng가 시드만 봐서 같은 시드 원본과
  추첨값, 트리 배정, 트리 파라미터가 같음. 트리 묶음은 이름 끝 ro로 고르고 섞이면 막음(trees_of_arm)
- tree3는 2026-11-19 추가(실험 3, docs/design_exp3.md). offset 종류에 실험 3 컴파일 트리(bt/exp3_r*).
  --tick-log를 주면 조건과 상관없이 Perception을 돌려 차량별 틱마다 지각값과 끝 명령으로 남은 행동 id를
  *_tick.csv.gz에 씀. 지각은 읽기만 해서 TM에 안 닿음(동기 모드라 계산 시간도 결과에 안 섞임)
- 배경차 전원에 충돌 센서. *_coll.csv에 사건 기록(앞 판은 배경차 충돌을 안 셌음)
- 트리 행동 값 클램프 횟수를 *_plan.csv에 남김. 0이어야 정상
- ego, 재추첨, 주변분포 대조군, 폭 배수, 시나리오 모드 삭제

원자료만 저장하고 채점은 measure.py. 속도와 가속은 여기서 안 구함(실측과 같은 평활 스택을
채점 쪽에서 걸어야 함).

정리 순서 주의(앞 판에서 두 번 당함). 동기 모드인 채로 destroy하면 서버가 죽고, 삭제를 비동기로
던지고 바로 load_world하면 먹통. 그래서 비동기로 돌린 뒤 apply_batch_sync로 기다림.
"""
import argparse
import csv
import glob
import gzip
import io
import json
import os
import random
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
CARLA_ROOT = os.environ.get("CARLA_ROOT", "C:/CARLA/WindowsNoEditor")
_egg = glob.glob(os.path.join(CARLA_ROOT, "PythonAPI", "carla", "dist",
                              "carla-*py3.7-win-amd64.egg"))
if _egg:
    sys.path.append(_egg[0])
try:
    import carla  # noqa: E402
except ImportError:
    carla = None

import bt_runtime as RT  # noqa: E402
from bt_nodes import KNOBS, LANE_CHANGE_P, validate, widen  # noqa: E402

OUT = os.path.join(ROOT, "data", "outputs", "arms")
DT = 0.1
TOWNS = ["Town01", "Town02", "Town03", "Town04", "Town05", "Town06", "Town07", "Town10HD"]
# 조건 -> (종류, 상자 배수). 아래 TREE_ARMS 등은 종류 기준. 넓힌 조건은 같은 배수끼리만 짝지음
SPEC = {"plain": ("plain", 1), "knob": ("knob", 1), "tree": ("tree", 1),
        "offset": ("offset", 1), "offkr": ("offkr", 1),
        "knobw2": ("knob", 2), "offkrw2": ("offkr", 2),
        "knobw05": ("knob", 0.5), "offkrw05": ("offkr", 0.5),
        "treero": ("tree", 1), "offkrro": ("offkr", 1),
        "offkrw05ro": ("offkr", 0.5), "offkrw2ro": ("offkr", 2),
        "treec": ("tree", 1), "treef": ("tree", 1), "tree3": ("offset", 1),
        # 실험 4. pknob은 같은 시드 ptree 차량의 측정 구간 평균 적용값을 상수로(트리 안 돌림)
        "ptree": ("offset", 1), "pknob": ("pknob", 1),
        # 수리 효과(2026-11-25 사후, docs/design/design_exp4.md). ptree와 같은 종류, 트리만 수리 전 초안 실행판
        "pdraft": ("offset", 1)}
ARMS = list(SPEC)
TREE_ARMS = ("tree", "offset", "offkr")
OFFSET_ARMS = ("offset", "offkr")
FIXED = {"offkr": ("keep_right",)}
TREES = os.path.join(ROOT, "data", "outputs", "bt", "prop_r*", "tree_*.xml")
TREES_RO = os.path.join(ROOT, "data", "outputs", "bt_ro", "prop_r*", "tree_*.xml")
# 보조 실험 9: 카탈로그 v1 재생성(대조), v2 재생성. 조건 이름으로만 고름
TREES_FIX = {"treec": os.path.join(ROOT, "data", "outputs", "bt", "fixc_r*", "tree_*.xml"),
             "treef": os.path.join(ROOT, "data", "outputs", "bt", "fixf_r*", "tree_*.xml"),
             # 실험 3. 조건 이름으로 트리 묶음을 못 박는 용도가 같아서 여기 둠
             "tree3": os.path.join(ROOT, "data", "outputs", "bt", "exp3_r*", "tree_*.xml"),
             "ptree": os.path.join(ROOT, "data", "outputs", "bt", "exp4_*", "tree_*.xml"),
             # 폴더, 파일 이름이 ptree와 같아 정렬 순서가 같음. 그래서 같은 시드 같은 자리에 같은 번호 트리
             "pdraft": os.path.join(ROOT, "data", "outputs", "bt_draft", "exp4_*", "tree_*.xml")}
FRAME_HEADER = "vid,t,x,y,yaw,road_id,lane_id,is_junction,length,width,tl_state\n"
# 틱 기록. Perception이 채운 p 그대로. fired는 끝 명령으로 남은 행동 id(실험 3 트리만 이름이 있음)
TICK_COLS = ["speed_kmh", "lead", "lead_stopped", "gap_m", "dv", "in_junction", "junction_m", "tl",
             "limit_kmh", "rightmost", "leftmost", "lane_count", "lane_time_s", "curvature"]
TICK_HEADER = "vid,t," + ",".join(TICK_COLS) + ",fired\n"


def sample_knobs(rng, box=None):
    """차량 하나의 추첨값. KNOBS 키 순서대로 뽑음(순서 바꾸면 같은 시드에서 다른 값).
    box는 넓힌 상자. rng 소비 횟수가 같아서 같은 시드면 x1 추첨을 늘린 값이 됨(짝 유지)"""
    v = {}
    for k, (_b, lo, hi) in KNOBS.items():
        if box:
            lo, hi = box[k]
        v[k] = rng.uniform(lo, hi)
    v["lane_change"] = rng.random() < LANE_CHANGE_P
    return v


def apply_knobs(tm, veh, k):
    """차량별 TM 설정 전부 명시. TM 인스턴스가 load_world를 넘어 살아남아 앞 값이 남기 때문"""
    tm.vehicle_percentage_speed_difference(veh, k["speed_diff"])
    tm.distance_to_leading_vehicle(veh, k["gap_m"])
    tm.auto_lane_change(veh, k["lane_change"])
    tm.set_percentage_keep_right_rule(veh, k["keep_right"])
    tm.ignore_lights_percentage(veh, k["ign_lights"])
    tm.ignore_signs_percentage(veh, k["ign_signs"])
    tm.ignore_vehicles_percentage(veh, k["ign_vehicles"])
    tm.ignore_walkers_percentage(veh, k["ign_walkers"])


def pknob_consts(plan_path):
    """ptree 계획의 want(측정 구간 평균/표준편차)에서 spawn_idx별 상수 설정. lane_change는 허용 틱 비율 0.5 이상이면
    허용(판단). want가 빈 차(트리 못 돈 차)는 빠짐, 그 자리는 호출 쪽이 추첨값을 씀"""
    out = {}
    with io.open(plan_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        w = {}
        for kv in (r.get("want") or "").split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                w[k] = float(v.split("/")[0])
        if not all(k in w for k in KNOBS) or "lane_change" not in w:
            continue
        c = dict((k, w[k]) for k in KNOBS)
        c["lane_change"] = w["lane_change"] >= 0.5
        out[int(r["spawn_idx"])] = c
    return out


def trees_of_arm(arm, given=None):
    """ro 조건은 재배치 트리(bt_ro), 나머지는 원본. 섞이면 원본 대비 짝 비교가 깨져서 막음"""
    if arm in TREES_FIX:
        if given is not None and given != TREES_FIX[arm]:
            raise SystemExit("보조 실험 9 조건은 정해진 트리 묶음만: %s" % arm)
        return TREES_FIX[arm]
    ro = arm.endswith("ro")
    if given is None:
        return TREES_RO if ro else TREES
    if ("bt_ro" in given.replace("\\", "/")) != ro:
        raise SystemExit("조건과 트리 묶음이 안 맞음: %s, %s" % (arm, given))
    return given


def load_trees(pattern):
    """(이름, xml) 목록과 건너뛴 목록. 실행 불가 트리는 빼고 이유를 남김"""
    use, skip = [], []
    paths = sorted(set(p for pat in pattern.split(",") for p in glob.glob(pat)))
    for p in paths:
        with io.open(p, encoding="utf-8") as fh:
            xml = fh.read()
        errs, _ = validate(xml)
        name = "%s/%s" % (os.path.basename(os.path.dirname(p)), os.path.basename(p)[:-4])
        if errs:
            skip.append((name, errs[0][:120]))
        else:
            use.append((name, xml))
    return use, skip


def _tick_row(vid, t, p, fired):
    out = [vid, t]
    for k in TICK_COLS:
        v = p.get(k)
        if isinstance(v, bool):
            v = int(v)
        elif isinstance(v, float):
            v = round(v, 3)
        out.append(v)
    out.append(";".join(f[2] for f in fired if len(f) > 2 and f[2]))
    return out


def run(client, town, arm, n_veh, seconds, dt, tm_port, seed, warmup, trees, tick_log=False, consts=None):
    world = client.load_world(town)
    tm = client.get_trafficmanager(tm_port)
    tm.set_synchronous_mode(True)
    tm.set_random_device_seed(seed)
    tm.global_percentage_speed_difference(0.0)
    st = world.get_settings()
    st.synchronous_mode = True
    st.fixed_delta_seconds = dt
    world.apply_settings(st)

    bpl = world.get_blueprint_library()
    bps = [b for b in bpl.filter("vehicle.*")
           if int(b.get_attribute("number_of_wheels")) == 4]
    coll_bp = bpl.find("sensor.other.collision")
    spawns = world.get_map().get_spawn_points()
    # 스폰 순서대로 뽑음. 실패한 자리도 뽑고 버려야 같은 시드에서 같은 차가 같은 값을 받음.
    # 추첨은 knob에서만 쓰지만 rng 소비는 세 조건이 같게 맞춤
    rng = random.Random(seed)
    kind, width = SPEC[arm]
    box = widen(width) if width != 1 else None
    vehicles, plan, draws = [], [], {}
    n_fallback = 0
    for i, sp in enumerate(spawns[:min(n_veh, len(spawns))]):
        k = sample_knobs(rng, box)
        v = world.try_spawn_actor(bps[i % len(bps)], sp)
        if v is None:
            continue
        v.set_autopilot(True, tm_port)
        if kind == "pknob":
            # 같은 자리 ptree 차가 없으면(그쪽 스폰 실패, 트리 못 돎) 추첨값 그대로. 수는 info에 남김
            if i in consts:
                k = consts[i]
            else:
                n_fallback += 1
        elif kind != "knob" and kind not in OFFSET_ARMS:
            k = RT.baseline_want()
        apply_knobs(tm, v, k)
        draws[v.id] = k
        row = dict(k)
        row.update({"vid": v.id, "spawn_idx": i, "bp": bps[i % len(bps)].id})
        plan.append(row)
        vehicles.append(v)
    dims = dict((v.id, (v.bounding_box.extent.x * 2, v.bounding_box.extent.y * 2))
                for v in vehicles)

    # 충돌 센서. 사건 하나가 양쪽 차에 한 번씩 찍히므로 채점에서 짝으로 묶음
    hits, sensors = [], []
    clock = {"t": -warmup}

    def on_hit(ev, vid=None):
        o = ev.other_actor
        imp = ev.normal_impulse
        hits.append((vid, round(clock["t"], 2), o.id if o else -1,
                     o.type_id if o else "", round((imp.x ** 2 + imp.y ** 2 + imp.z ** 2)
                                                   ** 0.5, 1)))
    for v in vehicles:
        s = world.spawn_actor(coll_bp, carla.Transform(), attach_to=v)
        s.listen(lambda ev, vid=v.id: on_hit(ev, vid))
        sensors.append(s)

    ctxs, trees_of, percep = {}, {}, None
    if kind in TREE_ARMS:
        # 블록마다 순서를 섞어 배정. 스폰 목록이 지도 순서라 j % N이면 트리가 위치와 묶임.
        # 배정 rng와 파라미터 rng를 분리(한쪽 소비가 다른 쪽 수열을 밀지 않게)
        trng = random.Random(seed + 1000)
        prng = random.Random(seed + 2000)
        percep = RT.Perception(world, dt, carla)
        decl = dict((n, RT.tree_params(x)) for n, x in trees)
        roots = dict((n, ET.fromstring(x)) for n, x in trees)
        order = []
        for j, v in enumerate(vehicles):
            if j % len(trees) == 0:
                order = list(range(len(trees)))
                trng.shuffle(order)
            name, _x = trees[order[j % len(trees)]]
            pv = RT.draw_params(decl[name], prng)
            c = (RT.Ctx(draws[v.id], True, FIXED.get(kind, ()), box) if kind in OFFSET_ARMS
                 else RT.Ctx())
            ctxs[v.id] = c
            trees_of[v.id] = (name, RT.build(roots[name], c, pv), pv)
        for p in plan:
            name, _r, pv = trees_of[p["vid"]]
            p["tree"] = name
            p["params"] = ";".join("%s=%.6g" % (k, x) for k, x in sorted(pv.items()))
    elif tick_log:
        # 트리 없는 조건도 같은 지각 기록. ctx는 p 그릇으로만 씀
        percep = RT.Perception(world, dt, carla)
        ctxs = dict((v.id, RT.Ctx()) for v in vehicles)
    ticks = []

    # 실제로 TM에 넣은 값을 차량별 (합, 제곱합, 횟수)로 누적
    want_acc = {}

    def tick_trees():
        if percep is None:
            return 0
        percep.update(vehicles, ctxs)
        if not trees_of:
            return 0
        n = 0
        for v in vehicles:
            if not v.is_alive:
                continue
            c = ctxs[v.id]
            w = RT.tick(trees_of[v.id][1], c)
            a = want_acc.setdefault(v.id, {})
            for kk, vv in w.items():
                vv = float(vv)
                s = a.setdefault(kk, [0.0, 0.0, 0])
                s[0] += vv
                s[1] += vv * vv
                s[2] += 1
            n += RT.apply_to_tm(tm, v, c)
        return n

    # 워밍업. 스폰 직후 출발 가속 구간을 버림. 트리는 워밍업 동안에도 돎
    for _ in range(int(warmup / dt)):
        world.tick()
        clock["t"] += dt
        tick_trees()
    clock["t"] = 0.0
    # want는 측정 구간만 평균(2026-11-20부터). pknob이 이 값을 상수로 받아 ptree와 측정 구간 평균을 맞추기 때문.
    # 그 전 수집물의 want는 워밍업 포함
    want_acc.clear()

    m = world.get_map()
    rows, n_apply = [], 0
    for step in range(int(seconds / dt)):
        world.tick()
        clock["t"] = step * dt
        n_apply += tick_trees()
        t = step * dt
        if tick_log:
            for v in vehicles:
                if v.is_alive and v.id in ctxs:
                    c = ctxs[v.id]
                    ticks.append(_tick_row(v.id, round(t, 2), c.p, c.fired if trees_of else ()))
        for v in vehicles:
            if not v.is_alive:
                continue
            tr = v.get_transform()
            wp = m.get_waypoint(tr.location, project_to_road=True)
            if wp is None:
                continue
            L, W = dims[v.id]
            # is_at_traffic_light로 먼저 안 거르면 영향권 밖에서도 Green을 돌려줌
            tl, tls = v.get_traffic_light(), ""
            if tl is not None and v.is_at_traffic_light():
                tls = str(v.get_traffic_light_state())
            rows.append((v.id, round(t, 2), round(tr.location.x, 3),
                         round(-tr.location.y, 3), round(tr.rotation.yaw, 2),
                         wp.road_id, wp.lane_id, int(wp.is_junction),
                         round(L, 2), round(W, 2), tls))

    for s in sensors:
        if s.is_alive:
            s.stop()
    tm.set_synchronous_mode(False)
    st.synchronous_mode = False
    st.fixed_delta_seconds = None
    world.apply_settings(st)
    client.apply_batch_sync([carla.command.DestroyActor(x) for x in sensors + vehicles
                             if x.is_alive], True)
    time.sleep(2.0)

    for p in plan:
        if kind in TREE_ARMS:
            p["clamped"] = ctxs[p["vid"]].clamped
        if kind in OFFSET_ARMS:
            p["clipped"] = ctxs[p["vid"]].clipped
        acc = want_acc.get(p["vid"]) or {}
        out = []
        for kk, (s1, s2, cnt) in sorted(acc.items()):
            mu = s1 / cnt
            out.append("%s=%.4g/%.4g" % (kk, mu, max(s2 / cnt - mu * mu, 0.0) ** 0.5))
        if out:
            p["want"] = ";".join(out)
    info = {"tree_applies": n_apply,
            "clamped": sum(c.clamped for c in ctxs.values()),
            "clipped": sum(c.clipped for c in ctxs.values()),
            "hits": len(hits), "vehicles": len(vehicles), "width": width,
            "alive_end": sum(1 for v in vehicles if v.is_alive)}
    if kind == "pknob":
        info["pknob_fallback"] = n_fallback
    return rows, plan, hits, info, ticks


def dst_path(arm, town, seed, out=None):
    return os.path.join(out or OUT, "%s_%s_s%d.csv" % (arm, town, seed))


def main_collect():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--town", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--vehicles", type=int, default=120)
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--warmup", type=float, default=60.0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--tm-port", type=int, default=8000)
    ap.add_argument("--trees", default=None, help="안 주면 조건 따라 원본 또는 bt_ro")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--tick-log", action="store_true", help="차량별 틱 지각값과 발화 행동 기록(실험 3)")
    a = ap.parse_args()
    a.trees = trees_of_arm(a.arm, a.trees)
    if carla is None:
        raise SystemExit("carla egg를 못 찾음. CARLA_ROOT를 설치 경로로: %s" % CARLA_ROOT)

    trees = []
    if SPEC[a.arm][0] in TREE_ARMS:
        trees, skip = load_trees(a.trees)
        if not trees:
            raise SystemExit("실행 가능한 트리가 없음: %s" % a.trees)
        print("트리 %d벌 사용, 실행 불가로 뺀 것 %d벌" % (len(trees), len(skip)), flush=True)

    consts = None
    if SPEC[a.arm][0] == "pknob":
        src = dst_path("ptree", a.town, a.seed, a.out)[:-4] + "_plan.csv"
        if not os.path.exists(dst_path("ptree", a.town, a.seed, a.out)):
            raise SystemExit("pknob은 같은 시드 ptree 수집이 먼저 있어야 함: %s" % src)
        consts = pknob_consts(src)
        print("pknob 상수 %d대분(%s)" % (len(consts), os.path.basename(src)), flush=True)

    os.makedirs(a.out, exist_ok=True)
    client = carla.Client(a.host, a.port)
    client.set_timeout(300.0)
    client.get_server_version()
    rows, plan, hits, info, ticks = run(client, a.town, a.arm, a.vehicles, a.seconds, DT,
                                        a.tm_port, a.seed, a.warmup, trees, a.tick_log, consts)
    dst = dst_path(a.arm, a.town, a.seed, a.out)
    # 본 파일을 맨 마지막에 씀. sweep 재개가 본 파일 유무로 판정하므로 딸림 파일 먼저
    cols = ["vid", "spawn_idx", "bp"] + list(KNOBS) + ["lane_change"]
    seen = set()
    for p in plan:
        seen.update(p)
    cols += [c for c in ("tree", "params", "clamped", "clipped", "want") if c in seen]
    with io.open(dst[:-4] + "_plan.csv", "w", encoding="utf-8") as fh:
        fh.write(",".join(cols) + "\n")
        for p in plan:
            fh.write(",".join(str(p.get(c, "")) for c in cols) + "\n")
    with io.open(dst[:-4] + "_coll.csv", "w", encoding="utf-8") as fh:
        fh.write("vid,t,other_id,other_type,impulse\n")
        for h in hits:
            fh.write(",".join(str(x) for x in h) + "\n")
    if a.tick_log:
        with gzip.open(dst[:-4] + "_tick.csv.gz", "wt", encoding="utf-8") as fh:
            fh.write(TICK_HEADER)
            for r in ticks:
                fh.write(",".join(str(x) for x in r) + "\n")
    with io.open(dst[:-4] + "_info.json", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(info, arm=a.arm, town=a.town, seed=a.seed,
                                 seconds=a.seconds, warmup=a.warmup, tick_log=a.tick_log,
                                 trees=[n for n, _x in trees])))
    tmp = dst + ".part"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(FRAME_HEADER)
        for r in rows:
            fh.write(",".join(str(x) for x in r) + "\n")
    os.replace(tmp, dst)
    print("%s %s s%d: 차량 %d, 프레임 %d, 충돌 기록 %d, 클램프 %d -> %s"
          % (a.arm, a.town, a.seed, info["vehicles"], len(rows), info["hits"],
             info["clamped"], dst), flush=True)


# ---- sweep: 실행마다 서버를 새로 띄우고 끝나면 죽임 ----
# load_world 반복하면 서버 메모리가 계속 늘어 후반이 느려지거나 죽음. 부팅 20~30초가 더 쌈

CARLA_EXE = os.path.join(CARLA_ROOT, "CarlaUE4.exe")
PY37 = os.environ.get("PY37", "py -3.7 -X utf8").split()
BOOT_WAIT = 180.0
RUN_TIMEOUT = 1500.0
RETRIES = 1


def kill_carla():
    """이미지 이름으로 죽임. CarlaUE4.exe는 런처라 실제 서버(Shipping)를 자식으로 띄움"""
    for image in ("CarlaUE4-Win64-Shipping.exe", "CarlaUE4.exe"):
        subprocess.call(["taskkill", "/F", "/IM", image],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def port_open(port, timeout=1.0):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
        return True
    except OSError:
        return False


def start_carla(port, wait=BOOT_WAIT):
    kill_carla()
    time.sleep(2.0)
    subprocess.Popen([CARLA_EXE, "-quality-level=Low", "-RenderOffScreen",
                      "-carla-rpc-port=%d" % port],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < wait:
        if port_open(port):
            time.sleep(5.0)     # 포트가 열려도 맵 로드 준비가 조금 더 걸림
            return True
        time.sleep(2.0)
    return False


def code_digest():
    import hashlib
    h = hashlib.sha256()
    for f in sorted(os.listdir(HERE)):
        if f.endswith(".py"):
            h.update(f.encode("utf-8"))
            h.update(open(os.path.join(HERE, f), "rb").read())
    return h.hexdigest()[:16]


def write_manifest(out_dir, extra):
    """실행마다 코드 판을 남김. 중간에 코드가 바뀌면 여기서 보임"""
    import platform
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv[:],
           "code_sha256_16": code_digest(), "python": platform.python_version(),
           "host": platform.node()}
    try:
        rec["git"] = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                             cwd=ROOT).decode().strip()
        rec["git_dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "scripts"], cwd=ROOT).decode().strip())
    except Exception:  # noqa: BLE001
        rec["git"] = rec["git_dirty"] = None
    rec.update(extra)
    p = os.path.join(out_dir, "manifest.json")
    old = json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else []
    old.append(rec)
    with io.open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(old, ensure_ascii=False, indent=1))
    digests = sorted(set(r["code_sha256_16"] for r in old))
    if len(digests) > 1:
        print("주의: 이 수집물 자리에 코드 판이 %d개 섞임 %s" % (len(digests), digests))
    print("매니페스트 %s (코드 %s%s)" % (p, rec["code_sha256_16"],
                                    ", git 더러움" if rec.get("git_dirty") else ""))


def main_sweep():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--towns", default=",".join(TOWNS))
    ap.add_argument("--seeds", type=int, default=8, help="시드 0..N-1")
    ap.add_argument("--seed-from", type=int, default=0, help="재현 수집용. 시드 F..F+N-1")
    ap.add_argument("--trees", default=None, help="안 주면 조건 따라 원본 또는 bt_ro")
    ap.add_argument("--vehicles", type=int, default=120)
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--tm-port", type=int, default=8000)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tick-log", action="store_true")
    ap.add_argument("--warmup", type=float, default=60.0)
    a = ap.parse_args()

    arms = a.arms.split(",")
    bad = [x for x in arms if x not in SPEC]
    if bad:
        raise SystemExit("모르는 조건: %s (가능: %s)" % (bad, ", ".join(ARMS)))
    # 같은 (시드, 맵) 안에서 조건 순서대로 도니 ptree가 pknob보다 앞이어야 그 계획을 읽음
    if "pknob" in arms and "ptree" in arms and arms.index("ptree") > arms.index("pknob"):
        raise SystemExit("--arms에서 ptree를 pknob보다 앞에: %s" % a.arms)
    tree_of = dict((x, trees_of_arm(x, a.trees)) for x in arms if SPEC[x][0] in TREE_ARMS)
    for pat in sorted(set(tree_of.values())):
        trees, skip = load_trees(pat)
        print("트리 %d벌 사용(%s): %s" % (len(trees), os.path.relpath(pat, ROOT),
                                      ", ".join(n for n, _x in trees)))
        for n, why in skip:
            print("  실행 불가로 뺌: %s  %s" % (n, why))
        if not trees:
            raise SystemExit("실행 가능한 트리가 없음: %s" % pat)
    # 시드가 바깥 루프. 도중에 끊겨도 남는 게 시드 뒤쪽이라 맵과 조건 균형이 유지됨
    plan = [(arm, town, s) for s in range(a.seed_from, a.seed_from + a.seeds) for town in a.towns.split(",")
            for arm in arms]
    todo = [p for p in plan if not os.path.exists(dst_path(p[0], p[1], p[2], a.out))]
    print("계획 %d회, 남은 것 %d회" % (len(plan), len(todo)))
    if a.dry_run:
        for arm, town, s in todo:
            print("  %-6s %-9s s%d" % (arm, town, s))
        return 0
    if not os.path.exists(CARLA_EXE):
        raise SystemExit("CarlaUE4.exe가 없음: %s" % CARLA_EXE)
    os.makedirs(a.out, exist_ok=True)
    write_manifest(a.out, {"arms": a.arms, "towns": a.towns, "seeds": a.seeds,
                           "seed_from": a.seed_from,
                           "vehicles": a.vehicles, "seconds": a.seconds, "warmup": a.warmup,
                           "tick_log": a.tick_log,
                           "trees": tree_of})
    logf = os.path.join(a.out, "sweep.log")

    def log(msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with io.open(logf, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    durs, fails = [], []
    for i, (arm, town, s) in enumerate(todo, 1):
        eta = ""
        if durs:
            eta = "  남은 시간 약 %.0f분" % (sum(durs) / len(durs) * (len(todo) - i + 1) / 60)
        log("(%d/%d) %s %s s%d%s" % (i, len(todo), arm, town, s, eta))
        t0, ok, msg = time.time(), False, ""
        for attempt in range(RETRIES + 1):
            if not start_carla(a.port):
                msg = "CARLA 부팅 실패"
                continue
            cmd = PY37 + [os.path.join(HERE, "collect.py"), "collect", "--arm", arm,
                          "--town", town, "--seed", str(s), "--vehicles", str(a.vehicles),
                          "--seconds", str(a.seconds), "--port", str(a.port),
                          "--tm-port", str(a.tm_port), "--out", a.out, "--warmup", str(a.warmup)]
            if a.tick_log:
                cmd.append("--tick-log")
            if arm in tree_of:
                cmd += ["--trees", tree_of[arm]]
            try:
                p = subprocess.run(cmd, timeout=RUN_TIMEOUT, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
                out = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
                err = (p.stderr or b"").decode("utf-8", "replace").strip()
                msg = out[-1] if out else err[-300:]
            except subprocess.TimeoutExpired:
                msg = "TIMEOUT"
            ok = os.path.exists(dst_path(arm, town, s, a.out))
            if ok:
                break
            log("    실패 (시도 %d): %s" % (attempt + 1, msg))
        kill_carla()
        if ok:
            durs.append(time.time() - t0)
            log("    OK %.0f초  %s" % (durs[-1], msg))
        else:
            fails.append((arm, town, s))
            log("    포기")
        time.sleep(3.0)
    log("끝. 성공 %d, 실패 %d %s" % (len(durs), len(fails), fails or ""))
    return 1 if fails else 0


def main_selftest():
    trees, skip = load_trees(sys.argv[1] if len(sys.argv) > 1 else TREES)
    for n, why in skip:
        print("실행 불가: %s  %s" % (n, why))
    for n, x in trees:
        p = os.path.join(ROOT, "data", "outputs", "bt", n + ".xml")
        RT.selftest(p)


if __name__ == "__main__":
    cmds = {"collect": main_collect, "sweep": main_sweep, "selftest": main_selftest}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        raise SystemExit("usage: py -3.7 scripts/collect.py {collect,sweep,selftest} ...")
    del sys.argv[1]
    sys.exit(cmds[c]() or 0)
