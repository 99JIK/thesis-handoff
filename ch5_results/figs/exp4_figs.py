"""실험 4 그림. results/exp4의 결과 파일만 읽음(수집물 안 읽음). py3.11

  python scripts/exp4_figs.py   -> results/exp4/figs/*.png
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "diag"))
from exp4_eval import t_ci  # noqa: E402
from slides_figs import C_DRAW, C_TREE, C_REF, C_PK, INK, INK2, MUTED, GRID, style  # noqa: E402

R4 = os.path.join(ROOT, "results", "exp4")
OUT = os.path.join(R4, "figs")
# 색은 slides_figs 덱 톤 한 군데서. pknob 피치는 대비 3:1 아래라 축 이름, 행 이름으로 구분
ARM = [("plain", "기준", C_REF), ("knob", "추첨", C_DRAW), ("ptree", "성향 트리", C_TREE), ("pknob", "평균 상수", C_PK)]
COL = dict((a, c) for a, _n, c in ARM)
NAME = dict((a, n) for a, n, _c in ARM)
ST = [("junction", "교차로"), ("urb_block", "도심 블록")]
STY = [("reckless", "무모"), ("angry", "분노"), ("anxious", "불안"), ("careful", "신중")]
CON = [("ptree-knob", "성향 트리 - 추첨"), ("ptree-pknob", "성향 트리 - 평균 상수"), ("pknob-knob", "평균 상수 - 추첨")]


def save(fig, name):
    # 제목은 슬라이드가 달고, 그림 위 글은 읽는 법 캡션으로만. 왼쪽 정렬 회색
    st = fig._suptitle
    if st is not None:
        st.set_horizontalalignment("left")
        st.set_x(0.01)
        st.set_color(INK2)
        st.set_fontsize(10)
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, name))
    plt.close(fig)
    print("-> results/exp4/figs/%s" % name)


def fig_manip():
    T = pd.read_csv(os.path.join(R4, "manip_trees.csv"))
    # speed_diff는 +가 느림이라 부호를 뒤집어 +가 빠름으로 그림
    T["speed"] = -T.d_speed_diff
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    for ax, col, lab in ((axes[0], "speed", "속도 변화(제한속도 대비 %p, +가 빠름)"),
                         (axes[1], "d_gap_m", "간격 변화(m, +가 넓음)")):
        ax.axhline(0, color=INK2, lw=1)
        for i, (st, ko) in enumerate(STY):
            v = T[T["style"] == st][col].to_numpy()
            ax.scatter(np.full(len(v), i) + np.linspace(-0.12, 0.12, len(v)), v, s=28, color=C_TREE, alpha=0.55,
                       edgecolor="white", linewidth=0.8, zorder=3)
            lo, hi = t_ci(v)
            ax.plot([i + 0.28] * 2, [lo, hi], color=INK, lw=2, solid_capstyle="round")
            ax.plot(i + 0.28, v.mean(), "o", ms=7, color=INK, zorder=4)
        ax.axvline(1.5, color=GRID, lw=1)
        ax.set_xticks(range(len(STY)))
        ax.set_xticklabels([ko for _s, ko in STY])
        ax.set_ylabel(lab)
        ax.grid(axis="x", visible=False)
        tr = ax.get_xaxis_transform()
        ax.text(0.5, 1.02, "기대: 빠르고 좁게", transform=tr, ha="center", va="bottom", fontsize=9, color=INK2)
        ax.text(2.5, 1.02, "기대: 느리고 넓게", transform=tr, ha="center", va="bottom", fontsize=9, color=INK2)
    fig.suptitle("조작 확인: 성향별 트리 평균 적용값 변화(점: 트리, 검은 점과 막대: 평균과 95% t 구간)", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "fig1_manip.png")


def fig_diversity():
    D = pd.read_csv(os.path.join(R4, "diversity", "diversity.csv"))
    D = D[D.metric == "diversity_q1"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), sharey=True)
    for ax, (st, ko) in zip(axes, ST):
        d = D[D.stratum == st].set_index("arm")
        pm = np.array([[float(x) for x in d.loc[a, "per_map"].split()] for a, _n, _c in ARM])
        for j in range(pm.shape[1]):
            ax.plot(range(len(ARM)), pm[:, j], color=GRID, lw=1, zorder=1)
        for i, (a, n, c) in enumerate(ARM):
            ax.scatter(np.full(pm.shape[1], i), pm[i], s=18, color=c, alpha=0.6, edgecolor="white", linewidth=0.6,
                       zorder=2)
            ax.plot([i + 0.18] * 2, [d.loc[a, "ci_lo"], d.loc[a, "ci_hi"]], color=c, lw=2.5, solid_capstyle="round")
            ax.plot(i + 0.18, d.loc[a, "mean"], "o", ms=7, color=c, markeredgecolor="white", zorder=4)
            ax.text(i + 0.28, d.loc[a, "mean"], "%.2f" % d.loc[a, "mean"], va="center", fontsize=9, color=INK2)
        ax.set_xticks(range(len(ARM)))
        ax.set_xticklabels([n for _a, n, _c in ARM])
        ax.set_title(ko, fontsize=11, color=INK)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("움직임 유형 다양성 q1(기준 대비 배수)")
    fig.suptitle("조건별 다양성(회색 선: 같은 맵, 큰 점과 막대: 맵 8개 평균과 95% t 구간)", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "fig2_diversity.png")


def floor_ci():
    F = pd.read_csv(os.path.join(R4, "floor_runs.csv"))
    out = {}
    for st, _k in ST:
        m = F[F.stratum == st].groupby(["town", "set", "arm"]).q1.mean().unstack(["set", "arm"])
        d = (m[("s24_31", "knob")] / m[("s24_31", "plain")] - m[("s16_23", "knob")] / m[("s16_23", "plain")]).dropna()
        out[st] = t_ci(d)
    return out


def forest(ax, rows, ref=None, xlabel="", fmt="%+.2f [%+.2f, %+.2f]"):
    """rows: (이름, 차이, 하한, 상한, 색). 색은 앞 조건, 채움은 판정(구간이 0을 안 품으면 채움)"""
    if ref is not None:
        ax.axvspan(ref[0], ref[1], color=GRID, alpha=0.9, lw=0, zorder=0)
    ax.axvline(0, color=INK, lw=1)
    for i, (lab, d, lo, hi, c) in enumerate(rows):
        y = len(rows) - 1 - i
        hit = lo > 0 or hi < 0
        # 판정 안 함은 가늘게. 투명도로 옅게 하면 주황이 피치(평균 상수)처럼 보여 안 씀
        ax.plot([lo, hi], [y, y], color=c, lw=3 if hit else 1.6, solid_capstyle="round")
        ax.plot(d, y, "o", ms=9, color=c, markerfacecolor=c if hit else "white", markeredgewidth=2, zorder=3)
        ax.text(d, y + 0.22, (fmt % (d, lo, hi)).replace("-", "−"), fontsize=8.5, color=INK if hit else MUTED, va="bottom", ha="center",
                bbox=dict(fc="white", ec="none", pad=0.4, alpha=0.9), zorder=4)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows][::-1])
    ax.set_ylim(-0.6, len(rows) - 0.3)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(xlabel)


def fig_contrasts():
    # 증가율(%) = 배수 차이 / 비교 조건 맵 평균 배수. 점은 briefing 표(맵 평균 비 - 1)와 같은 값.
    # 구간과 잡음 띠는 분모를 고정값으로 본 환산이라 근사. 잡음 띠 분모는 추첨 배수
    Q = pd.read_csv(os.path.join(R4, "diversity", "tests.csv"))
    Q = Q[Q.metric == "diversity_q1"].set_index(["contrast", "stratum"])
    D = pd.read_csv(os.path.join(R4, "diversity", "diversity.csv"))
    D = D[D.metric == "diversity_q1"].set_index(["stratum", "arm"])["mean"]
    fl = floor_ci()
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.2), sharex=True)
    for ax, (st, ko) in zip(axes, ST):
        rows = []
        for c, lab in CON:
            r = Q.loc[(c, st)]
            k = 100 / D[(st, c.split("-")[1])]
            rows.append((lab, r["diff"] * k, r.ci_lo * k, r.ci_hi * k, COL[c.split("-")[0]]))
        kf = 100 / D[(st, "knob")]
        forest(ax, rows, (fl[st][0] * kf, fl[st][1] * kf), "비교 조건 대비 다양성 증가율(%)",
               fmt="%+.1f%% [%+.1f, %+.1f]")
        ax.set_title(ko, fontsize=11, color=INK, loc="left")
    fig.suptitle("맵 8개 95% t 구간. 채운 점: 판정(구간이 0을 안 품음), 빈 점: 판정 안 함. "
                 "회색 띠: 시드 잡음 바닥(추첨 조건 시드 16~23 대 24~31)", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "fig3_contrasts.png")


def fig_realism():
    R = pd.read_csv(os.path.join(R4, "realism.csv"))
    fig, axes = plt.subplots(2, 2, figsize=(9.5, 5.4), sharex="row")
    for j, (st, ko) in enumerate(ST):
        for i, (kind, klab) in enumerate((("recall", "recall"), ("precision", "precision"))):
            sub = R[(R.stratum == st) & (R.kind == kind)].set_index("contrast")
            rows = [(lab, sub.loc[c, "diff"], sub.loc[c, "ci_lo"], sub.loc[c, "ci_hi"], COL[c.split("-")[0]])
                    for c, lab in CON]
            forest(axes[i, j], rows, None, "%s 차이(%%p)" % klab)
            axes[i, j].set_title("%s, %s" % (ko, klab), fontsize=10.5, color=INK)
    fig.suptitle("실측 행동 포괄 대비(맵 8개 95% t 구간)", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "fig4_realism.png")


def fig_realization():
    import re
    real = pd.read_csv(os.path.join(R4, "e2_realization.csv"))
    pla = pd.read_csv(os.path.join(R4, "placebo_realization.csv"))
    # 부트스트랩 구간은 placebo.md, e2.md 숫자를 그대로 씀(같은 시드로 다시 뽑으면 같은 값)
    vals = []
    for lab, g, md in (("위약(plain 차에 트리 예측)", pla, "placebo.md"), ("실제(성향 트리)", real, "e2.md")):
        j = g[g.judged]
        txt = open(os.path.join(R4, md), encoding="utf-8").read()
        m = re.findall(r"95% \[([0-9.]+), ([0-9.]+)\]", txt)
        lo, hi = map(float, m[0])
        vals.append((lab, j.realized.mean(), lo, hi, int(j.realized.sum()), len(j)))
    fig, ax = plt.subplots(figsize=(7, 2.2))
    ax.axvline(0.5, color=INK2, lw=1, ls=(0, (3, 3)))
    for i, (lab, v, lo, hi, k, n) in enumerate(vals):
        c = C_REF if i == 0 else C_TREE
        ax.plot([lo, hi], [i, i], color=c, lw=2.5, solid_capstyle="round")
        ax.plot(v, i, "o", ms=8, color=c, markeredgecolor="white", zorder=3)
        ax.text(hi + 0.015, i, "%d/%d = %.2f [%.2f, %.2f]" % (k, n, v, lo, hi), va="center", fontsize=9, color=INK2,
                bbox=dict(fc="white", ec="none", pad=0.4, alpha=0.9), zorder=4)
    ax.set_yticks(range(len(vals)))
    ax.set_yticklabels([v[0] for v in vals])
    ax.set_xlim(0.2, 1.0)
    ax.set_ylim(-0.6, 1.6)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("실현 비율(행동이 켜진 틱에서 의도한 방향으로 움직인 행동의 비율)")
    ax.set_title("실현율과 위약 대조((맵, 시드) 묶음 부트스트랩 95%)", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "fig5_realization.png")


def fig_exp3_trees():
    # 덱 17번. 실험 3 트리별 평균 이동. 입력은 exp4_eval.py exp3trees 결과
    T = pd.read_csv(os.path.join(ROOT, "results", "exp3", "rescore", "tree_deltas.csv"))
    x, y = -T.d_speed_diff.to_numpy(), T.d_gap_m.to_numpy()
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.axhline(0, color=INK2, lw=1)
    ax.axvline(0, color=INK2, lw=1)
    ax.scatter(x, y, s=60, color=C_TREE, edgecolor="white", linewidth=1, zorder=3)
    ax.set_xlim(-14, 14)
    ax.set_ylim(-0.9, 1.1)
    ax.text(-13.5, 1.05, "느리고 넓게  %d/%d" % (((x < 0) & (y > 0)).sum(), len(T)), ha="left", va="top", fontsize=10,
            color=INK)
    ax.text(13.5, -0.85, "빠르고 좁게  %d/%d" % (((x > 0) & (y < 0)).sum(), len(T)), ha="right", va="bottom", fontsize=10,
            color=INK2)
    ax.set_xlabel("속도 이동(제한속도 대비 %p, +가 빠름)")
    ax.set_ylabel("간격 이동(m, +가 넓음)")
    ax.set_title("실험 3 트리 20개의 평균 이동(차량별 적용값 - 추첨값, 트리마다 약 375대 평균)", fontsize=10.5, color=INK)
    fig.tight_layout()
    save(fig, "fig6_exp3_trees.png")


def fig_scale():
    # 척도별 증가율(Vendi, 탐색). 증가율 = 두 조건의 기준 대비 배수 맵 평균의 비 - 1. 채운 점은 판정 증가, 빈 점은 판정 안 함
    V = pd.read_csv(os.path.join(R4, "vendi.csv"))
    hs = (0.5, 1.0, 2.0)
    pairs = [("knob", "plain", "추첨 대 기준", C_DRAW), ("ptree", "pknob", "성향 트리 대 평균 상수", C_TREE)]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
    for ax, (st, ko) in zip(axes, ST):
        ax.axhline(0, color=INK2, lw=1)
        for a, b, lab, c in pairs:
            ys, fill = [], []
            for h in hs:
                m = V[(V.metric == "vs_h%g" % h) & (V.stratum == st)]
                lv = m.set_index("contrast")["mean"]
                ys.append(100 * (lv["level:" + a] / lv["level:" + b] - 1))
                t = m[m.contrast == "%s-%s" % (a, b)].iloc[0]
                fill.append(t.ci_lo > 0)
            ax.plot(hs, ys, color=c, lw=2, zorder=2, label=lab)
            for x, y, f in zip(hs, ys, fill):
                ax.plot(x, y, "o", ms=8, color=c, markerfacecolor=c if f else "white", markeredgewidth=2, zorder=3)
                ax.text(x, y + 3, "%+.0f%%" % y, ha="center", va="bottom", fontsize=9, color=INK2)
        ax.set_xscale("log", base=2)
        ax.set_xticks(hs)
        ax.set_xticklabels(["0.5 sd" + chr(10) + "세밀", "1 sd", "2 sd" + chr(10) + "굵음"])
        ax.set_xlim(0.42, 2.4)
        ax.set_ylim(-8, 85)
        ax.set_title(ko, fontsize=11, color=INK)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("다양성 증가율(%)")
    axes[1].legend(loc="upper right", frameon=False, fontsize=9)
    fig.suptitle("비슷하다고 보는 거리(실측 표준편차 단위)별 증가율. 채운 점: 95% 구간 0 위, 빈 점: 판정 안 함",
                 fontsize=10.5, color=INK)
    fig.tight_layout()
    save(fig, "fig7_scale.png")


def main():
    style()
    fig_scale()
    fig_exp3_trees()
    fig_manip()
    fig_diversity()
    fig_contrasts()
    fig_realism()
    fig_realization()


if __name__ == "__main__":
    main()
