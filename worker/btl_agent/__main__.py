"""NDJSON process boundary. No source is written or executed."""

import json
import sys


MAX_REQUEST_BYTES = 2 * 1024 * 1024


def serve(input_stream=None, output_stream=None, backend=None):
    from .agent import Agent
    from .providers import RoutingBackend
    from .types import WorkerError

    stream = input_stream if input_stream is not None else sys.stdin.buffer
    output = output_stream if output_stream is not None else sys.stdout
    engine = Agent(backend if backend is not None else RoutingBackend())
    while True:
        line = stream.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            break
        request_id = None
        stop = False
        try:
            if len(line) > MAX_REQUEST_BYTES:
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_REQUEST_BYTES + 1)
                raise WorkerError("request_too_large", "Maximum request size is 2 MiB.")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise WorkerError("invalid_request", "Request must be an object.")
            request_id = request.get("id")
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                raise WorkerError("invalid_request", "Request id must be a string or integer.")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise WorkerError("invalid_request", "params must be an object.")
            if method == "configure":
                result = engine.configure(params)
            elif method == "status":
                result = engine.backend.status()
            elif method == "analyze":
                result = engine.analyze(params)
            elif method == "annotateObserved":
                result = engine.annotate_observed(params)
            elif method == "retireAnnotation":
                from .automatic import retire_annotation

                result = retire_annotation(params)
            elif method in {"recordRequirements", "knownRequirements", "retireRequirement"}:
                from .history import History

                inbox = params.get("inbox")
                if not isinstance(inbox, str) or not inbox:
                    raise WorkerError("invalid_request", "Requirement history needs a local inbox path.")
                history = History(inbox)
                try:
                    if method == "recordRequirements":
                        records = params.get("records")
                        if not isinstance(records, list) or len(records) > 64:
                            raise WorkerError("invalid_request", "Requirement records must be a bounded list.")
                        result = {"records": [history.record_requirement(record) for record in records]}
                    elif method == "knownRequirements":
                        result = {"records": history.requirements_for_paths(params.get("paths", []), params.get("workspaceRoot", params.get("workspace")))}
                    else:
                        history.retire_requirement(params.get("requirementId"), params.get("workspaceRoot"))
                        result = {"retired": True}
                except (ValueError, TypeError, OSError):
                    raise WorkerError("history_unavailable", "The requirement history could not validate this operation.") from None
            elif method == "unload":
                engine.backend.unload()
                result = {"modelLoaded": False}
            elif method == "shutdown":
                engine.backend.unload()
                result = {"stopped": True}
                stop = True
            else:
                raise WorkerError("unknown_method", "Unsupported worker method.")
            response = {"id": request_id, "result": result}
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            response = {"id": request_id, "error": {"code": "invalid_request", "message": "Request is not valid bounded JSON."}}
        except WorkerError as exc:
            response = {"id": request_id, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            # Diagnostics never include source text, task prompts, or paths.
            print(f"Worker failure: {type(exc).__name__}", file=sys.stderr)
            response = {"id": request_id, "error": {"code": "internal_error", "message": "The worker could not complete this request."}}
        output.write(json.dumps(response, ensure_ascii=True) + "\n")
        output.flush()
        if stop:
            break
    engine.backend.unload()


def main():
    if "--capture-hook" in sys.argv[1:]:
        # Hook capture must not import parsers or inference dependencies.
        from .capture import main as capture_main
        capture_main(sys.argv[1:])
        return
    serve()


if __name__ == "__main__":
    main()
