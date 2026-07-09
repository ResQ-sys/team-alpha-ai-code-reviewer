"""
Build a small instruction-tuning dataset for LoRA fine-tuning a Code-LLM
towards *secure-coding recommendations*.

Sources:
  - rag/secure_coding_docs.json  (the same OWASP/CWE corpus the RAG uses)
  - a handful of hand-written vulnerable-code -> secure-fix examples

Output:
  finetune/data/train.jsonl
  finetune/data/eval.jsonl

Each line: {"instruction": "...", "input": "...", "output": "..."}

Run:  python finetune/prepare_data.py
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KB_PATH = os.path.join(ROOT, "rag", "secure_coding_docs.json")
OUT_DIR = os.path.join(HERE, "data")

SYSTEM = ("You are a secure-coding assistant. Given a security finding, explain "
          "why it is dangerous and how to fix it using established secure-coding "
          "best practice (OWASP / CWE).")

# A few concrete vulnerable -> fixed code pairs to teach the fix *format*.
CODE_EXAMPLES = [
    {
        "instruction": "Fix the SQL injection in this Python function.",
        "input": 'def get_user(db, name):\n    db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")',
        "output": ("Concatenating user input into SQL allows injection (CWE-89). "
                   "Use a parameterised query:\n"
                   'db.execute("SELECT * FROM users WHERE name = ?", (name,))'),
    },
    {
        "instruction": "Fix the hard-coded secret in this code.",
        "input": 'API_KEY = "sk_live_51H8xkL9..."',
        "output": ("Hard-coded credentials (CWE-798) leak in source control. "
                   "Load from the environment instead:\n"
                   'import os\nAPI_KEY = os.environ["API_KEY"]'),
    },
    {
        "instruction": "Fix the weak hashing in this code.",
        "input": "import hashlib\ndigest = hashlib.md5(password.encode()).hexdigest()",
        "output": ("MD5 is a broken algorithm for passwords (CWE-327). Use a slow, "
                   "salted KDF:\n"
                   "from hashlib import scrypt\n"
                   "digest = scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)"),
    },
    {
        "instruction": "Fix the insecure deserialization here.",
        "input": "import pickle\nobj = pickle.loads(untrusted_bytes)",
        "output": ("Unpickling untrusted data enables RCE (CWE-502). Use a safe "
                   "format such as JSON:\n"
                   "import json\nobj = json.loads(untrusted_text)"),
    },
    {
        "instruction": "Fix the command injection in this code.",
        "input": "import os\nos.system('ping ' + user_host)",
        "output": ("Passing user input to a shell allows command injection (CWE-78). "
                   "Use subprocess with an argument list and no shell:\n"
                   "import subprocess\nsubprocess.run(['ping', user_host], shell=False)"),
    },
]


def build_from_kb() -> list[dict]:
    with open(KB_PATH, "r", encoding="utf-8") as f:
        docs = json.load(f)
    rows = []
    for d in docs:
        rows.append({
            "instruction": f"A security finding was reported: {d['title']}. "
                           f"Explain the risk and the secure-coding fix.",
            "input": "",
            "output": d["text"],
        })
    return rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = build_from_kb() + CODE_EXAMPLES

    # deterministic split: last 4 rows to eval
    eval_rows = rows[-4:]
    train_rows = rows[:-4]

    with open(os.path.join(OUT_DIR, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in train_rows:
            f.write(json.dumps({**r, "system": SYSTEM}) + "\n")
    with open(os.path.join(OUT_DIR, "eval.jsonl"), "w", encoding="utf-8") as f:
        for r in eval_rows:
            f.write(json.dumps({**r, "system": SYSTEM}) + "\n")

    print(f"Wrote {len(train_rows)} train + {len(eval_rows)} eval examples to {OUT_DIR}")


if __name__ == "__main__":
    main()
