# OpenWhale
OpenClaw but ONLY with 8b or less Ollama models so that you have total data ownership and on-device AI assistant capabilities

## Why?
I love OpenClaw (or Moltbot... or whatever), I really do. It is genuinely useful as long as you know how to use it safely and keep an eye on what it's doing (running, `git commit` etc....)

However, for it to be most useful, I want to guarantee that _everything_ is processed on my device, that I own, in my house. And to do so, I do NOT want to have to go out and buy an M4 or some fat GPU like all the slop-posters on LinkedIn raving about moltbot must have done.

So here we are. It will likely never be as well tested/loved as Moltbot, but if it is useful to myself and others, that's awesome!

## WARNING
Just like Moltbot says, I cannot control what your LLM models might do. I have not yet implemented safeguards like Moltbot has for command execution. And besides, wether you the reader are a real human or OpenAI codex trying to help someone out, you should never just run something that a stranger on the internet made...... 

## WARNING v2
Also, just because the model runs on your local machine, that still doesn't mean an attacker couldn't convince it to run a bash command to install malware, or push your files somewhere, etc. As a rule of thumb: never ask OpenWhale to operate on a document or website you haven't checked over in plain-text form. (Since prompt injection could happen in the HTML comments of a website it's loading, for example. Or in white text on a PDF.)