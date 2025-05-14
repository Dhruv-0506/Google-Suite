import logging
import time
import requests
from flask import current_app # To access app.config from the currently running Flask app

logger = logging.getLogger(__name__) # Logger for shared utilities

# --- Helper Function for User-Specific Refresh Tokens ---
def get_access_token(refresh_token, client_id, client_secret):
    """
    Obtains a new access token using a user's refresh token.
    Uses TOKEN_URL and REQUEST_TIMEOUT_SECONDS from current_app.config.
    """
    logger.info(f"Shared Util: Getting access token for refresh token: {refresh_token[:10]}...")
    start_time = time.time()

    if not client_id:
        logger.error("CRITICAL: Client ID not provided for get_access_token.")
        raise ValueError("Client ID not provided.")
    if not client_secret:
        logger.error("CRITICAL: Client secret not available for token refresh.")
        raise ValueError("Client secret not available.")
    if not refresh_token:
        logger.error("CRITICAL: Refresh token not provided.")
        raise ValueError("Refresh token not provided.")
    
    token_url = current_app.config.get('TOKEN_URL')
    request_timeout = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    if not token_url:
        logger.error("CRITICAL: TOKEN_URL not configured in the application (shared_utils.get_access_token).")
        raise ValueError("TOKEN_URL not configured.")

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    log_payload = payload.copy(); log_payload['client_secret'] = 'REDACTED'; log_payload['refresh_token'] = 'REDACTED'
    logger.debug(f"Shared Util: get_access_token payload (redacted): {log_payload}")
    
    try:
        response = requests.post(token_url, data=payload, timeout=request_timeout)
        # Log response for debugging
        logger.debug(f"Shared Util (get_access_token) - Google Response Status: {response.status_code}")
        logger.debug(f"Shared Util (get_access_token) - Google Response Text: {response.text[:500]}")
        response.raise_for_status()
        token_data = response.json()
        access_token_val = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token_val:
            logger.info(f"Shared Util: Successfully obtained new access token in {duration:.2f}s. Expires in: {token_data.get('expires_in')}s")
            return access_token_val
        else:
            logger.error(f"Shared Util: Token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in refresh response.")
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        status_code_text = f" (Status: {e.response.status_code})" if hasattr(e, 'response') and e.response else ""
        logger.error(f"Shared Util: HTTPError{status_code_text} during token refresh after {duration:.2f}s: {error_text}", exc_info=True)
        if "invalid_grant" in error_text:
            logger.warning("Shared Util: Token refresh failed with 'invalid_grant'. Refresh token may be expired/revoked or lack necessary scopes.")
        raise
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Shared Util: Timeout ({request_timeout}s) during token refresh after {duration:.2f} seconds.", exc_info=True)
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Shared Util: Generic exception during token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

# --- Helper Function for Global Specific User Token ---
def get_global_specific_user_access_token():
    """
    Obtains an access token for a pre-configured global "specific user".
    Uses GLOBAL_SPECIFIC_USER_CLIENT_ID, GLOBAL_SPECIFIC_USER_REFRESH_TOKEN,
    CLIENT_SECRET, TOKEN_URL, and REQUEST_TIMEOUT_SECONDS from current_app.config.
    """
    logger.info("Shared Util: Attempting to get access token for global specific user.")
    
    specific_client_id = current_app.config.get('GLOBAL_SPECIFIC_USER_CLIENT_ID')
    specific_refresh_token = current_app.config.get('GLOBAL_SPECIFIC_USER_REFRESH_TOKEN')
    client_secret_global = current_app.config.get('CLIENT_SECRET')
    token_url_global = current_app.config.get('TOKEN_URL')
    request_timeout_global = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    # Validate required configurations
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
    log_payload = payload.copy(); log_payload['client_secret'] = 'REDACTED'; log_payload['refresh_token'] = 'REDACTED'
    logger.debug(f"Shared Util: Global specific user token refresh payload (redacted): {log_payload}")
    
    start_time = time.time()
    try:
        response = requests.post(token_url_global, data=payload, timeout=request_timeout_global)

        logger.debug(f"Shared Util (get_global_specific_user_access_token) - Google Response Status: {response.status_code}")
        logger.debug(f"Shared Util (get_global_specific_user_access_token) - Google Response Text: {response.text[:500]}")
        
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token:
            logger.info(f"Shared Util: Successfully obtained access token for specific user in {duration:.2f}s. Expires in: {token_data.get('expires_in')}s")
            return access_token
        else:
            logger.error(f"Shared Util: Global specific user token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in global specific user refresh response.")
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        status_code_text = f" (Status: {e.response.status_code})" if hasattr(e, 'response') and e.response else ""
        logger.error(f"Shared Util: HTTPError{status_code_text} during global specific user token refresh after {duration:.2f}s: {error_text}", exc_info=True)
        if "invalid_grant" in error_text:
            logger.warning("Shared Util: Global specific user token refresh failed with 'invalid_grant'. Check credentials and scopes.")
        raise
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Shared Util: Timeout ({request_timeout_global}s) during global specific user token refresh after {duration:.2f} seconds.", exc_info=True)
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Shared Util: Generic exception during global specific user token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

