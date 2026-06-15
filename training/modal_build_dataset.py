from pathlib import Path
import json
import random

import modal


app = modal.App("between-the-lines-data")

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "datasets==2.21.0",
    "huggingface_hub<1.0",
).add_local_dir("training", remote_path="/root/training")
volume = modal.Volume.from_name("between-the-lines-data", create_if_missing=True)


@app.function(
    image=image,
    volumes={"/data": volume},
    timeout=60 * 60,
)
def build_codesearchnet_dataset(limit: int = 10_000, eval_size: int = 500, seed: int = 42) -> dict[str, str | int]:
    from training.build_dataset import convert_row, write_jsonl
    from datasets import load_dataset

    dataset = load_dataset("code_search_net", "python", split="train", trust_remote_code=True)
    dataset = dataset.shuffle(seed=seed)

    rows = []
    seen = set()
    for raw_row in dataset:
        row = convert_row(raw_row)
        if not row:
            continue

        line_count = row["metadata"]["line_count"]
        if line_count < 3 or line_count > 40:
            continue

        if row["id"] in seen:
            continue

        seen.add(row["id"])
        rows.append(row)

        if len(rows) >= limit + eval_size:
            break

    eval_rows = rows[:eval_size]
    train_rows = rows[eval_size:]

    train_path = Path("/data/processed/codesearchnet_python_comments.jsonl")
    eval_path = Path("/data/eval/codesearchnet_python_comments_eval.jsonl")
    write_jsonl(train_path, train_rows)
    write_jsonl(eval_path, eval_rows)
    volume.commit()

    return {
        "train_rows": len(train_rows),
        "eval_rows": len(eval_rows),
        "train_path": str(train_path),
        "eval_path": str(eval_path),
    }


@app.function(
    image=image,
    volumes={"/data": volume},
    timeout=5 * 60,
)
def preview_dataset(path: str = "/data/processed/codesearchnet_python_comments.jsonl", count: int = 3) -> list[str]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as file:
        for _ in range(count):
            line = file.readline()
            if not line:
                break
            rows.append(line)
    return rows


@app.function(
    image=image,
    volumes={"/data": volume},
    timeout=5 * 60,
)
def sample_review_rows(
    path: str = "/data/processed/codesearchnet_python_comments.jsonl",
    count: int = 20,
    seed: int = 13,
) -> list[dict[str, str | int]]:
    with Path(path).open("r", encoding="utf-8") as file:
        rows = [json.loads(line) for line in file if line.strip()]

    rng = random.Random(seed)
    sample = rng.sample(rows, min(count, len(rows)))
    review_rows = []
    for row in sample:
        code = row["code"]
        review_rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "line_count": row["metadata"]["line_count"],
                "completion": row["completion"],
                "code": code[:900] + ("..." if len(code) > 900 else ""),
            }
        )
    return review_rows


@app.local_entrypoint()
def main(
    limit: int = 10_000,
    eval_size: int = 500,
    seed: int = 42,
    preview: bool = False,
    review: bool = False,
    review_count: int = 20,
) -> None:
    if review:
        for index, row in enumerate(sample_review_rows.remote(count=review_count, seed=seed), start=1):
            print(f"\n--- SAMPLE {index} ---")
            print(f"{row['kind']} {row['name']} ({row['line_count']} lines)")
            print(row["completion"])
            print(row["code"])
        return

    if preview:
        for row in preview_dataset.remote():
            print(row)
        return

    result = build_codesearchnet_dataset.remote(limit=limit, eval_size=eval_size, seed=seed)
    print(result)
