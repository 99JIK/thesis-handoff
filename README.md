# 논문 집필 자료 묶음

요청 목록(3~5장)에 대한 답과 근거 파일이다. 코드와 결과 파일은 원본 그대로 복사했다. 줄 번호는 이 묶음 안 파일 기준이다.
표기 규칙: **[코드]**는 실행 코드나 저장 기록에서 확인한 사실, **[설계]**는 설계 문서에 적힌 판단값이다.

| 폴더 | 내용 |
|---|---|
| [`ch2_related/`](ch2_related/) | 관련 연구 위치와 검증한 서지(`related_work.md`) |
| [`ch3_method/`](ch3_method/) | 생성, 카탈로그, 컴파일, SMT, 수정 루프 코드. 프롬프트 스냅샷 4개, 출력 예시 1개 |
| [`ch4_design/`](ch4_design/) | 수집, 채점, 평가 코드. 설계 문서. 실측 전처리 코드 |
| [`ch5_results/`](ch5_results/) | 결과 표와 CSV, 그림 PNG와 그리는 스크립트, 결과 요약 `docs/results.md` |

실측 자료(NGSIM, highD, inD, SinD, TGSIM)와 그 파생물은 재배포 불가라 넣지 않았다. 전처리 코드만 있다.

## 3장 제안 기법

### 프롬프트

- 템플릿 원문: [`exp3_author.py`](ch3_method/scripts/exp3_author.py) 33~48행 SYSTEM, 50~74행 CLASSIFY(분류), 76~95행 SPECIFY(구체화), 97~122행 SCHEMA(JSON 필드 정의), 124~146행 REPAIR(수정)
- 실행 규칙 문장: [`spec/runtime.py`](ch3_method/spec/runtime.py) 179~200행 `EXP3_RULES`
- 실제로 보낸 전문: [`prompts/`](ch3_method/prompts/) 성향별 스냅샷 4개(SYSTEM, CLASSIFY, SPECIFY, REPAIR 순)
- 주의: 분류 호출의 system 메시지는 스냅샷의 SYSTEM이 아니라 [`bt_author.py`](ch3_method/scripts/bt_author.py) 89~92행 `INTERMEDIATE_SYSTEM`. 스냅샷의 SYSTEM은 구체화와 수정 호출용
- 실제 출력 예시 전체: [`example/exp4_anxious_tree_04.md`](ch3_method/example/exp4_anxious_tree_04.md)(분류 응답, 구체화 응답, 회차별 오류). 원 기록 json, 컴파일된 XML 같이 둠. 수정 호출의 원문 응답은 저장하지 않고 파싱된 목록만 남김

### 성향 입력

[코드] [`exp4_author.py`](ch3_method/scripts/exp4_author.py) 27~36행 `STYLES`. MDSI 네 영역(Taubman-Ben-Ari 외 2004)을 보고 직접 쓴 문장이고 설정 이름, 숫자, 방향 지시는 넣지 않음([설계]).

| 영역 | 원문 |
|---|---|
| 무모와 부주의 | The driver enjoys driving fast and taking chances, gets bored easily, and pays loose attention to rules and to other road users. |
| 불안 | The driver feels tense and uneasy behind the wheel, worries about making mistakes, and is easily unsettled by traffic around them. |
| 신중과 인내 | The driver is calm and patient, plans ahead, and prefers to keep a margin of safety rather than save time. |
| 분노와 적대 | The driver is easily irritated by other road users, feels in a hurry, and reacts strongly when held up. |

붙이는 방식([코드] `exp4_author.py` 64~67행):
1. 객체 설명 끝에 공백 하나 두고 이어 붙인다(기본 설명은 [`spec/object.py`](ch3_method/spec/object.py) 26~30행).
2. 분류 호출: 이 설명이 `Environment Object: {name} - {desc}` 자리에 들어간다.
3. 구체화와 수정 호출: 앞 대화를 이어받지 않는 별도 요청이라 "The object is described as follows: <설명>" 한 줄을 넣는다.

### 노드 카탈로그(v3)

