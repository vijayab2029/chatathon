# chatathon

## Gemini API configuration

Copy `.env.example` to `.env` at the repo root and add your key from
<https://aistudio.google.com/apikey>. `.env` is gitignored.

```
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.8-flash
GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite
```

`part3/src/insight/llm/gemini.py` loads the repo-root `.env` first, then
`part3/.env` (part3 wins), and the real process environment beats both.

## Rate limits

Free-tier quota is the binding constraint on this project, not cost.
Limits are enforced **per Google Cloud project, not per API key**, and daily
quotas reset at **midnight Pacific**. Exceeding any one dimension returns
`429 RESOURCE_EXHAUSTED`.

| Model | Free-tier RPD | Notes |
|---|---|---|
| `gemini-3.8-flash` | **~20/day** | Most capable free model. Quota is tiny. |
| `gemini-3.7-flash` / `3.6` / `3.5-flash` | ~20/day | Same bracket as 3.8. |
| `gemini-3.5-flash-lite` | **~500/day** | Best free throughput. Current fallback. |
| `gemini-3.1-flash-lite` | ~500/day | Equivalent bracket. |
| `gemini-2.5-flash` | ~250/day | Older generation. |
| `gemini-2.5-flash-lite` | ~1,000/day | **404s for keys created recently.** |
| `gemini-3.1-pro` | n/a | Paid tier only — no free quota. |

Sourcing caveat: Google removed the per-model RPM/TPM/RPD table from
<https://ai.google.dev/gemini-api/docs/rate-limits> and now exposes live limits
only in AI Studio. The Flash/Flash-Lite figures above are community-reported
(Sept 2026) and the 2.5-series figures date to Jan 2026. Treat them as
order-of-magnitude. **Authoritative per-project limits:**
<https://aistudio.google.com/app/ratelimits>

### Consequences for the insight engine

The Part 3 pipeline spends up to 4 agent calls (hypothesis, narrator, critic)
per analysis. At ~20 RPD, `gemini-3.8-flash` supports roughly **five full runs
per day** before the fallback chain takes over. Mitigations already in place:

- **Disk cache** keyed on `(model, prompt)` — re-running the demo costs zero calls.
- **Fallback chain** on 429: primary → `GEMINI_FALLBACK_MODEL` → Flash-Lite
  variants → deterministic non-LLM output.
- **Thinking tokens count against TPM.** A 5-token prompt to `gemini-3.8-flash`
  measured 62 total tokens, 56 of them thinking.

Run the demo with `--offline` while iterating so you arrive at the live demo
with quota intact.
