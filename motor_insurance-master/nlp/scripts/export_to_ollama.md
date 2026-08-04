# From LoRA adapter to an Ollama-servable model

`train_lora.py` produces a PEFT LoRA *adapter* in
`nlp/models/claims-advisory-lora-v1/` -- a small set of delta weights, not a
full model. Ollama serves GGUF checkpoints, not HF/PEFT adapters directly, so
three steps are needed on the GPU box before `agents.py` / `ollama_client.py`
can call the fine-tuned model the same way they call the base one today.

## 1. Merge the adapter into the base model

```bash
python -c "
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

base = AutoModelForCausalLM.from_pretrained(
    'Qwen/Qwen2.5-7B-Instruct', torch_dtype=torch.bfloat16, device_map='cpu'
)
merged = PeftModel.from_pretrained(base, 'nlp/models/claims-advisory-lora-v1')
merged = merged.merge_and_unload()
merged.save_pretrained('nlp/models/claims-advisory-merged-v1')

tok = AutoTokenizer.from_pretrained('Qwen/Qwen2.5-7B-Instruct')
tok.save_pretrained('nlp/models/claims-advisory-merged-v1')
"
```

This produces a full-size fp16/bf16 HF checkpoint (~15GB for 7B) at
`nlp/models/claims-advisory-merged-v1/`.

## 2. Convert to GGUF (llama.cpp)

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
pip install -r requirements.txt

python convert_hf_to_gguf.py ../nlp/models/claims-advisory-merged-v1 \
    --outfile ../nlp/models/claims-advisory-v1.gguf \
    --outtype q4_k_m
```

`q4_k_m` matches the quantization level typically used for Ollama's own
published Qwen2.5 tags -- keeps inference speed/VRAM comparable to the base
model already in use. Use `f16` instead if you want to evaluate quality loss
from quantization before committing to it.

## 3. Import into Ollama

Create `nlp/models/Modelfile`:

```
FROM ./claims-advisory-v1.gguf

PARAMETER temperature 0.2
PARAMETER repeat_penalty 1.3
PARAMETER num_predict 1024
```

(The above mirrors `ollama_client.py`'s `DEFAULT_OPTIONS` so behavior matches
what's already tuned for structured-JSON output on this project.)

```bash
cd nlp/models
ollama create claims-advisory-v1 -f Modelfile
```

## 4. Point the app at it

Nothing in `ollama_client.py` needs to change -- it already reads
`OLLAMA_MODEL` from the environment:

```bash
export OLLAMA_MODEL=claims-advisory-v1
```

or pass `model="claims-advisory-v1"` explicitly to `generate()` /
`generate_json()` calls in `agents.py` for an A/B comparison against the base
model before fully cutting over.

## Before cutting over: evaluate against the held-out test set

`nlp/data/claims_finetune_test.jsonl` (2,500 examples, untouched by training)
exists exactly for this -- run both the base model and `claims-advisory-v1`
against its `user` messages, diff the JSON output against the `assistant`
target, and confirm the fine-tuned model's `recommended_action` /
`early_risk_indicator` fields align with the labels before relying on it for
anything. It should get noticeably better JSON-schema compliance and
Blueprint-terminology consistency than the base model's zero-shot output --
if it doesn't, that's a sign something in training (data formatting, chat
template mismatch, learning rate) needs revisiting before going further.
