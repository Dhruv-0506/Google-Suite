from flask import Blueprint, request, jsonify, current_app # current_app might not be needed if all config from chat_agent_config
import requests
import json
import logging

# Import configuration with hardcoded keys
from chat_agent_config import ON_DEMAND_API_KEY, ON_DEMAND_EXTERNAL_USER_ID

logger = logging.getLogger(__name__)
chat_bp = Blueprint('chat_agent', __name__, url_prefix='/chat')

BASE_URL = "https://api.on-demand.io/chat/v1" # From your script

# --- Internal Helper Functions based on your script ---

def _create_chat_session_internal():
    """Internal helper to create a chat session with the On-Demand API."""
    url = f"{BASE_URL}/sessions"
    headers = {"apikey": ON_DEMAND_API_KEY}
    # Agent IDs can be an empty list if the On-Demand API allows it or if it picks defaults.
    # Or, you can populate it with specific agent IDs from your On-Demand platform.
    body = {"agentIds": [], "externalUserId": ON_DEMAND_EXTERNAL_USER_ID}
    
    try:
        logger.info(f"Chat Agent Blueprint: Attempting to create session at URL: {url}")
        # Avoid logging full headers if API_KEY is sensitive, or redact it.
        # logger.debug(f"Chat Agent Blueprint: With headers: {headers}") 
        logger.debug(f"Chat Agent Blueprint: With headers containing API key (initial chars): {ON_DEMAND_API_KEY[:4]}...")
        logger.debug(f"Chat Agent Blueprint: With body: {json.dumps(body)}")
        
        response = requests.post(url, headers=headers, json=body, timeout=10) # Added timeout

        if response.status_code == 201:
            response_data = response.json()
            session_id = response_data.get("data", {}).get("id")
            if session_id:
                logger.info(f"Chat Agent Blueprint: Chat session created. Session ID: {session_id}")
                return session_id
            else:
                logger.error(f"Chat Agent Blueprint: Error - 'data.id' not found in session creation response. Full response: {response_data}")
                return None
        else:
            logger.error(f"Chat Agent Blueprint: Error creating chat session: {response.status_code} - {response.text[:500]}") # Log truncated response
            return None
    except requests.exceptions.Timeout:
        logger.error(f"Chat Agent Blueprint: Request timed out during session creation.", exc_info=True)
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Chat Agent Blueprint: Request failed during session creation: {e}", exc_info=True)
        return None
    except json.JSONDecodeError as e:
        responseText = "N/A"
        if "response" in locals() and hasattr(response, "text"):
            responseText = response.text
        logger.error(f"Chat Agent Blueprint: Failed to decode JSON response during session creation: {e}. Response text: {responseText[:500]}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Chat Agent Blueprint: Unexpected error during session creation: {e}", exc_info=True)
        return None


