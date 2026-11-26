# 관련 연구 서지와 위치

2026-11-23 정리. 서지는 Crossref, OpenAlex, Semantic Scholar, arXiv 조회로 확인했다. **학회 미확인** 표시는 조회에서
arXiv판만 나와 학회 게재를 확인하지 못한 것이다. 기존 [`deck_facts.md`](../results/slides/deck_facts.md#관련-연구)의
KCC 논문 표를 대체한다.

## 위치 정하는 축

각 계열을 **무엇을 만들고 무엇으로 평가하나**로 놓는다. 본 연구의 차이는 적용 도메인이 아니라 문제 설정과 평가에 둔다.

| 계열 | 만드는 것 | 평가 기준 | 본 연구와 갈리는 지점 |
|---|---|---|---|
| 규칙 기반 교통과 파라미터 무작위화 | 차량마다 다른 상수 파라미터. 같은 파라미터로 상황에 반응 | 실무 기본값 | 파라미터 자체가 상황에 따라 바뀌지 않음. 본 실험의 **추첨** 조건이 이 계열이라 직접 비교함 |
| 데이터 기반 교통 생성 | 실측 궤적을 학습한 정책이나 생성 모델 | 실측 궤적 재현도 | 생성에 실측 학습이 필요하고 행동이 모델 안에 있음. 본 연구는 생성에 실측을 안 쓰고, 기존 제어기 위에 읽을 수 있는 트리로 얹힘 |
| LLM 기반 시나리오 생성 | 문장으로 지정한 시나리오(배치, 궤적, 시뮬레이터 설정) | 지정 내용 재현, 위험 시나리오 발견 | 시나리오 단위 생성. 본 연구는 배경 교통 전체의 행동 분포를 넓힘 |
| 행동 트리 기반 주행 시나리오 | 시험 시나리오 속 행위자 행동을 트리로 기술하거나 생성 | 안전 위반 발견, 행동 포괄 | 대상 차량 시험용 위험 시나리오가 목표. 본 연구는 다양성과 실측 범위를 함께 판정하고, 상황 의존 몫을 분리함 |
| LLM 기반 행동 트리 생성(로봇) | 작업 하나를 수행하는 트리 | 작업 성공 | 작업 성공이 기준. 본 연구는 의도와 값의 일치, 교통 집단의 다양성을 평가함 |

**가장 가까운 연구**는 둘이다.

- Li et al.(2024, ScenLaBe): LLM과 행동 트리로 주행 시험 시나리오를 만든다. 사고 기술문에서 위험 시나리오를 재현하는 것이
  목표라 배경 교통의 다양성과는 문제가 다르다
- Queiroz et al.(2024, SDV): 차량 행동을 행동 트리로 표현하고 운전 스타일 여러 개를 두며, 움직임을 자연 주행 교통과 비교한다.
  과제 연구계획서가 인용한 연구라 지도교수가 안다. 트리는 시험자가 설계한다. 본 연구는 설명에서 트리를 생성하고, 교통
  전체의 다양성과 상황 의존 몫을 잰다

질문이 나오면 이 두 연구와의 차이부터 답한다.

## 서지

### 규칙 기반 교통과 시뮬레이터

| 연구 | 서지 | DOI 또는 arXiv |
|---|---|---|
| IDM | M. Treiber, A. Hennecke, D. Helbing, Congested traffic states in empirical observations and microscopic simulations, Physical Review E 62(2), 1805-1824, 2000 | 10.1103/PhysRevE.62.1805 |
| SUMO | P. A. Lopez et al., Microscopic Traffic Simulation using SUMO, IEEE ITSC 2018 | 10.1109/ITSC.2018.8569938 |
| CARLA | A. Dosovitskiy et al., CARLA: An Open Urban Driving Simulator, CoRL 2017(**학회 미확인**) | arXiv 1711.03938 |

### 데이터 기반 교통 생성

| 연구 | 서지 | DOI 또는 arXiv |
|---|---|---|
| TrafficSim | S. Suo, S. Regalado, S. Casas, R. Urtasun, CVPR 2021 | 10.1109/CVPR46437.2021.01026 |
| TrafficGen | L. Feng, Q. Li, Z. Peng, S. Tan, B. Zhou, IEEE ICRA 2023, 3567-3575 | 10.1109/ICRA48891.2023.10160296 |
| BITS | D. Xu, Y. Chen, B. Ivanovic, M. Pavone, IEEE ICRA 2023, 2929-2936 | 10.1109/ICRA48891.2023.10161167 |
| CTG | Z. Zhong et al., Guided Conditional Diffusion for Controllable Traffic Simulation, IEEE ICRA 2023 | 10.1109/ICRA48891.2023.10161463 |
| TrafficBots | Z. Zhang, A. Liniger, D. Dai, F. Yu, L. Van Gool, IEEE ICRA 2023. 차량마다 시간 불변 성향 잠재변수를 둠 | 10.1109/ICRA48891.2023.10161243 |

### LLM 기반 시나리오 생성

| 연구 | 서지 | DOI 또는 arXiv |
|---|---|---|
| LCTGen | S. Tan, B. Ivanovic, X. Weng, M. Pavone, P. Krähenbühl, Language Conditioned Traffic Generation, CoRL 2023(**학회 미확인**) | arXiv 2307.07947 |
| CTG++ | Z. Zhong et al., Language-Guided Traffic Simulation via Scene-Level Diffusion, CoRL 2023(**학회 미확인**) | arXiv 2306.06344 |
| ChatScene | J. Zhang, C. Xu, B. Li, IEEE/CVF CVPR 2024, 15459- | 10.1109/CVPR52733.2024.01464 |
| InteractTraj | J. Xia, C. Xu, Q. Xu, Y. Wang, S. Chen, Language-Driven Interactive Traffic Trajectory Generation, NeurIPS 2024 | 10.52202/079017-2474 |
| ChatSUMO | S. Li, T. Azfar, R. Ke, IEEE Transactions on Intelligent Vehicles, 2024 | 10.1109/TIV.2024.3508471 |

### 행동 트리 기반 주행 시나리오

| 연구 | 서지 | DOI 또는 arXiv |
|---|---|---|
| BTScenario | S. Kang et al., Behavior-Tree Based Scenario Specification and Test Case Generation for Autonomous Driving Simulation, ICITES 2022. py_trees로 행위자 행동 기술 | 10.1109/ICITES56274.2022.9943753 |
| Matrix-Fuzzer | P. Zhang et al., Realistic Safety-critical Scenarios Search for Autonomous Driving System via Behavior Tree, 2023(**학회 미확인**). 실측 궤적을 행동 순서로 추상화하고 실측 분포에서 속성을 바꿈 | arXiv 2305.06603 |
| SDV | R. Queiroz et al., A Driver-Vehicle Model for ADS Scenario-Based Testing, IEEE Transactions on Intelligent Transportation Systems, 2024. 시험자가 설계한 행동 트리로 차량 행동과 운전 스타일을 표현하고, 자연 주행 교통과 움직임 현실성을 비교. 과제 연구계획서 참고문헌 [9] | 10.1109/TITS.2024.3373531 |
| ScenLaBe | Y. Li, Z. Hou, H. Liu, Large Language Model and Behaviour Tree Based Real-World Test Scenario Generation for Autonomous Vehicles, IEEE TrustCom 2024. NHTSA CDS 사고 기술문에서 위험 시나리오 생성 | 10.1109/TrustCom63139.2024.00243 |

### LLM 기반 행동 트리 생성(로봇)

| 연구 | 서지 | DOI 또는 arXiv |
|---|---|---|
| LLM-BRAIn | A. Lykov, D. Tsetserukou, IEEE FLLM 2024 | 10.1109/FLLM63129.2024.10852491 |
| BTGenBot | R. A. Izzo, G. Bardaro, M. Matteucci, IEEE/RSJ IROS 2024 | 10.1109/IROS58592.2024.10802304 |
| OBTEA | X. Chen et al., Integrating Intent Understanding and Optimal Behavior Planning for Behavior Tree Generation from Human Instructions, IJCAI 2024 | 10.24963/ijcai.2024/755 |
| BETR-XP-LLM | J. Styrud et al., Automatic Behavior Tree Expansion with LLMs for Robotic Manipulation, IEEE ICRA 2025. 과제 연구계획서 참고문헌 [8] | 10.1109/ICRA55743.2025.11127942 |
| Code-BT | S. Zhang et al., Code-BT: A Code-Driven Approach to Behavior Tree Generation for Robot Tasks Planning with Large Language Models, IJCAI 2025 | 10.24963/ijcai.2025/980 |

기존 표의 **Code-BT (Jiang et al.)는 오기**다. 1저자는 Siyang Zhang이다.

### 평가 지표

| 연구 | 서지 | DOI 또는 arXiv |
|---|---|---|
| Vendi score | D. Friedman, A. B. Dieng, The Vendi Score: A Diversity Evaluation Metric for Machine Learning, TMLR 2023 | arXiv 2210.02410 |
| Waymo Sim Agents | N. Montali et al., The Waymo Open Sim Agents Challenge, 2023(**학회 미확인**). 데이터 기반 교통 생성의 실측 재현 평가 기준 | arXiv 2305.12032 |
