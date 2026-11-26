"""행동트리가 실제로 어떻게 도는지. 생성기와 실행기가 같은 문장을 봐야 한다.

왜 따로 두나: 프롬프트가 LLM에게 "Action이 정한 값은 다른 Action이 덮을 때까지 유지된다"고
말했는데 실행기(scripts/collect.py의 tick)는 매 틱 기준값으로 리셋한다. 정반대였다.
LLM은 값을 되돌리는 가지를 열심히 짰고 그 가지들이 평평한 Fallback을 차지해 뒤 섹션을 통째로
가렸다. 트리 10벌이 전부 틀린 실행 모델 위에서 설계됐다.

여기 적은 문장이 프롬프트로 나가고, 실행기가 이걸 어기면 실행기 쪽이 틀린 것이다.
"""

TICK_HZ = 10.0          # 시뮬레이션 고정 스텝 0.1s. 트리는 매 스텝 한 번 틱된다

# 리셋 규약. 이걸 바꾸면 트리 설계 전체가 바뀌므로 문서와 프롬프트에 같이 박는다.
#
# 매 틱 리셋을 고른 이유는 트리 간 비교 가능성이다. 유지 규약이면 차량의 현재 파라미터가
# 지나온 경로의 함수가 되고, 그러면 같은 상황에 있는 두 차가 다른 값을 갖는 것이 트리 설계
# 때문인지 우연한 경로 때문인지 안 갈린다.
#
# 대가: 임계 근처에서 채터링이 생길 수 있다. 틱당 파라미터 변경 횟수를 수집 때 찍고 있으므로
# (첫 실행에서 3.98회) 문제가 되면 히스테리시스를 조건 노드로 넣는다.
RESET_EACH_TICK = True

RUNTIME_RULES = [
    "The tree is ticked once every simulation step (10 Hz). Every condition is "
    "re-evaluated from scratch on every tick, so a higher-priority branch can take "
    "over at any moment.",

    "Before each tick every parameter is RESET to its baseline value. An Action "
    "overrides the baseline for that tick only. If no branch sets a parameter on a "
    "given tick, that parameter is at its baseline on that tick.",

    "Because of the reset you do NOT need branches that restore a parameter. Do not "
    "write a branch whose only job is to put a parameter back to its default.",

    "Children of a Fallback are tried in order and the first one that succeeds stops "
    "the rest. Exactly one child of a Fallback runs per tick. A child whose guard is "
    "implied by an earlier child's guard can never run. An unconditional child makes "
    "every later sibling dead.",

    # 예시는 노드 예시와 같은 가상 도메인으로 쓴다. 실제 조건 ID를 쓰면 그 조건을
    # 쓰라고 유도하는 셈이고, 이 파일은 도메인 사실이지 조건 목록이 아니다
    "Order the children of a Fallback from the most specific situation to the most "
    "general one. If two children test the SAME condition with different thresholds, "
    "the stricter one must come first: LevelAbove 80 before LevelAbove 50, and "
    "TempBelow 5 before TempBelow 20. Put it the other way round and the second child "
    "is dead.",

    "Children of a Sequence all run, in order, and it stops at the first failure. A "
    "Condition that fails stops the Actions after it in that Sequence.",

    "Children of a Parallel all run on every tick, independently of each other. "
    "Nothing a Parallel child does can stop a sibling from running.",

    "Actions are not commands to a controller. Each Action writes one number into a "
    "parameter vector that the simulator reads after the tick. Two Actions in the "
    "same tick that write different parameters do not interfere. Two that write the "
    "same parameter in the same tick: the last one wins.",

    # 선언값이 언제 정해지는지를 안 적으면 틱마다 다시 뽑히는 줄 알고 설계한다.
    # 앞 판본에서 리셋 규약을 안 적어 트리 10벌이 틀린 실행 모델 위에 지어졌다
    "A declared parameter is drawn once, when the object is created, and never changes "
    "again while that object exists. It is not re-drawn per tick and it does not drift. "
    "Two objects running the same tree hold different draws of it. Design branches "
    "around the fact that a parameter is a fixed property of the individual, not a "
    "signal that varies over time.",

    "The reset above applies to the parameter vector the simulator reads, not to the "
    "declared parameters. Resetting a parameter vector entry means putting back the "
    "baseline the object started with.",
]