[코드] 조건 20종, 행동 노드 8종([`bt_nodes.py`](ch3_method/scripts/bt_nodes.py) 53~131행). 단 `SetKeepRight`는 LLM에 열지 않아 생성에 쓰는 설정은 7종([`exp3_schema.py`](ch3_method/scripts/exp3_schema.py) 31~40행). 기존 표 5.1, 5.2와 같은지는 이 표와 대조해 주세요.

| 조건(인자 없음) | 조건(인자, 범위) |
|---|---|
| IsStopped(1.8 km/h 미만), LeaderPresent(앞 100 m, 폭 2 m 원뿔), LeaderStopped, InJunction, TrafficLightRed, TrafficLightGreen, IsRightmostLane, IsLeftmostLane | SpeedBelow, SpeedAbove: km/h 0~117 |
| | GapBelow, GapAbove: m 0~100 |
| | ClosingFaster, OpeningFaster: m/s 0~32.5 |
| | JunctionAhead: m 11~60(10 m 단위로만 보임) |
| | SpeedLimitBelow, SpeedLimitAbove: km/h 0~90 |
| | LaneCountAbove: 차로 1~5 |
| | TimeInLaneAbove: s 0~120 |
| | CurvatureAbove: 1/m 0~0.314 |

범위 출처: 117 = 90 x 1.3, 32.5 = 117/3.6, 0.314 = π/10(`bt_nodes.py` 85~90행). v3은 v2에서 SpeedLimit 두 조건의 설명만 바꿈(마지막으로 지난 제한 표지판 값, 표지판 전엔 30 km/h).

| 설정 | effect | 기준값, 하한~상한 | amount 상한, 단위 |
|---|---|---|---|
| speed | slower, faster | 0, -30~30 % | 30, 제한속도 대비 %(양수가 느림) |
| gap | wider, narrower | 2.5, 0.5~4.5 m | 2 m |
| lane_change | allow, forbid | 추첨 시 허용 확률 0.8 | 없음 |
| ign_lights, ign_signs | more | 0, 0~10 % | 10 %p |
| ign_vehicles, ign_walkers | more | 0, 0~5 % | 5 %p |

설정 범위는 앞 레포 값이고 CARLA가 정한 범위가 아님(`bt_nodes.py` 18행 주석).

### 조립 규칙

- **출발값은 차량별 추첨값이다.** TM 기본값이 아니다. [코드] 컴파일 단계에서는 트리에 `기준값 + 부호 x amount`를 적는다([`exp3_schema.py`](ch3_method/scripts/exp3_schema.py) 379~390행). 실행할 때 offset 방식으로 `o = 차량 추첨값 + (트리 값 - 기준값)`을 계산하고 설정 범위로 자른다([`bt_runtime.py`](ch3_method/scripts/bt_runtime.py) 106~125행). 그래서 실제 값은 **차량 추첨값 ± amount, 범위로 자름**이다
- 추첨: 차량마다 스폰 때 하한~상한 균등분포([`collect.py`](ch4_design/scripts/collect.py) 104~113행)
- amount가 `[lo, hi]` 범위로 오면 차량마다 스폰 때 그 안에서 한 번 균등하게 뽑는다(`bt_runtime.py` 204~207행)
- 크기 단위: speed는 제한속도 대비 %, gap은 m, ign_*는 %p(위 표)
- lane_change는 범위가 없어 offset을 거치지 않고, 조건이 참인 동안 허용이나 금지로 덮어쓴다
- 매 틱 적용값을 추첨값으로 초기화하므로, 켜진 행동이 없으면 추첨값으로 돌아간다(`bt_runtime.py` 222~230행)
- speed 기준값 0은 CARLA 기본 30%를 바꾼 것([`spec/tm_api.py`](ch3_method/spec/tm_api.py) 62~70행). gap 2.5 m가 TM 기본값과 같은지는 확인하지 못함

### 규칙 우선순위

- **LLM이 정한다.** 행동마다 정수 `priority`를 쓰게 하고, 같은 설정 안에서 작은 수가 이긴다(SCHEMA, `EXP3_RULES`)
- [코드] 컴파일러 순서(`exp3_schema.py` 299~317행 `fallback_order`): priority 오름차순. 같으면 SMT로 엄격히 더 구체적인 조건(함의)이 앞. 그래도 같으면 목록 순서
- 트리 구조(`compile_tree` 393~427행): 루트 Parallel 아래 설정마다 Fallback 하나, 행동 하나는 Sequence(조건들, Action). 설정마다 순서상 처음으로 조건이 모두 참인 행동 하나만 적용됨

