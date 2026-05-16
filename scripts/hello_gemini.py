"""Direct Gemini call (the path our app uses for synth/contradiction-check)."""
from palimpsest import config
import google.generativeai as genai

genai.configure(api_key=config.GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3-pro")
resp = model.generate_content("Say 'hello hackathon' and nothing else.")
print(resp.text)