# 예시에 쓰는 가짜 리프. 운전과 무관한 도메인이어야 한다.
#
# 왜 예시가 필요한가: v4 첫 8벌 중 절반이 Fallback을 하나도 안 썼고 tree_04는 조건 45개
# 대부분이 Sequence 밖에 떠 있었다. Parallel 직속 Condition은 판정만 하고 아무것도 안 한다.
# 배치 판단이 아니라 노드를 조립할 줄 모르는 것이다.
#
# 왜 온전한 트리를 안 주는가: 그러면 배치를 우리가 정해주는 게 된다. LLM이 3단계에서
# 스스로 도출해야 하는 것을 떠먹이면 실험이 성립 안 한다. 노드 하나씩, 서로 무관한 행동
# 둘로 보여서 어떤 배치 원칙도 암시하지 않게 한다.
EX = {
    "seq": ('<Sequence>\n'
            '  <Condition ID="TankIsEmpty"/>\n'
            '  <Action ID="SetPumpRate" lpm="5"/>\n'
            '</Sequence>'),
    "fb": ('<Fallback>\n'
           '  <Sequence><Condition ID="TankIsEmpty"/>'
           '<Action ID="SetPumpRate" lpm="5"/></Sequence>\n'
           '  <Sequence><Condition ID="TankIsLow"/>'
           '<Action ID="SetPumpRate" lpm="2"/></Sequence>\n'
           '</Fallback>\n'
           '<!-- When TankIsEmpty holds, the second child does nothing on that tick. -->'),
    "par": ('<Parallel>\n'
            '  <Sequence><Condition ID="TankIsEmpty"/>'
            '<Action ID="SetPumpRate" lpm="5"/></Sequence>\n'
            '  <Sequence><Condition ID="DoorIsOpen"/>'
            '<Action ID="SetAlarmVolume" level="3"/></Sequence>\n'
            '</Parallel>\n'
            '<!-- Both children are evaluated on the same tick. Neither blocks the '
            'other. -->'),
    "inv": ('<Inverter>\n'
            '  <Condition ID="TankIsEmpty"/>\n'
            '</Inverter>'),
    "leaf": ('<Condition ID="TankIsLow"/>          <!-- tests, changes nothing -->\n'
             '<Action ID="SetPumpRate" lpm="2"/>  <!-- writes lpm, always succeeds -->\n'
             '<!-- A Condition that is not inside a Sequence with an Action tests '
             'something and then nothing happens. -->'),
}

# 우리 실행기(scripts/bt_runtime.build)가 각 태그를 실제로 무엇으로 만드는지.
# BT.CPP 표준이 아니라 우리 구현이 기준이다. 다르면 LLM이 쓴 노드가 조용히 무시된다.
# Repeat과 RetryUntilSuccessful이 실제로 그렇다. 자식만 남기고 사라진다.
# (태그, 자식 수, 한 틱에 도는 자식, 성공 조건, 주의)
CONTROL_NODES = [
    ("Sequence", "1 or more", "from the first until one fails",
     "succeeds when every child succeeded",
     "A Condition that fails stops the Actions after it."),
    ("Fallback", "1 or more", "from the first until one succeeds",
     "succeeds when some child succeeded",
     "EXACTLY ONE child has an effect per tick. Everything after the first success "
     "is skipped."),
    ("Parallel", "1 or more", "all of them, always",
     "succeeds when every child succeeded",
     "Children cannot block each other. Use this when two branches must both act on "
     "the same tick."),
    ("Inverter", "exactly 1", "its child", "returns the opposite of its child", ""),
    ("ForceSuccess", "exactly 1", "its child", "always succeeds", ""),
    ("ForceFailure", "exactly 1", "its child", "always fails", ""),
    ("Repeat", "exactly 1", "its child ONCE", "returns what its child returns",
     "IGNORED by this executor. Every leaf returns immediately, so repeating inside "
     "one tick changes nothing. Writing it has no effect."),
    ("RetryUntilSuccessful", "exactly 1", "its child ONCE",
     "returns what its child returns",
     "IGNORED by this executor, for the same reason as Repeat."),
]

