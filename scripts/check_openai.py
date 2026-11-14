"""Quick check of the OpenAI key and model names. Run: python scripts/check_openai.py"""
import os, sys
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass
key = os.getenv("OPENAI_API_KEY", "")
print("Key found:", bool(key), f"(starts {key[:7]}..., length {len(key)})" if key else "")
import openai
client = openai.OpenAI(api_key=key)
try:
    ids = sorted(m.id for m in client.models.list())
    print("Key works. GPT models this key can use:")
    for i in ids:
        if i.startswith("gpt-"):
            print("  ", i)
except Exception as e:
    sys.exit(f"Key check failed: {type(e).__name__}: {e}")
for m in sys.argv[1:] or ["gpt-6-luna", "gpt-6.1-sol"]:
    try:
        r = client.responses.create(model=m, input="Reply with OK", max_output_tokens=2000)
        print(f"{m}: OK ({r.status})")
    except Exception as e:
        print(f"{m}: FAILED -> {type(e).__name__}: {e}")
