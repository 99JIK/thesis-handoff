# 실험 2 다양성과 부작용

맵 단위. 배수는 기준(plain) 대비, 평균과 95% t 구간. 생성: `scripts/measure.py score`

## 행동 다양성 (힐 수 q1)

| 도로 유형 | 조건 | 맵 | 평균 | 95% CI | 맵별 |
|---|---|---|---|---|---|
| junction | plain | 8 | 1.00 | [1.00, 1.00] | 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 |
| junction | knob | 8 | 1.54 | [1.35, 1.74] | 1.42 1.22 1.54 1.73 1.68 1.56 1.27 1.91 |
| junction | ptree | 8 | 1.61 | [1.41, 1.82] | 1.54 1.35 1.55 1.94 1.85 1.50 1.31 1.86 |
| junction | pknob | 8 | 1.49 | [1.29, 1.70] | 1.36 1.15 1.53 1.69 1.66 1.53 1.21 1.84 |
| urb_block | plain | 8 | 1.00 | [1.00, 1.00] | 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 |
| urb_block | knob | 8 | 1.52 | [1.27, 1.78] | 1.34 1.10 1.65 1.80 1.83 1.20 1.39 1.88 |
| urb_block | ptree | 8 | 1.75 | [1.36, 2.14] | 1.47 1.12 1.91 2.24 2.30 1.22 1.59 2.14 |
| urb_block | pknob | 8 | 1.53 | [1.24, 1.81] | 1.37 1.05 1.66 1.93 1.78 1.17 1.33 1.90 |
| hw_link | plain | 3 | 1.00 | [1.00, 1.00] | 1.00 1.00 1.00 |
| hw_link | knob | 3 | 1.29 | [1.02, 1.56] | 1.41 1.21 1.25 |
| hw_link | ptree | 3 | 1.37 | [0.91, 1.83] | 1.58 1.30 1.22 |
| hw_link | pknob | 3 | 1.29 | [1.07, 1.51] | 1.39 1.22 1.26 |

## 대비