### SMT 인코딩

- 변수([`bt_verify.py`](ch3_method/diag/bt_verify.py) 266~297행): speed, gap, dv, limit, lane_time, curv(실수), lead, lead_stopped, in_junction(참거짓), tl, nl, nr(정수). 조건식은 실행기 판정식과 같음(97~138행)
- 정의역(느슨 정의역, 46~54행 상수): speed 0~200, 선행차 있을 때 gap -10~100과 dv ±55.6, 없을 때 gap 999와 dv 0, 교차로 거리 {10, ..., 60, 999}, 신호 0~3, 제한속도 0~200, 좌우 차로 수 0~4, lane_time 0 이상, 곡률 0~π/10. **[설계] 실행기 지각 치역을 포함하도록 넉넉히 잡은 판단값**(41~45행 주석)
- 범위 임계는 행동마다 따로 실수 변수를 두고 `[lo, hi]`로 묶음. 모두 존재 양화라 "어떤 차, 어떤 상황에서든"의 뜻(`exp3_schema.py` 258~273행)
- 판정(`exp3_schema.py` 326~366행 `semantic_errors`). $C$는 행동의 조건, $G$는 정의역과 임계 제약:
  - **조건 모순**: $C_k \wedge G_k$가 unsat
  - **가려짐**: 우선순위상 앞에 있는 살아 있는 행동 $i$들에 대해 $\bigwedge C_i \wedge C_k \wedge G_k \wedge \bigwedge \neg G_i$가 unsat. 즉 $k$가 참인 모든 상황에서 앞 행동이 먼저 켜짐
  - **모호한 동률**: 같은 설정, 같은 priority, 값이 다르고, 두 조건이 동시에 참일 수 있고(sat), 어느 쪽도 다른 쪽의 엄격한 특수형이 아닌 쌍
- [설계] 동률은 수정 루프에서는 오류로 되돌려 주지만 평가에서는 결함으로 세지 않음(컴파일러가 결정적으로 정하고 모델 의도만 모름)

### 수정 루프

- 오류 메시지 템플릿: 구조 오류 `exp3_schema.py` 122~196행, 의미 오류 335행(모순), 349~351행(가려짐), 362~365행(동률), 금지 수정 219~225행. 실제 메시지 예는 [출력 예시](ch3_method/example/exp4_anxious_tree_04.md) 끝의 회차 기록
- 금지 수정 감지(`exp3_schema.py` 208~226행 `forbidden`): 행동 id 집합이 달라지면 추가나 삭제. 행동마다 (설정, effect, 정렬한 조건 목록)을 비교해 조건이나 방향 변경을 잡음. 걸리면 그 회차는 거부로 기록하고(회차 수에 포함) 위반 문장과 직전 통과 목록으로 다시 요청. intent와 dimension 문장은 거부하지 않고 초안 값으로 되돌림(229~241행)
- 허용되는 수정은 결과적으로 임계값, amount, priority뿐
- 6회 안에 못 통과하면([코드] `exp3_author.py` 205~243, 296~315행): 구조 검사를 통과한 마지막 목록을 컴파일해 배치하고 남은 의미 오류를 기록(`semantic_left`). 구조 검사를 한 번도 통과 못 하면 빠짐. 본 실험 20개는 모두 배치됨(상한 도달 14개, 그 14개가 모두 의미 오류가 남은 트리)

## 4장 실험 설계

### 설계 문서와 고정 시점

