from flask import jsonify, request, Blueprint # Import Blueprint
import os
import requests
import logging
import time

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Logging Configuration ---
# This will likely be centralized in Google_Suite.py, but keeping it here
# won't harm if Google_Suite.py also configures logging.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# The logger name will be based on the module name (e.g., 'Google_Docs_Agent')
logger = logging.getLogger(__name__)

# Create a Blueprint
# All routes defined in this file will be prefixed with /docs
docs_bp = Blueprint('docs_agent', __name__, url_prefix='/docs')

# --- Configuration (Consider centralizing in Google_Suite.py) ---
# These might be shared with the Sheets agent. If so, define once in Google_Suite.py
# and pass them to functions or access them via app.config if set there.
# For now, keeping them here to reflect minimal changes to this file's core logic
# beyond blueprinting.
CLIENT_ID = "26763482887-coiufpukc1l69aaulaiov5o0u3en2del.apps.googleusercontent.com"
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "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") # Prioritize env var
TOKEN_URL = "https://oauth2.googleapis.com/token"
# REDIRECT_URI might be specific to a global auth handler or need to be conditional
# If this /auth/callback endpoint remains within this blueprint, this REDIRECT_URI applies to it.
REDIRECT_URI = "https://serverless.on-demand.io/apps/google/auth/callback/docs" # Made more specific if needed
REQUEST_TIMEOUT_SECONDS = 30

# --- OAuth and Token Helper Functions (Consider centralizing in Google_Suite.py or a shared_utils.py) ---
# These functions are likely identical to those in Google_Sheets_Agent.py.
# They should ideally be defined once.
def exchange_code_for_tokens(authorization_code, client_id, client_secret, redirect_uri): # Added params for flexibility
    logger.info(f"Attempting to exchange authorization code for tokens. Code starts with: {authorization_code[:10]}...")
    start_time = time.time()
    if not client_secret:
        logger.error("CRITICAL: Client secret not available for token exchange.")
        raise ValueError("Client secret not available.")
    payload = {
        "code": authorization_code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"
    }
    logger.debug(f"Token exchange payload (secrets redacted): { {k: (v if k not in ['client_secret', 'code'] else '...') for k,v in payload.items()} }")
    try:
        response = requests.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
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
        logger.error(f"Timeout ({REQUEST_TIMEOUT_SECONDS}s) during token exchange after {duration:.2f} seconds.")
        raise
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        logger.error(f"HTTPError ({e.response.status_code}) during token exchange after {duration:.2f} seconds: {e.response.text if e.response else str(e)}")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during token exchange after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

def get_access_token(refresh_token, client_id, client_secret): # Added params for flexibility
    logger.info(f"Attempting to get new access token using refresh token (starts with: {refresh_token[:10]}...).")
    start_time = time.time()
    if not client_secret:
        logger.error("CRITICAL: Client secret not available for token refresh.")
        raise ValueError("Client secret not available.")
    payload = {
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"
    }
    logger.debug(f"Token refresh payload (secrets redacted): { {k: (v if k not in ['client_secret', 'refresh_token'] else '...') for k,v in payload.items()} }")
    try:
        response = requests.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token:
            logger.info(f"Successfully obtained new access token via refresh in {duration:.2f} seconds. Expires in: {token_data.get('expires_in')}s")
            return access_token
        else:
            logger.error(f"Token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in refresh response.")
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Timeout ({REQUEST_TIMEOUT_SECONDS}s) during token refresh after {duration:.2f} seconds.")
        raise
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        logger.error(f"HTTPError ({e.response.status_code}) during token refresh after {duration:.2f} seconds: {e.response.text if e.response else str(e)}")
        if "invalid_grant" in (e.response.text if e.response else ""):
            logger.warning("Token refresh failed with 'invalid_grant'. Refresh token may be expired or revoked (ensure it has Docs API scope if new).")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

# --- Google Docs API Service Builder ---
def get_docs_service(access_token):
    logger.info("Building Google Docs API service object...")
    if not access_token:
        logger.error("Cannot build docs service: access_token is missing.")
        raise ValueError("Access token is required to build docs service.")
    try:
        creds = OAuthCredentials(token=access_token)
        service = build("docs", "v1", credentials=creds)
        logger.info("Google Docs API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Docs API service object: {str(e)}", exc_info=True)
        raise