| 지표 | 대비 | 도로 유형 | 맵 | 차이 | 95% CI | 앞쪽 우세 맵 | p | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| diversity_q1 | ptree-knob | junction | 8 | 0.073 | [-0.011, 0.156] | 6/8 | 0.1484 | 0.1484 |
| diversity_q1 | ptree-knob | urb_block | 8 | 0.224 | [0.082, 0.365] | 8/8 | 0.0078 | 0.0156 |
| diversity_q1 | ptree-pknob | junction | 8 | 0.119 | [0.031, 0.207] | 7/8 | 0.0391 | 0.0391 |
| diversity_q1 | ptree-pknob | urb_block | 8 | 0.223 | [0.093, 0.353] | 8/8 | 0.0078 | 0.0156 |
| diversity_q1 | pknob-knob | junction | 8 | -0.046 | [-0.065, -0.027] | 0/8 | 0.0078 | 0.0156 |
| diversity_q1 | pknob-knob | urb_block | 8 | 0.001 | [-0.052, 0.054] | 4/8 | 0.7422 | 0.7422 |
| diversity_q1 | knob-plain | junction | 8 | 0.541 | [0.345, 0.737] | 8/8 | 0.0078 | 0.0156 |
| diversity_q1 | knob-plain | urb_block | 8 | 0.525 | [0.271, 0.779] | 8/8 | 0.0078 | 0.0156 |
| q1_w0.5 | ptree-knob | junction | 8 | 0.214 | [0.127, 0.301] | 8/8 | 0.0078 | 0.0156 |
| q1_w0.5 | ptree-knob | urb_block | 8 | 0.571 | [0.213, 0.930] | 8/8 | 0.0078 | 0.0156 |
| q1_w0.5 | ptree-pknob | junction | 8 | 0.272 | [0.173, 0.370] | 8/8 | 0.0078 | 0.0156 |
| q1_w0.5 | ptree-pknob | urb_block | 8 | 0.564 | [0.224, 0.904] | 8/8 | 0.0078 | 0.0156 |
| q1_w0.5 | pknob-knob | junction | 8 | -0.058 | [-0.079, -0.036] | 0/8 | 0.0078 | 0.0156 |
| q1_w0.5 | pknob-knob | urb_block | 8 | 0.007 | [-0.058, 0.073] | 3/8 | 0.9453 | 0.9453 |
| q1_w0.5 | knob-plain | junction | 8 | 0.726 | [0.505, 0.947] | 8/8 | 0.0078 | 0.0156 |
| q1_w0.5 | knob-plain | urb_block | 8 | 0.784 | [0.380, 1.187] | 8/8 | 0.0078 | 0.0156 |
| q1_w2.0 | ptree-knob | junction | 8 | 0.013 | [-0.044, 0.070] | 5/8 | 0.7422 | 0.7422 |
| q1_w2.0 | ptree-knob | urb_block | 8 | 0.084 | [0.016, 0.153] | 6/8 | 0.0391 | 0.0781 |
| q1_w2.0 | ptree-pknob | junction | 8 | 0.036 | [-0.028, 0.100] | 5/8 | 0.1953 | 0.1953 |
| q1_w2.0 | ptree-pknob | urb_block | 8 | 0.087 | [0.035, 0.139] | 8/8 | 0.0078 | 0.0156 |
| q1_w2.0 | pknob-knob | junction | 8 | -0.023 | [-0.037, -0.008] | 0/8 | 0.0078 | 0.0156 |
| q1_w2.0 | pknob-knob | urb_block | 8 | -0.003 | [-0.039, 0.034] | 2/8 | 0.7422 | 0.7422 |
| q1_w2.0 | knob-plain | junction | 8 | 0.322 | [0.206, 0.438] | 8/8 | 0.0078 | 0.0156 |
| q1_w2.0 | knob-plain | urb_block | 8 | 0.351 | [0.173, 0.530] | 8/8 | 0.0078 | 0.0156 |
| q0 | ptree-knob | junction | 8 | 0.000 | [-0.038, 0.039] | 3/8 | 0.9453 | 0.9453 |
| q0 | ptree-knob | urb_block | 8 | 0.021 | [-0.013, 0.055] | 7/8 | 0.1094 | 0.2188 |
| q0 | ptree-pknob | junction | 8 | 0.025 | [-0.022, 0.073] | 5/8 | 0.3125 | 0.3125 |
| q0 | ptree-pknob | urb_block | 8 | 0.028 | [0.009, 0.047] | 7/8 | 0.0234 | 0.0469 |
| q0 | pknob-knob | junction | 8 | -0.025 | [-0.041, -0.010] | 1/8 | 0.0156 | 0.0312 |
| q0 | pknob-knob | urb_block | 8 | -0.007 | [-0.040, 0.026] | 2/8 | 0.3125 | 0.3125 |
| q0 | knob-plain | junction | 8 | 0.264 | [0.131, 0.397] | 8/8 | 0.0078 | 0.0156 |
| q0 | knob-plain | urb_block | 8 | 0.274 | [0.094, 0.453] | 8/8 | 0.0078 | 0.0156 |
| q2 | ptree-knob | junction | 8 | 0.142 | [0.036, 0.248] | 7/8 | 0.0391 | 0.0391 |
| q2 | ptree-knob | urb_block | 8 | 0.416 | [0.222, 0.610] | 8/8 | 0.0078 | 0.0156 |
| q2 | ptree-pknob | junction | 8 | 0.189 | [0.088, 0.289] | 7/8 | 0.0156 | 0.0156 |
| q2 | ptree-pknob | urb_block | 8 | 0.404 | [0.236, 0.572] | 8/8 | 0.0078 | 0.0156 |
| q2 | pknob-knob | junction | 8 | -0.047 | [-0.070, -0.024] | 0/8 | 0.0078 | 0.0156 |
| q2 | pknob-knob | urb_block | 8 | 0.012 | [-0.057, 0.081] | 4/8 | 0.8438 | 0.8438 |
| q2 | knob-plain | junction | 8 | 0.601 | [0.411, 0.791] | 8/8 | 0.0078 | 0.0156 |
| q2 | knob-plain | urb_block | 8 | 0.573 | [0.323, 0.823] | 8/8 | 0.0078 | 0.0156 |

q1_w*, q0, q2 줄은 강건성 확인용. 본 결과는 diversity_q1 줄. offset, offkr 대비는 사후 추가라 탐색.

## 부작용

