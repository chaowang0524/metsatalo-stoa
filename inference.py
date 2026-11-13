import os

from openai import OpenAI

client = OpenAI(
    base_url=f"{os.environ['VERDA_INFERENCE_BASE_URL']}",
    api_key=os.environ["VERDA_INFERENCE_API_KEY"],
)

resp = client.chat.completions.create(
    model="deepseek-4-1-flash-1017",
    messages=[{"role": "user", "content": "What is the best viewpoint in Helsinki?"}],
    max_tokens=2048,
)
print(resp.choices[0].message.content or resp.choices[0].message.reasoning_content)
print(resp.usage)