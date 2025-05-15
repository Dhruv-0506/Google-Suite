import os
import logging

logger = logging.getLogger(__name__)

# --- On-Demand Chat API Configuration ---
# WARNING: API Keys are hardcoded here.
# This is suitable ONLY for private, isolated testing.
# DO NOT use this approach for production or shared repositories.

ON_DEMAND_API_KEY = "gd4TLS4e3bqk7WoqvVyx25beqzseWrzY"
ON_DEMAND_EXTERNAL_USER_ID = "680f1d933753c204a08fdf9e"

# Log that hardcoded keys are being used
logger.warning("--------------------------------------------------------------------")
logger.warning("CHAT AGENT CONFIG: Using HARDCODED API Key and External User ID.")
logger.warning("This configuration is suitable ONLY for private, isolated testing.")
logger.warning("DO NOT commit real secrets to public or shared repositories.")
logger.warning("--------------------------------------------------------------------")

if not ON_DEMAND_API_KEY:
    logger.error("CRITICAL: ON_DEMAND_CHAT_API_KEY environment variable is NOT SET.")
    # In a production system, you might want to raise an error here to prevent startup:
    # raise ValueError("FATAL: ON_DEMAND_CHAT_API_KEY is not configured. Application cannot start chat agent features.")
    # For now, we'll allow it to proceed but it will fail when used.
    ON_DEMAND_API_KEY = "YOUR_FALLBACK_OR_PLACEHOLDER_ON_DEMAND_API_KEY" # This will cause errors if used
    if ON_DEMAND_API_KEY == "YOUR_FALLBACK_OR_PLACEHOLDER_ON_DEMAND_API_KEY":
        logger.warning("WARNING: ON_DEMAND_CHAT_API_KEY is using a placeholder because the environment variable was not found.")


if ON_DEMAND_EXTERNAL_USER_ID == "siri_user_default_001" and not os.getenv("ON_DEMAND_CHAT_EXTERNAL_USER_ID"):
    logger.info("INFO: ON_DEMAND_CHAT_EXTERNAL_USER_ID is using the default value 'siri_user_default_001' as the environment variable was not found.")

# You can add other chat-agent-specific configurations here if needed in the future.
# For example:
# CHAT_AGENT_DEFAULT_MODEL = os.getenv("CHAT_AGENT_DEFAULT_MODEL", "predefined-openai-gpt4.1")
# CHAT_AGENT_DEFAULT_AGENT_IDS = os.getenv("CHAT_AGENT_DEFAULT_AGENT_IDS", '["agent-123","agent-456"]') # Store as JSON string


