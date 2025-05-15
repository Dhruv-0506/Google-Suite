from flask import jsonify, request, Blueprint, current_app
import logging
import time
import datetime # For handling dates and times

# Imports for Google API
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Import shared helper functions
from shared_utils import get_access_token, get_global_specific_user_access_token

logger = logging.getLogger(__name__)
calendar_bp = Blueprint('calendar_agent', __name__, url_prefix='/calendar')

# --- get_access_token should be imported from shared_utils.py ---

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
    
    if not time_min_iso: # Default to start of today if not provided
        now = datetime.datetime.utcnow()
        time_min_iso = datetime.datetime(now.year, now.month, now.day).isoformat() + 'Z'
    if not time_max_iso: # Default to end of today if not provided
        now = datetime.datetime.utcnow()
        time_max_iso = datetime.datetime(now.year, now.month, now.day, 23, 59, 59).isoformat() + 'Z'
        
    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            maxResults=max_results,
            singleEvents=single_events, # Expands recurring events into single instances
            orderBy=order_by # 'startTime' or 'updated'
        ).execute()
        events = events_result.get('items', [])
        logger.info(f"Found {len(events)} events on calendar '{calendar_id}'.")
        return events
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError listing events: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error listing events: {str(e)}", exc_info=True); raise


def api_create_event(service, calendar_id="primary", summary=None, description=None, 
                     start_datetime_iso=None, end_datetime_iso=None, start_date_iso=None, end_date_iso=None, 
                     attendees=None, location=None, timezone=None):
    """
    Creates an event.
    Provide either (start_datetime_iso AND end_datetime_iso) OR (start_date_iso AND end_date_iso for all-day).
    Datetimes/Dates must be in RFC3339 format.
    Timezone can be like "America/Los_Angeles". If omitted, Google uses calendar's default or UTC.
    """
    logger.info(f"API: Creating event '{summary}' on calendar '{calendar_id}'.")
    event = {}
    if summary: event['summary'] = summary
    if description: event['description'] = description
    if location: event['location'] = location

    if start_datetime_iso and end_datetime_iso:
        event['start'] = {'dateTime': start_datetime_iso}
        event['end'] = {'dateTime': end_datetime_iso}
        if timezone:
            event['start']['timeZone'] = timezone
            event['end']['timeZone'] = timezone
    elif start_date_iso and end_date_iso: # For all-day events
        event['start'] = {'date': start_date_iso}
        event['end'] = {'date': end_date_iso} # For multi-day all-day events, end date is exclusive
    else:
        raise ValueError("Either (start_datetime_iso AND end_datetime_iso) OR (start_date_iso AND end_date_iso) must be provided.")

    if attendees: # List of {"email": "user@example.com"}
        event['attendees'] = attendees
    
    if not event.get('summary'): # Google Calendar requires a summary for most event creations
        event['summary'] = "Untitled Event"

    try:
        created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
        logger.info(f"Event created: ID '{created_event.get('id')}', Summary '{created_event.get('summary')}'")
        return created_event
    except HttpError as e: error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e); logger.error(f"API: HttpError creating event: {error_content}", exc_info=True); raise
    except Exception as e: logger.error(f"API: Generic error creating event: {str(e)}", exc_info=True); raise

def api_delete_event(service, calendar_id="primary", event_id=None):
    logger.info(f"API: Deleting event_id '{event_id}' from calendar '{calendar_id}'.")
    if not event_id:
        raise ValueError("event_id is required to delete an event.")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"Event '{event_id}' deleted successfully.")
        return {"eventId": event_id, "status": "deleted"} # Google returns empty 204, so we make our own success
    except HttpError as e:
        if e.resp.status == 404 or e.resp.status == 410: # 410 Gone (already deleted or recurring instance)
            logger.warning(f"API: Event '{event_id}' not found or already gone. Treating as successful deletion for idempotency.")
            return {"eventId": event_id, "status": "notFoundOrGone"}
        error_content = e.content.decode('utf-8') if hasattr(e,'content') and e.content else str(e)
        logger.error(f"API: HttpError deleting event: {error_content}", exc_info=True)
        raise
    except Exception as e: logger.error(f"API: Generic error deleting event: {str(e)}", exc_info=True); raise

