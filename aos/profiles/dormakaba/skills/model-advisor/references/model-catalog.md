# AI Platform Model Catalog

Catalog date: 2026-11-16
Source: AI Platform model picker (screenshot). Intelligence and cost values are the platform's own ratings. Cost is relative; higher means more expensive.
Owner: update this file when models are added, removed, or repriced, then share a new package.

## Models

| Model | Provider family | Intelligence | Cost multiplier | Tier |
|---|---|---|---|---|
| GPT-6 Luna | GPT | 76 | 0.09x | Economy |
| DeepSeek 4.1 Flash | DeepSeek | 79 | 0.16x | Economy |
| GPT-6.1 Terra | GPT | 84 | 0.6x | Value |
| GPT-6.1 Sol | GPT | 89 | 1.2x | Strong |
| GLM 5.3 | GLM | 85 | 1.2x | Strong |
| Claude Sonnet 5.5 | Claude | 85 | 1.4x | Strong |
| Kimi K3 | Kimi | 88 | 2.09x | Premium |
| Claude Opus 5.5 | Claude | 90 | 2.7x | Premium |

Tier rule: Economy up to 0.2x, Value up to 0.8x, Strong up to 1.5x, Premium above 1.5x.

## In-Chat Switching

| Family | Switchable models after the chat starts | Status |
|---|---|---|
| Claude | Claude Opus 5.5 and Claude Sonnet 5.5 | Verified (2026-11-16) |
| GPT | GPT-6 Luna, GPT-6.1 Terra, GPT-6.1 Sol | Unverified; test before relying on it |
| DeepSeek | DeepSeek 4.1 Flash only | Unverified |
| GLM | GLM 5.3 only | Unverified |
| Kimi | Kimi K3 only | Unverified |

To verify: start a chat on one model of the family, send a message, open the model picker, and record which models remain selectable.

## Notes

- Intelligence scores are general ratings, not results on dormakaba tasks. Teams should spot-check important workflows, such as branded decks or documents built with dormakaba skills, on the recommended model before standardizing.
