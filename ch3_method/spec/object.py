"""무엇을 위한 트리인지. 도메인이 바뀌면 이 파일만 바뀐다.

왜 여기 있나: 도메인 정의가 bt_generate.py에 상수로 박혀 있었다. 그러면 파이프라인이 CARLA
배경차 전용이 되고, 원 논문(KCC2026)이 도메인을 갈아끼우며 돌리던 구조에서 멀어진다.
상위 과제가 엘리베이터·드론·AirSim까지 있으니 파이프라인은 환경 객체 일반이어야 하고
CARLA 배경차는 그 한 인스턴스여야 한다.

여기에 안 넣는 것: 노드 카탈로그(scripts/bt_nodes.py), 파라미터 사실(tm_api.py), 실행
모델(runtime.py), 도로 사실(environment.py). 각자 자기 파일이 있다.

누설 경계: 도메인 서술에 채점 층을 암시하는 도로 유형 나열을 넣지 않는다. 예전 DOMAIN이
"urban arterials with signalised intersections and multi-lane highway links"였는데 그게
채점 층 셋과 일대일이었다.
"""

DOMAIN = "background traffic in a driving simulator (CARLA), on a public road network"

SUT = ("Automated driving software under test in a Software-In-The-Loop setup. "
       "The background vehicles are NOT the system under test. They are the "
       "surrounding environment the system under test drives among.")

NAME = "background_vehicle"

# 트리가 무엇을 정하고 무엇을 안 정하는지가 핵심이다. 저수준 제어는 시뮬레이터 몫이라
# 트리가 조향이나 경로를 다시 짜려 들면 안 된다
DESC = ("A single ordinary vehicle in the background traffic stream. It follows the "
        "road network, obeys traffic control, and interacts with the vehicles around "
        "it. Its low-level control (steering, lane keeping, route following) is "
        "handled by the simulator. The behaviour tree only sets the parameters listed "
        "in the node catalogue.")

# 프롬프트 문장에 들어가는 도메인 명사. 템플릿이 "the vehicle"처럼 직접 쓰면 도메인에
# 묶이므로 여기서 받아 쓴다
NOUN = "vehicle"                 # 이 객체를 부르는 말
ENVIRONMENT = "road network"     # 객체가 놓인 곳
PEERS = "the other vehicles"     # 같은 환경의 동류들


if __name__ == "__main__":
    print("DOMAIN %s\nNAME   %s\nNOUN   %s / %s / %s"
          % (DOMAIN, NAME, NOUN, ENVIRONMENT, PEERS))
    print("DESC   %s" % DESC)
