# 🤖 MARK XXX

**Watch The Detailed Video To Set-up This Model**: https://www.youtube.com/watch?v=-YjbWjv1tJg

### Next-Generation Personal AI Assistant — By FatihMakes

A real-time voice AI that can hear, see, understand, and control your Windows computer.
Local execution. Zero subscriptions(Unless you want to increase request by buying requests from Google AI Studio).
Built for intelligent automation.

## ✨ Overview

**MARK XXX** is an advanced voice-driven AI assistant designed to turn your computer into an interactive intelligent system.

Speak naturally — it listens, understands context, responds with a human-like voice, and executes tasks across your system automatically.

Designed for speed, autonomy, and real-world usability.


## 🚀 Capabilities

* **Real-time voice interaction** — Natural conversation with instant response
* **System control** — Launch apps, manage files, execute commands
* **Autonomous task execution** — Plans and completes multi-step workflows
* **Visual awareness** — Screen analysis and webcam understanding
* **Persistent memory** — Learns preferences and remembers context
* **Integrated tools** — Web search, weather, reminders, messaging, code help, image generation

## ⚡ Quick Start

```bash
git clone https://github.com/FatihMakes/Mark-XXX.git
cd Mark-XXX
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
python -m playwright install
mark-xxx
```

If your Windows `python` command is broken or missing on PATH, you can also start the app with:

```powershell
.\start_jarvis.ps1
```

Enter your Gemini API key on first launch for the live voice session.
Optional text-model backends can also be configured in `config/api_keys.json`.

Example:

```json
{
  "gemini_api_key": "YOUR_GEMINI_KEY",
  "llm_provider": "openrouter",
  "llm_model": "anthropic/claude-3.7-sonnet",
  "openrouter_api_key": "YOUR_OPENROUTER_KEY",
  "openai_api_key": "YOUR_OPENAI_KEY",
  "anthropic_api_key": "YOUR_ANTHROPIC_KEY",
  "github_api_key": "YOUR_GITHUB_TOKEN"
}
```

Supported `llm_provider` values: `gemini`, `openai`, `openrouter`, `anthropic`, `github`.
Planner, executor, memory extraction, code/dev helpers, static screen/camera analysis, and similar text/model tasks use that provider selection.
The live native audio session still requires Gemini today.

If you prefer a one-command bootstrap flow, run `python bootstrap.py`.

If you got some problems or questions to ask or just want to support;

YouTube Account: [text](https://www.youtube.com/@FatihMakes)
Instagram Account: [text](https://www.instagram.com/fatihmakes/)

## 📋 Requirements

* Windows 10 / 11
* Python 3.10 or newer
* Microphone
* Gemini API key for live voice
* Optional: OpenAI, OpenRouter, Anthropic, or GitHub Models API key for text/model routing

## ⚠️ License

Personal and non-commercial use only.
Licensed under **Creative Commons BY-NC 4.0**. See `LICENSE`.

Engineered by a 17-year-old building a real JARVIS-style assistant.
⭐ Star the repository to support the project.
