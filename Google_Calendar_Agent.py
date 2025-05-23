from flask import jsonify, request, Blueprint, current_app
import logging
import datetime
import dateparser
import pytz
import json # For parsing HttpError content if it's JSON

from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Assuming shared_utils.py contains:
# def get_access_token(refresh_token_value, client_id_value, client_secret_value, token_uri="https://oauth2.googleapis.com/token"):
# And it correctly uses client_id_value and client_secret_value from app.config
from shared_utils import get_access_token
# get_global_specific_user_access_token is not used in these calendar endpoints if refresh token comes from header

logger = logging.getLogger(__name__)
calendar_bp = Blueprint('calendar_agent', __name__, url_prefix='/calendar')

EVENT_COLOR_MAP = {
    "default": None, "lavender": "1", "sage": "2", "grape": "3", "flamingo": "4",
    "banana": "5", "tangerine": "6", "peacock": "7", "graphite": "8",
    "blueberry": "9", "basil": "10", "tomato": "11"
}
HARDCODED_FALLBACK_TIMEZONE = 'Asia/Dubai' # Or 'UTC'
REFRESH_TOKEN_HEADER = 'X-Refresh-Token' # Define the header name

# --- Google API Service Helper ---
def get_calendar_service(access_token):
    logger.info("Building Google Calendar API service object...")
    if not access_token:
        raise ValueError("Access token is required to build calendar service.")
    try:
        creds = OAuthCredentials(token=access_token)
        return build("calendar", "v3", credentials=creds, static_discovery=False)
    except Exception as e:
        logger.error(f"Failed to build Google Calendar service: {str(e)}", exc_info=True)
        raise

# --- Date Parsing Helper ---
def parse_datetime_to_iso(datetime_str, prefer_future=True, default_timezone_str=HARDCODED_FALLBACK_TIMEZONE, settings_override=None):
    if not datetime_str: return None
    settings = {'PREFER_DATES_FROM': 'future' if prefer_future else 'past', 'RETURN_AS_TIMEZONE_AWARE': True}
    if settings_override and isinstance(settings_override, dict): settings.update(settings_override)
    
    effective_parser_timezone = settings.get('TIMEZONE', default_timezone_str)
    settings['TIMEZONE'] = effective_parser_timezone
    logger.debug(f"Dateparser: Using TIMEZONE '{effective_parser_timezone}' for parsing '{datetime_str}'.")
    parsed_dt = dateparser.parse(datetime_str, settings=settings)

    if parsed_dt:
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
            logger.warning(f"Parsed '{datetime_str}' as naive. Localizing with '{effective_parser_timezone}'.")
            try:
                tz_object = pytz.timezone(effective_parser_timezone)
                parsed_dt = tz_object.localize(parsed_dt, is_dst=None)
            except Exception as e: # Catch generic pytz errors too
                logger.error(f"Error localizing to '{effective_parser_timezone}': {e}. Falling back to UTC.", exc_info=True)
                # Ensure parsed_dt is naive before replacing tzinfo or localizing to UTC
                parsed_dt_naive = parsed_dt.replace(tzinfo=None)
                parsed_dt = pytz.utc.localize(parsed_dt_naive)
        return parsed_dt.isoformat()
    else:
        logger.warning(f"Could not parse datetime string: '{datetime_str}' with settings: {settings}")
        return None

# --- Google Calendar API Wrappers ---
def api_get_calendar_timezone(service):
    try:
        setting = service.settings().get(setting='timezone').execute()
        return setting.get('value')
    except Exception: # Catch all, log in calling function
        logger.warning(f"API: Could not fetch calendar timezone setting.", exc_info=True)
        return None

def api_list_events(service, calendar_id="primary", time_min_iso=None, time_max_iso=None, max_results=50):
    try:
        return service.events().list(
            calendarId=calendar_id, timeMin=time_min_iso, timeMax=time_max_iso,
            maxResults=max_results, singleEvents=True, orderBy="startTime"
        ).execute().get('items', [])
    except Exception: # Catch all, log in calling function
        raise

