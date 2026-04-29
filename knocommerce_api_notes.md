# KnoCommerce API Notes

## Auth Flow
1. OAuth 2.0 Client Credentials grant
2. POST https://app-api.knocommerce.com/api/oauth2/token
3. HTTP Basic auth: base64(url_encode(client_id):url_encode(client_secret))
4. Body: grant_type=client_credentials&scope=SURVEYS+RESPONSES
5. Returns Bearer token for subsequent calls

## Key Endpoints
- GET /api/rest/surveys — list surveys
- GET /api/rest/responses — list responses (cursor pagination, maxPageSize up to 250)
- GET /api/rest/responses/count — count responses

## Response Query Params
- maxPageSize: 1-250
- pageToken / prevPageToken: cursor pagination
- surveyId: filter by survey UUID
- questionId: filter by question UUID
- status: completed | partial | view_only
- updatedAt[gte/lte]: date filter (ISO 8601 or YYYY-MM-DD)
- completedAt[gte/lte]: date filter
- expand: "order" to include related order data
- sanitize: boolean to redact PII

## Response Schema
Each response has: id, account_id, created_at, updated_at, completed_at,
customer_id, customer_email, customer_phone, customer_shop,
customer_lifetime_spent, customer_lifetime_orders, time_spent,
survey_id, order, response (array of answers)

## NPS Strategy
- Need to identify the NPS survey/question by listing surveys first
- Filter responses by completedAt date range
- Parse NPS scores from the response answers
- Calculate: Promoters (9-10), Passives (7-8), Detractors (0-6)
- NPS = %Promoters - %Detractors

## Credentials
- Client ID: 54b230260cd231e8f5645fde.VOC
- Secret: 5b46737617a2c3c4466dbf6610cfc48068c5036aa680cdbb09099550c1b3553b
- Server: https://app-api.knocommerce.com
