"""Save the live public projection for the next deployment's outage fallback."""
import gzip
import json
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.public_snapshot import validate_public  # noqa: E402


def main():
    origin = sys.argv[1] if len(sys.argv) > 1 else 'https://nycopenmicmasterlist.com'
    if not origin.startswith('https://'):
        raise SystemExit('Use an HTTPS public origin.')
    with urlopen(origin.rstrip('/') + '/api/public/snapshot', timeout=45) as response:
        data = validate_public(json.load(response))
    if data.get('stale') or not data['listings']:
        raise SystemExit('Refusing to replace the bundle with an unavailable or empty directory.')
    target = ROOT / 'app' / 'public_snapshot.json.gz'
    encoded = json.dumps(data, separators=(',', ':')).encode()
    target.write_bytes(gzip.compress(encoded, mtime=0))
    print(f"Saved {len(data['listings'])} public listings, revision {data['revision']}, snapshot_at {data['snapshot_at']}.")


if __name__ == '__main__':
    main()