def api_create_event(service, calendar_id="primary", summary="Untitled Event", **kwargs):
    event_body = {'summary': summary}
    event_body.update({k: v for k, v in kwargs.items() if v is not None}) # Add other details
    
    # Ensure start/end structure based on provided ISO strings
    if kwargs.get('start_datetime_iso') and kwargs.get('end_datetime_iso'):
        event_body['start'] = {'dateTime': kwargs['start_datetime_iso']}
        event_body['end'] = {'dateTime': kwargs['end_datetime_iso']}
        if kwargs.get('timezone_for_api'): # This is the timezone for the event itself
            event_body['start']['timeZone'] = kwargs['timezone_for_api']
            event_body['end']['timeZone'] = kwargs['timezone_for_api']
    elif kwargs.get('start_date_iso') and kwargs.get('end_date_iso'):
        event_body['start'] = {'date': kwargs['start_date_iso']}
        event_body['end'] = {'date': kwargs['end_date_iso']}
    else:
        raise ValueError("Event creation requires start/end for timed or all-day event.")

    # Remove internal keys not meant for Google API body
    event_body.pop('start_datetime_iso', None)
    event_body.pop('end_datetime_iso', None)
    event_body.pop('start_date_iso', None)
    event_body.pop('end_date_iso', None)
    event_body.pop('timezone_for_api', None)

    try:
        return service.events().insert(calendarId=calendar_id, body=event_body).execute()
    except Exception: # Catch all, log in calling function
        raise

def api_update_event(service, calendar_id, event_id, update_body):
    if not event_id: raise ValueError("event_id is required.")
    if not update_body: raise ValueError("No fields provided for update.")
    try:
        return service.events().patch(calendarId=calendar_id, eventId=event_id, body=update_body).execute()
    except Exception: # Catch all, log in calling function
        raise

def api_delete_event(service, calendar_id="primary", event_id=None):
    if not event_id: raise ValueError("event_id is required.")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return {"eventId": event_id, "status": "deleted"}
    except HttpError as e:
        if hasattr(e, 'resp') and (e.resp.status == 404 or e.resp.status == 410):
            return {"eventId": event_id, "status": "notFoundOrGone"}
        raise # Re-raise other HttpErrors
    except Exception: # Catch all, log in calling function
        raise

# --- Helper to get refresh token and handle common errors ---
def get_refresh_token_from_header():
    refresh_token = request.headers.get(REFRESH_TOKEN_HEADER)
    if not refresh_token:
        # This error indicates the client didn't send the header.
        # A 400 Bad Request is appropriate. A 401 might imply the token was sent but invalid.
        raise ValueError(f"Missing required header: {REFRESH_TOKEN_HEADER}")
    return refresh_token

# --- Flask Endpoints ---
@calendar_bp.route('/events/list', methods=['POST'])
def list_events_endpoint():
    endpoint_name = "/calendar/events/list"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header()
        data = request.json or {} # Ensure data is a dict even if body is empty (though schema implies body)

        calendar_id = data.get('calendar_id', 'primary')
        date_natural = data.get('date_natural')
        time_min_natural = data.get('time_min_natural')
        time_max_natural = data.get('time_max_natural')
        user_timezone_for_parsing = data.get('user_timezone', 'UTC')
        logger.info(f"List events: Using timezone '{user_timezone_for_parsing}' for parsing.")

        time_min_iso, time_max_iso = None, None
        dp_settings = {'TIMEZONE': user_timezone_for_parsing, 'RETURN_AS_TIMEZONE_AWARE': True}

        if date_natural:
            parsed_date_obj = dateparser.parse(date_natural, settings=dp_settings)
            if parsed_date_obj:
                if parsed_date_obj.tzinfo is None:
                    parsed_date_obj = pytz.timezone(user_timezone_for_parsing).localize(parsed_date_obj.replace(tzinfo=None))
                time_min_iso = parsed_date_obj.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                time_max_iso = parsed_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            else: return jsonify({"success": False, "error": f"Could not parse date: '{date_natural}'"}), 400
        else:
            if time_min_natural:
                time_min_iso = parse_datetime_to_iso(time_min_natural, False, user_timezone_for_parsing)
                if not time_min_iso: return jsonify({"success": False, "error": f"Could not parse start time: '{time_min_natural}'"}), 400
            else:
                now_local = datetime.datetime.now(pytz.timezone(user_timezone_for_parsing))
                time_min_iso = now_local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

            if time_max_natural:
                time_max_iso = parse_datetime_to_iso(time_max_natural, True, user_timezone_for_parsing)
                if not time_max_iso: return jsonify({"success": False, "error": f"Could not parse end time: '{time_max_natural}'"}), 400
            else:
                parsed_min_dt = dateparser.parse(time_min_iso) # time_min_iso is already ISO with TZ
                if parsed_min_dt:
                    time_max_iso = parsed_min_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
                else: # Fallback, should not happen
                    now_local = datetime.datetime.now(pytz.timezone(user_timezone_for_parsing))
                    time_max_iso = now_local.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        events = api_list_events(service, calendar_id, time_min_iso, time_max_iso)
        return jsonify({"success": True, "calendar_id": calendar_id, "events": events, "count": len(events)})

    except ValueError as ve: # Catches missing header, client_id/secret issues from get_access_token
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Input or Configuration Error", "details": str(ve)}), 400
    except HttpError as he:
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        details = str(he)
        try: details = json.loads(he.content.decode('utf-8'))
        except: pass
        return jsonify({"success": False, "error": "Google API Error", "details": details}), getattr(he.resp, 'status', 500)
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "An unexpected error occurred", "details": str(e)}), 500


