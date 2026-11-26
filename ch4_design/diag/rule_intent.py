"""보조 실험 8: 규칙의 의도와 타당성. py3.11. 2026-11-19 추가, 탐색.
규칙은 docs/design.md 보조 실험 8 절(커밋 ebb3f45)에서 고정. 단어 사전 여기서 안 바꿈.

  python diag/rule_intent.py auto              -> results/intent/auto.csv, report.md
  python diag/rule_intent.py sheet             -> results/intent/rating/ 평가지 둘, 안내문
  python diag/rule_intent.py score A.csv B.csv -> results/intent/rating_report.md

평가지는 자동 판정 결과를 안 보여줌. 표본은 시드 0, 60줄.
"""
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bt_nodes as BN  # noqa: E402
import bt_runtime as RT  # noqa: E402

BT_DIR = os.path.join(ROOT, "data", "outputs", "bt")
OUT = os.path.join(ROOT, "results", "intent")
N_SAMPLE, SEED = 60, 0

ACT_IDS = [a[0] for a in BN.ACTIONS]
ACT_RE = re.compile(r"\b(%s)\b" % "|".join(ACT_IDS))
IGN = ("ign_lights", "ign_signs", "ign_vehicles", "ign_walkers")

# 설계 고정 사전. 부분 일치, 소문자
WORDS = {
    "speed_diff": {"down": ["slow", "reduc", "decreas", "brake", "braking", "cautio", "careful", "stop", "lower",
                            "decelerat", "yield"],
                   "up": ["speed up", "accelerat", "exceed", "faster", "increase speed", "increase its speed",
                          "higher speed"]},
    "gap_m": {"up": ["increase the gap", "increase gap", "increase the distance", "increase following", "larger",
                     "wider", "more distance", "longer"],
              "down": ["reduce the gap", "reduce gap", "decrease the gap", "reduce the distance", "reduce following",
                       "closer", "tailgat", "shorter", "smaller", "tighter"]},
    "keep_right": {"up": ["stay in the right", "keep right", "prefer the right", "prefer staying", "return to the right"],
                   "down": ["leave the right", "move left", "overtak", "pass"]},
    "ign": {"up": ["disregard", "ignor", "run", "violat"], "zero": ["obey", "comply", "respect"]},
    "lane_change": {"up": ["allow", "enable", "permit"], "down": ["disable", "forbid", "prevent", "avoid", "prohibit"]},
}
# speed_diff는 양수가 감속이라 값 방향 이름을 의도 쪽에 맞춤: 값 > 0 이면 "down"(감속)


