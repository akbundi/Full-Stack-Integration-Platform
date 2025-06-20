# slack.py

import os
from typing import Dict, List, Optional, TypedDict, Any
from urllib.parse import urlencode
import httpx
from fastapi import Request, HTTPException
import json
from redis_client import add_key_value_redis, get_value_redis
from integrations.integration_item import IntegrationItem
from fastapi.responses import HTMLResponse

class HubspotCredentials(TypedDict):
    """Type definition for HubSpot credentials"""
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str

# HubSpot API Constants
HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API_BASE_URL = "https://api.hubapi.com"
HUBSPOT_CLIENT_ID="ef6c8484-fb2a-4a76-b0a7-9789a2ca5b63"
HUBSPOT_CLIENT_SECRET="716a1576-f5ee-4e0f-b286-c1b0d183858d"
HUBSPOT_REDIRECT_URI="http://localhost:8000/integrations/hubspot/callback"


if not all([HUBSPOT_CLIENT_ID, HUBSPOT_CLIENT_SECRET, HUBSPOT_REDIRECT_URI]):
    raise ValueError("Missing required HubSpot environment variables")

# Required scopes for HubSpot API access
HUBSPOT_SCOPES = [
    "contacts",
    "content",
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.schemas.contacts.read",
]

async def save_hubspot_credentials(user_id: str, org_id: str, tokens: HubspotCredentials) -> None:
    """
    Save HubSpot tokens to Redis.
    Args:
        user_id: The user's ID(Who is loggedin)
        org_id: The organization's ID
        tokens: The HubSpot credentials to store
    """
    key = f"hubspot_credentials:{org_id}:{user_id}"
    await add_key_value_redis(key, json.dumps(tokens), expire=tokens.get('expires_in', 3600))

async def authorize_hubspot(user_id: str, org_id: str) -> str:
    """
    Generate the HubSpot authorization URL.
    Args:
        user_id: The user's ID
        org_id: The organization's ID
    Returns:
        The authorization URL for HubSpot OAuth flow
    """
    query_params = {
        "client_id": HUBSPOT_CLIENT_ID,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "scope": " ".join(HUBSPOT_SCOPES),
        "state": f"{user_id}:{org_id}",
    }   
    '''Encodes the required OAuth parameters (client ID, redirect URI, scopes, and state) into a URL'''
    return f"{HUBSPOT_AUTH_URL}?{urlencode(query_params)}"
'''Uses a refresh token to request a new access token.'''
async def refresh_hubspot_token(refresh_token: str) -> HubspotCredentials:
    """
    Refresh HubSpot access token using refresh token.
    Args:
        refresh_token: The refresh token to use
    Returns:
        New HubSpot credentials
    Raises:
        HTTPException: If token refresh fails
    """
    async with httpx.AsyncClient() as client:
        try:
            '''POST Request'''
            response = await client.post(
                HUBSPOT_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": HUBSPOT_CLIENT_ID,
                    "client_secret": HUBSPOT_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=response.status_code if hasattr(e, 'response') else 500,
                detail=f"Error refreshing token: {str(e)}"
            )
'''Attempts to fetch user credentials from Redis. Raises a 404 error if not found.'''
async def get_hubspot_credentials(user_id: str, org_id: str) -> HubspotCredentials:
    """
    Retrieve stored HubSpot credentials for a user and organization.
    Args:
        user_id: The user's ID
        org_id: The organization's ID
    Returns:
        HubSpot credentials
    Raises:
        HTTPException: If credentials are not found or refresh fails
    """
    credentials = await fetch_credentials_from_db(user_id, org_id, "hubspot")
    if not credentials:
        raise HTTPException(status_code=404, detail="No HubSpot credentials found")

    # Check if token needs refresh
    if credentials.get("refresh_token"):
        try:
            new_tokens = await refresh_hubspot_token(credentials["refresh_token"])
            await save_hubspot_credentials(user_id, org_id, new_tokens)
            return new_tokens
        except HTTPException as e:
            if e.status_code == 401:  # Unauthorized - token expired
                raise HTTPException(
                    status_code=401,
                    detail="HubSpot authorization expired. Please reconnect."
                )
            raise

    return credentials
