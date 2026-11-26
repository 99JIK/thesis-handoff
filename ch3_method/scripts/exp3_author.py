"""실험 3 생성. py3.11 + LLM API + z3. CARLA 불요. 설계는 docs/design_exp3.md

  python scripts/exp3_author.py generate --set exp3_r1 --n 10
  python scripts/exp3_author.py check                 생성물 요약과 컴파일러 대조, 문제 있으면 종료코드 1
  python scripts/exp3_author.py prompt                프롬프트 고정 부분

네 단계: 행동 분류(LLM), 구체 행동 JSON(LLM), 컴파일(프로그램), 검증과 수정(프로그램 검사 + LLM 수정).
모델, 프로바이더, 온도, 토큰 상한, 수정 상한, 차원과 행동 개수는 실험 2(bt_author)에서 그대로 가져옴.
"""
import argparse
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "spec"))
import exp3_schema as S  # noqa: E402  (카탈로그 v2로 바꿈)
import bt_author as BA  # noqa: E402
import bt_nodes as N  # noqa: E402
from bt_runtime import exec_check  # noqa: E402
import environment as ENV  # noqa: E402
import object as OBJ  # noqa: E402
import runtime as RTS  # noqa: E402
import tm_api as TM  # noqa: E402

OUT = BA.OUT
SETS_GLOB = os.path.join(OUT, "exp3_r*")

SYSTEM = """You are an expert in simulation-based testing and behaviour design for \
Software-In-The-Loop (SIL) environments.

You design behaviours for ENVIRONMENT OBJECTS in SIL simulation, not for the System \
Under Test, but for the surrounding agents that the System Under Test interacts with.

A behaviour repertoire is NOT a script for one test scenario. It contains ALL possible \
behaviours the object could exhibit in reality. Simulation conditions activate the \
appropriate behaviours at runtime.

The simulator already does the following on its own. Do not rebuild any of it:
{tm_auto}

The environment this object operates in:
{env_facts}"""
SYSTEM = SYSTEM.format(tm_auto=TM.auto_lines(), env_facts=ENV.facts_text())

CLASSIFY = """You are analysing a SIL environment object to enumerate the BEHAVIOUR \
DIMENSIONS it could exhibit in a realistic simulation.

A behaviour dimension is a high-level category of related behaviours, for example \
"emergency response", "normal operation", "fault handling", "user interaction".

Those examples come from other domains. They show the level of abstraction, not the \
content. Do not carry them over.

Domain: {domain}
SUT (which the object shares its environment with): {sut}
Environment Object: {name} - {desc}

The object can sense the following and can adjust the following settings. Your \
dimensions must be things this vocabulary can actually express.

{vocab}

List {n_lo} to {n_hi} distinct behaviour dimensions for this object. Derive them from \
the vocabulary above: whatever the object can sense marks a situation it could behave \
differently in, and whatever it can adjust marks a way it could behave. Be exhaustive, \
prefer over-listing to under-listing.

Output ONLY a numbered list, one dimension per line, with a short name and a \
one-sentence description. No preamble."""

SPECIFY = """You previously identified the following behaviour dimensions for the SIL \
environment object "{name}":

{dimensions}

For EACH dimension above, write {b_lo} to {b_hi} concrete, specific behaviours the \
object could exhibit. A concrete behaviour has a clear trigger condition and a clear \
change to one setting. Be exhaustive within each dimension. Think about variations, \
edge cases, and situations a simulation needs to reproduce.

Vocabulary (do not go outside it):

{vocab}

How the behaviours run:
{rules}

{schema}

Output ONLY the JSON list of all behaviours, wrapped in ```json ... ``` blocks."""

