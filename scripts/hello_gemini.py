"""Direct app LLM call (OpenAI or Gemini, depending on .env)."""
from palimpsest import gemini_io

print(gemini_io.generate_text("Say 'hello hackathon' and nothing else."))