# --- Google Docs API Wrapper Functions ---

def api_get_document_content(service, document_id):
    logger.info(f"API: Getting content for document '{document_id}'.")
    start_time = time.time()
    try:
        document = service.documents().get(documentId=document_id, fields='body,title,documentId,documentStyle,namedStyles,revisionId,suggestionsViewMode').execute()
        duration = time.time() - start_time
        logger.info(f"API: Document content retrieval successful in {duration:.2f}s. Title: {document.get('title')}")
        return document
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError getting document content after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error getting document content after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_batch_update_document(service, document_id, requests_body):
    logger.info(f"API: Performing batch update on document '{document_id}'. Number of requests: {len(requests_body)}")
    logger.debug(f"API: Batch update request body: {requests_body}")
    start_time = time.time()
    try:
        result = service.documents().batchUpdate(documentId=document_id, body={'requests': requests_body}).execute()
        duration = time.time() - start_time
        logger.info(f"API: Batch update successful in {duration:.2f}s. Result: {result}")
        return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError during batch update after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error during batch update after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_insert_text(service, document_id, text_to_insert, location_index=None, segment_id=None):
    logger.info(f"API: Inserting text into document '{document_id}'. Text: '{text_to_insert[:50]}...'")
    if location_index is not None:
        location = {"index": location_index}
        if segment_id:
            location["segmentId"] = segment_id
        insert_request = {"insertText": {"location": location, "text": text_to_insert}}
    else:
        end_of_segment = {}
        if segment_id:
            end_of_segment["segmentId"] = segment_id
        insert_request = {"insertText": {"endOfSegmentLocation": end_of_segment, "text": text_to_insert}}
    return api_batch_update_document(service, document_id, [insert_request])

def api_delete_content_range(service, document_id, start_index, end_index, segment_id=None):
    logger.info(f"API: Deleting content range in document '{document_id}' from {start_index} to {end_index}.")
    content_range = {"startIndex": start_index, "endIndex": end_index}
    if segment_id:
        content_range["segmentId"] = segment_id
    delete_request = {"deleteContentRange": {"range": content_range}}
    return api_batch_update_document(service, document_id, [delete_request])

def api_update_paragraph_style(service, document_id, start_index, end_index, named_style_type, segment_id=None):
    logger.info(f"API: Updating paragraph style to '{named_style_type}' in document '{document_id}' from {start_index} to {end_index}.")
    style_range = {"startIndex": start_index, "endIndex": end_index}
    if segment_id:
        style_range["segmentId"] = segment_id
    update_request = {
        "updateParagraphStyle": {
            "range": style_range,
            "paragraphStyle": {"namedStyleType": named_style_type},
            "fields": "namedStyleType"
        }
    }
    return api_batch_update_document(service, document_id, [update_request])

def api_update_text_style(service, document_id, start_index, end_index, bold=None, italic=None, underline=None, segment_id=None):
    logger.info(f"API: Updating text style in document '{document_id}' from {start_index} to {end_index}. B:{bold}, I:{italic}, U:{underline}")
    style_range = {"startIndex": start_index, "endIndex": end_index}
    if segment_id:
        style_range["segmentId"] = segment_id
    text_style = {}
    fields_to_update = []
    if bold is not None:
        text_style["bold"] = bold
        fields_to_update.append("bold")
    if italic is not None:
        text_style["italic"] = italic
        fields_to_update.append("italic")
    if underline is not None:
        text_style["underline"] = underline
        fields_to_update.append("underline")
    if not fields_to_update:
        logger.warning("API: No text style changes specified.")
        return {"warning": "No text style changes specified."}
    update_request = {
        "updateTextStyle": {
            "range": style_range,
            "textStyle": text_style,
            "fields": ",".join(fields_to_update)
        }
    }
    return api_batch_update_document(service, document_id, [update_request])

