"""Verify the release ZIP through the installed Codex CLI without inference."""

import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


def submit_prompt(cli, env, project):
    """Let Codex dispatch its real prompt hook with a loopback Responses fixture."""
    class Reply(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for kind in ("response.created", "response.completed"):
                data = {"type": kind, "response": {"id": "r_fixture", "object": "response",
                        "status": "completed" if kind.endswith("completed") else "in_progress",
                        "model": "fixture", "output": []}}
                self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), Reply) as http:
        Thread(target=http.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{http.server_port}/v1"
            provider = ('model_providers.fixture={name="fixture",wire_api="responses",'
                        f'base_url="{url}",requires_openai_auth=false}}')
            args = ["-c", provider, "-c", 'model_provider="fixture"', "-c", 'model="fixture"',
                    "exec", "--ephemeral", "--dangerously-bypass-hook-trust", "Inspect example."]
            subprocess.run(cli + args, cwd=project, env=env, check=True, timeout=60)
        finally:
            http.shutdown()


def main(executable, bundle):
    with TemporaryDirectory(ignore_cleanup_errors=True) as temp:
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
        cli = [executable, "--no-daemon", "-C", str(project)]
        for args in (["plugin", "marketplace", "add", str(catalog)],
                     ["plugin", "add", "between-the-lines@between-the-lines"]):
            subprocess.run(cli + args, cwd=project, env=env, check=True, timeout=60)
        submit_prompt(cli, env, project)
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
                    "tool": "lookup_intent", "arguments": {"path": str(project / "example.py"), "symbol": "example"}})
                assert not result.get("isError"), result
                value = json.loads(result["content"][0]["text"])
                assert (value["symbol"], value["status"]) == ("example", "missing"), value
                source = "def ascii_name(name): return name.encode('ascii')\n"
                (project / "names.py").write_text(source, encoding="utf-8")
                digest = sha256(ast.dump(ast.parse(source).body[0]).encode()).hexdigest()
                (project / "intent.json").write_text(json.dumps({"names.py": {"ascii_name": {
                    "intent": "Encode names as ASCII.", "ast_hash": "sha256:" + digest}}}), encoding="utf-8")
                search = {"threadId": session["thread"]["id"], "server": "between-the-lines",
                          "tool": "search_intent", "arguments": {"workspace": str(project),
                          "query": "find function that parses names into ascii"}}
                def candidates(number):
                    reply = request(number, "mcpServer/tool/call", search)
                    assert not reply.get("isError"), reply
                    return json.loads(reply["content"][0]["text"])["candidates"]
                hits = candidates(4)
                assert [hit["symbol"] for hit in hits] == ["ascii_name"], hits
                assert hits[0]["cosine_similarity"] > 0, hits
                (project / "names.py").write_text(source.replace("ascii')", "utf8')"), encoding="utf-8")
                assert candidates(5) == []
                source = "def first(): return 1\ndef second(): return 2\n"
                (project / "names.py").write_text(source, encoding="utf-8")
                entries = {}
                for node, intent in zip(ast.parse(source).body, ["Car.", "Banana."]):
                    digest = sha256(ast.dump(node).encode()).hexdigest()
                    entries[node.name] = {"intent": intent, "ast_hash": "sha256:" + digest}
                (project / "intent.json").write_text(json.dumps({"names.py": entries}), encoding="utf-8")
                search["arguments"]["query"] = "automobile"
                assert candidates(6)[0]["symbol"] == "first"
            finally:
                server.stdin.close()
                try:
                    server.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__ == "__main__":
    main(*sys.argv[1:])
