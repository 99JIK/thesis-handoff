"""실험 2 트리 생성과 수정. py3.11 + LLM API. CARLA 불요.

  python scripts/bt_author.py generate --mode stage    --set prop_r1 --n 10
  python scripts/bt_author.py generate --mode zeroshot --set zs_r1   --n 10
  python scripts/bt_author.py prompt                    프롬프트에 들어가는 시스템 문과 카탈로그

출처: C:\\Github\\sil-agent-repertoire scripts/bt_author.py (커밋 a604b91)의 generate 부분.
4단 프롬프트(분해, 유도, 배치, 합성)와 제로샷 프롬프트 문장은 그대로. 바꾼 것:
- 수정 회차마다 후보 XML, 형식/의미 오류, 도달 불가 경고, 오류 유형을 tree_NN.json에 남김.
  같은 트리를 수정 전후로 짝지어 보려는 것(docs/design.md 짝지은 기록)
- 도달 불가는 피드백에 안 넣음. 앞 판은 넣고 지우라고 압박했음. 수정은 구조 오류만 고친다는
  전제를 실험 1과 같게 두려는 것
- 형식 검증 4단계로 실제 실행기 빌드와 1틱(bt_runtime.exec_check)
- 호출마다 finish_reason과 토큰을 남겨 잘림과 요청 실패를 스키마 실패와 가름
- 세트당 시도 수 고정. api_error로 끝난 번호만 다시 돌림(같은 명령을 다시 치면 됨)
- 시스템 문의 "including its negative end" 문장 삭제(범위가 추첨 상자로 바뀜)
- 앞 판 SYNTHESIZE의 "{name}"이 format에 먹혀 "background_vehicle"로 나가던 것 고침
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "spec"))
import bt_nodes as N  # noqa: E402
from bt_runtime import exec_check  # noqa: E402
import environment as ENV  # noqa: E402
import object as OBJ  # noqa: E402
import runtime as RT  # noqa: E402
import tm_api as TM  # noqa: E402

OUT = os.path.join(ROOT, "data", "outputs", "bt")
# 학위논문 p0/p1과 KCC는 Together의 Llama-3.3-70B-Instruct-Turbo(fp8)였는데 2026-10에 서버리스가
# 내려가 OpenRouter로 옮김. 같은 가중치. 프로바이더와 양자화를 못 박고 다른 데로 안 새게 함.
# CoreWeave fp16을 고른 건 판단: 원 가중치 정밀도이고 출력 상한(115k)이 4096보다 넉넉
MODEL = "meta-llama/llama-3.3-70b-instruct"
BASE_URL = "https://openrouter.ai/api/v1"
KEY_ENV = "OPENROUTER_API_KEY"
PROVIDER = {"order": ["CoreWeave"], "allow_fallbacks": False, "quantizations": ["fp16"]}
TEMPERATURE = 0.7
MAX_TOKENS = 4096
MAX_REPAIR = 6
N_LO, N_HI = 6, 10        # 행동 차원 개수. KCC2026과 같은 값
B_LO, B_HI = 2, 4         # 차원당 구체 행동 개수

SYSTEM = """You are an expert in simulation-based testing and behaviour design for \
Software-In-The-Loop (SIL) environments.

You generate Behaviour Trees (BTs) for ENVIRONMENT OBJECTS in SIL simulation, not for \
the System Under Test, but for the surrounding agents that the System Under Test \
interacts with.

A behaviour repertoire BT is NOT a script for one test scenario. It contains ALL \
possible behaviours the object could exhibit in reality, organised as distinct \
subtrees. Simulation conditions activate the appropriate subtrees at runtime.

BehaviorTree.CPP v4 XML format:
- Root wrapper: <root BTCPP_format="4"> with <BehaviorTree ID="...">
- An optional <Params> block sits inside <root>, before <BehaviorTree>
- <BehaviorTree> must have exactly one child
- Control nodes: <Sequence>, <Fallback>, <Parallel>. They never carry an ID attribute.
- Decorator nodes: <Repeat>, <Inverter>, <ForceSuccess>, <ForceFailure>, \
<RetryUntilSuccessful>. Exactly one child each.
- Leaf nodes: <Action ID="..."/>, <Condition ID="..."/>. No children.