- **본 실험 설계 정본은 [`design_exp4.md`](ch4_design/docs/design/design_exp4.md).** 같은 폴더의 [`design.md`](ch4_design/docs/design/design.md), [`design_exp3.md`](ch4_design/docs/design/design_exp3.md)는 앞선 실험의 고정본이라 본 실험과 다름(`design.md`는 시드 0~7, 기준·추첨·트리 세 조건, XML 직접 생성. `design_exp3.md`는 본 실험 바로 앞 실험으로 시드가 다르고 성향 입력과 `pknob` 대조가 없음). 고속도로 제외 근거와 본 실험이 이어받은 정의를 찾으려고 넣은 것
- `design_exp4.md` 머리의 "상태: 초안, 고정 전(2026-11-20)"은 갱신 안 된 줄. 고정은 원본 레포 태그 `design-v4`로 확인함: 2026-11-20 13:47, 태그 메시지 "실험 4 설계 고정(생성 시작 뒤, 파일럿과 수집 전)"
- 태그 시점 문서에 이미 있던 것(태그의 `docs/design_exp4.md`를 직접 확인): 대비 둘(`ptree - knob`, `ptree - pknob`), 층 둘(교차로, 도심 블록), 맵 8개 95% t 구간, 하한 > 0 증가, 상한 < 0 감소, 0을 품으면 판정 안 함, 보정 없이 따로 보고. 조작 확인 기준, 위약 대조, 결과 조합별 해석 표(조작 확인 실패면 다양성을 성향 효과로 읽지 않음 포함)
- 수집이 끝난 뒤, 평가를 한 번도 안 돌린 상태에서 정한 것(2026-11-22, 변경 기록 해당 줄): 조작 확인 단위, 위약 계산 방식, precision 구간, 시드 잡음 바닥 등 평가 구현 세부. 최소 틱, 최소 대수 같은 판단값 포함
- 결과를 본 뒤 추가한 것: Vendi 민감도, 추가 실험 전부(생성 방식 비교, 수정 루프 실행 효과). 문서에 **사후**로 표시됨
- 설계 자체가 앞 실험 결과를 본 뒤 세운 것이라 새 시드 24~31로만 판정함(문서 4행)
- 다양성 표의 `p_holm` 열은 함께 출력한 Wilcoxon 부호 순위 p를 대비 하나 안 두 층에 Holm 보정한 값([`measure.py`](ch4_design/scripts/measure.py) `tests`). **판정 문구는 보정 없는 t 구간으로 냄**(설계대로)
- 사실대로 쓸 수 있는 문장: "판정 규칙은 트리 생성을 시작한 뒤, 파일럿과 수집 전에 고정했다. 평가 구현 세부는 수집 뒤 평가를 돌리기 전에 정했다." 고정 시점에 본 것은 생성 로그와 트리 12개의 행동 목록뿐(변경 기록)

### 맵과 시드

- 맵 8개: Town01, 02, 03, 04, 05, 06, 07, 10HD([`collect.py`](ch4_design/scripts/collect.py) 70행). 시드 24~31, 조건당 64판. 측정 300 s, 워밍업 60 s, 0.1 s 간격
- **층은 맵이 아니라 도로 단위로 나뉜다**([`measure.py`](ch4_design/scripts/measure.py) 164~183행). 교차로 연결로는 junction, 제한 30/40 km/h 일반 도로는 urb_block, 60/90 km/h는 hw_link. 그래서 junction과 urb_block은 8맵 모두에 있고, hw_link는 Town04, 05, 06 세 맵에만 있음
- 시드가 바꾸는 것: 차량별 설정 추첨(`collect.py` 209, 215행), TM 난수(195행), 트리 배정 순서(seed+1000), 트리 파라미터 추첨(seed+2000)
- **시드가 바꾸지 않는 것: 스폰 자리와 차종.** 스폰은 지도 순서대로 앞 n개(214행), 차종은 순번 순환(216행)

### 트리 배정

- 차량 수 기본 120대(`collect.py` 388행). 스폰 자리가 모자란 Town02는 101대, Town07은 116대
- 스폰 순서로 20대씩 묶고, 묶음마다 트리 20개의 순서를 섞어 한 대에 하나씩 줌(262~266행). 120대 맵에서 트리당 6대. 지도 순서와 트리가 묶이지 않게 한 블록 순열(254행 주석)
- ptree 조건에서는 **모든 차량에 트리를 적용함**. 성향 영역 균등(영역당 트리 5개)

### 비교 조건

