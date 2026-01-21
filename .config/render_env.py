#!/usr/bin/env python3
"""
Render .env files from .env.jinja templates using global JSON config.

Usage:
    uv run render_env.py [environment]

    environment: dev (default), prod, etc.
"""

import argparse
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


def main():
    parser = argparse.ArgumentParser(description="Render .env files from templates")
    parser.add_argument("env", nargs="?", default="dev", help="Environment (default: dev)")
    args = parser.parse_args()

    config_dir = Path(__file__).parent
    repo_root = config_dir.parent
    config_file = config_dir / f"config.{args.env}.json"

    if not config_file.exists():
        print(f"Error: Config file not found: {config_file}")
        return 1

    # Load config
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

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
