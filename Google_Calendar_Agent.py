from flask import jsonify, request, Blueprint, current_app
import logging
import datetime
import dateparser # For parsing natural language dates/times
import pytz # For robust timezone handling

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Import shared helper functions (assuming these exist and work correctly)
# Ensure these functions handle potential errors like missing CLIENT_ID/SECRET gracefully.
from shared_utils import get_access_token, get_global_specific_user_access_token

logger = logging.getLogger(__name__)
calendar_bp = Blueprint('calendar_agent', __name__, url_prefix='/calendar')

# --- Google Calendar Color ID Mapping ---
EVENT_COLOR_MAP = {
    "default": None,
    "lavender": "1", "sage": "2", "grape": "3", "flamingo": "4",
    "banana": "5", "tangerine": "6", "peacock": "7", "graphite": "8",
    "blueberry": "9", "basil": "10", "tomato": "11"
}
HARDCODED_FALLBACK_TIMEZONE = 'Asia/Dubai' # Or 'UTC' - Used if user provides no TZ and calendar's default can't be fetched.

# --- Google API Service Helper ---
def get_calendar_service(access_token):
    logger.info("Building Google Calendar API service object...")
    if not access_token:
        logger.error("Cannot build calendar service: access_token is missing.")
        raise ValueError("Access token is required to build calendar service.")
    try:
        creds = OAuthCredentials(token=access_token)
        service = build("calendar", "v3", credentials=creds, static_discovery=False)
        logger.info("Google Calendar API service object built successfully.")
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Calendar API service object: {str(e)}", exc_info=True)
        raise # Re-raise to be handled by endpoint

# --- Date Parsing Helper ---
def parse_datetime_to_iso(datetime_str, prefer_future=True, default_timezone_str=HARDCODED_FALLBACK_TIMEZONE, settings_override=None):
    if not datetime_str:
        return None
    settings = {
        'PREFER_DATES_FROM': 'future' if prefer_future else 'past',
        'RETURN_AS_TIMEZONE_AWARE': True
    }
    if settings_override and isinstance(settings_override, dict):
        settings.update(settings_override)

    effective_parser_timezone = settings.get('TIMEZONE', default_timezone_str)
    settings['TIMEZONE'] = effective_parser_timezone

    logger.debug(f"Dateparser: Using TIMEZONE '{effective_parser_timezone}' for parsing '{datetime_str}'.")
    parsed_dt = dateparser.parse(datetime_str, settings=settings)

    if parsed_dt:
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
            logger.warning(f"Parsed datetime '{datetime_str}' as naive. Localizing with '{effective_parser_timezone}'.")
            try:
                tz_object = pytz.timezone(effective_parser_timezone)
                parsed_dt = tz_object.localize(parsed_dt, is_dst=None)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.error(f"UnknownTimeZoneError: '{effective_parser_timezone}'. Falling back to UTC.")
                parsed_dt = pytz.utc.localize(parsed_dt.replace(tzinfo=None)) # Localize to UTC
            except Exception as e:
                logger.error(f"Error localizing to '{effective_parser_timezone}': {e}. Falling back to UTC.")
                parsed_dt = pytz.utc.localize(parsed_dt.replace(tzinfo=None)) # Localize to UTC
        return parsed_dt.isoformat()
    else:
        logger.warning(f"Could not parse datetime string: '{datetime_str}' with settings: {settings}")
        return None

