"""Consistent SQLite backup, including WAL. Run inside the dashboard container."""

import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("destination")
parser.add_argument("--source", default="/data/review.sqlite3")
args = parser.parse_args()
if Path(args.destination).exists():
    parser.error("대상 파일이 이미 존재합니다. 새 백업 경로를 지정하세요.")
Path(args.destination).parent.mkdir(parents=True, exist_ok=True)
with (
    sqlite3.connect(f"file:{args.source}?mode=ro", uri=True) as source,
    sqlite3.connect(args.destination) as target,
):
    source.backup(target)
print("백업 완료:", args.destination)
