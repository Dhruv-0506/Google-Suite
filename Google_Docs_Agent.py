from flask import jsonify, request, Blueprint, current_app
import logging
import time
# os import is no longer needed here if all config comes from current_app
import requests # Kept for get_access_token

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Import the centralized function for specific user tokens from shared_utils
from shared_utils import get_global_specific_user_access_token

logger = logging.getLogger(__name__)
docs_bp = Blueprint('docs_agent', __name__, url_prefix='/docs')

# --- REMOVED Module-Level Configurations ---
# --- REMOVED exchange_code_for_tokens function ---

# --- get_access_token uses current_app.config and passed parameters ---
def get_access_token(refresh_token, client_id, client_secret):
    logger.info(f"Docs Agent: Getting access token for refresh token: {refresh_token[:10]}...")
    start_time = time.time()
    if not client_secret:
        logger.error("CRITICAL: Client secret not available for token refresh (Docs Agent).")
        raise ValueError("Client secret not available.")

    token_url = current_app.config.get('TOKEN_URL')
    request_timeout = current_app.config.get('REQUEST_TIMEOUT_SECONDS', 30)

    if not token_url:
        logger.error("CRITICAL: TOKEN_URL not configured in the application.")
        raise ValueError("TOKEN_URL not configured.")

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    logger.debug(f"Token refresh payload (secrets redacted): { {k: (v if k not in ['client_secret', 'refresh_token'] else '...') for k,v in payload.items()} }")
    try:
        response = requests.post(token_url, data=payload, timeout=request_timeout)
        response.raise_for_status()
        token_data = response.json()
        access_token_val = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token_val:
            logger.info(f"Successfully obtained new access token via refresh in {duration:.2f} seconds. Expires in: {token_data.get('expires_in')}s")
            return access_token_val
        else:
            logger.error(f"Token refresh response missing access_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token not found in refresh response.")
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        logger.error(f"HTTPError ({e.response.status_code if hasattr(e, 'response') and e.response else 'Unknown'}) during token refresh after {duration:.2f} seconds: {error_text}", exc_info=True)
        if "invalid_grant" in error_text: # Check error_text, not e.response.text directly if e.response might be None
            logger.warning("Token refresh failed with 'invalid_grant'. Refresh token may be expired or revoked (ensure it has Docs API scope if new).")
        raise
    except requests.exceptions.Timeout:
        duration = time.time() - start_time; logger.error(f"Timeout ({request_timeout}s) during token refresh after {duration:.2f} seconds."); raise
    except Exception as e:
        duration = time.time() - start_time; logger.error(f"Generic exception during token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True); raise

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

# --- Google Docs API Wrapper Functions (No changes to internal logic) ---
def api_get_document_content(service, document_id):
    logger.info(f"API: Getting content for document '{document_id}'.")
    start_time = time.time()
    try:
        document = service.documents().get(documentId=document_id, fields='body,title,documentId,documentStyle,namedStyles,revisionId,suggestionsViewMode').execute()
        duration = time.time() - start_time
        logger.info(f"API: Document content retrieval successful in {duration:.2f}s. Title: {document.get('title')}")
        return document
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError getting document content after {duration:.2f}s: {error_content}", exc_info=True); raise
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
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError during batch update after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error during batch update after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_insert_text(service, document_id, text_to_insert, location_index=None, segment_id=None):
    logger.info(f"API: Inserting text into document '{document_id}'. Text: '{text_to_insert[:50]}...'")
    if location_index is not None:
        location = {"index": location_index}
        if segment_id: location["segmentId"] = segment_id
        insert_request = {"insertText": {"location": location, "text": text_to_insert}}
    else:
        end_of_segment = {}
        if segment_id: end_of_segment["segmentId"] = segment_id
        insert_request = {"insertText": {"endOfSegmentLocation": end_of_segment, "text": text_to_insert}}
    return api_batch_update_document(service, document_id, [insert_request])

def api_delete_content_range(service, document_id, start_index, end_index, segment_id=None):
    logger.info(f"API: Deleting content range in document '{document_id}' from {start_index} to {end_index}.")
    content_range = {"startIndex": start_index, "endIndex": end_index}
    if segment_id: content_range["segmentId"] = segment_id
    delete_request = {"deleteContentRange": {"range": content_range}}
    return api_batch_update_document(service, document_id, [delete_request])

def api_update_paragraph_style(service, document_id, start_index, end_index, named_style_type, segment_id=None):
    logger.info(f"API: Updating paragraph style to '{named_style_type}' in document '{document_id}' from {start_index} to {end_index}.")
    style_range = {"startIndex": start_index, "endIndex": end_index}
    if segment_id: style_range["segmentId"] = segment_id
    update_request = {"updateParagraphStyle": {"range": style_range, "paragraphStyle": {"namedStyleType": named_style_type}, "fields": "namedStyleType"}}
    return api_batch_update_document(service, document_id, [update_request])

def api_update_text_style(service, document_id, start_index, end_index, bold=None, italic=None, underline=None, segment_id=None):
    logger.info(f"API: Updating text style in document '{document_id}' from {start_index} to {end_index}. B:{bold}, I:{italic}, U:{underline}")
    style_range = {"startIndex": start_index, "endIndex": end_index}
    if segment_id: style_range["segmentId"] = segment_id
    text_style = {}; fields_to_update = []
    if bold is not None: text_style["bold"] = bold; fields_to_update.append("bold")
    if italic is not None: text_style["italic"] = italic; fields_to_update.append("italic")
    if underline is not None: text_style["underline"] = underline; fields_to_update.append("underline")
    if not fields_to_update: logger.warning("API: No text style changes specified."); return {"warning": "No text style changes specified."}
    update_request = {"updateTextStyle": {"range": style_range, "textStyle": text_style, "fields": ",".join(fields_to_update)}}
    return api_batch_update_document(service, document_id, [update_request])

def api_insert_table(service, document_id, rows, columns, location_index=None, segment_id=None):
    logger.info(f"API: Inserting {rows}x{columns} table into document '{document_id}'.")
    if location_index is not None:
        location = {"index": location_index}
        if segment_id: location["segmentId"] = segment_id
        insert_request = {"insertTable": {"location": location, "rows": rows, "columns": columns}}
    else:
        end_of_segment = {}
        if segment_id: end_of_segment["segmentId"] = segment_id
        insert_request = {"insertTable": {"endOfSegmentLocation": end_of_segment, "rows": rows, "columns": columns}}
    return api_batch_update_document(service, document_id, [insert_request])

def api_create_document(service, title):
    logger.info(f"API: Creating new document with title '{title}'.")
    start_time = time.time()
    try:
        body = {'title': title}
        doc = service.documents().create(body=body).execute()
        duration = time.time() - start_time; logger.info(f"API: Document creation successful in {duration:.2f}s. Document ID: {doc.get('documentId')}")
        return doc
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating document after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error creating document after {duration:.2f}s: {str(e)}", exc_info=True); raise

# --- Flask Endpoints for Docs (on docs_bp Blueprint) ---

# REMOVED: Blueprint-specific /auth/callback endpoint
# REMOVED: Local get_specific_user_access_token function

@docs_bp.route('/token', methods=['GET'])
def specific_user_token_docs_endpoint():
    endpoint_name = "/docs/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        access_token = get_global_specific_user_access_token() # Imported from shared_utils
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user.")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        error_message = f"Failed to obtain access token: {str(e)}"
        if isinstance(e, ValueError): # More specific error for config issues
            return jsonify({"success": False, "error": error_message}), 400
        return jsonify({"success": False, "error": error_message}), 500

# All data manipulation endpoints now use current_app.config for CLIENT_ID and CLIENT_SECRET
@docs_bp.route('/create', methods=['POST'])
def create_document_endpoint():
    endpoint_name = "/docs/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('title', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'title' or 'refresh_token'"}), 400
        title = data['title']; refresh_token = data['refresh_token']
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        doc_info = api_create_document(service, title)
        return jsonify({"success": True, "message": "Document created successfully.", "document": doc_info})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/read', methods=['POST'])
