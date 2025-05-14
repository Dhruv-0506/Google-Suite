from flask import jsonify, request, Blueprint, current_app
import logging
import time
# No longer need 'os' or 'requests' directly at the module level if helpers are in shared_utils

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Import shared helper functions
from shared_utils import get_access_token, get_global_specific_user_access_token

logger = logging.getLogger(__name__)
sheets_bp = Blueprint('sheets_agent', __name__, url_prefix='/sheets')

# --- REMOVED local get_access_token function; now imported from shared_utils ---

def get_sheets_service(access_token):
    logger.info("Building Google Sheets API service object...")
    if not access_token:
        logger.error("Cannot build sheets service: access_token is missing.")
        raise ValueError("Access token is required to build sheets service.")
    try:
        creds = OAuthCredentials(token=access_token)
        service = build("sheets", "v4", credentials=creds, static_discovery=False) # Added static_discovery=False
        logger.info("Google Sheets API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Sheets API service object: {str(e)}", exc_info=True)
        raise

# --- Google Sheets API Wrapper Functions (No changes to internal logic needed here) ---
def api_update_cell(service, spreadsheet_id, cell_range, new_value, value_input_option="USER_ENTERED"):
    logger.info(f"API: Updating cell '{cell_range}' in sheet '{spreadsheet_id}' to '{new_value}' with option '{value_input_option}'.")
    start_time = time.time()
    try:
        body = {"values": [[new_value]]}
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=cell_range, valueInputOption=value_input_option, body=body
        ).execute()
        duration = time.time() - start_time; logger.info(f"API: Cell update successful in {duration:.2f}s. Result: {result}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError updating cell after {duration:.2f}s: {error_content}", exc_info=True); raise
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
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError appending rows after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error appending rows after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_delete_rows(service, spreadsheet_id, sheet_id, start_row_index, end_row_index):
    logger.info(f"API: Deleting rows from sheet '{spreadsheet_id}', sheetId {sheet_id}, from index {start_row_index} to {end_row_index-1}.")
    start_time = time.time()
    try:
        requests_body = [{"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start_row_index, "endIndex": end_row_index}}}]
        body = {"requests": requests_body}
        result = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        duration = time.time() - start_time; logger.info(f"API: Row deletion successful in {duration:.2f}s. Result: {result}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError deleting rows after {duration:.2f}s: {error_content}", exc_info=True); raise
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
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating new tab after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error creating new tab after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_clear_values(service, spreadsheet_id, range_name):
    logger.info(f"API: Clearing values from sheet '{spreadsheet_id}', range '{range_name}'.")
    start_time = time.time()
    try:
        result = service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=range_name, body={}).execute()
        duration = time.time() - start_time; logger.info(f"API: Values clear successful in {duration:.2f}s. Cleared range: {result.get('clearedRange')}"); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError clearing values after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error clearing values after {duration:.2f}s: {str(e)}", exc_info=True); raise

def api_get_spreadsheet_metadata(service, spreadsheet_id):
    logger.info(f"API: Getting metadata for spreadsheet '{spreadsheet_id}'.")
    start_time = time.time()
    try:
        result = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="properties,sheets.properties").execute()
        duration = time.time() - start_time; logger.info(f"API: Metadata retrieval successful in {duration:.2f}s."); return result
    except HttpError as e: duration = time.time() - start_time; error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError getting metadata after {duration:.2f}s: {error_content}", exc_info=True); raise
    except Exception as e: duration = time.time() - start_time; logger.error(f"API: Generic error getting metadata after {duration:.2f}s: {str(e)}", exc_info=True); raise

# --- Flask Endpoints (on sheets_bp Blueprint) ---

# REMOVED: Blueprint-specific /auth/callback endpoint

@sheets_bp.route('/token', methods=['GET'])
def specific_user_token_sheets_endpoint():
    endpoint_name = "/sheets/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        # Calls the centralized function imported from shared_utils.py
        # This function uses app.config values set in Google_Suite.py
        access_token = get_global_specific_user_access_token()
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user.")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        error_message = f"Failed to obtain access token: {str(e)}"
        if isinstance(e, ValueError): # More specific error for config issues
            return jsonify({"success": False, "error": error_message}), 400
        return jsonify({"success": False, "error": error_message}), 500

@sheets_bp.route('/<spreadsheet_id>/cell/update', methods=['POST'])
def update_cell_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/cell/update"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('cell_range', 'new_value', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'cell_range', 'new_value', or 'refresh_token'"}), 400
        cell_range = data['cell_range']; new_value = data['new_value']; refresh_token = data['refresh_token']
        value_input_option = data.get('value_input_option', "USER_ENTERED")
        
        # Uses imported get_access_token and global config
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_sheets_service(access_token)
        result = api_update_cell(service, spreadsheet_id, cell_range, new_value, value_input_option)
        return jsonify({"success": True, "message": "Cell updated successfully.", "details": result})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@sheets_bp.route('/<spreadsheet_id>/rows/append', methods=['POST'])