You may ONLY use leaf node IDs from the catalogue you are given. Do not invent node \
IDs. Every leaf attribute value must be inside the range stated in the catalogue.

How the tree runs, which you must design for:
{runtime_rules}

The simulator already does the following on its own. Do not rebuild any of it in the \
tree:
{tm_auto}

The environment this object operates in:
{env_facts}

Output ONLY valid BehaviorTree.CPP v4 XML wrapped in ```xml ... ``` blocks."""
SYSTEM = SYSTEM.format(runtime_rules=RT.rules_text(), tm_auto=TM.auto_lines(),
                       env_facts=ENV.facts_text())

INTERMEDIATE_SYSTEM = ("You are an expert in simulation-based testing and behaviour "
                       "design for SIL environments. You are helping enumerate "
                       "behaviours for an environment object in a simulation. Output "
                       "plain text only, no XML, no code blocks.")

DECOMPOSE = """You are analysing a SIL environment object to enumerate the BEHAVIOUR \
DIMENSIONS it could exhibit in a realistic simulation.

A behaviour dimension is a high-level category of related behaviours, for example \
"emergency response", "normal operation", "fault handling", "user interaction".

Those examples come from other domains. They show the level of abstraction, not the \
content. Do not carry them over.

Domain: {domain}
SUT (which the object shares its environment with): {sut}
Environment Object: {name} - {desc}

The object can sense the following and can adjust the following parameters. Your \
dimensions must be things this vocabulary can actually express.

{catalogue}

List {n_lo} to {n_hi} distinct behaviour dimensions for this object. Derive them from \
the vocabulary above: whatever the object can sense marks a situation it could behave \
differently in, and whatever it can adjust marks a way it could behave. Be exhaustive, \
prefer over-listing to under-listing.

Output ONLY a numbered list, one dimension per line, with a short name and a \
one-sentence description. No XML, no preamble."""

ELICIT = """You previously identified the following behaviour dimensions for the SIL \
environment object "{name}":

{dimensions}

For EACH dimension above, list {b_lo} to {b_hi} concrete, specific behaviours the \
object could exhibit. A concrete behaviour is something that could be implemented as \
a Behaviour Tree subtree: it has a clear trigger condition and a clear parameter \
setting.

Available vocabulary (do not go outside it):

{catalogue}

Be exhaustive within each dimension. Think about variations, edge cases, and \
situations a simulation needs to reproduce.

Output as a structured list, grouped by dimension:

Dimension 1: <name>
  - <concrete behaviour>
  - <concrete behaviour>
Dimension 2: <name>
  - <concrete behaviour>

No XML, no preamble. Just the structured list."""

ORGANIZE = """You have enumerated the following behaviours for the SIL environment \
object "{name}":

{enumeration}

Node catalogue:

{catalogue}

How the tree runs:
{runtime_rules}

Before writing any XML, work out how to lay these behaviours out so that every one of \
them can actually run. Answer in order.

1. For each parameter that appears in the catalogue's Action nodes, list which of the \
enumerated behaviours write it. Note any parameter that no behaviour writes.

2. For each parameter, say whether two or more behaviours that write it can hold at \
the same instant. If they can, say which should win and why.

3. Suppose you put every behaviour as a Sequence and make all of them children of one \
Fallback at the root. Using the control-node rules above, work through ONE specific \
pair from your list where an earlier child would prevent a later child from ever \
running, and say what that means for the parameter the later child writes.

4. Decide the layout. State what the root node is, what each of its children is \
responsible for, and why each enumerated behaviour is reachable under that layout. If \
your answer to 3 shows a problem, your layout must solve it rather than repeat it.

5. Now check the layout you just chose. For every Fallback in it, write its children \
out in the order they will be tried. Then go through the pairs: for each later child, \
can its conditions hold at a moment when an earlier child's conditions also hold? If \
they can, the later child is dead and you must reorder them or narrow the earlier \
one. Report the ordered children of each Fallback and state that you checked every \
pair.

6. List any behaviour from the enumeration that you are dropping, with the reason.

7. Every leaf you write carries a number: an Action value or a Condition threshold. \
Go through them and split them into two lists. First, the numbers that are the same \
for every individual object of this kind. Second, the numbers that depend on which \
individual object it is, because two objects of this kind would not agree on the \
right value. For each entry in the second list give the range you would allow and the \
one value you would pick if you were forced to pick a single one. Either list may be \
empty if that is your judgement.

