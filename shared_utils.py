# shared_utils.py
import logging
import time
import requests
from flask import current_app

logger = logging.getLogger(__name__)

def get_global_specific_user_access_token():
    # ... (full implementation as discussed, using current_app.config)
    logger.info("Attempting to get access token for a specific pre-configured user (Shared Util).")
    specific_client_id = current_app.config.get('GLOBAL_SPECIFIC_USER_CLIENT_ID')
    specific_refresh_token = current_app.config.get('GLOBAL_SPECIFIC_USER_REFRESH_TOKEN')
    # ... rest of the logic from the function previously in Google_Suite.py
    # Ensure all necessary app.config keys are checked (TOKEN_URL, CLIENT_SECRET etc.)
    client_secret_global = current_app.config.get('CLIENT_SECRET')
    token_url_global = current_app.config.get('TOKEN_URL')
    request_timeout_global = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    if not all([specific_client_id, specific_refresh_token, client_secret_global, token_url_global]):
        missing = [k for k,v in {
            "GLOBAL_SPECIFIC_USER_CLIENT_ID": specific_client_id,
            "GLOBAL_SPECIFIC_USER_REFRESH_TOKEN": specific_refresh_token,
            "CLIENT_SECRET": client_secret_global,
            "TOKEN_URL": token_url_global
        }.items() if not v]
        logger.error(f"CRITICAL: Missing configurations for specific user token: {', '.join(missing)}")
        raise ValueError(f"Missing configurations for specific user token: {', '.join(missing)}")

    payload = {
        "client_id": specific_client_id,
        "client_secret": client_secret_global,
        "refresh_token": specific_refresh_token,
        "grant_type": "refresh_token"
    }
    # ... (rest of the requests.post logic and error handling)
    log_payload = payload.copy(); log_payload['client_secret'] = 'REDACTED'; log_payload['refresh_token'] = 'REDACTED'
    logger.debug(f"Shared util: Specific user token refresh payload (redacted): {log_payload}")
    start_time = time.time()
    try:
        response = requests.post(token_url_global, data=payload, timeout=request_timeout_global)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token:
            logger.info(f"Successfully obtained access token (Shared Util) in {duration:.2f}s.")
            return access_token
        else:
            raise ValueError("Access token not found in specific user refresh response (Shared Util).")
    except Exception as e:
        logger.error(f"Error in get_global_specific_user_access_token (Shared Util): {e}", exc_info=True)
        raise

# You could also move exchange_code_for_tokens_global here
# def exchange_code_for_tokens_global(authorization_code, client_id, client_secret, redirect_uri_used):
#    ...
