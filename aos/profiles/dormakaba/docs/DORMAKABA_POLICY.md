# dormakaba policy profile

This package is the dormakaba build of AOS. `config/dormakaba-policy.yaml` is the source of truth; `scripts/policy_guard.py` enforces it and runs in the test suite.

## Removed features
| Feature | Reason |
|---|---|
| Credits and trials registry | Not an approved procurement path |
| Free-tier APIs, personal API keys | Bypass enterprise contracts; data and training terms unverified |
| Local model runtime (`core/compute`) | Unapproved software with mixed licenses |
| Automatic model switching | The user picks the model in the AI Platform picker |
| External media-generation APIs | Not approved; video uses local FFmpeg, brand assets come from Frontify |
| AOS 4.0 portable hardware-adaptive OS | Out of scope for dormakaba projects |

## Restricted features
- Handoff: only to AI Platform models or IT-approved enterprise accounts, always redacted.
- Chat-history import: only the user's own export, on explicit request.
- Cross-project memory: only `user_preference`, `reusable_pattern`, `lesson_general`.

## Always
- Never store or echo passwords, tokens, key system numbers or codes, card codes, TAN codes, security answers.
- Write "dormakaba" in lowercase.
- Branded deliverables follow the dormakaba brand and document skills.

## Model catalog
`config/models.yaml` mirrors `skills/model-advisor/references/model-catalog.md` (dated 2026-11-16). Update both when the catalog changes.