def api_insert_table(service, document_id, rows, columns, location_index=None, segment_id=None):
    logger.info(f"API: Inserting {rows}x{columns} table into document '{document_id}'.")
    if location_index is not None:
        location = {"index": location_index}
        if segment_id:
            location["segmentId"] = segment_id
        insert_request = {"insertTable": {"location": location, "rows": rows, "columns": columns}}
    else:
        end_of_segment = {}
        if segment_id:
            end_of_segment["segmentId"] = segment_id
        insert_request = {"insertTable": {"endOfSegmentLocation": end_of_segment, "rows": rows, "columns": columns}}
    return api_batch_update_document(service, document_id, [insert_request])

def api_create_document(service, title):
    logger.info(f"API: Creating new document with title '{title}'.")
    start_time = time.time()
    try:
        body = {'title': title}
        doc = service.documents().create(body=body).execute()
        duration = time.time() - start_time
        logger.info(f"API: Document creation successful in {duration:.2f}s. Document ID: {doc.get('documentId')}")
        return doc
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError creating document after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error creating document after {duration:.2f}s: {str(e)}", exc_info=True); raise

# --- Specific User Token Helper (Consider centralizing) ---
# This function uses a specific_client_id and specific_refresh_token.
# If these are globally unique and used by both Sheets & Docs for this purpose, centralize it.
# Otherwise, it might need to be parameterized or duplicated if client_ids differ.
def get_specific_user_access_token():
    logger.info("Attempting to get access token for a specific pre-configured user.")
    # These specific values might need to be managed in Google_Suite.py or env vars
    specific_client_id = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
    specific_refresh_token = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"

    global CLIENT_SECRET # Access the module-level or globally defined CLIENT_SECRET
    if not CLIENT_SECRET:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET not available for specific user token refresh.")
        raise ValueError("GOOGLE_CLIENT_SECRET not available.")
    payload = {
        "client_id": specific_client_id, "client_secret": CLIENT_SECRET,
        "refresh_token": specific_refresh_token, "grant_type": "refresh_token"
    }
    # ... (rest of the get_specific_user_access_token logic from original code)
    logger.debug(f"Specific user token refresh payload (secrets redacted): { {k: (v if k not in ['client_secret', 'refresh_token'] else '...') for k,v in payload.items()} }")
    start_time = time.time()
    try:
        response = requests.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token:
            logger.info(f"Successfully obtained access token for specific user in {duration:.2f}s. Expires in: {token_data.get('expires_in')}s")
            return access_token
        else:
            logger.error(f"Specific user token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in specific user refresh response.")
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        logger.error(f"HTTPError ({e.response.status_code}) during specific user token refresh after {duration:.2f} seconds: {e.response.text if e.response else str(e)}")
        if "invalid_grant" in (e.response.text if e.response else ""):
            logger.warning("Specific user token refresh failed with 'invalid_grant'. Refresh token may be expired, revoked, or lack Docs API scope.")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during specific user token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

# --- Flask Endpoints for Docs (Now on docs_bp Blueprint) ---

# OAuth Callback Endpoint: If this is truly global, it should be in Google_Suite.py.
# If it's specific to Docs (e.g., different client_id or redirect_uri handling), it can stay.
# For now, keeping it here, assuming it might be specific or for demonstration.
# Remember, the URL will be /docs/auth/callback
@docs_bp.route('/auth/callback', methods=['GET'])
def oauth2callback_docs_endpoint(): # Renamed for clarity if it stays here
    endpoint_name = "/docs/auth/callback"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    authorization_code = request.args.get('code')
    if authorization_code:
        try:
            # Use the CLIENT_ID and REDIRECT_URI defined in this module for Docs
            token_data = exchange_code_for_tokens(authorization_code, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
            logger.info(f"ENDPOINT {endpoint_name}: Docs authorization successful, tokens obtained.")
            return jsonify({"message": "Docs authorization successful.", "tokens": token_data})
        except Exception as e:
            logger.error(f"ENDPOINT {endpoint_name}: Error during token exchange: {str(e)}", exc_info=True)
            return jsonify({"error": f"Failed to exchange authorization code for tokens: {str(e)}"}), 500
    else:
        logger.warning(f"ENDPOINT {endpoint_name}: Authorization code missing in request.")
        return jsonify({"error": "Authorization code missing"}), 400

# Specific User Token Endpoint: This might be global (in Google_Suite.py on /token)
# or if it serves a purpose specific to docs (e.g., different hardcoded refresh token)
# it could be /docs/token. For now, keeping it distinct.
@docs_bp.route('/token', methods=['GET'])
def specific_user_docs_token_endpoint(): # Renamed for clarity
    endpoint_name = "/docs/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received for specific user Docs access token.")
    try:
        access_token = get_specific_user_access_token() # Uses the hardcoded values within that function
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user (Docs context).")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": f"Failed to obtain access token: {str(e)}"}), 500


