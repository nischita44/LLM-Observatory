import streamlit as st, sqlite3, pandas as pd, requests, time

DB = "traces.db"
API = "http://localhost:8000/chat"

st.set_page_config(page_title="LLM Observatory", layout="wide")
st.title("LLM Observatory")

def load(query):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(query, con)
    con.close()
    return df

tab_console, tab_dash = st.tabs(["Console", "Dashboard"])

# ---------------- CONSOLE ----------------
with tab_console:
    st.subheader("Send a prompt through the proxy")
    prompt = st.text_area("Prompt", "What is a write-ahead log, in two sentences?", height=100)
    mode = st.radio("Mode", ["monitor", "gate"], horizontal=True,
                    help="monitor = respond then eval async · gate = block on failed checks")

    if st.button("Send", type="primary"):
        with st.spinner("Calling the proxy… (retries can take ~30s)"):
            try:
                r = requests.post(API, json={"prompt": prompt, "mode": mode}, timeout=120)
                body = r.json()
                if r.status_code == 502:
                    st.error(f"BLOCKED (retryable={body.get('retryable')})")
                    for reason in body.get("reasons", []):
                        st.write(f"• {reason}")
                else:
                    st.success(f"{body.get('latency_ms')} ms · "
                               f"attempts={body.get('attempts')} · "
                               f"fallback={body.get('fallback_used')}")
                    st.markdown(body.get("response", ""))
                st.caption(f"trace_id: {body.get('trace_id')}")
                st.info("Evals run in the background — check the Dashboard in a few seconds.")
            except Exception as e:
                st.error(f"Request failed: {e}")

# ---------------- DASHBOARD ----------------
with tab_dash:
    if st.button("Refresh"):
        st.rerun()

    traces = load("SELECT * FROM traces ORDER BY ts DESC")
    evals = load("SELECT * FROM evals")

    if traces.empty:
        st.warning("No traces yet — send a prompt from the Console tab.")
        st.stop()

    ok = traces[traces.error.isna()]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Requests", len(traces))
    c2.metric("Error rate", f"{(traces.error.notna().mean()*100):.0f}%")
    c3.metric("P50 latency", f"{ok.latency_ms.median():.0f} ms" if len(ok) else "—")
    c4.metric("P95 latency", f"{ok.latency_ms.quantile(0.95):.0f} ms" if len(ok) else "—")
    c5.metric("Fallback rescues", int(traces.fallback_used.sum()))

    st.subheader("Latency over time")
    lat = traces.sort_values("ts")[["ts", "latency_ms"]].set_index("ts")
    st.line_chart(lat)

    st.subheader("Latency vs response tokens")
    st.scatter_chart(ok, x="response_tokens", y="latency_ms")

    st.subheader("Retry behaviour")
    r1, r2 = st.columns(2)
    r1.bar_chart(traces.attempts.value_counts().sort_index())
    retried = traces[traces.attempts > 1]
    r2.metric("Requests that retried", len(retried))
    r2.metric("Avg retry wait", f"{retried.retry_wait_ms.mean():.0f} ms" if len(retried) else "—")

    st.subheader("Eval results")
    if evals.empty:
        st.info("No evals yet.")
    else:
        det = evals[evals.passed.notna()]
        e1, e2 = st.columns(2)
        with e1:
            st.caption("Deterministic pass rate by check")
            st.bar_chart(det.groupby("check_name").passed.mean())
        with e2:
            judge = evals[(evals.check_name == "groundedness") & evals.score.notna()]
            st.caption("Groundedness score distribution")
            if not judge.empty:
                st.bar_chart(judge.score.value_counts().sort_index())
            else:
                st.write("no judge scores yet")

    st.subheader("Worst responses")
    worst = load("""
        SELECT substr(t.prompt,1,60) AS prompt, e.check_name,
               e.passed, e.score, substr(e.detail,1,80) AS detail
        FROM traces t JOIN evals e ON t.trace_id = e.trace_id
        WHERE e.passed = 0 OR e.score <= 3
        ORDER BY t.ts DESC LIMIT 20""")
    st.dataframe(worst, width='stretch')

    st.subheader("Recent traces")
    st.dataframe(
        traces[["ts","prompt","latency_ms","response_tokens","attempts",
                "model_used","fallback_used","error"]].head(25),
        width='stretch')