# --- Flask Endpoints for Calendar ---

@calendar_bp.route('/token', methods=['GET'])
def specific_user_token_calendar_endpoint():
    endpoint_name = "/calendar/token"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    try:
        access_token = get_global_specific_user_access_token() # Imported from shared_utils
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
        # For timeMin and timeMax, expect RFC3339 UTC or with offset, or allow natural language and parse
        # For simplicity, expect ISO format strings for now
        time_min = data.get('time_min_iso') # e.g., "2024-05-16T00:00:00Z"
        time_max = data.get('time_max_iso') # e.g., "2024-05-16T23:59:59Z"
        
        # If only a date is provided (e.g., "2024-05-16"), set it for the whole day UTC
        date_for_events = data.get('date_iso') # e.g., "2024-05-16"
        if date_for_events and not time_min and not time_max:
            try:
                dt_obj = datetime.datetime.fromisoformat(date_for_events.replace('Z','')) # Remove Z if present for date only
                time_min = dt_obj.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "Z"
                time_max = dt_obj.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat() + "Z"
                logger.info(f"Querying events for full day: {date_for_events} (UTC: {time_min} to {time_max})")
            except ValueError:
                return jsonify({"success": False, "error": "Invalid date_iso format. Use YYYY-MM-DD."}), 400

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        events = api_list_events(service, calendar_id, time_min_iso=time_min, time_max_iso=time_max)
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
        summary = data.get('summary', 'New Event') # Event title
        description = data.get('description')
        location = data.get('location')
        attendees_emails = data.get('attendees') # List of email strings

        # Datetime/Date inputs - expect RFC3339 / ISO 8601 strings
        # e.g., "2024-05-16T10:00:00-07:00" or "2024-05-16T17:00:00Z"
        # For all-day: "2024-05-16" (start_date_iso) and "2024-05-17" (end_date_iso, exclusive)
        start_datetime_iso = data.get('start_datetime_iso')
        end_datetime_iso = data.get('end_datetime_iso')
        start_date_iso = data.get('start_date_iso') # For all-day events
        end_date_iso = data.get('end_date_iso')     # For all-day events
        timezone = data.get('timezone') # e.g., "America/Los_Angeles"

        if not ((start_datetime_iso and end_datetime_iso) or (start_date_iso and end_date_iso)):
            return jsonify({"success": False, "error": "Must provide either (start_datetime_iso & end_datetime_iso) or (start_date_iso & end_date_iso)"}), 400
        
        attendees_list = None
        if isinstance(attendees_emails, list):
            attendees_list = [{'email': email} for email in attendees_emails if isinstance(email, str)]

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        
        created_event = api_create_event(service, calendar_id, summary, description,
                                         start_datetime_iso, end_datetime_iso,
                                         start_date_iso, end_date_iso,
                                         attendees_list, location, timezone)
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
        if not all(k in data for k in ('event_id', 'refresh_token')):
            return jsonify({"success": False, "error": "Missing 'event_id' or 'refresh_token'"}), 400
        event_id = data['event_id']
        refresh_token = data['refresh_token']
        calendar_id = data.get('calendar_id', 'primary')

        access_token = get_access_token(refresh_token, current_app.config['CLIENT_ID'], current_app.config['CLIENT_SECRET'])
        service = get_calendar_service(access_token)
        
        delete_status = api_delete_event(service, calendar_id, event_id)
        if delete_status.get("status") == "deleted" or delete_status.get("status") == "notFoundOrGone":
            return jsonify({"success": True, "message": f"Event '{event_id}' deletion processed.", "details": delete_status})
        else: # Should not happen if api_delete_event raises exceptions for other errors
            return jsonify({"success": False, "message": "Event deletion failed or had an unknown status.", "details": delete_status}), 500
            
    except Exception as e:
        logger.error(f"ENDPOINT {endpoint_name}: Exception: {str(e)}", exc_info=True)
        status_code = e.resp.status if isinstance(e, HttpError) and hasattr(e, 'resp') else 500
        error_details = e.content.decode('utf-8') if isinstance(e, HttpError) and hasattr(e,'content') and e.content else str(e)
        return jsonify({"success": False, "error": "Failed to delete event", "details": error_details}), status_code