- plain: 기준값. knob: 차량별 추첨. ptree: 같은 추첨 위에 트리(offset). pknob: 같은 시드, 같은 스폰 자리 ptree 차가 실제로 받은 값의 시간 평균을 상수로 줌
- pknob 평균 계산([코드] `collect.py` 285~315, 361~368행): ptree 판에서 매 틱 적용값을 차량별로 누적. 워밍업 뒤 초기화해 측정 구간만 평균. lane_change는 허용 틱 비율이 0.5 이상이면 허용([설계] 판단값, 143행). 같은 자리 ptree 값이 없으면 추첨값을 쓰도록 했으나 64판 모두 해당 0건
- **sd 17.29**: [`mech.md`](ch5_results/results/exp4/mech.md) 적용값 퍼짐 표의 speed_diff 차량 간 표준편차. **단위는 % (제한속도 대비 속도차)**. 판마다 차량 간 sd를 내고 판 평균. knob 17.74, ptree와 pknob 17.29(같은 값이 맞음. pknob은 ptree 평균을 그대로 받음)

### RQ2 지표

- 의미 방향 사전: [`rule_intent.py`](ch4_design/diag/rule_intent.py) 33~46행 `WORDS`(설계 때 고정, 소문자 부분 일치). intent 문장에서 한쪽 낱말만 걸려야 방향을 줌(121~124행). effect 방향과 비교해 일치, 불일치, 판정 불가(127~137행). effect에서 방향으로 바꾸는 표는 [`exp3_eval.py`](ch4_design/scripts/exp3_eval.py) 43~44행
- 발화율(`exp3_eval.py` 393~400, 476~478행): 행동마다, 그 트리를 받은 차량-틱 가운데 그 행동이 실제 최종 명령이 된 틱의 비율
- 실현율(422~471행): speed는 정지 틱을 뺀 제한속도 대비 속도비, gap은 앞차 100 m 안 간격. 그 행동이 켜진 ptree 틱의 평균과, 같은 시드 knob 판의 같은 자리 차에서 같은 트리였다면 켜졌을 틱의 평균을 비교. 차량별 차이를 가중 평균하고, 양쪽 50틱 이상일 때만 판정. 부호가 기대 방향이면 실현
- 위약([`exp4_eval.py`](ch4_design/scripts/exp4_eval.py) 540~627행): ptree 자리에 같은 시드 plain 판을 넣고 같은 계산. 구간은 (맵, 시드) 묶음 부트스트랩 2000회. [설계] 기대값 약 0.5
- 기록 재현(`exp3_eval.py` 401~406, 483행): 기록된 지각값으로 행동 목록을 다시 해석한 최종 명령이 실행기 기록과 모든 행동에서 같은 틱의 비율

### RQ1 척도

- 창 분할(`measure.py` 252~289행): (차량, 층)이 이어진 구간을 run으로 묶음(층이 바뀌거나 0.15 s 넘게 끊기면 새 run). 도심 블록은 run 안 누적거리 40 m마다 자르고 꼬리는 버림. **교차로는 run 하나(통과 한 번)가 단위**
- 교차로 판정: CARLA `is_junction` 플래그가 아니라 OpenDRIVE 연결로 분류(87~161행). 무신호 직진이나 두 갈래 교차로는 일반 도로로 봄
- 확인 필요: 관측 시작과 끝에 걸린 첫 run과 끝 run은 run이 3개 이상인 차에서만 뺌(265~268, 279행). run이 1~2개인 차의 잘린 교차로 run은 남음
- 다섯 특징(`sig_of` 234~249행): 위치를 sEMA($\Delta$ = 0.5 s, 창 $\pm 3\Delta$)로 평활한 뒤 미분. 단위마다 $v$ 평균, $v$ 표준편차, $a$ 평균, $a$ 표준편차, 가속 부호 변화 수 / 이동거리 x 100. $|a| > 8$인 프레임과 15프레임 미만 단위는 버림
- 격자와 Hill 수(377~404행): 특징을 실측 층별 sd([`scale.yaml`](ch4_design/config/scale.yaml))로 나눠 폭 1 격자. 원점 20개를 균등하게 옮겨 평균. 판마다 단위 수를 최소값에 맞춰 희박화. $q=1$ Hill 수 $\exp(H)$. 배수는 맵마다 (조건 시드 평균 / plain 시드 평균), 대비는 맵 8개 차이의 t 구간
- 실측 전처리: [`realism.py`](ch4_design/diag/realism.py) 160~195행이 앞 레포 코드([`sil-agent-repertoire_measure.py`](ch4_design/real_preprocess/sil-agent-repertoire_measure.py))의 `real_frames`(2434~2448행)를 출처별로 부름. NGSIM 1736행~, TGSIM 1678행~, inD 2074행~, SinD 2326행~, highD 2382행~. 각 함수 docstring에 단위 환산, 솎음, 교차로 판정 방식이 있음(세부는 확인 안 함)
- recall(`realism.py` 283~309행): 층, 맵마다. 실측 단위 가운데 그 조건의 시뮬 단위가 밟은 칸에 떨어지는 비율. 조건 사이 단위 수를 맞추고 원점 10개 평균. 실측과 시뮬 모두 500단위 이상일 때만 계산
- precision(`exp4_eval.py` 241~266행): 판마다 시뮬 단위 가운데 실측이 밟은 칸에 드는 비율, 원점 10개 평균, 맵별 시드 평균. 희박화 없음

