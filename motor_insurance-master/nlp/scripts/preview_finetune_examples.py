"""
Dumps a readable sample of the LoRA fine-tuning dataset to a self-contained
HTML file for easy browsing (no need to parse raw JSONL by eye).

Usage:
    venv/Scripts/python.exe nlp/scripts/preview_finetune_examples.py [N] [split]

    N     - number of examples to sample (default 25)
    split - train | valid | test | all (default all, sampled proportionally)
"""

import html
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "nlp" / "data"
OUT_PATH = DATA_DIR / "finetune_preview.html"

RNG_SEED = 7


def load_examples(split):
    path = DATA_DIR / f"claims_finetune_{split}.jsonl"
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def render_example(idx, ex, split):
    system_msg, user_msg, assistant_msg = ex["messages"]
    claim_id = user_msg["content"].splitlines()[0].replace("CLAIM ", "")

    try:
        target = json.loads(assistant_msg["content"])
        target_pretty = json.dumps(target, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        target_pretty = assistant_msg["content"]

    return f"""
    <div class="example">
      <h2>#{idx} &mdash; {html.escape(claim_id)} <span class="split-tag">{split}</span></h2>
      <div class="cols">
        <div class="col">
          <h3>Input (structured claim context)</h3>
          <pre>{html.escape(user_msg["content"])}</pre>
        </div>
        <div class="col">
          <h3>Target output</h3>
          <pre class="json">{html.escape(target_pretty)}</pre>
        </div>
      </div>
    </div>
    """


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    split_arg = sys.argv[2] if len(sys.argv) > 2 else "all"

    rng = random.Random(RNG_SEED)

    if split_arg == "all":
        splits = ["train", "valid", "test"]
        per_split = max(1, n // len(splits))
        sampled = []
        for split in splits:
            exs = load_examples(split)
            for ex in rng.sample(exs, min(per_split, len(exs))):
                sampled.append((split, ex))
        rng.shuffle(sampled)
    else:
        exs = load_examples(split_arg)
        sampled = [(split_arg, ex) for ex in rng.sample(exs, min(n, len(exs)))]

    body = "\n".join(render_example(i + 1, ex, split) for i, (split, ex) in enumerate(sampled))

    html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Claims Fine-Tune Dataset Preview</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem; background: #f7f7f8; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .example {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.5rem; margin-bottom: 1.5rem; }}
  .example h2 {{ font-size: 1rem; margin: 0 0 0.75rem 0; }}
  .split-tag {{ font-size: 0.7rem; background: #e0e7ff; color: #3730a3; padding: 0.1rem 0.5rem; border-radius: 4px; vertical-align: middle; }}
  .cols {{ display: flex; gap: 1.5rem; }}
  .col {{ flex: 1; min-width: 0; }}
  .col h3 {{ font-size: 0.8rem; text-transform: uppercase; color: #666; margin-bottom: 0.4rem; }}
  pre {{ white-space: pre-wrap; word-break: break-word; background: #f3f3f4; padding: 0.75rem; border-radius: 6px; font-size: 0.8rem; line-height: 1.4; margin: 0; }}
  pre.json {{ background: #0f1117; color: #d4d4d4; }}
  @media (max-width: 900px) {{ .cols {{ flex-direction: column; }} }}
</style>
</head>
<body>
<h1>Claims Fine-Tune Dataset &mdash; {len(sampled)} sampled examples</h1>
{body}
</body>
</html>
"""

    OUT_PATH.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {len(sampled)} examples to {OUT_PATH}")


if __name__ == "__main__":
    main()
