# 실제 LLM 출력 예시: exp4_anxious/tree_04

수정 2회 뒤 의미 오류 0으로 배치된 트리. 원 기록은 같은 폴더 json, 컴파일 결과는 xml.

## 분류 호출 원문 응답

```text
1. SpeedAdaptation - Adjusting speed in response to various traffic conditions, such as speed limits, road curvature, or leader vehicles.
2. FollowingDistance - Maintaining a safe or varying distance to the leader vehicle, depending on the situation.
3. LaneManagement - Deciding when to change lanes or stay in the current lane, based on factors like lane count, road curvature, or time spent in the lane.
4. TrafficRuleCompliance - Obeying or disregarding traffic rules, such as traffic lights, stop signs, or yield signs, with varying levels of compliance.
5. CollisionAvoidance - Reacting to potential collisions with other vehicles, pedestrians, or objects, by adjusting speed, lane position, or ignoring obstacles.
6. JunctionNavigation - Behaving differently when approaching or navigating through intersections, taking into account traffic lights, road layout, and other vehicles.
7. LanePositioning - Choosing a lane position based on factors like road curvature, lane count, or time spent in the lane, and adjusting speed accordingly.
8. LeaderVehicleResponse - Reacting to the actions of the leader vehicle, such as slowing down or speeding up, and adjusting following distance or speed accordingly.
9. RoadTypeAdaptation - Adjusting behavior based on the type of road or speed limit, such as urban, rural, or highway environments.
10. StressAndErrorProneBehaviour - Exhibiting behaviors that reflect the driver's tense and uneasy state, such as erratic speed changes, lane changes, or rule compliance.
```

## 구체화 호출 원문 응답

