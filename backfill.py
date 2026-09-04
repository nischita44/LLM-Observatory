import sqlite3, time
from evals import DETERMINISTIC_CHECKS, judge_groundedness
from main import save_evals

con = sqlite3.connect("traces.db")
rows = con.execute("""SELECT trace_id, prompt, response FROM traces
    WHERE trace_id NOT IN (
        SELECT DISTINCT trace_id FROM evals
        WHERE check_name='groundedness' AND score IS NOT NULL
    )""").fetchall()
con.close()

for trace_id, prompt, response in rows:
    results = [c(prompt, response) for c in DETERMINISTIC_CHECKS]
    results.append(judge_groundedness(prompt, response))
    save_evals(trace_id, results)
    print(trace_id[:8], [f'{r["check_name"]}:{r["passed"] if r["passed"] is not None else r["score"]}' for r in results])
    time.sleep(5)   # be gentle with the free tier — you've met the 503