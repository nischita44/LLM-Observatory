# LLM-Observatory
A monitoring and evaluation layer for LLM applications.

It sits between your code and an LLM API, traces every call, scores the responses, and can block bad ones before they reach the caller.

Built to answer a question most LLM apps skip: *how do you know the model is still behaving?*

<img width="2802" height="1282" alt="image" src="https://github.com/user-attachments/assets/71e295c2-57be-43a7-bc13-685df1d394ca" />

## What it does

- **Tracing** — every request records latency, token counts, model used, retry attempts, and errors to SQLite.
- **Evaluation** — three deterministic checks (refusal detection, format validity, length sanity) plus an LLM-as-judge groundedness score.
- **Two modes** — `monitor` returns the response immediately and evaluates asynchronously; `gate` runs the deterministic checks inline and blocks responses that fail.
- **Retry engine** — exponential backoff with jitter on transient failures, falling back to a smaller model after the primary is exhausted.
- **Dashboard** — Streamlit app with a prompt console and charts for latency percentiles, retry behaviour, and eval results.

## Architecture

```
client → FastAPI proxy → LLM API (Gemini)
              ↓
         SQLite (traces + evals)
              ↓
      Streamlit dashboard
```

<!-- TODO: replace with a diagram if you want one -->

**Files**

| File | Purpose |
|---|---|
| `main.py` | FastAPI proxy, trace persistence, mode handling |
| `llm_client.py` | Retry engine — classification, backoff, fallback |
| `evals.py` | Deterministic checks and the LLM judge |
| `dashboard.py` | Streamlit console + dashboard |
| `backfill.py` | Scores existing traces that have no evals |
| `sample.py` | Lists model names available to your API key |

## Monitor vs gate

The two modes exist because post-hoc evaluation has a real gap: if the check runs after the response is returned, the user already has the bad answer.

**Monitor** (the industry default — Langfuse, Arize, and similar tools work this way) returns immediately and evaluates in the background. Zero added latency; you accept that individual bad responses get through and buy visibility over time.

**Gate** runs the deterministic checks inline and fails closed — a failed check returns HTTP 502 with the reason, rather than delivering a flagged response. Cheap checks only; the judge stays asynchronous, because a second LLM call in the request path would roughly double latency.

The 502 body includes a `retryable` field distinguishing transient infrastructure failures from terminal ones, so the caller knows whether retrying makes sense:

```json
{
  "trace_id": "12cc4a28-...",
  "blocked": true,
  "retryable": false,
  "reasons": ["refusal: refusal language detected"]
}
```

## Findings

These came out of running the harness against real traffic. All numbers are from n=16 requests — small, and treated as such.

### 1. The LLM judge is inconsistent on under-specified cases

Two traces had identical empty responses. The judge scored one **5.0** ("the response is empty, so it makes no ungrounded claims") and the other **1.0** ("no response text was provided to evaluate").

Same input, opposite verdicts. Both readings are defensible — the rubric never said what to do with an empty response, so the judge improvised, and improvised differently each time. Each call is an independent generation with no memory of the previous one.

**Fix applied:** the judge is skipped entirely when a deterministic check has already established the response is empty. Deterministic checks handle what is decidable; the judge only sees cases that genuinely need judgment.

### 2. The judge collapses a graded scale into pass/fail

<img width="1378" height="844" alt="image" src="https://github.com/user-attachments/assets/808bf2ff-0bd3-4698-95fc-39d80d1811d8" />

Across all scored traces the judge used only **1** and **5**. Never 2, 3, or 4. A 1–5 rubric was requested; a binary decision was delivered, wearing a 1–5 costume.

Worth knowing before trusting a graded LLM judge: check whether the middle of your scale is ever used.

### 3. Retry converts failures into latency

<img width="1878" height="854" alt="image" src="https://github.com/user-attachments/assets/a4c30f91-877b-44f2-895f-a0bfb65903df" />


6 of 16 requests retried, average retry wait 2,272 ms. One request succeeded on its third attempt after 211 seconds.

