"""Verify the release ZIP through the installed Codex CLI without inference."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile


def main(executable, bundle):
    with TemporaryDirectory() as temp:
        root = Path(temp)
        catalog = root / "marketplace"
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(catalog)
        project = root / "repo"
        project.mkdir()
        subprocess.run(["git", "init", "--quiet", str(project)], check=True)
        (project / "example.py").write_text("def example(): return 1\n", encoding="utf-8")
        home = root / "codex-home"
        home.mkdir()
        env = dict(os.environ, CODEX_HOME=str(home))
        cli = [executable, "-C", str(project)]
        for args in (["plugin", "marketplace", "add", str(catalog)],
                     ["plugin", "add", "between-the-lines-minimal@between-the-lines-preview"]):
            subprocess.run(cli + args, cwd=project, env=env, check=True, timeout=60)
        with subprocess.Popen(cli + ["app-server", "--listen", "stdio://"], cwd=project,
                              env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, encoding="utf-8") as server:
            def request(number, method, params):
                data = {"id": number, "method": method, "params": params}
                server.stdin.write(json.dumps(data) + "\n")
                server.stdin.flush()
                for line in server.stdout:
                    message = json.loads(line)
                    if message.get("id") == number:
                        if "error" in message:
                            raise RuntimeError(f"{method}: {message['error']}")
                        return message["result"]
                raise EOFError(method)

            try:
                request(1, "initialize", {"clientInfo": {"name": "release-test", "version": "1"},
                                         "capabilities": {"experimentalApi": True}})
                server.stdin.write('{"method":"initialized","params":{}}\n')
                session = request(2, "thread/start", {"cwd": str(project), "ephemeral": True})
                result = request(3, "mcpServer/tool/call", {
                    "threadId": session["thread"]["id"], "server": "between-the-lines",
                    "tool": "lookup_intent", "arguments": {"path": "example.py", "symbol": "example"}})
                assert not result.get("isError"), result
                value = json.loads(result["content"][0]["text"])
                assert (value["symbol"], value["status"]) == ("example", "missing"), value
            finally:
                server.stdin.close()
                try:
                    server.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__ == "__main__":
    main(*sys.argv[1:])
