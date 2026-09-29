"""Create a consistent, private SQLite backup without stopping the application."""
import argparse
import os
import sqlite3
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path)
    p.add_argument('destination',type=Path)
    args=p.parse_args()
    if not args.source.is_file():
        p.error('Source database does not exist.')
    if args.destination.exists():
        p.error('Destination exists. Choose a new filename; existing backups are never overwritten.')
    os.umask(0o077)
    args.destination.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(f'file:{args.source.resolve()}?mode=ro',uri=True) as source:
        with sqlite3.connect(str(args.destination)) as destination:
            source.backup(destination)
    print(f'Private backup created: {args.destination}')

if __name__=='__main__':main()
