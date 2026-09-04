import re, json, os
from google import genai


MODEL = "gemini-3.6-flash"
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

REFUSAL_PATTERNS = [r"i cannot", r"i can't", r"i'm not able", r"i am not able",
    r"i won't be able", r"unable to (help|assist|provide)",]


def check_refusal(prompt, response):
    hit = any(re.search(p, response.lower()) for p in REFUSAL_PATTERNS)
    return {"check_name": "refusal", "passed": 0 if hit else 1, "score": None,
            "detail": "refusal language detected" if hit else ""}  


def check_length(prompt, response):
    n = len(response.strip())
    if n == 0:
        return {"check_name": "length", "passed": 0, "score": None, "detail": "empty response"}
    if n > 8000:
        return {"check_name": "length", "passed": 0, "score": None, "detail": f"suspiciously long: {n} chars"}
    return{ "check_name": "length", "passed": 1, "score": None, "detail": f"{n} chars"}
      

def check_format(prompt, response):
    if "just the number" in prompt.lower():
        ok = bool(re.fullmatch(r"[\s\d,.\-]+", response.strip()))
        return {"check_name": "format", "passed": 1 if ok else 0, "score": None,
                "detail": "" if ok else "non-numeric content despite 'just the number'"}
    return {"check_name": "format", "passed": 1, "score": None, "detail": "no format constraint"}


DETERMINISTIC_CHECKS = [check_refusal, check_length, check_format]


JUDGE_PROMPT = """You are grading an AI response for roundedness.
Question: {prompt}
Response: {response} 
Score 1-5: 5 = every factual claim s well established r supported; 1 = contains invented specifics.
Return ONLY JSON, no markdown fences: {{"score": <1-5> , "reason": "<one sentence>"}}"""


def judge_groundedness(prompt, response):
    try:
        r = client.models.generate_content(
            model=MODEL,
            contents=JUDGE_PROMPT.format(prompt=prompt, response=response))
        text = r.text.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(text)
        return {"check_name": "groundedness", "passed": None,
                "score": float(data["score"]), "detail": data.get("reason", "")}
    except Exception as e:
        return {"check_name": "groundedness", "passed": None, "score": None,
                "detail": f"judge_error: {e}"}       