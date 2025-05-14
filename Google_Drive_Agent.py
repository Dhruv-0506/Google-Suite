from flask import jsonify, request, Blueprint, current_app, send_file
import logging
import time
import io # For sending file data
import requests # For get_access_token and potentially downloading files

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# Import the centralized function for specific user tokens (if you decide to have a /drive/token endpoint)
# from shared_utils import get_global_specific_user_access_token
# For now, we'll assume data manipulation endpoints use user-provided refresh tokens

logger = logging.getLogger(__name__)
drive_bp = Blueprint('drive_agent', __name__, url_prefix='/drive')

# --- get_access_token (Likely identical to Sheets/Docs agent - should be in shared_utils.py) ---
# For this example, I'll include a copy here. In a real setup, import from shared_utils.
def get_access_token(refresh_token, client_id, client_secret):
    logger.info(f"Drive Agent: Getting access token for refresh token: {refresh_token[:10]}...")
    start_time = time.time()
    if not client_secret:
        logger.error("CRITICAL: Client secret not available for token refresh (Drive Agent).")
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
    try:
        response = requests.post(token_url, data=payload, timeout=request_timeout)
        response.raise_for_status()
        token_data = response.json()
        access_token_val = token_data.get("access_token")
        duration = time.time() - start_time
        if access_token_val:
            logger.info(f"Successfully obtained new access token via refresh in {duration:.2f} seconds.")
            return access_token_val
        else:
            raise ValueError("Access token not found in refresh response.")
    except requests.exceptions.HTTPError as e:
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        logger.error(f"HTTPError ({e.response.status_code if hasattr(e, 'response') and e.response else 'Unknown'}) during token refresh (Drive): {error_text}", exc_info=True)
        if "invalid_grant" in error_text:
            logger.warning("Token refresh failed with 'invalid_grant'. Ensure refresh token has Drive scopes.")
        raise
    # ... (other exception handling from previous agent files) ...
    except requests.exceptions.Timeout:
        duration = time.time() - start_time; logger.error(f"Timeout ({request_timeout}s) during token refresh (Drive) after {duration:.2f} seconds."); raise
    except Exception as e:
        duration = time.time() - start_time; logger.error(f"Generic exception during token refresh (Drive) after {duration:.2f} seconds: {str(e)}", exc_info=True); raise


def get_drive_service(access_token):
    logger.info("Building Google Drive API service object...")
    if not access_token:
        logger.error("Cannot build drive service: access_token is missing.")
        raise ValueError("Access token is required to build drive service.")
    try:
        creds = OAuthCredentials(token=access_token)
        # Using version v3 of the Drive API
        service = build("drive", "v3", credentials=creds, static_discovery=False)
        logger.info("Google Drive API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Drive API service object: {str(e)}", exc_info=True)
        raise

# --- Google Drive API Wrapper Functions ---

def api_create_folder(service, folder_name, parent_folder_id=None):
    logger.info(f"API: Creating folder '{folder_name}'" + (f" inside parent '{parent_folder_id}'" if parent_folder_id else ""))
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_folder_id:
        file_metadata['parents'] = [parent_folder_id]
    try:
        folder = service.files().create(body=file_metadata, fields='id, name').execute()
        logger.info(f"Folder created successfully: ID '{folder.get('id')}', Name '{folder.get('name')}'")
        return folder
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating folder: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error creating folder: {str(e)}", exc_info=True); raise

def api_list_folder_contents(service, folder_id="root", page_size=100):
    # 'root' is a special alias for the main "My Drive" folder
    logger.info(f"API: Listing contents of folder_id '{folder_id}'")
    query = f"'{folder_id}' in parents and trashed = false"
    try:
        results = service.files().list(
            q=query,
            pageSize=page_size,
            fields="nextPageToken, files(id, name, mimeType, webViewLink, createdTime, modifiedTime, size, iconLink)"
        ).execute()
        items = results.get('files', [])
        logger.info(f"Found {len(items)} items in folder '{folder_id}'.")
        return items
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError listing folder contents: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error listing folder contents: {str(e)}", exc_info=True); raise