@calendar_bp.route('/event/create', methods=['POST'])
def create_event_endpoint():
    endpoint_name = "/calendar/event/create"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header()
        data = request.json or {}
        if 'summary' not in data or not data['summary']:
            return jsonify({"success": False, "error": "Event 'summary' is required."}), 400
        summary = data['summary']

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)

        user_req_tz = data.get('timezone')
        final_parsing_tz = user_req_tz or api_get_calendar_timezone(service) or HARDCODED_FALLBACK_TIMEZONE
        logger.info(f"Create event: Using timezone '{final_parsing_tz}' for parsing.")
        
        api_event_args = {
            "description": data.get('description'),
            "location": data.get('location'),
            "recurrence_rules": data.get('recurrence_rules'),
            "attendees": [{'email': e} for e in data.get('attendees', []) if isinstance(e, str)] or None
        }

        # Color
        color_input = data.get('color')
        if color_input:
            color_lower = str(color_input).lower()
            if color_lower in EVENT_COLOR_MAP: api_event_args["color_id"] = EVENT_COLOR_MAP[color_lower]
            elif color_lower.isdigit() and 1 <= int(color_lower) <= 11: api_event_args["color_id"] = color_lower
            else: logger.warning(f"Invalid color '{color_input}'. Using default.")

        # Times/Dates
        start_natural, end_natural = data.get('start_natural'), data.get('end_natural')
        start_date_natural, end_date_natural = data.get('start_date_natural'), data.get('end_date_natural')
        dp_settings = {'TIMEZONE': final_parsing_tz, 'RETURN_AS_TIMEZONE_AWARE': True}

        if start_natural: # Timed event
            api_event_args["start_datetime_iso"] = parse_datetime_to_iso(start_natural, True, final_parsing_tz)
            if not api_event_args["start_datetime_iso"]:
                return jsonify({"success": False, "error": f"Could not parse start time: '{start_natural}'"}), 400
            
            if end_natural:
                api_event_args["end_datetime_iso"] = parse_datetime_to_iso(end_natural, True, final_parsing_tz)
                if not api_event_args["end_datetime_iso"]:
                    return jsonify({"success": False, "error": f"Could not parse end time: '{end_natural}'"}), 400
            else: # Default 1hr
                start_dt_obj = dateparser.parse(api_event_args["start_datetime_iso"]) # Already ISO w TZ
                api_event_args["end_datetime_iso"] = (start_dt_obj + datetime.timedelta(hours=1)).isoformat()
            api_event_args["timezone_for_api"] = final_parsing_tz # Timezone for the event on Google Calendar
        
        elif start_date_natural: # All-day event
            start_obj = dateparser.parse(start_date_natural, settings=dp_settings)
            if not start_obj: return jsonify({"success": False, "error": f"Could not parse start date: '{start_date_natural}'"}), 400
            api_event_args["start_date_iso"] = start_obj.strftime('%Y-%m-%d')

            if end_date_natural:
                end_obj = dateparser.parse(end_date_natural, settings=dp_settings)
                if not end_obj: return jsonify({"success": False, "error": f"Could not parse end date: '{end_date_natural}'"}), 400
                api_event_args["end_date_iso"] = (end_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                api_event_args["end_date_iso"] = (start_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            api_event_args["timezone_for_api"] = None # No specific TZ for all-day events in Google API body
        else:
            return jsonify({"success": False, "error": "Start time/date (start_natural or start_date_natural) is required."}), 400
        
        created_event = api_create_event(service, data.get('calendar_id', 'primary'), summary, **api_event_args)
        return jsonify({"success": True, "message": "Event created successfully.", "event": created_event})

    except ValueError as ve:
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Input or Configuration Error", "details": str(ve)}), 400
    except HttpError as he:
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        details = str(he)
        try: details = json.loads(he.content.decode('utf-8'))
        except: pass
        return jsonify({"success": False, "error": "Google API Error", "details": details}), getattr(he.resp, 'status', 500)
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "An unexpected error occurred", "details": str(e)}), 500


