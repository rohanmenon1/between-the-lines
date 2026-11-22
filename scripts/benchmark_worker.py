"""Measure a frozen local worker. Results describe execution, not comment quality."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import subprocess
import threading
import time


def process_usage(process):
    """Return current RSS bytes and accumulated CPU seconds on Windows/Linux."""
    if os.name == "nt":
        from ctypes import wintypes

        class Memory(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak", "rss", "peakPaged", "paged", "peakNonpaged", "nonpaged", "pagefile", "peakPagefile")]
        memory = Memory()
        memory.cb = ctypes.sizeof(memory)
        handle = wintypes.HANDLE(int(process._handle))
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            raise OSError("Could not read worker memory usage.")
        creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError("Could not read worker CPU usage.")
        ticks = lambda value: (value.dwHighDateTime << 32) + value.dwLowDateTime
        return memory.rss, (ticks(kernel) + ticks(user)) / 10_000_000, memory.peak
    if platform.system() == "Linux":
        status = Path(f"/proc/{process.pid}/status").read_text()
        rss = next(int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith("VmRSS:"))
        fields = Path(f"/proc/{process.pid}/stat").read_text().rpartition(")")[2].split()
        return rss, (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"), rss
    return None, None, None


def hardware():
    result = {"os": platform.platform(), "architecture": platform.machine(), "cpu": platform.processor(), "logicalCpus": os.cpu_count()}
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            result["cpu"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total", "available", "pageTotal", "pageAvailable", "virtualTotal", "virtualAvailable", "extended")]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            result["physicalMemoryBytes"] = status.total
    return result


class Worker:
    def __init__(self, executable, diagnostic_path):
        self.log = diagnostic_path.open("wb")
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.responses = queue.Queue()
        self.stop = threading.Event()
        self.samples = []
        self.started = time.perf_counter()
        self.sequence = 0
        threading.Thread(target=self.read_responses, daemon=True).start()
        self.sampler = threading.Thread(target=self.sample, daemon=True)
        self.sampler.start()

    def read_responses(self):
        try:
            while line := self.process.stdout.readline(2 * 1024 * 1024 + 1):
                if len(line) > 2 * 1024 * 1024:
                    raise ValueError("Worker response exceeds 2 MiB.")
                self.responses.put(json.loads(line))
            self.responses.put(RuntimeError("Worker stdout closed."))
        except Exception as error:
            self.responses.put(error)

    def sample(self):
        while not self.stop.is_set():
            try:
                rss, cpu, peak = process_usage(self.process)
                self.samples.append({"elapsedMs": round((time.perf_counter() - self.started) * 1000), "rssBytes": rss,
                                     "cpuSeconds": cpu, "peakRssBytes": peak})
            except (OSError, StopIteration, ProcessLookupError):
                pass
            self.stop.wait(0.1)

    def request(self, method, params=None):
        self.sequence += 1
        request = {"id": str(self.sequence), "method": method, "params": params or {}}
        started = time.perf_counter()
        self.process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        response = self.responses.get(timeout=120)
        if isinstance(response, Exception):
            raise response
        if response.get("id") != request["id"]:
            raise ValueError("Worker response id does not match request.")
        return {"wallMs": round((time.perf_counter() - started) * 1000), "response": response}

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.stop.set()
        self.sampler.join(timeout=1)
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("BTL_MODEL_PATH"), required=not os.environ.get("BTL_MODEL_PATH"))
    parser.add_argument("--worker", default=os.environ.get("BTL_WORKER_PATH"), required=not os.environ.get("BTL_WORKER_PATH"))
    parser.add_argument("--output", default=os.environ.get("BTL_BENCHMARK_OUTPUT", "benchmark-results.json"))
    args = parser.parse_args()
    model, executable, output = (Path(value).resolve() for value in (args.model, args.worker, args.output))
    output.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    with model.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    report = {"hardware": hardware(), "model": {"path": str(model), "sizeBytes": model.stat().st_size, "sha256": hasher.hexdigest()},
              "worker": str(executable), "measurement": "Worker process RSS and CPU time; excludes VS Code and OS file cache. No energy measurement.", "runs": {}}
    worker = Worker(executable, output.with_suffix(".stderr.log"))
    python = {"uri": "file:///benchmark/retry.py", "language": "python", "source": "def retry_delay(status, attempt):\n    if status == 429:\n        return min(60, 2 ** attempt)\n    return None\n",
              "intent": {"source": "manual", "prompt": "Retry rate-limited requests with exponential backoff capped at 60 seconds; do not retry other statuses."}, "maxSuggestions": 1}
    java = {"uri": "file:///benchmark/Label.java", "language": "java", "source": "import java.util.Locale;\nclass Label {\n    static String normalize(String raw) {\n        if (raw == null) return \"\";\n        return raw.strip().toLowerCase(Locale.ROOT);\n    }\n}\n",
            "intent": {"source": "manual", "prompt": "Normalize lookup keys independently of the machine's locale, treating null as empty."}, "maxSuggestions": 1}
    obvious = {"uri": "file:///benchmark/add.py", "language": "python", "source": "def add(a, b):\n    return a + b\n", "maxSuggestions": 1}
    try:
        report["runs"]["configure"] = worker.request("configure", {"modelPath": str(model), "threads": 2, "contextTokens": 2048, "maxOutputTokens": 160, "idleSeconds": 2})
        for name, case in (("python_retry", python), ("obvious_function", obvious), ("java_normalization", java), ("obvious_cached", obvious)):
            report["runs"][name] = {"request": case, **worker.request("analyze", case)}
        report["runs"]["before_idle"] = worker.request("status")
        time.sleep(2.3)
        report["runs"]["after_idle"] = worker.request("status")
        report["runs"]["explicit_unload"] = worker.request("unload")
        report["runs"]["after_unload"] = worker.request("status")
        report["runs"]["shutdown"] = worker.request("shutdown")
        results = {name: run["response"].get("result", {}) for name, run in report["runs"].items()}
        report["qualityObservations"] = {name: {
            "proposals": result.get("suggestions", []), "skipped": result.get("skipped", []),
            "invalidOutputCount": sum("invalid_model_output:" in item["reason"] for item in result.get("skipped", [])),
            "rejectedDecisionCount": sum(item["reason"].startswith(("invalid_model_output:", "unsafe_proposal:", "context_limit:", "context_denied:", "context_unavailable:", "inference_failed:", "tool_limit:")) for item in result.get("skipped", [])),
            "commentAccuracy": "Not rated; requires independent human review.",
        } for name, result in results.items() if "suggestions" in result}
        original_valid = report["qualityObservations"].get("obvious_function", {}).get("rejectedDecisionCount") == 0
        report["checks"] = {
            "noProtocolErrors": all("error" not in run["response"] for run in report["runs"].values()),
            "cacheCheckApplicable": original_valid,
            "cachedWithoutInference": results["obvious_cached"].get("metrics", {}).get("modelCalls") == 0 if original_valid else None,
            "idleUnloaded": results["after_idle"].get("modelLoaded") is False,
            "explicitlyUnloaded": results["after_unload"].get("modelLoaded") is False,
            "cleanShutdown": results["shutdown"].get("stopped") is True,
        }
        if any(value is False for key, value in report["checks"].items() if key != "cacheCheckApplicable"):
            report["error"] = "One or more lifecycle/cache checks failed; inspect exact responses."
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        worker.close()
        report["samples"] = worker.samples
        report["peakRssBytes"] = max((sample["peakRssBytes"] for sample in worker.samples if sample["peakRssBytes"] is not None), default=None)
        report["cpuSeconds"] = max((sample["cpuSeconds"] for sample in worker.samples if sample["cpuSeconds"] is not None), default=None)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
