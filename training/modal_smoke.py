import modal


app = modal.App("between-the-lines-smoke")


@app.function()
def ping() -> str:
    return "modal-ok"


@app.local_entrypoint()
def main() -> None:
    print(ping.remote())
