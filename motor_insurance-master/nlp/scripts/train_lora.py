"""
LoRA fine-tune of Qwen2.5-7B-Instruct (the same model family already served
via Ollama in this project -- see ollama_client.py's OLLAMA_MODEL default)
on the claims AI-advisory dataset built by build_finetune_dataset.py.

This is NOT runnable in the sandbox that prepared the dataset -- it needs a
CUDA GPU with ~24GB VRAM (4-bit QLoRA) and the packages in
nlp/requirements-train.txt. Run it on the GPU box after rsyncing this repo
over (nlp/data/*.jsonl + this script).

After training, the adapter needs merging + GGUF conversion before Ollama
can serve it -- see nlp/scripts/export_to_ollama.md for those steps.

Usage (on the GPU box):
    pip install -r nlp/requirements-train.txt
    python nlp/scripts/train_lora.py

Verified against trl 1.9.2 / transformers 5.14.1 / peft 0.19.1 (installed
directly on the GPU box this was run on) via `inspect.signature` before
committing to a full run -- SFTConfig uses `max_length` (not the older
`max_seq_length`), everything else below matched as originally written.
If running against a different trl version and it rejects an argument,
check `pip show trl` and that version's SFTConfig signature first -- it's
very likely just a renamed/relocated kwarg, not a sign the approach is wrong.
"""

from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "nlp" / "data"
OUT_DIR = REPO_ROOT / "nlp" / "models" / "claims-advisory-lora-v1"
MAX_SEQ_LEN = 2048


def main():
    print(f"Loading base model: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, quantization_config=bnb_config, device_map="auto",
    )
    model = prepare_model_for_kbit_training(model)

    # Standard target modules for Qwen2 architecture attention + MLP blocks.
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    print("Loading dataset...")
    dataset = load_dataset(
        "json",
        data_files={
            "train": str(DATA_DIR / "claims_finetune_train.jsonl"),
            "validation": str(DATA_DIR / "claims_finetune_valid.jsonl"),
        },
    )

    def format_example(ex):
        return {"text": tokenizer.apply_chat_template(ex["messages"], tokenize=False)}

    dataset = dataset.map(format_example, remove_columns=dataset["train"].column_names)

    sft_config = SFTConfig(
        output_dir=str(OUT_DIR),
        num_train_epochs=2,
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=25,
        save_strategy="epoch",
        eval_strategy="epoch",
        bf16=True,
        max_length=MAX_SEQ_LEN,
        dataset_text_field="text",
        packing=False,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        processing_class=tokenizer,
    )

    print("Training...")
    trainer.train()

    print(f"Saving adapter + tokenizer to {OUT_DIR}")
    trainer.save_model(str(OUT_DIR))
    tokenizer.save_pretrained(str(OUT_DIR))
    print("Done. See nlp/scripts/export_to_ollama.md for merge + GGUF conversion steps.")


if __name__ == "__main__":
    main()
