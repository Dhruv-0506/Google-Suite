from flask import Flask, jsonify
import logging
import os

# Import the blueprints from your agent files
from Google_Sheets_Agent import sheets_bp
from Google_Docs_Agent import docs_bp

# --- Central Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# This logger will be for messages originating from Google_Suite.py itself
logger = logging.getLogger(__name__) # Will be 'Google_Suite' or '__main__' if run directly

# Create the main Flask application instance
app = Flask(__name__)

# --- Global Configuration (Centralized) ---
# Load sensitive or environment-specific configurations from environment variables
app.config['CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET", "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") # Fallback for local dev
app.config['TOKEN_URL'] = "https://oauth2.googleapis.com/token"
app.config['REQUEST_TIMEOUT_SECONDS'] = 30

# Common Client ID if applicable to both Sheets and Docs OAuth flows,
# otherwise, CLIENT_ID might remain specific within each agent's blueprint if they differ.
# If they are the same, define it here:
app.config['COMMON_CLIENT_ID'] = "26763482887-coiufpukc1l69aaulaiov5o0u3en2del.apps.googleusercontent.com"

# Specific Client ID for the hardcoded refresh token user (if this is a global utility)
app.config['SPECIFIC_USER_CLIENT_ID'] = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
app.config['SPECIFIC_USER_REFRESH_TOKEN'] = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"


# Check if critical configuration is missing at startup
if not app.config['CLIENT_SECRET'] or app.config['CLIENT_SECRET'] == "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": # Check against placeholder
    logger.warning("WARNING: GOOGLE_CLIENT_SECRET is using a placeholder or is not set. OAuth operations might fail.")
    if os.getenv("GOOGLE_CLIENT_SECRET") is None:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable is NOT SET.")


# --- Shared Helper Functions (Example - if OAuth logic is identical) ---
# You might move the `exchange_code_for_tokens` and `get_access_token` functions
# from the agent files here if their logic is truly identical and they can use
# app.config values or passed parameters.
# For example:
# def shared_get_access_token(refresh_token, client_id, client_secret):
#     # ... implementation using passed client_id and client_secret ...
#     pass
# Then in your agent files, you would call:
# from Google_Suite import shared_get_access_token
# access_token = shared_get_access_token(refresh_token, app.config['COMMON_CLIENT_ID'], app.config['CLIENT_SECRET'])
# For simplicity in this iteration, we've kept them in the agent files and parameterized them.

# --- Global Endpoints (Optional) ---

# Example: A global /token endpoint for the specific hardcoded user
# This re-implements the logic here, or you could import it if it's cleanly separated.
# For this example, let's assume the token functions in agent files are parameterized.
# If `get_specific_user_access_token` was moved to a shared util or here:
# @app.route('/token', methods=['GET'])
# def global_specific_user_token_endpoint():
#     endpoint_name = "/token"
#     logger.info(f"ENDPOINT {endpoint_name}: Request received for specific user access token.")
#     try:
#         # This would call a centralized version of get_specific_user_access_token
#         # that uses app.config['SPECIFIC_USER_CLIENT_ID'], app.config['SPECIFIC_USER_REFRESH_TOKEN'],
#         # and app.config['CLIENT_SECRET']
#         # For now, the /token endpoints are within each blueprint.
#         # If you want a single global one, you'd refactor accordingly.
#         # Example of calling a hypothetical shared function:
#         # access_token = get_shared_specific_user_access_token(
#         #    app.config['SPECIFIC_USER_CLIENT_ID'],
#         #    app.config['CLIENT_SECRET'],
#         #    app.config['SPECIFIC_USER_REFRESH_TOKEN']
#         # )
#         # logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token.")
#         # return jsonify({"success": True, "access_token": access_token})
#         return jsonify({"message": "Global /token endpoint placeholder. Implement if needed."})
#     except Exception as e:
#         logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
#         return jsonify({"success": False, "error": f"Failed to obtain access token: {str(e)}"}), 500

# Example: A single global OAuth callback
# @app.route('/auth/callback', methods=['GET'])
# def global_oauth_callback():
#     code = request.args.get('code')
#     state = request.args.get('state') # Use state to determine if it's for Sheets or Docs
#     logger.info(f"GLOBAL OAuth Callback: code received (starts with {code[:10]}), state: {state}")
#     # Based on state, you might use different client_ids or redirect_uris if they differ.
#     # Or, if one client_id has scopes for both, this can be simpler.
#     # For now, the callbacks are within each blueprint.
#     return jsonify({"message": f"Global OAuth callback received. State: {state}. Implement if needed."})


# Register the blueprints from the agent files
app.register_blueprint(sheets_bp) # Endpoints will be available under /sheets/...
app.register_blueprint(docs_bp)   # Endpoints will be available under /docs/...

# --- Root and Health Check Endpoints ---
@app.route('/')
def index():
    logger.info("Root endpoint '/' hit.")
    return jsonify(message="Google Suite Unified Agent. Access services at /sheets or /docs prefixes.")

@app.route('/health')
def health_check():
    logger.info("Health check '/health' endpoint hit.")
    return jsonify(status="UP", message="Google Suite Agent is healthy."), 200

# --- Main Execution (for local development) ---
# This block is only executed when you run `python Google_Suite.py` directly.
# Gunicorn (or other WSGI servers) will not execute this block; they will
# directly use the `app` object defined above.
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080)) # Use PORT from env var, default to 8080
    logger.info(f"Starting Google Suite Flask application for local development on port {port}...")
    # Note: debug=True is not recommended for production deployments.
    # Gunicorn will handle production serving.
    app.run(debug=True, host="0.0.0.0", port=port)
