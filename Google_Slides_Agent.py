from flask import jsonify, request, Blueprint, current_app
import logging
import time
import os
import requests # If get_access_token is still here or for image fetching

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# from googleapiclient.http import MediaFileUpload # For uploading, less common for Slides directly unless embedding

# Import shared helper functions
from shared_utils import get_access_token, get_global_specific_user_access_token

logger = logging.getLogger(__name__)
slides_bp = Blueprint('slides_agent', __name__, url_prefix='/slides')

# --- get_access_token should be imported from shared_utils.py ---
# If it's not, you'd have the definition here or import it.
# For this example, we assume it's correctly imported from shared_utils.

def get_slides_service(access_token):
    logger.info("Building Google Slides API service object...")
    if not access_token:
        logger.error("Cannot build slides service: access_token is missing.")
        raise ValueError("Access token is required to build slides service.")
    try:
        creds = OAuthCredentials(token=access_token)
        service = build("slides", "v1", credentials=creds, static_discovery=False)
        logger.info("Google Slides API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Slides API service object: {str(e)}", exc_info=True)
        raise

# --- Google Slides API Wrapper Functions ---

def api_create_presentation(service, title):
    logger.info(f"API: Creating new presentation with title '{title}'.")
    try:
        body = {'title': title}
        presentation = service.presentations().create(body=body).execute()
        logger.info(f"Presentation created successfully: ID '{presentation.get('presentationId')}', Title '{presentation.get('title')}'")
        return presentation
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating presentation: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error creating presentation: {str(e)}", exc_info=True); raise

def api_get_presentation(service, presentation_id):
    logger.info(f"API: Getting presentation with ID '{presentation_id}'.")
    try:
        presentation = service.presentations().get(presentationId=presentation_id).execute()
        logger.info(f"Presentation retrieved: ID '{presentation.get('presentationId')}', Title '{presentation.get('title')}'")
        return presentation
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError getting presentation: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error getting presentation: {str(e)}", exc_info=True); raise

def api_batch_update_presentation(service, presentation_id, requests_list):
    logger.info(f"API: Batch updating presentation '{presentation_id}' with {len(requests_list)} requests.")
    logger.debug(f"API: Batch update requests: {requests_list}")
    try:
        body = {'requests': requests_list}
        response = service.presentations().batchUpdate(presentationId=presentation_id, body=body).execute()
        logger.info(f"Batch update successful for presentation '{presentation_id}'.")
        return response
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError batch updating presentation: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error batch updating presentation: {str(e)}", exc_info=True); raise

def api_create_slide(service, presentation_id, slide_layout_reference_id="BLANK", placeholder_id_mappings=None, index=None):
    logger.info(f"API: Creating new slide in presentation '{presentation_id}' using layout '{slide_layout_reference_id}'.")
    requests_list = []
    create_slide_request = {
        'createSlide': {
            'slideLayoutReference': {
                'predefinedLayout': slide_layout_reference_id # e.g., 'TITLE_SLIDE', 'BLANK', 'TITLE_AND_BODY'
            }
        }
    }
    if placeholder_id_mappings: # For specific object IDs on the new slide
        create_slide_request['createSlide']['placeholderIdMappings'] = placeholder_id_mappings
    if index is not None: # 0-based index
        create_slide_request['createSlide']['insertionIndex'] = index
    
    requests_list.append(create_slide_request)
    
    # We'll return the response which contains the ID of the created slide
    response = api_batch_update_presentation(service, presentation_id, requests_list)
    created_slide_info = {}
    if response and response.get('replies'):
        for reply in response.get('replies'):
            if reply.get('createSlide'):
                created_slide_info = reply.get('createSlide')
                logger.info(f"Slide created with ID: {created_slide_info.get('objectId')}")
                break
    return created_slide_info # Returns {'objectId': 'new_slide_id'}

def api_insert_text_into_shape(service, presentation_id, shape_object_id, text_to_insert, insertion_index=0):
    logger.info(f"API: Inserting text '{text_to_insert}' into shape '{shape_object_id}' in presentation '{presentation_id}'.")
    requests_list = [
        {
            'insertText': {
                'objectId': shape_object_id,
                'insertionIndex': insertion_index, # 0 to prepend, or len(existing_text) to append
                'text': text_to_insert
            }
        }
    ]
    return api_batch_update_presentation(service, presentation_id, requests_list)

def api_delete_text_from_shape(service, presentation_id, shape_object_id, start_index, end_index):
    logger.info(f"API: Deleting text from shape '{shape_object_id}' range ({start_index}-{end_index}) in presentation '{presentation_id}'.")
    requests_list = [
        {
            'deleteText': {
                'objectId': shape_object_id,
                'textRange': {
                    'type': 'FIXED_RANGE', # or 'ALL'
                    'startIndex': start_index,
                    'endIndex': end_index
                }
            }
        }
    ]
    return api_batch_update_presentation(service, presentation_id, requests_list)

