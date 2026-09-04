from google import genai
import os
c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
for m in c.models.list():
    if "generateContent" in m.supported_actions:
        print(m.name)