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
        # This will be caught by the endpoint's ValueError handler
        raise ValueError("Access token is required to build calendar service.")
    try:
        creds = OAuthCredentials(token=access_token)
        return build("calendar", "v3", credentials=creds, static_discovery=False)
    except Exception as e:
        logger.error(f"Failed to build Google Calendar service: {str(e)}", exc_info=True)
        raise # Re-raise to be handled by endpoint's generic Exception handler

# --- Date Parsing Helper ---
def parse_datetime_to_iso(datetime_str, prefer_future=True, default_timezone_str=HARDCODED_FALLBACK_TIMEZONE, settings_override=None):
    if not datetime_str: return None
    settings = {'PREFER_DATES_FROM': 'future' if prefer_future else 'past', 'RETURN_AS_TIMEZONE_AWARE': True}
    if settings_override and isinstance(settings_override, dict): settings.update(settings_override)
    
    effective_parser_timezone = settings.get('TIMEZONE', default_timezone_str)
    settings['TIMEZONE'] = effective_parser_timezone # Ensure it's in settings for dateparser
    logger.debug(f"Dateparser: Using TIMEZONE '{effective_parser_timezone}' for parsing '{datetime_str}'.")
    
    parsed_dt = dateparser.parse(datetime_str, settings=settings)

    if parsed_dt:
        # Ensure the datetime is timezone-aware
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
            logger.warning(f"Parsed '{datetime_str}' as naive. Attempting to localize with '{effective_parser_timezone}'.")
            try:
                tz_object = pytz.timezone(effective_parser_timezone)
                # dateparser might return datetime.date for date-only strings, handle that.
                if isinstance(parsed_dt, datetime.date) and not isinstance(parsed_dt, datetime.datetime):
                    # Convert date to datetime at midnight for localization
                    parsed_dt_datetime = datetime.datetime.combine(parsed_dt, datetime.time.min)
                    parsed_dt = tz_object.localize(parsed_dt_datetime, is_dst=None)
                else: # It's already a datetime object (or should be)
                    parsed_dt = tz_object.localize(parsed_dt.replace(tzinfo=None), is_dst=None) # Ensure naive before localizing
            except Exception as e:
                logger.error(f"Error localizing to '{effective_parser_timezone}': {e}. Falling back to UTC.", exc_info=True)
                parsed_dt_naive = parsed_dt.replace(tzinfo=None)
                if isinstance(parsed_dt_naive, datetime.date) and not isinstance(parsed_dt_naive, datetime.datetime):
                     parsed_dt_naive = datetime.datetime.combine(parsed_dt_naive, datetime.time.min)
                parsed_dt = pytz.utc.localize(parsed_dt_naive)
        return parsed_dt.isoformat()
    else:
        logger.warning(f"Could not parse datetime string: '{datetime_str}' with settings: {settings}")
        return None

# --- Google Calendar API Wrappers ---
def api_get_calendar_timezone(service):
    try:
        setting = service.settings().get(setting='timezone').execute()
        tz_value = setting.get('value')
        logger.info(f"API: Fetched calendar timezone: {tz_value}")
        return tz_value
    except Exception:
        logger.warning(f"API: Could not fetch calendar timezone setting.", exc_info=True)
        return None

def api_list_events(service, calendar_id="primary", time_min_iso=None, time_max_iso=None, max_results=50):
    logger.debug(f"API: Listing events for {calendar_id} from {time_min_iso} to {time_max_iso}")
    try:
        events_result = service.events().list(
            calendarId=calendar_id, timeMin=time_min_iso, timeMax=time_max_iso,
            maxResults=max_results, singleEvents=True, orderBy="startTime"
        ).execute()
        return events_result.get('items', [])
    except Exception as e:
        logger.error(f"API: Error listing events.", exc_info=True)
        raise

def api_create_event(service, calendar_id="primary", summary="Untitled Event", **kwargs):
    logger.debug(f"API: Creating event '{summary}' in {calendar_id} with data: {kwargs}")
    event_body = {'summary': summary}
    
    # Filter out internal keys before updating event_body
    api_kwargs = {k: v for k, v in kwargs.items() if v is not None and k not in [
        'start_datetime_iso', 'end_datetime_iso', 'start_date_iso', 'end_date_iso', 'timezone_for_api'
    ]}
    event_body.update(api_kwargs)
    
    # Handle start/end times based on provided ISO strings
    start_dt_iso = kwargs.get('start_datetime_iso')
    end_dt_iso = kwargs.get('end_datetime_iso')
    start_d_iso = kwargs.get('start_date_iso')
    end_d_iso = kwargs.get('end_date_iso')
    event_api_timezone = kwargs.get('timezone_for_api')

    if start_dt_iso and end_dt_iso: # Timed event
        event_body['start'] = {'dateTime': start_dt_iso}
        event_body['end'] = {'dateTime': end_dt_iso}
        if event_api_timezone:
            event_body['start']['timeZone'] = event_api_timezone
            event_body['end']['timeZone'] = event_api_timezone
    elif start_d_iso and end_d_iso: # All-day event
        event_body['start'] = {'date': start_d_iso}
        event_body['end'] = {'date': end_d_iso}
    else:
        raise ValueError("Event creation requires start/end for timed or all-day event.")

    logger.debug(f"API: Final event body for creation: {event_body}")
    try:
        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        logger.info(f"API: Event created successfully with ID: {created_event.get('id')}")
        return created_event
    except Exception as e:
        logger.error(f"API: Error creating event.", exc_info=True)
        raise

