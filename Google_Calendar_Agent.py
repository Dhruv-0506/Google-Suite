from flask import jsonify, request, Blueprint, current_app
import logging
import time
import datetime
import dateparser # For parsing natural language dates/times

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Import shared helper functions
from shared_utils import get_access_token, get_global_specific_user_access_token

logger = logging.getLogger(__name__)
calendar_bp = Blueprint('calendar_agent', __name__, url_prefix='/calendar')

# --- Google Calendar Color ID Mapping ---
# Standard Google Calendar event colors. API expects colorId as a string.
# For more details: https://developers.google.com/calendar/api/v3/reference/colors
EVENT_COLOR_MAP = {
    "default": None, # Special case: use calendar's default color (achieved by not setting colorId or setting to null)
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4", # (Light red)
    "banana": "5", # (Yellow)
    "tangerine": "6", # (Orange)
    "peacock": "7", # (Blue)
    "graphite": "8", # (Gray)
    "blueberry": "9", # (Dark blue)
    "basil": "10", # (Green)
    "tomato": "11"  # (Red)
}

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
        raise

# --- Google Calendar API Wrapper Functions ---

def api_list_events(service, calendar_id="primary", time_min_iso=None, time_max_iso=None, max_results=50, single_events=True, order_by="startTime"):
    """
    Lists events on a calendar.
    time_min_iso and time_max_iso should be in RFC3339 format e.g., '2024-05-16T00:00:00Z'
    """
    logger.info(f"API: Listing events for calendar_id '{calendar_id}' between {time_min_iso} and {time_max_iso}")
    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            maxResults=max_results,
            singleEvents=single_events,
            orderBy=order_by
        ).execute()
        events = events_result.get('items', [])
        logger.info(f"Found {len(events)} events on calendar '{calendar_id}'.")
        return events
    except HttpError as e:
        error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e)
        logger.error(f"API: HttpError listing events: {error_content}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error listing events: {str(e)}", exc_info=True)
        raise


def api_create_event(service, calendar_id="primary", summary=None, description=None,
                     start_datetime_iso=None, end_datetime_iso=None,
                     start_date_iso=None, end_date_iso=None,  # For all-day events
                     attendees=None, location=None, timezone=None,
                     recurrence_rules=None, color_id=None): # <<< MODIFIED: Added color_id
    """
    Creates an event, potentially recurring, and with a specific color.
    recurrence_rules: A list of strings, e.g., ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20241231T235959Z"]
    color_id: String from "1" to "11". If None, calendar's default color is used.
    """
    logger.info(f"API: Creating event '{summary}' on calendar '{calendar_id}'" +
                (" with recurrence." if recurrence_rules else ".") +
                (f" with colorId '{color_id}'." if color_id else "."))
    event_body = {}
    if summary: event_body['summary'] = summary
    if description: event_body['description'] = description
    if location: event_body['location'] = location

    if start_datetime_iso and end_datetime_iso:
        event_body['start'] = {'dateTime': start_datetime_iso}
        event_body['end'] = {'dateTime': end_datetime_iso}
        if timezone: # Apply timezone to timed events
            event_body['start']['timeZone'] = timezone
            event_body['end']['timeZone'] = timezone
    elif start_date_iso and end_date_iso: # For all-day events
        event_body['start'] = {'date': start_date_iso}
        event_body['end'] = {'date': end_date_iso}
    else:
        raise ValueError("Either (start_datetime_iso AND end_datetime_iso for timed event) OR "
                         "(start_date_iso AND end_date_iso for all-day event) must be provided.")

    if attendees and isinstance(attendees, list):
        event_body['attendees'] = attendees

    if not event_body.get('summary'): # Google Calendar often requires a summary
        event_body['summary'] = "Untitled Event"

    if recurrence_rules and isinstance(recurrence_rules, list):
        event_body['recurrence'] = recurrence_rules
        logger.info(f"Adding recurrence rules: {recurrence_rules}")

    if color_id: # <<< ADDED: Handle color_id
        event_body['colorId'] = str(color_id) # Ensure it's a string

    try:
        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        logger.info(f"Event created: ID '{created_event.get('id')}', Summary '{created_event.get('summary')}'")
        return created_event
    except HttpError as e:
        error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e)
        logger.error(f"API: HttpError creating event: {error_content}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error creating event: {str(e)}", exc_info=True)
        raise