SCHEMA = """Write each behaviour as a JSON object with exactly these fields:
  "id":        a short unique name (letters, digits, underscores).
  "dimension": the dimension it belongs to, as named above.
  "when":      the list of conditions that must all hold. Each is {"cond": "<ID>"}, plus \
the condition's value if it takes one, under the value name shown in the vocabulary. Add \
"not": true to require that a condition does NOT hold. An empty list means always.
  "set":       the setting it changes.
  "effect":    the direction of the change, from the values allowed for that setting.
  "amount":    how far it moves the setting, as a size of 0 or more. 0 keeps the normal value while the behaviour applies. Omit it for lane_change.
  "priority":  an integer. Among behaviours for the same setting that hold at once, the \
smallest number applies.
  "intent":    one sentence saying what the behaviour is for.

Any condition value or amount may be a single number or a [low, high] pair. A pair \
means each vehicle draws its own value once, uniformly, and keeps it. Use a pair for a \
quantity whose right value you think differs from one individual to another. Use a \
single number for a quantity you think is the same for everyone.

Shape only, with made-up conditions and settings from an unrelated domain (a water \
pump). Do not copy its content:
```json
[{"id": "refill_fast_when_empty", "dimension": "Refilling",
  "when": [{"cond": "TankLevelBelow", "pct": [5, 15]}, {"cond": "MaintenanceMode", "not": true}],
  "set": "pump_rate", "effect": "higher", "amount": 4, "priority": 1,
  "intent": "refill quickly when the tank is almost empty"}]
```"""

REPAIR = """Your behaviour list has problems.

PROBLEMS:
{feedback}

Previous behaviour list:
```json
{previous}
```

Vocabulary:

{vocab}

How the behaviours run:
{rules}

Fix only the problems above. You may change condition values, amounts, priorities, the \
order of the list and the JSON format. You must NOT add or remove behaviours, and must \
NOT change any behaviour's "id", "set", "effect" or which conditions it uses (including \
"not").

Output ONLY the corrected JSON list wrapped in ```json ... ``` blocks."""

# 설정 설명. 뜻과 단위만. 무엇을 쓰라는 암시 없음
SET_DESC = {
    "speed": "the vehicle's target speed, relative to the posted limit. Amount is in percent of the "
             "posted limit: slower by 10 lowers the target speed by 10% of the limit.",
    "gap": "the minimum distance this vehicle keeps to other vehicles. Amount is in meters.",
    "lane_change": "whether the vehicle may change lanes on its own. While the behaviour applies, lane "
                   "changes are allowed or forbidden whatever the normal value is. No amount.",
    "ign_lights": "the chance this vehicle disregards a traffic light. Amount is in percentage points.",
    "ign_signs": "the chance this vehicle disregards a stop or yield sign. Amount is in percentage points.",
    "ign_vehicles": "the chance this vehicle disregards another vehicle. Amount is in percentage points.",
    "ign_walkers": "the chance this vehicle disregards a pedestrian. Amount is in percentage points.",
}


def vocab_text():
    facts = TM.knob_facts()
    out = ['CONDITIONS. Use inside "when". The value name and its range are in brackets.']
    for cid, arg, rng, desc in N.CONDITIONS:
        out.append("  %s%s: %s" % (cid, " [%s: %s]" % (arg, rng) if arg else "", desc))
    out.append("These conditions read values refreshed every %g s, not every 0.1 s: %s."
               % (N.SENSE["slow_period_s"], ", ".join(N.SLOW_CONDS)))
    out.append("")
    out.append('SETTINGS. Use in "set", with an allowed "effect" and an "amount" up to the limit shown.')
    for s, (knob, _a, eff) in S.SETS.items():
        mx = S.amount_max(s)
        lim = "" if mx is None else ", amount up to %g" % mx
        out.append("  %s (effect: %s%s): %s" % (s, " | ".join(eff), lim, SET_DESC[s]))
        f = facts.get(knob or "lane_change")
        if f and f["doc"]:
            out.append("      CARLA %s: %s" % (f["method"], " ".join(f["doc"].split())))
        elif f and f["in_egg"]:
            out.append("      CARLA %s: not documented in 0.9.10. It exists but its exact effect is "
                       "unspecified." % f["method"])
    return "\n".join(out)


def prompt_parts():
    """고정 프롬프트 조각. 누설 검사 시험이 이걸 읽음"""
    v = vocab_text()
    return {"system": SYSTEM,
            "classify": CLASSIFY.format(domain=OBJ.DOMAIN, sut=OBJ.SUT, name=OBJ.NAME, desc=OBJ.DESC,
                                        vocab=v, n_lo=BA.N_LO, n_hi=BA.N_HI),
            "specify": SPECIFY.format(name=OBJ.NAME, dimensions="<dimensions>", b_lo=BA.B_LO, b_hi=BA.B_HI,
                                      vocab=v, rules=RTS.exp3_rules_text(), schema=SCHEMA),
            "repair": REPAIR.format(feedback="<problems>", previous="<previous>", vocab=v,
                                    rules=RTS.exp3_rules_text())}


