"""GitHub Actions entrypoint. Uses an ephemeral identity, no stored site password."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def report_result(result):
    if result.get('state') not in ('complete', 'busy'):
        raise SystemExit('Unexpected source-check response.')
    print(f"Source checks: {result['state']}; checked {result.get('checked', 0)}, "
          f"updated {result.get('updated', 0)}, unchanged {result.get('unchanged', 0)}, "
          f"failed {result.get('failed', 0)}, review {result.get('review', 0)}, "
          f"still due {result.get('due_remaining', 0)}, unhealthy {result.get('unhealthy_sources', 0)}; "
          f"{result.get('snapshot_sources', 0)} uploaded snapshot(s) are not scheduled.")
    for source in result.get('results', []):
        if source.get('state') in ('error', 'review'):
            # JSON encoding keeps imported error text from injecting log lines or commands.
            print('Source issue: ' + json.dumps(source, ensure_ascii=True))
    if result.get('failed', 0) or result.get('review', 0) or result.get('unhealthy_sources', 0):
        raise SystemExit('One or more live sources need attention. Existing listings were kept; check the admin Sources page.')


def main():
    target = json.loads((Path(__file__).resolve().parents[1] / 'deployment.json').read_text())['production_url'].rstrip('/')
    if not target:
        print('Deployment has not been connected yet; no source check requested.')
        return
    parsed = urlsplit(target)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise SystemExit('production_url must be an HTTPS origin.')
    client = build_opener(NoRedirect())
    oidc_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    oidc_url += ('&' if '?' in oidc_url else '?') + urlencode({'audience': 'nycstandupopenmicmaster:source-sync'})
    identity = Request(oidc_url, headers={'Authorization': 'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    try:
        with client.open(identity, timeout=20) as response:
            token = json.load(response)['value']
        request = Request(target + '/api/cron/sync', headers={'Authorization': 'Bearer '+token})
        with client.open(request, timeout=290) as response:
            result = json.load(response)
        report_result(result)
    except HTTPError as error:
        raise SystemExit(f'Scheduled check failed with HTTP {error.code}.') from None


if __name__ == '__main__':
    main()