def read_document_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/read"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not data or 'refresh_token' not in data:
            return jsonify({"success": False, "error": "Missing 'refresh_token' in JSON body"}), 400
        refresh_token = data['refresh_token']
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        document_content = api_get_document_content(service, document_id)
        return jsonify({"success": True, "document": document_content})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/insert_text', methods=['POST'])
def insert_text_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/insert_text"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('text', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'text' or 'refresh_token'"}), 400
        text_to_insert = data['text']; refresh_token = data['refresh_token']
        location_index = data.get('location_index'); segment_id = data.get('segment_id') 
        if location_index is not None: location_index = int(location_index)

        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        result = api_insert_text(service, document_id, text_to_insert, location_index, segment_id)
        return jsonify({"success": True, "message": "Text inserted successfully.", "details": result})
    except ValueError: return jsonify({"success": False, "error": "location_index must be an integer if provided."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/delete_range', methods=['POST'])
def delete_range_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/delete_range"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('start_index', 'end_index', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'start_index', 'end_index', or 'refresh_token'"}), 400
        start_index = int(data['start_index']); end_index = int(data['end_index']); refresh_token = data['refresh_token']
        segment_id = data.get('segment_id') 
        if start_index < 0 or end_index <= start_index: 
            return jsonify({"success": False, "error": "Invalid 'start_index' or 'end_index'. Ensure 0 <= start_index < end_index."}), 400
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        result = api_delete_content_range(service, document_id, start_index, end_index, segment_id)
        return jsonify({"success": True, "message": "Content deleted successfully.", "details": result})
    except ValueError: return jsonify({"success": False, "error": "start_index and end_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/format/paragraph', methods=['POST'])
def format_paragraph_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/format/paragraph"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        required_fields = ['start_index', 'end_index', 'style_type', 'refresh_token']
        if not all(k in data for k in required_fields):
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(required_fields)}"}), 400
        start_index = int(data['start_index']); end_index = int(data['end_index']); style_type = data['style_type'] 
        refresh_token = data['refresh_token']; segment_id = data.get('segment_id')
        valid_named_styles = ["NORMAL_TEXT", "TITLE", "SUBTITLE", "HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6"]
        if style_type not in valid_named_styles:
            return jsonify({"success": False, "error": f"Invalid 'style_type'. Must be one of {valid_named_styles}"}), 400
        if start_index < 0 or end_index <= start_index: 
            return jsonify({"success": False, "error": "Invalid 'start_index' or 'end_index'. Ensure 0 <= start_index < end_index."}), 400
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        result = api_update_paragraph_style(service, document_id, start_index, end_index, style_type, segment_id)
        return jsonify({"success": True, "message": "Paragraph style updated.", "details": result})
    except ValueError: return jsonify({"success": False, "error": "start_index and end_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/format/text', methods=['POST'])
def format_text_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/format/text"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        required_fields = ['start_index', 'end_index', 'refresh_token']
        if not all(k in data for k in required_fields):
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(required_fields)}"}), 400
        if not (data.get('bold') is not None or data.get('italic') is not None or data.get('underline') is not None):
            return jsonify({"success": False, "error": "At least one formatting option (bold, italic, underline) must be provided."}), 400
        start_index = int(data['start_index']); end_index = int(data['end_index']); refresh_token = data['refresh_token']
        bold = data.get('bold'); italic = data.get('italic'); underline = data.get('underline'); segment_id = data.get('segment_id')
        if start_index < 0 or end_index <= start_index: 
            return jsonify({"success": False, "error": "Invalid 'start_index' or 'end_index'. Ensure 0 <= start_index < end_index."}), 400
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        result = api_update_text_style(service, document_id, start_index, end_index, bold, italic, underline, segment_id)
        return jsonify({"success": True, "message": "Text style updated.", "details": result})
    except ValueError: return jsonify({"success": False, "error": "start_index and end_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@docs_bp.route('/<document_id>/insert_table', methods=['POST'])
def insert_table_endpoint(document_id):
    endpoint_name = f"/docs/{document_id}/insert_table"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        required_fields = ['rows', 'columns', 'refresh_token']
        if not all(k in data for k in required_fields):
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(required_fields)}"}), 400
        rows = int(data['rows']); columns = int(data['columns']); refresh_token = data['refresh_token']
        location_index = data.get('location_index'); segment_id = data.get('segment_id') 
        if location_index is not None: location_index = int(location_index)
        if rows < 1 or columns < 1:
            return jsonify({"success": False, "error": "Rows and columns must be positive integers."}), 400
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_docs_service(access_token)
        result = api_insert_table(service, document_id, rows, columns, location_index, segment_id)
        return jsonify({"success": True, "message": "Table inserted successfully.", "details": result})
    except ValueError: return jsonify({"success": False, "error": "rows, columns, and location_index (if provided) must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

# --- REMOVED if __name__ == "__main__": ---