@calendar_bp.route('/event/update', methods=['POST'])
def update_event_endpoint():
    endpoint_name = "/calendar/event/update"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header()
        data = request.json or {}
        event_id = data.get('event_id')
        if not event_id:
            return jsonify({"success": False, "error": "'event_id' is required."}), 400

        update_payload = {}
        if 'summary' in data: update_payload['summary'] = data['summary']
        if 'description' in data: update_payload['description'] = data['description']
        if 'location' in data: update_payload['location'] = data['location']
        if 'color' in data:
            color_input = data['color']
            if color_input is None or str(color_input).lower() == 'default': update_payload['colorId'] = None
            else:
                color_lower = str(color_input).lower()
                if color_lower in EVENT_COLOR_MAP: update_payload['colorId'] = EVENT_COLOR_MAP[color_lower]
                elif color_lower.isdigit() and 1 <= int(color_lower) <= 11: update_payload['colorId'] = color_lower
                else: return jsonify({"success": False, "error": f"Invalid color value: '{color_input}'"}), 400
        
        if not update_payload:
            return jsonify({"success": False, "error": "No updatable fields provided."}), 400

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        updated_event = api_update_event(service, data.get('calendar_id', 'primary'), event_id, update_payload)
        return jsonify({"success": True, "message": "Event updated successfully.", "event": updated_event})

    except ValueError as ve:
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Input or Configuration Error", "details": str(ve)}), 400
    except HttpError as he:
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        details = str(he); status_code = getattr(he.resp, 'status', 500)
        try: details = json.loads(he.content.decode('utf-8'))
        except: pass
        if status_code == 404:
            return jsonify({"success": False, "error": "Event not found.", "details": details}), 404
        return jsonify({"success": False, "error": "Google API Error", "details": details}), status_code
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "An unexpected error occurred", "details": str(e)}), 500


@calendar_bp.route('/event/delete', methods=['POST'])
def delete_event_endpoint():
    endpoint_name = "/calendar/event/delete"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header()
        data = request.json or {}
        event_id = data.get('event_id')
        if not event_id:
            return jsonify({"success": False, "error": "'event_id' is required."}), 400

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        delete_details = api_delete_event(service, data.get('calendar_id', 'primary'), event_id)
        return jsonify({"success": True, "message": f"Event '{event_id}' deletion processed.", "details": delete_details})

    except ValueError as ve:
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Input or Configuration Error", "details": str(ve)}), 400
    except HttpError as he: # Should be rare if api_delete_event handles 404/410
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        details = str(he)
        try: details = json.loads(he.content.decode('utf-8'))
        except: pass
        return jsonify({"success": False, "error": "Google API Error", "details": details}), getattr(he.resp, 'status', 500)
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "An unexpected error occurred", "details": str(e)}), 500