Output plain text only. No XML, no code blocks."""

SYNTHESIZE = """You are building a behaviour repertoire Behaviour Tree for the SIL \
environment object "{name}" (a {desc}) in the {domain} domain.

Behaviours to implement:

{enumeration}

Node catalogue. Use ONLY these leaf IDs, with the attribute names and ranges shown:

{catalogue}

The layout you decided on:

{layout}

Write that tree in BehaviorTree.CPP v4 XML. Follow the layout you chose above, \
including the child order you checked. Each concrete behaviour is a Sequence of \
Conditions followed by the Actions it sets.

Your answer to question 7 above decides the <Params> block. Every number you put in \
the second list becomes a <Param> with the range and the single value you gave, and \
you write {{name}} at the leaf that uses it. Every number in the first list stays a \
literal.

Two hard requirements. Inside any Fallback, no child's conditions may be implied by an \
earlier sibling's, so a stricter threshold on the same condition always comes first. \
An unconditional child, if you write one at all, must be the last child of its \
Fallback.

Output ONLY the BehaviorTree.CPP v4 XML wrapped in ```xml ... ``` blocks."""

ZEROSHOT = """You are building a behaviour repertoire Behaviour Tree for the SIL \
environment object "{name}" (a {desc}) in the {domain} domain.

The tree must cover the situations this object can be in: normal operation, fault \
states, safety functions, and boundary situations. Decide yourself which behaviours \
belong in the repertoire and how many.

Node catalogue. Use ONLY these leaf IDs, with the attribute names and ranges shown:

{catalogue}

Runtime rules:

{runtime_rules}

Write the tree in BehaviorTree.CPP v4 XML. The root is a Fallback whose children are \
the behaviour groups. Each concrete behaviour is a Sequence of Conditions followed by \
the Actions it sets.

Any number a leaf needs may be either a literal or a <Param>. A <Param> declares a \
name and a range, and each vehicle draws one value from that range and keeps it for \
the episode; you write {{name}} at the leaf that uses it. Declare a <Param> where \
vehicles should differ and use a literal where they should not.

Two hard requirements. Inside any Fallback, no child's conditions may be implied by an \
earlier sibling's, so a stricter threshold on the same condition always comes first. \
An unconditional child, if you write one at all, must be the last child of its \
Fallback.

Output ONLY the BehaviorTree.CPP v4 XML wrapped in ```xml ... ``` blocks."""

# 앞 판의 도달 불가 압박 문단("One exception ... can never run")을 뺐음
REPAIR = """Your previous Behaviour Tree has errors that prevent it from loading or \
executing.

ERRORS:
{feedback}

Previous BT:
```xml
{previous}
```

Node catalogue. Use ONLY these leaf IDs, with the attribute names and ranges shown:

{catalogue}

Produce a corrected version that resolves these errors. PRESERVE the original tree's \
intent and structure as much as possible, only fix the specific errors above. Do NOT \
remove subtrees that implement a distinct behaviour, and do not add new behaviours \
that were not there before.