def rules(pattern="prop_r*"):
    out = []
    for f in sorted(glob.glob(os.path.join(BT_DIR, pattern, "tree_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        if not d.get("executable"):
            continue
        name = "%s/%s" % (os.path.basename(os.path.dirname(f)), os.path.basename(f)[:-5])
        root = __import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(
            open(f[:-5] + ".xml", encoding="utf-8").read())
        decl = BN.parse_params(root, lambda *_a: None)
        for ln in (d.get("intermediate") or {}).get("enumeration", "").splitlines():
            if ln.strip().startswith("-"):
                out.append(dict(tree=name, rule=ln.strip(), decl=decl))
    return out


def parse_action(line, decl):
    """(노브, 값 종류, 값, 의도문) 또는 None과 이유"""
    acts = list(ACT_RE.finditer(line))
    if len(acts) != 1:
        return None, "행동 수 %d" % len(acts)
    am = re.match(r'(\w+)\s+(\w+)="([^"]*)"', line[acts[0].start():])
    if not am:
        return None, "행동 인자"
    aid, an, av = am.groups()
    knob, need = RT.ACT[aid]
    if an != need:
        return None, "행동 인자"
    tail = line[acts[0].start() + am.end():]
    i = tail.find(" to ")
    intent = tail[i + 4:].strip().lower() if i >= 0 else ""
    if knob is None:
        if av.strip().lower() not in ("true", "false"):
            return None, "행동 값"
        return ("lane_change", "bool", av.strip().lower() == "true", intent), None
    m = BN.PARAM_REF.match(av)
    if m:
        if m.group(1) not in decl:
            return None, "행동 값"
        lo, hi, _d = decl[m.group(1)]
        return (knob, "param", (lo, hi), intent), None
    x = BN._num(av)
    if x is None:
        return None, "행동 값"
    return (knob, "lit", x, intent), None


def value_dir(knob, kind, v):
    """기준값 대비 값 방향. up, down, zero(무변화), none(구간이 기준값 품음).
    구간 경계가 기준값이면(예: ign [0, 5]) 그 반대쪽 방향으로 봄. 균등 추첨이라 기준값이 정확히 나올 확률 0.
    엄격 해석(방향 없음)은 main_auto에서 민감도로 같이 냄"""
    if knob == "lane_change":
        return "up" if v else "down"
    base = BN.KNOBS[knob][0]
    if kind == "lit":
        lo = hi = v
    else:
        lo, hi = v
    if lo == hi == base:
        return "zero"
    if lo >= base and hi > base:
        d = "up"
    elif hi <= base and lo < base:
        d = "down"
    else:
        return "none"
    if knob == "speed_diff":       # 양수가 감속
        return "down" if d == "up" else "up"
    return d


def intent_dir(knob, text):
    w = WORDS["ign" if knob in IGN else knob]
    hit = [k for k, ws in w.items() if any(x in text for x in ws)]
    return hit[0] if len(hit) == 1 else ("none" if not hit else "both")


def judge(knob, vd, idir):
    if idir in ("none", "both"):
        return "판정 불가", "의도 단어 %s" % ("없음" if idir == "none" else "양쪽")
    if vd == "none":
        return "판정 불가", "값 구간이 기준값 품음"
    if knob in IGN and idir == "zero":
        return ("일치", "") if vd == "zero" else ("불일치", "")
    if vd == "zero":
        return "판정 불가", "값 무변화"
    return ("일치", "") if vd == idir else ("불일치", "")


def main_auto():
    rows = []
    for r in rules():
        a, why = parse_action(r["rule"], r["decl"])
        row = dict(tree=r["tree"], rule=r["rule"])
        if a is None:
            row.update(status="제외", why=why)
        else:
            knob, kind, v, intent = a
            vd, idir = value_dir(knob, kind, v), intent_dir(knob, intent)
            st, wy = judge(knob, vd, idir)
            # 민감도: 파라미터 구간 경계가 기준값이면(예: [0, 5]) 방향 없음으로 보는 엄격 해석
            edge = kind == "param" and knob != "lane_change" and BN.KNOBS[knob][0] in v
            st2 = judge(knob, "none", idir)[0] if edge else st
            row.update(knob=knob, value_dir=vd, intent=intent, intent_dir=idir, status=st, why=wy, status_strict=st2)
        rows.append(row)
    t = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    t.to_csv(os.path.join(OUT, "auto.csv"), index=False)
    L = ["# 보조 실험 8. 의도와 값 방향 자동 판정", "",
         "설계: [`docs/design.md`](../../docs/design.md#보조-실험-8-규칙의-의도와-타당성). 사후 실험, 기술. "
         "생성: `python diag/rule_intent.py auto`", "",
         "제안 트리 20개 enumeration %d줄. %s" % (len(t), ", ".join("%s %d" % kv for kv in t.status.value_counts().items())),
         "", "| 노브 | 일치 | 불일치 | 판정 불가 |", "|---|---|---|---|"]
    j = t[t.status != "제외"]
    for k, g in j.groupby("knob"):
        c = g.status.value_counts()
        L.append("| %s | %d | %d | %d |" % (k, c.get("일치", 0), c.get("불일치", 0), c.get("판정 불가", 0)))
    L += ["", "민감도(파라미터 구간 경계가 기준값이면 방향 없음): " + ", ".join(
        "%s %d" % kv for kv in j.status_strict.value_counts().items()) + ". speed_diff 불일치 %d" % int(
        ((j.knob == "speed_diff") & (j.status_strict == "불일치")).sum())]
    L += ["", "판정 불가 사유: " + ", ".join("%s %d" % kv for kv in j[j.status == "판정 불가"].why.value_counts().items()),
          "", "## 불일치 전 목록", ""]
    for _i, r in j[j.status == "불일치"].iterrows():
        L.append("- `%s` %s (값 %s, 의도 %s)" % (r.tree, r.rule.lstrip("- "), r.value_dir, r.intent_dir))
    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L[:20]))


SIM = """시뮬레이터(CARLA Traffic Manager)가 이미 하는 일. 규칙이 이걸 다시 하려 하면 Q3 예:
- 경로 선택(교차로에서 길을 고름)
- 목표 속도 기본값(제한속도 100%)
- 충돌 회피(앞 장애물에 맞춰 제동)
- 신호 준수(적색에 정지)
- 경로와 차로 유지"""

GUIDE = """# 보조 실험 8 평가 안내

규칙 한 줄마다 세 문항에 답한다. 다른 평가자와 상의하지 않는다. 확신이 없으면 판단 불가, 애매를 쓴다.

## 행동 값의 뜻(기준값 대비)

- SetSpeedDiff pct: 제한속도보다 몇 % 느리게. 양수면 감속, 음수면 제한속도 초과(가속). 기준 0
- SetGap meters: 앞차와 최소 간격. 2.5보다 크면 넓힘, 작으면 좁힘
- SetKeepRight pct: 오른쪽 차로 선호. 50보다 크면 강화, 작으면 약화
- SetIgnoreLights, Signs, Vehicles, Walkers pct: 무시할 확률. 0이면 무시 안 함
- EnableLaneChange enabled: true면 스스로 차로 변경 허용, false면 금지
- 값이 {이름}이면 차량마다 정해지는 파라미터. 평가지의 구간 열 참고

{sim}

## 문항

- **Q1 의도와 값:** 규칙 끝의 "to ..." 의도와 행동 값의 방향이 맞는가. 맞음 / 틀림 / 판단 불가
- **Q2 타당성:** 실제 도로에서 운전자가 이 조건에서 이렇게 행동하는 게 그럴듯한가. 예 / 애매 / 아니오
- **Q3 역할 침범:** 위 시뮬레이터 몫을 이 규칙이 다시 하려 하는가. 예 / 아니오

평가지 열 Q1, Q2, Q3에 위 낱말 그대로 적는다. 메모는 자유.
""".replace("{sim}", SIM)


def main_sheet():
    rs = rules()
    idx = np.random.RandomState(SEED).choice(len(rs), size=N_SAMPLE, replace=False)
    d = os.path.join(OUT, "rating")
    os.makedirs(d, exist_ok=True)
    rows = []
    for k, i in enumerate(sorted(idx)):
        r = rs[i]
        ps = ["%s=[%g, %g]" % (n, lo, hi) for n, (lo, hi, _x) in r["decl"].items() if "{%s}" % n in r["rule"]]
        rows.append(dict(id=k + 1, rule=r["rule"].lstrip("- "), params="; ".join(ps), Q1="", Q2="", Q3="", memo=""))
    sheet = pd.DataFrame(rows)
    for who in ("A", "B"):
        sheet.to_csv(os.path.join(d, "sheet_%s.csv" % who), index=False, encoding="utf-8-sig")
    key = pd.DataFrame([dict(id=k + 1, tree=rs[i]["tree"], rule=rs[i]["rule"]) for k, i in enumerate(sorted(idx))])
    key.to_csv(os.path.join(d, "key.csv"), index=False)
    with open(os.path.join(d, "GUIDE.md"), "w", encoding="utf-8") as fh:
        fh.write(GUIDE)
    print("평가지 %d줄: %s" % (len(sheet), d))


def kappa(a, b):
    a, b = list(a), list(b)
    cats = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan"), po


def main_score(fa, fb):
    A = pd.read_csv(fa, encoding="utf-8-sig")
    B = pd.read_csv(fb, encoding="utf-8-sig")
    key = pd.read_csv(os.path.join(OUT, "rating", "key.csv"))
    auto = pd.read_csv(os.path.join(OUT, "auto.csv"))
    L = ["# 보조 실험 8. 두 사람 평가", "", "평가지 `%s`, `%s`. 논의 전 값." % (os.path.basename(fa), os.path.basename(fb)), "",
         "| 문항 | κ | 단순 일치 | A 분포 | B 분포 |", "|---|---|---|---|---|"]
    for q in ("Q1", "Q2", "Q3"):
        a, b = A[q].astype(str).str.strip(), B[q].astype(str).str.strip()
        k, po = kappa(a, b)
        L.append("| %s | %.2f | %.2f | %s | %s |" % (q, k, po, dict(a.value_counts()), dict(b.value_counts())))
    m = key.merge(auto[["tree", "rule", "status"]], on=["tree", "rule"], how="left").merge(
        A[["id", "Q1"]].rename(columns={"Q1": "A"}), on="id").merge(B[["id", "Q1"]].rename(columns={"Q1": "B"}), on="id")
    j = m[m.status.isin(["일치", "불일치"]) & (m.A == m.B) & m.A.isin(["맞음", "틀림"])]
    hit = ((j.status == "일치") & (j.A == "맞음")) | ((j.status == "불일치") & (j.A == "틀림"))
    L += ["", "자동 판정과 두 평가자가 같은 Q1의 일치: %d/%d" % (int(hit.sum()), len(j))]
    with open(os.path.join(OUT, "rating_report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if cmd == "auto":
        main_auto()
    elif cmd == "sheet":
        main_sheet()
    elif cmd == "score":
        main_score(sys.argv[2], sys.argv[3])