```json
[
  {"id": "speed_adapt_to_limit", "dimension": "SpeedAdaptation", 
   "when": [{"cond": "SpeedLimitBelow", "kmh": 60}], 
   "set": "speed", "effect": "slower", "amount": [10, 20], "priority": 1, 
   "intent": "reduce speed when the limit is low"},
  {"id": "speed_up_on_high_limit", "dimension": "SpeedAdaptation", 
   "when": [{"cond": "SpeedLimitAbove", "kmh": 60}], 
   "set": "speed", "effect": "faster", "amount": [5, 15], "priority": 2, 
   "intent": "increase speed when the limit is high"},
  {"id": "slow_down_on_curves", "dimension": "SpeedAdaptation", 
   "when": [{"cond": "CurvatureAbove", "invmeters": [0.1, 0.2]}], 
   "set": "speed", "effect": "slower", "amount": [5, 10], "priority": 3, 
   "intent": "reduce speed on sharp curves"},
  {"id": "maintain_distance", "dimension": "FollowingDistance", 
   "when": [{"cond": "LeaderPresent"}, {"cond": "GapBelow", "meters": [10, 20]}], 
   "set": "gap", "effect": "wider", "amount": 1, "priority": 1, 
   "intent": "increase following distance when too close"},
  {"id": "close_gap", "dimension": "FollowingDistance", 
   "when": [{"cond": "LeaderPresent"}, {"cond": "GapAbove", "meters": [20, 30]}], 
   "set": "gap", "effect": "narrower", "amount": 0.5, "priority": 2, 
   "intent": "decrease following distance when too far"},
  {"id": "stay_in_lane", "dimension": "LaneManagement", 
   "when": [{"cond": "IsRightmostLane"}, {"cond": "IsLeftmostLane"}], 
   "set": "lane_change", "effect": "forbid", "priority": 1, 
   "intent": "avoid lane changes on the sides"},
  {"id": "change_lanes_on_multilane", "dimension": "LaneManagement", 
   "when": [{"cond": "LaneCountAbove", "lanes": 2}], 
   "set": "lane_change", "effect": "allow", "priority": 2, 
   "intent": "allow lane changes on multilane roads"},
  {"id": "comply_with_traffic_lights", "dimension": "TrafficRuleCompliance", 
   "when": [{"cond": "TrafficLightRed"}], 
   "set": "ign_lights", "effect": "more", "amount": 0, "priority": 1, 
   "intent": "always comply with red traffic lights"},
  {"id": "disregard_traffic_lights_sometimes", "dimension": "TrafficRuleCompliance", 
   "when": [{"cond": "TrafficLightGreen"}], 
   "set": "ign_lights", "effect": "more", "amount": [2, 5], "priority": 2, 
   "intent": "occasionally disregard traffic lights"},
  {"id": "avoid_collisions", "dimension": "CollisionAvoidance", 
   "when": [{"cond": "LeaderPresent"}, {"cond": "ClosingFaster", "mps": [2, 5]}], 
   "set": "speed", "effect": "slower", "amount": [10, 20], "priority": 1, 
   "intent": "reduce speed to avoid collisions"},
  {"id": "navigate_junctions_cautiously", "dimension": "JunctionNavigation", 
   "when": [{"cond": "JunctionAhead", "meters": [20, 40]}], 
   "set": "speed", "effect": "slower", "amount": [10, 20], "priority": 1, 
   "intent": "reduce speed when approaching junctions"},
  {"id": "position_in_lane_center", "dimension": "LanePositioning", 
   "when": [{"cond": "IsRightmostLane"}, {"cond": "IsLeftmostLane"}], 
   "set": "speed", "effect": "slower", "amount": [5, 10], "priority": 2, 
   "intent": "reduce speed when on the sides"},
  {"id": "react_to_leader", "dimension": "LeaderVehicleResponse", 
   "when": [{"cond": "LeaderPresent"}, {"cond": "LeaderStopped"}], 
   "set": "speed", "effect": "slower", "amount": [10, 20], "priority": 1, 
   "intent": "reduce speed when the leader stops"},
  {"id": "adapt_to_road_type", "dimension": "RoadTypeAdaptation", 
   "when": [{"cond": "SpeedLimitBelow", "kmh": 60}], 
   "set": "speed", "effect": "slower", "amount": [10, 20], "priority": 1, 
   "intent": "reduce speed on urban roads"},
  {"id": "exhibit_stress_behaviour", "dimension": "StressAndErrorProneBehaviour", 
   "when": [{"cond": "TimeInLaneAbove", "seconds": [30, 60]}], 
   "set": "speed", "effect": "faster", "amount": [5, 10], "priority": 2, 
   "intent": "increase speed when feeling stressed"},
  {"id": "make_errors", "dimension": "StressAndErrorProneBehaviour", 
   "when": [{"cond": "LeaderPresent"}, {"cond": "GapBelow", "meters": [10, 20]}], 
   "set": "ign_vehicles", "effect": "more", "amount": [2, 5], "priority": 3, 
   "intent": "occasionally make errors when following"}
]
```

## 수정 회차 기록

- 회차 0: errors, 행동 16, 구조 오류 0, 의미 오류 4
  - `Behaviour 'adapt_to_road_type' never takes effect: whenever its conditions hold, 'speed_adapt_to_limit', 'avoid_collisions', 'navigate_junctions_cautiously', 'react_to_leader' for the same setting applies first (smaller priority number, or same priority and more specific, or listed earlier).`
  - `Behaviours 'speed_up_on_high_limit' and 'position_in_lane_center' both set speed with priority 2 and can hold at the same time, but neither is a special case of the other, so only their order in the list decides which applies. Give them different priorities.`
  - `Behaviours 'speed_up_on_high_limit' and 'exhibit_stress_behaviour' both set speed with priority 2 and can hold at the same time, but neither is a special case of the other, so only their order in the list decides which applies. Give them different priorities.`
- 회차 1: errors, 행동 16, 구조 오류 0, 의미 오류 2
  - `Behaviour 'adapt_to_road_type' never takes effect: whenever its conditions hold, 'speed_adapt_to_limit', 'avoid_collisions', 'navigate_junctions_cautiously', 'react_to_leader' for the same setting applies first (smaller priority number, or same priority and more specific, or listed earlier).`
  - `Behaviours 'speed_up_on_high_limit' and 'slow_down_on_curves' both set speed with priority 3 and can hold at the same time, but neither is a special case of the other, so only their order in the list decides which applies. Give them different priorities.`
- 회차 2: ok, 행동 16, 구조 오류 0, 의미 오류 0