Output ONLY the corrected BehaviorTree.CPP v4 XML wrapped in ```xml ... ``` blocks."""


def load_env():
    """레포 루트 .env. 이미 설정된 환경변수가 이김"""
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def extract_xml(text):
    """LLM 응답에서 XML. KCC2026의 폴백 순서"""
    if not text:
        return None
    for pat in (r"```xml\s*(.*?)```", r"```\s*(<(?:root|\?xml).*?)```"):
        m = re.search(pat, text, re.S)
        if m:
            return m.group(1).strip()
    for pat in (r"(<\?xml.*?</root>)", r"(<root\b.*?</root>)",
                r"(<BehaviorTree\b.*?</BehaviorTree>)"):
        m = re.search(pat, text, re.S)
        if m:
            return m.group(1).strip()
    return None


class Client(object):
    def __init__(self, model, base_url, key):
        from openai import OpenAI
        self.c = OpenAI(api_key=key, base_url=base_url)
        self.base = base_url
        self.model = model
        self.log = []       # 호출마다 (단계, finish_reason, 토큰, 초)

    def chat(self, stage, system, user, retries=4):
        for a in range(retries):
            t0 = time.time()
            try:
                r = self.c.chat.completions.create(
                    model=self.model, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    extra_body={"provider": PROVIDER} if "openrouter" in self.base else None)
            except Exception as e:  # noqa: BLE001
                if a == retries - 1:
                    raise
                print("   재시도 %d/%d: %s" % (a + 1, retries - 1, type(e).__name__),
                      flush=True)
                time.sleep(2 ** (a + 1))
                continue
            ch = r.choices[0]
            u = r.usage
            self.log.append({"stage": stage, "finish_reason": ch.finish_reason,
                             "prompt_tokens": getattr(u, "prompt_tokens", None),
                             "completion_tokens": getattr(u, "completion_tokens", None),
                             "seconds": round(time.time() - t0, 2),
                             "model_reported": getattr(r, "model", None),
                             "provider": (getattr(r, "model_extra", None) or {}).get("provider")})
            return ch.message.content or ""


def assess(xml):
    """회차 하나의 판정. 형식 4단계(문법, 스키마, 구조, 실행기)와 의미, 도달 불가"""
    r = N.check(xml)
    if not r[N.FORMAT] and not r[N.SEMANTIC]:
        ok, msg = exec_check(xml)
        if not ok:
            r[N.FORMAT].append(msg)
    errs = r[N.FORMAT] + r[N.SEMANTIC]
    r["types"] = sorted(set(N.err_type(e) for e in errs))
    r["executable"] = not errs
    return r


def generate_stage(cli, cat):
    d = cli.chat("decompose", INTERMEDIATE_SYSTEM, DECOMPOSE.format(
        domain=OBJ.DOMAIN, sut=OBJ.SUT, name=OBJ.NAME, desc=OBJ.DESC,
        catalogue=cat, n_lo=N_LO, n_hi=N_HI))
    e = cli.chat("elicit", INTERMEDIATE_SYSTEM, ELICIT.format(
        name=OBJ.NAME, dimensions=d, b_lo=B_LO, b_hi=B_HI, catalogue=cat))
    o = cli.chat("organize", INTERMEDIATE_SYSTEM, ORGANIZE.format(
        name=OBJ.NAME, enumeration=e, catalogue=cat, runtime_rules=RT.rules_text()))
    s = cli.chat("synthesize", SYSTEM, SYNTHESIZE.format(
        name=OBJ.NAME, desc=OBJ.DESC, domain=OBJ.DOMAIN, enumeration=e, catalogue=cat,
        layout=o))
    return s, {"dimensions": d, "enumeration": e, "layout": o, "synth_raw": s}


def generate_zeroshot(cli, cat):
    """한 호출. 나머지 입력과 수정 루프는 4단과 같음"""
    s = cli.chat("zeroshot", SYSTEM, ZEROSHOT.format(
        name=OBJ.NAME, desc=OBJ.DESC, domain=OBJ.DOMAIN, catalogue=cat,
        runtime_rules=RT.rules_text()))
    return s, {"synth_raw": s}


def _round(k, xml, res, status):
    return {"round": k, "status": status, "xml": xml,
            "format": res[N.FORMAT], "semantic": res[N.SEMANTIC],
            "unreachable": res["unreachable"], "types": res["types"],
            "stats": res["stats"]}


def repair_loop(cli, cat, xml, log):
    """회차 0이 초안. 실행 가능해지거나 상한까지. 회차마다 전부 기록"""
    res = assess(xml)
    rounds = [_round(0, xml, res, "ok" if res["executable"] else "errors")]
    k = 0
    while not res["executable"] and k < MAX_REPAIR:
        k += 1
        errs = res[N.FORMAT] + res[N.SEMANTIC]
        log("  수정 %d회차 전 오류 %d개: %s" % (k, len(errs), errs[0][:70]))
        out = cli.chat("repair_%d" % k, SYSTEM, REPAIR.format(
            feedback="\n".join(errs[:20]), previous=xml, catalogue=cat))
        nxt = extract_xml(out)
        if not nxt:
            # 이 회차는 후보가 없음. 이전 트리를 최종으로 두고 끝냄
            rounds.append({"round": k, "status": "no_xml", "xml": None,
                           "raw": out[:2000], "format": [], "semantic": [],
                           "unreachable": [], "types": ["no_xml"], "stats": {}})
            break
        xml = nxt
        res = assess(xml)
        rounds.append(_round(k, xml, res, "ok" if res["executable"] else "errors"))
    return xml, res, rounds


def main_generate():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("stage", "zeroshot"))
    ap.add_argument("--set", dest="setname", required=True,
                    help="세트 이름. prop_r1, prop_r2, zs_r1, zs_r2")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--key-env", default=KEY_ENV)
    ap.add_argument("--force", action="store_true", help="있는 번호도 다시 생성")
    ap.add_argument("--catalog", default="v1", choices=("v1", "v2"),
                    help="카탈로그 판. v2는 보조 실험 9 설명 수정판. 기본 v1이 실험 2")
    a = ap.parse_args()
    N.set_catalog(a.catalog)

    load_env()
    key = os.environ.get(a.key_env)
    if not key:
        raise SystemExit("%s 가 없음. 레포 루트 .env에 넣을 것" % a.key_env)

    out = os.path.join(OUT, a.setname)
    os.makedirs(out, exist_ok=True)
    cat = N.catalogue_text()
    # 생성 시점 프롬프트 재료를 세트와 같이 남김. 나중에 카탈로그가 바뀌어도 대조 가능
    with open(os.path.join(out, "prompt_snapshot.txt"), "w", encoding="utf-8") as fh:
        fh.write("== SYSTEM\n" + SYSTEM + "\n\n== CATALOGUE %s\n" % a.catalog + cat + "\n")

    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    log("세트 %s, 모델 %s, %s, 시도 %d" % (a.setname, a.model,
                                     "4단" if a.mode == "stage" else "제로샷", a.n))
    for i in range(a.n):
        base = os.path.join(out, "tree_%02d" % i)
        if os.path.exists(base + ".json") and not a.force:
            prev = json.load(open(base + ".json", encoding="utf-8"))
            if prev.get("status") != "api_error":
                continue
        log("")
        log("== tree_%02d" % i)
        cli = Client(a.model, a.base_url, key)
        rec = {"set": a.setname, "index": i, "mode": a.mode, "model": a.model,
               "base_url": a.base_url, "provider_pin": PROVIDER, "temperature": TEMPERATURE,
               "max_tokens": MAX_TOKENS, "max_repair": MAX_REPAIR, "catalog": a.catalog,
               "time_utc": datetime.datetime.utcnow().isoformat() + "Z"}
        try:
            gen = generate_stage if a.mode == "stage" else generate_zeroshot
            raw, mid = gen(cli, cat)
            rec["intermediate"] = mid
            xml = extract_xml(raw)
            if not xml:
                rec.update(status="no_xml", rounds=[], executable=False)
                log("  초안 XML을 못 꺼냄")
            else:
                xml, res, rounds = repair_loop(cli, cat, xml, log)
                rec.update(status="ok", rounds=rounds, executable=res["executable"],
                           repairs=len(rounds) - 1,
                           cap_hit=(len(rounds) - 1 == MAX_REPAIR
                                    and not res["executable"]))
                with open(base + ".xml", "w", encoding="utf-8") as fh:
                    fh.write(xml)
                log("  초안 오류 %d, 최종 %s, 수정 %d회, 도달 불가 %d"
                    % (len(rounds[0]["format"]) + len(rounds[0]["semantic"]),
                       "실행 가능" if res["executable"] else "실행 불가",
                       len(rounds) - 1, len(res["unreachable"])))
        except Exception as e:  # noqa: BLE001
            rec.update(status="api_error", error="%s: %s" % (type(e).__name__, e))
            log("  요청 실패: %s" % rec["error"])
        rec["calls"] = cli.log
        if any(c["finish_reason"] == "length" for c in cli.log):
            rec["truncated"] = True
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "generate.txt"), "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main_prompt():
    print(SYSTEM)
    print()
    print(N.catalogue_text())


if __name__ == "__main__":
    cmds = {"generate": main_generate, "prompt": main_prompt}
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c not in cmds:
        raise SystemExit("usage: python scripts/bt_author.py {generate,prompt} [options]")
    del sys.argv[1]
    cmds[c]()