def api_update_event(service, calendar_id, event_id, update_body):
    if not event_id: raise ValueError("'event_id' is required for update.")
    if not update_body: raise ValueError("No fields provided for update.")
    logger.debug(f"API: Updating event {event_id} in {calendar_id} with: {update_body}")
    try:
        updated_event = service.events().patch(calendarId=calendar_id, eventId=event_id, body=update_body).execute()
        logger.info(f"API: Event {event_id} updated successfully.")
        return updated_event
    except Exception as e:
        logger.error(f"API: Error updating event {event_id}.", exc_info=True)
        raise

def api_delete_event(service, calendar_id="primary", event_id=None):
    if not event_id: raise ValueError("'event_id' is required for delete.")
    logger.debug(f"API: Deleting event {event_id} from {calendar_id}")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"API: Event {event_id} deleted.")
        return {"eventId": event_id, "status": "deleted"}
    except HttpError as e:
        if hasattr(e, 'resp') and (e.resp.status == 404 or e.resp.status == 410):
            logger.warning(f"API: Event {event_id} not found or gone during delete.")
            return {"eventId": event_id, "status": "notFoundOrGone"}
        logger.error(f"API: HttpError deleting event {event_id}.", exc_info=True)
        raise # Re-raise other HttpErrors
    except Exception as e:
        logger.error(f"API: Error deleting event {event_id}.", exc_info=True)
        raise

# --- Helper to get refresh token ---
def get_refresh_token_from_header_or_fail():
    token = request.headers.get(REFRESH_TOKEN_HEADER)
    if not token:
        # This error means the client did not send the required header.
        # A ValueError here will be caught by the endpoint and result in a 400.
        raise ValueError(f"Missing required authentication header: {REFRESH_TOKEN_HEADER}")
    return token

# --- Generic Error Handler for Endpoints ---
def handle_endpoint_errors(endpoint_name, exception_instance):
    if isinstance(exception_instance, ValueError):
        logger.warning(f"ENDPOINT {endpoint_name}: ValueError: {str(exception_instance)}", exc_info=False) # No full stack for client errors
        return jsonify({"success": False, "error": "Input or Configuration Error", "details": str(exception_instance)}), 400
    elif isinstance(exception_instance, HttpError):
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(exception_instance)}", exc_info=True)
        details = str(exception_instance)
        status_code = getattr(exception_instance.resp, 'status', 500)
        try:
            details_json = json.loads(exception_instance.content.decode('utf-8'))
            # Use a more specific message if available from Google's error structure
            if isinstance(details_json, dict) and 'error' in details_json and 'message' in details_json['error']:
                 error_message = f"Google API Error: {details_json['error']['message']}"
            else:
                 error_message = "Google API Error"
            details_to_return = details_json
        except:
            error_message = "Google API Error"
            details_to_return = str(exception_instance) # Fallback to string if not JSON
        
        if status_code == 401: # Specific handling for 401
            return jsonify({"success": False, "error": "Unauthorized by Google", "details": details_to_return}), 401
        if status_code == 403: # Specific handling for 403 - often permissions
            return jsonify({"success": False, "error": "Forbidden by Google (check permissions/scopes)", "details": details_to_return}), 403
        if status_code == 404 and "event" in endpoint_name: # If it's an event operation and Google says 404
             return jsonify({"success": False, "error": "Event not found on Google Calendar.", "details": details_to_return}), 404

        return jsonify({"success": False, "error": error_message, "details": details_to_return}), status_code
    else: # Generic Exception
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected error: {str(exception_instance)}", exc_info=True)
        return jsonify({"success": False, "error": "An unexpected server error occurred", "details": str(exception_instance)}), 500

