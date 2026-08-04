"""
Generates paraphrased variants of the 623 unique synthetic claim narratives,
to reduce literal-template memorization when the fine-tuning dataset is
rebuilt with augmented narratives (see build_finetune_dataset.py's
--paraphrases flag).

Requires a reachable Ollama instance (see ollama_client.py) -- this sandbox
doesn't have one, so this script is meant to run on the GPU box / wherever
OLLAMA_BASE_URL actually resolves. It will fail fast with a clear error if
Ollama isn't reachable, rather than silently producing nothing.

Each of the 623 templates gets N paraphrases (default 5), asked to vary
phrasing/tone/length the way real claimants would (including some informal
English and Sheng/Swahili loanwords common in Kenyan usage) while preserving
every factual detail in the original sentence -- paraphrasing must not
invent or drop facts, since the target JSON labels were written against the
ORIGINAL narrative's facts.

Output: nlp/data/narrative_paraphrases.json
    {"<original template>": ["<paraphrase 1>", "<paraphrase 2>", ...], ...}

Usage:
    venv/Scripts/python.exe nlp/scripts/paraphrase_narratives.py [N_PER_TEMPLATE]
"""

import json
import sys
import time
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so `import ollama_client` resolves from repo root

from ollama_client import generate_json, OllamaError  # noqa: E402

EXCEL_PATH = REPO_ROOT / "Old_Mutual_Kenya_Motor_Claims_Synthetic_Data_1.xlsx"
OUT_PATH = REPO_ROOT / "nlp" / "data" / "narrative_paraphrases.json"

PARAPHRASE_PROMPT = """You are helping build training data for a Kenyan motor insurance claims system.

Below is a claimant's description of a vehicle incident. Write {n} different paraphrases of it,
as if written by different real claimants describing the SAME incident. Vary:
- sentence length and structure (some short and blunt, some more detailed)
- formality (some plain/informal English, occasionally a natural Swahili/Sheng word like
  "gari" for car or "accident" phrased as "ajali", where it would sound natural)
- level of detail (some claimants give more context, some less)

Do NOT change, add, or remove any factual detail (location, what was damaged, how it happened,
whether the vehicle was moving or parked, etc.) -- only vary how it's phrased. If the original
doesn't mention something, don't invent it.

Original narrative:
"{narrative}"

Return ONLY a JSON object: {{"paraphrases": ["...", "...", ...]}} with exactly {n} strings.
"""


def load_unique_narratives():
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb["ClaimNarrative"]
    it = iter(ws.iter_rows(values_only=True))
    next(it)
    texts = {row[2] for row in it}
    return sorted(texts)


def main():
    n_per_template = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    templates = load_unique_narratives()
    print(f"{len(templates)} unique narrative templates to paraphrase, {n_per_template} variants each")

    # Fail fast on a tiny smoke-test call rather than burning time on 623
    # templates before discovering Ollama isn't reachable.
    try:
        _ = generate_json(
            PARAPHRASE_PROMPT.format(n=2, narrative=templates[0]),
            retries=1, timeout=60,
        )
    except OllamaError as e:
        raise SystemExit(
            f"Ollama isn't reachable (OLLAMA_BASE_URL / OLLAMA_MODEL env vars) -- "
            f"this script must run wherever the self-hosted Ollama instance lives. "
            f"Underlying error: {e}"
        )

    results = {}
    existing = {}
    if OUT_PATH.exists():
        existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        print(f"Resuming -- {len(existing)} templates already done")

    for i, template in enumerate(templates):
        if template in existing and len(existing[template]) >= n_per_template:
            results[template] = existing[template][:n_per_template]
            continue

        prompt = PARAPHRASE_PROMPT.format(n=n_per_template, narrative=template)
        try:
            resp = generate_json(prompt, retries=2, timeout=90)
            paraphrases = resp.get("paraphrases", [])
            if len(paraphrases) < n_per_template:
                print(f"  [{i+1}/{len(templates)}] WARNING: got {len(paraphrases)}/{n_per_template} for: {template[:60]}...")
            results[template] = paraphrases
        except OllamaError as e:
            print(f"  [{i+1}/{len(templates)}] FAILED, keeping original only: {template[:60]}... ({e})")
            results[template] = [template]

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(templates)}] done, checkpointing...")
            OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    total_variants = sum(len(v) for v in results.values())
    print(f"\nWrote {len(results)} templates / {total_variants} total variants -> {OUT_PATH}")


if __name__ == "__main__":
    main()
