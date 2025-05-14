from flask import Flask, request, jsonify, current_app
import logging
import os
import requests
import time # Ensure time is imported if used by token exchange
import uuid # For state generation

# Import the blueprints
from Google_Sheets_Agent import sheets_bp # Assuming this file exists and defines sheets_bp
from Google_Docs_Agent import docs_bp   # Assuming this file exists and defines docs_bp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.config['CLIENT_ID'] = "26763482887-coiufpukc1l69aaulaiov5o0u3en2del.apps.googleusercontent.com"
app.config['CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET", "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")
app.config['TOKEN_URL'] = "https://oauth2.googleapis.com/token"
app.config['REQUEST_TIMEOUT_SECONDS'] = 30
app.config['UNIFIED_REDIRECT_URI'] = "https://serverless.on-demand.io/apps/googlesuite/auth/callback"

app.config['SPECIFIC_USER_CLIENT_ID'] = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
app.config['SPECIFIC_USER_REFRESH_TOKEN'] = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"

if not app.config['CLIENT_SECRET'] or app.config['CLIENT_SECRET'] == "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":
    logger.warning("WARNING: GOOGLE_CLIENT_SECRET is using a placeholder or is not set.")
    if os.getenv("GOOGLE_CLIENT_SECRET") is None:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable is NOT SET.")

# --- Centralized OAuth Token Exchange Function ---
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
        return jsonify({"error": "State parameter missing or invalid"}), 400

    # --- Basic State Handling Example (replace with your actual state management) ---
    # This is a placeholder. In a real app, you'd validate 'received_state'
    # against a state you stored before redirecting the user to Google.
    # For example, if you stored state in a session:
    # if 'oauth_state' not in session or session['oauth_state'] != received_state:
    #     logger.error(f"ENDPOINT {endpoint_name}: Invalid state parameter. Possible CSRF.")
    #     return jsonify({"error": "Invalid state parameter"}), 400
    # service_context = session.pop('oauth_service_context', 'unknown') # e.g., 'sheets' or 'docs'
    # logger.info(f"ENDPOINT {endpoint_name}: State validated. Context: {service_context}")
    # For now, we'll proceed without full state validation for brevity in this example.
    logger.info(f"ENDPOINT {endpoint_name}: Received state: {received_state}. Full state validation is recommended.")


    try:
        token_data = exchange_code_for_tokens_global(
            authorization_code,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET'],
            current_app.config['UNIFIED_REDIRECT_URI']
        )

        logger.info(f"ENDPOINT {endpoint_name}: Authorization successful, tokens obtained.")
        return jsonify({
            "message": "Authorization successful for Google Suite.",
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
    service_type = request.args.get('service', 'sheets') # Default to sheets, or pass ?service=docs
    
    state = str(uuid.uuid4())
    # In a real application, you'd store this 'state' and 'service_type' in the user's session
    # or a temporary database to validate it on callback.
    # session['oauth_state'] = state
    # session['oauth_service_context'] = service_type
    logger.info(f"Initiating OAuth for service: {service_type} with state: {state}")

    scope_map = {
        "sheets": "https://www.googleapis.com/auth/spreadsheets",
        "docs": "https://www.googleapis.com/auth/documents",
        "both": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/documents"
    }
    scope = scope_map.get(service_type, scope_map["sheets"]) # Default to sheets scope

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
    from flask import redirect
    return redirect(auth_url)

app.register_blueprint(sheets_bp)
app.register_blueprint(docs_bp)

@app.route('/')
def index():
    logger.info("Root endpoint '/' hit.")
    return jsonify(message="Google Suite Unified Agent. Access services at /sheets or /docs prefixes, or initiate auth at /auth/google?service=[sheets|docs]")

@app.route('/health')
def health_check():
    logger.info("Health check '/health' endpoint hit.")
    return jsonify(status="UP", message="Google Suite Agent is healthy."), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Google Suite Flask application for local development on port {port}...")
    # For local testing of OAuth, you might need to run on HTTPS if Google enforces it for localhost,
    # or use a tool like ngrok. For simplicity, http for now.
    # To use session for state management locally, you'd need: app.secret_key = os.urandom(24)
    app.run(debug=True, host="0.0.0.0", port=port)