# --- Helper Function for Exchanging Authorization Code for Tokens ---
def exchange_code_for_tokens_global(authorization_code, client_id, client_secret, redirect_uri_used):
    """
    Exchanges an authorization code for access and refresh tokens.
    Uses TOKEN_URL and REQUEST_TIMEOUT_SECONDS from current_app.config.
    """
    logger.info(f"Shared Util: Attempting to exchange authorization code '{authorization_code[:20]}...' for tokens.")
    start_time = time.time()

    if not client_id:
        logger.error("CRITICAL: Client ID not provided for code exchange.")
        raise ValueError("Client ID not provided.")
    if not client_secret:
        logger.error("CRITICAL: Client secret not provided for code exchange.")
        raise ValueError("Client secret not provided.")
    if not redirect_uri_used:
        logger.error("CRITICAL: Redirect URI not provided for code exchange.")
        raise ValueError("Redirect URI not provided.")
    if not authorization_code:
        logger.error("CRITICAL: Authorization code not provided for code exchange.")
        raise ValueError("Authorization code not provided.")

    token_url = current_app.config.get('TOKEN_URL')
    request_timeout = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    if not token_url:
        logger.error("CRITICAL: TOKEN_URL not configured in the application (shared_utils.exchange_code).")
        raise ValueError("TOKEN_URL not configured.")

    payload = {
        "code": authorization_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri_used,
        "grant_type": "authorization_code"
    }
    log_payload = payload.copy(); log_payload['client_secret'] = 'REDACTED'; log_payload['code'] = 'REDACTED'
    logger.debug(f"Shared Util: exchange_code_for_tokens_global payload (redacted): {log_payload}")
    
    try:
        response = requests.post(token_url, data=payload, timeout=request_timeout)

        logger.debug(f"Shared Util (exchange_code_for_tokens_global) - Google Response Status: {response.status_code}")
        logger.debug(f"Shared Util (exchange_code_for_tokens_global) - Google Response Text: {response.text[:500]}")

        response.raise_for_status()
        token_data = response.json()
        duration = time.time() - start_time
        if token_data.get("access_token"): # Should also contain refresh_token on first auth
            logger.info(f"Shared Util: Successfully exchanged code for tokens in {duration:.2f} seconds.")
            if "refresh_token" not in token_data:
                logger.warning("Shared Util: Refresh token was NOT included in the token response from Google (exchange_code).")
            return token_data
        else:
            logger.error(f"Shared Util: Token exchange response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in response from code exchange.")
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        status_code_text = f" (Status: {e.response.status_code})" if hasattr(e, 'response') and e.response else ""
        logger.error(f"Shared Util: HTTPError{status_code_text} during code exchange after {duration:.2f}s: {error_text}", exc_info=True)
        raise
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Shared Util: Timeout ({request_timeout}s) during code exchange after {duration:.2f} seconds.", exc_info=True)
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Shared Util: Generic exception during code exchange after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise
