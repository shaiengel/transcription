#!/usr/bin/env python3
# /// script
# dependencies = [
#   "jinja2",
# ]
# ///
"""
Render .env files from .env.jinja templates using global JSON config.

Config is loaded from two files:
- config.<env>.json: Public configuration (committed to git)
- config.secrets.<env>.json: Secrets (NOT committed, optional)

Secrets are deep-merged into config, overriding any placeholder values.

Usage:
    uv run render_env.py [environment] [--require-secrets] [--skip-validation]

    environment: dev (default), prod, etc.
    --require-secrets: Fail if secrets file is missing
    --skip-validation: Skip validation of required secrets

Note: Uses inline script dependencies (PEP 723). Dependencies are installed
in an isolated cache by uv - NOT in your global environment or any virtualenv.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from jinja2 import Environment, FileSystemLoader


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge override dict into base dict.
    Override values take precedence for leaf values.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_file: Path, secrets_file: Path, require_secrets: bool = False) -> tuple[dict[str, Any], bool]:
    """
    Load and merge public config with secrets file.

    Args:
        config_file: Path to public config JSON
        secrets_file: Path to secrets JSON
        require_secrets: If True, fail if secrets file doesn't exist

    Returns:
        Tuple of (merged configuration dictionary, whether secrets were loaded)
    """
    # Load public config
    if not config_file.exists():
        print(f"Error: Config file not found: {config_file}", file=sys.stderr)
        sys.exit(1)

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Load secrets if available
    secrets_loaded = False
    if secrets_file.exists():
        try:
            with open(secrets_file, "r", encoding="utf-8") as f:
                secrets = json.load(f)
            config = deep_merge(config, secrets)
            print(f"Loaded secrets from: {secrets_file.name}")
            secrets_loaded = True
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in secrets file: {secrets_file}", file=sys.stderr)
            print(f"  {e}", file=sys.stderr)
            sys.exit(1)
    elif require_secrets:
        print(f"Error: Secrets file required but not found: {secrets_file}", file=sys.stderr)
        print(f"  Copy {config_file.parent / 'config.secrets.example.json'} to {secrets_file.name}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Warning: Secrets file not found: {secrets_file.name} (continuing without secrets)")

    return config, secrets_loaded


def validate_secrets(config: dict[str, Any]) -> list[str]:
    """
    Validate that required secrets are present and non-empty.

    Returns:
        List of missing/empty secret paths
    """
    required_secrets = [
        ("db", "password"),
        ("gitlab", "private_token"),
    ]

    missing = []
    for path in required_secrets:
        value = config
        for key in path:
            value = value.get(key, {})

        if not value or value == "":
            missing.append(".".join(path))

    return missing


def main():
    parser = argparse.ArgumentParser(description="Render .env files from templates")
    parser.add_argument("env", nargs="?", default="dev", help="Environment (default: dev)")
    parser.add_argument("--require-secrets", action="store_true",
                       help="Fail if secrets file is missing")
    parser.add_argument("--skip-validation", action="store_true",
                       help="Skip validation of required secrets")
    args = parser.parse_args()

    config_dir = Path(__file__).parent
    repo_root = config_dir.parent
    config_file = config_dir / f"config.{args.env}.json"
    secrets_file = config_dir / f"config.secrets.{args.env}.json"

    # Load and merge config
    config, secrets_loaded = load_config(config_file, secrets_file, args.require_secrets)

    # Validate secrets unless skipped or secrets file wasn't loaded
    if not args.skip_validation and secrets_loaded:
        missing = validate_secrets(config)
        if missing:
            print(f"\nError: Required secrets are missing or empty:", file=sys.stderr)
            for secret in missing:
                print(f"  - {secret}", file=sys.stderr)
            print(f"\nPlease update: {secrets_file.name}", file=sys.stderr)
            sys.exit(1)
    elif not args.skip_validation and not secrets_loaded:
        print("Skipping secrets validation (no secrets file loaded)")

    # Find all .env.jinja templates
    templates = list(repo_root.glob("*/.env.jinja"))

    if not templates:
        print("No .env.jinja templates found")
        return 1

    # Render each template
    for template_path in templates:
        project_dir = template_path.parent
        env = Environment(loader=FileSystemLoader(project_dir))
        template = env.get_template(".env.jinja")

        rendered = template.render(**config)

        output_path = project_dir / ".env"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(rendered)

        print(f"Rendered: {output_path.relative_to(repo_root)}")

    print(f"\nDone! Rendered {len(templates)} .env files from config.{args.env}.json")
    return 0


if __name__ == "__main__":
    exit(main())
