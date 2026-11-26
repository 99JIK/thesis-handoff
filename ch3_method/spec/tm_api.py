"""파라미터 사실을 한 곳에서 낸다. 설명 문장은 손으로 안 쓰고 크롤링본에서 가져온다.

왜 이렇게 하나: 파라미터 의미를 기억으로 적었더니 틀렸고, 그 한 줄로 트리 10벌이 잘못
설계됐다. 이제 doc은 fetch_carla_api.py가 받은 CARLA 원본에서만 온다. 우리가 여기 적는 것은
knob 이름 매핑과, 원본에 없는 것을 없다고 표시하는 일뿐이다.

출처 둘을 합친다.
  carla_api.json   문서(PythonAPI/docs/client.yml). 의미가 여기 있다
  carla_egg.json   설치된 egg. 존재 여부만. Boost 시그니처뿐이라 의미는 없다

둘이 어긋난다. 0.9.10 egg는 19개인데 문서는 15개다. 우리가 arm 3에서 무작위화하는
ignore_signs_percentage와 set_percentage_keep_right_rule이 문서에 없다. 이름이 다른 것도 있다
(문서 global_distance_to_leading_vehicle, egg set_global_distance_to_leading_vehicle).
그래서 knob마다 상태를 표시해 내보낸다. 확인 안 된 것을 LLM에 확언으로 주면 안 된다.

경계: 우리 설계값(범위, 기준값)은 여기 안 둔다. bt_nodes.KNOBS에 남는다.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "carla_api.json")
EGG = os.path.join(HERE, "carla_egg.json")
CLS = "TrafficManager"
# 산문 가이드 원본. TM_AUTO와 ADV_NOTES의 문장이 전부 여기서 나온다.
# 예전엔 이 파일이 레포에 없어서 내가 읽고 옮겨 적었다. SetGap이 틀렸던 것과 같은 구조라
# fetch_carla_api.py가 같이 받게 했다. 문장을 고칠 때 여기서 대조할 것
ADV_MD = os.path.join(HERE, "carla_api", "adv_traffic_manager.md")
ADV_URL = "https://carla.readthedocs.io/en/0.9.10/adv_traffic_manager/"

# bt_nodes.KNOBS의 키 -> CARLA 메서드. 두 파일을 조인하는 축이다
KNOB_METHOD = {
    "speed_diff": "vehicle_percentage_speed_difference",
    "gap_m": "distance_to_leading_vehicle",
    "lane_change": "auto_lane_change",
    "keep_right": "set_percentage_keep_right_rule",
    "ign_lights": "ignore_lights_percentage",
    "ign_signs": "ignore_signs_percentage",
    "ign_vehicles": "ignore_vehicles_percentage",
    "ign_walkers": "ignore_walkers_percentage",
}

# adv_traffic_manager 페이지 문장. API 레퍼런스와 강조점이 다른 것만 남긴다.
# gap_m을 정지 이격이라고 단정했다가 API 레퍼런스에서 아니라는 걸 봤다. 둘 다 남긴다
ADV_NOTES = {
    "gap_m": ("adv 페이지는 'a minimum distance between stopped vehicles'라고 쓰고 "
              "API 레퍼런스는 'minimum distance ... to keep with the others'로 정지에 "
              "한정하지 않는다. 'affect the minimum moving distance'는 양쪽 같다. "
              "어느 쪽으로도 단정하지 말 것"),
    "speed_diff": ("adv 페이지 예시가 -20을 '120% of speed limit'으로 설명한다. "
                   "기본 목표속도가 제한의 70%라는 서술과 문서 기본값 30이 맞물린다"),
}

# TM이 알아서 하는 것. 트리가 다시 짤 필요가 없다는 걸 프롬프트에 알려야 한다.
# v3에 신호 준수를 트리로 다시 짠 분기가 있었다.
# 앞부분은 ADV_MD 원문 인용이고 뒷부분(trailing)이 그것을 트리 설계로 옮긴 우리 말이다.
# 섞어 쓰면 어디까지가 CARLA 말인지 안 보이므로 튜플로 갈라 둔다
TM_AUTO = [
    ("Vehicles are not goal-oriented, they follow a trajectory and whenever "
     "approaching a junction, choose a path randomly.",
     "The tree does not choose a route."),
    # 원문은 CARLA 기본 설정(percentage_speed_difference 기본 30) 얘기다. 우리 실행은
    # 전역을 0으로 두고 기준값도 0이라 실제 기준은 제한속도의 100%다. 원문만 실으면
    # 프롬프트가 카탈로그의 baseline 0과 모순되고, LLM이 틀린 기준 위에서 설계한다.
    # v3를 날린 것과 같은 종류의 사고라 부연으로 갈라 적는다
    ("Vehicles' target speed is 70% their current speed limit, unless any other "
     "value is set.",
     "That is the CARLA default. In this setup it has been set otherwise: the "
     "baseline target speed is 100% of the posted limit, which is what the "
     "catalogue's baseline of 0 means."),
    ("Collision avoidance runs in the Collision Stage.",
     "The tree does not need to brake for obstacles."),
    ("Traffic light compliance runs in the Traffic Light Stage.",
     "The tree does not need to stop at red lights."),
    ("Path and lane keeping run in the Localization Stage.", ""),
]


def _load():
    doc = json.load(open(DOC, encoding="utf-8")) if os.path.exists(DOC) else {}
    egg = json.load(open(EGG, encoding="utf-8")) if os.path.exists(EGG) else {}
    return (doc.get("classes", {}).get(CLS, {}).get("methods", {}),
            egg.get("classes", {}).get(CLS, {}),
            doc.get("carla_version", "?"))


def knob_facts():
    """knob -> {method, doc, params, documented, in_egg, adv}. 없으면 doc이 None."""
    dm, em, _ = _load()
    out = {}
    for k, meth in KNOB_METHOD.items():
        m = dm.get(meth)
        out[k] = {"method": meth, "documented": m is not None,
                  "in_egg": meth in em, "doc": (m or {}).get("doc"),
                  "params": [p for p in (m or {}).get("params", [])
                             if p["name"] != "actor"],
                  "adv": ADV_NOTES.get(k)}
    return out


def knob_lines(knobs=None):
    """프롬프트로 나갈 파라미터 사실. 확인 안 된 것은 그렇게 적어서 내보낸다."""
    f = knob_facts()
    out = []
    for k in (knobs or KNOB_METHOD):
        a = f[k]
        s = "%s -> carla.TrafficManager.%s" % (k, a["method"])
        if a["doc"]:
            s += "\n    %s" % " ".join(a["doc"].split())
        for p in a["params"]:
            bit = "%s: %s" % (p["name"], p["type"])
            if p["units"]:
                bit += " in %s" % p["units"]
            if p["doc"]:
                bit += ". %s" % " ".join(p["doc"].split())
            s += "\n    (%s)" % bit
        if not a["documented"]:
            s += ("\n    NOT DOCUMENTED in the CARLA 0.9.10 API reference. It exists "
                  "in the binary but its exact effect is unspecified."
                  if a["in_egg"] else "\n    NOT PRESENT in this CARLA build.")
        out.append(s)
    return "\n".join(out)


def auto_lines():
    """CARLA 원문과 우리 부연을 한 줄로 붙인다. 원문이 앞이다."""
    return "\n".join(("- " + q + ((" " + note) if note else ""))
                     for q, note in TM_AUTO)


def status():
    f = knob_facts()
    return {"knobs": len(f),
            "documented": sum(1 for a in f.values() if a["documented"]),
            "in_egg": sum(1 for a in f.values() if a["in_egg"]),
            "undocumented": sorted(k for k, a in f.items() if not a["documented"])}


if __name__ == "__main__":
    dm, em, ver = _load()
    print("CARLA %s. 문서 메서드 %d, egg 메서드 %d" % (ver, len(dm), len(em)))
    only_doc, only_egg = sorted(set(dm) - set(em)), sorted(set(em) - set(dm))
    if only_doc:
        print("  문서에만: %s" % ", ".join(only_doc))
    if only_egg:
        print("  egg에만:  %s" % ", ".join(only_egg))
    print()
    print("== TM이 알아서 하는 것")
    print(auto_lines())
    print()
    print("== 파라미터")
    print(knob_lines())
    print()
    print(status())
