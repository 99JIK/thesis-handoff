# 실험 4 실측 행동 포괄

생성: `scripts/exp4_eval.py realism`. 맵 단위(%), 대비는 맵 8개 95% t 구간. 보정 없음.
recall은 `realism.coverage`, precision은 `slides_figs.precision`과 같은 규칙.

## junction

| 조건 | recall | precision |
|---|---|---|
| plain | 32.3 | 98.0 |
| knob | 40.3 | 97.4 |
| ptree | 40.0 | 97.9 |
| pknob | 39.3 | 97.2 |

| 대비 | 지표 | 차이(%p) | 95% 구간 | 앞쪽 우세 맵 | 판정 |
|---|---|---|---|---|---|
| ptree-knob | recall | -0.27 | [-1.88, +1.35] | 1/8 | 판정 안 함 |
| ptree-knob | precision | +0.45 | [+0.00, +0.90] | 7/8 | 증가 |
| ptree-pknob | recall | +0.67 | [-0.93, +2.27] | 4/8 | 판정 안 함 |
| ptree-pknob | precision | +0.67 | [+0.13, +1.21] | 7/8 | 증가 |
| pknob-knob | recall | -0.94 | [-1.69, -0.19] | 1/8 | 감소 |
| pknob-knob | precision | -0.22 | [-0.42, -0.02] | 2/8 | 감소 |
| knob-plain | recall | +7.97 | [+6.12, +9.82] | 8/8 | 증가 |
| knob-plain | precision | -0.60 | [-0.92, -0.28] | 1/8 | 감소 |

## urb_block

| 조건 | recall | precision |
|---|---|---|
| plain | 44.6 | 95.3 |
| knob | 55.7 | 95.1 |
| ptree | 56.4 | 96.0 |
| pknob | 55.3 | 94.8 |

| 대비 | 지표 | 차이(%p) | 95% 구간 | 앞쪽 우세 맵 | 판정 |
|---|---|---|---|---|---|
| ptree-knob | recall | +0.64 | [-0.50, +1.78] | 5/8 | 판정 안 함 |
| ptree-knob | precision | +0.84 | [+0.37, +1.31] | 7/8 | 증가 |
| ptree-pknob | recall | +1.08 | [-0.33, +2.50] | 6/8 | 판정 안 함 |
| ptree-pknob | precision | +1.18 | [+0.71, +1.64] | 8/8 | 증가 |
| pknob-knob | recall | -0.44 | [-1.30, +0.41] | 4/8 | 판정 안 함 |
| pknob-knob | precision | -0.34 | [-0.65, -0.02] | 1/8 | 감소 |
| knob-plain | recall | +11.08 | [+5.36, +16.80] | 8/8 | 증가 |
| knob-plain | precision | -0.14 | [-0.70, +0.41] | 4/8 | 판정 안 함 |

## 묶어 읽기

| 대비 | 층 | q1 | recall | precision | 서술 |
|---|---|---|---|---|---|
| ptree-knob | junction | 판정 안 함 | 판정 안 함 | 감소 아님 | 다양성 효과 없음, 또는 깎임 |
| ptree-knob | urb_block | 증가 | 판정 안 함 | 감소 아님 | 다양해졌으나 실측 포괄은 안 늘어남 |
| ptree-pknob | junction | 증가 | 판정 안 함 | 감소 아님 | 다양해졌으나 실측 포괄은 안 늘어남 |
| ptree-pknob | urb_block | 증가 | 판정 안 함 | 감소 아님 | 다양해졌으나 실측 포괄은 안 늘어남 |
