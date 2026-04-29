# KnoCommerce Auth Debug

From the docs:
- Token endpoint: `POST https://api.knocommerce.com/api/oauth2/token` (NOT app-api!)
- The server URL shown is `https://app-api.knocommerce.com` but the token endpoint example uses `https://api.knocommerce.com`
- Auth method: HTTP Basic with URL-encoded client_id:client_secret, Base64 encoded
- Body: `grant_type=client_credentials&scope=SURVEYS+RESPONSES`
- Content-Type: application/x-www-form-urlencoded

Key difference: The token endpoint might be at `api.knocommerce.com` not `app-api.knocommerce.com`
