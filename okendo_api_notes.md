# Okendo API Notes

## Auth
- Basic auth: Authorization: Basic base64(subscriberId:apiKey)
- Base URL: https://api.okendo.io/enterprise
- API key provided: 5a750d84cdb183148ef62525257a799f29994d14ce7f9176f62114586fa0500b
- Need subscriberId (not yet provided)

## List Reviews Endpoint
- GET /reviews
- Params: limit (1-100, default 25), lastEvaluated (cursor), orderBy, status
- Cursor pagination via lastEvaluated (URL-encoded JSON)

## Review Object Fields
- subscriberId, reviewId, productId
- attributesWithRating: [{title, type, value, minLabel, midLabel, maxLabel}]
- body: review text content
- containsProfanity: boolean
- dateCreated: datetime
- helpfulCount, unhelpfulCount: int
- isRecommended: boolean
- media: images/videos
- productName: string
- rating: 1-5 stars
- sentiment: positive | negative | neutral | mixed
- status: approved | pending | rejected
- tags: string[] (e.g. "Support Needed", "Favourite")
- title: review title
- reviewer: object with customer info
- reply: object with merchant reply
- variantId: Shopify variant ID

## Strategy for /reviews command
- Fetch approved reviews within date range
- Calculate: avg rating, rating distribution, sentiment breakdown
- Top products by review count and avg rating
- Surface common themes from tags
- Show sample positive and negative reviews