# --- Google Calendar API Wrappers ---
def api_get_calendar_timezone(service):
    try:
        setting = service.settings().get(setting='timezone').execute()
        calendar_timezone = setting.get('value')
        logger.info(f"API: Fetched calendar timezone setting: {calendar_timezone}")
        return calendar_timezone
    except HttpError as e:
        error_content = e.content.decode('utf-8') if hasattr(e, 'content') and e.content else str(e)
        logger.error(f"API: HttpError fetching calendar timezone: {error_content}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"API: Generic error fetching calendar timezone: {str(e)}", exc_info=True)
        return None

def api_list_events(service, calendar_id="primary", time_min_iso=None, time_max_iso=None, max_results=50, single_events=True, order_by="startTime"):
    logger.info(f"API: Listing events for calendar_id '{calendar_id}' from {time_min_iso} to {time_max_iso}")
    try:
        events_result = service.events().list(
            calendarId=calendar_id, timeMin=time_min_iso, timeMax=time_max_iso,
            maxResults=max_results, singleEvents=single_events, orderBy=order_by
        ).execute()
        return events_result.get('items', [])
    except HttpError as e:
        logger.error(f"API: HttpError listing events.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error listing events.", exc_info=True)
        raise

def api_create_event(service, calendar_id="primary", summary=None, description=None,
                     start_datetime_iso=None, end_datetime_iso=None,
                     start_date_iso=None, end_date_iso=None,
                     attendees=None, location=None, timezone=None, # Timezone for the event itself
                     recurrence_rules=None, color_id=None):
    logger.info(f"API: Creating event '{summary}' on calendar '{calendar_id}' with timezone '{timezone}'.")
    event_body = {'summary': summary or "Untitled Event"} # Summary is required by schema
    if description: event_body['description'] = description
    if location: event_body['location'] = location

    if start_datetime_iso and end_datetime_iso: # Timed event
        event_body['start'] = {'dateTime': start_datetime_iso}
        event_body['end'] = {'dateTime': end_datetime_iso}
        if timezone: # Apply timezone to the event's start/end times
            event_body['start']['timeZone'] = timezone
            event_body['end']['timeZone'] = timezone
    elif start_date_iso and end_date_iso: # All-day event
        event_body['start'] = {'date': start_date_iso}
        event_body['end'] = {'date': end_date_iso}
        # No timezone field for all-day events in Google API
    else:
        raise ValueError("Event creation requires either (start/end datetimes for timed event) or (start/end dates for all-day event).")

    if attendees and isinstance(attendees, list):
        event_body['attendees'] = attendees # Assuming attendees is already a list of {'email': '...'}
    if recurrence_rules and isinstance(recurrence_rules, list):
        event_body['recurrence'] = recurrence_rules
    if color_id:
        event_body['colorId'] = str(color_id)

    try:
        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        logger.info(f"Event created: ID '{created_event.get('id')}'")
        return created_event
    except HttpError as e:
        logger.error(f"API: HttpError creating event.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error creating event.", exc_info=True)
        raise

def api_update_event(service, calendar_id, event_id, update_body):
    logger.info(f"API: Patching event_id '{event_id}' on calendar '{calendar_id}' with: {update_body}")
    if not event_id: raise ValueError("event_id is required to update an event.")
    if not update_body: # No fields to update
        logger.warning("API: Update event called with empty update_body.")
        # Fetch current event to return something meaningful, or raise error.
        # Per schema, we should probably error if no updatable fields are provided.
        # This part might need adjustment based on how strictly schema wants "at least one updatable field".
        try:
            return service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as e_get:
             logger.error(f"API: HttpError fetching event during empty update attempt.", exc_info=True)
             raise e_get

    try:
        updated_event = service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=update_body, sendNotifications=True
        ).execute()
        logger.info(f"Event '{event_id}' patched successfully.")
        return updated_event
    except HttpError as e:
        logger.error(f"API: HttpError patching event.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error patching event.", exc_info=True)
        raise

