from flask import Flask, request, jsonify, current_app, redirect, session # Added session
import logging
import os
import requests
import time
import uuid

# Import the blueprints
from Google_Sheets_Agent import sheets_bp
from Google_Docs_Agent import docs_bp
from Google_Drive_Agent import drive_bp # Assuming this exists and is correctly set up
from Chat_Agent_Blueprint import chat_bp # <<< NEWLY ADDED
from Google_Calendar_Agent import calendar_bp
# from Google_Slides_Agent import slides_bp # Example for future

# Assuming shared_utils.py contains:
# - exchange_code_for_tokens_global (if you moved it, otherwise it's defined below)
# - get_global_specific_user_access_token (if you kept this functionality)
# - get_access_token (for user-specific refresh tokens)
# If these are not in shared_utils.py and are needed by Google_Suite.py or agents,
# they need to be defined or imported correctly.
# For this version, I will assume exchange_code_for_tokens_global is defined below
# and agent files import what they need from shared_utils.py.

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Centralized Configuration ---
app.config['CLIENT_ID'] = "26763482887-coiufpukc1l69aaulaiov5o0u3en2del.apps.googleusercontent.com"
# For security, this should ideally always be from an environment variable in production.
# The fallback is kept as per your existing code.
app.config['CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET", "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")
app.config['TOKEN_URL'] = "https://oauth2.googleapis.com/token"
app.config['REQUEST_TIMEOUT_SECONDS'] = 30
app.config['UNIFIED_REDIRECT_URI'] = "https://serverless.on-demand.io/apps/googlesuite/auth/callback"

# Configuration for a globally accessible "specific user" for /token endpoints in blueprints
# This is used by get_global_specific_user_access_token (expected to be in shared_utils.py)
app.config['GLOBAL_SPECIFIC_USER_CLIENT_ID'] = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
app.config['GLOBAL_SPECIFIC_USER_REFRESH_TOKEN'] = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"

# Flask Secret Key - CRUCIAL for session management (e.g., OAuth state)
# Set FLASK_SECRET_KEY environment variable in production.
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback_dev_secret_!@#$") # Replace with a strong random default for dev if preferred

if app.secret_key == "fallback_dev_secret_!@#$":
    logger.warning("SECURITY WARNING: Using a fallback FLASK_SECRET_KEY. "
                   "Set a strong, unique FLASK_SECRET_KEY environment variable for production!")


if not app.config['CLIENT_SECRET'] or app.config['CLIENT_SECRET'] == "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":
    logger.warning("WARNING: GOOGLE_CLIENT_SECRET is using a placeholder or is not properly set via environment variable.")
    if os.getenv("GOOGLE_CLIENT_SECRET") is None:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable is NOT SET. OAuth operations will likely fail.")

# --- Centralized Helper Functions ---
# This function is used by /auth/callback in this file.
# If shared_utils.py also defines this, ensure one source of truth or distinct names.
def exchange_code_for_tokens_global(authorization_code, client_id, client_secret, redirect_uri_used):
    logger.info(f"Google_Suite exchange: Attempting to exchange code for tokens. Code: {authorization_code[:10]}...")
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
        logger.info(f"DEBUG: Google token exchange RESPONSE text: {response.text[:500]}") # Log more for debugging
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
        # Error details will be in e.response.text if available
        logger.error(f"HTTPError caught in exchange_code_for_tokens_global: {e}", exc_info=True)
        raise 
    except requests.exceptions.Timeout:
        duration = time.time() - start_time; logger.error(f"Timeout ({timeout_to_use}s) during token exchange after {duration:.2f} seconds."); raise
    except Exception as e:
        duration = time.time() - start_time; logger.error(f"Generic exception in exchange_code_for_tokens_global: {str(e)}", exc_info=True); raise

# get_global_specific_user_access_token is expected to be in shared_utils.py
# and imported by the agent blueprints if they have /token endpoints.

# --- Unified OAuth Callback ---
@app.route('/auth/callback', methods=['GET'])
def unified_oauth_callback():
    endpoint_name = "/auth/callback (unified)"
    authorization_code = request.args.get('code')
    received_state = request.args.get('state') 

    logger.info(f"DEBUG CALLBACK: unified_oauth_callback HIT.")
    logger.info(f"DEBUG CALLBACK:   Received authorization_code (first 20): {authorization_code[:20] if authorization_code else 'None'}")
    logger.info(f"DEBUG CALLBACK:   Received state: {received_state}")

    # --- Robust State Validation using Flask session ---
    stored_state = session.pop('oauth_state', None)
    # service_context = session.pop('oauth_service_context', 'unknown') # Retrieve context if stored

    if not received_state or received_state != stored_state:
        logger.error(f"ENDPOINT {endpoint_name}: Invalid OAuth state. Possible CSRF attack. Received: '{received_state}', Expected: '{stored_state}'")
        return jsonify({"error": "Invalid state parameter. Authorization failed. Please try initiating the authorization again."}), 400
    logger.info(f"OAuth state validated successfully: {received_state}")
    # logger.info(f"OAuth context for this authorization: {service_context}")
    
    if not authorization_code: # This should ideally be caught by state check if state is always set.
        logger.warning(f"ENDPOINT {endpoint_name}: Authorization code missing.")
        return jsonify({"error": "Authorization code missing"}), 400
    
    try:
        token_data = exchange_code_for_tokens_global(
            authorization_code,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET'],
            current_app.config['UNIFIED_REDIRECT_URI']
        )
        
        logger.info(f"ENDPOINT {endpoint_name}: Authorization successful, tokens obtained.")
        if "refresh_token" not in token_data:
            logger.warning("Refresh token was NOT included in the token response from Google (callback). "
                           "This usually means the user has already granted these exact scopes to this app, "
                           "and 'prompt=consent' was not used or wasn't effective in forcing a new refresh token.")
        
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
    requested_services_str = request.args.get('service', 'all') 
    requested_services = [s.strip().lower() for s in requested_services_str.split(',')]
    
    state = str(uuid.uuid4())
    session['oauth_state'] = state # Store state in session for CSRF protection
    session['oauth_requested_services'] = requested_services_str # Optional: store context
    logger.info(f"Initiating OAuth for service(s): {requested_services_str} with state: {state}")

    master_scope_map = {
        "sheets": "https://www.googleapis.com/auth/spreadsheets",
        "docs": "https://www.googleapis.com/auth/documents",
        "drive": "https://www.googleapis.com/auth/drive",
        "slides": "https://www.googleapis.com/auth/presentations",
        "calendar": "https://www.googleapis.com/auth/calendar.events",
        
        # "gmail": "https://www.googleapis.com/auth/gmail.modify",
        # Add other service scopes here as you create agents for them
    }

    final_scopes = set()
    if 'all' in requested_services:
        for s_name in master_scope_map: 
            final_scopes.add(master_scope_map[s_name])
    else:
        for service_name in requested_services:
            if service_name in master_scope_map:
                final_scopes.add(master_scope_map[service_name])
            else:
                logger.warning(f"Unknown Google service '{service_name}' requested for OAuth. Ignoring.")
    
    # Always include basic OpenID Connect scopes for user identification if desired
    final_scopes.add("openid")
    final_scopes.add("https://www.googleapis.com/auth/userinfo.email")
    final_scopes.add("https://www.googleapis.com/auth/userinfo.profile")
    
    if not final_scopes: 
        logger.error("FATAL: No scopes determined for OAuth flow. This should not happen with defaults. Aborting.")
        return jsonify({"error": "Internal server error: No scopes could be determined for OAuth."}), 500

    scope_string = " ".join(list(final_scopes))

    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={current_app.config['CLIENT_ID']}"
        f"&redirect_uri={current_app.config['UNIFIED_REDIRECT_URI']}"
        f"&response_type=code"
        f"&scope={scope_string}"
        f"&access_type=offline"  
        f"&prompt=consent"       
        f"&state={state}"        
    )
    return redirect(auth_url)

