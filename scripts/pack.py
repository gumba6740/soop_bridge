"""
사용법:
python scripts/pack.py      # zip 생성(OUTPUT)
python scripts/pack.py -p   # 폴더 구조만 출력
"""

import fnmatch
import sys
import zipfile
from itertools import groupby
from pathlib import Path, PurePosixPath

ROOT = Path.cwd()  # 프로젝트 루트에서 실행
OUTPUT = "soop_bridge.zip"
DRY_RUN = "-p" in sys.argv  # True면 압축하지 않고 미리보기만 출력

# ─────────────────────────────────────────────
# 최종 = (INCLUDE - EXCLUDE) + FORCE_INCLUDE
# ─────────────────────────────────────────────

# 1. 전역 가져오기: 루트 바로 아래 폴더/파일 (폴더면 하위 전체)
INCLUDE: list[str] = [
    # ex) "app/",
]

# 2. 전역 빼기: 이름/패턴. 경로의 어느 위치에 있든 적용
#    - "/" 없는 패턴: 파일명이나 폴더명 하나와 매칭 (예: "__pycache__", "*.pyc")
#    - "/" 있는 패턴: 전체 상대경로와 매칭 (예: "app/*/tests/*")
EXCLUDE: list[str] = [
    "__init__.py",
    "__pycache__",
    "*.pyc",
    ".DS_Store",

    #
    "app/utils/decorator.py",
]

# 3. 지정해서 넣기. EXCLUDE 규칙을 이김 (INCLUDE 밖이어도 들어감)
# 용법) app/ 또는 app(app 폴더 내부의 모든 파일을 재귀 탐색), app/main.py(지정한 파일만)
FORCE_INCLUDE: list[str] = [

]


def walk(entries: list[str]) -> set[str]:
    """경로 목록 → 파일 상대경로 set (폴더는 재귀 탐색)"""
    files: set[str] = set()
    for e in entries:
        e = PurePosixPath(e.strip().rstrip("/")).as_posix()
        path = ROOT / e
        if path.is_file():
            files.add(e)
        elif path.is_dir():
            files |= {p.relative_to(ROOT).as_posix() for p in path.rglob("*") if p.is_file()}
        else:
            print(f"[경고] 존재하지 않는 경로: {e}")
    return files


def get_include() -> set[str]:
    return walk(INCLUDE)


def get_exclude(files: set[str]) -> set[str]:
    excluded: set[str] = set()
    for f in files:
        parts = f.split("/")
        for pat in EXCLUDE:
            if "/" in pat:
                hit = fnmatch.fnmatch(f, pat)
            else:
                hit = any(fnmatch.fnmatch(part, pat) for part in parts)
            if hit:
                excluded.add(f)
                break
    return excluded


def get_force_include() -> set[str]:
    return walk(FORCE_INCLUDE)


include = get_include()
exclude = get_exclude(include)
force = get_force_include()
final = (include - exclude) | force

skipped = exclude - force  # 출력용

if DRY_RUN:
    # 같은 폴더끼리 묶고, 폴더 사이는 빈 줄로 구분. 루트 파일은 맨 아래
    parent = lambda f: PurePosixPath(f).parent.as_posix()
    ordered = sorted(final, key=lambda f: (parent(f) == ".", parent(f), f))
    print("\n\n".join(
        "\n".join(f'    "{f}",' for f in group) for _, group in groupby(ordered, parent)
    ))
    print(f"\n# 포함 {len(final)}개 / 제외 {len(skipped)}개 (압축하지 않음)")
    raise SystemExit

with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for rel in sorted(final):
        zf.write(ROOT / rel, arcname=rel)

print(f"{len(final)}개 압축 완료 / {len(skipped)}개 제외 → {OUTPUT}")