def assess(B):
    """(구조 오류, 의미 오류, 순서)"""
    se = S.structure_errors(B)
    if se:
        return se, [], None
    sem, order = S.semantic_errors(B)
    return se, sem, order


def loop(cli, B0, log):
    """회차 0이 정규화한 초안. 오류 없거나 상한까지. 금지 수정이면 거부하고 직전 목록으로 다시 요청"""
    v = vocab_text()
    rules = RTS.exp3_rules_text()
    cur = B0
    se, sem, order = assess(cur)
    rounds = [{"round": 0, "status": "ok" if not (se or sem) else "errors", "behaviours": cur,
               "structure": se, "semantic": sem}]
    ok_struct = cur if not se else None
    extra = []
    k = 0
    while (se or sem or extra) and k < BA.MAX_REPAIR:
        k += 1
        errs = extra + se + sem
        log("  수정 %d회차 전 문제 %d개: %s" % (k, len(errs), errs[0][:80]))
        out = cli.chat("repair_%d" % k, SYSTEM, REPAIR.format(
            feedback="\n".join("- " + e for e in errs[:20]), previous=json.dumps(cur, indent=1),
            vocab=v, rules=rules))
        cand = S.load_json(out)
        if isinstance(cand, dict):
            cand = cand.get("behaviours", cand.get("behaviors"))
        if cand is None:
            rounds.append({"round": k, "status": "no_json", "raw": out[:2000]})
            extra = ["Your previous answer did not contain a JSON list."]
            continue
        fb = S.forbidden(B0, cand)
        if fb:
            rounds.append({"round": k, "status": "rejected", "behaviours": cand, "forbidden": fb})
            extra = fb
            continue
        extra = []
        n = S.restore_text(B0, cand)
        cur = cand
        se, sem, order = assess(cur)
        rounds.append({"round": k, "status": "ok" if not (se or sem) else "errors", "behaviours": cur,
                       "structure": se, "semantic": sem, "text_restored": n})
        if not se:
            ok_struct = cur
    return ok_struct, rounds