def api_delete_event(service, calendar_id="primary", event_id=None):
    logger.info(f"API: Deleting event_id '{event_id}' from calendar '{calendar_id}'.")
    if not event_id: raise ValueError("event_id is required to delete an event.")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"Event '{event_id}' deleted successfully.")
        return {"eventId": event_id, "status": "deleted"}
    except HttpError as e:
        if hasattr(e, 'resp') and (e.resp.status == 404 or e.resp.status == 410): # Not Found or Gone
            logger.warning(f"API: Event '{event_id}' not found or already gone. Treating as successful deletion.")
            return {"eventId": event_id, "status": "notFoundOrGone"}
        logger.error(f"API: HttpError deleting event.", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error deleting event.", exc_info=True)
        raise

# --- Flask Endpoints ---
@calendar_bp.route('/events/list', methods=['POST'])
def list_events_endpoint():
    endpoint_name = "/calendar/events/list"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data:
            return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']

        calendar_id = data.get('calendar_id', 'primary')
        date_natural = data.get('date_natural')
        time_min_natural = data.get('time_min_natural')
        time_max_natural = data.get('time_max_natural')
        # Per schema, user_timezone defaults to UTC for parsing if not provided.
        user_timezone_for_parsing = data.get('user_timezone', 'UTC') 
        logger.info(f"Using timezone '{user_timezone_for_parsing}' for parsing list event dates.")

        time_min_iso, time_max_iso = None, None

        # Dateparser settings for this endpoint
        dp_settings_override = {'TIMEZONE': user_timezone_for_parsing, 'RETURN_AS_TIMEZONE_AWARE': True}

        if date_natural:
            logger.info(f"Parsing date_natural for list: '{date_natural}'")
            # For a full day, parse it then create start/end of day in that timezone
            parsed_date_obj = dateparser.parse(date_natural, settings=dp_settings_override)
            if parsed_date_obj:
                if parsed_date_obj.tzinfo is None: # Ensure it's aware
                     parsed_date_obj = pytz.timezone(user_timezone_for_parsing).localize(parsed_date_obj.replace(tzinfo=None))
                
                start_of_day_local = parsed_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day_local = parsed_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
                # Google API expects timeMin/timeMax for list often in UTC, but if they are full ISO with TZ, it works.
                # Let's keep them in their parsed timezone.
                time_min_iso = start_of_day_local.isoformat()
                time_max_iso = end_of_day_local.isoformat()
                logger.info(f"Querying events for day '{date_natural}': {time_min_iso} to {time_max_iso}")
            else:
                return jsonify({"success": False, "error": f"Could not understand date: '{date_natural}'"}), 400
        else: # Use time_min_natural and time_max_natural
            if time_min_natural:
                time_min_iso = parse_datetime_to_iso(time_min_natural, prefer_future=False, default_timezone_str=user_timezone_for_parsing)
                if not time_min_iso:
                    return jsonify({"success": False, "error": f"Could not understand start time: '{time_min_natural}'"}), 400
            else: # Default time_min to start of today in user_timezone_for_parsing
                now_local = datetime.datetime.now(pytz.timezone(user_timezone_for_parsing))
                time_min_iso = now_local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

            if time_max_natural:
                time_max_iso = parse_datetime_to_iso(time_max_natural, prefer_future=True, default_timezone_str=user_timezone_for_parsing)
                if not time_max_iso:
                    return jsonify({"success": False, "error": f"Could not understand end time: '{time_max_natural}'"}), 400
            else: # Default time_max to end of day of time_min_iso
                parsed_min_dt = dateparser.parse(time_min_iso) # time_min_iso is already an ISO string
                if parsed_min_dt:
                    time_max_iso = parsed_min_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
                else: # Should not happen if time_min_iso was valid
                    now_local = datetime.datetime.now(pytz.timezone(user_timezone_for_parsing))
                    time_max_iso = now_local.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
        
        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        events = api_list_events(service, calendar_id, time_min_iso=time_min_iso, time_max_iso=time_max_iso)
        
        return jsonify({"success": True, "calendar_id": calendar_id, "events": events, "count": len(events)})

    except ValueError as ve:
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Configuration or input error", "details": str(ve)}), 400
    except HttpError as he:
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        error_details = he.content.decode('utf-8') if hasattr(he, 'content') and he.content else str(he)
        return jsonify({"success": False, "error": "Google API error", "details": error_details}), he.resp.status if hasattr(he, 'resp') else 500
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected Exception: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to list events", "details": str(e)}), 500


@calendar_bp.route('/event/create', methods=['POST'])
def create_event_endpoint():
    endpoint_name = "/calendar/event/create"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data:
            return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']

        if 'summary' not in data or not data['summary']: # Summary is required
            return jsonify({"success": False, "error": "Event 'summary' is required."}), 400
        summary = data['summary']

        # Timezone determination logic
        user_provided_req_timezone = data.get('timezone') # Optional from user request
        final_timezone_for_parsing = None # This will be used by dateparser
        final_timezone_for_api_event = None # This will be sent to Google API for timed events

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)

        if user_provided_req_timezone:
            logger.info(f"Using user-provided timezone for event parsing: '{user_provided_req_timezone}'")
            final_timezone_for_parsing = user_provided_req_timezone
        else:
            logger.info("No timezone in request. Attempting to fetch calendar's default timezone.")
            calendar_default_timezone = api_get_calendar_timezone(service)
            if calendar_default_timezone:
                logger.info(f"Using calendar's default timezone for parsing: '{calendar_default_timezone}'")
                final_timezone_for_parsing = calendar_default_timezone
            else:
                logger.warning(f"Could not fetch calendar's default. Falling back to '{HARDCODED_FALLBACK_TIMEZONE}' for parsing.")
                final_timezone_for_parsing = HARDCODED_FALLBACK_TIMEZONE
        
        # Common event details
        calendar_id = data.get('calendar_id', 'primary')
        description = data.get('description')
        location = data.get('location')
        attendees_emails = data.get('attendees') # List of email strings
        recurrence_rules = data.get('recurrence_rules')
        color_input = data.get('color')

        start_natural = data.get('start_natural')
        end_natural = data.get('end_natural')
        start_date_natural = data.get('start_date_natural')
        end_date_natural = data.get('end_date_natural')

        start_datetime_iso, end_datetime_iso = None, None
        start_date_iso, end_date_iso = None, None

        # Dateparser settings override for this specific parsing context
        dp_settings_override = {'TIMEZONE': final_timezone_for_parsing, 'RETURN_AS_TIMEZONE_AWARE': True}

        if start_natural: # Timed event
            start_datetime_iso = parse_datetime_to_iso(start_natural, default_timezone_str=final_timezone_for_parsing)
            if not start_datetime_iso:
                return jsonify({"success": False, "error": f"Could not understand start time: '{start_natural}' with timezone '{final_timezone_for_parsing}'"}), 400

            if end_natural:
                end_datetime_iso = parse_datetime_to_iso(end_natural, default_timezone_str=final_timezone_for_parsing)
                if not end_datetime_iso:
                    return jsonify({"success": False, "error": f"Could not understand end time: '{end_natural}' with timezone '{final_timezone_for_parsing}'"}), 400
            else: # Default to 1 hour duration
                temp_start_dt = dateparser.parse(start_natural, settings=dp_settings_override)
                if temp_start_dt:
                    if temp_start_dt.tzinfo is None: # Ensure aware
                        temp_start_dt = pytz.timezone(final_timezone_for_parsing).localize(temp_start_dt.replace(tzinfo=None))
                    parsed_end_dt = temp_start_dt + datetime.timedelta(hours=1)
                    end_datetime_iso = parsed_end_dt.isoformat()
                    logger.info(f"Defaulted event duration to 1 hour. End: {end_datetime_iso}")
                else:
                    return jsonify({"success": False, "error": "Cannot determine default end time as start time was unparseable."}), 400
            final_timezone_for_api_event = final_timezone_for_parsing # Use this TZ for Google API

        elif start_date_natural: # All-day event
            parsed_start_obj = dateparser.parse(start_date_natural, settings=dp_settings_override)
            if not parsed_start_obj:
                return jsonify({"success": False, "error": f"Could not understand start date: '{start_date_natural}' with TZ '{final_timezone_for_parsing}'"}), 400
            start_date_iso = parsed_start_obj.strftime('%Y-%m-%d')

            if end_date_natural: # Multi-day all-day event
                parsed_end_obj = dateparser.parse(end_date_natural, settings=dp_settings_override)
                if not parsed_end_obj:
                    return jsonify({"success": False, "error": f"Could not understand end date: '{end_date_natural}' with TZ '{final_timezone_for_parsing}'"}), 400
                # Google's end date for all-day is exclusive, so add 1 day if user means inclusive
                end_date_iso = (parsed_end_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            else: # Single all-day event
                end_date_iso = (parsed_start_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            final_timezone_for_api_event = None # No timezone for Google API for all-day events
        else:
            return jsonify({"success": False, "error": "Must provide natural language for (start & end times for timed event) OR (start date for all-day event)."}), 400

        attendees_list_for_api = None
        if isinstance(attendees_emails, list):
            attendees_list_for_api = [{'email': email} for email in attendees_emails if isinstance(email, str)]

        color_id_for_api = None
        if color_input:
            color_input_lower = str(color_input).lower()
            if color_input_lower in EVENT_COLOR_MAP:
                color_id_for_api = EVENT_COLOR_MAP[color_input_lower]
            elif color_input_lower.isdigit() and 1 <= int(color_input_lower) <= 11:
                color_id_for_api = color_input_lower
            else:
                logger.warning(f"Invalid color input '{color_input}'. Using calendar default.")
                # Optionally return 400 error for invalid color

        created_event = api_create_event(
            service, calendar_id, summary, description,
            start_datetime_iso, end_datetime_iso,
            start_date_iso, end_date_iso,
            attendees_list_for_api, location,
            final_timezone_for_api_event, # Pass the resolved timezone
            recurrence_rules, color_id_for_api
        )
        return jsonify({"success": True, "message": "Event created successfully.", "event": created_event}), 200

    except ValueError as ve:
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Configuration or input error", "details": str(ve)}), 400
    except HttpError as he:
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        error_details = he.content.decode('utf-8') if hasattr(he, 'content') and he.content else str(he)
        return jsonify({"success": False, "error": "Google API error", "details": error_details}), he.resp.status if hasattr(he, 'resp') else 500
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected Exception: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to create event", "details": str(e)}), 500


@calendar_bp.route('/event/update', methods=['POST'])
def update_event_endpoint():
    endpoint_name = "/calendar/event/update"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('event_id', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'event_id' or 'refresh_token'"}), 400

        event_id = data['event_id']
        refresh_token = data['refresh_token']
        calendar_id = data.get('calendar_id', 'primary')

        update_payload = {}
        has_updates = False # Flag to check if any updatable field was provided

        # Process color
        if 'color' in data:
            has_updates = True
            color_value = data['color']
            if color_value is None or (isinstance(color_value, str) and color_value.lower() == "default"):
                update_payload['colorId'] = None # Reset to calendar default
            else:
                color_value_str = str(color_value).lower()
                if color_value_str in EVENT_COLOR_MAP:
                    update_payload['colorId'] = EVENT_COLOR_MAP[color_value_str]
                elif color_value_str.isdigit() and 1 <= int(color_value_str) <= 11:
                    update_payload['colorId'] = color_value_str
                else:
                    return jsonify({"success": False, "error": f"Invalid color value: '{color_value}'"}), 400
        
        # Process other updatable fields
        if 'summary' in data:
            has_updates = True
            update_payload['summary'] = data['summary'] # Empty string can be a valid summary
        if 'description' in data: # Allows null or empty string to clear
            has_updates = True
            update_payload['description'] = data['description']
        if 'location' in data: # Allows null or empty string to clear
            has_updates = True
            update_payload['location'] = data['location']
        
        # Note: Updating times, timezone, attendees, recurrence would require more complex logic here,
        # similar to create_event_endpoint, including parsing natural language and determining effective timezone.
        # The current schema and implementation for update are simplified.

        if not has_updates:
            return jsonify({"success": False, "error": "No updatable fields provided (e.g., summary, description, location, color)."}), 400

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        updated_event = api_update_event(service, calendar_id, event_id, update_payload)
        
        return jsonify({"success": True, "message": f"Event '{event_id}' updated successfully.", "event": updated_event}), 200

    except ValueError as ve:
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Configuration or input error", "details": str(ve)}), 400
    except HttpError as he:
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        status_code = he.resp.status if hasattr(he, 'resp') else 500
        error_details = {"message": f"Google API error: {str(he)}"}
        if hasattr(he, 'content') and he.content:
            try:
                error_details['google_error'] = json.loads(he.content.decode('utf-8'))
            except json.JSONDecodeError:
                error_details['google_raw_error'] = he.content.decode('utf-8')

        if status_code == 404: # Event not found
             return jsonify({"success": False, "error": "Event not found.", "details": error_details}), 404
        return jsonify({"success": False, "error": "Google API error during update.", "details": error_details}), status_code
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected Exception: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to update event", "details": str(e)}), 500


@calendar_bp.route('/event/delete', methods=['POST'])
def delete_event_endpoint():
    endpoint_name = "/calendar/event/delete"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('event_id', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'event_id' or 'refresh_token'"}), 400
        
        event_id = data['event_id']
        refresh_token = data['refresh_token']
        calendar_id = data.get('calendar_id', 'primary')

        access_token = get_access_token(refresh_token, current_app.config.get('CLIENT_ID'), current_app.config.get('CLIENT_SECRET'))
        service = get_calendar_service(access_token)
        delete_status_details = api_delete_event(service, calendar_id, event_id)

        # Your schema expects specific 'details' structure for success
        return jsonify({"success": True, "message": f"Event '{event_id}' deletion processed.", "details": delete_status_details}), 200

    except ValueError as ve: # Catches missing event_id from api_delete_event
        logger.error(f"ENDPOINT {endpoint_name}: ValueError: {str(ve)}", exc_info=True)
        return jsonify({"success": False, "error": "Input error", "details": str(ve)}), 400
    except HttpError as he: # This block might be less likely if api_delete_event handles 404/410
        logger.error(f"ENDPOINT {endpoint_name}: HttpError: {str(he)}", exc_info=True)
        error_details = he.content.decode('utf-8') if hasattr(he, 'content') and he.content else str(he)
        status_code = he.resp.status if hasattr(he, 'resp') else 500
        if status_code == 404: # Should be caught by api_delete_event, but as a safeguard
            return jsonify({"success": True, "message": "Event not found or already gone.", "details": {"eventId": event_id, "status": "notFoundOrGone"}}), 200
        return jsonify({"success": False, "error": "Google API error during delete", "details": error_details}), status_code
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Unexpected Exception: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to delete event", "details": str(e)}), 500

# Note: /auth/google and /auth/callback endpoints are assumed to be in your main app or another blueprint.
# The /calendar/token endpoint is also omitted as it wasn't in your provided schema for paths.
# If you need them, they can be added back.
