import time, uuid, sqlite3, os
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai
from evals import DETERMINISTIC_CHECKS, judge_groundedness
from llm_client import call_llm

app = FastAPI()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

DB = "traces.db"

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS traces (
        trace_id TEXT PRIMARY KEY,
        ts REAL,
        prompt TEXT,
        response TEXT,
        latency_ms REAL,
        prompt_tokens INTEGER,
        response_tokens INTEGER,
        model TEXT,
        error TEXT,
        attempts INTEGER,
        model_used TEXT,
        fallback_used INTEGER,
        retry_wait_ms REAL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS evals (
        trace_id TEXT, check_name TEXT, passed INTEGER,
        score REAL, detail TEXT, ts REAL
    )""")
    con.commit(); con.close()

init_db()

def save_evals(trace_id, results):
    con = sqlite3.connect(DB)
    for r in results:
        con.execute("INSERT INTO evals VALUES (?,?,?,?,?,?)",
            (trace_id, r["check_name"], r["passed"], r["score"], r["detail"], time.time()))
    con.commit(); con.close()

def run_background_evals(trace_id, prompt, response, include_deterministic):
    results = []
    if include_deterministic:
        results += [c(prompt, response) for c in DETERMINISTIC_CHECKS]
    results.append(judge_groundedness(prompt, response))
    save_evals(trace_id, results)

GATING_CHECKS = {"refusal", "length", "format"}

class ChatRequest(BaseModel):
    prompt: str
    mode: str = "monitor"   # "monitor" | "gate"

@app.post("/chat")
def chat(req: ChatRequest, background: BackgroundTasks):
    trace_id = str(uuid.uuid4())
    start = time.time()

    result = call_llm(req.prompt)
    latency = (time.time() - start) * 1000
    text = result["text"]
    error = result["error"]

    con = sqlite3.connect(DB)
    con.execute("INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trace_id, start, req.prompt, text, latency,
         result["p_tok"], result["r_tok"], "gemini-3.6-flash", error,
         result["attempts"], result["model_used"],
         result["fallback_used"], result["retry_wait_ms"]))
    con.commit(); con.close()

    if req.mode == "gate":
        det = [c(req.prompt, text) for c in DETERMINISTIC_CHECKS]
        save_evals(trace_id, det)
        failed = [d for d in det if d["check_name"] in GATING_CHECKS and d["passed"] == 0]
        background.add_task(run_background_evals, trace_id, req.prompt, text, False)

        if failed:
            retryable = any(d["check_name"] == "length" for d in failed)
            return JSONResponse(status_code=502, content={
                "trace_id": trace_id, "blocked": True,
                "retryable": retryable,
                "reasons": [f'{d["check_name"]}: {d["detail"]}' for d in failed]})

        return {"trace_id": trace_id, "response": text,
                "latency_ms": round(latency), "gate": "passed",
                "attempts": result["attempts"],
                "fallback_used": result["fallback_used"]}

    background.add_task(run_background_evals, trace_id, req.prompt, text, True)
    return {"trace_id": trace_id, "response": text,
            "latency_ms": round(latency), "error": error,
            "attempts": result["attempts"],
            "fallback_used": result["fallback_used"]}