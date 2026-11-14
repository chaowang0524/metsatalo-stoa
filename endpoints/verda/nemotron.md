instructions are The endpoint address is https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/ . With opencode, this config should work for it:
 
{
  "$schema": "https://opencode.ai/config.json",
  "model": "verda-nemotron/nemotron",
  "providers": {
    "verda-nemotron": {
      "name": "Verda Nemotron 3.5 Super VL",
      "env": ["VERDA_INFERENCE_KEY"],
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1",
        "apiKey": "{env:VERDA_INFERENCE_KEY}"
      },
      "models": {
        "nemotron": {
          "modelID": "nemotron-3.5-super-vl",
          "name": "Nemotron 3.5 Super VL",
          "capabilities": {
            "tools": true,
            "input": ["text", "image"],
            "output": ["text"]
          },
          "compatibility": {
            "maxTokensField": "max_tokens",
            "reasoningField": "reasoning_content"
          },
          "limit": {
            "context": 1048576,
            "output": 32768
          }
        }
      }
    }
  }
}
(Key moved to .env as NEMOTRON_API_KEY. Config lives in nemotron.py.)
