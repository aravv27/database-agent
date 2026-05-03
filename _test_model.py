"""Quick test: which model works with JSON structured output?"""
import os
import sys
import json
from openai import OpenAI
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.getenv("NVIDIA_API_KEY"),
)

MODELS_TO_TEST = [
    "mistralai/mistral-nemotron",
    "nv-mistralai/mistral-nemo-12b-instruct",
    "z-ai/glm4.7",
]

for model in MODELS_TO_TEST:
    print(f"\nTesting: {model}")
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": 'Respond with JSON: {"greeting": "hello"}'}],
            response_format={"type": "json_object"},
            max_tokens=50,
            temperature=0.1,
        )
        print(f"  OK: {r.choices[0].message.content}")
    except Exception as e:
        print(f"  FAIL: {e}")
