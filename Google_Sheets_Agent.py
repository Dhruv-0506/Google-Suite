from flask import Flask, jsonify, request
import os
import requests
import logging
import time

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration (Main App) ---
CLIENT_ID = "26763482887-coiufpukc1l69aaulaiov5o0u3en2del.apps.googleusercontent.com"
CLIENT_SECRET = "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"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "https://serverless.on-demand.io/apps/googlesheets/auth/callback"
REQUEST_TIMEOUT_SECONDS = 30

# --- OAuth and Token Helper Functions (Main App) ---
def exchange_code_for_tokens(authorization_code):
    logger.info(f"Attempting to exchange authorization code for tokens. Code starts with: {authorization_code[:10]}...")
    start_time = time.time()
    if not CLIENT_SECRET:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable not set for token exchange.")
        raise ValueError("GOOGLE_CLIENT_SECRET environment variable not set.")
    payload = {
        "code": authorization_code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"
    }
    logger.debug(f"Token exchange payload (secrets redacted): { {k: (v if k not in ['client_secret', 'code'] else '...') for k,v in payload.items()} }")
    try:
        response = requests.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        token_data = response.json()
        duration = time.time() - start_time
        if token_data.get("access_token"): #and token_data.get("refresh_token"):
            logger.info(f"Successfully exchanged code for tokens in {duration:.2f} seconds.")
            return token_data
        else:
            logger.error(f"Token exchange response missing access_token or refresh_token after {duration:.2f}s. Response: {token_data}")
            raise ValueError("Access token or refresh token not found in response.")
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