### 고속도로 제외

[설계] hw_link 층이 있는 맵이 3개뿐이라 맵 8개 짝 t 구간을 못 세워 판정하지 않고 기술만 함([`design.md`](ch4_design/docs/design/design.md) 173행, `measure.py` 50행 주석 "고속은 맵 3개라 검정 안 함"). 그 밖의 근거는 찾지 못함.

## 5장 결과

### RQ2 트리별 수치

[`rq2_trees.csv`](ch5_results/rq2_trees.csv)(이번에 합쳐 만든 표). 열: 행동 수, 분류 수, 동률, 가려짐, 모순, 정적으로 죽은 행동, 그 비율, 수정 회차, 거부 회차, 상한 도달, 남은 의미 오류. 합계: 행동 360, 분류 199(평균 9.95), 동률 11, 죽은 행동 51(가려짐 50, 모순 1), 수정 회차 평균 5.1. [`smt.md`](ch5_results/results/exp4/smt.md), [`e1.md`](ch5_results/results/exp4/e1.md)와 일치.

### 의도와 반대 방향 행동 3개

출처 [`e1_intent.csv`](ch5_results/results/exp4/e1_intent.csv). 세 개 모두 speed, effect faster인데 사전 판정이 감속.

| 트리 | 행동 | 조건 | priority, amount | intent |
|---|---|---|---|---|
| angry/tree_01 | aggressive_driving | LeaderPresent, SpeedBelow 40 | 5, [10, 20] | drive more aggressively when behind a slow leader |
| reckless/tree_02 | speed_up_when_leader_stopped | LeaderPresent, LeaderStopped | 3, [5, 10] | try to overtake when leader is stopped |
| reckless/tree_03 | speed_up_when_leader_stopped | LeaderPresent, LeaderStopped | 2, [5, 10] | try to overtake when leader is stopped |

분모는 두 값 모두 맞고 묶음이 다름([`e1.md`](ch5_results/results/exp4/e1.md) 37~38행). 분모는 사전이 방향을 판정한 행동만 셈(판정 불가는 뺌).

| 묶음 | 불일치/판정 |
|---|---|
| speed 행동 | 3/123 |
| 전체(keep_right 뺌) | 3/221 |

불일치 3개가 모두 speed라 두 비율의 분자가 같음. 발표의 "속도 행동 123개 중 3개"도 맞는 값.

판단(검증 안 함): 사전이 intent 안의 "slow", "stopped"를 감속 낱말로 읽은 것으로 보임. 사람이 읽으면 실제 모순이 아닐 수 있음. 판정 규칙은 사전 고정이라 수치는 그대로 두고 서술로 다루는 것이 맞음.

### RQ1 원자료

- 맵별 결과(8맵 x knob, ptree, pknob, plain 대비 배수): [`diversity.csv`](ch5_results/results/exp4/diversity/diversity.csv) `per_map` 열(맵 순서 Town01, 02, 03, 04, 05, 06, 07, 10HD). 판 단위 원자료 `diversity_runs.csv`, 대비 `tests.csv`, 강건성 `robustness.csv`, 요약 `report.md`
- 잡음 바닥: [`floor.md`](ch5_results/results/exp4/floor.md). knob/plain 배수의 시드 24~31 값 빼기 16~23 값. 교차로 +0.007 [-0.066, +0.080], 도심 블록 +0.067 [-0.004, +0.137]
- recall, precision: [`realism.md`](ch5_results/results/exp4/realism.md), `realism.csv`. 층별 조건 수준값과 대비 구간이 있음. **구간은 대비에만 있고 수준값 자체의 구간은 없음**