'''Retrieves a list of HubSpot contacts via API.'''
async def get_items_hubspot(credentials: HubspotCredentials) -> List[IntegrationItem]:
    """
    Fetch items from HubSpot using API endpoints with pagination support.
    Args:
        credentials: HubSpot credentials
    Returns:
        List of integration items
    Raises:
        HTTPException: If API request fails
    """
    headers = {"Authorization": f"Bearer {credentials['access_token']}"}
    all_contacts: List[Dict[str, Any]] = []
    after: Optional[str] = None

    async with httpx.AsyncClient() as client:
        while True:
            try:
                '''Makes paginated API calls to fetch 100 contacts at a time.'''
                url = f"{HUBSPOT_API_BASE_URL}/crm/v3/objects/contacts"
                params = {"limit": 100}
                if after:
                    params["after"] = after

                response = await client.get(
                    url, 
                    headers=headers, 
                    params=params,
                    timeout=30.0
                )
                response.raise_for_status()

                data = response.json()
                contacts = data.get("results", [])
                all_contacts.extend(contacts)

                # Check for more pages
                paging = data.get("paging", {})
                after = paging.get("next", {}).get("after")
                if not after:
                    break

            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=getattr(e.response, 'status_code', 500),
                    detail=f"Error fetching HubSpot items: {str(e)}"
                )

        # Use list comprehension with await
        return [await create_integration_item_metadata_object(contact) for contact in all_contacts]

async def fetch_credentials_from_db(user_id: str, org_id: str, integration: str) -> Optional[Dict[str, Any]]:
    """
    Fetch integration credentials from Redis.
    Args:
        user_id: The user's ID
        org_id: The organization's ID
        integration: The integration type
    Returns:
        Credentials if found, None otherwise
    """
    key = f"{integration}_credentials:{org_id}:{user_id}"
    credentials = await get_value_redis(key)
    if credentials:
        try:
            return json.loads(credentials.decode('utf-8'))
        except json.JSONDecodeError:
            return None
    return None
'''Handles the OAuth2 callback, exchanges the authorization code for tokens, and stores them.'''
async def oauth2callback_hubspot(request: Request) -> HTMLResponse:
    """
    Handle OAuth2 callback from HubSpot
    """
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error_description'))
    
    code = request.query_params.get('code')
    state = request.query_params.get('state')
    user_id, org_id = state.split(':')

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                HUBSPOT_TOKEN_URL,
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': HUBSPOT_REDIRECT_URI,
                    'client_id': HUBSPOT_CLIENT_ID,
                    'client_secret': HUBSPOT_CLIENT_SECRET,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10.0
            )
            response.raise_for_status()
            tokens = response.json()
            await save_hubspot_credentials(user_id, org_id, tokens)
            
            close_window_script = """
            <html>
                <script>
                    window.close();
                </script>
            </html>
            """
            return HTMLResponse(content=close_window_script)
            
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=response.status_code if hasattr(e, 'response') else 500,
                detail=f"Error in OAuth callback: {str(e)}"
            )
'''Converts raw contact data into a structured IntegrationItem format, containing metadata like name, creation time, and URL.'''
async def create_integration_item_metadata_object(contact: Dict[str, Any]) -> IntegrationItem:
    """Convert HubSpot contact to IntegrationItem format"""
    properties = contact.get('properties', {})
    return IntegrationItem(
        id=contact.get('id'),
        name=f"{properties.get('firstname', '')} {properties.get('lastname', '')}".strip(),
        type='contact',
        directory=False,
        parent_path_or_name=properties.get('company'),
        parent_id=None,
        creation_time=properties.get('createdate'),
        last_modified_time=properties.get('lastmodifieddate'),
        url=f"https://app.hubspot.com/contacts/{contact.get('id')}",
        children=None,
        mime_type=None,
        delta=None,
        drive_id=None,
        visibility=True
    )