def api_delete_event(service, calendar_id="primary", event_id=None):
    logger.info(f"API: Deleting event_id '{event_id}' from calendar '{calendar_id}'.")
    if not event_id:
        raise ValueError("event_id is required to delete an event.")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"Event '{event_id}' deleted successfully.")
        return {"eventId": event_id, "status": "deleted"}
    except HttpError as e:
        if hasattr(e, 'resp') and (e.resp.status == 404 or e.resp.status == 410):
            logger.warning(f"API: Event '{event_id}' not found or already gone. Treating as successful deletion for idempotency.")
            return {"eventId": event_id, "status": "notFoundOrGone"}
        error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e)
        logger.error(f"API: HttpError deleting event: {error_content}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error deleting event: {str(e)}", exc_info=True)
        raise

# <<< NEW FUNCTION >>>
def api_update_event(service, calendar_id, event_id, update_body):
    """
    Updates an existing event using the patch method.
    update_body should be a dictionary containing fields to update.
    e.g., {'summary': 'New Summary', 'colorId': '5'}
    To reset a color, update_body can be {'colorId': None}
    """
    logger.info(f"API: Patching event_id '{event_id}' on calendar '{calendar_id}' with body: {update_body}")
    if not event_id:
        raise ValueError("event_id is required to update an event.")
    if not update_body:
        logger.warning("API: Update event called with empty update_body. No changes will be made by this call.")
        # Fetching event to return current state, or could raise an error.
        # For now, let the API call proceed if desired, though endpoint should prevent empty body.
        try:
            event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            return event # Return the event as is
        except HttpError as e_get:
            logger.error(f"API: HttpError fetching event during empty update: {e_get.content.decode('utf-8') if hasattr(e_get,'content') and e_get.content else str(e_get)}", exc_info=True)
            raise e_get


    try:
        # If colorId is explicitly set to None in update_body, it means reset to default.
        # The API handles `colorId: null` correctly.
        updated_event = service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=update_body,
            sendNotifications=True # Optional: whether to send notifications for the update
        ).execute()
        logger.info(f"Event '{event_id}' patched successfully. New summary: '{updated_event.get('summary')}'")
        return updated_event
    except HttpError as e:
        error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e)
        logger.error(f"API: HttpError patching event: {error_content}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"API: Generic error patching event: {str(e)}", exc_info=True)
        raise

# --- Helper function for date parsing ---
def parse_datetime_to_iso(datetime_str, prefer_future=True, default_timezone='UTC', settings_override=None):
    if not datetime_str:
        return None
    
    settings = {'PREFER_DATES_FROM': 'future' if prefer_future else 'past', 
                'RETURN_AS_TIMEZONE_AWARE': True}
    if settings_override and isinstance(settings_override, dict):
        settings.update(settings_override)
    
    if 'TIMEZONE' not in settings:
        settings['TIMEZONE'] = default_timezone
        logger.debug(f"Dateparser: Using timezone '{default_timezone}' for parsing '{datetime_str}'.")

    parsed_dt = dateparser.parse(datetime_str, settings=settings)
    if parsed_dt:
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
            logger.warning(f"Parsed datetime '{datetime_str}' as naive datetime '{parsed_dt}' despite settings. Assuming UTC.")
            parsed_dt = parsed_dt.replace(tzinfo=datetime.timezone.utc)
        return parsed_dt.isoformat()
    else:
        logger.warning(f"Could not parse datetime string: '{datetime_str}'")
        return None

# --- Flask Endpoints for Calendar ---

@calendar_bp.route('/token', methods=['GET'])
def specific_user_token_calendar_endpoint():
    endpoint_name = "/calendar/token"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        access_token = get_global_specific_user_access_token()
        logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user (Calendar context).")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": f"Failed to obtain access token: {str(e)}"}), 500

