# shared_utils.py
import logging
import time
import requests
from flask import current_app # To access app.config

logger = logging.getLogger(__name__)

def get_global_specific_user_access_token():
    logger.info("Attempting to get access token for a specific pre-configured user (Shared Util).")
    # These config keys are expected to be set in the Flask app's config (Google_Suite.py)
    specific_client_id = current_app.config.get('GLOBAL_SPECIFIC_USER_CLIENT_ID')
    specific_refresh_token = current_app.config.get('GLOBAL_SPECIFIC_USER_REFRESH_TOKEN')
    client_secret_global = current_app.config.get('CLIENT_SECRET')
    token_url_global = current_app.config.get('TOKEN_URL')
    request_timeout_global = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    if not client_secret_global:
        logger.error("CRITICAL: Global CLIENT_SECRET not available in app.config for specific user token refresh.")
        raise ValueError("Global CLIENT_SECRET not configured.")
    if not specific_client_id:
        logger.error("CRITICAL: GLOBAL_SPECIFIC_USER_CLIENT_ID not configured in app.config.")
        raise ValueError("GLOBAL_SPECIFIC_USER_CLIENT_ID not configured.")
    if not specific_refresh_token:
        logger.error("CRITICAL: GLOBAL_SPECIFIC_USER_REFRESH_TOKEN not configured in app.config.")
        raise ValueError("GLOBAL_SPECIFIC_USER_REFRESH_TOKEN not configured.")
    if not token_url_global:
        logger.error("CRITICAL: TOKEN_URL not configured in app.config.")
        raise ValueError("TOKEN_URL not configured.")

    payload = {
        "client_id": specific_client_id,
        "client_secret": client_secret_global,
        "refresh_token": specific_refresh_token,
        "grant_type": "refresh_token"
    }
    
    log_payload = payload.copy()
    log_payload['client_secret'] = 'REDACTED_FOR_LOG'
    log_payload['refresh_token'] = f"{log_payload.get('refresh_token', '')[:10]}..." if log_payload.get('refresh_token') else 'None'
    logger.debug(f"Shared util: Specific user token refresh payload (redacted): {log_payload}")
    
    start_time = time.time()
    try:
        response = requests.post(token_url_global, data=payload, timeout=request_timeout_global)
        
        logger.info(f"DEBUG (shared_utils): Google specific user token RESPONSE status: {response.status_code}")
        logger.info(f"DEBUG (shared_utils): Google specific user token RESPONSE text: {response.text[:500]}")

        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token:
            logger.info(f"Successfully obtained access token for specific user (Shared Util) in {duration:.2f}s. Expires in: {token_data.get('expires_in')}s")
            return access_token
        else:
            logger.error(f"Shared util specific user token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in shared util specific user refresh response.")
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        status_code = e.response.status_code if hasattr(e, 'response') and e.response is not None else 500
        logger.error(f"HTTPError ({status_code}) during shared util specific user token refresh after {duration:.2f} seconds: {error_text}", exc_info=True)
        if "invalid_grant" in error_text:
            logger.warning("Shared util specific user token refresh failed with 'invalid_grant'. Check credentials and scopes.")
        raise
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Timeout ({request_timeout_global}s) during shared util specific user token refresh after {duration:.2f} seconds.", exc_info=True)
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during shared util specific user token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

# You can also move exchange_code_for_tokens_global here if you want
# def exchange_code_for_tokens_global(authorization_code, client_id, client_secret, redirect_uri_used):
#   logger.info(f"Shared_utils exchange: Attempting to exchange code for tokens. Code: {authorization_code[:10]}...")
#   # ... (rest of the implementation, using current_app.config for TOKEN_URL etc.)