### 작동 불가 행동 수(77, 41, 51)

출처 [`static.md`](ch5_results/results/repair/static.md), [`static_rescue.csv`](ch5_results/results/repair/static_rescue.csv). 초안과 최종의 같은 행동을 짝지어 셈.

| 상태 | 행동 수 |
|---|---|
| 초안에서 죽고 최종에서 삶(수정이 살림) | 41 |
| 초안과 최종 모두 죽음 | 36 |
| 초안에서 살고 최종에서 죽음(수정이 바꾼 임계값이나 priority 때문에 새로 가려짐. 어느 쪽인지는 행동별로 확인 안 함) | 14 |

- 초안 77 = 41 + 36
- 최종 51 = 36 + 14 + 1. 남은 1개는 `exp4_reckless/tree_00`의 `speed_up_after_intersection`
- 그 1개의 사정: 초안 조건 `JunctionAhead [0, 10]`이 실행 정의역 밖이라, 초안을 CARLA로 돌리는 실행판에서 삭제됨. 그래서 초안 쪽 짝이 없고, 최종에서는 가려짐으로 셈
- 원 초안 360개로 세면 초안 죽음은 78(모순 2, 가려짐 76)

### 부가 분석

- 아래 분석은 모두 본 실험 최종 설정(시드 24~31, 조건 4개)의 수집물로 [`exp4_eval.py`](ch4_design/scripts/exp4_eval.py)와 `exp3_eval.py`가 만듦(2026-11-22)
  - 조작 확인 `manip.md`
  - 기제 `mech.md`
  - 위약 `placebo.md`
  - 실측 포괄 `realism.md`
  - 잡음 바닥 `floor.md`
  - 정적 결함 `smt.md`
  - E1~E3
- `vendi.md`는 결과를 본 뒤 추가한 탐색 분석(설계 변경 기록에 그렇게 적힘)
- 이 목록에 없는 기존 5장 분석은 새 설정으로 다시 돌리지 않았음. 원본 레포의 보조 실험 결과는 2026-11-10~11-17에 본 실험 설계(11-20) 전의 수집물(시드 0~7 등)로 만들었고, 본 실험 수집물(시드 24~31)을 참조하지 않음(원본 `results/` 폴더 확인)
- 단, 기존 제로샷 실행 분석과 별개로 **본 실험 트리를 쓴 추가 실험**이 있음. 본 실험 결과를 본 뒤 제안 파이프라인의 효과를 보려고 한 실험이고, 결과는 [`docs/results.md`](ch5_results/docs/results.md)의 "제안 파이프라인 효과(추가 실험)" 절에 있음
  - 생성 방식 비교(정적): 제안, 제로샷, 단일 호출(같은 지시를 한 번에), 각 20개. 같은 모델과 서버
  - 수정 루프의 CARLA 실행 효과: 본 실험 트리의 수정 전 초안을 같은 맵 8개, 시드 24~31로 64판 수집해 최종과 비교

### 그림

[`figs/`](ch5_results/figs/)의 PNG는 전부 [`exp4_figs.py`](ch5_results/figs/exp4_figs.py)가 결과 CSV만 읽어 그림.

| 그림 | 데이터 |
|---|---|
| `fig1_manip.png` | `manip_trees.csv` |
| `fig2_diversity.png` | `diversity/diversity.csv` |
| `fig3_contrasts.png` | `diversity/tests.csv`, `diversity.csv`, 잡음 바닥 띠 |
| `fig4_realism.png` | `realism.csv` |
| `fig5_realization.png` | `e2_realization.csv`, `placebo_realization.csv` |
| `fig7_scale.png` | `vendi.csv` |

`deck/`의 세 장은 원본 레포 `deck_figs.py`로 그린 발표용. 데이터는 `realism.csv`, `diversity/tests.csv`이고, `fig_exp4_design.png`는 값이 임의인 개념도.