LEAF_NODES = [
    ("Condition", "succeeds when the thing it tests is true, fails otherwise. "
                  "It never changes anything."),
    ("Action", "ALWAYS succeeds. It writes one number into one parameter and returns. "
               "It never fails and never takes more than one tick."),
]


def nodes_text():
    out = ["Control nodes, as this executor runs them:"]
    for tag, nch, runs, ok, warn in CONTROL_NODES:
        s = "  <%s>  children: %s. Runs: %s. It %s." % (tag, nch, runs, ok)
        if warn:
            s += " " + warn
        out.append(s)
    out.append("Leaf nodes:")
    for tag, d in LEAF_NODES:
        out.append("  <%s>  %s" % (tag, d))
    # 예시는 다른 도메인의 가짜 리프로 쓴다. 형태만 보여주고 내용은 안 준다
    out.append("")
    out.append("How the nodes compose. These use made-up leaf IDs from an unrelated "
               "domain: they show the shape only, not what to build.")
    for title, key in (("A rule is a Sequence of Conditions then Actions", "seq"),
                       ("A Fallback picks one rule", "fb"),
                       ("A Parallel runs several branches on the same tick", "par"),
                       ("An Inverter flips a Condition", "inv"),
                       ("Leaves on their own", "leaf")):
        out.append("")
        out.append("%s:" % title)
        for ln in EX[key].splitlines():
            out.append("  " + ln)
    return "\n".join(out)


def rules_text():
    return nodes_text() + "\n\n" + "\n".join("- " + r for r in RUNTIME_RULES)


# 실험 3. LLM은 트리가 아니라 행동 목록을 쓰고 프로그램이 컴파일(scripts/exp3_schema.py).
# 그래서 노드 조립 규칙 대신 행동 목록이 어떻게 실행되는지만 알림. 컴파일 결과가 이 문장과 같게 도는지는
# exp3_schema.equivalence와 tests/test_exp3.py가 확인. 다르면 컴파일러 쪽이 틀린 것
EXP3_RULES = [
    "Every 0.1 s all conditions are re-evaluated from scratch. A behaviour applies on a tick only "
    "while all of its conditions hold on that tick.",

    "Each setting is decided separately. Among the behaviours for one setting whose conditions all "
    "hold, exactly one applies: the one with the smallest priority number. If several share that "
    "priority, the one whose conditions are a special case of the other's applies; if that still does "
    "not decide it, the one listed first applies. Behaviours for different settings never interfere.",

    "If no behaviour for a setting applies on a tick, the vehicle uses its normal value for that "
    "setting on that tick. You do not need behaviours that restore a normal value.",

    "A behaviour moves the vehicle's normal value by its amount, in the direction given by its effect, "
    "while it applies. The result is kept inside the setting's allowed range.",

    "A value written as [low, high] is drawn once per vehicle, uniformly, when the vehicle is created, "
    "and stays fixed for that vehicle. Different vehicles hold different draws. A single number is the "
    "same for every vehicle.",

    "A behaviour with an empty \"when\" list always holds.",
]


def exp3_rules_text():
    return "\n".join("- " + r for r in EXP3_RULES)


if __name__ == "__main__":
    print("틱 %g Hz, 매 틱 리셋 = %s" % (TICK_HZ, RESET_EACH_TICK))
    print()
    print(rules_text())
