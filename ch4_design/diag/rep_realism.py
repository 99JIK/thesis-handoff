"""보조 실험 4 기술 지표: 재현 시드(8~15)의 recall, precision. py3.11. 2026-11-18 추가

  python diag/rep_realism.py        -> results/rep/realism.md, realism.csv
  python diag/rep_realism.py --units data/outputs/units_exp3 --arms plain,knob,tree3 --pair tree3,knob       --out results/exp3/realism --title "실험 3 E3 실측 행동 포괄"      (2026-11-19 추가, 실험 3)

recall은 realism.coverage(cov_unit), precision은 slides_figs.precision과 같은 칸, 같은 오프셋 규칙.
둘 다 단위 캐시 경로만 data/outputs/units_rep로 바꿈. 판정 밖 기술이라 맵 단위 차이와 t CI만 냄.
덮개는 맵 안 조건 사이 단위 수를 맞추는데 조건 목록이 앞 판(아홉 조건)과 달라 절대값은 직접 비교 안 함.
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "diag"))
import measure as M  # noqa: E402
import realism as R  # noqa: E402
import slides_figs as SF  # noqa: E402

UNITS = os.path.join(ROOT, "data", "outputs", "units_rep")
OUT = os.path.join(ROOT, "results", "rep")
ARMS = ["plain", "knob", "offkr"]
STS = ["junction", "urb_block"]


def load_units(UNITS=UNITS, ARMS=ARMS):
    fs = [f for f in glob.glob(os.path.join(UNITS, "*.csv"))
          if M.FILE_RE.match(os.path.basename(f)) and M.FILE_RE.match(os.path.basename(f)).group(1) in ARMS]
    out = []
    for f in fs:
        arm, town, seed = M.FILE_RE.match(os.path.basename(f)).groups()
        u = pd.read_csv(f)
        u["arm"], u["town"], u["seed"] = arm, town, int(seed)
        out.append(u)
    return pd.concat(out, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default=UNITS)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--pair", default="offkr,knob", help="차이를 낼 짝 a,b(a - b)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--title", default="보조 실험 4 기술: recall, precision")
    ap.add_argument("--note", default="시드 8~15, 세 조건 한 판")
    a = ap.parse_args()
    run(a.units, a.arms.split(","), a.pair.split(","), a.out, a.title, a.note)


def run(UNITS, ARMS, pair, OUT, title, note):
    pa, pb = pair
    sc = M.load_scale()
    real = pd.read_csv(os.path.join(R.REAL_DIR, "units.csv"))
    real = real[real.site.map(R.src_of).isin(R.SOURCES)]
    u = load_units(UNITS, ARMS)
    cov = R.coverage(u, real, ARMS, sc)
    SF.UNIT_DIR = UNITS
    rows = []
    for st in STS:
        pre = SF.precision(set(ARMS), st)       # 조건별 맵 평균만 돌려줌
        c = cov[cov.stratum == st].pivot(index="town", columns="arm", values="cov_unit")
        for a in ARMS:
            rows.append(dict(stratum=st, arm=a, recall=c[a].mean() * 100, precision=pre[a] * 100))
        d = (c[pa] - c[pb]).to_numpy(float) * 100
        lo, hi = M.t_ci(d)
        rows.append(dict(stratum=st, arm="%s-%s recall" % (pa, pb), recall=d.mean(), ci_lo=lo, ci_hi=hi,
                         wins="%d/%d" % ((d > 0).sum(), len(d))))
    t = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    t.to_csv(os.path.join(OUT, "realism.csv"), index=False)
    lines = ["# %s" % title, "",
             "%s. 맵 평균(%%). 생성: `diag/rep_realism.py`" % note, ""]
    for st in STS:
        s = t[t.stratum == st]
        lines += ["## %s" % st, "", "| 조건 | recall | precision |", "|---|---|---|"]
        for _i, r in s[s.arm.isin(ARMS)].iterrows():
            lines.append("| %s | %.1f | %.1f |" % (r.arm, r.recall, r.precision))
        r = s[s.arm == "%s-%s recall" % (pa, pb)].iloc[0]
        lines += ["", "recall 차이 %s - %s: %+.1f%%p [%+.1f, %+.1f], 우세 맵 %s" % (
            pa, pb, r.recall, r.ci_lo, r.ci_hi, r.wins), ""]
    with open(os.path.join(OUT, "realism.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines).encode("cp949", "replace").decode("cp949"))


if __name__ == "__main__":
    main()
