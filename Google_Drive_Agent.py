from flask import jsonify, request, Blueprint, current_app, send_file
import logging
import time
import io # For sending file data
import os # For os.path and os.makedirs, os.remove
import requests # Still needed by get_access_token if it's making HTTP calls

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# Import shared helper functions
from shared_utils import get_access_token, get_global_specific_user_access_token

logger = logging.getLogger(__name__)
drive_bp = Blueprint('drive_agent', __name__, url_prefix='/drive')

# --- REMOVED local get_access_token function; now imported from shared_utils ---

def get_drive_service(access_token):
    logger.info("Building Google Drive API service object...")
    if not access_token:
        logger.error("Cannot build drive service: access_token is missing.")
        raise ValueError("Access token is required to build drive service.")
    try:
        creds = OAuthCredentials(token=access_token)
        service = build("drive", "v3", credentials=creds, static_discovery=False)
        logger.info("Google Drive API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Drive API service object: {str(e)}", exc_info=True)
        raise

# --- Google Drive API Wrapper Functions (No changes to internal logic) ---
def api_create_folder(service, folder_name, parent_folder_id=None):
    logger.info(f"API: Creating folder '{folder_name}'" + (f" inside parent '{parent_folder_id}'" if parent_folder_id else ""))
    file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_folder_id: file_metadata['parents'] = [parent_folder_id]
    try:
        folder = service.files().create(body=file_metadata, fields='id, name, webViewLink').execute() # Added webViewLink
        logger.info(f"Folder created successfully: ID '{folder.get('id')}', Name '{folder.get('name')}', Link: {folder.get('webViewLink')}")
        return folder
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating folder: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error creating folder: {str(e)}", exc_info=True); raise

def api_list_folder_contents(service, folder_id="root", page_size=100):
    logger.info(f"API: Listing contents of folder_id '{folder_id}'")
    query = f"'{folder_id}' in parents and trashed = false"
    try:
        results = service.files().list(q=query, pageSize=page_size, fields="nextPageToken, files(id, name, mimeType, webViewLink, createdTime, modifiedTime, size, iconLink, capabilities, parents)").execute() # Added more fields
        items = results.get('files', [])
        logger.info(f"Found {len(items)} items in folder '{folder_id}'.")
        return items
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError listing folder contents: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error listing folder contents: {str(e)}", exc_info=True); raise

def api_upload_file(service, local_file_path, file_name, mime_type, folder_id=None):
    logger.info(f"API: Uploading file '{local_file_path}' as '{file_name}' (MIME: {mime_type})" + (f" to folder '{folder_id}'" if folder_id else ""))
    file_metadata = {'name': file_name}
    if folder_id: file_metadata['parents'] = [folder_id]
    try:
        media = MediaFileUpload(local_file_path, mimetype=mime_type, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id, name, webViewLink').execute()
        logger.info(f"File uploaded successfully: ID '{file.get('id')}', Name '{file.get('name')}', Link: {file.get('webViewLink')}")
        return file
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError uploading file: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error uploading file: {str(e)}", exc_info=True); raise

def api_download_file(service, file_id, local_download_path_dir): # Changed param name for clarity
    logger.info(f"API: Downloading file_id '{file_id}' to directory '{local_download_path_dir}'")
    try:
        file_metadata = service.files().get(fileId=file_id, fields='id, name, mimeType').execute()
        file_name = file_metadata.get('name', f"downloaded_file_{file_id}") # Default filename
        mime_type = file_metadata.get('mimeType')
        
        request_obj = None # Renamed to avoid conflict with 'requests' import
        export_mime_type = None

        if mime_type == 'application/vnd.google-apps.document':
            export_mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            if not file_name.lower().endswith('.docx'): file_name += '.docx'
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            export_mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            if not file_name.lower().endswith('.xlsx'): file_name += '.xlsx'
        elif mime_type == 'application/vnd.google-apps.presentation':
            export_mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            if not file_name.lower().endswith('.pptx'): file_name += '.pptx'
        
        if export_mime_type:
            logger.info(f"Exporting Google Workspace file '{file_name}' as {export_mime_type}")
            request_obj = service.files().export_media(fileId=file_id, mimeType=export_mime_type)
        else:
            logger.info(f"Downloading binary file '{file_name}' (MIME: {mime_type})")
            request_obj = service.files().get_media(fileId=file_id)

        final_download_path_file = os.path.join(local_download_path_dir, file_name)
        os.makedirs(local_download_path_dir, exist_ok=True)

        fh = io.FileIO(final_download_path_file, 'wb')
        downloader = MediaIoBaseDownload(fh, request_obj)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            logger.info(f"Download {int(status.progress() * 100)}% for {file_name}.")
        fh.close()
        logger.info(f"File '{file_name}' downloaded to '{final_download_path_file}'.")
        return {"file_path": final_download_path_file, "file_name": file_name, "original_mime_type": mime_type}

    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError downloading file: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error downloading file: {str(e)}", exc_info=True); raise

def api_get_file_metadata(service, file_id):
    logger.info(f"API: Getting metadata for file_id '{file_id}'")
    try:
        file = service.files().get(fileId=file_id, fields='id, name, mimeType, webViewLink, createdTime, modifiedTime, parents, size, iconLink, capabilities, shared, owners, permissions').execute() # Added more fields
        logger.info(f"Metadata retrieved for file ID '{file_id}': Name '{file.get('name')}'")
        return file
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError getting file metadata: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error getting file metadata: {str(e)}", exc_info=True); raise

# --- Flask Endpoints for Drive ---

@drive_bp.route('/token', methods=['GET'])
def specific_user_token_drive_endpoint():
    endpoint_name = "/drive/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        access_token = get_global_specific_user_access_token() # Imported from shared_utils
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user (Drive context).")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        error_message = f"Failed to obtain access token: {str(e)}"
        if isinstance(e, ValueError): return jsonify({"success": False, "error": error_message}), 400
        return jsonify({"success": False, "error": error_message}), 500

@drive_bp.route('/folder/create', methods=['POST'])
def create_folder_endpoint():
    endpoint_name = "/drive/folder/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('folder_name', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'folder_name' or 'refresh_token'"}), 400
        folder_name = data['folder_name']; parent_folder_id = data.get('parent_folder_id'); refresh_token = data['refresh_token']
        
        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        folder_info = api_create_folder(service, folder_name, parent_folder_id)
        return jsonify({"success": True, "message": "Folder created successfully.", "folder": folder_info})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@drive_bp.route('/folder/list', methods=['POST'])
