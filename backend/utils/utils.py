import os
import yaml
from dotenv import load_dotenv
from backend.agent.llm_client import LLMClient

# Load environment variables
load_dotenv()

def load_config():
    """Loads configuration from config.yaml."""
    
    # Get the absolute path of this script
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Construct the absolute path to config.yaml
    config_path = os.path.join(base_dir, "config.yaml")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"❌ Config file not found at: {config_path}")  # Better error message

    with open(config_path, "r") as file:
        return yaml.safe_load(file)

def get_user_identity():
    """Retrieve user identity based on environment variables."""
    return {
        "role": os.getenv("USER_ROLE", "customer"),
        "user_id": os.getenv("USER_ID"),
        "username": os.getenv("USERNAME", "guest"),
    }
def get_user_identity():
    """Retrieve user identity from config.yaml. in production these will be supplied by your authentication system."""

    config = load_config()  # Load configuration
    
    user_settings = config.get("USER_SETTINGS", {})  # Get USER_SETTINGS section

    return {
        "role": user_settings.get("USER_ROLE", "customer"),   # Default: "customer"
        "user_id": user_settings.get("USER_ID"),             # May be None if missing
        "username": user_settings.get("USERNAME", "guest"),  # Default: "guest"
    }

def get_llm_client():
    """Initializes and returns an LLM client instance."""
    config = load_config()
    return LLMClient(config)