def api_update_text_style(service, presentation_id, shape_object_id, start_index, end_index, foreground_color_rgb=None, bold=None, italic=None, font_family=None, font_size_pt=None):
    logger.info(f"API: Updating text style for shape '{shape_object_id}' range ({start_index}-{end_index}).")
    style = {}
    fields = []
    if foreground_color_rgb: # e.g., {"red": 1.0, "green": 0.0, "blue": 0.0} for red
        style['foregroundColor'] = {'opaqueColor': {'rgbColor': foreground_color_rgb}}
        fields.append('foregroundColor')
    if bold is not None:
        style['bold'] = bold
        fields.append('bold')
    if italic is not None:
        style['italic'] = italic
        fields.append('italic')
    if font_family:
        style['fontFamily'] = font_family
        fields.append('fontFamily')
    if font_size_pt:
        style['fontSize'] = {'magnitude': font_size_pt, 'unit': 'PT'}
        fields.append('fontSize')

    if not fields:
        logger.warning("API: No text style attributes provided for update.")
        return {"warning": "No text style attributes provided."}

    requests_list = [
        {
            'updateTextStyle': {
                'objectId': shape_object_id,
                'textRange': {'type': 'FIXED_RANGE', 'startIndex': start_index, 'endIndex': end_index},
                'style': style,
                'fields': ",".join(fields) # e.g. "foregroundColor,bold"
            }
        }
    ]
    return api_batch_update_presentation(service, presentation_id, requests_list)

def api_update_page_background(service, presentation_id, page_object_id, background_fill_rgb=None):
    # background_fill_rgb e.g. {"red": 0.9, "green": 0.9, "blue": 0.9} for light gray
    logger.info(f"API: Updating background for page '{page_object_id}'.")
    if not background_fill_rgb:
        logger.warning("API: No background fill color provided.")
        return {"warning": "No background fill color provided."}
    
    requests_list = [
        {
            'updatePageProperties': {
                'objectId': page_object_id,
                'pageProperties': {
                    'pageBackgroundFill': {
                        'solidFill': {
                            'color': {'rgbColor': background_fill_rgb}
                        }
                    }
                },
                'fields': 'pageBackgroundFill.solidFill.color'
            }
        }
    ]
    return api_batch_update_presentation(service, presentation_id, requests_list)

def api_create_image(service, presentation_id, page_object_id, image_url, size_width_pt, size_height_pt, transform_x_pt, transform_y_pt):
    logger.info(f"API: Adding image from URL '{image_url}' to page '{page_object_id}'.")
    # Element ID for the new image can be specified or auto-generated
    image_object_id = f"image_{int(time.time()*1000)}" # Simple unique ID

    requests_list = [
        {
            'createImage': {
                'objectId': image_object_id,
                'url': image_url,
                'elementProperties': {
                    'pageObjectId': page_object_id,
                    'size': {
                        'width': {'magnitude': size_width_pt, 'unit': 'PT'},
                        'height': {'magnitude': size_height_pt, 'unit': 'PT'}
                    },
                    'transform': { # Position from top-left
                        'scaleX': 1, 'scaleY': 1, 'shearX': 0, 'shearY': 0,
                        'translateX': transform_x_pt, 'translateY': transform_y_pt, 'unit': 'PT'
                    }
                }
            }
        }
    ]
    return api_batch_update_presentation(service, presentation_id, requests_list)

# --- Flask Endpoints for Slides ---

@slides_bp.route('/token', methods=['GET'])
def specific_user_token_slides_endpoint():
    # This assumes you have a global specific user token mechanism like in other agents
    endpoint_name = "/slides/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        access_token = get_global_specific_user_access_token() # Imported from shared_utils
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user (Slides context).")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": f"Failed to obtain access token: {str(e)}"}), 500

@slides_bp.route('/create', methods=['POST'])
def create_presentation_endpoint():
    endpoint_name = "/slides/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('title', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'title' or 'refresh_token'"}), 400
        title = data['title']; refresh_token = data['refresh_token']
        
        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        presentation_info = api_create_presentation(service, title)
        return jsonify({"success": True, "message": "Presentation created successfully.", "presentation": presentation_info})
    except Exception as e: # Catch generic exceptions too
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to create presentation", "details": error_details}), status_code

@slides_bp.route('/<presentation_id>/read', methods=['POST'])
def get_presentation_endpoint(presentation_id):
    endpoint_name = f"/slides/{presentation_id}/read"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        presentation_data = api_get_presentation(service, presentation_id)
        return jsonify({"success": True, "presentation": presentation_data})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to read presentation", "details": error_details}), status_code

