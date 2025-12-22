import logging
import os

def setup_logger():
    os.makedirs("logs", exist_ok=True)
    log_file = "logs/viking_ai.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger("VikingAI")