def _submit_query_internal(session_id, query_text):
    """
    Internal helper to submit a query in sync mode to the On-Demand API 
    and attempt to extract the primary answer text.
    """
    url = f"{BASE_URL}/sessions/{session_id}/query"
    headers = {"apikey": ON_DEMAND_API_KEY}

    # Agent IDs from your original script. Consider making this configurable if it changes.
    agent_ids = [
        "agent-1712327325", "agent-1713962163", "agent-1747205988",
        "agent-1746427905", "agent-1718116202", "agent-1713924030"
    ]
    # Stop sequences from your original script. If empty, can be an empty list.
    stop_sequences = []

    body = {
        "endpointId": "predefined-openai-gpt4.1", # This could also be made configurable
        "query": query_text,
        "agentIds": agent_ids, 
        "responseMode": "sync", # Siri needs a synchronous response
        "reasoningMode": "low", # As per your script
        "modelConfigs": {
            "fulfillmentPrompt": "", # As per your script
            "stopSequences": stop_sequences,
            "temperature": 0.7,
            "topP": 1,
            "maxTokens": 0, # Check On-Demand API docs for meaning of 0. Might mean 'default' or 'no limit'.
            "presencePenalty": 0,
            "frequencyPenalty": 0
        },
    }

    try:
        logger.info(f"Chat Agent Blueprint: Attempting to submit sync query to URL: {url}")
        # logger.debug(f"Chat Agent Blueprint: With headers: {headers}") # API key in headers
        logger.debug(f"Chat Agent Blueprint: With headers containing API key (initial chars): {ON_DEMAND_API_KEY[:4]}...")
        # logger.debug(f"Chat Agent Blueprint: With body: {json.dumps(body, indent=2)}") # Can be very verbose

        response = requests.post(url, headers=headers, json=body, timeout=60) # Increased timeout for potentially long queries
        
        logger.debug(f"Chat Agent Blueprint (submit_query) - OnDemand API Response Status: {response.status_code}")
        logger.debug(f"Chat Agent Blueprint (submit_query) - OnDemand API Response Text (first 500 chars): {response.text[:500]}")

        if response.status_code == 200:
            logger.info("Chat Agent Blueprint: Sync query submitted successfully to OnDemand API.")
            response_data = response.json()
            
            # Attempt to extract the answer. This part is HEAVILY dependent on the
            # actual JSON structure returned by the OnDemand API.
            # You WILL need to inspect a successful response to refine this.
            answer = None
            if isinstance(response_data, dict):
                data_content = response_data.get("data")
                if isinstance(data_content, dict):
                    query_result = data_content.get("queryResult")
                    if isinstance(query_result, dict):
                        fulfillment = query_result.get("fulfillment")
                        if isinstance(fulfillment, dict):
                            answer = fulfillment.get("answer")
                        if not answer and fulfillment is not None: # Try another common key if 'answer' fails
                           answer = fulfillment.get("text")
                    if not answer and data_content.get("answer"): # Try higher level 'answer'
                        answer = data_content.get("answer")
                    if not answer and data_content.get("text"): # Try higher level 'text'
                        answer = data_content.get("text")
                if not answer and response_data.get("answer"): # Try top-level 'answer'
                    answer = response_data.get("answer")
                if not answer and response_data.get("text"): # Try top-level 'text'
                    answer = response_data.get("text")

            if answer is not None:
                return str(answer) # Ensure it's a string
            else:
                logger.warning(f"Chat Agent Blueprint: Could not extract a definitive 'answer' from OnDemand API response. Returning full data. Response: {response_data}")
                return json.dumps(response_data) # Return full JSON if specific answer not found
        else:
            logger.error(f"Chat Agent Blueprint: Error submitting sync query to OnDemand API: {response.status_code} - {response.text[:500]}")
            return f"Error from chat service: Status {response.status_code}. Please check server logs for details."
    except requests.exceptions.Timeout:
        logger.error(f"Chat Agent Blueprint: Request timed out during query submission to OnDemand API.", exc_info=True)
        return "Sorry, the chat service took too long to respond."
    except requests.exceptions.RequestException as e:
        logger.error(f"Chat Agent Blueprint: Request failed during query submission to OnDemand API: {e}", exc_info=True)
        return "Sorry, I couldn't connect to the chat service."
    except json.JSONDecodeError as e:
        responseText = "N/A"
        if "response" in locals() and hasattr(response, "text"):
            responseText = response.text
        logger.error(f"Chat Agent Blueprint: Failed to decode JSON response from OnDemand API: {e}. Response text: {responseText[:500]}", exc_info=True)
        return "Sorry, I received an unexpected response from the chat service."
    except Exception as e:
        logger.error(f"Chat Agent Blueprint: An unexpected error occurred during query submission: {e}", exc_info=True)
        return "Sorry, an unexpected error occurred while I was trying to get an answer."


