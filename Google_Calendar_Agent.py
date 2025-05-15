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
    
    # Defaulting for time_min_iso and time_max_iso if not provided will be handled in the endpoint
    # to allow for natural language parsing first.
        
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
                     recurrence_rules=None): # <<< ADDED recurrence_rules
    """
    Creates an event, potentially recurring.
    recurrence_rules: A list of strings, e.g., ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20241231T235959Z"]
    """
    logger.info(f"API: Creating event '{summary}' on calendar '{calendar_id}'" + 
                (" with recurrence." if recurrence_rules else "."))
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

    if recurrence_rules and isinstance(recurrence_rules, list): # <<< ADDED THIS BLOCK
        event_body['recurrence'] = recurrence_rules
        logger.info(f"Adding recurrence rules: {recurrence_rules}")

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

# --- Helper function for date parsing ---
def parse_datetime_to_iso(datetime_str, prefer_future=True, default_timezone='UTC', settings_override=None):
    if not datetime_str:
        return None
    
    settings = {'PREFER_DATES_FROM': 'future' if prefer_future else 'past', 
                'RETURN_AS_TIMEZONE_AWARE': True}
    if settings_override and isinstance(settings_override, dict):
        settings.update(settings_override)
    
    # If no explicit timezone in settings_override, use default_timezone
    if 'TIMEZONE' not in settings:
        settings['TIMEZONE'] = default_timezone
        logger.debug(f"Dateparser: Using timezone '{default_timezone}' for parsing '{datetime_str}'.")


    parsed_dt = dateparser.parse(datetime_str, settings=settings)
    if parsed_dt:
        # Ensure it's timezone-aware; if dateparser made it naive with the given settings,
        # which can happen if TIMEZONE setting was for a local interpretation but output is naive.
        # This logic ensures it's always offset-aware, defaulting to UTC if needed.
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
            logger.warning(f"Parsed datetime '{datetime_str}' as naive datetime '{parsed_dt}' despite settings. Assuming UTC.")
            parsed_dt = parsed_dt.replace(tzinfo=datetime.timezone.utc)
        return parsed_dt.isoformat() # RFC3339 format
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
        date_natural = data.get('date_natural') # e.g., "today", "next Friday"
        user_timezone = data.get('user_timezone', 'UTC') # Client can provide their timezone

        time_min_iso, time_max_iso = None, None
        dp_settings_for_range = {'TIMEZONE': user_timezone, 'RETURN_AS_TIMEZONE_AWARE': True}

        if date_natural:
            logger.info(f"Parsing date_natural for list: '{date_natural}' with timezone '{user_timezone}'")
            # For a single date, we want the whole day in that user's timezone
            parsed_date_local = dateparser.parse(date_natural, settings={'TIMEZONE': user_timezone, 'PREFER_DATES_FROM': 'future'})
            if parsed_date_local:
                # Convert start of day and end of day in user's timezone to UTC ISO strings for Google API
                start_of_day_local = parsed_date_local.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day_local = parsed_date_local.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                time_min_iso = start_of_day_local.astimezone(datetime.timezone.utc).isoformat()
                time_max_iso = end_of_day_local.astimezone(datetime.timezone.utc).isoformat()
                logger.info(f"Querying events for full day: {date_natural} (parsed as local {start_of_day_local} to {end_of_day_local}, UTC: {time_min_iso} to {time_max_iso})")
            else:
                return jsonify({"success": False, "error": f"Could not understand the date: '{date_natural}'"}), 400
        else:
            if time_min_natural:
                time_min_iso = parse_datetime_to_iso(time_min_natural, prefer_future=False, settings_override=dp_settings_for_range)
                if not time_min_iso: return jsonify({"success": False, "error": f"Could not understand start time: '{time_min_natural}'"}), 400
            if time_max_natural:
                time_max_iso = parse_datetime_to_iso(time_max_natural, prefer_future=True, settings_override=dp_settings_for_range)
                if not time_max_iso: return jsonify({"success": False, "error": f"Could not understand end time: '{time_max_natural}'"}), 400
        
        if not time_min_iso: # Default to start of today UTC if nothing else specified
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            time_min_iso = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if not time_max_iso: # Default to end of the day of time_min_iso
            # Parse time_min_iso back to ensure it's timezone aware for correct end of day calculation
            start_dt = dateparser.parse(time_min_iso) # dateparser handles ISO strings with TZ
            if start_dt:
                 time_max_iso = start_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            else: # Fallback if time_min_iso was somehow invalid for dateparser (shouldn't happen)
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
        event_timezone = data.get('timezone') # User's preferred IANA timezone for this event

        start_natural = data.get('start_natural')
        end_natural = data.get('end_natural')    
        start_date_natural = data.get('start_date_natural') 
        end_date_natural = data.get('end_date_natural')     
        recurrence_rules = data.get('recurrence_rules') # Expect a list of RRULE strings

        start_datetime_iso, end_datetime_iso = None, None
        start_date_iso, end_date_iso = None, None
        
        # Dateparser settings. If event_timezone is provided, parse relative to it.
        dp_settings = {'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': True}
        if event_timezone:
            dp_settings['TIMEZONE'] = event_timezone
            logger.info(f"Event creation: Using timezone '{event_timezone}' for parsing natural language dates/times.")
        else:
            # If no specific timezone for the event, parse as UTC to be explicit for Google API
            dp_settings['TIMEZONE'] = 'UTC' 
            logger.warning(f"No event 'timezone' provided, parsing datetimes as UTC. Google Calendar will use calendar's default for display.")

        if start_natural: # Prioritize timed event if start_natural is given
            start_datetime_iso = parse_datetime_to_iso(start_natural, settings_override=dp_settings)
            if not start_datetime_iso:
                return jsonify({"success": False, "error": f"Could not understand start time: '{start_natural}'"}), 400
            
            if end_natural:
                end_datetime_iso = parse_datetime_to_iso(end_natural, settings_override=dp_settings)
                if not end_datetime_iso:
                    return jsonify({"success": False, "error": f"Could not understand end time: '{end_natural}'"}), 400
            else: # If no end_natural, try to infer a default duration (e.g., 1 hour)
                parsed_start_dt = dateparser.parse(start_natural, settings=dp_settings)
                if parsed_start_dt:
                    parsed_end_dt = parsed_start_dt + datetime.timedelta(hours=1)
                    end_datetime_iso = parsed_end_dt.isoformat()
                    logger.info(f"No end_natural provided, defaulting to 1 hour duration. End: {end_datetime_iso}")
                else: # Should have been caught by start_datetime_iso check
                    return jsonify({"success": False, "error": "Cannot determine end time as start time was unparseable."}), 400
        
        elif start_date_natural: # All-day event
            # For all-day events, timezone is less critical for date part, but good to be consistent
            parsed_start_date = dateparser.parse(start_date_natural, settings={'PREFER_DATES_FROM': 'future'}) # No explicit TZ here, just get the date
            if not parsed_start_date:
                return jsonify({"success": False, "error": f"Could not understand start date: '{start_date_natural}'"}), 400
            start_date_iso = parsed_start_date.strftime('%Y-%m-%d')

            if end_date_natural:
                parsed_end_date = dateparser.parse(end_date_natural, settings={'PREFER_DATES_FROM': 'future'})
                if not parsed_end_date:
                    return jsonify({"success": False, "error": f"Could not understand end date: '{end_date_natural}'"}), 400
                # Google's end date for multi-day all-day events is exclusive.
                # If user says "event on July 4th and July 5th", end_date_natural should be July 5th,
                # and API needs end date as July 6th.
                # If it's just a date (no time), add one day to make it exclusive.
                if parsed_end_date.hour == 0 and parsed_end_date.minute == 0 and parsed_end_date.second == 0:
                     end_date_iso = (parsed_end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                else: # User might have specified a time, just take the date part and make it exclusive
                     end_date_iso = (parsed_end_date.replace(hour=0,minute=0,second=0,microsecond=0) + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            else: # Single all-day event
                end_date_iso = (parsed_start_date.replace(hour=0,minute=0,second=0,microsecond=0) + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            event_timezone = None # Timezone not used for all-day events in start/end['date']
        else:
            return jsonify({"success": False, "error": "Must provide natural language for (start & end times) or (start date for all-day event)."}), 400
        
        attendees_list = None
        if isinstance(attendees_emails, list):
            attendees_list = [{'email': email} for email in attendees_emails if isinstance(email, str)]

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        
        created_event = api_create_event(service, calendar_id, summary, description,
                                         start_datetime_iso, end_datetime_iso,
                                         start_date_iso, end_date_iso,
                                         attendees_list, location, 
                                         event_timezone if (start_datetime_iso and end_datetime_iso) else None, # Pass API timezone only for timed events
                                         recurrence_rules)
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
        else: return jsonify({"success": False, "message": "Event deletion failed or had an unknown status.", "details": delete_status}), 500 # Should be caught by api_delete_event
    except Exception as e: 
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to delete event", "details": error_details}), status_code
