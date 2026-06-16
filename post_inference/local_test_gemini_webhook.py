"""Local test server for Gemini Webhook."""

import json
import logging
import os
import sys

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from post_inference.handler import lambda_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 8000))


@app.route("/test-event", methods=["POST"])
def test_event():
    """
    Test Gemini webhook event.
    
    Example:
    {
        "type": "batch.succeeded",
        "version": "v1",
        "timestamp": "2026-01-22T12:00:00Z",
        "data": {
            "id": "batch_123456",
            "output_file_uri": "gs://my-bucket/results.jsonl"
        }
    }
    """
    payload = request.get_data(as_text=True)
    headers = request.headers

    
    
    logger.info("Received test event: %s", payload)
    
    lambda_event = {
        "headers": {k.lower(): v for k, v in headers},
        "body": payload,
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": "POST", "path": "/test-event"}},
    }
    
    try:
        response = lambda_handler(lambda_event, None)
        logger.info("Response: %s", json.dumps(response, indent=2))
        
        status_code = response.get("statusCode", 200)
        body = response.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        
        return jsonify(body), status_code
    except Exception as e:
        logger.exception("Handler failed: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print(f"Server: http://localhost:{PORT}/test-event")
    app.run(host="0.0.0.0", port=PORT, debug=True)
