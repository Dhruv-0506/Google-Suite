from flask import jsonify, request, Blueprint, current_app
import logging
import time
import datetime
import dateparser # <<< IMPORT DATEPARSER

# ... (other imports like Google API client, shared_utils) ...
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from shared_utils import get_access_token, get_global_specific_user_access_token


logger = logging.getLogger(__name__)
calendar_bp = Blueprint('calendar_agent', __name__, url_prefix='/calendar')

# --- get_calendar_service and other API wrappers remain the same ---
# ... (get_calendar_service, api_list_events, api_create_event, api_delete_event) ...
# We will modify the *endpoints* that call these wrappers.

def get_calendar_service(access_token): # Copied for completeness
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

def api_list_events(service, calendar_id="primary", time_min_iso=None, time_max_iso=None, max_results=50, single_events=True, order_by="startTime"):
    logger.info(f"API: Listing events for calendar_id '{calendar_id}' between {time_min_iso} and {time_max_iso}")
    # Defaulting logic for time_min_iso and time_max_iso can remain or be enhanced by NLP in the endpoint
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
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError listing events: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error listing events: {str(e)}", exc_info=True); raise

def api_create_event(service, calendar_id="primary", summary=None, description=None, 
                     start_datetime_iso=None, end_datetime_iso=None, start_date_iso=None, end_date_iso=None, 
                     attendees=None, location=None, timezone=None):
    logger.info(f"API: Creating event '{summary}' on calendar '{calendar_id}'.")
    event_body = {} # Renamed to avoid conflict with 'event' module if ever imported
    if summary: event_body['summary'] = summary
    if description: event_body['description'] = description
    if location: event_body['location'] = location

    if start_datetime_iso and end_datetime_iso:
        event_body['start'] = {'dateTime': start_datetime_iso}
        event_body['end'] = {'dateTime': end_datetime_iso}
        if timezone:
            event_body['start']['timeZone'] = timezone
            event_body['end']['timeZone'] = timezone
    elif start_date_iso and end_date_iso:
        event_body['start'] = {'date': start_date_iso}
        event_body['end'] = {'date': end_date_iso}
    else:
        raise ValueError("Either (start_datetime_iso AND end_datetime_iso) OR (start_date_iso AND end_date_iso) must be provided for event creation.")

    if attendees: event_body['attendees'] = attendees
    if not event_body.get('summary'): event_body['summary'] = "Untitled Event"

    try:
        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        logger.info(f"Event created: ID '{created_event.get('id')}', Summary '{created_event.get('summary')}'")
        return created_event
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating event: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error creating event: {str(e)}", exc_info=True); raise

def api_delete_event(service, calendar_id="primary", event_id=None): # ... (same as before)
    logger.info(f"API: Deleting event_id '{event_id}' from calendar '{calendar_id}'.")
    if not event_id: raise ValueError("event_id is required to delete an event.")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"Event '{event_id}' deleted successfully.")
        return {"eventId": event_id, "status": "deleted"}
    except HttpError as e:
        if hasattr(e, 'resp') and (e.resp.status == 404 or e.resp.status == 410):
            logger.warning(f"API: Event '{event_id}' not found or already gone. Treating as successful deletion.")
            return {"eventId": event_id, "status": "notFoundOrGone"}
        error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e)
        logger.error(f"API: HttpError deleting event: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error deleting event: {str(e)}", exc_info=True); raise

# --- Helper function for date parsing ---
def parse_datetime_to_iso(datetime_str, setting=None, prefer_future=True):
    """
    Parses a natural language datetime string to an ISO 8601 string.
    `setting` can be a dictionary for dateparser settings, e.g., {'TIMEZONE': 'UTC'}
    `prefer_future` tells dateparser to prefer future dates for ambiguous inputs like "Friday".
    """
    if not datetime_str:
        return None
    
    settings = {'PREFER_DATES_FROM': 'future' if prefer_future else 'past'}
    if isinstance(setting, dict):
        settings.update(setting)
    
    parsed_dt = dateparser.parse(datetime_str, settings=settings)
    if parsed_dt:
        # If timezone info is missing, dateparser might make it naive.
        # Google Calendar API prefers timezone-aware datetimes in RFC3339.
        # For simplicity, we'll convert to UTC if no timezone was parsed.
        # A more advanced version could try to infer user's timezone or use calendar's timezone.
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
            logger.warning(f"Parsed datetime '{datetime_str}' as naive datetime '{parsed_dt}'. Assuming UTC.")
            parsed_dt = parsed_dt.replace(tzinfo=datetime.timezone.utc)
        return parsed_dt.isoformat()
    else:
        logger.warning(f"Could not parse datetime string: '{datetime_str}'")
        return None