@calendar_bp.route('/events/list', methods=['POST'])
def list_events_endpoint():
    endpoint_name = "/calendar/events/list"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        
        calendar_id = data.get('calendar_id', 'primary')
        time_min_natural = data.get('time_min_natural')
        time_max_natural = data.get('time_max_natural')
        date_natural = data.get('date_natural')
        user_timezone = data.get('user_timezone', 'UTC')

        time_min_iso, time_max_iso = None, None
        dp_settings_for_range = {'TIMEZONE': user_timezone, 'RETURN_AS_TIMEZONE_AWARE': True}

        if date_natural:
            logger.info(f"Parsing date_natural for list: '{date_natural}' with timezone '{user_timezone}'")
            parsed_date_local = dateparser.parse(date_natural, settings={'TIMEZONE': user_timezone, 'PREFER_DATES_FROM': 'future'})
            if parsed_date_local:
                start_of_day_local = parsed_date_local.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day_local = parsed_date_local.replace(hour=23, minute=59, second=59, microsecond=999999)
                time_min_iso = start_of_day_local.astimezone(datetime.timezone.utc).isoformat()
                time_max_iso = end_of_day_local.astimezone(datetime.timezone.utc).isoformat()
                logger.info(f"Querying events for full day: {date_natural} (UTC: {time_min_iso} to {time_max_iso})")
            else:
                return jsonify({"success": False, "error": f"Could not understand the date: '{date_natural}'"}), 400
        else:
            if time_min_natural:
                time_min_iso = parse_datetime_to_iso(time_min_natural, prefer_future=False, settings_override=dp_settings_for_range)
                if not time_min_iso: return jsonify({"success": False, "error": f"Could not understand start time: '{time_min_natural}'"}), 400
            if time_max_natural:
                time_max_iso = parse_datetime_to_iso(time_max_natural, prefer_future=True, settings_override=dp_settings_for_range)
                if not time_max_iso: return jsonify({"success": False, "error": f"Could not understand end time: '{time_max_natural}'"}), 400
        
        if not time_min_iso:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            time_min_iso = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if not time_max_iso:
            start_dt = dateparser.parse(time_min_iso)
            if start_dt:
                 time_max_iso = start_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            else:
                 now_utc = datetime.datetime.now(datetime.timezone.utc)
                 time_max_iso = now_utc.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        events = api_list_events(service, calendar_id, time_min_iso=time_min_iso, time_max_iso=time_max_iso)
        return jsonify({"success": True, "calendar_id": calendar_id, "events": events, "count": len(events)})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to list events", "details": error_details}), status_code