# --- Flask Endpoints ---
@calendar_bp.route('/events/list', methods=['POST'])
def list_events_endpoint():
    endpoint_name = "/calendar/events/list"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header_or_fail()
        data = request.json if request.is_json else {}

        calendar_id = data.get('calendar_id', 'primary')
        date_natural = data.get('date_natural')
        time_min_natural = data.get('time_min_natural')
        time_max_natural = data.get('time_max_natural')
        user_timezone_for_parsing = data.get('user_timezone', 'UTC') # Default to UTC if not provided
        logger.info(f"List events: Using timezone '{user_timezone_for_parsing}' for parsing natural language dates.")

        time_min_iso, time_max_iso = None, None
        dp_settings = {'TIMEZONE': user_timezone_for_parsing, 'RETURN_AS_TIMEZONE_AWARE': True}

        if date_natural:
            parsed_date_obj = dateparser.parse(date_natural, settings=dp_settings)
            if parsed_date_obj:
                # Ensure it's timezone-aware using the parsing timezone
                if parsed_date_obj.tzinfo is None or parsed_date_obj.tzinfo.utcoffset(parsed_date_obj) is None:
                    parsed_date_obj = pytz.timezone(user_timezone_for_parsing).localize(parsed_date_obj.replace(tzinfo=None))
                time_min_iso = parsed_date_obj.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                time_max_iso = parsed_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            else:
                raise ValueError(f"Could not parse date: '{date_natural}' with timezone '{user_timezone_for_parsing}'")
        else: # Use time_min_natural and time_max_natural
            if time_min_natural:
                time_min_iso = parse_datetime_to_iso(time_min_natural, prefer_future=False, default_timezone_str=user_timezone_for_parsing)
                if not time_min_iso:
                    raise ValueError(f"Could not parse start time: '{time_min_natural}' with timezone '{user_timezone_for_parsing}'")
            else: # Default time_min to start of today in the specified parsing timezone
                now_local = datetime.datetime.now(pytz.timezone(user_timezone_for_parsing))
                time_min_iso = now_local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

            if time_max_natural:
                time_max_iso = parse_datetime_to_iso(time_max_natural, prefer_future=True, default_timezone_str=user_timezone_for_parsing)
                if not time_max_iso:
                    raise ValueError(f"Could not parse end time: '{time_max_natural}' with timezone '{user_timezone_for_parsing}'")
            else: # Default time_max to end of day of time_min_iso
                # time_min_iso is already an ISO string, possibly with timezone
                parsed_min_dt = dateparser.parse(time_min_iso) # dateparser can handle ISO strings
                if parsed_min_dt:
                    time_max_iso = parsed_min_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
                else: # Fallback if time_min_iso was somehow unparseable (shouldn't happen)
                    now_local = datetime.datetime.now(pytz.timezone(user_timezone_for_parsing))
                    time_max_iso = now_local.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
        
        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        events = api_list_events(service, calendar_id, time_min_iso, time_max_iso)
        return jsonify({"success": True, "calendar_id": calendar_id, "events": events, "count": len(events)}), 200
    except Exception as e:
        return handle_endpoint_errors(endpoint_name, e)


