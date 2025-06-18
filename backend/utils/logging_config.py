import logging
import os
from logging.handlers import RotatingFileHandler

# Ensure logs directory exists
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "application.log")

# Configure rotating logs (max size = 5MB, keep last 5 log files)
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)

file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to INFO for production
    handlers=[file_handler, logging.StreamHandler()]
)

logging.info("📌 Logging initialized. Logs will be saved in 'logs/application.log'")

logger = logging.getLogger(__name__)