# --- List of all blueprints to register ---
# When you add a new agent (e.g., Google_Slides_Agent.py with slides_bp),
# import its blueprint at the top and add it to this list.
all_blueprints = [
    sheets_bp,
    docs_bp,
    drive_bp,
    chat_bp, 
    calendar_bp, 
    # slides_bp, # Uncomment when Google_Slides_Agent.py and slides_bp are created
]

# Register all blueprints
for bp in all_blueprints:
    if bp is not None: 
        app.register_blueprint(bp)
        logger.info(f"Registered blueprint: {bp.name} with prefix {bp.url_prefix if hasattr(bp, 'url_prefix') else 'None'}")
    else:
        logger.warning("Encountered a None blueprint in all_blueprints list during registration.")


@app.route('/')
def index():
    logger.info("Root endpoint '/' hit.")
    registered_prefixes = [bp.url_prefix for bp in all_blueprints if hasattr(bp, 'url_prefix') and bp.url_prefix]
    return jsonify(
        message="Google Suite Unified Agent. Initiate auth at /auth/google. Access services at listed prefixes.",
        available_service_prefixes=registered_prefixes
    )

@app.route('/health')
def health_check():
    logger.info("Health check '/health' endpoint hit.")
    return jsonify(status="UP", message="Google Suite Agent is healthy."), 200

if __name__ == "__main__":
    if not app.secret_key or app.secret_key == "fallback_dev_secret_!@#$": # Check against the actual fallback
        logger.warning("FLASK_SECRET_KEY is using a fallback or not set via env. Session-based OAuth state might be insecure/unreliable for local dev.")
        app.secret_key = os.urandom(24) 
        logger.info(f"Generated temporary Flask secret key for local development as FLASK_SECRET_KEY env var was not set or was the default fallback.")
    
    port = int(os.environ.get("PORT", 8080)) 
    logger.info(f"Starting Google Suite Flask application for local development on port {port}...")
    app.run(debug=True, host="0.0.0.0", port=port)
