import os
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("OPENROUTER_API_KEY")
model = os.getenv("OPENROUTER_MODEL")

print("OPENROUTER_API_KEY:", "SET" if key else "MISSING")
print("OPENROUTER_MODEL:", model if model else "MISSING")
