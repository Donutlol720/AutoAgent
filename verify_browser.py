"""Read-only, model-independent verifier against open Chrome tabs.

Examples:
  .venv/bin/python verify_browser.py https://httpbin.org/uuid --expected-uuid <uuid>
  .venv/bin/python verify_browser.py https://example.com --expected-title 'Example Domain'

Invokes the browser-use CLI's browser-harness helpers. Does not reload tabs
or send Ollama requests. It changes which tab the harness is attached to,
but normally does not activate Chrome's visible tab.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


MARKER = "AUTOAGENT_VERIFY_JSON="


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))


def inspect_open_tabs(url: str) -> dict:
    runner = Path(sys.executable).with_name('browser-use')
    if not runner.is_file():
        raise RuntimeError(f"browser-use CLI not found at {runner}")

    # Pass URL as a Python string literal, never shell code.
    script = f'''
import json
want = {json.dumps(normalize_url(url))}
def norm(u):
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit((u or '').strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))
found = []
for tab in list_tabs(include_chrome=False):
    if norm(tab.get('url', '')) != want:
        continue
    try:
        switch_tab(tab)
        observed = js("({{url: location.href, title: document.title, h1: document.querySelector('h1')?.textContent || null, body: document.body?.innerText || ''}})")
        text = observed.get('body', '') if isinstance(observed, dict) else ''
        item = {{'url': observed.get('url') if isinstance(observed, dict) else None,
                'title': observed.get('title') if isinstance(observed, dict) else None,
                'h1': observed.get('h1') if isinstance(observed, dict) else None,
                'uuid': None, 'read_error': None}}
        if isinstance(text, str):
            try:
                data = json.loads(text)
                if isinstance(data, dict) and isinstance(data.get('uuid'), str):
                    item['uuid'] = data['uuid']
            except (ValueError, TypeError):
                pass
        found.append(item)
    except Exception as exc:
        found.append({{'url': tab.get('url'), 'read_error': type(exc).__name__ + ': ' + str(exc)[:200]}})
print({json.dumps(MARKER)} + json.dumps({{'matched_tab_count': len(found), 'tabs': found}}, ensure_ascii=False))
'''
    completed = subprocess.run(
        [str(runner)], input=script, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=35, check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"browser-use CLI returned exit code {completed.returncode}: "
            f"{completed.stderr[-500:]}"
        )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise RuntimeError(
        'Verifier could not locate its result marker. '
        + (completed.stderr[-300:] if completed.stderr else completed.stdout[-300:])
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Exact URL expected in the existing Chrome tabs')
    parser.add_argument('--expected-uuid', help='UUID reported by AutoAgent')
    parser.add_argument('--expected-title', help='Expected page title')
    parser.add_argument('--expected-h1', help='Expected H1 text')
    parser.add_argument('--require-single-tab', action='store_true', help='Fail if several matching tabs are present')
    args = parser.parse_args()

    result = inspect_open_tabs(args.url)
    checks = []
    for tab in result['tabs']:
        if tab.get('read_error'):
            continue
        valid = True
        if args.expected_uuid is not None:
            valid &= tab.get('uuid') == args.expected_uuid
        if args.expected_title is not None:
            valid &= (tab.get('title') or '').removeprefix('🐴 ').strip() == args.expected_title
        if args.expected_h1 is not None:
            valid &= (tab.get('h1') or '').strip() == args.expected_h1
        checks.append(bool(valid))

    passed = any(checks)
    if args.require_single_tab and result['matched_tab_count'] != 1:
        passed = False
    print(json.dumps({
        'verified': passed,
        'requested_url': args.url,
        'matching_tabs': result['matched_tab_count'],
        'details': result['tabs'],
    }, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'Verification unavailable: {exc}', file=sys.stderr)
        sys.exit(2)
