from flask import Flask, request, jsonify, current_app, redirect
import logging
import os
import requests
import time
import uuid

# Import the blueprints
from Google_Sheets_Agent import sheets_bp
from Google_Docs_Agent import docs_bp

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

# Configuration for a globally accessible "specific user" (e.g., for server-to-server type actions)
# The blueprints' /token endpoints will call the get_global_specific_user_access_token function
app.config['GLOBAL_SPECIFIC_USER_CLIENT_ID'] = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
app.config['GLOBAL_SPECIFIC_USER_REFRESH_TOKEN'] = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"


if not app.config['CLIENT_SECRET'] or app.config['CLIENT_SECRET'] == "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": # Check against placeholder
    logger.warning("WARNING: GOOGLE_CLIENT_SECRET is using a placeholder or is not properly set via environment variable.")
    if os.getenv("GOOGLE_CLIENT_SECRET") is None:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable is NOT SET. OAuth operations will likely fail.")

# --- Centralized Helper Functions ---

def exchange_code_for_tokens_global(authorization_code, client_id, client_secret, redirect_uri_used):
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
    logger.debug(f"Token exchange payload (secrets redacted): { {k: (v if k not in ['client_secret', 'code'] else '...') for k,v in payload.items()} }")
    try:
        response = requests.post(
            current_app.config['TOKEN_URL'],
            data=payload,
            timeout=current_app.config['REQUEST_TIMEOUT_SECONDS']
        )
        response.raise_for_status()
        token_data = response.json()
        duration = time.time() - start_time
        if token_data.get("access_token"):
            logger.info(f"Successfully exchanged code for tokens in {duration:.2f} seconds.")
            return token_data
        else:
            logger.error(f"Token exchange response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in response.")
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Timeout ({current_app.config['REQUEST_TIMEOUT_SECONDS']}s) during token exchange after {duration:.2f} seconds.")
        raise
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        logger.error(f"HTTPError ({e.response.status_code}) during token exchange after {duration:.2f} seconds: {e.response.text if e.response else str(e)}")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during token exchange after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

def get_global_specific_user_access_token():
    logger.info("Attempting to get access token for a specific pre-configured user (Global Function).")
    specific_client_id = current_app.config['GLOBAL_SPECIFIC_USER_CLIENT_ID']
    specific_refresh_token = current_app.config['GLOBAL_SPECIFIC_USER_REFRESH_TOKEN']
    client_secret_global = current_app.config['CLIENT_SECRET'] # Use the main app's client secret
    token_url_global = current_app.config['TOKEN_URL']
    request_timeout_global = current_app.config['REQUEST_TIMEOUT_SECONDS']

    if not client_secret_global:
        logger.error("CRITICAL: Global CLIENT_SECRET not available for specific user token refresh.")
        raise ValueError("Global CLIENT_SECRET not available.")
    if not specific_client_id or not specific_refresh_token:
        logger.error("CRITICAL: GLOBAL_SPECIFIC_USER_CLIENT_ID or GLOBAL_SPECIFIC_USER_REFRESH_TOKEN not configured.")
        raise ValueError("Specific user credentials not configured.")

    payload = {
        "client_id": specific_client_id,
        "client_secret": client_secret_global,
        "refresh_token": specific_refresh_token,
        "grant_type": "refresh_token"
    }
    logger.debug(f"Global specific user token refresh payload (secrets redacted): { {k: (v if k not in ['client_secret', 'refresh_token'] else '...') for k,v in payload.items()} }")
    start_time = time.time()
    try:
        response = requests.post(token_url_global, data=payload, timeout=request_timeout_global)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token:
            logger.info(f"Successfully obtained access token for specific user (Global) in {duration:.2f}s. Expires in: {token_data.get('expires_in')}s")
            return access_token
        else:
            logger.error(f"Global specific user token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in global specific user refresh response.")
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Timeout ({request_timeout_global}s) during global specific user token refresh after {duration:.2f} seconds.")
        raise
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        error_text = e.response.text if e.response else str(e)
        logger.error(f"HTTPError ({e.response.status_code}) during global specific user token refresh after {duration:.2f} seconds: {error_text}")
        if "invalid_grant" in error_text:
            logger.warning("Global specific user token refresh failed with 'invalid_grant'. Refresh token may be expired/revoked or lack necessary scopes.")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during global specific user token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise


# --- Unified OAuth Callback ---
@app.route('/auth/callback', methods=['GET'])
def unified_oauth_callback():
    endpoint_name = "/auth/callback (unified)"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")

    authorization_code = request.args.get('code')
    received_state = request.args.get('state')

    if not authorization_code:
        logger.warning(f"ENDPOINT {endpoint_name}: Authorization code missing.")
        return jsonify({"error": "Authorization code missing"}), 400
    if not received_state:
        logger.warning(f"ENDPOINT {endpoint_name}: State parameter missing.")
        # For robust security, you MUST validate this state against one you stored
        # before redirecting the user to Google. This prevents CSRF.
        # e.g., if 'oauth_state' not in session or session['oauth_state'] != received_state: handle error
        logger.warning(f"ENDPOINT {endpoint_name}: State received but not validated in this example. Implement state validation.")
        # return jsonify({"error": "State parameter invalid or missing"}), 400


    try:
        # Use the global CLIENT_ID from app.config for the main user OAuth flow
        token_data = exchange_code_for_tokens_global(
            authorization_code,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET'],
            current_app.config['UNIFIED_REDIRECT_URI']
        )

        logger.info(f"ENDPOINT {endpoint_name}: Authorization successful, tokens obtained.")
        # Here you would typically store the refresh_token securely, associated with the user.
        # You might also use the 'received_state' to determine which service (Sheets/Docs)
        # the user was authorizing if you need to store that context.
        return jsonify({
            "message": "Authorization successful for Google Suite. IMPORTANT: Securely store the refresh_token.",
            "tokens": token_data
        })

    except requests.exceptions.HTTPError as e:
        error_detail = e.response.text if e.response else str(e)
        status_code = e.response.status_code if e.response is not None else 500
        logger.error(f"ENDPOINT {endpoint_name}: HTTPError during token exchange: {error_detail}", exc_info=True)
        return jsonify({"error": f"Failed to exchange code for tokens (HTTP {status_code})", "details": error_detail}), status_code
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Error during token exchange: {str(e)}", exc_info=True)
        return jsonify({"error": f"Failed to exchange authorization code for tokens: {str(e)}"}), 500

# --- Endpoint to initiate OAuth flow (example) ---
@app.route('/auth/google')
def auth_google():
    service_type_param = request.args.get('service', 'both') # Default to 'both', or pass ?service=sheets or ?service=docs
    
    state = str(uuid.uuid4())
    # In a real application, you'd store this 'state' and perhaps 'service_type_param'
    # in the user's session or a temporary database to validate it on callback.
    # Example using session (requires app.secret_key to be set):
    # from flask import session
    # session['oauth_state'] = state
    # session['oauth_service_context'] = service_type_param
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
        f"&access_type=offline"  # To get a refresh token
        f"&prompt=consent"       # To ensure a refresh token is typically re-issued
        f"&state={state}"
    )
    return redirect(auth_url)

# Register the blueprints
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
    # For local OAuth testing that requires a session for state, set a secret key.
    # app.secret_key = os.urandom(24) # Or a fixed string for dev, but random for prod if session used
    
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Google Suite Flask application for local development on port {port}...")
    # When running locally, for OAuth redirects to work with http://localhost,
    # ensure "http://localhost:{port}/auth/callback" is an authorized redirect URI in your
    # Google Cloud Console if you're testing the full OAuth flow locally.
    # Otherwise, the UNIFIED_REDIRECT_URI will be used by the /auth/google endpoint.
    app.run(debug=True, host="0.0.0.0", port=port)