def append_rows_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/rows/append"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('range_name', 'values_data', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'range_name', 'values_data', or 'refresh_token'"}), 400
        range_name = data['range_name']; values_data = data['values_data']; refresh_token = data['refresh_token']
        value_input_option = data.get('value_input_option', "USER_ENTERED")
        if not isinstance(values_data, list) or not all(isinstance(row, list) for row in values_data):
            return jsonify({"success": False, "error": "'values_data' must be a list of lists (rows of cells)."}), 400
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_sheets_service(access_token)
        result = api_append_rows(service, spreadsheet_id, range_name, values_data, value_input_option)
        return jsonify({"success": True, "message": "Rows appended successfully.", "details": result.get("updates")})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@sheets_bp.route('/<spreadsheet_id>/rows/delete', methods=['POST'])
def delete_rows_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/rows/delete"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('sheet_id', 'start_row_index', 'end_row_index', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'sheet_id', 'start_row_index', 'end_row_index', or 'refresh_token'"}), 400
        sheet_id = int(data['sheet_id']); start_row_index = int(data['start_row_index']); end_row_index = int(data['end_row_index']); refresh_token = data['refresh_token']
        if start_row_index < 0 or end_row_index <= start_row_index:
            return jsonify({"success": False, "error": "Invalid 'start_row_index' or 'end_row_index'. Ensure start < end and both >= 0."}), 400
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_sheets_service(access_token)
        result = api_delete_rows(service, spreadsheet_id, sheet_id, start_row_index, end_row_index)
        return jsonify({"success": True, "message": "Row deletion request processed.", "details": result})
    except ValueError: return jsonify({"success": False, "error": "sheet_id, start_row_index, and end_row_index must be integers."}), 400
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@sheets_bp.route('/<spreadsheet_id>/tabs/create', methods=['POST'])
def create_tab_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/tabs/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('new_sheet_title', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'new_sheet_title' or 'refresh_token'"}), 400
        new_sheet_title = data['new_sheet_title']; refresh_token = data['refresh_token']
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_sheets_service(access_token)
        result = api_create_new_tab(service, spreadsheet_id, new_sheet_title)
        new_sheet_props = result.get('replies', [{}])[0].get('addSheet', {}).get('properties', {})
        return jsonify({"success": True, "message": "New tab/sheet created successfully.", "new_sheet_properties": new_sheet_props})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@sheets_bp.route('/<spreadsheet_id>/values/clear', methods=['POST'])
def clear_values_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/values/clear"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('range_name', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'range_name' or 'refresh_token'"}), 400
        range_name = data['range_name']; refresh_token = data['refresh_token']
        
        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_sheets_service(access_token)
        result = api_clear_values(service, spreadsheet_id, range_name)
        return jsonify({"success": True, "message": "Values cleared successfully.", "details": result})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@sheets_bp.route('/<spreadsheet_id>/metadata', methods=['POST'])
def get_metadata_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/metadata"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
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
        service = get_sheets_service(access_token)
        metadata = api_get_spreadsheet_metadata(service, spreadsheet_id)
        return jsonify({"success": True, "metadata": metadata})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

def get_sheet_id_by_name(service, spreadsheet_id, sheet_name):
    logger.info(f"Attempting to find sheetId for sheet name '{sheet_name}' in spreadsheet '{spreadsheet_id}'.")
    try:
        metadata = api_get_spreadsheet_metadata(service, spreadsheet_id)
        for sheet_prop in metadata.get('sheets', []):
            properties = sheet_prop.get('properties', {})
            if properties.get('title') == sheet_name:
                sheet_id = properties.get('sheetId')
                if sheet_id is not None:
                    logger.info(f"Found sheetId {sheet_id} for sheet name '{sheet_name}'.")
                    return sheet_id
        logger.warning(f"Sheet name '{sheet_name}' not found in spreadsheet '{spreadsheet_id}'.")
        return None
    except Exception as e:
        logger.error(f"Error getting sheetId for sheet name '{sheet_name}': {str(e)}", exc_info=True)
        raise