@slides_bp.route('/<presentation_id>/slide/create', methods=['POST'])
def create_slide_endpoint(presentation_id):
    endpoint_name = f"/slides/{presentation_id}/slide/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        layout = data.get('layout', 'BLANK') # e.g., TITLE_SLIDE, TITLE_AND_BODY, BLANK
        index = data.get('index') # Optional: 0-based insertion index
        if index is not None: index = int(index)

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        slide_info = api_create_slide(service, presentation_id, slide_layout_reference_id=layout, index=index)
        return jsonify({"success": True, "message": "Slide created successfully.", "slide": slide_info})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to create slide", "details": error_details}), status_code

@slides_bp.route('/<presentation_id>/element/<element_object_id>/text/insert', methods=['POST'])
def insert_text_into_element_endpoint(presentation_id, element_object_id):
    endpoint_name = f"/slides/.../text/insert"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('text', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'text' or 'refresh_token'"}), 400
        text = data['text']; refresh_token = data['refresh_token']
        insertion_index = int(data.get('insertion_index', 0))

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        response = api_insert_text_into_shape(service, presentation_id, element_object_id, text, insertion_index)
        return jsonify({"success": True, "message": "Text inserted.", "details": response})
    except Exception as e: # ... (similar error handling)
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to insert text", "details": error_details}), status_code


@slides_bp.route('/<presentation_id>/element/<element_object_id>/text/delete', methods=['POST'])
def delete_text_from_element_endpoint(presentation_id, element_object_id):
    # ... (similar structure, call api_delete_text_from_shape)
    endpoint_name = f"/slides/.../text/delete"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('start_index', 'end_index', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'start_index', 'end_index', or 'refresh_token'"}), 400
        start_idx = int(data['start_index']); end_idx = int(data['end_index']); refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        response = api_delete_text_from_shape(service, presentation_id, element_object_id, start_idx, end_idx)
        return jsonify({"success": True, "message": "Text deleted.", "details": response})
    except Exception as e: # ... (similar error handling)
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to delete text", "details": error_details}), status_code

@slides_bp.route('/<presentation_id>/element/<element_object_id>/text/style', methods=['POST'])
def style_text_in_element_endpoint(presentation_id, element_object_id):
    # ... (similar structure, call api_update_text_style)
    endpoint_name = f"/slides/.../text/style"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('start_index', 'end_index', 'refresh_token')): # style itself is optional
            return jsonify({"success": False, "error": "Missing 'start_index', 'end_index', or 'refresh_token'"}), 400
        
        start_idx = int(data['start_index']); end_idx = int(data['end_index']); refresh_token = data['refresh_token']
        color = data.get('color_rgb'); bold = data.get('bold'); italic = data.get('italic')
        font_family = data.get('font_family'); font_size = data.get('font_size_pt')
        if font_size is not None: font_size = float(font_size)

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        response = api_update_text_style(service, presentation_id, element_object_id, start_idx, end_idx, 
                                         foreground_color_rgb=color, bold=bold, italic=italic, 
                                         font_family=font_family, font_size_pt=font_size)
        return jsonify({"success": True, "message": "Text style updated.", "details": response})
    except Exception as e: # ... (similar error handling)
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to style text", "details": error_details}), status_code


@slides_bp.route('/<presentation_id>/page/<page_object_id>/background', methods=['POST'])
def change_page_background_endpoint(presentation_id, page_object_id):
    # ... (similar structure, call api_update_page_background)
    endpoint_name = f"/slides/.../background"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('color_rgb', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'color_rgb' or 'refresh_token'"}), 400
        color = data['color_rgb']; refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        response = api_update_page_background(service, presentation_id, page_object_id, background_fill_rgb=color)
        return jsonify({"success": True, "message": "Page background updated.", "details": response})
    except Exception as e: # ... (similar error handling)
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to update background", "details": error_details}), status_code

@slides_bp.route('/<presentation_id>/page/<page_object_id>/image/add', methods=['POST'])
def add_image_to_page_endpoint(presentation_id, page_object_id):
    # ... (similar structure, call api_create_image)
    endpoint_name = f"/slides/.../image/add"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        required_fields = ['image_url', 'width_pt', 'height_pt', 'x_pt', 'y_pt', 'refresh_token']
        if not all(k in data for k in required_fields):
            return jsonify({"success": False, "error": f"Missing one or more required fields: {', '.join(required_fields)}"}), 400
        
        img_url = data['image_url']; w = float(data['width_pt']); h = float(data['height_pt'])
        x = float(data['x_pt']); y = float(data['y_pt']); refresh_token = data['refresh_token']

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_slides_service(access_token)
        response = api_create_image(service, presentation_id, page_object_id, img_url, w, h, x, y)
        return jsonify({"success": True, "message": "Image added.", "details": response})
    except Exception as e: # ... (similar error handling)
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to add image", "details": error_details}), status_code