def get_access_token(refresh_token):
    logger.info(f"Attempting to get new access token using refresh token (starts with: {refresh_token[:10]}...).")
    start_time = time.time()
    if not CLIENT_SECRET:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable not set for token refresh.")
        raise ValueError("GOOGLE_CLIENT_SECRET environment variable not set.")
    payload = {
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
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
            logger.warning("Token refresh failed with 'invalid_grant'. Refresh token may be expired or revoked.")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

def get_sheets_service(access_token):
    logger.info("Building Google Sheets API service object...")
    if not access_token:
        logger.error("Cannot build sheets service: access_token is missing.")
        raise ValueError("Access token is required to build sheets service.")
    try:
        creds = OAuthCredentials(token=access_token)
        service = build("sheets", "v4", credentials=creds)
        logger.info("Google Sheets API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Sheets API service object: {str(e)}", exc_info=True)
        raise

# --- Google Sheets API Wrapper Functions (Main App) ---
def api_update_cell(service, spreadsheet_id, cell_range, new_value, value_input_option="USER_ENTERED"):
    logger.info(f"API: Updating cell '{cell_range}' in sheet '{spreadsheet_id}' to '{new_value}' with option '{value_input_option}'.")
    start_time = time.time()
    try:
        body = {"values": [[new_value]]}
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=cell_range, valueInputOption=value_input_option, body=body
        ).execute()
        duration = time.time() - start_time; logger.info(f"API: Cell update successful in {duration:.2f}s. Result: {result}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError updating cell after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error updating cell after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_append_rows(service, spreadsheet_id, range_name, values_data, value_input_option="USER_ENTERED"):
    logger.info(f"API: Appending rows to sheet '{spreadsheet_id}', range '{range_name}', option '{value_input_option}'. Rows: {len(values_data)}")
    logger.debug(f"API: Values to append: {values_data}")
    start_time = time.time()
    try:
        body = {"values": values_data}
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=range_name, valueInputOption=value_input_option, insertDataOption="INSERT_ROWS", body=body
        ).execute()
        duration = time.time() - start_time; logger.info(f"API: Row append successful in {duration:.2f}s. Updates: {result.get('updates')}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError appending rows after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error appending rows after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_delete_rows(service, spreadsheet_id, sheet_id, start_row_index, end_row_index):
    logger.info(f"API: Deleting rows from sheet '{spreadsheet_id}', sheetId {sheet_id}, from index {start_row_index} to {end_row_index-1}.")
    start_time = time.time()
    try:
        requests_body = [{"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start_row_index, "endIndex": end_row_index}}}]
        body = {"requests": requests_body}
        result = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        duration = time.time() - start_time; logger.info(f"API: Row deletion successful in {duration:.2f}s. Result: {result}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError deleting rows after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error deleting rows after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_create_new_tab(service, spreadsheet_id, new_sheet_title):
    logger.info(f"API: Creating new tab/sheet named '{new_sheet_title}' in spreadsheet '{spreadsheet_id}'.")
    start_time = time.time()
    try:
        requests_body = [{"addSheet": {"properties": {"title": new_sheet_title}}}]
        body = {"requests": requests_body}
        result = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        duration = time.time() - start_time
        new_sheet_props = result.get('replies', [{}])[0].get('addSheet', {}).get('properties', {})
        logger.info(f"API: New tab creation successful in {duration:.2f}s. New sheet ID: {new_sheet_props.get('sheetId')}, Title: {new_sheet_props.get('title')}")
        return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError creating new tab after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error creating new tab after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_clear_values(service, spreadsheet_id, range_name):
    logger.info(f"API: Clearing values from sheet '{spreadsheet_id}', range '{range_name}'.")
    start_time = time.time()
    try:
        result = service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=range_name, body={}).execute()
        duration = time.time() - start_time; logger.info(f"API: Values clear successful in {duration:.2f}s. Cleared range: {result.get('clearedRange')}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError clearing values after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error clearing values after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_get_spreadsheet_metadata(service, spreadsheet_id):
    logger.info(f"API: Getting metadata for spreadsheet '{spreadsheet_id}'.")
    start_time = time.time()
    try:
        result = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="properties,sheets.properties").execute()
        duration = time.time() - start_time; logger.info(f"API: Metadata retrieval successful in {duration:.2f}s."); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"API: HttpError getting metadata after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error getting metadata after {duration:.2f}s: {str(e)}", exc_info=True); raise

# --- NEW: Helper function for the /token endpoint (uses specific hardcoded values) ---
def get_specific_user_access_token():
    logger.info("Attempting to get access token for a specific pre-configured user.")
    specific_client_id = "26763482887-q9lcln5nmb0setr60gkohdjrt2msl6o5.apps.googleusercontent.com"
    # --- THIS IS THE UPDATED REFRESH TOKEN ---
    specific_refresh_token = "1//09qu30gV5_1hZCgYIARAAGAkSNwF-L9IrEOR20gZnhzmvcFcU46oN89TXt-Sf7ET2SAUwx7d9wo0E2E2ISkXw4CxCDDNxouGAVo4"
    # --- END OF UPDATE ---

    if not CLIENT_SECRET:
        logger.error("CRITICAL: GOOGLE_CLIENT_SECRET environment variable not set for token refresh (specific user).")
        raise ValueError("GOOGLE_CLIENT_SECRET environment variable not set.")
    payload = {
        "client_id": specific_client_id, "client_secret": CLIENT_SECRET,
        "refresh_token": specific_refresh_token, "grant_type": "refresh_token"
    }
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
    except requests.exceptions.Timeout:
        duration = time.time() - start_time
        logger.error(f"Timeout ({REQUEST_TIMEOUT_SECONDS}s) during specific user token refresh after {duration:.2f} seconds.")
        raise
    except requests.exceptions.HTTPError as e:
        duration = time.time() - start_time
        logger.error(f"HTTPError ({e.response.status_code}) during specific user token refresh after {duration:.2f} seconds: {e.response.text if e.response else str(e)}")
        if "invalid_grant" in (e.response.text if e.response else ""):
            logger.warning("Specific user token refresh failed with 'invalid_grant'. Refresh token may be expired or revoked.")
        raise
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Generic exception during specific user token refresh after {duration:.2f} seconds: {str(e)}", exc_info=True)
        raise

# --- Flask Endpoints (Main App) ---
@app.route('/auth/callback', methods=['GET'])
def oauth2callback_endpoint():
    endpoint_name = "/auth/callback"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    authorization_code = request.args.get('code')
    if authorization_code:
        try:
            token_data = exchange_code_for_tokens(authorization_code)
            logger.info(f"ENDPOINT {endpoint_name}: Authorization successful, tokens obtained.")
            return jsonify({"message": "Authorization successful. IMPORTANT: Securely store the refresh_token.", "tokens": token_data})
        except Exception as e:
            logger.error(f"ENDPOINT {endpoint_name}: Error during token exchange: {str(e)}", exc_info=True)
            return jsonify({"error": f"Failed to exchange authorization code for tokens: {str(e)}"}), 500
    else:
        logger.warning(f"ENDPOINT {endpoint_name}: Authorization code missing in request.")
        return jsonify({"error": "Authorization code missing"}), 400

@app.route('/sheets/<spreadsheet_id>/cell/update', methods=['POST'])
def update_cell_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/cell/update"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('cell_range', 'new_value', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing required fields.")
            return jsonify({"success": False, "error": "Missing 'cell_range', 'new_value', or 'refresh_token'"}), 400
        cell_range = data['cell_range']; new_value = data['new_value']; refresh_token = data['refresh_token']
        value_input_option = data.get('value_input_option', "USER_ENTERED")
        access_token = get_access_token(refresh_token); service = get_sheets_service(access_token)
        result = api_update_cell(service, spreadsheet_id, cell_range, new_value, value_input_option)
        logger.info(f"ENDPOINT {endpoint_name}: Cell update successful.")
        return jsonify({"success": True, "message": "Cell updated successfully.", "details": result})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/sheets/<spreadsheet_id>/rows/append', methods=['POST'])
def append_rows_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/rows/append"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('range_name', 'values_data', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing required fields.")
            return jsonify({"success": False, "error": "Missing 'range_name', 'values_data', or 'refresh_token'"}), 400
        range_name = data['range_name']; values_data = data['values_data']; refresh_token = data['refresh_token']
        value_input_option = data.get('value_input_option', "USER_ENTERED")
        if not isinstance(values_data, list) or not all(isinstance(row, list) for row in values_data):
            logger.warning(f"ENDPOINT {endpoint_name}: 'values_data' is not a list of lists.")
            return jsonify({"success": False, "error": "'values_data' must be a list of lists (rows of cells)."}), 400
        access_token = get_access_token(refresh_token); service = get_sheets_service(access_token)
        result = api_append_rows(service, spreadsheet_id, range_name, values_data, value_input_option)
        logger.info(f"ENDPOINT {endpoint_name}: Row append successful.")
        return jsonify({"success": True, "message": "Rows appended successfully.", "details": result.get("updates")})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/sheets/<spreadsheet_id>/rows/delete', methods=['POST'])
