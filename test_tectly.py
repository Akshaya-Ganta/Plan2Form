
import os
from pathlib import Path
from dotenv import load_dotenv
from tectly import Client

# Load .env from the same directory as this script
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

api_key = os.getenv("TECTLY_API_KEY")
api_secret = os.getenv("TECTLY_API_SECRET")

print("Environment file found:", env_path.is_file())
print("API key loaded:", bool(api_key))
print("Secret loaded:", bool(api_secret))

if not api_key or not api_secret:
    raise RuntimeError(
        "Missing Tectly credentials. Check the .env file and variable names."
    )

with Client().create_session(api_key, api_secret) as session:
    projects = list(session.iter_projects())
    print("Tectly authentication successful!")
    print(f"Accessible projects: {len(projects)}")
