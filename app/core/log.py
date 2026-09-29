import logging
import os


def setup_logging(level: int = logging.INFO) -> None:
    # RUN_SOURCE가 있으면 로그 앞에 [cron], [manual] 같은 표시를 붙인다.
    source = os.getenv("RUN_SOURCE")
    tag = f"[{source}] " if source else ""

    logging.basicConfig(
        level=level,
        format=f"%(asctime)s %(levelname)s {tag}%(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)