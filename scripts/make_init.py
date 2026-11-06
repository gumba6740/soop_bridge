"""폴더와 그 아래 모든 폴더에 __init__.py를 만든다.

용례:
    python scripts/make_inits.py app

이미 있는 __init__.py는 건드리지 않는다.
__pycache__와 숨김 폴더(.으로 시작)는 건너뛴다.
"""

import argparse
import os
from pathlib import Path


def walk_dirs(root: Path) -> list[Path]:
    """root와 그 아래 모든 폴더. 위에서 아래로, 이름순."""
    found = []
    for cur, dirnames, _ in os.walk(root):
        # 이 목록에서 지운 폴더는 os.walk가 들어가지 않는다.
        dirnames[:] = sorted(
            d for d in dirnames if d != "__pycache__" and not d.startswith(".")
        )
        found.append(Path(cur))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="모든 폴더에 __init__.py를 만든다.")
    parser.add_argument("dir", metavar="DIR", type=Path)
    args = parser.parse_args()
    root: Path = args.dir

    if not root.is_dir():
        parser.error(f"폴더가 아님: {root}")

    created = 0
    for d in walk_dirs(root):
        f = d / "__init__.py"
        if not f.exists():
            f.touch()
            print(f"생성: {f.as_posix()}")
            created += 1
    print(f"__init__.py {created}개를 생성.")


if __name__ == "__main__":
    main()