| 지표 | 도로 유형 | 조건 | 맵 | 평균 | 95% CI |
|---|---|---|---|---|---|
| stop_ratio | junction | plain | 8 | 0.2190 | [0.1279, 0.3101] |
| stop_ratio | junction | knob | 8 | 0.2424 | [0.1704, 0.3143] |
| stop_ratio | junction | ptree | 8 | 0.2306 | [0.1485, 0.3126] |
| stop_ratio | junction | pknob | 8 | 0.2522 | [0.1813, 0.3231] |
| stop_ratio | urb_block | plain | 8 | 0.6328 | [0.5713, 0.6943] |
| stop_ratio | urb_block | knob | 8 | 0.6255 | [0.5581, 0.6930] |
| stop_ratio | urb_block | ptree | 8 | 0.6228 | [0.5570, 0.6886] |
| stop_ratio | urb_block | pknob | 8 | 0.6193 | [0.5582, 0.6805] |
| stop_ratio | hw_link | plain | 3 | 0.1678 | [-0.1069, 0.4426] |
| stop_ratio | hw_link | knob | 3 | 0.1614 | [-0.1059, 0.4288] |
| stop_ratio | hw_link | ptree | 3 | 0.1665 | [-0.1111, 0.4441] |
| stop_ratio | hw_link | pknob | 3 | 0.1566 | [-0.1035, 0.4167] |
| space_mean_speed_ratio | junction | plain | 8 | 1.0000 | [1.0000, 1.0000] |
| space_mean_speed_ratio | junction | knob | 8 | 0.9323 | [0.8879, 0.9767] |
| space_mean_speed_ratio | junction | ptree | 8 | 0.9102 | [0.8753, 0.9450] |
| space_mean_speed_ratio | junction | pknob | 8 | 0.9004 | [0.8621, 0.9387] |
| space_mean_speed_ratio | urb_block | plain | 8 | 1.0000 | [1.0000, 1.0000] |
| space_mean_speed_ratio | urb_block | knob | 8 | 0.9905 | [0.9657, 1.0153] |
| space_mean_speed_ratio | urb_block | ptree | 8 | 0.9755 | [0.9563, 0.9947] |
| space_mean_speed_ratio | urb_block | pknob | 8 | 0.9825 | [0.9586, 1.0063] |
| space_mean_speed_ratio | hw_link | plain | 3 | 1.0000 | [1.0000, 1.0000] |
| space_mean_speed_ratio | hw_link | knob | 3 | 0.9610 | [0.8862, 1.0357] |
| space_mean_speed_ratio | hw_link | ptree | 3 | 0.9650 | [0.8883, 1.0418] |
| space_mean_speed_ratio | hw_link | pknob | 3 | 0.9622 | [0.8983, 1.0260] |
| collisions_per_1000km | all | plain | 8 | 81.9520 | [-34.2301, 198.1342] |
| collisions_per_1000km | all | knob | 8 | 75.0980 | [-23.5788, 173.7748] |
| collisions_per_1000km | all | ptree | 8 | 72.1180 | [-19.2900, 163.5260] |
| collisions_per_1000km | all | pknob | 8 | 71.2104 | [-24.5840, 167.0048] |
| collisions_per_1000km_win1s | all | plain | 8 | 95.3335 | [-42.6969, 233.3639] |
| collisions_per_1000km_win1s | all | knob | 8 | 81.6704 | [-25.0613, 188.4020] |
| collisions_per_1000km_win1s | all | ptree | 8 | 79.1401 | [-20.1031, 178.3832] |
| collisions_per_1000km_win1s | all | pknob | 8 | 76.1017 | [-24.5411, 176.7446] |
| vehicle_collisions_per_1000km | all | plain | 8 | 31.3936 | [-9.7970, 72.5843] |
| vehicle_collisions_per_1000km | all | knob | 8 | 26.7098 | [-1.5962, 55.0158] |
| vehicle_collisions_per_1000km | all | ptree | 8 | 28.2487 | [-0.9984, 57.4957] |
| vehicle_collisions_per_1000km | all | pknob | 8 | 30.2908 | [-9.1536, 69.7351] |

### 맵별 collisions_per_1000km

| 맵 | plain | knob | ptree | pknob |
|---|---|---|---|---|
| Town01 | 8.5 | 26.9 | 32.9 | 11.8 |
| Town02 | 365.3 | 225.6 | 273.4 | 271.6 |
| Town03 | 13.8 | 12.2 | 17.8 | 15.8 |
| Town04 | 7.0 | 6.1 | 6.4 | 7.0 |
| Town05 | 4.5 | 6.9 | 4.7 | 2.7 |
| Town06 | 16.9 | 13.0 | 14.7 | 17.0 |
| Town07 | 234.2 | 300.9 | 221.0 | 240.7 |
| Town10HD | 5.5 | 9.1 | 6.1 | 3.1 |

### 맵별 collisions_per_1000km_win1s

| 맵 | plain | knob | ptree | pknob |
|---|---|---|---|---|
| Town01 | 8.5 | 29.4 | 37.8 | 11.8 |
| Town02 | 441.2 | 241.0 | 294.5 | 283.6 |
| Town03 | 14.9 | 19.5 | 24.0 | 24.1 |
| Town04 | 7.3 | 6.1 | 6.4 | 7.0 |
| Town05 | 4.5 | 6.9 | 4.7 | 2.7 |
| Town06 | 20.0 | 13.0 | 15.3 | 17.6 |
| Town07 | 260.9 | 328.3 | 244.3 | 257.3 |
| Town10HD | 5.5 | 9.1 | 6.1 | 4.6 |

### 맵별 vehicle_collisions_per_1000km

| 맵 | plain | knob | ptree | pknob |
|---|---|---|---|---|
| Town01 | 7.2 | 18.5 | 24.4 | 9.1 |
| Town02 | 143.2 | 65.5 | 96.0 | 134.4 |
| Town03 | 12.7 | 10.2 | 11.5 | 11.4 |
| Town04 | 5.8 | 3.9 | 4.9 | 4.5 |
| Town05 | 0.9 | 3.9 | 2.8 | 0.9 |
| Town06 | 13.3 | 8.7 | 10.8 | 12.0 |
| Town07 | 62.5 | 93.8 | 69.4 | 67.0 |
| Town10HD | 5.5 | 9.1 | 6.1 | 3.1 |
