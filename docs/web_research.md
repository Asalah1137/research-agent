# Web research diagnostics

Search, HTTP access, and text extraction can fail independently. The tools log `stage=...` messages and identify where a failure occurred. Logs go to stderr; stdout remains reserved for the MCP protocol or CLI report.

## Search: empty results versus failures

| Result / log | Interpretation |
| --- | --- |
| `[]`, `stage=search.empty` | DDGS returned an empty list normally. This describes that call, not proof that no relevant pages exist on the web. |
| `reason=no_results_unknown` | DDGS raised its generic “No results found” exception. It did not distinguish absent matches from backend/parser problems. |
| `reason=backend_error` | A backend/library exception occurred. See the error type and message. |
| `reason=timeout` | DDGS reported a timeout. |
| `reason=rate_limited` | Rate-limit retries were exhausted. |
| `reason=invalid_results` | Returned items did not contain usable URL fields. |
| `stage=mcp.transport_failed` | The local tool connection/request failed; this is a transport issue. |

Search errors keep an `error` field and add `stage` and `reason` strings. Normal empty returns and ambiguous no-result exceptions each get one broader-query retry in collection. Other backend failures are logged and skipped; they are not assumed to be bad query wording.

DDGS's automatic mode can try several engines, and its backend order can vary. One query may make several HTTP requests. INFO logging exposes backend status messages and engine errors when DDGS provides them. A backend returning HTTP 200 can still send an unexpected page that its parser cannot use.

To isolate one backend, set `SEARCH_BACKEND` to a DDGS backend name. For example, `wikipedia` checks one provider at a time; it restricts coverage and is not a universal reliability improvement. The default is `auto`.

## Page reading: where a failure happened

```text
fetch.request -> fetch.response -> content_type -> page_check -> extract -> returned text
```

- **`fetch.failed` / `fetch.network_error`:** connection, timeout, or HTTP access failure. HTTP 403 needs server-side access investigation; the tool does not retry it. HTTP 404/410 are also returned directly.
- **`fetch.retry`:** bounded retry for rate limits, selected transient 5xx statuses, or network/timeouts. `Retry-After` is honored within a 60-second cap.
- **`content_type.rejected`:** the response is not supported HTML. A missing Content-Type is accepted only when the body clearly starts as HTML.
- **`page_check.blocked`:** obvious Cloudflare/access-challenge metadata or titles were found, including challenges returned with HTTP 200.
- **`extract.retry`:** default extraction found no text, so Trafilatura is tried once with `favor_recall=True`.
- **`extract.empty`:** HTML was downloaded but no readable main text was recovered. Sparse markup and JavaScript-rendered articles are possible causes.
- **`extract.success`:** logs the strategy, extracted characters, returned characters, and whether text was truncated.

The reader sends an honest `ResearcherAgent/0.1` User-Agent and an HTML Accept header. These may help some servers recognize the request, but they do not grant access to restricted pages. Fetch error logs include a short title or plain-text error hint; they do not dump complete HTML or request headers.

The second extraction pass favors recall and may include more boilerplate. It is only attempted after an empty first extraction. Neither HTTPX nor Trafilatura executes JavaScript or signs into websites. A nonempty result is still evidence to review, not a guarantee that the complete article was recovered.

## Enable logs

The defaults in `.env.example` are:

```dotenv
LOG_LEVEL=INFO
SEARCH_BACKEND=auto
```

With the venv activated, run:

```powershell
python run_cli.py "your research question"
```

For a temporary diagnostic session:

```powershell
$env:LOG_LEVEL = "INFO"
$env:SEARCH_BACKEND = "wikipedia"
python run_cli.py "your research question"
```

Set `SEARCH_BACKEND` back to `auto` for default multi-backend search. Use `LOG_LEVEL=WARNING` for less console output. Streamlit diagnostics appear in the terminal that started the app.

## Observed in this investigation

- Automatic searches for `LangGraph` and `LangGraph architecture components` both returned results in the baseline run. Earlier empty responses were not permanent proof that these queries had no matches.
- With only the Wikipedia backend, `LangGraph` returned one result. A deliberately nonexistent query produced a backend HTTP 200 followed by DDGS's generic exception; the tool reported `no_results_unknown` instead of presenting it as a confirmed empty search.
- The official LangChain overview returned HTTP 200. Trafilatura recovered 1,944 characters; the page limit returned 1,000.
- The Wikipedia article returned HTTP 403 with a plain-text robot-policy refusal. The log identified `fetch` as the failure stage; Trafilatura was never called.

The tests also cover HTTP-200 challenge pages, JavaScript shells, missing Content-Type, extraction fallback, bounded retries, and failure-stage messages. Mocked responses do not establish that every live website is readable.
