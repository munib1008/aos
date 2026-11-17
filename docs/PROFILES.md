# Policy profiles
`aos/config/*.yaml` are the generic defaults. A **profile** replaces whole config files and removes features at build time.

| Profile | For | Differences |
|---|---|---|
| `default` | personal / general projects | everything, incl. TCCM (local inference, free tiers, credits registry, budgets, cache, dashboard) |
| `dormakaba` | dormakaba projects | AI Platform model catalog only; no local runtimes, free tiers, personal API keys, credits registry, automatic model switching or external media APIs; model-advisor skill; policy guard; extended redaction rules |

Mechanics: `profiles/<p>/*.yaml` overlay `config/`; `profiles/<p>/skills|docs` are added; `build.yaml` lists excluded files (packaging decision); `<p>-policy.yaml` is the policy source of truth (changing it needs the policy owner's approval, so it is shipped exactly as supplied); `<!-- profile:x -->` blocks in Markdown keep or drop text per profile. Select at runtime with `AOS_PROFILE`, `.aos/profile`, or a baked `config/PROFILE`. Build: `python3 aos/packaging/package.py build --out dist`.