def delete_rows_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/rows/delete"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('sheet_id', 'start_row_index', 'end_row_index', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing required fields.")
            return jsonify({"success": False, "error": "Missing 'sheet_id', 'start_row_index', 'end_row_index', or 'refresh_token'"}), 400
        sheet_id = int(data['sheet_id']); start_row_index = int(data['start_row_index']); end_row_index = int(data['end_row_index']); refresh_token = data['refresh_token']
       
        if start_row_index < 0 or end_row_index <= start_row_index:
            logger.warning(f"ENDPOINT {endpoint_name}: Invalid row indices.")
            return jsonify({"success": False, "error": "Invalid 'start_row_index' or 'end_row_index'. Ensure start < end and both >= 0."}), 400
        access_token = get_access_token(refresh_token); service = get_sheets_service(access_token)
        result = api_delete_rows(service, spreadsheet_id, sheet_id, start_row_index, end_row_index)
        logger.info(f"ENDPOINT {endpoint_name}: Row deletion request successful.")
        return jsonify({"success": True, "message": "Row deletion request processed.", "details": result})
    except ValueError: logger.warning(f"ENDPOINT {endpoint_name}: Invalid non-integer input for sheet_id or row indices.", exc_info=True); return jsonify({"success": False, "error": "sheet_id, start_row_index, and end_row_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/sheets/<spreadsheet_id>/tabs/create', methods=['POST'])
def create_tab_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/tabs/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('new_sheet_title', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing required fields.")
            return jsonify({"success": False, "error": "Missing 'new_sheet_title' or 'refresh_token'"}), 400
        new_sheet_title = data['new_sheet_title']; refresh_token = data['refresh_token']
        access_token = get_access_token(refresh_token); service = get_sheets_service(access_token)
        result = api_create_new_tab(service, spreadsheet_id, new_sheet_title)
        new_sheet_props = result.get('replies', [{}])[0].get('addSheet', {}).get('properties', {})
        logger.info(f"ENDPOINT {endpoint_name}: New tab creation successful.")
        return jsonify({"success": True, "message": "New tab/sheet created successfully.", "new_sheet_properties": new_sheet_props})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/sheets/<spreadsheet_id>/values/clear', methods=['POST'])
def clear_values_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/values/clear"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json; logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")
        if not all(k in data for k in ('range_name', 'refresh_token')):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing required fields.")
            return jsonify({"success": False, "error": "Missing 'range_name' or 'refresh_token'"}), 400
        range_name = data['range_name']; refresh_token = data['refresh_token']
        access_token = get_access_token(refresh_token); service = get_sheets_service(access_token)
        result = api_clear_values(service, spreadsheet_id, range_name)
        logger.info(f"ENDPOINT {endpoint_name}: Values clear successful.")
        return jsonify({"success": True, "message": "Values cleared successfully.", "details": result})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/sheets/<spreadsheet_id>/metadata', methods=['POST'])
def get_metadata_endpoint(spreadsheet_id):
    data = request.json
    refresh_token = data['refresh_token']
    endpoint_name = f"/sheets/{spreadsheet_id}/metadata"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        if not refresh_token:
            logger.warning(f"ENDPOINT {endpoint_name}: Missing 'refresh_token' query parameter.")
            return jsonify({"success": False, "error": "Missing 'refresh_token' query parameter"}), 400
        access_token = get_access_token(refresh_token); service = get_sheets_service(access_token)
        metadata = api_get_spreadsheet_metadata(service, spreadsheet_id)
        logger.info(f"ENDPOINT {endpoint_name}: Metadata retrieval successful.")
        return jsonify({"success": True, "metadata": metadata})
    except HttpError as e: error_content = e.content.decode('utf-8') if e.content else str(e); logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

# --- NEW: Endpoint to get an access token for a specific hardcoded refresh token ---
@app.route('/token', methods=['GET'])
def specific_user_token_endpoint():
    endpoint_name = "/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received to get specific user access token.")
    try:
        access_token = get_specific_user_access_token()
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user.")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": f"Failed to obtain access token: {str(e)}"}), 500
#___________________________________________________________________________________________________________________
# --- Helper function to get sheetId from sheetName ---
def get_sheet_id_by_name(service, spreadsheet_id, sheet_name):
    """Gets the numeric sheetId for a given sheet name."""
    logger.info(f"Attempting to find sheetId for sheet name '{sheet_name}' in spreadsheet '{spreadsheet_id}'.")
    try:
        metadata = api_get_spreadsheet_metadata(service, spreadsheet_id) # Assumes you have api_get_spreadsheet_metadata
        for sheet_prop in metadata.get('sheets', []):
            properties = sheet_prop.get('properties', {})
            if properties.get('title') == sheet_name:
                sheet_id = properties.get('sheetId')
                if sheet_id is not None:
                    logger.info(f"Found sheetId {sheet_id} for sheet name '{sheet_name}'.")
                    return sheet_id
        logger.warning(f"Sheet name '{sheet_name}' not found in spreadsheet '{spreadsheet_id}'. Metadata: {metadata}")
        return None
    except Exception as e:
        logger.error(f"Error getting sheetId for sheet name '{sheet_name}': {str(e)}", exc_info=True)
        raise