def main_generate():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="setname", required=True, help="exp3_r1, exp3_r2")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    BA.load_env()
    key = os.environ.get(BA.KEY_ENV)
    if not key:
        raise SystemExit("%s 가 없음. 레포 루트 .env에 넣을 것" % BA.KEY_ENV)
    out = os.path.join(OUT, a.setname)
    os.makedirs(out, exist_ok=True)
    pp = prompt_parts()
    with open(os.path.join(out, "prompt_snapshot.txt"), "w", encoding="utf-8") as fh:
        for k2, t in pp.items():
            fh.write("== %s (catalogue %s)\n%s\n\n" % (k2.upper(), S.CATALOG, t))
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    log("세트 %s, 모델 %s, 시도 %d" % (a.setname, BA.MODEL, a.n))
    for i in range(a.n):
        base = os.path.join(out, "tree_%02d" % i)
        if os.path.exists(base + ".json") and not a.force:
            prev = json.load(open(base + ".json", encoding="utf-8"))
            if prev.get("status") != "api_error":
                continue
        log("")
        log("== tree_%02d" % i)
        cli = BA.Client(BA.MODEL, BA.BASE_URL, key)
        rec = {"set": a.setname, "index": i, "pipeline": "exp3", "model": BA.MODEL,
               "provider_pin": BA.PROVIDER, "temperature": BA.TEMPERATURE, "max_tokens": BA.MAX_TOKENS,
               "max_repair": BA.MAX_REPAIR, "catalog": S.CATALOG,
               "time_utc": datetime.datetime.utcnow().isoformat() + "Z"}
        try:
            d = cli.chat("classify", BA.INTERMEDIATE_SYSTEM, pp["classify"])
            sp = cli.chat("specify", SYSTEM, SPECIFY.format(
                name=OBJ.NAME, dimensions=d, b_lo=BA.B_LO, b_hi=BA.B_HI, vocab=vocab_text(),
                rules=RTS.exp3_rules_text(), schema=SCHEMA))
            rec["intermediate"] = {"dimensions": d, "specify_raw": sp}
            raw = S.load_json(sp)
            B0, notes = S.normalize(raw) if raw is not None else (None, ["no JSON in answer"])
            rec["normalize"] = notes
            if not B0:
                rec.update(status="no_json" if raw is None else "empty", deployed=False)
                log("  초안 목록 없음: %s" % (notes[:1],))
            else:
                rec["draft"] = B0
                final, rounds = loop(cli, B0, log)
                rec["rounds"] = rounds
                rec["repairs"] = len(rounds) - 1
                if final is None:
                    rec.update(status="ok", deployed=False)
                    log("  구조 검사를 한 번도 통과 못 함. 빠짐")
                else:
                    sem, order = S.semantic_errors(final)
                    xml = S.compile_tree(final, order)
                    ok, msg = exec_check(xml)
                    errs, _ = N.validate(xml)
                    if errs or not ok:
                        raise RuntimeError("컴파일러 오류: %s %s" % (errs[:2], msg))
                    with open(base + ".xml", "w", encoding="utf-8") as fh:
                        fh.write(xml)
                    rec.update(status="ok", deployed=True, final=final, semantic_left=sem,
                               order=dict((s, [final[k2]["id"] for k2 in ks]) for s, ks in order.items()))
                    log("  행동 %d(초안 %d, 뺀 것 %d), 수정 %d회, 남은 의미 오류 %d"
                        % (len(final), len(B0), sum(1 for x in notes if x.startswith("drop")),
                           len(rounds) - 1, len(sem)))
        except Exception as e:  # noqa: BLE001
            rec.update(status="api_error" if "컴파일러" not in str(e) else "compile_error",
                       error="%s: %s" % (type(e).__name__, e))
            log("  실패: %s" % rec["error"])
        rec["calls"] = cli.log
        if any(c["finish_reason"] == "length" for c in cli.log):
            rec["truncated"] = True
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "generate.txt"), "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main_check():
    """생성물 요약. 배치 트리마다 컴파일러 대조(직접 해석 대 실행기). 어긋나거나 트리 없으면 1"""
    fs = sorted(glob.glob(os.path.join(SETS_GLOB, "tree_*.json")))
    if not fs:
        print("생성물 없음: %s" % SETS_GLOB)
        return 1
    bad, dep = 0, 0
    print("%-16s %-13s %5s %5s %5s %5s %6s" % ("트리", "상태", "초안", "최종", "수정", "의미", "대조"))
    for f in fs:
        r = json.load(open(f, encoding="utf-8"))
        name = "%s/%s" % (r["set"], os.path.basename(f)[:-5])
        mis = "-"
        if r.get("deployed"):
            dep += 1
            B = r["final"]
            smt = S.Smt(B)
            order = S.fallback_order(B, smt)
            xml = open(f[:-5] + ".xml", encoding="utf-8").read()
            if xml != S.compile_tree(B, order):
                mis = "XML다름"
                bad += 1
            else:
                m = S.equivalence(B, order, xml, n=2000)
                mis = str(m)
                bad += m > 0
        print("%-16s %-13s %5s %5s %5s %5s %6s" % (
            name, r["status"] + ("" if r.get("deployed") else "(빠짐)"), len(r.get("draft") or []),
            len(r.get("final") or []), r.get("repairs", "-"), len(r.get("semantic_left") or []), mis))
    print("배치 %d / %d, 컴파일러 대조 실패 %d" % (dep, len(fs), bad))
    api = [f for f in fs if json.load(open(f, encoding="utf-8")).get("status") == "api_error"]
    if api:
        print("요청 실패로 끝난 번호 %d개. 같은 생성 명령을 다시 치면 이어서 만듦" % len(api))
    return 1 if (bad or dep == 0 or api) else 0


def main_prompt():
    for k, t in prompt_parts().items():
        print("== %s\n%s\n" % (k.upper(), t))


if __name__ == "__main__":
    cmds = {"generate": main_generate, "check": main_check, "prompt": main_prompt}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        raise SystemExit("usage: python scripts/exp3_author.py {generate,check,prompt} [options]")
    del sys.argv[1]
    sys.exit(cmds[c]() or 0)
