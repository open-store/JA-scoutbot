"""Set new API credential env vars on Railway."""
import requests

RAILWAY_TOKEN = "8b58e33d-4b81-4143-864c-fedfaf82d7a8"
PROJECT_ID = "a19179e2-7db7-40aa-91d0-74addaf3aa4b"
ENVIRONMENT_ID = "7ff7674a-9c39-42ac-8d02-6b5a384943e3"
SERVICE_ID = "bf7aa666-a6e1-4f6c-9d6b-5048947b12c0"
GRAPHQL_URL = "https://backboard.railway.app/graphql/v2"
HEADERS = {
    "Authorization": f"Bearer {RAILWAY_TOKEN}",
    "Content-Type": "application/json",
}

new_vars = {
    "KNOCOMMERCE_CLIENT_ID": "54b230260cd231e8f5645fde.VOC",
    "KNOCOMMERCE_SECRET": "5b46737617a2c3c4466dbf6610cfc48068c5036aa680cdbb09099550c1b3553b",
    "OKENDO_SUBSCRIBER_ID": "526c4142-f651-4747-8dc6-bf06b53ade92",
    "OKENDO_API_KEY": "5a750d84cdb183148ef62525257a799f29994d14ce7f9176f62114586fa0500b",
    "REDO_API_KEY": "Bi5T7rFHoxfGgf_z6FG1HIm7iJvOmo_VVHgWG_imaMM",
}

mutation = """
mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
    variableCollectionUpsert(input: $input)
}
"""

for name, value in new_vars.items():
    variables = {
        "input": {
            "projectId": PROJECT_ID,
            "environmentId": ENVIRONMENT_ID,
            "serviceId": SERVICE_ID,
            "variables": {name: value},
        }
    }
    resp = requests.post(
        GRAPHQL_URL,
        headers=HEADERS,
        json={"query": mutation, "variables": variables},
        timeout=30,
    )
    data = resp.json()
    if "errors" in data:
        print(f"ERROR {name}: {data['errors']}")
    else:
        print(f"OK    {name}")
