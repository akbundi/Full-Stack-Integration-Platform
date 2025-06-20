import json
import secrets
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
import httpx
import asyncio
import base64
import requests
from integrations.integration_item import IntegrationItem

from redis_client import add_key_value_redis, get_value_redis, delete_key_redis

CLIENT_ID = '1b9d872b-594c-80db-af5f-0037a3e544bf'
CLIENT_SECRET = 'secret_7BdGAESGtB0VxrBHmMNIHFgbhp4ZTBqPyThJK6vCibu'
encoded_client_id_secret = base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()

REDIRECT_URI = 'http://localhost:8000/integrations/notion/callback'
authorization_url = f'https://api.notion.com/v1/oauth/authorize?client_id=1b9d872b-594c-80db-af5f-0037a3e544bf&response_type=code&owner=user&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fintegrations%2Fnotion%2Fcallback'

async def authorize_notion(user_id, org_id):
    state_data = {
        'state': secrets.token_urlsafe(32),
        'user_id': user_id,
        'org_id': org_id
    }
    encoded_state = json.dumps(state_data)
    await add_key_value_redis(f'notion_state:{org_id}:{user_id}', encoded_state, expire=600)

    return f'{authorization_url}&state={encoded_state}'

async def oauth2callback_notion(request: Request):
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error'))
    code = request.query_params.get('code')
    encoded_state = request.query_params.get('state')
    state_data = json.loads(encoded_state)

    original_state = state_data.get('state')
    user_id = state_data.get('user_id')
    org_id = state_data.get('org_id')

    saved_state = await get_value_redis(f'notion_state:{org_id}:{user_id}')

    if not saved_state or original_state != json.loads(saved_state).get('state'):
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.notion.com/v1/oauth/token',
                json={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': REDIRECT_URI
                }, 
                headers={
                    'Authorization': f'Basic {encoded_client_id_secret}',
                    'Content-Type': 'application/json',
                }
            ),
            delete_key_redis(f'notion_state:{org_id}:{user_id}'),
        )

    await add_key_value_redis(f'notion_credentials:{org_id}:{user_id}', json.dumps(response.json()), expire=600)
    
    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_notion_credentials(user_id, org_id):
    credentials = await get_value_redis(f'notion_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    credentials = json.loads(credentials)
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    await delete_key_redis(f'notion_credentials:{org_id}:{user_id}')

    return credentials

def create_integration_item_metadata_object(response_json: str) -> IntegrationItem:
    name = response_json.get('title', [{'text': {'content': 'Unknown'}}])[0]['text']['content']
    return IntegrationItem(
        id=response_json['id'],
        type=response_json['object'],
        name=name,
        creation_time=response_json['created_time'],
        last_modified_time=response_json['last_edited_time'],
        parent_id=response_json.get('parent', {}).get('database_id')
    )

async def get_items_notion(credentials) -> list[IntegrationItem]:
    credentials = json.loads(credentials)
    headers = {
        'Authorization': f'Bearer {credentials.get("access_token")}',
        'Notion-Version': '2022-06-28',
    }
    
    # Fetch all databases to find Employees database
    response = requests.post('https://api.notion.com/v1/search', headers=headers)
    if response.status_code != 200:
        print("Error fetching databases:", response.json())
        return []
    
    databases = response.json()['results']
    employees_db = next((db for db in databases if db.get('title', [{'text': {'content': ''}}])[0]['text']['content'] == 'Employees'), None)
    if not employees_db:
        print("Employees database not found!")
        return []
    
    database_id = employees_db['id']
    query_url = f'https://api.notion.com/v1/databases/{database_id}/query'
    
    # Fetch employee records
    response = requests.post(query_url, headers=headers)
    if response.status_code != 200:
        print("Error fetching employee records:", response.json())
        return []
    
    results = response.json()['results']
    #return [create_integration_item_metadata_object(result) for result in results]

    print("Data Loaded Successfully:", results)  # Print statement when data is retrieved
    return [create_integration_item_metadata_object(result) for result in results]