@app.route('/sheets/<spreadsheet_id>/deduplicate', methods=['POST'])
def deduplicate_sheet_rows_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/deduplicate"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")

    try:
        data = request.json
        logger.debug(f"ENDPOINT {endpoint_name}: Request body: {data}")

        required_fields = ['refresh_token', 'key_columns']
        if not (data.get('sheet_name') or data.get('sheet_id') is not None):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing 'sheet_name' or 'sheet_id'.")
            return jsonify({"success": False, "error": "Missing 'sheet_name' or 'sheet_id'"}), 400
        if not all(k in data for k in required_fields):
            logger.warning(f"ENDPOINT {endpoint_name}: Missing required fields among {required_fields}.")
            return jsonify({"success": False, "error": f"Missing one or more required fields: {', '.join(required_fields)}"}), 400

        refresh_token = data['refresh_token']
        key_column_indices = data['key_columns']
        sheet_name_param = data.get('sheet_name')
        sheet_id_param = data.get('sheet_id') # User can provide numeric sheetId directly
        header_rows_count = int(data.get('header_rows', 1)) # Default to 1 header row
        keep_option = data.get('keep', 'first').lower() # 'first' or 'last'

        if not isinstance(key_column_indices, list) or not all(isinstance(i, int) and i >= 0 for i in key_column_indices):
            logger.warning(f"ENDPOINT {endpoint_name}: 'key_columns' must be a list of non-negative integers.")
            return jsonify({"success": False, "error": "'key_columns' must be a list of non-negative integers (0-based column indices)."}), 400
        if not key_column_indices:
            logger.warning(f"ENDPOINT {endpoint_name}: 'key_columns' cannot be empty.")
            return jsonify({"success": False, "error": "'key_columns' cannot be empty."}), 400
        if keep_option not in ['first', 'last']:
            logger.warning(f"ENDPOINT {endpoint_name}: Invalid 'keep' option. Must be 'first' or 'last'.")
            return jsonify({"success": False, "error": "Invalid 'keep' option. Must be 'first' or 'last'."}), 400

        access_token = get_access_token(refresh_token)
        service = get_sheets_service(access_token)

        # --- Determine numeric sheet_id ---
        numeric_sheet_id = None
        sheet_identifier_for_get_api = None # This will be used for fetching values (e.g., "Sheet1")

        if sheet_id_param is not None:
            try:
                numeric_sheet_id = int(sheet_id_param)
                # To get values, we still need a name if possible, or we need to find it if we only have id
                # For simplicity, if sheet_id is given, we fetch metadata to find its name for values().get()
                # or construct a range like "!R1C1" which is less common for full sheet.
                # A safer bet is to get metadata and find the title for the given sheetId.
                metadata = api_get_spreadsheet_metadata(service, spreadsheet_id)
                found_sheet = next((s['properties'] for s in metadata.get('sheets', []) if s['properties']['sheetId'] == numeric_sheet_id), None)
                if not found_sheet:
                    logger.error(f"ENDPOINT {endpoint_name}: Provided sheet_id {numeric_sheet_id} not found in spreadsheet.")
                    return jsonify({"success": False, "error": f"Sheet with ID {numeric_sheet_id} not found."}), 404
                sheet_identifier_for_get_api = found_sheet['title']
                logger.info(f"Using provided sheet_id: {numeric_sheet_id} (Title: {sheet_identifier_for_get_api}).")
            except ValueError:
                logger.error(f"ENDPOINT {endpoint_name}: Invalid 'sheet_id' provided, not an integer: {sheet_id_param}")
                return jsonify({"success": False, "error": f"Invalid 'sheet_id': {sheet_id_param}. Must be an integer."}), 400
        elif sheet_name_param:
            numeric_sheet_id = get_sheet_id_by_name(service, spreadsheet_id, sheet_name_param)
            if numeric_sheet_id is None:
                logger.error(f"ENDPOINT {endpoint_name}: Sheet name '{sheet_name_param}' not found.")
                return jsonify({"success": False, "error": f"Sheet name '{sheet_name_param}' not found."}), 404
            sheet_identifier_for_get_api = sheet_name_param
            logger.info(f"Using sheet_name: '{sheet_name_param}', found sheet_id: {numeric_sheet_id}.")
        else: # Should be caught by initial check, but defensive
            return jsonify({"success": False, "error": "Sheet identifier (name or id) is missing."}), 400

        # --- Get all data from the sheet ---
        # Fetching a wide range. Adjust if your sheets are wider.
        # Ensure sheet_identifier_for_get_api is properly quoted if it contains spaces or special chars.
        # The API client library usually handles this for sheet names.
        range_to_get = f"'{sheet_identifier_for_get_api}'!A:ZZ" # Fetch all columns
        logger.info(f"ENDPOINT {endpoint_name}: Fetching data from range: {range_to_get}")
        try:
            result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_to_get).execute()
        except HttpError as he_get:
            if he_get.resp.status == 400 and "Unable to parse range" in str(he_get):
                 # Fallback for sheets with no data, or very new sheets if A:ZZ is too much
                 logger.warning(f"ENDPOINT {endpoint_name}: Could not parse range '{range_to_get}'. Trying to fetch just '{sheet_identifier_for_get_api}'.")
                 result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=sheet_identifier_for_get_api).execute()
            else:
                raise
        all_rows_from_sheet = result.get('values', [])
        if not all_rows_from_sheet:
            logger.info(f"ENDPOINT {endpoint_name}: Sheet '{sheet_identifier_for_get_api}' is empty or contains no data. No deduplication needed.")
            return jsonify({"success": True, "message": "Sheet is empty, no duplicates to remove.", "rows_deleted_count": 0})

        logger.info(f"ENDPOINT {endpoint_name}: Fetched {len(all_rows_from_sheet)} rows from sheet '{sheet_identifier_for_get_api}'.")

        # --- Identify duplicate rows ---
        data_rows_with_original_indices = []
        for i, row_content in enumerate(all_rows_from_sheet):
            if i >= header_rows_count:
                data_rows_with_original_indices.append({'data': row_content, 'original_index_in_sheet': i})

        if not data_rows_with_original_indices:
            logger.info(f"ENDPOINT {endpoint_name}: No data rows found after skipping {header_rows_count} header row(s).")
            return jsonify({"success": True, "message": "No data rows to process after headers.", "rows_deleted_count": 0})

        seen_keys = {}  # Stores composite_key -> original_index_in_sheet
        indices_to_delete_0_based = [] # Stores 0-based sheet indices of rows to delete

        max_key_col_index = max(key_column_indices)

        for row_info in data_rows_with_original_indices:
            row_data = row_info['data']
            current_original_index = row_info['original_index_in_sheet']
            
            # Construct composite key
            composite_key_parts = []
            valid_key = True
            if len(row_data) <= max_key_col_index: # Check if row is long enough for all key columns
                # Row is shorter than the max key column index, consider how to handle
                # Option 1: Treat as unique (skip for dedupe based on these keys)
                # Option 2: Pad with a special value to form key
                # Option 3: Log and skip (for now, let's log and treat as if its key can't be fully formed)
                logger.debug(f"Row {current_original_index} is too short (len {len(row_data)}) for key columns up to index {max_key_col_index}. Skipping this row for deduplication based on full key.")
                # This row won't be marked as a duplicate of another, nor will it mark others as duplicates
                # if the missing key part is essential.
                # A more robust approach might involve defining behavior for partial keys or rows not meeting key criteria.
                # For simplicity, we construct the key with available parts, it might lead to more "unique" keys if short.
                for k_idx in key_column_indices:
                    if k_idx < len(row_data):
                        composite_key_parts.append(row_data[k_idx])
                    else:
                        composite_key_parts.append(None) # Or some placeholder for missing cell
            else:
                for k_idx in key_column_indices:
                    composite_key_parts.append(row_data[k_idx])
            
            composite_key = tuple(composite_key_parts)

            if composite_key in seen_keys:
                if keep_option == 'first':
                    indices_to_delete_0_based.append(current_original_index)
                elif keep_option == 'last':
                    indices_to_delete_0_based.append(seen_keys[composite_key]) # Add previous one to delete list
                    seen_keys[composite_key] = current_original_index # Update to keep current
            else:
                seen_keys[composite_key] = current_original_index

        if not indices_to_delete_0_based:
            logger.info(f"ENDPOINT {endpoint_name}: No duplicate rows found based on specified criteria.")
            return jsonify({"success": True, "message": "No duplicate rows found.", "rows_deleted_count": 0})

        # Sort indices in descending order to avoid shifting issues during deletion
        indices_to_delete_0_based.sort(reverse=True)
        # Remove duplicates from the list of indices to delete (if 'last' option caused multiple adds of same old index)
        indices_to_delete_0_based = sorted(list(set(indices_to_delete_0_based)), reverse=True)


        logger.info(f"ENDPOINT {endpoint_name}: Identified {len(indices_to_delete_0_based)} rows for deletion. Indices: {indices_to_delete_0_based}")

        # --- Perform batch deletion ---
        delete_requests = []
        for row_idx_to_delete in indices_to_delete_0_based:
            delete_requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": numeric_sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_idx_to_delete,    # 0-indexed
                        "endIndex": row_idx_to_delete + 1  # endIndex is exclusive
                    }
                }
            })
        
        if delete_requests:
            body = {"requests": delete_requests}
            logger.info(f"ENDPOINT {endpoint_name}: Sending batchUpdate to delete {len(delete_requests)} rows.")
            start_time_delete = time.time()
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
            duration_delete = time.time() - start_time_delete
            logger.info(f"ENDPOINT {endpoint_name}: Batch deletion successful in {duration_delete:.2f}s.")
        
        return jsonify({
            "success": True, 
            "message": f"Deduplication complete. {len(indices_to_delete_0_based)} row(s) removed.",
            "rows_deleted_count": len(indices_to_delete_0_based),
            "deleted_row_indices_0_based": indices_to_delete_0_based
        })

    except HttpError as e:
        error_content = e.content.decode('utf-8') if e.content else str(e)
        logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True)
        return jsonify({"success": False, "error": "Google API Error", "details": error_content}), e.resp.status if hasattr(e, 'resp') else 500
    except ValueError as ve: # Catches int conversion errors for header_rows, sheet_id
        logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": f"Invalid input value: {str(ve)}"}), 400
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500
#____________________________________________________________________________________________________________________________________________________________________________________#

# Run Flask
if __name__ == "__main__":
    if not CLIENT_SECRET:
        logger.critical("CRITICAL STARTUP ERROR: GOOGLE_CLIENT_SECRET environment variable is not set.")
        print("CRITICAL ERROR: GOOGLE_CLIENT_SECRET environment variable is not set. The application will likely fail on token operations.")
        print("Please set this environment variable before running.")
    else:
        logger.info(f"GOOGLE_CLIENT_SECRET is loaded (length: {len(CLIENT_SECRET)}).")
    logger.info("Starting Flask application...")
    app.run(debug=True, host="0.0.0.0", port=5000)
