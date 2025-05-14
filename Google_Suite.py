from flask import Flask, request, jsonify, current_app, redirect
import logging
import os
import requests
import time
import uuid

# Import the blueprints
from Google_Sheets_Agent import sheets_bp
from Google_Docs_Agent import docs_bp
# Note: We are NOT importing get_global_specific_user_access_token from here anymore.
# The agent files will import it from shared_utils.py

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Centralized Configuration ---
app.config['CLIENT_ID'] = "26763482887-coiufpukc1l69aaulaiov5o0u3en2del.apps.googleusercontent.com"
app.config['CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET", "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")
app.config['TOKEN_URL'] = "https://oauth2.googleapis.com/token"
app.config['REQUEST_TIMEOUT_SECONDS'] = 30
app.config['UNIFIED_REDIRECT_URI'] = "https://serverless.on-demand.io/apps/googlesuite/auth/callback"

# Configuration for a globally accessible "specific user" - to be used by shared_utils
app.config['GLOBAL_SPECIFIC_USER_CLIENT_ID'] = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
app.config['GLOBAL_SPECIFIC_USER_REFRESH_TOKEN'] = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"


if not app.config['CLIENT_SECRET'] or app.config['CLIENT_SECRET'] == "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": # Check against placeholder
    logger.warning("WARNING: GOOGLE_CLIENT_SECRET is using a placeholder or is not properly set via environment variable.")
    if os.getenv("GOOGLE_CLIENT_SECRET") is None:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable is NOT SET. OAuth operations will likely fail.")

# --- Centralized Helper Functions ---

def exchange_code_for_tokens_global(authorization_code, client_id, client_secret, redirect_uri_used):
    # This function remains here as it's directly used by the /auth/callback endpoint in this file.
    # Alternatively, it could also be moved to shared_utils.py and imported.
    logger.info(f"Global exchange: Attempting to exchange code for tokens. Code: {authorization_code[:10]}...")
    start_time = time.time()
    if not client_secret:
        logger.error("CRITICAL: Client secret not available for token exchange.")
        raise ValueError("Client secret not available.")
    payload = {
        "code": authorization_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri_used,
        "grant_type": "authorization_code"
    }
    log_payload = payload.copy()
    log_payload['client_secret'] = 'REDACTED_FOR_LOG'
    log_payload['code'] = f"{log_payload.get('code', '')[:10]}..." if log_payload.get('code') else 'None'
    
    token_url_to_use = current_app.config.get('TOKEN_URL')
    timeout_to_use = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    logger.info(f"DEBUG: Token exchange request URL: {token_url_to_use}")
    logger.info(f"DEBUG: Token exchange payload being sent (redacted): {log_payload}")
    try:
        response = requests.post(
            token_url_to_use,
            data=payload,
            timeout=timeout_to_use
        )
        logger.info(f"DEBUG: Google token exchange RESPONSE status: {response.status_code}")
        logger.info(f"DEBUG: Google token exchange RESPONSE text: {response.text[:500]}")
        response.raise_for_status()
        token_data = response.json()
        duration = time.time() - start_time
        if token_data.get("access_token"):
            logger.info(f"Successfully exchanged code for tokens in {duration:.2f} seconds.")
            if "refresh_token" not in token_data:
                 logger.warning("Refresh token was NOT included in the token response from Google (exchange).")
            return token_data
        else:
            logger.error(f"Token exchange response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in response.")
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTPError caught in exchange_code_for_tokens_global: {e}", exc_info=True)
        raise 
    except requests.exceptions.Timeout:
        duration = time.time() - start_time; logger.error(f"Timeout ({timeout_to_use}s) during token exchange after {duration:.2f} seconds."); raise
    except Exception as e:
        duration = time.time() - start_time; logger.error(f"Generic exception in exchange_code_for_tokens_global: {str(e)}", exc_info=True); raise

# REMOVED: get_global_specific_user_access_token() function from here.
# It is now expected to be in shared_utils.py and imported by agent files.

# --- Unified OAuth Callback ---
@app.route('/auth/callback', methods=['GET'])
def unified_oauth_callback():
    endpoint_name = "/auth/callback (unified)"
    authorization_code = request.args.get('code')
    received_state = request.args.get('state') 

    logger.info(f"DEBUG CALLBACK: unified_oauth_callback HIT.")
    logger.info(f"DEBUG CALLBACK:   Received authorization_code (first 20): {authorization_code[:20] if authorization_code else 'None'}")
    logger.info(f"DEBUG CALLBACK:   Received state: {received_state}")

    if not authorization_code:
        logger.warning(f"ENDPOINT {endpoint_name}: Authorization code missing.")
        return jsonify({"error": "Authorization code missing"}), 400
    
    if not received_state: 
        logger.warning(f"ENDPOINT {endpoint_name}: State parameter missing. CSRF risk if not validated properly.")

    try:
        client_id_to_use = current_app.config['CLIENT_ID']
        client_secret_to_use = current_app.config['CLIENT_SECRET']
        redirect_uri_to_use = current_app.config['UNIFIED_REDIRECT_URI']

        logger.info(f"DEBUG CALLBACK: Calling exchange_code_for_tokens_global with:")
        logger.info(f"DEBUG CALLBACK:   client_id: {client_id_to_use}")
        logger.info(f"DEBUG CALLBACK:   client_secret (is present?): {'Yes' if client_secret_to_use and client_secret_to_use != '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' else 'No or Placeholder!'}")
        logger.info(f"DEBUG CALLBACK:   redirect_uri: {redirect_uri_to_use}")
        
        token_data = exchange_code_for_tokens_global(
            authorization_code,
            client_id_to_use,
            client_secret_to_use,
            redirect_uri_to_use
        )
        
        logger.info(f"ENDPOINT {endpoint_name}: Authorization successful, tokens obtained.")
        if "refresh_token" not in token_data:
            logger.warning("Refresh token was NOT included in the token response from Google (callback).")
        
        return jsonify({
            "message": "Authorization successful! IMPORTANT: Securely store the 'refresh_token' if received. It will be used for future access.",
            "tokens": token_data 
        })
    except requests.exceptions.HTTPError as e:
        error_detail = e.response.text if hasattr(e, 'response') and e.response else str(e)
        status_code = e.response.status_code if hasattr(e, 'response') and e.response is not None else 500
        logger.error(f"ENDPOINT {endpoint_name}: HTTPError during token exchange: {error_detail}", exc_info=True)
        return jsonify({"error": f"Failed to exchange code for tokens (HTTP {status_code})", "details": error_detail}), status_code
    except ValueError as ve: 
        logger.error(f"ENDPOINT {endpoint_name}: ValueError during token exchange: {str(ve)}", exc_info=True)
        return jsonify({"error": f"Configuration or input error: {str(ve)}"}), 400
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Error during token exchange: {str(e)}", exc_info=True)
        return jsonify({"error": f"Failed to exchange authorization code for tokens: {str(e)}"}), 500

# --- Endpoint to initiate OAuth flow ---
@app.route('/auth/google')
def auth_google():
    service_type_param = request.args.get('service', 'both') 
    
    state = str(uuid.uuid4())
    logger.info(f"Initiating OAuth for service(s): {service_type_param} with state: {state}")

    scope_map = {
        "sheets": "https://www.googleapis.com/auth/spreadsheets",
        "docs": "https://www.googleapis.com/auth/documents",
        "both": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/documents"
    }
    scope = scope_map.get(service_type_param, scope_map["both"])

    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={current_app.config['CLIENT_ID']}"
        f"&redirect_uri={current_app.config['UNIFIED_REDIRECT_URI']}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"  
        f"&prompt=consent"       
        f"&state={state}"        
    )
    return redirect(auth_url)

# Register the blueprints
# These imports are at the top of the file now.
app.register_blueprint(sheets_bp)
app.register_blueprint(docs_bp)

@app.route('/')
def index():
    logger.info("Root endpoint '/' hit.")
    return jsonify(message="Google Suite Unified Agent. Access services at /sheets or /docs prefixes. Initiate auth at /auth/google?service=[sheets|docs|both]")

@app.route('/health')
def health_check():
    logger.info("Health check '/health' endpoint hit.")
    return jsonify(status="UP", message="Google Suite Agent is healthy."), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Google Suite Flask application for local development on port {port}...")
    app.run(debug=True, host="0.0.0.0", port=port)
