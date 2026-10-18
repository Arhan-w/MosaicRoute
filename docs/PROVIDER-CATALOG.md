# Built-in provider preset catalog

MosaicRoute v0.6.0 ships **71 configurable provider presets**. This catalog is a defaults loaded from `config/provider_catalog.json` (the local source of truth); presets are convenience endpoints, not live availability checks. Add or edit plain JSON presets in that file (unique `id`, `name`, `base_url`, and `protocol` are required). An active provider is queried for its real model list after setup. Free access and model lists can change.

| Provider | Base URL | Auth default | Access label | Discovery note |
|---|---|---|---|---|
| OpenAI (`openai`) | `https://api.openai.com/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Anthropic (`anthropic`) | `https://api.anthropic.com/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Google Gemini (`gemini`) | `https://generativelanguage.googleapis.com/v1beta/openai` | `api_key` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/v1beta/models (host)` |
| OpenRouter (`openrouter`) | `https://openrouter.ai/api/v1` | `auto` | `free-models-available` | `Only explicitly free models such as :free routes are intended to be zero-priced; verify live pricing. Path: `/models (base)` |
| Groq (`groq`) | `https://api.groq.com/openai/v1` | `auto` | `free-tier-advertised` | `Free-tier limits and eligible models may change; confirm current account limits. Path: `/models (base)` |
| Cerebras (`cerebras`) | `https://api.cerebras.ai/v1` | `auto` | `free-tier-advertised` | `Free-tier limits and eligible models may change; confirm current account limits. Path: `/models (base)` |
| DeepSeek (`deepseek`) | `https://api.deepseek.com` | `auto` | `unknown` | `No free inference is guaranteed by this preset; check current pricing. Path: `/models (base)` |
| Mistral (`mistral`) | `https://api.mistral.ai/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| xAI (`xai`) | `https://api.x.ai/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Perplexity (`perplexity`) | `https://api.perplexity.ai` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Together AI (`together`) | `https://api.together.xyz/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Fireworks AI (`fireworks`) | `https://api.fireworks.ai/inference/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Hugging Face Router (`huggingface`) | `https://router.huggingface.co/v1` | `auto` | `small-free-credit` | `A small recurring inference-credit allowance has been documented; verify the current account plan. Path: `/models (base)` |
| NVIDIA NIM (`nvidia`) | `https://integrate.api.nvidia.com/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| SiliconFlow (`siliconflow`) | `https://api.siliconflow.com/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| DeepInfra (`deepinfra`) | `https://api.deepinfra.com/v1/openai` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| SambaNova Cloud (`sambanova`) | `https://api.sambanova.ai/v1` | `auto` | `free-tier-retired` | `Free tier was retired; do not assume this provider is free. Path: `/models (base)` |
| Hyperbolic (`hyperbolic`) | `https://api.hyperbolic.xyz/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Nebius AI Studio (`nebius`) | `https://api.studio.nebius.com/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Featherless AI (`featherless`) | `https://api.featherless.ai/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Pollinations API (`pollinations`) | `https://gen.pollinations.ai/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Moonshot AI / Kimi (`moonshot`) | `https://api.moonshot.ai/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Alibaba Model Studio (Singapore) (`alibaba-intl`) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Novita AI (`novita`) | `https://api.novita.ai/v3/openai` | `auto` | `unknown` | `Check current account balance and provider pricing; no free tier is assumed. Path: `/models (base)` |
| Chutes (`chutes`) | `https://llm.chutes.ai/v1` | `auto` | `unknown` | `Check current account balance and provider pricing; no free tier is assumed. Path: `/models (base)` |
| Z.ai / GLM (`zai`) | `https://api.z.ai/api/paas/v4` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Cohere Compatibility API (`cohere`) | `https://api.cohere.com/compatibility/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Cloudflare Workers AI (replace ACCOUNT_ID) (`cloudflare`) | `https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/ai/v1` | `api_key` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Azure OpenAI (use your deployment URL) (`azure`) | `https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT` | `api_key` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| OVHcloud AI Endpoints (`ovh`) | `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1` | `auto` | `keyless-limited-tier` | `A limited keyless tier has been documented; verify current live endpoint and rate limits. Path: `/models (base)` |
| NaraRouter (`nara`) | `https://router.bynara.id/v1` | `auto` | `gated-free-tier` | `Free access may require account and community verification; zero-balance accounts may have only a subset available. Path: `/models (base)` |
| Requesty (`requesty`) | `https://router.requesty.ai/v1` | `auto` | `free-tier-advertised` | `Free daily request limits and eligible models can change. Path: `/models (base)` |
| AnyAPI (`anyapi`) | `https://api.anyapi.ai/v1` | `auto` | `recurring-free-tier-advertised` | `Free tier has been documented with a daily token cap; confirm current pricing and account limits. Path: `/models (base)` |
| AINative Studio (`ainative`) | `https://api.ainative.studio/api/v1` | `auto` | `free-tier-advertised-unverified` | `Publicly advertised recurring free allowance; treat as unverified until your own account shows availability. Path: `/models (base)` |
| Aion Labs (`aion`) | `https://api.aionlabs.ai/v1` | `auto` | `free-tier-advertised` | `Free access eligibility and model catalog can depend on account and age-gating. Path: `/models (base)` |
| SEA-LION (`sealion`) | `https://api.sea-lion.ai/v1` | `auto` | `free-tier-advertised` | `Limited free requests per minute have been documented; verify current availability. Path: `/models (base)` |
| OrcaRouter (`orcarouter`) | `https://api.orcarouter.ai/v1` | `auto` | `free-aliases-advertised` | `Use only confirmed free aliases; do not permit fallback from a free alias to a paid model. Path: `/models (base)` |
| UnoRouter (`unorouter`) | `https://api.unorouter.com/v1` | `auto` | `free-models-advertised` | `Explicit :free models are rate-limited; verify current catalog. Path: `/models (base)` |
| LLM7 (`llm7`) | `https://api.llm7.io/v1` | `auto` | `free-tier-advertised` | `Free quota is rate-limited and may change. Path: `/models (base)` |
| Kilo Gateway (`kilo`) | `https://api.kilo.ai/api/gateway/v1` | `none` | `anonymous-free-routes` | `Some anonymous routes are free and rate-limited; model availability and data policies are provider-controlled. Path: `/api/gateway/models (host)` |
| Ollama (local) (`ollama`) | `http://127.0.0.1:11434/v1` | `none` | `local-no-hosted-api-cost` | `Local inference uses your own hardware; electricity and hardware are not included. Path: `/models (base)` |
| LM Studio (local) (`lmstudio`) | `http://127.0.0.1:1234/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| llama.cpp server (local) (`llamacpp`) | `http://127.0.0.1:8080/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| vLLM (local/self-hosted) (`vllm`) | `http://127.0.0.1:8000/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| LocalAI (local) (`localai`) | `http://127.0.0.1:8080/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| GitHub Models (`github-models`) | `https://models.github.ai/inference` | `auto` | `unknown` | `Limited free quota may apply; verify current terms. Path: `/models (base)` |
| NavyAI (`navy`) | `https://api.navy/v1` | `auto` | `free-tier-advertised` | `Free limits and account requirements may change. Path: `/models (base)` |
| xKiro (`xkiro`) | `https://api.xkiro.com/v1` | `auto` | `free-model-tier-advertised` | `Free-model scope is account/plan dependent. Path: `/models (base)` |
| BazaarLink (`bazaarlink`) | `https://bazaarlink.ai/api/v1` | `auto` | `free-auto-route-advertised` | `Only explicitly free aliases should be used for zero-cost routing. Path: `/models (base)` |
| Routeway (`routeway`) | `https://api.routeway.ai/v1` | `auto` | `free-models-advertised` | `Some free model routes require a browser-like User-Agent; verify current base URL and limits in provider docs. Path: `/models (base)` |
| ElectronHub (`electronhub`) | `https://api.electronhub.ai/v1` | `auto` | `unknown` | `Check live account credit/quota; not necessarily free. Path: `/models (base)` |
| Experiential Labs (`experiential`) | `https://api.experiential.ai/v1` | `auto` | `unknown` | `Check live account credit/quota; not necessarily free. Path: `/models (base)` |
| AMD Radeon Cloud Model API (`radeon-cloud`) | `https://developer.amd.com.cn/radeon/api/v1` | `auto` | `experimental-free-roster` | `Public model roster may rotate; check account and current terms. Path: `/models (base)` |
| Baidu Qianfan (`qianfan`) | `https://qianfan.baidubce.com/v2` | `auto` | `unknown` | `Account verification and billing/rate limits may apply. Path: `/models (base)` |
| Volcengine Ark (`volcengine`) | `https://ark.cn-beijing.volces.com/api/v3` | `auto` | `unknown` | `Check regional account and free-quota terms. Path: `/models (base)` |
| LongCat / Meituan (`longcat`) | `https://api.longcat.chat/openai/v1` | `auto` | `unknown` | `Confirm current endpoint and free allocation from official docs. Path: `/models (base)` |
| iFlytek Spark (`xfyun`) | `https://spark-api-open.xf-yun.com/v1` | `bearer` | `unknown` | `Console APIPassword is used as a token; check model eligibility. Path: `/models (base)` |
| Zhipu / Z.ai (global API) (`zhipu`) | `https://api.z.ai/api/paas/v4` | `auto` | `unknown` | `Global and domestic keys may use different hosts. Path: `/models (base)` |
| Baichuan AI (`baichuan`) | `https://api.baichuan-ai.com/v1` | `auto` | `unknown` | `Paid/credit status depends on account. Path: `/models (base)` |
| ModelScope inference (`modelscope`) | `https://api-inference.modelscope.cn/v1` | `bearer` | `unknown` | `Model availability and account requirements vary. Path: `/models (base)` |
| Qwen Code / compatible endpoint (`qwen-code`) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `auto` | `unknown` | `Requires an authorized DashScope key; free quota varies by model/region. Path: `/models (base)` |
| Open WebUI (local OpenAI-compatible) (`openwebui`) | `http://127.0.0.1:3000/v1` | `bearer` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Lemonade (local server) (`lemonade`) | `http://127.0.0.1:8000/api/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| llamafile server (`llamafile`) | `http://127.0.0.1:8080/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Xinference (local/self-hosted) (`xinference`) | `http://127.0.0.1:9997/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Docker Model Runner (`docker-model-runner`) | `http://127.0.0.1:12434/engines/v1` | `none` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| B.AI (`bai`) | `https://api.b.ai/v1` | `auto` | `limited-time-promotion` | `Provider documentation/source has listed limited-time zero-credit promotions; do not assume all models remain free. Path: `/models (base)` |
| Ollama Cloud (`ollama-cloud`) | `https://ollama.com/v1` | `auto` | `limited-cloud-free-tier` | `Cloud free-tier concurrency/session caps and eligible models can change. Path: `/models (base)` |
| 9Router (local gateway)  (`9router-local`) | `http://127.0.0.1:20128/v1` | `bearer` | `local-gateway` | `Use only when you have 9Router running locally; paste its own local API key. Default API port documented by 9Router is 20128. Path: `/models (base)` |
| Custom Anthropic-compatible endpoint (`custom-anthropic`) | `http://127.0.0.1:4000/v1` | `auto` | `unknown` | `Free access is not guaranteed; check your provider account and current terms. Path: `/models (base)` |
| Custom OpenAI-compatible endpoint (`custom`) | `http://127.0.0.1:1234/v1` | `auto` | `user-defined` | `Access and cost are determined by the endpoint you configure. Path: `/models (base)` |

## Reading the free-tier labels

- `unknown` means MosaicRoute makes no free-access claim.
- Labels such as `free-tier-advertised` or `free-models-advertised` are pointers to terms you must confirm with the provider, not a guarantee.
- `free-tier-retired` means the provider had a previously documented free tier that is no longer assumed active; the provider preset remains useful if you have paid access.
- `provider-priced-zero` is only assigned when the provider model catalog explicitly supplies zero input and output pricing.
- `explicit-free-model-id-hint` is based on a route/model ID marker and can become stale if an upstream changes model names or account policy.

The router never claims an unverified model is free. `auto/free` only includes models recognized as explicitly free-like or explicitly zero-priced in current metadata. It is still possible for providers to change quotas, terms, or behavior after discovery.

## Discovery and compatibility

By default MosaicRoute requests `{base_url}/models`. Each connection can customize its discovery path and select whether it is resolved relative to the base URL or from the host root. A provider that does not expose compatible JSON model listing may need a dedicated adapter or manual alias. The current request engine supports OpenAI-compatible Chat Completions and Responses pass-through, and Anthropic Messages pass-through; cataloging a provider does not mean native image, audio, search, cloud IAM, or proprietary web-session APIs are implemented.

Credentials are manually configured using a supported auth mode. Standard OAuth2 PKCE works only where the upstream allows a third-party client and localhost redirect. MosaicRoute does not silently scrape browser cookies or reuse proprietary CLI/IDE subscription sessions.
