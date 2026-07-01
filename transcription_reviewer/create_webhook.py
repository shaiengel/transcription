"""Create a Gemini static webhook and print the signing secret.

IMPORTANT: The signing secret is only returned ONCE at creation time.
Store it securely (e.g., in your .env file as GEMINI_WEBHOOK_SECRET).
"""

import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def create_webhook():
    """Create a new webhook for batch job notifications."""
    
    # Get webhook URL from environment or use a default
    webhook_url = os.getenv("GEMINI_BATCH_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: GEMINI_BATCH_WEBHOOK_URL not set in .env")
        print("Please set it to your webhook endpoint URL first.")
        return
    
    print(f"Creating webhook for URL: {webhook_url}")
    print()
    
    try:
        webhook = client.webhooks.create(
            name="TranscriptionBatchWebhook",
            subscribed_events=["batch.succeeded", "batch.failed", "batch.expired"],
            uri=webhook_url,
        )
        
        print("=" * 60)
        print("WEBHOOK CREATED SUCCESSFULLY")
        print("=" * 60)
        print()
        print(f"Webhook ID:     {webhook.id}")
        print(f"Webhook Name:   {webhook.name}")
        print(f"URI:            {webhook.uri}")
        print(f"Events:         {webhook.subscribed_events}")
        print(f"State:          {getattr(webhook, 'state', 'N/A')}")
        print()
        print("=" * 60)
        print("SIGNING SECRET (SAVE THIS - IT'S ONLY SHOWN ONCE!)")
        print("=" * 60)
        print()
        print(f"GEMINI_WEBHOOK_SECRET={webhook.new_signing_secret}")
        print()
        print("Add this to your .env file for webhook signature verification.")
        print("=" * 60)
        
    except Exception as e:
        print(f"ERROR creating webhook: {e}")
        print()
        print("If webhook already exists, use cleanup_gemini.py to delete it first.")


def list_webhooks():
    """List all existing webhooks."""
    print("\n=== EXISTING WEBHOOKS ===")
    result = client.webhooks.list()
    webhooks = list(result.webhooks) if result.webhooks else []
    
    if not webhooks:
        print("No webhooks found.")
        return
    
    for wh in webhooks:
        print(f"  ID: {wh.id}")
        print(f"  Name: {wh.name}")
        print(f"  URI: {wh.uri}")
        print(f"  Events: {wh.subscribed_events}")
        print(f"  State: {getattr(wh, 'state', 'N/A')}")
        print()


def main():
    print("=" * 60)
    print("GEMINI WEBHOOK CREATION SCRIPT")
    print("=" * 60)
    
    # First list existing webhooks
    # list_webhooks()
    
    # Then create new one
    print("\n=== CREATING NEW WEBHOOK ===\n")
    create_webhook()


if __name__ == "__main__":
    main()