def list_folder_endpoint():
    endpoint_name = "/drive/folder/list"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        folder_id = data.get('folder_id', 'root'); page_size = int(data.get('page_size', 100)); refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        items = api_list_folder_contents(service, folder_id, page_size)
        return jsonify({"success": True, "folder_id": folder_id, "items": items, "count": len(items)})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500

@drive_bp.route('/file/upload', methods=['POST'])
def upload_file_endpoint():
    endpoint_name = "/drive/file/upload"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    temp_file_path = None # Initialize for cleanup in finally block
    try:
        if 'file' not in request.files: return jsonify({"success": False, "error": "No file part in the request"}), 400
        file_to_upload = request.files['file']
        if file_to_upload.filename == '': return jsonify({"success": False, "error": "No selected file"}), 400

        refresh_token = request.form.get('refresh_token')
        if not refresh_token: return jsonify({"success": False, "error": "Missing 'refresh_token' in form data"}), 400

        file_name = request.form.get('file_name', file_to_upload.filename)
        mime_type = request.form.get('mime_type', file_to_upload.mimetype)
        folder_id = request.form.get('folder_id')

        temp_dir = "/tmp"; os.makedirs(temp_dir, exist_ok=True)
        # Sanitize filename to prevent directory traversal or invalid characters for temp file
        safe_filename = "".join(c for c in file_name if c.isalnum() or c in ('.', '_', '-')).rstrip()
        if not safe_filename: safe_filename = "uploaded_file" # Default if all chars are stripped
        temp_file_path = os.path.join(temp_dir, safe_filename)
        
        file_to_upload.save(temp_file_path)
        logger.info(f"Temporary file saved at {temp_file_path}")

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        upload_result = api_upload_file(service, temp_file_path, file_name, mime_type, folder_id)
        
        return jsonify({"success": True, "message": "File uploaded successfully.", "file_info": upload_result})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Temporary file {temp_file_path} removed.")
            except Exception as ex_clean:
                logger.error(f"Error cleaning up temp file {temp_file_path}: {ex_clean}")

@drive_bp.route('/file/<file_id>/download', methods=['POST'])
def download_file_endpoint(file_id):
    endpoint_name = f"/drive/file/{file_id}/download"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    downloaded_file_path_to_send = None
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        
        local_download_path_dir = data.get('local_download_path_dir', '/tmp/google_drive_downloads') 

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        download_info = api_download_file(service, file_id, local_download_path_dir)
        downloaded_file_path_to_send = download_info['file_path']
        
        return send_file(downloaded_file_path_to_send, as_attachment=True, download_name=download_info['file_name'])
    except FileNotFoundError:
        logger.error(f"ENDPOINT {endpoint_name}: Downloaded file path not found locally for sending: {downloaded_file_path_to_send}", exc_info=True)
        return jsonify({"success": False, "error": "File downloaded but couldn't be sent. Check server logs."}), 500
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500
    finally:
        # Cleanup of the downloaded file after sending is tricky with send_file as it streams.
        # A better approach for serverless is often to return a signed URL from cloud storage.
        # If keeping this, you might need a background job or accept that /tmp fills up over time
        # or that some serverless environments clean /tmp per invocation.
        # For now, no explicit post-send cleanup here to avoid complexity with Flask's send_file.
        if downloaded_file_path_to_send and os.path.exists(downloaded_file_path_to_send):
             logger.warning(f"File '{downloaded_file_path_to_send}' was sent. Consider a cleanup strategy for /tmp files.")
             # For example, you could try to remove it here but it might be too soon if send_file is async
             # try:
             #     os.remove(downloaded_file_path_to_send)
             # except Exception as e_clean:
             #     logger.error(f"Could not clean up {downloaded_file_path_to_send}: {e_clean}")


@drive_bp.route('/file/<file_id>/metadata', methods=['POST'])
def get_file_metadata_endpoint(file_id):
    endpoint_name = f"/drive/file/{file_id}/metadata"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        metadata = api_get_file_metadata(service, file_id)
        return jsonify({"success": True, "metadata": metadata})
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); status = e.resp.status if hasattr(e, 'resp') else 500; logger.error(f"ENDPOINT {endpoint_name}: Google API HttpError: {error_content}", exc_info=True); return jsonify({"success": False, "error": "Google API Error", "details": error_content}), status
    except ValueError as ve: logger.error(f"ENDPOINT {endpoint_name}: Value error: {str(ve)}", exc_info=True); return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Generic exception: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"An unexpected error occurred: {str(e)}"}), 500