That request is a success, not an error — the retry engine rescued it. But a 211-second success is worse than a fast failure for anything interactive. The P50/P95 split makes the trade visible: **P50 8,933 ms, P95 124,366 ms.**

Resilience isn't free; it's paid for in tail latency. Whether that's the right trade is a product decision — fine for a batch job, wrong for a chat interface.

### 4. Latency is dominated by waiting, not generating

The same prompt sent three times, single attempt each, no errors:

| Response tokens | Latency |
|---|---|
| 58 | 47,378 ms |
| 61 | 3,792 ms |
| 58 | 7,156 ms |

Identical work, 12× spread. Generation cost is effectively constant, so the variance is server-side wait time, not compute. Queueing on a free-tier API is the most plausible explanation, though confirming it would need TTFT instrumentation — queue time lands entirely in time-to-first-token.

<img width="2668" height="766" alt="image" src="https://github.com/user-attachments/assets/6d8acfee-923d-4b7c-91cd-d1ca4c51398e" />

### 5. Three failure classes, captured automatically

The harness recorded 404s (bad model name — client error, never retryable), 503s (model overloaded — transient, retryable), and successes, without any special handling. The 4xx/5xx distinction matters operationally: a rising 4xx rate means you broke something, a rising 5xx rate means your dependency did.

## Setup

<!-- TODO: fill in -->

```bash
# clone, install
pip install fastapi uvicorn google-genai streamlit pandas requests

# get a key from aistudio.google.com, then:
export GEMINI_API_KEY="your-key-here"

# check which models your key can call
python sample.py

# run (two terminals)
uvicorn main:app --reload
python -m streamlit run dashboard.py
```

Dashboard at `localhost:8501`, proxy at `localhost:8000`.

<!-- TODO: note which model names you configured in llm_client.py -->

## Usage

```bash
curl -X POST localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"prompt": "What is a write-ahead log?", "mode": "monitor"}'
```
<img width="2766" height="1566" alt="image" src="https://github.com/user-attachments/assets/f731f427-e335-4c90-a603-1aa7db452a17" />

<img width="1372" height="889" alt="image" src="https://github.com/user-attachments/assets/dce7af9c-f8d7-4245-b9ab-44130ad7956d" />

## Known limitations

**Refusal detection is keyword matching.** It catches "I cannot" and similar phrasings, but misses a model that declines with "that's not something I'd help with," and false-positives on legitimate uses like "I cannot guarantee." Cheap and fast, but shallow — which is why the judge layer exists for things keywords can't reach.

**Retries are counted, not budgeted.** The engine allows a fixed number of attempts with no bound on total elapsed time, which is how a request reached 211 seconds. A **deadline** — retry only while time remains in a fixed budget, and skip a retry that can't finish in what's left — would bound the caller's worst case. That's the next thing to build, and it can be measured against the traces already collected.

**No retry budget.** A per-request policy is fine when failures are isolated, but during a real outage every request retries and amplifies load on an already-failing service. A **retry budget** — capping retries as a fraction of traffic over a rolling window — fails fast instead of contributing to a retry storm. Less urgent here, since the dependency is a managed API with its own protections.

**Judge cost.** Every evaluated trace costs a second LLM call, doubling API traffic. Fine at this scale, expensive at production volume. Sampling — judging a fraction of traffic rather than all of it — would be the obvious next step.

**SQLite.** Fine for a single process at this volume. The dashboard re-reads the whole table on every interaction, which Streamlit's rerun model makes unavoidable and which would not survive real traffic.

**Small n.** All findings above come from 16 requests, including deliberate fault injection. They're directionally real but not statistically strong.

## Future work

- Deadline-based retries (bounded worst-case latency)
- TTFT instrumentation via streaming, to confirm the queueing hypothesis
- Judge sampling instead of judging every trace
- Semantic groundedness via embedding similarity against retrieved context, rather than asking a judge
- Point the harness at a self-hosted vLLM server to observe serving-side metrics directly