@sheets_bp.route('/<spreadsheet_id>/deduplicate', methods=['POST'])
def deduplicate_sheet_rows_endpoint(spreadsheet_id):
    endpoint_name = f"/sheets/{spreadsheet_id}/deduplicate"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        required_fields = ['refresh_token', 'key_columns']
        if not (data.get('sheet_name') or data.get('sheet_id') is not None):
            return jsonify({"success": False, "error": "Missing 'sheet_name' or 'sheet_id'"}), 400
        if not all(k in data for k in required_fields):
            return jsonify({"success": False, "error": f"Missing one or more required fields: {', '.join(required_fields)}"}), 400

        refresh_token = data['refresh_token']
        key_column_indices = data['key_columns']
        sheet_name_param = data.get('sheet_name')
        sheet_id_param = data.get('sheet_id')
        header_rows_count = int(data.get('header_rows', 1))
        keep_option = data.get('keep', 'first').lower()

        if not isinstance(key_column_indices, list) or not all(isinstance(i, int) and i >= 0 for i in key_column_indices):
            return jsonify({"success": False, "error": "'key_columns' must be a list of non-negative integers (0-based column indices)."}), 400
        if not key_column_indices: return jsonify({"success": False, "error": "'key_columns' cannot be empty."}), 400
        if keep_option not in ['first', 'last']: return jsonify({"success": False, "error": "Invalid 'keep' option. Must be 'first' or 'last'."}), 400

        access_token = get_access_token(
            refresh_token,
            current_app.config['CLIENT_ID'],
            current_app.config['CLIENT_SECRET']
        )
        service = get_sheets_service(access_token)
        
        numeric_sheet_id = None
        sheet_identifier_for_get_api = None 
        if sheet_id_param is not None:
            try:
                numeric_sheet_id = int(sheet_id_param)
                metadata = api_get_spreadsheet_metadata(service, spreadsheet_id)
                found_sheet = next((s['properties'] for s in metadata.get('sheets', []) if s['properties']['sheetId'] == numeric_sheet_id), None)
                if not found_sheet: return jsonify({"success": False, "error": f"Sheet with ID {numeric_sheet_id} not found."}), 404
                sheet_identifier_for_get_api = found_sheet['title']
            except ValueError: return jsonify({"success": False, "error": f"Invalid 'sheet_id': {sheet_id_param}. Must be an integer."}), 400
        elif sheet_name_param:
            numeric_sheet_id = get_sheet_id_by_name(service, spreadsheet_id, sheet_name_param)
            if numeric_sheet_id is None: return jsonify({"success": False, "error": f"Sheet name '{sheet_name_param}' not found."}), 404
            sheet_identifier_for_get_api = sheet_name_param
        else: return jsonify({"success": False, "error": "Sheet identifier (name or id) is missing."}), 400

        range_to_get = f"'{sheet_identifier_for_get_api}'!A:ZZ" 
        try:
            result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_to_get).execute()
        except HttpError as he_get:
            if hasattr(he_get, 'resp') and he_get.resp.status == 400 and "Unable to parse range" in str(he_get):
                 result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=sheet_identifier_for_get_api).execute()
            else: raise
        all_rows_from_sheet = result.get('values', [])
        if not all_rows_from_sheet: return jsonify({"success": True, "message": "Sheet is empty, no duplicates to remove.", "rows_deleted_count": 0})

        data_rows_with_original_indices = [{'data': rc, 'original_index_in_sheet': i} for i, rc in enumerate(all_rows_from_sheet) if i >= header_rows_count]
        if not data_rows_with_original_indices: return jsonify({"success": True, "message": "No data rows to process after headers.", "rows_deleted_count": 0})

        seen_keys = {}; indices_to_delete_0_based = []; max_key_col_index = max(key_column_indices)
        for row_info in data_rows_with_original_indices:
            row_data, idx = row_info['data'], row_info['original_index_in_sheet']
            key_parts = [(row_data[k_idx] if k_idx < len(row_data) else None) for k_idx in key_column_indices]
            key = tuple(key_parts)
            if key in seen_keys:
                if keep_option == 'first': indices_to_delete_0_based.append(idx)
                else: indices_to_delete_0_based.append(seen_keys[key]); seen_keys[key] = idx
            else: seen_keys[key] = idx
        
        if not indices_to_delete_0_based: return jsonify({"success": True, "message": "No duplicate rows found.", "rows_deleted_count": 0})
        indices_to_delete_0_based = sorted(list(set(indices_to_delete_0_based)), reverse=True)
        delete_reqs = [{"deleteDimension": {"range": {"sheetId": numeric_sheet_id, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1}}} for i in indices_to_delete_0_based]
        if delete_reqs: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": delete_reqs}).execute()
        
        return jsonify({"success": True, "message": f"Deduplication complete. {len(indices_to_delete_0_based)} row(s) removed.", "rows_deleted_count": len(indices_to_delete_0_based), "deleted_row_indices_0_based": indices_to_delete_0_based})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": f"Invalid input value: {str(ve)}"}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500