# --- Flask Endpoints for Calendar (Modified for NLP) ---

@calendar_bp.route('/token', methods=['GET']) # Remains the same
def specific_user_token_calendar_endpoint():
    # ... (same as before)
    endpoint_name = "/calendar/token"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        access_token = get_global_specific_user_access_token(); logger.info(f"ENDPOINT {endpoint_name}: Successfully obtained access token for specific user (Calendar context).")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Failed to get specific user access token: {str(e)}", exc_info=True); return jsonify({"success": False, "error": f"Failed to obtain access token: {str(e)}"}), 500


@calendar_bp.route('/events/list', methods=['POST'])
def list_events_endpoint():
    endpoint_name = "/calendar/events/list"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        
        calendar_id = data.get('calendar_id', 'primary')
        
        # Natural language date/time for range
        time_min_str = data.get('time_min_natural') # e.g., "today", "tomorrow morning"
        time_max_str = data.get('time_max_natural') # e.g., "end of today", "tomorrow evening"
        date_for_events_str = data.get('date_natural') # e.g., "today", "next Friday"

        # Dateparser settings - you might want to configure based on user's locale or calendar timezone
        # For now, assume UTC or let dateparser infer.
        dp_settings = {'TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True}


        time_min_iso, time_max_iso = None, None

        if date_for_events_str:
            logger.info(f"Parsing date_natural: '{date_for_events_str}'")
            parsed_date = dateparser.parse(date_for_events_str, settings=dp_settings)
            if parsed_date:
                time_min_iso = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                time_max_iso = parsed_date.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
                logger.info(f"Querying events for full day: {date_for_events_str} (Parsed as: {time_min_iso} to {time_max_iso})")
            else:
                return jsonify({"success": False, "error": f"Could not understand the date: '{date_for_events_str}'"}), 400
        else:
            if time_min_str:
                time_min_iso = parse_datetime_to_iso(time_min_str, setting=dp_settings, prefer_future=False) # For start, don't always prefer future
                if not time_min_iso: return jsonify({"success": False, "error": f"Could not understand start time: '{time_min_str}'"}), 400
            if time_max_str:
                time_max_iso = parse_datetime_to_iso(time_max_str, setting=dp_settings, prefer_future=True)
                if not time_max_iso: return jsonify({"success": False, "error": f"Could not understand end time: '{time_max_str}'"}), 400
        
        # If still no time_min_iso, default to start of today (as per api_list_events)
        if not time_min_iso:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            time_min_iso = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            logger.info(f"Defaulting time_min_iso to start of today UTC: {time_min_iso}")

        # If no time_max_iso after parsing, default to end of day of time_min_iso (if time_min_iso is set)
        if not time_max_iso and time_min_iso:
            start_dt = datetime.datetime.fromisoformat(time_min_iso.replace('Z', '+00:00')) # Ensure timezone aware
            time_max_iso = start_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            logger.info(f"Defaulting time_max_iso to end of day of time_min_iso: {time_max_iso}")


        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        events = api_list_events(service, calendar_id, time_min_iso=time_min_iso, time_max_iso=time_max_iso)
        return jsonify({"success": True, "calendar_id": calendar_id, "events": events, "count": len(events)})
    # ... (rest of error handling remains the same) ...
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True); status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500; error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e); return jsonify({"success": False, "error": "Failed to list events", "details": error_details}), status_code


@calendar_bp.route('/event/create', methods=['POST'])
def create_event_endpoint():
    endpoint_name = "/calendar/event/create"; logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        data = request.json
        if 'refresh_token' not in data: return jsonify({"success": False, "error": "Missing 'refresh_token'"}), 400
        refresh_token = data['refresh_token']
        
        calendar_id = data.get('calendar_id', 'primary')
        summary = data.get('summary') # Event title/name
        if not summary: return jsonify({"success": False, "error": "Event 'summary' (name) is required."}), 400
        
        description = data.get('description')
        location = data.get('location')
        attendees_emails = data.get('attendees') 
        timezone = data.get('timezone') # e.g., "America/New_York" (IANA Time Zone)

        # Natural language date/time inputs
        start_natural = data.get('start_natural') # e.g., "tomorrow 3pm", "next Monday at 10 AM"
        end_natural = data.get('end_natural')     # e.g., "tomorrow 4pm", "next Monday at 11 AM"
        # For all-day events
        start_date_natural = data.get('start_date_natural') # e.g., "tomorrow", "July 4th"
        end_date_natural = data.get('end_date_natural')     # e.g., "day after tomorrow", "July 5th" (exclusive end for multi-day)

        start_datetime_iso, end_datetime_iso = None, None
        start_date_iso, end_date_iso = None, None
        
        # Dateparser settings:
        # PREFER_DATES_FROM: 'future' or 'past'. For start times, 'future' is usually good.
        # For end times, it depends. If duration is implied, 'future' from start time is good.
        # TIMEZONE: User's local timezone is ideal if known, otherwise UTC or calendar's default.
        # For simplicity, let's use UTC for parsing if no timezone given,
        # but Google Calendar API will use the calendar's default timezone for display if not specified in ISO string.
        # Or, better, use the 'timezone' parameter provided by the user for the event.
        dp_settings = {'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': True}
        if timezone:
            dp_settings['TIMEZONE'] = timezone # Parse relative to this timezone
        else:
            # If no timezone given for the event, parse as if it's local, then convert to UTC for Google
            # Or, let Google handle it based on the calendar's default timezone by not including TZ offset in ISO string
            # For RFC3339, it's best to be explicit.
            # Let's default to parsing as UTC if no event timezone specified by user.
            # A better approach would be to get user's primary calendar timezone.
            dp_settings['TIMEZONE'] = 'UTC'
            logger.warning(f"No event timezone provided, parsing datetimes assuming UTC. Specify 'timezone' for accuracy.")


        if start_natural and end_natural:
            start_datetime_iso = parse_datetime_to_iso(start_natural, setting=dp_settings)
            end_datetime_iso = parse_datetime_to_iso(end_natural, setting=dp_settings)
            if not start_datetime_iso or not end_datetime_iso:
                return jsonify({"success": False, "error": "Could not understand start or end time. Please be more specific (e.g., 'tomorrow 3pm', 'next Monday 10 AM')."}), 400
        elif start_date_natural: # All-day event
            parsed_start_date = dateparser.parse(start_date_natural, settings={'PREFER_DATES_FROM': 'future'})
            if not parsed_start_date:
                return jsonify({"success": False, "error": f"Could not understand start date: '{start_date_natural}'"}), 400
            start_date_iso = parsed_start_date.strftime('%Y-%m-%d')

            if end_date_natural:
                parsed_end_date = dateparser.parse(end_date_natural, settings={'PREFER_DATES_FROM': 'future'})
                if not parsed_end_date:
                    return jsonify({"success": False, "error": f"Could not understand end date: '{end_date_natural}'"}), 400
                # For all-day events, Google's end date is exclusive.
                # If user says "event on July 4th", start_date is July 4, end_date should be July 5.
                # If dateparser gives just the date, add one day.
                if parsed_end_date.hour == 0 and parsed_end_date.minute == 0: # Likely just a date
                     end_date_iso = (parsed_end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                else: # User might have specified an end time, treat as specific end
                     end_date_iso = parsed_end_date.strftime('%Y-%m-%d')

            else: # Single all-day event
                end_date_iso = (parsed_start_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            return jsonify({"success": False, "error": "Must provide either (start_natural & end_natural for timed event) or (start_date_natural for all-day event)."}), 400
        
        attendees_list = None
        if isinstance(attendees_emails, list):
            attendees_list = [{'email': email} for email in attendees_emails if isinstance(email, str)]

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        
        created_event = api_create_event(service, calendar_id, summary, description,
                                         start_datetime_iso, end_datetime_iso,
                                         start_date_iso, end_date_iso,
                                         attendees_list, location, timezone if (start_datetime_iso and end_datetime_iso) else None) # Only pass timezone for timed events
        return jsonify({"success": True, "message": "Event created successfully.", "event": created_event})
    # ... (rest of error handling remains the same) ...
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True); status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500; error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e); return jsonify({"success": False, "error": "Failed to create event", "details": error_details}), status_code


@calendar_bp.route('/event/delete', methods=['POST']) # Remains largely the same
def delete_event_endpoint():
    # ... (same as before, as it takes a specific event_id) ...
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
    except Exception as e: logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True); status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500; error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e); return jsonify({"success": False, "error": "Failed to delete event", "details": error_details}), status_code