@calendar_bp.route('/event/create', methods=['POST'])
def create_event_endpoint():
    endpoint_name = "/calendar/event/create"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header_or_fail()
        data = request.json if request.is_json else {}
        
        summary = data.get('summary')
        if not summary:
            raise ValueError("Event 'summary' is required.")

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)

        # Timezone determination for parsing
        user_req_tz = data.get('timezone') # Optional from request
        final_parsing_tz = user_req_tz or api_get_calendar_timezone(service) or HARDCODED_FALLBACK_TIMEZONE
        logger.info(f"Create event: Using timezone '{final_parsing_tz}' for parsing natural language times.")
        
        api_event_args = {
            "description": data.get('description'),
            "location": data.get('location'),
            "recurrence_rules": data.get('recurrence_rules'),
            "attendees": [{'email': e} for e in data.get('attendees', []) if isinstance(e, str)] or None,
            # color_id will be added below
        }

        color_input = data.get('color')
        if color_input:
            color_lower = str(color_input).lower()
            if color_lower in EVENT_COLOR_MAP:
                api_event_args["color_id"] = EVENT_COLOR_MAP[color_lower]
            elif color_lower.isdigit() and 1 <= int(color_lower) <= 11:
                api_event_args["color_id"] = color_lower
            else:
                logger.warning(f"Invalid color input '{color_input}'. Using calendar default.")
                # Not raising ValueError for invalid color, just ignoring it as per schema's optional nature.

        # Times/Dates processing
        start_natural, end_natural = data.get('start_natural'), data.get('end_natural')
        start_date_natural, end_date_natural = data.get('start_date_natural'), data.get('end_date_natural')
        dp_settings = {'TIMEZONE': final_parsing_tz, 'RETURN_AS_TIMEZONE_AWARE': True} # Used for direct dateparser.parse

        if start_natural: # Timed event
            api_event_args["start_datetime_iso"] = parse_datetime_to_iso(start_natural, prefer_future=True, default_timezone_str=final_parsing_tz)
            if not api_event_args["start_datetime_iso"]:
                raise ValueError(f"Could not parse start time: '{start_natural}' with timezone '{final_parsing_tz}'")
            
            if end_natural:
                api_event_args["end_datetime_iso"] = parse_datetime_to_iso(end_natural, prefer_future=True, default_timezone_str=final_parsing_tz)
                if not api_event_args["end_datetime_iso"]:
                    raise ValueError(f"Could not parse end time: '{end_natural}' with timezone '{final_parsing_tz}'")
            else: # Default 1hr duration
                # Parse start_datetime_iso (which is ISO string) back to datetime object to add duration
                start_dt_obj = dateparser.parse(api_event_args["start_datetime_iso"]) # dateparser handles ISO strings
                if start_dt_obj:
                    api_event_args["end_datetime_iso"] = (start_dt_obj + datetime.timedelta(hours=1)).isoformat()
                else: # Should not happen if start_datetime_iso was valid
                    raise ValueError("Internal error: Could not re-parse start_datetime_iso for duration calculation.")
            api_event_args["timezone_for_api"] = final_parsing_tz # This is the timezone for the Google Calendar event
        
        elif start_date_natural: # All-day event
            # Use dp_settings which includes the final_parsing_tz for interpreting "today", "tomorrow"
            start_obj = dateparser.parse(start_date_natural, settings=dp_settings)
            if not start_obj:
                raise ValueError(f"Could not parse start date: '{start_date_natural}' with timezone '{final_parsing_tz}'")
            api_event_args["start_date_iso"] = start_obj.strftime('%Y-%m-%d')

            if end_date_natural:
                end_obj = dateparser.parse(end_date_natural, settings=dp_settings)
                if not end_obj:
                    raise ValueError(f"Could not parse end date: '{end_date_natural}' with timezone '{final_parsing_tz}'")
                api_event_args["end_date_iso"] = (end_obj.replace(hour=0,minute=0,second=0,microsecond=0) + datetime.timedelta(days=1)).strftime('%Y-%m-%d') # Inclusive end date
            else: # Single all-day event
                api_event_args["end_date_iso"] = (start_obj.replace(hour=0,minute=0,second=0,microsecond=0) + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            api_event_args["timezone_for_api"] = None # No specific timezone for all-day event body in Google API
        else:
            raise ValueError("Either 'start_natural' (for timed event) or 'start_date_natural' (for all-day event) is required.")
        
        created_event = api_create_event(service, data.get('calendar_id', 'primary'), summary, **api_event_args)
        return jsonify({"success": True, "message": "Event created successfully.", "event": created_event}), 200
    except Exception as e:
        return handle_endpoint_errors(endpoint_name, e)


@calendar_bp.route('/event/update', methods=['POST'])
def update_event_endpoint():
    endpoint_name = "/calendar/event/update"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header_or_fail()
        data = request.json if request.is_json else {}
        
        event_id = data.get('event_id')
        if not event_id:
            raise ValueError("'event_id' is required for update.")

        update_payload = {}
        # Check for presence of keys in data to decide if they should be updated
        if 'summary' in data: update_payload['summary'] = data['summary']
        if 'description' in data: update_payload['description'] = data['description'] # null is allowed
        if 'location' in data: update_payload['location'] = data['location'] # null is allowed
        
        if 'color' in data:
            color_input = data['color']
            if color_input is None or str(color_input).lower() == 'default':
                update_payload['colorId'] = None # API uses null to reset color
            else:
                color_lower = str(color_input).lower()
                if color_lower in EVENT_COLOR_MAP:
                    update_payload['colorId'] = EVENT_COLOR_MAP[color_lower]
                elif color_lower.isdigit() and 1 <= int(color_lower) <= 11:
                    update_payload['colorId'] = color_lower
                else:
                    raise ValueError(f"Invalid color value for update: '{color_input}'")
        
        if not update_payload: # No valid fields were provided for update
            raise ValueError("No updatable fields provided (e.g., summary, description, location, color).")

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        updated_event = api_update_event(service, data.get('calendar_id', 'primary'), event_id, update_payload)
        return jsonify({"success": True, "message": "Event updated successfully.", "event": updated_event}), 200
    except Exception as e:
        return handle_endpoint_errors(endpoint_name, e)


@calendar_bp.route('/event/delete', methods=['POST'])
def delete_event_endpoint():
    endpoint_name = "/calendar/event/delete"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        refresh_token = get_refresh_token_from_header_or_fail()
        data = request.json if request.is_json else {}
        
        event_id = data.get('event_id')
        if not event_id:
            raise ValueError("'event_id' is required for delete.")

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        delete_details = api_delete_event(service, data.get('calendar_id', 'primary'), event_id)
        
        # For delete, the schema expects a specific details structure for success
        return jsonify({"success": True, "message": f"Event '{event_id}' deletion processed.", "details": delete_details}), 200
    except Exception as e:
        return handle_endpoint_errors(endpoint_name, e)
