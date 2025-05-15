from flask import Flask, request, jsonify, current_app, redirect # REMOVED: session
import logging
import os
import requests
import time
import uuid # Still used to generate a state to send to Google, even if not validated

# Import the blueprints
from Google_Sheets_Agent import sheets_bp
from Google_Docs_Agent import docs_bp
from Google_Drive_Agent import drive_bp
from Chat_Agent_Blueprint import chat_bp
from Google_Calendar_Agent import calendar_bp # Assuming this is now present
# from Google_Slides_Agent import slides_bp

# Assuming shared_utils.py contains:
from shared_utils import exchange_code_for_tokens_global, get_global_specific_user_access_token, get_access_token

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

app.config['GLOBAL_SPECIFIC_USER_CLIENT_ID'] = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
app.config['GLOBAL_SPECIFIC_USER_REFRESH_TOKEN'] = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"

# REMOVED: app.secret_key related lines as session is not used for state validation

if not app.config['CLIENT_SECRET'] or app.config['CLIENT_SECRET'] == "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":
    logger.warning("WARNING: GOOGLE_CLIENT_SECRET is using a placeholder or is not properly set via environment variable.")
    if os.getenv("GOOGLE_CLIENT_SECRET") is None:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable is NOT SET. OAuth operations will likely fail.")


# exchange_code_for_tokens_global is now expected to be in shared_utils.py
# get_global_specific_user_access_token is also expected to be in shared_utils.py

# --- Unified OAuth Callback (STATE VALIDATION REMOVED - SECURITY RISK) ---
@app.route('/auth/callback', methods=['GET'])
def unified_oauth_callback():
    endpoint_name = "/auth/callback (unified)"
    authorization_code = request.args.get('code')
    received_state = request.args.get('state') # State is still received from Google

    logger.info(f"DEBUG CALLBACK: unified_oauth_callback HIT.")
    logger.info(f"DEBUG CALLBACK:   Received authorization_code (first 20): {authorization_code[:20] if authorization_code else 'None'}")
    logger.info(f"DEBUG CALLBACK:   Received state: {received_state}")
    logger.warning(f"ENDPOINT {endpoint_name}: OAuth state validation has been SKIPPED. This is a SECURITY RISK (CSRF vulnerability).")


    if not authorization_code:
        logger.warning(f"ENDPOINT {endpoint_name}: Authorization code missing.")
        return jsonify({"error": "Authorization code missing"}), 400
    
    # If you still wanted to check if state was sent AT ALL by Google (though not validating its value)
    # if not received_state:
    #     logger.warning(f"ENDPOINT {endpoint_name}: State parameter was expected from Google but is missing.")
    #     return jsonify({"error": "State parameter missing from Google's response"}), 400

    try:
        token_data = exchange_code_for_tokens_global( # This function is now expected in shared_utils
            authorization_code,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET'],
            current_app.config['UNIFIED_REDIRECT_URI']
        )
        
        logger.info(f"ENDPOINT {endpoint_name}: Authorization successful, tokens obtained.")
        if "refresh_token" not in token_data:
            logger.warning("Refresh token was NOT included in the token response from Google (callback).")
        
        return jsonify({
            "message": "Authorization successful! IMPORTANT: Securely store the 'refresh_token' if received.",
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

# --- Endpoint to initiate OAuth flow (State is generated but not stored for validation) ---
@app.route('/auth/google')
def auth_google():
    requested_services_str = request.args.get('service', 'all') 
    
    state = str(uuid.uuid4()) # Generate state to send to Google (good practice, even if not validated on return)
    # REMOVED: session['oauth_state'] = state
    # REMOVED: session['oauth_requested_services'] = requested_services_str 
    logger.info(f"Initiating OAuth for service(s): {requested_services_str} with state: {state} (State WILL NOT BE VALIDATED ON CALLBACK)")

    master_scope_map = {
        "sheets": "https://www.googleapis.com/auth/spreadsheets",
        "docs": "https://www.googleapis.com/auth/documents",
        "drive": "https://www.googleapis.com/auth/drive",
        "slides": "https://www.googleapis.com/auth/presentations",
        "calendar": "https://www.googleapis.com/auth/calendar.events",
    }

    final_scopes = set()
    if 'all' in requested_services.split(','): # check if 'all' is in the list
        for s_name in master_scope_map: 
            final_scopes.add(master_scope_map[s_name])
    else:
        for service_name in requested_services.split(','):
            service_name = service_name.strip().lower()
            if service_name in master_scope_map:
                final_scopes.add(master_scope_map[service_name])
            else:
                logger.warning(f"Unknown Google service '{service_name}' requested for OAuth. Ignoring.")
    
    final_scopes.add("openid")
    final_scopes.add("https://www.googleapis.com/auth/userinfo.email")
    final_scopes.add("https://www.googleapis.com/auth/userinfo.profile")
    
    if not final_scopes: 
        logger.error("FATAL: No scopes determined for OAuth flow. Aborting.")
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
        f"&state={state}" # State is still sent to Google
    )
    return redirect(auth_url)

# --- List of all blueprints to register ---
all_blueprints = [
    sheets_bp,
    docs_bp,
    drive_bp,
    chat_bp,
    calendar_bp,
    # slides_bp, 
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
    # REMOVED: app.secret_key related logic for local dev as sessions are not used for state
    port = int(os.environ.get("PORT", 8080)) 
    logger.info(f"Starting Google Suite Flask application for local development on port {port}...")
    app.run(debug=True, host="0.0.0.0", port=port)