def api_upload_file(service, local_file_path, file_name, mime_type, folder_id=None):
    logger.info(f"API: Uploading file '{local_file_path}' as '{file_name}' (MIME: {mime_type})" + (f" to folder '{folder_id}'" if folder_id else ""))
    file_metadata = {'name': file_name}
    if folder_id:
        file_metadata['parents'] = [folder_id]
    try:
        media = MediaFileUpload(local_file_path, mimetype=mime_type, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id, name, webViewLink').execute()
        logger.info(f"File uploaded successfully: ID '{file.get('id')}', Name '{file.get('name')}', Link: {file.get('webViewLink')}")
        return file
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError uploading file: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error uploading file: {str(e)}", exc_info=True); raise

def api_download_file(service, file_id, local_download_path):
    logger.info(f"API: Downloading file_id '{file_id}' to '{local_download_path}'")
    try:
        # First, get file metadata to check MIME type for potential export
        file_metadata = service.files().get(fileId=file_id, fields='id, name, mimeType').execute()
        file_name = file_metadata.get('name')
        mime_type = file_metadata.get('mimeType')
        
        request = None
        # For Google Workspace types, we need to export them
        if mime_type == 'application/vnd.google-apps.document':
            request = service.files().export_media(fileId=file_id, mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            # You might want to adjust filename extension based on export type
            if not file_name.lower().endswith('.docx'):
                 file_name += '.docx'
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            request = service.files().export_media(fileId=file_id, mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            if not file_name.lower().endswith('.xlsx'):
                 file_name += '.xlsx'
        elif mime_type == 'application/vnd.google-apps.presentation':
            request = service.files().export_media(fileId=file_id, mimeType='application/vnd.openxmlformats-officedocument.presentationml.presentation')
            if not file_name.lower().endswith('.pptx'):
                 file_name += '.pptx'
        else:
            # For other file types (PDF, images, etc.), download directly
            request = service.files().get_media(fileId=file_id)

        final_download_path = os.path.join(local_download_path, file_name)
        os.makedirs(local_download_path, exist_ok=True) # Ensure directory exists

        fh = io.FileIO(final_download_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            logger.info(f"Download {int(status.progress() * 100)}%.")
        fh.close()
        logger.info(f"File '{file_name}' downloaded successfully to '{final_download_path}'.")
        return {"file_path": final_download_path, "file_name": file_name, "original_mime_type": mime_type}

    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError downloading file: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error downloading file: {str(e)}", exc_info=True); raise

def api_get_file_metadata(service, file_id):
    logger.info(f"API: Getting metadata for file_id '{file_id}'")
    try:
        file = service.files().get(fileId=file_id, fields='id, name, mimeType, webViewLink, createdTime, modifiedTime, parents, size, iconLink, capabilities').execute()
        logger.info(f"Metadata retrieved for file ID '{file_id}': Name '{file.get('name')}'")
        return file
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError getting file metadata: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error getting file metadata: {str(e)}", exc_info=True); raise


# --- Flask Endpoints for Drive ---

@drive_bp.route('/folder/create', methods=['POST'])
def create_folder_endpoint():
    endpoint_name = "/drive/folder/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('folder_name', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'folder_name' or 'refresh_token'"}), 400
        folder_name = data['folder_name']
        parent_folder_id = data.get('parent_folder_id') # Optional
        refresh_token = data['refresh_token']
        
        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        
        folder_info = api_create_folder(service, folder_name, parent_folder_id)
        return jsonify({"success": True, "message": "Folder created successfully.", "folder": folder_info})
    except Exception as e: # Catch generic exceptions too
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to create folder", "details": error_details}), status_code


@drive_bp.route('/folder/list', methods=['POST']) # Using POST to send folder_id and refresh_token in body
def list_folder_endpoint():
    endpoint_name = "/drive/folder/list"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data:
            return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
            
        folder_id = data.get('folder_id', 'root') # Default to root if not provided
        page_size = int(data.get('page_size', 100))
        refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        
        items = api_list_folder_contents(service, folder_id, page_size)
        return jsonify({"success": True, "folder_id": folder_id, "items": items, "count": len(items)})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to list folder contents", "details": error_details}), status_code


@drive_bp.route('/file/upload', methods=['POST'])
def upload_file_endpoint():
    endpoint_name = "/drive/file/upload"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file part in the request"}), 400
        
        file_to_upload = request.files['file']
        if file_to_upload.filename == '':
            return jsonify({"success": False, "error": "No selected file"}), 400

        # Required form fields for metadata
        refresh_token = request.form.get('refresh_token')
        if not refresh_token:
            return jsonify({"success": False, "error": "Missing 'refresh_token' in form data"}), 400

        file_name = request.form.get('file_name', file_to_upload.filename) # Use provided name or original
        mime_type = request.form.get('mime_type', file_to_upload.mimetype) # Use provided or detected
        folder_id = request.form.get('folder_id') # Optional

        # Save the uploaded file temporarily to pass its path to MediaFileUpload
        # In a serverless environment, ensure you have write access to /tmp
        temp_dir = "/tmp" # Common writable directory in serverless/containers
        os.makedirs(temp_dir, exist_ok=True)
        temp_file_path = os.path.join(temp_dir, file_name)
        file_to_upload.save(temp_file_path)
        logger.info(f"Temporary file saved at {temp_file_path}")

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        
        upload_result = api_upload_file(service, temp_file_path, file_name, mime_type, folder_id)
        
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            logger.info(f"Temporary file {temp_file_path} removed.")

        return jsonify({"success": True, "message": "File uploaded successfully.", "file_info": upload_result})

    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        # Clean up temp file on error too
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Temporary file {temp_file_path} removed after error.")
            except Exception as ex_clean:
                logger.error(f"Error cleaning up temp file {temp_file_path}: {ex_clean}")
        return jsonify({"success": False, "error": "Failed to upload file", "details": error_details}), status_code


@drive_bp.route('/file/<file_id>/download', methods=['POST']) # POST to include refresh_token in body
def download_file_endpoint(file_id):
    endpoint_name = f"/drive/file/{file_id}/download"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data:
            return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        
        # Define a temporary download path. Ensure this is writable in your environment.
        # For serverless, /tmp is usually writable.
        local_download_path = data.get('local_download_path', '/tmp/google_drive_downloads') 

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        
        download_info = api_download_file(service, file_id, local_download_path)
        
        # Send the file back to the client
        # Note: In a truly serverless environment, you might upload this to a bucket
        # and return a signed URL, rather than streaming directly from the function instance
        # if files are large or requests are long-lived.
        # For now, we'll try to send it directly.
        return send_file(
            download_info['file_path'],
            as_attachment=True,
            download_name=download_info['file_name'] # Use the determined filename
        )
        # After sending, you might want to clean up the downloaded file from /tmp
        # This is tricky with send_file as it happens after the response is initiated.
        # A background task or cleanup mechanism might be needed for robust /tmp cleanup.

    except FileNotFoundError:
        logger.error(f"ENDPOINT {endpoint_name}: Downloaded file not found locally for sending.", exc_info=True)
        return jsonify({"success": False, "error": "File downloaded but couldn't be sent. Check server logs."}), 500
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to download file", "details": error_details}), status_code

@drive_bp.route('/file/<file_id>/metadata', methods=['POST']) # POST for refresh_token in body
def get_file_metadata_endpoint(file_id):
    endpoint_name = f"/drive/file/{file_id}/metadata"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data:
            return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_drive_service(access_token)
        
        metadata = api_get_file_metadata(service, file_id)
        return jsonify({"success": True, "metadata": metadata})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to get file metadata", "details": error_details}), status_code
