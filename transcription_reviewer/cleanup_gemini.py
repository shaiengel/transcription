"""Cleanup script for Gemini API resources: files, batches, and webhooks."""

import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def cleanup_files():
    """List and delete all uploaded files."""
    print("\n=== FILES ===")
    files = list(client.files.list())
    print(f"Found {len(files)} files")
    
    for f in files:
        print(f"  - {f.name} (display: {getattr(f, 'display_name', 'N/A')}, size: {getattr(f, 'size_bytes', 'N/A')})")
    
    for f in files:
        print(f"  Deleting: {f.name}")
        client.files.delete(name=f.name)
    
    if files:
        print("All files deleted.")
    else:
        print("No files to delete.")


def cleanup_batches():
    """List and delete all batch jobs."""
    print("\n=== BATCH JOBS ===")
    jobs = list(client.batches.list())
    print(f"Found {len(jobs)} batch jobs")
    
    for job in jobs:
        print(f"  - {job.name}")
        print(f"    Display: {getattr(job, 'display_name', 'N/A')}")
        print(f"    State: {job.state}")
        print(f"    Model: {getattr(job, 'model', 'N/A')}")
    
    for job in jobs:
        print(f"  Deleting: {job.name}")
        client.batches.delete(name=job.name)
    
    if jobs:
        print("All batch jobs deleted.")
    else:
        print("No batch jobs to delete.")


def cleanup_webhooks():
    """List and delete all webhooks."""
    print("\n=== WEBHOOKS ===")
    result = client.webhooks.list()
    webhooks = list(result.webhooks) if result.webhooks else []
    print(f"Found {len(webhooks)} webhooks")
    
    for wh in webhooks:
        print(f"  - ID: {wh.id}")
        print(f"    Name: {wh.name}")
        print(f"    URI: {wh.uri}")
        print(f"    Events: {wh.subscribed_events}")
        print(f"    State: {getattr(wh, 'state', 'N/A')}")
    
    for wh in webhooks:
        print(f"  Deleting: {wh.id}")
        client.webhooks.delete(id=wh.id)
    
    if webhooks:
        print("All webhooks deleted.")
    else:
        print("No webhooks to delete.")


def main():
    print("=" * 50)
    print("GEMINI API CLEANUP SCRIPT")
    print("=" * 50)
    
    cleanup_files()
    cleanup_batches()
    #cleanup_webhooks()
    
    print("\n" + "=" * 50)
    print("Cleanup complete!")
    print("=" * 50)


if __name__ == "__main__":
    main()