@chat_bp.route('/ask', methods=['POST'])
def ask_chat_agent_endpoint():
    """
    Endpoint for Siri (or other clients) to send a query.
    Expects JSON: {"query": "Your question for the chat agent"}
    Returns JSON: {"answer": "The chat agent's response"}
    """
    endpoint_name = "/chat/ask"
    logger.info(f"ENDPOINT {endpoint_name}: Request received.")
    
    if not ON_DEMAND_API_KEY or ON_DEMAND_API_KEY == "YOUR_FALLBACK_OR_PLACEHOLDER_ON_DEMAND_API_KEY": # Check against a more generic placeholder
        logger.error(f"ENDPOINT {endpoint_name}: ON_DEMAND_CHAT_API_KEY is not configured correctly on the server.")
        # For the client (Siri), give a user-friendly error. The server logs have the critical detail.
        return jsonify({"answer": "Sorry, the chat service is not configured correctly on my end."}), 500

    data = request.json
    if not data or not isinstance(data, dict) or 'query' not in data: # Added type check for data
        logger.warning(f"ENDPOINT {endpoint_name}: Missing 'query' in JSON request body or body is not JSON.")
        return jsonify({"answer": "Please tell me what your question is."}), 400 # User-friendly error

    user_query = data.get('query')
    if not isinstance(user_query, str) or not user_query.strip(): # Check if query is a non-empty string
        logger.warning(f"ENDPOINT {endpoint_name}: 'query' is empty or not a string.")
        return jsonify({"answer": "Your question seems to be empty. Please try again."}), 400

    logger.info(f"ENDPOINT {endpoint_name}: User query: '{user_query}'")

    # Create a new session for each query for simplicity with Siri.
    # If OnDemand API sessions are expensive or stateful across queries, this needs rethinking.
    session_id = _create_chat_session_internal()
    if not session_id:
        logger.error(f"ENDPOINT {endpoint_name}: Failed to create chat session with OnDemand API.")
        return jsonify({"answer": "Sorry, I couldn't start a new chat session right now. Please try again later."}), 503 # Service Unavailable

    answer_text = _submit_query_internal(session_id, user_query)

    # Note: The OnDemand API might have session cleanup implicitly or explicitly.
    # If sessions need to be explicitly closed (e.g., DELETE /sessions/{session_id}), add that logic.

    logger.info(f"ENDPOINT {endpoint_name}: Replying with answer length: {len(str(answer_text))}") # Log length instead of full answer
    return jsonify({"answer": answer_text})

# Test endpoint to check if config is loaded and basic session creation works
@chat_bp.route('/ping-ondemand-config', methods=['GET'])
def ping_ondemand_config_endpoint():
    logger.info("ENDPOINT /chat/ping-ondemand-config: Request received.")
    if not ON_DEMAND_API_KEY or ON_DEMAND_API_KEY == "YOUR_FALLBACK_OR_PLACEHOLDER_ON_DEMAND_API_KEY":
         return jsonify({
            "message": "OnDemand Chat API Key is NOT configured correctly (using placeholder or missing).",
            "api_key_status": "MISCONFIGURED",
            "external_user_id": ON_DEMAND_EXTERNAL_USER_ID
        }), 500
    
    logger.info("Attempting to create a test session with OnDemand API for ping...")
    session_id = _create_chat_session_internal()
    if session_id:
        # Optionally, immediately delete the test session if the API supports it
        # and you have a function for it.
        return jsonify({
            "message": "Successfully created a test session with OnDemand Chat API.",
            "api_key_status": "CONFIGURED (key seems to work for session creation)",
            "external_user_id": ON_DEMAND_EXTERNAL_USER_ID,
            "test_session_id": session_id
        }), 200
    else:
        return jsonify({
            "message": "Failed to create a test session with OnDemand Chat API. Check API key and service status.",
            "api_key_status": "CONFIGURED (but session creation failed)",
            "external_user_id": ON_DEMAND_EXTERNAL_USER_ID
        }), 503