@docs_bp.route('/create', methods=['POST'])
def create_document_endpoint():
    endpoint_name = "/docs/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('title', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing 'title' or 'refresh_token'.")
            return jsonify({"success": False, "error": "Missing 'title' or 'refresh_token'"}), 400
        title = data['title']
        refresh_token = data['refresh_token']
        # Use the module-level CLIENT_ID and CLIENT_SECRET for get_access_token
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        doc_info = api_create_document(service, title)
        logger.info(f"ENDPOINT {endpoint_name}: Document creation successful.")
        return jsonify({"success": True, "message": "Document created successfully.", "document": doc_info})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/read', methods=['POST'])
def read_document_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/read"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not data or 'refresh_token' not in data:
            logger.warning(f"ENDPOINT {endpoint_name}: Missing 'refresh_token' in JSON body.")
            return jsonify({"success": False, "error": "Missing 'refresh_token' in JSON body"}), 400
        refresh_token = data['refresh_token']
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        document_content = api_get_document_content(service, document_id)
        logger.info(f"ENDPOINT {endpoint_name}: Document read successful.")
        return jsonify({"success": True, "document": document_content})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/insert_text', methods=['POST'])
def insert_text_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/insert_text"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('text', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing 'text' or 'refresh_token'.")
            return jsonify({"success": False, "error": "Missing 'text' or 'refresh_token'"}), 400
        text_to_insert = data['text']
        refresh_token = data['refresh_token']
        location_index = data.get('location_index')
        segment_id = data.get('segment_id')
        if location_index is not None: location_index = int(location_index)
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        result = api_insert_text(service, document_id, text_to_insert, location_index, segment_id)
        logger.info(f"ENDPOINT {endpoint_name}: Text insertion successful.")
        return jsonify({"success": True, "message": "Text inserted successfully.", "details": result})
    except ValueError: logger.warning(f"ENDPOINT {endpoint_name}: Invalid non-integer input for location_index.", exc_info=True); return jsonify({"success": False, "error": "location_index must be an integer if provided."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/delete_range', methods=['POST'])
def delete_range_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/delete_range"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('start_index', 'end_index', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing 'start_index', 'end_index', or 'refresh_token'.")
            return jsonify({"success": False, "error": "Missing 'start_index', 'end_index', or 'refresh_token'"}), 400
        start_index = int(data['start_index'])
        end_index = int(data['end_index'])
        refresh_token = data['refresh_token']
        segment_id = data.get('segment_id')
        if start_index < 0 or end_index <= start_index: # Corrected: start_index can be 0
            logger.warning(f"ENDPOINT {endpoint_name}: Invalid indices. start_index: {start_index}, end_index: {end_index}")
            return jsonify({"success": False, "error": "Invalid 'start_index' or 'end_index'. Ensure 0 <= start_index < end_index."}), 400
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        result = api_delete_content_range(service, document_id, start_index, end_index, segment_id)
        logger.info(f"ENDPOINT {endpoint_name}: Content deletion successful.")
        return jsonify({"success": True, "message": "Content deleted successfully.", "details": result})
    except ValueError: logger.warning(f"ENDPOINT {endpoint_name}: Invalid non-integer input for indices.", exc_info=True); return jsonify({"success": False, "error": "start_index and end_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/format/paragraph', methods=['POST'])
def format_paragraph_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/format/paragraph"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        required_fields = ['start_index', 'end_index', 'style_type', 'refresh_token']
        if not all(k in data for k in required_fields):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing one or more required fields: {', '.join(required_fields)}.")
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(required_fields)}"}), 400
        start_index = int(data['start_index'])
        end_index = int(data['end_index'])
        style_type = data['style_type']
        refresh_token = data['refresh_token']
        segment_id = data.get('segment_id')
        valid_named_styles = ["NORMAL_TEXT", "TITLE", "SUBTITLE", "HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6"]
        if style_type not in valid_named_styles:
            logger.warning(f"ENDPOINT {endpoint_name}: Invalid style_type '{style_type}'.")
            return jsonify({"success": False, "error": f"Invalid 'style_type'. Must be one of {valid_named_styles}"}), 400
        if start_index < 0 or end_index <= start_index: # Corrected
            logger.warning(f"ENDPOINT {endpoint_name}: Invalid indices for paragraph formatting.")
            return jsonify({"success": False, "error": "Invalid 'start_index' or 'end_index'. Ensure 0 <= start_index < end_index."}), 400
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        result = api_update_paragraph_style(service, document_id, start_index, end_index, style_type, segment_id)
        logger.info(f"ENDPOINT {endpoint_name}: Paragraph style update successful.")
        return jsonify({"success": True, "message": "Paragraph style updated.", "details": result})
    except ValueError: logger.warning(f"ENDPOINT {endpoint_name}: Invalid non-integer input for indices.", exc_info=True); return jsonify({"success": False, "error": "start_index and end_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/format/text', methods=['POST'])
def format_text_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/format/text"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        required_fields = ['start_index', 'end_index', 'refresh_token']
        if not all(k in data for k in required_fields):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing one or more required fields: {', '.join(required_fields)}.")
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(required_fields)}"}), 400
        if not (data.get('bold') is not None or data.get('italic') is not None or data.get('underline') is not None):
            logger.warning(f"ENDPOINT {endpoint_name}: At least one formatting option (bold, italic, underline) must be provided.")
            return jsonify({"success": False, "error": "At least one formatting option (bold, italic, underline) must be provided."}), 400
        start_index = int(data['start_index'])
        end_index = int(data['end_index'])
        refresh_token = data['refresh_token']
        bold = data.get('bold')
        italic = data.get('italic')
        underline = data.get('underline')
        segment_id = data.get('segment_id')
        if start_index < 0 or end_index <= start_index: # Corrected
            logger.warning(f"ENDPOINT {endpoint_name}: Invalid indices for text formatting.")
            return jsonify({"success": False, "error": "Invalid 'start_index' or 'end_index'. Ensure 0 <= start_index < end_index."}), 400
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        result = api_update_text_style(service, document_id, start_index, end_index, bold, italic, underline, segment_id)
        logger.info(f"ENDPOINT {endpoint_name}: Text style update successful.")
        return jsonify({"success": True, "message": "Text style updated.", "details": result})
    except ValueError: logger.warning(f"ENDPOINT {endpoint_name}: Invalid non-integer input for indices.", exc_info=True); return jsonify({"success": False, "error": "start_index and end_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/insert_table', methods=['POST'])
def insert_table_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/insert_table"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        required_fields = ['rows', 'columns', 'refresh_token']
        if not all(k in data for k in required_fields):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing one or more required fields: {', '.join(required_fields)}.")
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(required_fields)}"}), 400
        rows = int(data['rows'])
        columns = int(data['columns'])
        refresh_token = data['refresh_token']
        location_index = data.get('location_index')
        segment_id = data.get('segment_id')
        if location_index is not None: location_index = int(location_index)
        if rows < 1 or columns < 1:
            logger.warning(f"ENDPOINT {endpoint_name}: Rows and columns must be positive integers.")
            return jsonify({"success": False, "error": "Rows and columns must be positive integers."}), 400
        access_token = get_access_token(refresh_token, CLIENT_ID, CLIENT_SECRET)
        service = get_docs_service(access_token)
        result = api_insert_table(service, document_id, rows, columns, location_index, segment_id)
        logger.info(f"ENDPOINT {endpoint_name}: Table insertion successful.")
        return jsonify({"success": True, "message": "Table inserted successfully.", "details": result})
    except ValueError: logger.warning(f"ENDPOINT {endpoint_name}: Invalid non-integer input for rows, columns, or location_index.", exc_info=True); return jsonify({"success": False, "error": "rows, columns, and location_index (if provided) must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

# Note: The `if __name__ == "__main__":` block has been removed.
# This file is now intended to be imported as a module, and its 'docs_bp'
# Blueprint registered by the main application in Google_Suite.py.
