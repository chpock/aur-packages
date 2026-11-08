"""Exercise an installed package as a normal user in a disposable container."""

import argparse
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PREFIX = Path('/usr/lib/repowise')


def run(*arguments, cwd, input=None):
    result = subprocess.run(arguments, cwd=cwd, input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    print(result.stdout, flush=True)
    result.check_returncode()
    return result.stdout


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def payload_snapshot():
    return {str(path.relative_to(PREFIX)): digest(path)
            for path in PREFIX.rglob('*') if path.is_file()}


def userdata_snapshot(repository):
    result = {}
    for label, root in (('project', repository / '.repowise'), ('home', Path.home() / '.repowise')):
        assert root.is_dir(), root
        for path in root.rglob('*'):
            details = path.stat()
            assert details.st_uid == os.geteuid(), path
            if path.is_file():
                result[f'{label}/{path.relative_to(root)}'] = [digest(path), details.st_uid, details.st_mode & 0o777]
    return result


def request(port, path):
    headers = {}
    if key := os.environ.get('REPOWISE_API_KEY'):
        headers['Authorization'] = f'Bearer {key}'
    with urlopen(Request(f'http://127.0.0.1:{port}{path}', headers=headers), timeout=5) as response:
        return response.read()


def wait_ready(server, port, path):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f'Serve exited early: {server.returncode}')
        try:
            return request(port, path)
        except HTTPError:
            raise
        except (URLError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError(f'Service did not become ready: {port}{path}')


async def check_mcp(repository):
    # The SDK is an installed application dependency, not a host test dependency.
    from mcp import ClientSession, StdioServerParameters  # pyright: ignore[reportMissingImports]
    from mcp.client.stdio import stdio_client  # pyright: ignore[reportMissingImports]

    parameters = StdioServerParameters(command='/usr/bin/repowise',
                                       args=['mcp', str(repository)], env=dict(os.environ))
    with (repository.parent / 'mcp.log').open('w') as errors:
        async with stdio_client(parameters, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert 'get_symbol' in {tool.name for tool in tools.tools}
                result = await client.call_tool('get_symbol', {'symbol_id': 'maths.py::sum_values'})
                assert not result.isError, result
                text = '\n'.join(item.text for item in result.content if item.type == 'text')
                assert 'return left + right' in text, text
                (repository.parent / 'mcp-response.json').write_text(result.model_dump_json(indent=2))
                return len(tools.tools)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path, help='new repository directory in a fresh disposable HOME')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--browser', action='store_true', help='wait for SIGUSR1 while an external browser tests the UI')
    mode.add_argument('--removed', action='store_true', help='verify preservation after reinstalling and removing the package')
    arguments = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit('Run as an unprivileged test user inside a disposable container')
    repository = arguments.repository.resolve()
    expected = repository.parent / 'expected-userdata.json'
    if arguments.removed:
        assert not PREFIX.exists()
        assert shutil.which('repowise') is None
        assert userdata_snapshot(repository) == json.loads(expected.read_text())
        print('REINSTALL_REMOVAL_PASSED; all user state content, owners and permissions preserved', flush=True)
        return
    repository.mkdir()
    if arguments.browser:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
    os.environ.update(DO_NOT_TRACK='1', NEXT_TELEMETRY_DISABLED='1', PYTHONDONTWRITEBYTECODE='1')
    before = payload_snapshot()
    try:
        (PREFIX / 'web/.write-probe').write_text('must not succeed')
    except PermissionError:
        pass
    else:
        raise AssertionError('The unprivileged user can write packaged assets')
    run('git', 'init', '-b', 'main', cwd=repository)
    run('git', 'config', 'user.name', 'Package Integration', cwd=repository)
    run('git', 'config', 'user.email', 'integration@example.invalid', cwd=repository)
    (repository / 'maths.py').write_text('def sum_values(left, right):\n    return left + right\n')
    (repository / 'main.py').write_text(
        'from maths import sum_values\n\ndef main():\n    return sum_values(2, 3)\n\n'
        'if __name__ == "__main__":\n    print(main())\n')
    (repository / 'native.cpp').write_text('int bump(int value) { return value + 1; }\n')
    run('git', 'add', '.', cwd=repository)
    run('git', 'commit', '-m', 'add integration fixture', cwd=repository)
    run('node', '-e',
        'const sharp = require("/usr/lib/repowise/web/node_modules/sharp"); '
        'sharp({create:{width:1,height:1,channels:4,background:"red"}}).png().toBuffer()'
        '.then(data => { if (!data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) '
        'process.exit(1); console.log("Sharp PNG bytes:", data.length); });', cwd=repository)
    version = run('repowise', '--version', cwd=repository).strip()
    run('repowise', '--help', cwd=repository)
    run('repowise', 'init', '.', '--yes', '--mode', 'standard', '--no-prose',
        '--embedder', 'mock', '--no-editor-setup', cwd=repository)
    for command in ('repowise-augment', 'repowise-rewrite'):
        run(command, cwd=repository, input='{}')
    tool_count = asyncio.run(check_mcp(repository))
    log = (repository.parent / 'serve.log').open('w')
    server = subprocess.Popen(['repowise', 'serve', '--host', '0.0.0.0', '--port', '7337',
                               '--ui-port', '3000', '--no-open', '--refresh-ui'],
                              cwd=repository, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        repositories = json.loads(wait_ready(server, 7337, '/api/repos'))
        indexed = next(repo for repo in repositories if Path(repo['local_path']) == repository)
        repo_id = indexed['id']
        stats = json.loads(request(7337, f'/api/repos/{repo_id}/stats'))
        assert stats['file_count'] == 3, stats
        for name in ('sum_values', 'main', 'bump'):
            query = urlencode({'repo_id': repo_id, 'q': name})
            symbols = json.loads(request(7337, f'/api/symbols?{query}'))
            assert any(symbol['name'] == name for symbol in symbols['items']), symbols
        html = wait_ready(server, 3000, '/')
        assert b'/_next/static/' in html
        assert json.loads(request(3000, '/api/repos')) == repositories
        meta = json.loads(request(3000, '/api/meta/version'))
        assert 'AUR' in json.dumps(meta), meta
        assert not (Path.home() / '.repowise/web').exists()
        report = {'version': version, 'repository_id': repo_id, 'stats': stats,
                  'mcp_tools': tool_count, 'packaged_files': len(before), 'api_proxy': True}
        (repository.parent / 'api-response.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        if arguments.browser:
            (repository.parent / 'integration.pid').write_text(str(os.getpid()))
            print('READY_FOR_BROWSER', flush=True)
            signal.sigwait({signal.SIGUSR1})
    finally:
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()
                raise
        log.close()
    assert payload_snapshot() == before, 'Installed application mutated its payload'
    assert not (Path.home() / '.repowise/web').exists()
    expected.write_text(json.dumps(userdata_snapshot(repository), indent=2))
    print('INSTALLED_INTEGRATION_PASSED; packaged payload unchanged', flush=True)


if __name__ == '__main__':
    main()
