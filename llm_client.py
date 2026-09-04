import time, random, os
from google import genai

PRIMARY = "gemini-3.6-flash"
FALLBACK = "gemini-3.5-flash-lite"   # verify exact name in AI Studio
MAX_RETRIES = 2                       # 3 attempts total on primary

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

TRANSIENT_MARKERS = ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                     "DEADLINE_EXCEEDED", "timed out", "overloaded"]

def is_transient(err: str) -> bool:
    e = err.upper()
    return any(m.upper() in e for m in TRANSIENT_MARKERS)

def call_llm(prompt: str) -> dict:
    """Returns: text, p_tok, r_tok, model_used, attempts,
    fallback_used, retry_wait_ms, error (None on success)."""
    total_wait = 0.0
    attempts = 0
    last_err = None

    for attempt in range(1 + MAX_RETRIES):          # primary: up to 3 tries
        attempts += 1
        try:
            r = client.models.generate_content(model=PRIMARY, contents=prompt)
            return {"text": r.text,
                    "p_tok": r.usage_metadata.prompt_token_count,
                    "r_tok": r.usage_metadata.candidates_token_count,
                    "model_used": PRIMARY, "attempts": attempts,
                    "fallback_used": 0, "retry_wait_ms": round(total_wait * 1000),
                    "error": None}
        except Exception as e:
            last_err = str(e)
            if not is_transient(last_err):
                break                                # terminal: stop immediately
            if attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(0, 0.5)   # 1s, 2s + jitter
                total_wait += wait
                time.sleep(wait)

    if last_err and is_transient(last_err):          # fallback, one attempt
        attempts += 1
        try:
            r = client.models.generate_content(model=FALLBACK, contents=prompt)
            return {"text": r.text,
                    "p_tok": r.usage_metadata.prompt_token_count,
                    "r_tok": r.usage_metadata.candidates_token_count,
                    "model_used": FALLBACK, "attempts": attempts,
                    "fallback_used": 1, "retry_wait_ms": round(total_wait * 1000),
                    "error": None}
        except Exception as e:
            last_err = str(e)

    return {"text": "", "p_tok": 0, "r_tok": 0,
            "model_used": PRIMARY, "attempts": attempts, "fallback_used": 0,
            "retry_wait_ms": round(total_wait * 1000), "error": last_err}