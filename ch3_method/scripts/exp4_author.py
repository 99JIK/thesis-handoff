"""실험 4 생성. 실험 3 파이프라인 그대로 + 객체 설명 끝에 운전 성향 한 단락 + 카탈로그 v3(제한속도 문구).
설계는 docs/design_exp4.md

  python scripts/exp4_author.py generate --style reckless --n 5      세트 exp4_reckless
  python scripts/exp4_author.py generate --all --n 5                 네 영역 다
  python scripts/exp4_author.py check                                exp4_* 요약, 컴파일러 대조
  python scripts/exp4_author.py prompt --style anxious                프롬프트 고정 부분
  python scripts/exp4_author.py generate --all --n 5 --prefix st_   같은 파이프라인 재생성(사후 비교용)

--prefix 기본 exp4_. 다른 접두어는 exp4_* 글롭에 안 걸려야 함(본 실험 평가, 수집기가 읽음). 본 실험 트리 덮어쓰기 방지

실험 3 모듈의 전역(OBJ.DESC, SETS_GLOB, 카탈로그)을 바꿔 끼워 씀. 한 프로세스에서 실험 3 생성과 섞어 돌리지 말 것.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exp3_author as A  # noqa: E402
import exp3_schema as S  # noqa: E402
import bt_nodes as N  # noqa: E402

CATALOG = "v3"
# MDSI(Taubman-Ben-Ari, Mikulincer, Gillath 2004) 네 영역. 문항 아니고 영역 정의 보고 직접 쓴 문장(판단).
# 설정 이름, 숫자, 방향 지시 안 넣음. 이 원문이 조작 자체라 바꾸면 설계 변경
STYLES = {
    "reckless": ("The driver enjoys driving fast and taking chances, gets bored easily, and pays loose "
                 "attention to rules and to other road users."),
    "anxious": ("The driver feels tense and uneasy behind the wheel, worries about making mistakes, and is "
                "easily unsettled by traffic around them."),
    "careful": ("The driver is calm and patient, plans ahead, and prefers to keep a margin of safety rather "
                "than save time."),
    "angry": ("The driver is easily irritated by other road users, feels in a hurry, and reacts strongly when "
              "held up."),
}
BASE_DESC = A.OBJ.DESC
BASE_SPECIFY, BASE_REPAIR = A.SPECIFY, A.REPAIR
SETS_GLOB = os.path.join(A.OUT, "exp4_*")


def _desc_line(desc):
    # format 템플릿에 들어가므로 중괄호 이스케이프
    return ("The object is described as follows: " + desc).replace("{", "{{").replace("}", "}}")


MAIN_PREFIX = "exp4_"


def check_prefix(prefix):
    # 본 실험 묶음과 섞이면 평가, 수집 숫자가 바뀜. 기본값 아니면 exp4_* 에 안 걸려야 함
    import fnmatch
    if prefix != MAIN_PREFIX and fnmatch.fnmatch(prefix + "x", MAIN_PREFIX + "*"):
        raise SystemExit("접두어 %s 는 본 실험 묶음 exp4_* 와 겹침" % prefix)
    return prefix


def use(style=None):
    """카탈로그 v3, 객체 설명에 성향 붙임. style None이면 성향 없이(누설 시험용).
    classify만 객체 설명을 받고 specify, repair는 상태 없는 별도 호출이라, 성향이 값과 방향을 정하는
    단계까지 가게 두 템플릿에도 설명 한 줄을 넣음. 실험 3 템플릿 원본은 안 건드림(모듈 속성만 바꿈)"""
    N.set_catalog(CATALOG)
    S.CATALOG = CATALOG
    A.OBJ.DESC = BASE_DESC + ("" if style is None else " " + STYLES[style])
    line = _desc_line(A.OBJ.DESC)
    A.SPECIFY = BASE_SPECIFY.replace("{dimensions}\n", "{dimensions}\n\n" + line + "\n", 1)
    A.REPAIR = line + "\n\n" + BASE_REPAIR
    A.SETS_GLOB = SETS_GLOB


def prompt_parts(style):
    use(style)
    return A.prompt_parts()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("generate", "check", "prompt"))
    ap.add_argument("--style", choices=sorted(STYLES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--prefix", default=MAIN_PREFIX)
    a = ap.parse_args()
    prefix = check_prefix(a.prefix)
    if a.cmd == "check":
        use()
        A.SETS_GLOB = os.path.join(A.OUT, prefix + "*")
        return A.main_check()
    styles = sorted(STYLES) if a.all else [a.style]
    if styles == [None]:
        raise SystemExit("--style 또는 --all")
    if a.cmd == "prompt":
        for k, t in prompt_parts(styles[0]).items():
            print("== %s\n%s\n" % (k.upper(), t))
        return 0
    for st in styles:
        use(st)
        # 실험 3 생성 함수가 sys.argv를 읽음. 세트 이름이 곧 성향
        sys.argv = [sys.argv[0], "--set", prefix + st, "--n", str(a.n)] + (["--force"] if a.force else [])
        A.main_generate()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
