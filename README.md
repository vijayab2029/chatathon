# chatathon

Team Stress Insight Tool — WHOOP track, Chatathon 2026.

**ALL DATA IN THIS PROJECT IS SYNTHETIC.** No real biometrics and no real
calendars are used anywhere.

See [`part3/README.md`](part3/README.md) for the correlation & insight engine
and [`docs/superpowers/specs/`](docs/superpowers/specs/) for the design spec.

## Building the site's data

The website (`ui/`) reads exactly two files, and one script writes both:

```bash
python scripts/part5_site/build_site_data.py
./serve.sh        # then open http://localhost:8080/ui/
```

That runs the real chain — Part 1/2's `data/` → Part 3's pipeline → Part 4's
k-anonymity gate → `data/employee_insight.json` + `data/employer_view.json`.
It is offline and deterministic by default, so it costs no API quota.

Useful flags:

| Flag | Effect |
|---|---|
| `--as-of 2026-09-17` | which day the employee view calls "today". Defaults to the last day of data, which is a **Saturday** — an honest but very quiet hero card. `2026-09-17` is the Thursday peak the demo script narrates. |
| `--person user_104` | whose private view to build (default `user_101`, the trajectory case) |
| `--skip-part3` | reuse `part3/data/out/` instead of re-running the engine |
| `--llm` | use the OpenAI agents instead of deterministic narration |
| `--fallback` | also run `node ui/build-fallback.mjs`, so the demo works over `file://` |

`scripts/part5_site/test_site_contract.py` asserts the generated JSON carries
every field `ui/app.js` actually dereferences. Nothing else in the repo fails
when the site and the pipeline drift — the page just renders `undefined` —
so run it after changing either side:

```bash
python -m pytest scripts/part5_site/test_site_contract.py
```

Nothing under `ui/` is written unless you pass `--fallback`. The site is
another owner's deliverable; we connect to it.

## LLM provider configuration

The project uses **OpenAI**. Copy `.env.example` to `.env` at the repo root and
add your key from <https://platform.openai.com/api-keys>. `.env` is gitignored.

```
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
OPENAI_FALLBACK_MODEL=gpt-4o
```

`part3/src/insight/llm/openai_client.py` loads the repo-root `.env` first, then
`part3/.env` (part3 wins), and the real process environment beats both.

The client is stdlib-only — it calls the REST endpoint through `urllib`, so
there is no `openai` package to install.

### Switching providers

Only `openai_client.py` is provider-specific. The three agents, the validator,
the pipeline and the API all talk to the provider-neutral `LLMClient` alias, so
a future swap means writing one new client and repointing that alias.

The previous Gemini implementation is preserved in git history at commit
`6fbb18f` if it is ever needed back.

## Rate limits

Quota is the binding constraint on this project, not cost. The pipeline spends
**3 agent calls per person** (hypothesis, narrator, critic), so a full 12-person
run costs ~36 calls.

Three mitigations are already in place, and they matter more than the specific
provider:

- **Disk cache** keyed on `(model, prompt)` under `.llm_cache/` — re-running the
  demo costs **zero** API calls. Run it once before judging and the live demo is
  free and instant.
- **Fallback chain** on 429/503: primary → `OPENAI_FALLBACK_MODEL` → exponential
  backoff → deterministic non-LLM output.
- **`--offline` flag**: the engine produces real, validated insights with no LLM
  at all. Baseline hypotheses still run, the validator still proves them, and
  template narration is built from the same validated numbers.

Run with `--offline` while iterating so you arrive at the demo with quota intact.

### A lesson worth keeping

An earlier Gemini configuration used `gemini-3.8-flash`, which carries only
**~20 requests/day** on the free tier. It was quota-exhausted, so every call
failed, burned three retries, then walked a fallback chain that itself contained
a retired model. One logical call cost **five** HTTP round trips, and a 3-person
run made 39 calls instead of 9.

Two takeaways that still apply to the OpenAI setup:

1. **Verify every model in the fallback chain actually works with your key.** A
   dead entry costs a wasted round trip on *every single call*. The chain exists
   to save you during a rate limit, not to burn quota faster.
2. **Count HTTP attempts, not logical calls.** `calls_made` counts attempts on
   purpose — it is what revealed the problem.