@calendar_bp.route('/event/create', methods=['POST'])
def create_event_endpoint():
    endpoint_name = "/calendar/event/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        
        calendar_id = data.get('calendar_id', 'primary')
        summary = data.get('summary')
        if not summary: return jsonify({"success": False, "error": "Event 'summary' (name) is required."}), 400
        
        description = data.get('description')
        location = data.get('location')
        attendees_emails = data.get('attendees')
        event_timezone = data.get('timezone')
        recurrence_rules = data.get('recurrence_rules')
        color_input = data.get('color') # <<< ADDED: Get color input (name or ID)

        start_natural = data.get('start_natural')
        end_natural = data.get('end_natural')
        start_date_natural = data.get('start_date_natural')
        end_date_natural = data.get('end_date_natural')

        start_datetime_iso, end_datetime_iso = None, None
        start_date_iso, end_date_iso = None, None
        
        dp_settings = {'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': True}
        if event_timezone:
            dp_settings['TIMEZONE'] = event_timezone
            logger.info(f"Event creation: Using timezone '{event_timezone}' for parsing natural language dates/times.")
        else:
            dp_settings['TIMEZONE'] = 'UTC'
            logger.warning(f"No event 'timezone' provided, parsing datetimes as UTC.")

        if start_natural:
            start_datetime_iso = parse_datetime_to_iso(start_natural, settings_override=dp_settings)
            if not start_datetime_iso:
                return jsonify({"success": False, "error": f"Could not understand start time: '{start_natural}'"}), 400
            
            if end_natural:
                end_datetime_iso = parse_datetime_to_iso(end_natural, settings_override=dp_settings)
                if not end_datetime_iso:
                    return jsonify({"success": False, "error": f"Could not understand end time: '{end_natural}'"}), 400
            else:
                parsed_start_dt = dateparser.parse(start_natural, settings=dp_settings)
                if parsed_start_dt:
                    parsed_end_dt = parsed_start_dt + datetime.timedelta(hours=1)
                    end_datetime_iso = parsed_end_dt.isoformat()
                    logger.info(f"No end_natural provided, defaulting to 1 hour duration. End: {end_datetime_iso}")
                else:
                    return jsonify({"success": False, "error": "Cannot determine end time as start time was unparseable."}), 400
        
        elif start_date_natural:
            parsed_start_date = dateparser.parse(start_date_natural, settings={'PREFER_DATES_FROM': 'future'})
            if not parsed_start_date:
                return jsonify({"success": False, "error": f"Could not understand start date: '{start_date_natural}'"}), 400
            start_date_iso = parsed_start_date.strftime('%Y-%m-%d')

            if end_date_natural:
                parsed_end_date = dateparser.parse(end_date_natural, settings={'PREFER_DATES_FROM': 'future'})
                if not parsed_end_date:
                    return jsonify({"success": False, "error": f"Could not understand end date: '{end_date_natural}'"}), 400
                if parsed_end_date.hour == 0 and parsed_end_date.minute == 0 and parsed_end_date.second == 0:
                     end_date_iso = (parsed_end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                else:
                     end_date_iso = (parsed_end_date.replace(hour=0,minute=0,second=0,microsecond=0) + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                end_date_iso = (parsed_start_date.replace(hour=0,minute=0,second=0,microsecond=0) + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            event_timezone = None
        else:
            return jsonify({"success": False, "error": "Must provide natural language for (start & end times) or (start date for all-day event)."}), 400
        
        attendees_list = None
        if isinstance(attendees_emails, list):
            attendees_list = [{'email': email} for email in attendees_emails if isinstance(email, str)]

        # <<< ADDED: Process color input >>>
        color_id_for_api = None
        if color_input:
            if isinstance(color_input, str) and color_input.lower() in EVENT_COLOR_MAP:
                color_id_for_api = EVENT_COLOR_MAP[color_input.lower()]
            elif isinstance(color_input, str) and color_input.isdigit() and 1 <= int(color_input) <= 11:
                color_id_for_api = str(color_input)
            elif color_input is None and "default" in EVENT_COLOR_MAP: # Explicit "default" or null maps to None
                 color_id_for_api = EVENT_COLOR_MAP["default"]
            else:
                logger.warning(f"Invalid color input '{color_input}'. Using default calendar color. Supported: {list(EVENT_COLOR_MAP.keys())} or IDs 1-11.")
                # Optionally, return an error:
                # return jsonify({"success": False, "error": f"Invalid color: '{color_input}'. Supported colors are: {list(EVENT_COLOR_MAP.keys())} or IDs 1-11."}), 400
        # If color_id_for_api is None (e.g., for "default" or if not provided), it won't be sent to API, which is correct.

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        
        created_event = api_create_event(service, calendar_id, summary, description,
                                         start_datetime_iso, end_datetime_iso,
                                         start_date_iso, end_date_iso,
                                         attendees_list, location,
                                         event_timezone if (start_datetime_iso and end_datetime_iso) else None,
                                         recurrence_rules,
                                         color_id_for_api) # <<< MODIFIED: Pass resolved color_id
        return jsonify({"success": True, "message": "Event created successfully.", "event": created_event})
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to create event", "details": error_details}), status_code

@calendar_bp.route('/event/delete', methods=['POST'])
def delete_event_endpoint():
    endpoint_name = "/calendar/event/delete"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('event_id', 'refresh_token')): return jsonify({"success": False, "error": "Missing 'event_id' or 'refresh_token'"}), 400
        event_id = data['event_id']; refresh_token = data['refresh_token']
        calendar_id = data.get('calendar_id', 'primary')
        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        delete_status = api_delete_event(service, calendar_id, event_id)
        if delete_status.get("status") == "deleted" or delete_status.get("status") == "notFoundOrGone":
            return jsonify({"success": True, "message": f"Event '{event_id}' deletion processed.", "details": delete_status})
        else: return jsonify({"success": False, "message": "Event deletion failed or had an unknown status.", "details": delete_status}), 500
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to delete event", "details": error_details}), status_code

# <<< NEW ENDPOINT for updating events, including color >>>
@calendar_bp.route('/event/update', methods=['POST'])
def update_event_endpoint():
    endpoint_name = "/calendar/event/update"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if not all(k in data for k in ('event_id', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'event_id' or 'refresh_token'"}), 400

        event_id = data['event_id']
        refresh_token = data['refresh_token']
        calendar_id = data.get('calendar_id', 'primary')

        update_payload = {} # This will be the body for the patch request

        # --- Color update logic ---
        # Process color only if 'color' key is present in the request data
        if 'color' in data:
            color_value = data['color']
            if color_value is None or (isinstance(color_value, str) and color_value.lower() == "default"):
                # Setting colorId to None API-side resets it to the calendar's default.
                update_payload['colorId'] = None # This will be sent as null in JSON
            elif isinstance(color_value, str) and color_value.lower() in EVENT_COLOR_MAP:
                resolved_color_id = EVENT_COLOR_MAP[color_value.lower()]
                if resolved_color_id is None: # Handles case where "default" maps to None directly
                    update_payload['colorId'] = None
                else:
                    update_payload['colorId'] = str(resolved_color_id)
            elif isinstance(color_value, str) and color_value.isdigit() and 1 <= int(color_value) <= 11:
                update_payload['colorId'] = str(color_value)
            else:
                # Invalid color value provided
                valid_colors_msg = f"Supported colors: {', '.join(k for k in EVENT_COLOR_MAP.keys() if k != 'default')}, IDs 1-11, 'default', or null to reset."
                return jsonify({"success": False, "error": f"Invalid color value: '{color_value}'. {valid_colors_msg}"}), 400

        # --- Add other updatable fields here as needed ---
        if 'summary' in data:
            update_payload['summary'] = data['summary']
        if 'description' in data:
            update_payload['description'] = data['description']
        if 'location' in data:
            update_payload['location'] = data['location']
        
        # Add logic for start/end times, attendees, recurrence if they need to be updatable
        # Example for start/end (would need parsing similar to create_event_endpoint):
        # if 'start_natural' in data or 'end_natural' in data:
        #     # ... (complex parsing and ISO conversion logic) ...
        #     # update_payload['start'] = {'dateTime': start_iso, 'timeZone': event_tz}
        #     # update_payload['end'] = {'dateTime': end_iso, 'timeZone': event_tz}
        #     pass


        if not update_payload:
            return jsonify({"success": False, "error": "No valid fields provided for update. Please provide 'color', 'summary', 'description', etc."}), 400

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)

        updated_event = api_update_event(service, calendar_id, event_id, update_payload)
        return jsonify({"success": True, "message": f"Event '{event_id}' updated successfully.", "event": updated_event})

    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to update event", "details": error_details}), status_code
